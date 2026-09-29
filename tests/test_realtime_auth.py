"""How the realtime socket signs in, and what it does when the server says no."""

from __future__ import annotations

import logging
from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.audiobookshelf_plus.api import AudiobookshelfRestError
from custom_components.audiobookshelf_plus.const import DOMAIN, REFRESH_COOLDOWN_SECONDS
from custom_components.audiobookshelf_plus.coordinator import _at_least, _same_clock

REALTIME = "binary_sensor.audiobookshelf_plus_realtime_updates"
SOCKET_CLIENT = "custom_components.audiobookshelf_plus.coordinator.SocketClient"


def _handler(mock_abs_client, event: str):
    """The callback the coordinator registered for a raw socket event."""
    for call in mock_abs_client.socket.client.on.call_args_list:
        if call.args[0] == event:
            return call.args[1]
    raise AssertionError(f"no handler registered for {event}")


async def _setup(hass, entry, mock_abs_client):
    """Set the entry up, returning the SocketClient class mock to inspect."""
    with patch(SOCKET_CLIENT, return_value=mock_abs_client.socket) as socket_cls:
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return socket_cls


# ------------------------------------------------------------ which token


async def test_api_key_signs_the_socket_in_on_2_37(
    hass, mock_config_entry, mock_abs_client, mock_rest
) -> None:
    """2.37.0 takes the API key on the socket, so the legacy token is not needed."""
    mock_rest.async_get_status.return_value = {"serverVersion": "2.37.0"}
    socket_cls = await _setup(hass, mock_config_entry, mock_abs_client)

    config = socket_cls.call_args.kwargs["session_config"]
    assert config.token == mock_config_entry.data["api_key"]
    mock_rest.async_get_me.assert_not_awaited()


async def test_older_servers_get_the_legacy_token(
    hass, mock_config_entry, mock_abs_client, mock_rest
) -> None:
    """Before 2.37.0 an API key is refused on the socket; /api/me's token is not."""
    mock_rest.async_get_status.return_value = {"serverVersion": "2.36.1"}
    socket_cls = await _setup(hass, mock_config_entry, mock_abs_client)

    assert socket_cls.call_args.kwargs["session_config"].token == "socket-token"


async def test_unknown_version_falls_back_to_the_legacy_token(
    hass, mock_config_entry, mock_abs_client, mock_rest
) -> None:
    """Not knowing the version must not gamble on a handshake old servers refuse."""
    mock_rest.async_get_status.side_effect = [
        AudiobookshelfRestError("status down"),
        {"serverVersion": "2.37.0"},
    ]
    socket_cls = await _setup(hass, mock_config_entry, mock_abs_client)

    assert socket_cls.call_args.kwargs["session_config"].token == "socket-token"


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("2.37.0", True),
        ("2.37.1", True),
        ("3.0.0", True),
        ("2.36.9", False),
        ("", False),
        (None, False),
        ("not-a-version", False),
    ],
)
def test_version_gate(version, expected) -> None:
    assert _at_least(version, "2.37.0") is expected


# -------------------------------------------------- connection state events


async def test_realtime_waits_for_the_servers_init(
    hass, mock_config_entry, mock_abs_client, mock_rest
) -> None:
    """A connected transport is not enough: nothing flows until the server inits."""
    mock_abs_client.socket.init_client.side_effect = None  # server never answers
    await _setup(hass, mock_config_entry, mock_abs_client)
    assert hass.states.get(REALTIME).state == "off"

    await _handler(mock_abs_client, "init")({})
    await hass.async_block_till_done()
    assert hass.states.get(REALTIME).state == "on"


async def test_auth_failed_turns_realtime_off_at_once_and_polls(
    hass, init_integration, mock_abs_client, mock_rest, caplog
) -> None:
    """A refused socket must not keep claiming realtime, and must say why."""
    assert hass.states.get(REALTIME).state == "on"
    polls = mock_rest.async_get_users.await_count

    with caplog.at_level(logging.WARNING):
        await _handler(mock_abs_client, "auth_failed")({"message": "API key expired"})
        await hass.async_block_till_done()
    assert hass.states.get(REALTIME).state == "off"
    assert "API key expired" in caplog.text

    # The follow-up poll is what turns a dead key into a reauth prompt, and it
    # comes after the refresh debounce, not five minutes later.
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=REFRESH_COOLDOWN_SECONDS + 1)
    )
    await hass.async_block_till_done()
    assert mock_rest.async_get_users.await_count > polls


async def test_auth_failed_without_a_reason(
    hass, init_integration, mock_abs_client, caplog
) -> None:
    with caplog.at_level(logging.WARNING):
        await _handler(mock_abs_client, "auth_failed")(None)
        await hass.async_block_till_done()
    assert "no reason given" in caplog.text
    assert hass.states.get(REALTIME).state == "off"


async def test_disconnect_and_reconnect_show_immediately(
    hass, init_integration, mock_abs_client
) -> None:
    """Realtime state is pushed the moment it changes, not at the next poll."""
    await _handler(mock_abs_client, "disconnect")()
    await hass.async_block_till_done()
    assert hass.states.get(REALTIME).state == "off"

    await _handler(mock_abs_client, "init")({})
    await hass.async_block_till_done()
    assert hass.states.get(REALTIME).state == "on"


# ------------------------------------------------------------- time zones


def _issue(hass, entry):
    return ir.async_get(hass).async_get_issue(
        DOMAIN, f"timezone_mismatch_{entry.entry_id}"
    )


async def test_different_server_clock_raises_an_issue(
    hass, mock_config_entry, mock_abs_client, mock_rest
) -> None:
    """Stats are bucketed by the server's day, so a UTC server skews "today"."""
    await hass.config.async_set_time_zone("Pacific/Honolulu")
    mock_rest.async_authorize.return_value = {"serverSettings": {"timeZone": "UTC"}}
    await _setup(hass, mock_config_entry, mock_abs_client)

    issue = _issue(hass, mock_config_entry)
    assert issue is not None
    assert issue.translation_placeholders == {
        "url": mock_config_entry.data["url"],
        "abs_zone": "UTC",
        "ha_zone": "Pacific/Honolulu",
    }


async def test_same_clock_under_another_name_is_fine(
    hass, mock_config_entry, mock_abs_client, mock_rest
) -> None:
    await hass.config.async_set_time_zone("Pacific/Honolulu")
    mock_rest.async_authorize.return_value = {
        "serverSettings": {"timeZone": "US/Hawaii"}
    }
    await _setup(hass, mock_config_entry, mock_abs_client)
    assert _issue(hass, mock_config_entry) is None


async def test_unreadable_server_zone_raises_nothing(
    hass, mock_config_entry, mock_abs_client, mock_rest
) -> None:
    mock_rest.async_authorize.side_effect = AudiobookshelfRestError("nope")
    await _setup(hass, mock_config_entry, mock_abs_client)
    assert _issue(hass, mock_config_entry) is None


def test_same_clock() -> None:
    assert _same_clock("UTC", "UTC")
    assert _same_clock("US/Hawaii", "Pacific/Honolulu")
    assert not _same_clock("UTC", "Pacific/Honolulu")
    # Same offset in northern winter, an hour apart in summer.
    assert not _same_clock("Europe/London", "Africa/Abidjan")
    # An unknown name is not evidence of a problem.
    assert _same_clock("Not/AZone", "UTC")
    assert _same_clock("../etc", "UTC")
