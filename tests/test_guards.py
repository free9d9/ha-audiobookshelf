"""The defensive branches.

Every one of these is a "this cannot happen" path: a speaker that vanishes
mid-session, a user record with no id, a key whose `exp` is nonsense. They can
all happen. Home Assistant restarts, entities get removed, and Audiobookshelf
has shipped every one of these payload shapes at some point.

Each test provokes one branch that no other test reaches.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
from unittest.mock import AsyncMock, MagicMock

from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.audiobookshelf.const import CONF_LINKED_USERS, DOMAIN
from custom_components.audiobookshelf.coordinator import _api_key_expiry, _is_new
from custom_components.audiobookshelf.playback import (
    ActiveSession,
    _async_end_session,
    _make_ticker,
    _make_watcher,
    _sessions,
)

from .conftest import URL

# --------------------------------------------------------------- coordinator


def test_api_key_with_a_nonsense_expiry() -> None:
    """We read the `exp` claim; we do not trust it. A bad one means no expiry."""
    claims = json.dumps({"keyId": "abc", "exp": "next tuesday"}).encode()
    body = base64.urlsafe_b64encode(claims).rstrip(b"=").decode()
    assert _api_key_expiry(f"header.{body}.signature") is None


def test_api_key_with_an_out_of_range_expiry() -> None:
    """An `exp` too large for a `time_t` raises OverflowError.

    Which is neither a ValueError nor an OSError, and so escaped the original
    except clause and took the whole config entry down with it.
    """
    claims = json.dumps({"exp": 10**20}).encode()
    body = base64.urlsafe_b64encode(claims).rstrip(b"=").decode()
    assert _api_key_expiry(f"header.{body}.signature") is None


def test_an_item_with_no_added_date_is_not_new() -> None:
    assert _is_new(None) is False
    assert _is_new(dt.datetime.now(dt.UTC)) is True


async def test_a_user_record_with_no_id_is_skipped(
    hass, mock_config_entry, mock_abs_client, mock_rest
) -> None:
    """No id means no stats call, and no entity, but the refresh still succeeds."""
    mock_rest.async_get_users.return_value = [
        {"username": "Ghost", "type": "user"},
        {"id": "u1", "username": "Alice", "type": "admin", "latestSession": None},
    ]
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_rest.async_get_user_stats.await_count == 1
    # An account we cannot key on is an account we cannot track.
    assert hass.states.get("sensor.audiobookshelf_users").state == "1"


# ----------------------------------------------------------------- migration


async def test_migration_leaves_unrelated_entities_alone(
    hass, mock_abs_client, mock_rest
) -> None:
    """The rename must match on unique id, not sweep the whole entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="abs.example.com:13378",
        data={CONF_URL: URL, CONF_API_KEY: "k"},
        options={CONF_LINKED_USERS: {}},
        version=1,
        minor_version=1,
    )
    entry.add_to_hass(hass)

    registry = er.async_get(hass)
    bystander = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.entry_id}_libraries",
        suggested_object_id="audiobookshelf_libraries",
        config_entry=entry,
    )

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    survivor = registry.async_get(bystander.entity_id)
    assert survivor.unique_id == f"{entry.entry_id}_libraries"


# -------------------------------------------------------------------- update


async def test_release_notes_before_github_has_answered(
    hass, mock_config_entry, mock_abs_client, mock_rest, mock_release
) -> None:
    """Asking for notes we do not have yet returns nothing, rather than raising."""
    mock_release.return_value = None
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    component = hass.data["entity_components"]["update"]
    entity = component.get_entity("update.audiobookshelf_server")
    assert await entity.async_release_notes() is None


# ------------------------------------------------------------------ playback

SPEAKER = "media_player.kitchen"


def _active(**kwargs) -> ActiveSession:
    rest = MagicMock()
    rest.async_sync_session = AsyncMock()
    rest.async_close_session = AsyncMock()
    rest.async_close_session_without_sync = AsyncMock()
    defaults = {
        "session_id": "sess-1",
        "rest": rest,
        "entity_id": SPEAKER,
        "username": "Alice",
        "duration": 3600.0,
        "track_offset": 0.0,
        "content_id": "item-1",
        "last_synced": 0.0,
        "last_position": 10.0,
        "sync_enabled": True,
        "unsub_timer": None,
        "unsub_state": None,
    }
    return ActiveSession(**{**defaults, **kwargs})


async def test_ticker_stops_when_the_session_is_gone(hass) -> None:
    """A timer can fire once more after the session was popped."""
    tick = _make_ticker(hass, SPEAKER)
    await tick(dt.datetime.now(dt.UTC))  # no session registered; must not raise


async def test_ticker_stops_when_the_entity_is_gone(hass) -> None:
    """Someone removed the speaker from Home Assistant mid-book."""
    active = _active()
    _sessions(hass)[SPEAKER] = active
    tick = _make_ticker(hass, SPEAKER)

    await tick(dt.datetime.now(dt.UTC))
    active.rest.async_sync_session.assert_not_awaited()


async def test_ticker_does_not_sync_a_paused_player(hass) -> None:
    """Audiobookshelf should not be told a paused position over and over."""
    hass.states.async_set(SPEAKER, "paused", {"media_position": 30.0})
    _sessions(hass)[SPEAKER] = active = _active()
    tick = _make_ticker(hass, SPEAKER)

    await tick(dt.datetime.now(dt.UTC))
    active.rest.async_sync_session.assert_not_awaited()


async def test_ticker_never_syncs_when_seeking_failed(hass) -> None:
    """The regression guard, from the bug that rewound a book to zero.

    If we could not seek to the saved position, playback started at the top of
    the track. Syncing that back would overwrite the listener's real progress
    with a few seconds. A playing speaker with a valid position is exactly the
    case that would otherwise sync, so it is the only one worth pinning.
    """
    hass.states.async_set(SPEAKER, "playing", {"media_position": 4.0})
    _sessions(hass)[SPEAKER] = active = _active(sync_enabled=False)
    tick = _make_ticker(hass, SPEAKER)

    await tick(dt.datetime.now(dt.UTC))

    active.rest.async_sync_session.assert_not_awaited()
    # It still tracks where the speaker is; it just refuses to write it.
    assert active.last_position == 4.0


async def test_watcher_ignores_events_for_a_closed_session(hass) -> None:
    """The state listener is unsubscribed asynchronously, so it can fire late."""
    watcher = _make_watcher(hass, SPEAKER)
    event = MagicMock()
    event.data = {"new_state": None, "old_state": None}
    watcher(event)  # no session registered; must not schedule a close
    await hass.async_block_till_done()


async def test_end_session_without_closing_just_unsubscribes(hass) -> None:
    """Unloading the integration tears down timers without touching the server."""
    unsub_timer, unsub_state = MagicMock(), MagicMock()
    active = _active(unsub_timer=unsub_timer, unsub_state=unsub_state)
    _sessions(hass)[SPEAKER] = active

    await _async_end_session(hass, SPEAKER, closing=False)

    unsub_timer.assert_called_once()
    unsub_state.assert_called_once()
    active.rest.async_close_session.assert_not_awaited()
    active.rest.async_close_session_without_sync.assert_not_awaited()
    assert SPEAKER not in _sessions(hass)


async def test_end_session_for_a_speaker_with_no_session(hass) -> None:
    await _async_end_session(hass, SPEAKER, closing=True)  # must not raise
