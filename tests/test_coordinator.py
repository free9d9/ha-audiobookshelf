"""The push-first coordinator, and the Audiobookshelf quirks it works around."""

from __future__ import annotations

import time
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory

from custom_components.audiobookshelf.api import AudiobookshelfRestError
from custom_components.audiobookshelf.const import (
    DOMAIN,
    EVENT_PLAYBACK_STARTED,
    EVENT_PLAYBACK_STOPPED,
    SCAN_INTERVAL,
    SCAN_INTERVAL_LISTENING,
)
from custom_components.audiobookshelf.coordinator import _api_key_expiry
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .conftest import make_api_key, open_session


async def test_setup_builds_libraries_and_users(hass, init_integration) -> None:
    """Both libraries and all users land in coordinator data."""
    data = init_integration.runtime_data.data
    assert set(data.libraries) == {"lib-books", "lib-ebooks"}
    assert data.libraries["lib-books"].total_items == 10
    assert set(data.users) == {"u1", "u2"}
    assert data.connected is True


async def test_ebook_library_detected_by_content(hass, init_integration) -> None:
    """Audiobookshelf types both libraries as `book`; only content tells them apart."""
    data = init_integration.runtime_data.data
    assert data.libraries["lib-books"].is_ebook is False
    assert data.libraries["lib-ebooks"].is_ebook is True


async def test_recently_added_entry_shape(hass, init_integration) -> None:
    """The card feed carries what a poster wall needs, and no description."""
    entry = init_integration.runtime_data.data.libraries["lib-books"].recent[0]
    assert entry["title"] == "A Book"
    assert entry["author"] == "An Author"
    assert entry["media_type"] == "audiobook"
    assert entry["runtime"] == 60
    assert entry["genres"] == "Fantasy, Fiction"
    assert entry["poster"].startswith("/api/audiobookshelf/cover/")
    assert "authSig=" in entry["poster"]
    assert "description" not in entry


async def test_socket_token_is_bootstrapped_from_the_api_key(
    hass, init_integration, mock_rest
) -> None:
    """The API key is rejected on the socket, so /api/me hands us a usable token."""
    mock_rest.async_get_me.assert_awaited()


async def test_socket_failure_falls_back_to_polling(
    hass, mock_config_entry, mock_abs_client, mock_rest
) -> None:
    """No realtime token means polling, not a broken integration."""
    mock_rest.async_get_me.side_effect = AudiobookshelfRestError("nope")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert mock_config_entry.runtime_data.connected is False


async def test_stale_open_sessions_are_not_live(
    hass, mock_config_entry, mock_abs_client, mock_rest
) -> None:
    """Audiobookshelf never closes sessions, so presence proves nothing."""
    stale_ms = int((time.time() - 3600) * 1000)
    mock_rest.async_get_open_sessions.return_value = [
        open_session(user_id="u1", updated_at=stale_ms)
    ]
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    data = mock_config_entry.runtime_data.data
    assert data.users["u1"].session is not None
    assert data.users["u1"].session.is_live is False
    assert data.listening_now == []


async def test_live_session_shortens_the_poll_interval(
    hass, mock_config_entry, mock_abs_client, mock_rest
) -> None:
    """Pause emits nothing, so we poll faster while something plays."""
    mock_rest.async_get_open_sessions.return_value = [open_session(user_id="u1")]
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = mock_config_entry.runtime_data
    assert coordinator.data.listening_now[0].username == "Alice"
    assert coordinator.update_interval == SCAN_INTERVAL_LISTENING

    mock_rest.async_get_open_sessions.return_value = []
    await coordinator.async_refresh()
    assert coordinator.update_interval == SCAN_INTERVAL


async def test_stream_update_starts_playback_instantly(hass, init_integration) -> None:
    """A new session id means a genuine start: fire the event, update state now."""
    coordinator = init_integration.runtime_data
    events = []
    hass.bus.async_listen(EVENT_PLAYBACK_STARTED, lambda e: events.append(e.data))

    await coordinator._on_stream_update(
        {"id": "u1", "username": "Alice", "type": "admin", "session": open_session()}
    )
    await hass.async_block_till_done()

    assert coordinator.data.users["u1"].session.session_id == "sess-1"
    assert events[0]["title"] == "A Book"
    assert events[0]["user"] == "Alice"
    assert events[0]["device"] == "samsung SM-F946U1"


async def test_stream_update_for_a_known_session_defers_to_the_poll(
    hass, init_integration
) -> None:
    """Audiobookshelf emits a close event that still contains the session.

    closeSession() fires user_stream_update *before* removeSession(), so a close
    is byte-identical to a start. The session id is the only tell, and
    /api/sessions/open is the authority.
    """
    coordinator = init_integration.runtime_data
    payload = {
        "id": "u1",
        "username": "Alice",
        "type": "admin",
        "session": open_session(),
    }
    await coordinator._on_stream_update(payload)
    await hass.async_block_till_done()

    with patch.object(coordinator, "async_request_refresh", AsyncMock()) as refresh:
        await coordinator._on_stream_update(payload)
    refresh.assert_awaited_once()


async def test_stopped_playback_is_detected_by_the_poll(
    hass, mock_config_entry, mock_abs_client, mock_rest
) -> None:
    """The socket can never tell us a session ended; the poll can."""
    mock_rest.async_get_open_sessions.return_value = [open_session(user_id="u1")]
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    events = []
    hass.bus.async_listen(EVENT_PLAYBACK_STOPPED, lambda e: events.append(e.data))

    mock_rest.async_get_open_sessions.return_value = []
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert mock_config_entry.runtime_data.data.users["u1"].session is None
    assert events[0]["title"] == "A Book"


async def test_stream_update_ignores_junk(hass, init_integration) -> None:
    """Malformed payloads must not take the integration down."""
    coordinator = init_integration.runtime_data
    await coordinator._on_stream_update("not a dict")
    await coordinator._on_stream_update({"no": "id"})
    assert coordinator.last_update_success


async def test_item_events_request_a_coalesced_refresh(hass, init_integration) -> None:
    """A 500-book scan must produce one refresh, not five hundred."""
    coordinator = init_integration.runtime_data
    with patch.object(coordinator, "async_request_refresh", AsyncMock()) as refresh:
        await coordinator._on_library_changed(object())
    refresh.assert_awaited_once()
    # The debouncer, not the handler, is what does the coalescing.
    assert coordinator._debounced_refresh.cooldown == 5.0


async def test_update_failure_is_reported(
    hass, mock_config_entry, mock_abs_client, mock_rest
) -> None:
    """A server that goes away marks the coordinator failed rather than crashing."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    mock_rest.async_get_libraries_with_stats.side_effect = AudiobookshelfRestError(
        "boom"
    )
    await mock_config_entry.runtime_data.async_refresh()
    assert mock_config_entry.runtime_data.last_update_success is False


# ------------------------------------------------------------- key expiry


def test_api_key_expiry_parsing() -> None:
    """Read `exp` out of the key, tolerate anything that is not a key."""
    assert _api_key_expiry(make_api_key()) is None
    assert _api_key_expiry(make_api_key(expires_in_days=3)) is not None
    assert _api_key_expiry("not-a-jwt") is None
    assert _api_key_expiry("") is None
    assert _api_key_expiry("a.!!!not-base64!!!.c") is None


async def test_expiring_key_raises_a_repair_issue(
    hass, mock_abs_client, mock_rest
) -> None:
    """Warn before the key lapses, not after."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.audiobookshelf.const import CONF_LINKED_USERS
    from homeassistant.const import CONF_API_KEY, CONF_URL

    from .conftest import URL

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="abs.example.com:13378",
        data={CONF_URL: URL, CONF_API_KEY: make_api_key(expires_in_days=3)},
        options={CONF_LINKED_USERS: {}},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    issues = ir.async_get(hass)
    issue = issues.async_get_issue(DOMAIN, f"api_key_expiring_{entry.entry_id}")
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.WARNING


async def test_non_expiring_key_raises_nothing(hass, init_integration) -> None:
    """The recommended setup is quiet."""
    issues = ir.async_get(hass)
    assert (
        issues.async_get_issue(DOMAIN, f"api_key_expiring_{init_integration.entry_id}")
        is None
    )
