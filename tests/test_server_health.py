"""Version checking, library scans, and library health."""

from __future__ import annotations

from datetime import timedelta

import pytest
from aiohttp import ClientError
from homeassistant.components.update import SERVICE_INSTALL, UpdateEntityFeature
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.audiobookshelf.const import (
    EVENT_SCAN_COMPLETED,
    REFRESH_COOLDOWN_SECONDS,
    RELEASE_CHECK_INTERVAL,
)
from custom_components.audiobookshelf.release import async_get_latest_release

from .conftest import LATEST_RELEASE, SERVER_VERSION

SCANNING = "binary_sensor.audiobooks_scanning"
UPDATE = "update.audiobookshelf_server"

TASK_SCAN_STARTED = {
    "id": "task-1",
    "action": "library-scan",
    "data": {"libraryId": "lib-books", "libraryName": "Audiobooks"},
}
TASK_SCAN_FINISHED = {
    "id": "task-1",
    "action": "library-scan",
    "isFailed": False,
    "data": {
        "libraryId": "lib-books",
        "libraryName": "Audiobooks",
        "scanResults": {
            "added": 3,
            "updated": 1,
            "missing": 1,
            "elapsed": 739,
            "text": "3 added, 1 updated, 1 missing (739 ms)",
        },
    },
}


def _entity(hass, entity_id: str):
    """The live entity object, for methods HA exposes over websocket only."""
    component = hass.data["entity_components"]["update"]
    return component.get_entity(entity_id)


def _handler(mock_abs_client, event: str):
    """The callback the coordinator registered for a raw socket event."""
    for call in mock_abs_client.socket.client.on.call_args_list:
        if call.args[0] == event:
            return call.args[1]
    raise AssertionError(f"no handler registered for {event}")


# ---------------------------------------------------------------- release fetch


async def test_release_parses_and_strips_the_v(hass, aioclient_mock) -> None:
    """Tags carry a leading `v`; the server's own version string does not."""
    aioclient_mock.get(
        "https://api.github.com/repos/advplyr/audiobookshelf/releases/latest",
        json={"tag_name": "v2.36.0", "html_url": "https://x/y", "body": "notes"},
    )
    release = await async_get_latest_release(async_get_clientsession(hass))
    assert release is not None
    assert release.version == "2.36.0"
    assert release.url == "https://x/y"
    assert release.notes == "notes"


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"status": 403}, "rate limited"),
        ({"status": 500}, "github is down"),
        ({"json": {}}, "no tag in the payload"),
        ({"json": {"tag_name": ""}}, "empty tag"),
        ({"exc": ClientError("boom")}, "network is down"),
        ({"text": "not json", "headers": {"Content-Type": "application/json"}}, "junk"),
    ],
)
async def test_release_never_raises(hass, aioclient_mock, kwargs, reason) -> None:
    """A version check is not worth failing a refresh over."""
    aioclient_mock.get(
        "https://api.github.com/repos/advplyr/audiobookshelf/releases/latest",
        **kwargs,
    )
    assert await async_get_latest_release(async_get_clientsession(hass)) is None, reason


# ---------------------------------------------------------------- update entity


async def test_update_entity_reports_both_versions(hass, init_integration) -> None:
    state = hass.states.get(UPDATE)
    assert state.attributes["installed_version"] == SERVER_VERSION
    assert state.attributes["latest_version"] == LATEST_RELEASE.version
    assert state.attributes["release_url"] == LATEST_RELEASE.url
    assert state.state == "on"  # an update is available


async def test_update_entity_is_off_when_current(
    hass, mock_config_entry, mock_abs_client, mock_rest, mock_release
) -> None:
    mock_rest.async_get_status.return_value = {"serverVersion": LATEST_RELEASE.version}
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(UPDATE).state == "off"


async def test_update_entity_claims_nothing_before_github_answers(
    hass, mock_config_entry, mock_abs_client, mock_rest, mock_release
) -> None:
    """An unreachable GitHub must not look like `up to date` or like an update."""
    mock_release.return_value = None
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(UPDATE)
    assert state.state == "off"
    assert state.attributes["latest_version"] == SERVER_VERSION
    assert state.attributes["release_url"] is None


async def test_release_notes_are_not_truncated(hass, init_integration) -> None:
    """`release_summary` caps at 255 characters, so the notes go through the API."""
    assert len(LATEST_RELEASE.notes) > 255
    state = hass.states.get(UPDATE)
    assert UpdateEntityFeature.RELEASE_NOTES in UpdateEntityFeature(
        state.attributes["supported_features"]
    )
    entity = _entity(hass, UPDATE)
    assert await entity.async_release_notes() == LATEST_RELEASE.notes


async def test_there_is_no_install_button(hass, init_integration) -> None:
    """Upgrading the container is the operator's decision."""
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "update",
            SERVICE_INSTALL,
            {ATTR_ENTITY_ID: UPDATE},
            blocking=True,
        )


async def test_github_is_asked_once_a_day(hass, init_integration, mock_release) -> None:
    """The one outbound call must not ride the 30-second listening poll."""
    assert mock_release.await_count == 1
    await init_integration.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert mock_release.await_count == 1
    assert RELEASE_CHECK_INTERVAL.total_seconds() == 86400


async def test_a_failed_check_keeps_the_last_answer(
    hass, init_integration, mock_release
) -> None:
    """One bad day at GitHub should not blank the entity."""
    coordinator = init_integration.runtime_data
    coordinator._release_checked = 0.0  # force a re-check
    mock_release.return_value = None

    await coordinator.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get(UPDATE)
    assert state.attributes["latest_version"] == LATEST_RELEASE.version


# ------------------------------------------------------------------- scan state


async def test_scanning_sensor_follows_the_task_events(
    hass, init_integration, mock_abs_client
) -> None:
    assert hass.states.get(SCANNING).state == "off"

    await _handler(mock_abs_client, "task_started")(TASK_SCAN_STARTED)
    await hass.async_block_till_done()
    assert hass.states.get(SCANNING).state == "on"
    # Only the library being scanned.
    assert hass.states.get("binary_sensor.e_books_scanning").state == "off"

    await _handler(mock_abs_client, "task_finished")(TASK_SCAN_FINISHED)
    await hass.async_block_till_done()
    assert hass.states.get(SCANNING).state == "off"


async def test_other_tasks_are_ignored(hass, init_integration, mock_abs_client) -> None:
    """Every background job rides the same two events, not just scans."""
    await _handler(mock_abs_client, "task_started")(
        {"id": "t", "action": "embed-metadata", "data": {"libraryId": "lib-books"}}
    )
    await hass.async_block_till_done()
    assert hass.states.get(SCANNING).state == "off"


@pytest.mark.parametrize(
    "payload",
    [
        "not a dict",
        {"action": "library-scan"},  # no data
        {"action": "library-scan", "data": {}},  # no libraryId
    ],
)
async def test_malformed_task_payloads_are_survivable(
    hass, init_integration, mock_abs_client, payload
) -> None:
    await _handler(mock_abs_client, "task_started")(payload)
    await _handler(mock_abs_client, "task_finished")(payload)
    await hass.async_block_till_done()
    assert hass.states.get(SCANNING).state == "off"


async def test_scan_completed_fires_an_event(
    hass, init_integration, mock_abs_client
) -> None:
    """What the scan found, for automations to act on."""
    events = []
    hass.bus.async_listen(EVENT_SCAN_COMPLETED, events.append)

    await _handler(mock_abs_client, "task_finished")(TASK_SCAN_FINISHED)
    await hass.async_block_till_done()

    assert len(events) == 1
    assert events[0].data == {
        "library": "Audiobooks",
        "library_id": "lib-books",
        "failed": False,
        "added": 3,
        "updated": 1,
        "missing": 1,
        "elapsed_ms": 739,
        "summary": "3 added, 1 updated, 1 missing (739 ms)",
    }


async def test_a_failed_scan_still_fires(
    hass, init_integration, mock_abs_client
) -> None:
    events = []
    hass.bus.async_listen(EVENT_SCAN_COMPLETED, events.append)

    await _handler(mock_abs_client, "task_finished")(
        {
            "id": "t",
            "action": "library-scan",
            "isFailed": True,
            "data": {"libraryId": "lib-books", "libraryName": "Audiobooks"},
        }
    )
    await hass.async_block_till_done()
    assert events[0].data["failed"] is True
    assert events[0].data["summary"] == ""


async def test_a_finished_scan_refreshes_the_counts(
    hass, init_integration, mock_abs_client, mock_rest, freezer
) -> None:
    """A scan changes item and issue counts, so pick them up rather than wait.

    The request goes through the coordinator's debouncer, which is the whole
    point: a scan that touches five hundred books emits one refresh, not five
    hundred.
    """
    before = mock_rest.async_get_issue_count.await_count
    await _handler(mock_abs_client, "task_finished")(TASK_SCAN_FINISHED)
    await hass.async_block_till_done()

    freezer.tick(timedelta(seconds=REFRESH_COOLDOWN_SECONDS + 1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert mock_rest.async_get_issue_count.await_count > before


# ------------------------------------------------------------- library health


async def test_issues_sensor(hass, init_integration) -> None:
    assert hass.states.get("sensor.audiobooks_issues").state == "0"


async def test_issues_sensor_counts_broken_items(
    hass, mock_config_entry, mock_abs_client, mock_rest
) -> None:
    mock_rest.async_get_issue_count.return_value = 2
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.e_books_issues").state == "2"


async def test_last_scan_sensor(hass, init_integration) -> None:
    state = hass.states.get("sensor.audiobooks_last_scan")
    assert state.state == "2023-11-14T22:13:20+00:00"


async def test_last_scan_is_unknown_when_never_scanned(
    hass, mock_config_entry, mock_abs_client, mock_rest
) -> None:
    libraries = mock_rest.async_get_libraries_with_stats.return_value
    for library in libraries:
        library.pop("lastScan", None)
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.audiobooks_last_scan").state == "unknown"
