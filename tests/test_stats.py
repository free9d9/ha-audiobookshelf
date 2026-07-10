"""Server counts, and per-user listening statistics."""

from __future__ import annotations

import datetime as dt

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.audiobookshelf_plus import async_migrate_entry
from custom_components.audiobookshelf_plus.api import AudiobookshelfRestError
from custom_components.audiobookshelf_plus.const import CONF_LINKED_USERS, DOMAIN
from custom_components.audiobookshelf_plus.coordinator import _parse_stats

from .conftest import API_KEY, URL, stats_payload

# ------------------------------------------------------------- server counts


async def test_server_count_sensors(hass, init_integration) -> None:
    """Parity with the sensors wolffshots' integration exposes."""
    assert hass.states.get("sensor.audiobookshelf_plus_users").state == "2"
    assert hass.states.get("sensor.audiobookshelf_plus_libraries").state == "2"
    assert hass.states.get("sensor.audiobookshelf_plus_users_online").state == "1"


async def test_users_online_is_not_listening(hass, init_integration) -> None:
    """Online means an app is open. It is a different question from listening."""
    online = hass.states.get("sensor.audiobookshelf_plus_users_online")
    assert online.attributes["users"] == ["Alice"]
    # Nobody has a session in the default fixture.
    assert hass.states.get("sensor.audiobookshelf_plus_listening_now").state == "0"


async def test_libraries_sensor_lists_names(hass, init_integration) -> None:
    state = hass.states.get("sensor.audiobookshelf_plus_libraries")
    assert sorted(state.attributes["libraries"]) == ["Audiobooks", "E-Books"]


async def test_open_sessions_keeps_its_own_key(hass, init_integration) -> None:
    """`open_sessions` and `users_online` are different sensors, not one renamed."""
    registry = er.async_get(hass)
    open_sessions = registry.async_get("sensor.audiobookshelf_plus_open_sessions")
    users_online = registry.async_get("sensor.audiobookshelf_plus_users_online")
    assert open_sessions.translation_key == "open_sessions"
    assert users_online.translation_key == "users_online"
    assert open_sessions.unique_id != users_online.unique_id


# ------------------------------------------------------------------ bucketing


def test_parse_stats_buckets_by_calendar() -> None:
    """Today, this week, this month, this year, computed from the day map."""
    today = dt.date(2026, 7, 9)  # a Thursday
    raw = {
        "totalTime": 100 * 3600,
        "days": {
            "2026-07-09": 3600,  # today
            "2026-07-06": 1800,  # Monday of this week
            "2026-07-05": 900,  # Sunday, previous week
            "2026-07-01": 600,  # earlier this month
            "2026-01-15": 300,  # earlier this year
            "2025-12-31": 60,  # last year
        },
    }
    stats = _parse_stats(raw, today)
    assert stats.today == 3600
    assert stats.week == 3600 + 1800  # Monday-based, so Sunday is excluded
    assert stats.month == 3600 + 1800 + 900 + 600
    assert stats.year == 3600 + 1800 + 900 + 600 + 300
    # all_time is the server's own total, not the sum of `days`
    assert stats.all_time == 100 * 3600


def test_parse_stats_survives_junk() -> None:
    """A malformed day key must not take out the whole refresh."""
    stats = _parse_stats(
        {"days": {"not-a-date": 10, "2026-07-09": "also-bad"}}, dt.date(2026, 7, 9)
    )
    assert stats.today == 0
    assert stats.all_time == 0


def test_parse_stats_empty() -> None:
    stats = _parse_stats({}, dt.date(2026, 7, 9))
    assert (stats.today, stats.week, stats.month, stats.year, stats.all_time) == (
        0,
        0,
        0,
        0,
        0,
    )


def test_parse_stats_on_a_monday() -> None:
    """On Monday the week bucket is just today."""
    monday = dt.date(2026, 7, 6)
    stats = _parse_stats({"days": {"2026-07-06": 100, "2026-07-05": 500}}, monday)
    assert stats.week == 100


# ------------------------------------------------------------------- sensors


async def test_listening_time_sensors(hass, init_integration) -> None:
    """Five buckets per user, in hours."""
    payload = stats_payload()
    today_h = payload["today"] / 3600

    state = hass.states.get("sensor.audiobookshelf_plus_alice_listening_today")
    assert float(state.state) == pytest.approx(today_h, abs=0.01)
    assert state.attributes["unit_of_measurement"] == "h"
    assert state.attributes["device_class"] == "duration"

    all_time = hass.states.get("sensor.audiobookshelf_plus_alice_listening_all_time")
    assert float(all_time.state) == pytest.approx(100.0, abs=0.01)
    assert all_time.attributes["hours_by_weekday"] == {"Monday": 1.0, "Tuesday": 0.5}


async def test_only_all_time_carries_the_weekday_breakdown(hass, init_integration):
    today = hass.states.get("sensor.audiobookshelf_plus_alice_listening_today")
    assert "hours_by_weekday" not in today.attributes


async def test_stats_for_dormant_users_are_disabled(hass, init_integration) -> None:
    """Five sensors each for a dozen dormant accounts is noise."""
    registry = er.async_get(hass)
    alice = registry.async_get("sensor.audiobookshelf_plus_alice_listening_today")
    bob = registry.async_get("sensor.audiobookshelf_plus_bob_listening_today")
    assert alice.disabled_by is None
    assert bob.disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_stats_are_fetched_once_per_interval(
    hass, init_integration, mock_rest
) -> None:
    """One REST call per user is too expensive to ride the 30s listening poll."""
    calls_after_setup = mock_rest.async_get_user_stats.await_count
    assert calls_after_setup == 2  # one per user

    await init_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert mock_rest.async_get_user_stats.await_count == calls_after_setup


async def test_stats_failure_for_one_user_is_survivable(
    hass, mock_config_entry, mock_abs_client, mock_rest
) -> None:
    """Their sensors go unknown; everyone else is unaffected."""
    mock_rest.async_get_user_stats.side_effect = AudiobookshelfRestError("nope")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.audiobookshelf_plus_users").state == "2"
    state = hass.states.get("sensor.audiobookshelf_plus_alice_listening_today")
    assert state.state == "unknown"


# ----------------------------------------------------------------- migration


def _legacy_entry() -> MockConfigEntry:
    """A config entry as written by version 1.1."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Audiobookshelf",
        unique_id="abs.example.com:13378",
        data={CONF_URL: URL, CONF_API_KEY: API_KEY},
        options={CONF_LINKED_USERS: {}},
        version=1,
        minor_version=1,
    )


async def test_open_sessions_unique_id_is_migrated(
    hass, mock_abs_client, mock_rest
) -> None:
    """1.1 -> 1.2.

    The open-sessions sensor was keyed `users_online`, which now belongs to a
    genuinely new sensor. The existing entity must keep its entity id and its
    history, and the new sensor must take the freed key.
    """
    entry = _legacy_entry()
    entry.add_to_hass(hass)

    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.entry_id}_users_online",
        suggested_object_id="audiobookshelf_plus_open_sessions",
        config_entry=entry,
    )
    assert old.entity_id == "sensor.audiobookshelf_plus_open_sessions"

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.minor_version == 2
    migrated = registry.async_get("sensor.audiobookshelf_plus_open_sessions")
    assert migrated.unique_id == f"{entry.entry_id}_open_sessions"

    # The freed key now belongs to the real users-online sensor, on its own id.
    online = registry.async_get("sensor.audiobookshelf_plus_users_online")
    assert online.unique_id == f"{entry.entry_id}_users_online"


async def test_migration_is_not_run_twice(hass, mock_abs_client, mock_rest) -> None:
    """An already-migrated entry is left alone."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="abs.example.com:13378",
        data={CONF_URL: URL, CONF_API_KEY: API_KEY},
        options={CONF_LINKED_USERS: {}},
        version=1,
        minor_version=2,
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    online = registry.async_get("sensor.audiobookshelf_plus_users_online")
    assert online.unique_id == f"{entry.entry_id}_users_online"


async def test_migration_refuses_a_downgrade(hass) -> None:
    """A future major version must not be silently mangled."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_URL: URL, CONF_API_KEY: API_KEY},
        version=2,
        minor_version=1,
    )
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry) is False
