"""The 15-second playback poll: how pause and resume reach Home Assistant.

Audiobookshelf pushes nothing to an admin on pause or resume, so without this a
resume waited for the five-minute poll (issue #5).
"""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta

from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.audiobookshelf_plus.api import AudiobookshelfRestError
from custom_components.audiobookshelf_plus.const import (
    EVENT_PLAYBACK_STARTED,
    SCAN_INTERVAL,
    SESSION_FRESH_SECONDS,
    SESSION_POLL_INTERVAL,
)

from .conftest import open_session

PLAYER = "media_player.audiobookshelf_plus_alice"


def _ms_ago(seconds: float) -> int:
    return int((time.time() - seconds) * 1000)


async def _tick(hass, freezer, delta: timedelta = SESSION_POLL_INTERVAL) -> None:
    freezer.tick(delta)
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()


async def test_resume_shows_within_one_session_poll(
    hass, init_integration, mock_rest, freezer
) -> None:
    """Resuming a paused book produces no socket event; the poll must see it."""
    paused = open_session(updated_at=_ms_ago(SESSION_FRESH_SECONDS + 60))
    mock_rest.async_get_open_sessions.return_value = [paused]
    await _tick(hass, freezer)
    assert hass.states.get(PLAYER).state == "paused"

    users_calls = mock_rest.async_get_users.await_count
    mock_rest.async_get_open_sessions.return_value = [open_session()]
    await _tick(hass, freezer)
    assert hass.states.get(PLAYER).state == "playing"
    # Only sessions were asked for: the heavy endpoints stay on five minutes.
    assert mock_rest.async_get_users.await_count == users_calls


async def test_pause_shows_once_the_session_goes_stale(
    hass, init_integration, mock_rest, freezer
) -> None:
    """A pause changes nothing in the payload; the session only ages."""
    synced = _ms_ago(0)
    mock_rest.async_get_open_sessions.return_value = [open_session(updated_at=synced)]
    await _tick(hass, freezer)
    assert hass.states.get(PLAYER).state == "playing"

    # Same payload, byte for byte, but now past the freshness window.
    freezer.tick(timedelta(seconds=SESSION_FRESH_SECONDS))
    await _tick(hass, freezer)
    assert hass.states.get(PLAYER).state == "paused"


async def test_an_unchanged_poll_writes_nothing(
    hass, init_integration, mock_rest, freezer
) -> None:
    """Every entity writing every 15 seconds would flood the recorder."""
    mock_rest.async_get_open_sessions.return_value = []
    await _tick(hass, freezer)
    coordinator = init_integration.runtime_data
    before = coordinator.data

    await _tick(hass, freezer)
    assert coordinator.data is before


async def test_a_new_session_fires_playback_started(
    hass, init_integration, mock_rest, freezer
) -> None:
    events = []
    hass.bus.async_listen(EVENT_PLAYBACK_STARTED, lambda e: events.append(e.data))
    mock_rest.async_get_open_sessions.return_value = [open_session()]
    await _tick(hass, freezer)
    assert [e["user"] for e in events] == ["Alice"]


async def test_constant_listening_does_not_starve_the_full_poll(
    hass, init_integration, mock_rest, freezer
) -> None:
    """A session that changes every sync must not keep postponing libraries."""
    users_calls = mock_rest.async_get_users.await_count
    elapsed = timedelta()
    position = 0.0
    while elapsed <= SCAN_INTERVAL + SESSION_POLL_INTERVAL:
        position += 15
        session = open_session() | {"currentTime": position}
        mock_rest.async_get_open_sessions.return_value = [session]
        await _tick(hass, freezer)
        elapsed += SESSION_POLL_INTERVAL
    assert mock_rest.async_get_users.await_count > users_calls


async def test_a_failed_session_poll_is_not_an_outage(
    hass, init_integration, mock_rest
) -> None:
    """One missed tick leaves the last known state alone."""
    coordinator = init_integration.runtime_data
    before = coordinator.data
    mock_rest.async_get_open_sessions.side_effect = AudiobookshelfRestError("blip")
    await coordinator._async_poll_sessions()
    await hass.async_block_till_done()
    assert coordinator.data is before
    assert coordinator.last_update_success is True
    assert hass.states.get(PLAYER).state != "unavailable"
    assert coordinator._session_poll_busy is False


async def test_polls_do_not_pile_up_behind_a_slow_server(
    hass, init_integration, mock_rest
) -> None:
    """A tick that lands while the last is still waiting is skipped, not queued."""
    coordinator = init_integration.runtime_data
    release = asyncio.Event()
    calls = 0

    async def _slow() -> list:
        nonlocal calls
        calls += 1
        await release.wait()
        return []

    mock_rest.async_get_open_sessions.side_effect = _slow
    first = hass.async_create_task(coordinator._async_poll_sessions())
    await asyncio.sleep(0)
    await coordinator._async_poll_sessions()  # returns at once: one in flight
    release.set()
    await first
    assert calls == 1


async def test_session_poll_waits_for_first_data(hass, mock_config_entry) -> None:
    """Before the first refresh there is nothing to update."""
    from custom_components.audiobookshelf_plus.coordinator import (
        AudiobookshelfCoordinator,
    )

    mock_config_entry.add_to_hass(hass)
    coordinator = AudiobookshelfCoordinator(hass, mock_config_entry)
    await coordinator._async_poll_sessions()
    assert coordinator.data is None
