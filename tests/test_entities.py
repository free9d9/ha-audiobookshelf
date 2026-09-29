"""Sensors, binary sensors, buttons and media players."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from homeassistant.components.media_player import MediaPlayerState, MediaType
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON, Platform
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.audiobookshelf_plus.api import AudiobookshelfRestError
from custom_components.audiobookshelf_plus.const import DOMAIN

from .conftest import open_session


async def test_recently_added_sensor(hass, init_integration) -> None:
    """State is when the newest item landed; data[0] is the card header."""
    state = hass.states.get("sensor.audiobooks_recently_added")
    assert state is not None
    data = state.attributes["data"]
    assert data[0]["icon"] == "mdi:headphones"
    assert data[1]["title"] == "A Book"
    assert state.attributes["count"] == 1
    assert state.attributes["media_type"] == "audiobook"


async def test_ebook_sensor_uses_the_ebook_header(hass, init_integration) -> None:
    """An e-book library gets the book icon, not headphones."""
    state = hass.states.get("sensor.e_books_recently_added")
    assert state.attributes["data"][0]["icon"] == "mdi:book-open-page-variant"
    assert state.attributes["media_type"] == "ebook"


async def test_library_stat_sensors(hass, init_integration) -> None:
    """Items, duration in hours, size in gigabytes."""
    assert hass.states.get("sensor.audiobooks_items").state == "10"
    assert hass.states.get("sensor.audiobooks_duration").state == "10.0"
    assert hass.states.get("sensor.audiobooks_size").state == "2.0"


async def test_listening_now_ignores_stale_sessions(
    hass, mock_config_entry, mock_abs_client, mock_rest
) -> None:
    """The whole point of this sensor: open != listening."""
    stale = int((time.time() - 86400) * 1000)
    mock_rest.async_get_open_sessions.return_value = [
        open_session(user_id="u1", updated_at=stale)
    ]
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.audiobookshelf_plus_listening_now").state == "0"
    open_sessions = hass.states.get("sensor.audiobookshelf_plus_open_sessions")
    assert open_sessions.state == "1"
    assert open_sessions.attributes["users"] == [{"user": "Alice", "live": False}]


async def test_listening_now_lists_live_listeners(
    hass, mock_config_entry, mock_abs_client, mock_rest
) -> None:
    """A fresh session is a real listener."""
    mock_rest.async_get_open_sessions.return_value = [open_session(user_id="u1")]
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.audiobookshelf_plus_listening_now")
    assert state.state == "1"
    assert state.attributes["listeners"][0]["user"] == "Alice"
    assert state.attributes["listeners"][0]["title"] == "A Book"


async def test_realtime_binary_sensor(hass, init_integration) -> None:
    """Connectivity reflects the socket, not the server."""
    assert (
        hass.states.get("binary_sensor.audiobookshelf_plus_realtime_updates").state
        == STATE_ON
    )


async def test_realtime_binary_sensor_off_when_socket_down(
    hass, mock_config_entry, mock_abs_client, mock_rest
) -> None:
    """Polling fallback still works, and says so."""
    mock_rest.async_get_me.side_effect = AudiobookshelfRestError("no token")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert (
        hass.states.get("binary_sensor.audiobookshelf_plus_realtime_updates").state
        == STATE_OFF
    )


async def test_scan_button(hass, init_integration, mock_rest) -> None:
    """Pressing the button asks Audiobookshelf to rescan that library."""
    await hass.services.async_call(
        "button",
        "press",
        {ATTR_ENTITY_ID: "button.audiobooks_scan_library"},
        blocking=True,
    )
    mock_rest.async_scan_library.assert_awaited_once_with("lib-books")


async def test_scan_button_failure_raises(hass, init_integration, mock_rest) -> None:
    """A failed scan is an error the user sees, not a silent no-op."""
    mock_rest.async_scan_library.side_effect = AudiobookshelfRestError("busy")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "button",
            "press",
            {ATTR_ENTITY_ID: "button.audiobooks_scan_library"},
            blocking=True,
        )


# ------------------------------------------------------------- media player


async def test_media_player_idle_without_a_session(hass, init_integration) -> None:
    """No session means idle, and no metadata."""
    state = hass.states.get("media_player.audiobookshelf_plus_alice")
    assert state.state == MediaPlayerState.IDLE
    assert state.attributes.get("media_title") is None


async def test_media_player_playing(
    hass, mock_config_entry, mock_abs_client, mock_rest
) -> None:
    """A live session is playing, with position and art."""
    mock_rest.async_get_open_sessions.return_value = [open_session(user_id="u1")]
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("media_player.audiobookshelf_plus_alice")
    assert state.state == MediaPlayerState.PLAYING
    assert state.attributes["media_title"] == "A Book"
    assert state.attributes["media_artist"] == "An Author"
    assert state.attributes["media_duration"] == 3600
    assert state.attributes["media_position"] == 120
    assert state.attributes["media_content_type"] == MediaType.MUSIC
    assert state.attributes["device"] == "samsung SM-F946U1"
    # Read-only on purpose: Audiobookshelf has no remote-control API.
    assert state.attributes["supported_features"] == 0


async def test_media_player_paused_when_session_is_stale(
    hass, mock_config_entry, mock_abs_client, mock_rest
) -> None:
    """Audiobookshelf emits nothing on pause; staleness is the only signal."""
    stale = int((time.time() - 600) * 1000)
    mock_rest.async_get_open_sessions.return_value = [
        open_session(user_id="u1", updated_at=stale)
    ]
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("media_player.audiobookshelf_plus_alice").state == (
        MediaPlayerState.PAUSED
    )


async def test_media_player_shows_last_session_when_idle(
    hass, init_integration
) -> None:
    """An idle player still surfaces the user's last book.

    Downloaded and offline playback never opens a live session, so the last
    session is the only thing that makes it visible on a dashboard.
    """
    state = hass.states.get("media_player.audiobookshelf_plus_alice")
    assert state.state == MediaPlayerState.IDLE
    attrs = state.attributes
    assert attrs["last_title"] == "Last Book"
    assert attrs["last_author"] == "Last Author"
    assert attrs["last_position"] == 1800.0
    assert attrs["last_duration"] == 7200.0
    assert isinstance(attrs["last_updated"], str)
    cover = attrs["last_cover"]
    assert cover.startswith("/api/audiobookshelf_plus/cover/")
    assert "item-latest" in cover
    assert "authSig=" in cover
    # Idle: no live session, so its device attribute is absent.
    assert "device" not in attrs


async def test_last_updated_is_rendered_in_the_local_timezone(
    hass, init_integration
) -> None:
    """A human reads this attribute, so it carries the local offset.

    Audiobookshelf reports the instant in UTC. Rendering it raw showed UTC
    digits in the attributes panel, which in Hawaii is ten hours out and can
    name the wrong day entirely.
    """
    from datetime import datetime, timedelta

    await hass.config.async_set_time_zone("Pacific/Honolulu")
    await init_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()

    attrs = hass.states.get("media_player.audiobookshelf_plus_alice").attributes
    shown = datetime.fromisoformat(attrs["last_updated"])
    assert shown.utcoffset() == timedelta(hours=-10)
    # Same instant the server reported, only rendered in the house's zone, so
    # anything parsing the attribute is unaffected.
    assert (
        shown
        == init_integration.runtime_data.data.users["u1"].latest_session.updated_at
    )


async def test_media_player_without_a_last_session(
    hass, mock_config_entry, mock_abs_client, mock_rest
) -> None:
    """A user with no usable last session exposes no last-* attributes.

    A latest session that names no library item, or carries no timestamp, is
    nothing to show. The live session's own attributes must be untouched.
    """
    mock_rest.async_get_users.return_value = [
        {
            "id": "u1",
            "username": "Alice",
            "type": "admin",
            # Has an item but no timestamp: not enough to show.
            "latestSession": {"libraryItemId": "item-x"},
        },
    ]
    mock_rest.async_get_open_sessions.return_value = [open_session(user_id="u1")]
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    attrs = hass.states.get("media_player.audiobookshelf_plus_alice").attributes
    assert "last_title" not in attrs
    assert "last_cover" not in attrs
    assert "last_position" not in attrs
    # The live session is reported exactly as before.
    assert attrs["device"] == "samsung SM-F946U1"


async def test_dormant_users_are_disabled_by_default(hass, init_integration) -> None:
    """Alice listened recently; Bob never did."""
    registry = er.async_get(hass)
    alice = registry.async_get("media_player.audiobookshelf_plus_alice")
    bob = registry.async_get("media_player.audiobookshelf_plus_bob")
    assert alice.disabled_by is None
    assert bob.disabled_by is er.RegistryEntryDisabler.INTEGRATION


# --------------------------------------------------------- dynamic / stale


async def test_new_library_appears_without_a_restart(
    hass, init_integration, mock_rest
) -> None:
    """Gold `dynamic-devices`."""
    from .conftest import _library

    mock_rest.async_get_libraries_with_stats.return_value = [
        _library("lib-books", "Audiobooks"),
        _library("lib-ebooks", "E-Books"),
        _library("lib-pods", "Podcasts", "podcast"),
    ]
    await init_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.podcasts_items") is not None
    assert hass.states.get("button.podcasts_scan_library") is not None


async def test_removed_library_device_is_pruned(
    hass, init_integration, mock_rest
) -> None:
    """Gold `stale-devices`."""
    from .conftest import _library

    registry = dr.async_get(hass)
    before = dr.async_entries_for_config_entry(registry, init_integration.entry_id)
    assert any(d.name == "E-Books" for d in before)

    mock_rest.async_get_libraries_with_stats.return_value = [
        _library("lib-books", "Audiobooks")
    ]
    await init_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()

    after = dr.async_entries_for_config_entry(registry, init_integration.entry_id)
    assert not any(d.name == "E-Books" for d in after)


async def test_manual_device_removal_only_when_gone(hass, init_integration) -> None:
    """A device the server still knows about cannot be removed by hand."""
    from custom_components.audiobookshelf_plus import async_remove_config_entry_device

    registry = dr.async_get(hass)
    live = next(
        d
        for d in dr.async_entries_for_config_entry(registry, init_integration.entry_id)
        if d.name == "Audiobooks"
    )
    assert not await async_remove_config_entry_device(hass, init_integration, live)

    ghost = dr.DeviceEntry(identifiers={(DOMAIN, "nonexistent")})
    assert await async_remove_config_entry_device(hass, init_integration, ghost)


async def test_unload(hass, init_integration, mock_abs_client) -> None:
    """Unloading tears everything down cleanly and closes the socket."""
    from homeassistant.config_entries import ConfigEntryState
    from homeassistant.const import STATE_UNAVAILABLE

    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()

    assert init_integration.state is ConfigEntryState.NOT_LOADED
    # Registry entries survive an unload; their states go unavailable.
    assert (
        hass.states.get("sensor.audiobooks_recently_added").state == STATE_UNAVAILABLE
    )
    mock_abs_client.socket.logout.assert_awaited_once()


async def test_child_devices_hang_off_the_server_device(
    hass, mock_config_entry, mock_abs_client, mock_rest, caplog
) -> None:
    """Library and user devices must be linked to the server device.

    The server device used to come into being with the first server-level
    entity. Platforms load in parallel, and one holding only per-library
    entities (the scan buttons) could reference it before it existed; HA logs
    that and drops the link. Loading the button platform alone makes that
    ordering certain instead of a race.
    """
    with patch("custom_components.audiobookshelf_plus.PLATFORMS", [Platform.BUTTON]):
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    registry = dr.async_get(hass)
    entry_id = mock_config_entry.entry_id
    server = registry.async_get_device(identifiers={(DOMAIN, entry_id)})
    assert server is not None
    child = registry.async_get_device(identifiers={(DOMAIN, f"{entry_id}_lib-books")})
    assert child is not None
    assert child.via_device_id == server.id
    assert "non existing `via_device`" not in caplog.text
