"""Downloaded ("local") playback on the media players.

Apps playing a downloaded book never open a session on the server and emit no
socket event; they sync the session into the database as they play. The
integration reads it back from /api/sessions (issue #5).
"""

from __future__ import annotations

import time
from datetime import timedelta

from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.audiobookshelf_plus.api import AudiobookshelfRestError
from custom_components.audiobookshelf_plus.const import (
    EVENT_PLAYBACK_STARTED,
    LOCAL_SESSION_FRESH_SECONDS,
    LOCAL_SESSION_PAUSED_WINDOW,
    PLAY_METHOD_LOCAL,
    SCAN_INTERVAL,
    SESSION_FRESH_SECONDS,
    SESSION_POLL_INTERVAL,
)

from .conftest import URL, open_session

PLAYER = "media_player.audiobookshelf_plus_alice"


def _ms_ago(seconds: float) -> int:
    return int((time.time() - seconds) * 1000)


def local_session(**kwargs) -> dict:
    """A /api/sessions entry for downloaded playback."""
    return open_session(**kwargs) | {"playMethod": PLAY_METHOD_LOCAL}


async def _tick(hass, freezer, delta: timedelta = SESSION_POLL_INTERVAL) -> None:
    freezer.tick(delta)
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()


async def test_downloaded_playback_shows_as_playing(
    hass, init_integration, mock_rest, freezer
) -> None:
    mock_rest.async_get_recent_sessions.return_value = [local_session()]
    await _tick(hass, freezer)

    state = hass.states.get(PLAYER)
    assert state.state == "playing"
    assert state.attributes["media_title"] == "A Book"
    assert state.attributes["downloaded"] is True


async def test_streamed_playback_is_not_marked_downloaded(
    hass, init_integration, mock_rest, freezer
) -> None:
    mock_rest.async_get_open_sessions.return_value = [open_session()]
    await _tick(hass, freezer)
    assert hass.states.get(PLAYER).attributes["downloaded"] is False


async def test_downloaded_playback_tolerates_a_cellular_sync_gap(
    hass, init_integration, mock_rest, freezer
) -> None:
    """The Android app syncs downloaded playback only every 60s on cellular."""
    gap = SESSION_FRESH_SECONDS + 30
    assert gap < LOCAL_SESSION_FRESH_SECONDS
    mock_rest.async_get_recent_sessions.return_value = [
        local_session(updated_at=_ms_ago(gap))
    ]
    await _tick(hass, freezer)
    assert hass.states.get(PLAYER).state == "playing"


async def test_downloaded_playback_pauses_then_goes_idle(
    hass, init_integration, mock_rest, freezer
) -> None:
    """The server never closes a local session, so it is aged out."""
    mock_rest.async_get_recent_sessions.return_value = [
        local_session(updated_at=_ms_ago(LOCAL_SESSION_FRESH_SECONDS + 30))
    ]
    await _tick(hass, freezer)
    assert hass.states.get(PLAYER).state == "paused"

    mock_rest.async_get_recent_sessions.return_value = [
        local_session(
            updated_at=_ms_ago(LOCAL_SESSION_PAUSED_WINDOW.total_seconds() + 60)
        )
    ]
    await _tick(hass, freezer)
    assert hass.states.get(PLAYER).state == "idle"


async def test_finished_streams_in_the_database_are_ignored(
    hass, init_integration, mock_rest, freezer
) -> None:
    """/api/sessions also holds closed streaming sessions; they must stay closed."""
    mock_rest.async_get_recent_sessions.return_value = [
        open_session() | {"playMethod": 0}
    ]
    await _tick(hass, freezer)
    assert hass.states.get(PLAYER).state == "idle"


async def test_the_full_poll_agrees_with_the_session_poll(
    hass, init_integration, mock_rest, freezer
) -> None:
    """Both polls must see downloaded playback, or the player flaps every 5 min."""
    mock_rest.async_get_recent_sessions.return_value = [local_session()]
    await _tick(hass, freezer)
    assert hass.states.get(PLAYER).state == "playing"

    users_calls = mock_rest.async_get_users.await_count
    # Still syncing: stamped with the time the clock is about to jump to.
    mock_rest.async_get_recent_sessions.return_value = [
        local_session(updated_at=_ms_ago(-SCAN_INTERVAL.total_seconds()))
    ]
    await _tick(hass, freezer, SCAN_INTERVAL)
    assert mock_rest.async_get_users.await_count > users_calls  # full poll ran
    assert hass.states.get(PLAYER).state == "playing"


async def test_fresher_session_wins_across_both_sources(
    hass, init_integration, mock_rest, freezer
) -> None:
    """Someone who switches from streaming to a download shows the download."""
    mock_rest.async_get_open_sessions.return_value = [
        open_session(session_id="stream", updated_at=_ms_ago(600))
    ]
    mock_rest.async_get_recent_sessions.return_value = [
        local_session(session_id="download")
    ]
    await _tick(hass, freezer)
    assert hass.states.get(PLAYER).attributes["session_id"] == "download"


async def test_downloaded_playback_fires_playback_started(
    hass, init_integration, mock_rest, freezer
) -> None:
    events = []
    hass.bus.async_listen(EVENT_PLAYBACK_STARTED, lambda e: events.append(e.data))
    mock_rest.async_get_recent_sessions.return_value = [local_session()]
    await _tick(hass, freezer)
    assert [e["user"] for e in events] == ["Alice"]


async def test_recent_sessions_failing_leaves_streaming_alone(
    hass, init_integration, mock_rest, freezer
) -> None:
    """A non-admin key, or a hiccup, hides downloads only; streams still show."""
    mock_rest.async_get_recent_sessions.side_effect = AudiobookshelfRestError("404")
    mock_rest.async_get_open_sessions.return_value = [open_session()]
    await _tick(hass, freezer)
    assert hass.states.get(PLAYER).state == "playing"


async def test_recent_sessions_request(hass, aioclient_mock) -> None:
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    from custom_components.audiobookshelf_plus.api import AudiobookshelfRest

    aioclient_mock.get(f"{URL}/api/sessions", json={"sessions": [{"id": "s"}]})
    rest = AudiobookshelfRest(async_get_clientsession(hass), URL, "key")
    assert await rest.async_get_recent_sessions(20) == [{"id": "s"}]
    _, url, _, _ = aioclient_mock.mock_calls[0]
    assert url.query == {"sort": "updatedAt", "desc": "1", "itemsPerPage": "20"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{URL}/api/sessions", json=[])
    assert await rest.async_get_recent_sessions(20) == []


def test_device_falls_back_to_the_app_name() -> None:
    """AudioBooth reports no model, only the app; that beats a bare "ios"."""
    from custom_components.audiobookshelf_plus.coordinator import _parse_session

    audiobooth = open_session() | {
        "mediaPlayer": "ios",
        "deviceInfo": {"clientName": "AudioBooth iOS 1.11 (1784179683)"},
    }
    assert _parse_session(audiobooth).device == "AudioBooth iOS 1.11"
    bare = open_session() | {"mediaPlayer": "ios", "deviceInfo": {}}
    assert _parse_session(bare).device == "ios"


def test_apps_that_sync_every_20s_pause_sooner() -> None:
    """Absorb and AudioBooth push downloaded playback every 20s on any network.

    They get the streaming window, so a pause shows in under a minute. Anything
    else downloaded keeps the wide window the official Android app needs.
    """
    from custom_components.audiobookshelf_plus.coordinator import _parse_session

    def downloaded(client: str | None) -> dict:
        return local_session() | {"deviceInfo": {"clientName": client}}

    for client in ("Absorb 1.10.0", "AudioBooth iOS 1.11 (1784179683)"):
        assert _parse_session(downloaded(client)).fresh_seconds == (
            SESSION_FRESH_SECONDS
        )
    for client in ("Audiobookshelf Android", None):
        assert _parse_session(downloaded(client)).fresh_seconds == (
            LOCAL_SESSION_FRESH_SECONDS
        )
    assert _parse_session(open_session()).fresh_seconds == SESSION_FRESH_SECONDS


async def test_absorb_pause_shows_within_a_minute(
    hass, init_integration, mock_rest, freezer
) -> None:
    """Brian's own client: Absorb on a Samsung, playing a downloaded book."""
    absorb = local_session(updated_at=_ms_ago(SESSION_FRESH_SECONDS + 10)) | {
        "mediaPlayer": "exo-player",
        "deviceInfo": {
            "clientName": "Absorb",
            "clientVersion": "1.10.0",
            "manufacturer": "samsung",
            "model": "SM-F968U1",
        },
    }
    mock_rest.async_get_recent_sessions.return_value = [absorb]
    await _tick(hass, freezer)
    state = hass.states.get(PLAYER)
    assert state.state == "paused"
    assert state.attributes["device"] == "samsung SM-F968U1"
    assert state.attributes["downloaded"] is True
