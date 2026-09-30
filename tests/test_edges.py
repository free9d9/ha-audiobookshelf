"""Failure paths and boundary cases that only bite in the field."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from aioaudiobookshelf_plus.exceptions import LoginError
from aiohttp import ClientError
from homeassistant.components.media_player import MediaPlayerState
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_SUPPORTED_FEATURES
from homeassistant.core import State
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.audiobookshelf_plus.api import AudiobookshelfRest
from custom_components.audiobookshelf_plus.coordinator import (
    _looks_like_ebook_library,
    _ms_to_dt,
    _parse_session,
)
from custom_components.audiobookshelf_plus.playback import (
    _extrapolated_position,
    _track_for_position,
)

from .conftest import URL, open_session

# ---------------------------------------------------------------- coordinator


async def test_bad_api_key_triggers_reauth(hass, mock_config_entry, mock_rest) -> None:
    """A rejected key must start a reauth flow, not retry forever."""
    mock_config_entry.add_to_hass(hass)
    with patch(
        "custom_components.audiobookshelf_plus.coordinator.absapi.get_admin_client_by_token",
        AsyncMock(side_effect=LoginError),
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR


async def test_unreachable_server_retries(hass, mock_config_entry, mock_rest) -> None:
    """A server that is merely down should be retried, not abandoned."""
    mock_config_entry.add_to_hass(hass)
    with patch(
        "custom_components.audiobookshelf_plus.coordinator.absapi.get_admin_client_by_token",
        AsyncMock(side_effect=ClientError),
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_recent_items_failure(hass, init_integration, mock_abs_client):
    """A feed that will not load fails the refresh rather than half-updating."""

    async def _unreachable(**_kwargs):
        # Like the real client: the call is lazy, the request fails on iteration.
        raise ClientError
        yield

    mock_abs_client.get_library_items.side_effect = _unreachable
    await init_integration.runtime_data.async_refresh()
    assert init_integration.runtime_data.last_update_success is False


async def test_empty_library_feed(hass, init_integration, mock_abs_client):
    """A library with no items yields an empty feed, not a crash."""

    async def _nothing(**_kwargs):
        return
        yield  # an async generator that yields no pages

    mock_abs_client.get_library_items.side_effect = _nothing
    await init_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert init_integration.runtime_data.data.libraries["lib-books"].recent == []
    # An empty library cannot be classified, and must not claim to be e-books.
    assert init_integration.runtime_data.data.libraries["lib-books"].is_ebook is False


async def test_stream_update_for_an_unknown_user(hass, init_integration) -> None:
    """A user invited seconds ago can still start playing."""
    coordinator = init_integration.runtime_data
    await coordinator._on_stream_update(
        {"id": "u9", "username": "Carol", "type": "user", "session": open_session("u9")}
    )
    await hass.async_block_till_done()
    assert coordinator.data.users["u9"].username == "Carol"
    assert coordinator.data.users["u9"].session is not None


async def test_stream_update_before_first_refresh(hass, mock_config_entry) -> None:
    """An event arriving before any data exists is ignored."""
    from custom_components.audiobookshelf_plus.coordinator import (
        AudiobookshelfCoordinator,
    )

    mock_config_entry.add_to_hass(hass)
    coordinator = AudiobookshelfCoordinator(hass, mock_config_entry)
    await coordinator._on_stream_update({"id": "u1", "session": None})  # no data yet


def test_ms_to_dt_edges() -> None:
    assert _ms_to_dt(None) is None
    assert _ms_to_dt(0) is None
    assert _ms_to_dt("nonsense") is None
    assert _ms_to_dt(1_700_000_000_000).year == 2023


def test_parse_session_edges() -> None:
    assert _parse_session(None) is None
    assert _parse_session("not a dict") is None
    assert _parse_session({}) is None
    assert _parse_session({"id": "s", "updatedAt": None}) is None

    without_model = _parse_session(
        {
            "id": "s",
            "updatedAt": 1_700_000_000_000,
            "mediaPlayer": "web",
            "deviceInfo": {},
        }
    )
    assert without_model.device == "web"


def test_empty_library_is_not_an_ebook_library() -> None:
    assert _looks_like_ebook_library([]) is False


# ------------------------------------------------------------------ playback


def test_extrapolated_position_edges() -> None:
    """Position is extrapolated only while playing, and never runs backwards."""
    assert _extrapolated_position(State("media_player.x", "playing")) is None

    paused = State("media_player.x", "paused", {"media_position": 100})
    assert _extrapolated_position(paused) == 100.0

    updated = datetime.now(UTC) - timedelta(seconds=5)
    playing = State(
        "media_player.x",
        MediaPlayerState.PLAYING,
        {"media_position": 100, "media_position_updated_at": updated},
    )
    assert _extrapolated_position(playing) == pytest.approx(105, abs=2)

    # A clock that ran backwards must not rewind the book.
    future = datetime.now(UTC) + timedelta(seconds=60)
    skewed = State(
        "media_player.x",
        MediaPlayerState.PLAYING,
        {"media_position": 100, "media_position_updated_at": future},
    )
    assert _extrapolated_position(skewed) == 100.0


def test_track_for_position_falls_back_to_the_first() -> None:
    """A position past the end of every track still plays something."""
    tracks = [{"index": 1, "startOffset": 0.0, "duration": 10.0}]
    assert _track_for_position(tracks, 9999.0)["index"] == 1


async def test_wait_until_seekable_times_out(hass) -> None:
    """A speaker that never starts playing is not seekable."""
    from custom_components.audiobookshelf_plus.playback import (
        _async_wait_until_seekable,
    )

    hass.states.async_set("media_player.dead", "idle", {ATTR_SUPPORTED_FEATURES: 0})
    with patch(
        "custom_components.audiobookshelf_plus.playback.asyncio.sleep", AsyncMock()
    ):
        assert (
            await _async_wait_until_seekable(hass, "media_player.dead", 0.01) is False
        )


async def test_wait_until_seekable_entity_vanishes(hass) -> None:
    """The entity disappearing mid-wait is handled."""
    from custom_components.audiobookshelf_plus.playback import (
        _async_wait_until_seekable,
    )

    with patch(
        "custom_components.audiobookshelf_plus.playback.asyncio.sleep", AsyncMock()
    ):
        assert (
            await _async_wait_until_seekable(hass, "media_player.gone", 0.01) is False
        )


# -------------------------------------------------------------------- sensor


async def test_recently_added_attributes_when_library_vanishes(
    hass, init_integration
) -> None:
    """A sensor whose library was deleted reports nothing rather than raising."""
    from custom_components.audiobookshelf_plus.sensor import (
        AudiobookshelfRecentlyAddedSensor,
    )

    coordinator = init_integration.runtime_data
    library = coordinator.data.libraries["lib-books"]
    sensor = AudiobookshelfRecentlyAddedSensor(coordinator, library)
    coordinator.data.libraries.pop("lib-books")

    assert sensor.extra_state_attributes == {}
    assert sensor.native_value is None
    assert sensor.available is False


async def test_media_player_for_a_removed_user(hass, init_integration) -> None:
    """Same for a user who was deleted on the server."""
    from custom_components.audiobookshelf_plus.media_player import (
        AudiobookshelfMediaPlayer,
    )

    coordinator = init_integration.runtime_data
    user = coordinator.data.users["u1"]
    player = AudiobookshelfMediaPlayer(coordinator, user)
    coordinator.data.users.pop("u1")

    assert player.available is False
    assert player.state is MediaPlayerState.IDLE
    assert player.media_title is None
    assert player.media_duration is None
    assert player.media_position is None
    assert player.media_position_updated_at is None
    assert player.media_image_url is None
    assert player.media_content_type is None
    assert player.extra_state_attributes is None


async def test_media_player_podcast_content_type(
    hass, mock_config_entry, mock_abs_client, mock_rest
) -> None:
    """A podcast episode reports as a podcast."""
    session = open_session(user_id="u1")
    session["episodeId"] = "ep-1"
    mock_rest.async_get_open_sessions.return_value = [session]
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    from homeassistant.components.media_player import MediaType

    state = hass.states.get("media_player.audiobookshelf_plus_alice")
    assert state.attributes["media_content_type"] == MediaType.PODCAST


async def test_media_player_zero_duration(
    hass, mock_config_entry, mock_abs_client, mock_rest
) -> None:
    """A session with no duration reports none, rather than zero."""
    session = open_session(user_id="u1")
    session["duration"] = 0
    mock_rest.async_get_open_sessions.return_value = [session]
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("media_player.audiobookshelf_plus_alice")
    assert "media_duration" not in state.attributes


# ----------------------------------------------------------------------- api


async def test_empty_body(hass, aioclient_mock) -> None:
    """A 200 with no body is None, not a JSON error."""
    aioclient_mock.post(f"{URL}/api/libraries/l1/scan", text="")
    rest = AudiobookshelfRest(async_get_clientsession(hass), URL, "k")
    await rest.async_scan_library("l1")
    assert aioclient_mock.call_count == 1


async def test_old_items_still_fill_the_feed(hass, init_integration, mock_abs_client):
    """A library that has not grown in months still shows its newest items.

    Audiobookshelf's own Recently Added shelf drops everything added more than
    60 days ago, which left the live E-Books sensor unknown.
    """
    import time

    from .conftest import _item, _page

    old = _item("item-9", "An Old Ebook", 0.0)
    old.added_at = int((time.time() - 200 * 86400) * 1000)

    async def _only_old(**_kwargs):
        yield _page([old])

    mock_abs_client.get_library_items.side_effect = _only_old
    await init_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()
    library = init_integration.runtime_data.data.libraries["lib-ebooks"]
    assert [e["title"] for e in library.recent] == ["An Old Ebook"]
    assert library.newest_added is not None
    assert hass.states.get("sensor.e_books_recently_added").state not in (
        "unknown",
        "unavailable",
    )
