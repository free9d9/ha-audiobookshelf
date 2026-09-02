"""Two Audiobookshelf servers at once.

The unique id is host:port, so a household can configure two servers. Every
action then has a question it did not have before: which one? Answering it with
"whichever entry loaded first" is how `remove_progress`, which is destructive and
not undoable, deletes the wrong person's place in a book on the wrong server. So
these tests pin down that an action resolves the server and the listener
together, and refuses when it cannot be sure.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.media_player import MediaPlayerEntityFeature as Feature
from homeassistant.components.media_player import MediaPlayerState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    CONF_API_KEY,
    CONF_URL,
)
from homeassistant.core import Context
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.audiobookshelf_plus.const import (
    CONF_LINKED_USERS,
    DOMAIN,
    SERVICE_CONTINUE_LISTENING,
    SERVICE_REMOVE_PROGRESS,
)
from custom_components.audiobookshelf_plus.playback import _sessions

from .conftest import API_KEY, URL, make_api_key
from .test_playback import SESSION, SPEAKER

URL_B = "http://abs2.example.com:13378"
SPEAKER_B = "media_player.study"
# Idle, and able to play: enough for a resume that needs no seek.
SPEAKER_IDLE = int(Feature.PLAY_MEDIA | Feature.PAUSE | Feature.STOP)

IN_PROGRESS = [{"id": "item-1", "media": {"metadata": {"title": "A Book"}}}]


def set_speaker(hass, entity_id: str = SPEAKER) -> None:
    """An idle speaker that will accept a book."""
    hass.states.async_set(
        entity_id,
        MediaPlayerState.IDLE,
        {ATTR_SUPPORTED_FEATURES: SPEAKER_IDLE},
    )


def _linked(username: str, ha_user_id: str | None) -> dict[str, dict]:
    return {
        "u1": {
            "username": username,
            "api_key": make_api_key(),
            "key_id": "key-1",
            "ha_user_id": ha_user_id,
        }
    }


@pytest.fixture
async def servers(hass, mock_abs_client, mock_rest):
    """Two loaded servers: Alice listens on one, Bob on the other."""
    entries = []
    for url, username, ha_user_id in (
        (URL, "Alice", "ha-alice"),
        (URL_B, "Bob", "ha-bob"),
    ):
        entry = MockConfigEntry(
            domain=DOMAIN,
            title=f"Audiobookshelf Plus ({url})",
            unique_id=url,
            data={CONF_URL: url, CONF_API_KEY: API_KEY},
            options={CONF_LINKED_USERS: _linked(username, ha_user_id)},
        )
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        entries.append(entry)
    await hass.async_block_till_done()
    return entries


@pytest.fixture
def playback_rest():
    """The per-user REST client, plus the constructor, to see which server."""
    rest = MagicMock()
    rest.async_items_in_progress = AsyncMock(return_value=IN_PROGRESS)
    rest.async_get_progress = AsyncMock(
        return_value={"currentTime": 0.0, "isFinished": False}
    )
    rest.async_play_item = AsyncMock(return_value=SESSION)
    rest.async_sync_session = AsyncMock()
    rest.async_close_session = AsyncMock()
    rest.async_close_session_without_sync = AsyncMock()
    with patch(
        "custom_components.audiobookshelf_plus.playback.AudiobookshelfRest",
        return_value=rest,
    ) as ctor:
        rest.ctor = ctor
        yield rest


@pytest.fixture
def player(hass):
    """Register real media_player services, so the action drives the real path."""
    calls: dict[str, list] = {"play": [], "seek": []}

    def _make():
        async def _play(service_call):
            calls["play"].append(dict(service_call.data))

        async def _seek(service_call):
            calls["seek"].append(dict(service_call.data))

        hass.services.async_register("media_player", "play_media", _play)
        hass.services.async_register("media_player", "media_seek", _seek)
        return calls

    return _make


@pytest.fixture
def progress_rest():
    """The per-user REST client `remove_progress` builds."""
    rest = MagicMock()
    rest.async_get_progress = AsyncMock(return_value={"id": "prog-a"})
    rest.async_get_item = AsyncMock(
        return_value={"media": {"metadata": {"title": "A Book"}}}
    )
    rest.async_delete_progress = AsyncMock()
    with patch(
        "custom_components.audiobookshelf_plus.progress.AudiobookshelfRest",
        return_value=rest,
    ):
        yield rest


async def resume(hass, entity_id=SPEAKER, user_id=None, **data):
    await hass.services.async_call(
        DOMAIN,
        SERVICE_CONTINUE_LISTENING,
        {ATTR_ENTITY_ID: entity_id, **data},
        blocking=True,
        context=Context(user_id=user_id) if user_id else None,
    )


async def remove(hass, **data):
    return await hass.services.async_call(
        DOMAIN,
        SERVICE_REMOVE_PROGRESS,
        data,
        blocking=True,
        return_response=True,
        context=Context(user_id="ha-alice"),
    )


def _server_used(rest) -> str:
    """The base URL the action built its per-user client against."""
    return rest.ctor.call_args.args[1]


# ------------------------------------------------------------ picking a server


async def test_only_one_server_knows_the_user(
    hass, servers, playback_rest, player
) -> None:
    """Two servers, but only one has Bob. Nothing has to be said."""
    set_speaker(hass)
    player()
    await resume(hass, user="Bob")
    assert _server_used(playback_rest) == URL_B


async def test_config_entry_picks_the_server(
    hass, servers, playback_rest, player
) -> None:
    """Naming the entry outright wins, even where the user is unambiguous."""
    set_speaker(hass)
    player()
    await resume(hass, user="Bob", config_entry=servers[1].entry_id)
    assert _server_used(playback_rest) == URL_B


async def test_the_same_user_on_both_servers_is_ambiguous(
    hass, servers, playback_rest, player
) -> None:
    """Alice on two servers, and no hint, is a question -- not a coin toss."""
    hass.config_entries.async_update_entry(
        servers[1], options={CONF_LINKED_USERS: _linked("Alice", "ha-alice")}
    )
    await hass.async_block_till_done()
    set_speaker(hass)
    player()

    with pytest.raises(ServiceValidationError, match="more than one"):
        await resume(hass, user="Alice")
    playback_rest.async_play_item.assert_not_awaited()


async def test_ambiguity_is_settled_by_naming_the_entry(
    hass, servers, playback_rest, player
) -> None:
    """And the way out of it is the selector the action now offers."""
    hass.config_entries.async_update_entry(
        servers[1], options={CONF_LINKED_USERS: _linked("Alice", "ha-alice")}
    )
    await hass.async_block_till_done()
    set_speaker(hass)
    player()

    await resume(hass, user="Alice", config_entry=servers[1].entry_id)
    assert _server_used(playback_rest) == URL_B


async def test_no_server_knows_the_user(hass, servers, playback_rest) -> None:
    """With one server its own error is clearer; with two, say so plainly."""
    set_speaker(hass)
    with pytest.raises(ServiceValidationError, match="None of the configured"):
        await resume(hass, user="Nobody")


async def test_unknown_config_entry(hass, servers, playback_rest) -> None:
    set_speaker(hass)
    with pytest.raises(ServiceValidationError, match="not an Audiobookshelf conf"):
        await resume(hass, user="Alice", config_entry="no-such-entry")


async def test_config_entry_that_is_not_loaded(hass, servers, playback_rest) -> None:
    """A configured but unloaded server is named, not silently skipped."""
    third = MockConfigEntry(
        domain=DOMAIN,
        title="Audiobookshelf Plus (offline)",
        unique_id="offline:13378",
        data={CONF_URL: "http://offline:13378", CONF_API_KEY: API_KEY},
        options={CONF_LINKED_USERS: _linked("Alice", "ha-alice")},
    )
    third.add_to_hass(hass)
    set_speaker(hass)

    with pytest.raises(ServiceValidationError, match="is not loaded"):
        await resume(hass, user="Alice", config_entry=third.entry_id)


# --------------------------------------------------- the destructive one


async def test_remove_progress_refuses_to_guess(hass, servers, progress_rest) -> None:
    """The whole point. This deletes progress that cannot be restored."""
    hass.config_entries.async_update_entry(
        servers[1], options={CONF_LINKED_USERS: _linked("Alice", "ha-alice")}
    )
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError, match="more than one"):
        await remove(hass, item_id="item-1")
    progress_rest.async_delete_progress.assert_not_awaited()


async def test_remove_progress_on_the_named_server(
    hass, servers, progress_rest
) -> None:
    """Named, it goes ahead -- and says which server it acted on."""
    result = await remove(hass, item_id="item-1", config_entry=servers[0].entry_id)
    progress_rest.async_delete_progress.assert_awaited_once_with("prog-a")
    assert result["server"] == servers[0].title


# ------------------------------------------------------- unloading one server


async def test_unloading_one_server_leaves_the_other_playing(
    hass, servers, playback_rest, player
) -> None:
    """Reloading one server must not close the session running on the other.

    Sessions are tracked in one map keyed by speaker, so a teardown that walked
    the whole map hung up on a listener who was mid-chapter on a different
    server for no reason they could see.
    """
    calls = player()
    set_speaker(hass)
    await resume(hass, user="Alice")
    set_speaker(hass, SPEAKER_B)
    await resume(hass, entity_id=SPEAKER_B, user="Bob")
    assert set(_sessions(hass)) == {SPEAKER, SPEAKER_B}
    assert len(calls["play"]) == 2

    await hass.config_entries.async_unload(servers[0].entry_id)
    await hass.async_block_till_done()

    assert set(_sessions(hass)) == {SPEAKER_B}
    assert _sessions(hass)[SPEAKER_B].entry_id == servers[1].entry_id
