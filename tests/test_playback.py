"""The continue_listening action, and the two ways it once destroyed progress."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.audiobookshelf.api import AudiobookshelfRestError
from custom_components.audiobookshelf.const import (
    CONF_LINKED_USERS,
    DOMAIN,
    SERVICE_CONTINUE_LISTENING,
)
from homeassistant.components.media_player import (
    MediaPlayerEntityFeature as Feature,
    MediaPlayerState,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_SUPPORTED_FEATURES
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .conftest import make_api_key

SPEAKER = "media_player.speaker"

# A real speaker, idle. Note it does NOT advertise SEEK: players only do that
# once media is loaded, which is exactly what tripped the original bug.
SPEAKER_IDLE = int(Feature.PLAY_MEDIA | Feature.PAUSE | Feature.STOP)
SPEAKER_PLAYING = int(SPEAKER_IDLE | Feature.SEEK)
# A camera talkback speaker: plays and stops, cannot pause.
TALKBACK_FEATURES = int(Feature.PLAY_MEDIA | Feature.STOP | Feature.VOLUME_SET)

SESSION = {
    "id": "abs-sess",
    "duration": 3600.0,
    "audioTracks": [
        {"index": 1, "startOffset": 0.0, "duration": 1800.0},
        {"index": 2, "startOffset": 1800.0, "duration": 1800.0},
    ],
}
IN_PROGRESS = [{"id": "item-1", "media": {"metadata": {"title": "A Book"}}}]


@pytest.fixture
def linked(hass, init_integration):
    """One linked Audiobookshelf user, mapped to a Home Assistant account."""
    hass.config_entries.async_update_entry(
        init_integration,
        options={
            CONF_LINKED_USERS: {
                "u1": {
                    "username": "Alice",
                    "api_key": make_api_key(),
                    "key_id": "key-1",
                    "ha_user_id": "ha-alice",
                }
            }
        },
    )
    return init_integration


@pytest.fixture
def user_rest():
    """The per-user REST client that continue_listening builds."""
    rest = MagicMock()
    rest.async_items_in_progress = AsyncMock(return_value=IN_PROGRESS)
    rest.async_get_progress = AsyncMock(
        return_value={"currentTime": 600.0, "isFinished": False}
    )
    rest.async_play_item = AsyncMock(return_value=SESSION)
    rest.async_sync_session = AsyncMock()
    rest.async_close_session = AsyncMock()
    rest.async_close_session_without_sync = AsyncMock()
    with patch(
        "custom_components.audiobookshelf.playback.AudiobookshelfRest",
        return_value=rest,
    ):
        yield rest


@pytest.fixture
def player(hass):
    """Register real media_player services so the action drives the real path."""
    calls: dict[str, list] = {"play": [], "seek": []}

    def _make(on_play=None):
        async def _play(service_call):
            calls["play"].append(dict(service_call.data))
            if on_play is not None:
                on_play(dict(service_call.data))

        async def _seek(service_call):
            calls["seek"].append(dict(service_call.data))

        hass.services.async_register("media_player", "play_media", _play)
        hass.services.async_register("media_player", "media_seek", _seek)
        return calls

    return _make


def set_speaker(
    hass,
    state=MediaPlayerState.IDLE,
    features=SPEAKER_IDLE,
    position=None,
    content_id=None,
):
    attrs = {ATTR_SUPPORTED_FEATURES: features}
    if position is not None:
        attrs["media_position"] = position
    if content_id is not None:
        attrs["media_content_id"] = content_id
    hass.states.async_set(SPEAKER, state, attrs)


def playing(hass, features=None, position=0, data=None):
    """What a speaker looks like once it has our stream loaded."""
    set_speaker(
        hass,
        state=MediaPlayerState.PLAYING,
        features=SPEAKER_PLAYING if features is None else features,
        position=position,
        content_id=(data or {}).get("media_content_id"),
    )


async def call(hass, user_id=None, **data):
    await hass.services.async_call(
        DOMAIN,
        SERVICE_CONTINUE_LISTENING,
        {ATTR_ENTITY_ID: SPEAKER, **data},
        blocking=True,
        context=Context(user_id=user_id) if user_id else None,
    )


# ------------------------------------------------------------- validation


async def test_no_linked_users(hass, init_integration) -> None:
    set_speaker(hass)
    with pytest.raises(ServiceValidationError, match="No Audiobookshelf users"):
        await call(hass)


async def test_unknown_user(hass, linked, user_rest) -> None:
    set_speaker(hass)
    with pytest.raises(ServiceValidationError, match="not a linked"):
        await call(hass, user="Nobody")


async def test_user_named_explicitly(hass, linked, user_rest, player) -> None:
    """An explicit user beats the call context."""
    set_speaker(hass)
    player(on_play=lambda data: playing(hass, position=0, data=data))
    await call(hass, user="alice")  # case-insensitive
    user_rest.async_play_item.assert_awaited_once()


async def test_user_resolved_from_the_calling_ha_account(
    hass, linked, user_rest, player
) -> None:
    """Home Assistant puts the invoking user on the call context."""
    set_speaker(hass)
    player(on_play=lambda data: playing(hass, position=0, data=data))
    await call(hass, user_id="ha-alice")
    user_rest.async_play_item.assert_awaited_once()


async def test_single_linked_user_needs_no_hint(
    hass, linked, user_rest, player
) -> None:
    """With exactly one linked user there is nothing to disambiguate."""
    set_speaker(hass)
    player(on_play=lambda data: playing(hass, position=0, data=data))
    await call(hass)
    user_rest.async_play_item.assert_awaited_once()


async def test_ambiguous_user(hass, init_integration, user_rest) -> None:
    """Two linked users and no hint is an error, not a coin toss."""
    hass.config_entries.async_update_entry(
        init_integration,
        options={
            CONF_LINKED_USERS: {
                "u1": {
                    "username": "Alice",
                    "api_key": "a",
                    "key_id": "1",
                    "ha_user_id": None,
                },
                "u2": {
                    "username": "Bob",
                    "api_key": "b",
                    "key_id": "2",
                    "ha_user_id": None,
                },
            }
        },
    )
    set_speaker(hass)
    with pytest.raises(ServiceValidationError, match="Could not tell which"):
        await call(hass)


async def test_missing_entity(hass, linked, user_rest) -> None:
    with pytest.raises(ServiceValidationError, match="does not exist"):
        await call(hass)


async def test_entity_cannot_play_media(hass, linked, user_rest) -> None:
    set_speaker(hass, features=int(Feature.PAUSE))
    with pytest.raises(ServiceValidationError, match="cannot play media"):
        await call(hass)


async def test_talkback_speaker_is_refused(hass, linked, user_rest) -> None:
    """A camera speaker plays and stops but cannot pause. Do not send it a book."""
    set_speaker(hass, features=TALKBACK_FEATURES)
    with pytest.raises(ServiceValidationError, match="cannot be paused"):
        await call(hass)
    # And crucially, nothing was asked of Audiobookshelf.
    user_rest.async_play_item.assert_not_awaited()


async def test_nothing_in_progress(hass, linked, user_rest) -> None:
    set_speaker(hass)
    user_rest.async_items_in_progress.return_value = []
    with pytest.raises(ServiceValidationError, match="nothing in progress"):
        await call(hass)


async def test_server_unreachable(hass, linked, user_rest) -> None:
    set_speaker(hass)
    user_rest.async_items_in_progress.side_effect = AudiobookshelfRestError("down")
    with pytest.raises(HomeAssistantError, match="Could not reach"):
        await call(hass)


async def test_session_cannot_be_opened(hass, linked, user_rest) -> None:
    set_speaker(hass)
    user_rest.async_play_item.side_effect = AudiobookshelfRestError("busy")
    with pytest.raises(HomeAssistantError, match="Could not start"):
        await call(hass)


async def test_no_audio_tracks(hass, linked, user_rest) -> None:
    set_speaker(hass)
    user_rest.async_play_item.return_value = {"id": "s", "audioTracks": []}
    with pytest.raises(HomeAssistantError, match="no audio tracks"):
        await call(hass)


async def test_integration_not_loaded(hass, linked, user_rest) -> None:
    """The action exists even when no entry is loaded, and says so."""
    await hass.config_entries.async_unload(linked.entry_id)
    await hass.async_block_till_done()
    set_speaker(hass)
    with pytest.raises(ServiceValidationError, match="not loaded"):
        await call(hass)


# ---------------------------------------------------------------- playback


async def test_resume_plays_and_seeks(hass, linked, user_rest, player) -> None:
    """The happy path: unauthenticated track URL, then seek to the saved position."""
    set_speaker(hass)
    calls = player(on_play=lambda data: playing(hass, position=0, data=data))
    await call(hass, user_id="ha-alice")

    assert calls["play"][0]["media_content_id"].endswith(
        "/public/session/abs-sess/track/1"
    )
    assert calls["seek"][0]["seek_position"] == 600.0


async def test_resume_picks_the_track_containing_the_position(
    hass, linked, user_rest, player
) -> None:
    """Multi-file rips: each track carries its own offset into the whole book."""
    user_rest.async_get_progress.return_value = {"currentTime": 2400.0}
    set_speaker(hass)
    calls = player(on_play=lambda data: playing(hass, position=0, data=data))
    await call(hass, user_id="ha-alice")

    assert calls["play"][0]["media_content_id"].endswith("/track/2")
    assert calls["seek"][0]["seek_position"] == 600.0  # 2400 - 1800 offset


async def test_finished_book_starts_over(hass, linked, user_rest, player) -> None:
    """Resuming past the end is not resuming."""
    user_rest.async_get_progress.return_value = {
        "currentTime": 3599.0,
        "isFinished": True,
    }
    set_speaker(hass)
    calls = player(on_play=lambda data: playing(hass, position=0, data=data))
    await call(hass, user_id="ha-alice")

    assert calls["play"]
    assert calls["seek"] == []  # offset is 0; there is nothing to seek to


async def test_seek_can_be_turned_off(hass, linked, user_rest, player) -> None:
    """seek: false starts the track from the top, on purpose."""
    set_speaker(hass)
    calls = player(on_play=lambda data: playing(hass, position=0, data=data))
    await call(hass, user_id="ha-alice", seek=False)
    assert calls["seek"] == []


# ------------------------------------------- the two progress-destroying bugs


async def test_a_speaker_that_cannot_seek_does_not_write_progress(
    hass, linked, user_rest, player
) -> None:
    """REGRESSION.

    The player never gains SEEK, so playback starts at the top of the track.
    Writing that position back would rewind the listener's book. It must not.
    """
    from custom_components.audiobookshelf.playback import _sessions

    set_speaker(hass)
    player(
        on_play=lambda data: playing(hass, features=SPEAKER_IDLE, position=0, data=data)
    )
    with patch("custom_components.audiobookshelf.playback.asyncio.sleep", AsyncMock()):
        await call(hass, user_id="ha-alice")

    assert _sessions(hass)[SPEAKER].sync_enabled is False

    hass.states.async_set(
        SPEAKER, MediaPlayerState.OFF, {ATTR_SUPPORTED_FEATURES: SPEAKER_IDLE}
    )
    await hass.async_block_till_done()

    user_rest.async_close_session_without_sync.assert_awaited_once_with("abs-sess")
    user_rest.async_close_session.assert_not_awaited()


async def test_stopping_never_writes_a_zero(hass, linked, user_rest, player) -> None:
    """REGRESSION.

    A stopped player reports no position. The original code read that as 0.0 and
    wrote it to Audiobookshelf, wiping the listener's place in the book. The last
    position we actually saw must be used instead.
    """
    from custom_components.audiobookshelf.playback import _sessions

    set_speaker(hass)
    player(on_play=lambda data: playing(hass, position=600, data=data))
    await call(hass, user_id="ha-alice")

    content_id = _sessions(hass)[SPEAKER].content_id
    # It plays on to 900s...
    set_speaker(
        hass,
        state=MediaPlayerState.PLAYING,
        features=SPEAKER_PLAYING,
        position=900,
        content_id=content_id,
    )
    await hass.async_block_till_done()
    # ...then is switched off, and forgets where it was.
    hass.states.async_set(
        SPEAKER, MediaPlayerState.OFF, {ATTR_SUPPORTED_FEATURES: SPEAKER_PLAYING}
    )
    await hass.async_block_till_done()

    user_rest.async_close_session.assert_awaited_once()
    written = user_rest.async_close_session.await_args.args[1]
    assert written == pytest.approx(900.0, abs=2)
    assert written != 0.0


async def test_switching_media_closes_our_session(
    hass, linked, user_rest, player
) -> None:
    """Someone plays Spotify on the speaker; our session should not linger."""
    from custom_components.audiobookshelf.playback import _sessions

    set_speaker(hass)
    player(on_play=lambda data: playing(hass, position=600, data=data))
    await call(hass, user_id="ha-alice")

    set_speaker(
        hass,
        state=MediaPlayerState.PLAYING,
        features=SPEAKER_PLAYING,
        position=5,
        content_id="spotify:track:whatever",
    )
    await hass.async_block_till_done()
    assert SPEAKER not in _sessions(hass)


async def test_speaker_disappearing_closes_our_session(
    hass, linked, user_rest, player
) -> None:
    """An unplugged speaker must not leave a session open on the server."""
    from custom_components.audiobookshelf.playback import _sessions

    set_speaker(hass)
    player(on_play=lambda data: playing(hass, position=600, data=data))
    await call(hass, user_id="ha-alice")

    hass.states.async_remove(SPEAKER)
    await hass.async_block_till_done()
    assert SPEAKER not in _sessions(hass)


# ------------------------------------------------------------ progress sync


async def test_progress_syncs_while_playing(hass, linked, user_rest, player) -> None:
    """The periodic tick pushes the speaker position back to Audiobookshelf."""
    from custom_components.audiobookshelf.playback import _make_ticker, _sessions

    set_speaker(hass)
    player(on_play=lambda data: playing(hass, position=700, data=data))
    await call(hass, user_id="ha-alice")

    await _make_ticker(hass, SPEAKER)(None)
    user_rest.async_sync_session.assert_awaited()
    assert user_rest.async_sync_session.await_args.args[1] == pytest.approx(700, abs=2)
    assert _sessions(hass)[SPEAKER].last_position == pytest.approx(700, abs=2)


async def test_ticker_is_quiet_when_not_playing(
    hass, linked, user_rest, player
) -> None:
    """A paused speaker records its position but writes nothing."""
    from custom_components.audiobookshelf.playback import _make_ticker, _sessions

    set_speaker(hass)
    player(on_play=lambda data: playing(hass, position=600, data=data))
    await call(hass, user_id="ha-alice")

    user_rest.async_sync_session.reset_mock()
    set_speaker(
        hass, state=MediaPlayerState.PAUSED, features=SPEAKER_PLAYING, position=650
    )
    await _make_ticker(hass, SPEAKER)(None)
    user_rest.async_sync_session.assert_not_awaited()
    assert _sessions(hass)[SPEAKER].last_position == pytest.approx(650, abs=2)


async def test_ticker_after_session_gone(hass, linked, user_rest) -> None:
    """A tick that fires after teardown is a no-op."""
    from custom_components.audiobookshelf.playback import _make_ticker

    await _make_ticker(hass, SPEAKER)(None)  # must not raise


async def test_sync_failure_is_swallowed(hass, linked, user_rest, player) -> None:
    """A hiccup mid-book must not raise into the Home Assistant timer."""
    from custom_components.audiobookshelf.playback import _make_ticker

    set_speaker(hass)
    player(on_play=lambda data: playing(hass, position=700, data=data))
    await call(hass, user_id="ha-alice")

    user_rest.async_sync_session.side_effect = AudiobookshelfRestError("hiccup")
    await _make_ticker(hass, SPEAKER)(None)  # must not raise


async def test_close_failure_is_swallowed(hass, linked, user_rest, player) -> None:
    """Nor must a failure to close."""
    from custom_components.audiobookshelf.playback import async_teardown

    set_speaker(hass)
    player(on_play=lambda data: playing(hass, position=700, data=data))
    await call(hass, user_id="ha-alice")

    user_rest.async_close_session.side_effect = AudiobookshelfRestError("gone")
    await async_teardown(hass)  # must not raise


async def test_teardown_closes_open_sessions(hass, linked, user_rest, player) -> None:
    """Unloading the integration must not leave sessions open on the server."""
    from custom_components.audiobookshelf.playback import _sessions, async_teardown

    set_speaker(hass)
    player(on_play=lambda data: playing(hass, position=600, data=data))
    await call(hass, user_id="ha-alice")

    await async_teardown(hass)
    assert _sessions(hass) == {}
    user_rest.async_close_session.assert_awaited()


async def test_resuming_twice_replaces_the_session(
    hass, linked, user_rest, player
) -> None:
    """Starting again on the same speaker closes the first session first."""
    from custom_components.audiobookshelf.playback import _sessions

    set_speaker(hass)
    player(on_play=lambda data: playing(hass, position=600, data=data))
    await call(hass, user_id="ha-alice")
    await call(hass, user_id="ha-alice")

    user_rest.async_close_session.assert_awaited()
    assert len(_sessions(hass)) == 1
