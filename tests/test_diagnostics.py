"""Diagnostics must be useful and must not leak credentials."""

from __future__ import annotations

import json

from custom_components.audiobookshelf_plus.const import CONF_LINKED_USERS
from custom_components.audiobookshelf_plus.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import make_api_key, open_session


async def test_diagnostics_redacts_every_credential(hass, init_integration) -> None:
    """The admin key, minted per-user keys, and user ids all stay out."""
    secret = make_api_key()
    hass.config_entries.async_update_entry(
        init_integration,
        options={
            CONF_LINKED_USERS: {
                "u1": {
                    "username": "Alice",
                    "api_key": secret,
                    "key_id": "key-1",
                    "ha_user_id": "ha-1",
                }
            }
        },
    )
    await hass.async_block_till_done()

    result = await async_get_config_entry_diagnostics(hass, init_integration)
    blob = json.dumps(result, default=str)

    assert result["entry"]["data"]["api_key"] == "**REDACTED**"
    assert secret not in blob
    assert "ha-1" not in blob
    assert "key-1" not in blob
    # But the useful part survives.
    assert result["entry"]["options"][CONF_LINKED_USERS] == [
        {"username": "Alice", "mapped_to_ha_user": True}
    ]


async def test_diagnostics_content(hass, init_integration) -> None:
    """Enough to debug with: connection, libraries, users, who is listening."""
    result = await async_get_config_entry_diagnostics(hass, init_integration)

    assert result["connection"]["realtime_connected"] is True
    assert result["connection"]["last_update_success"] is True
    names = {library["name"] for library in result["libraries"]}
    assert names == {"Audiobooks", "E-Books"}
    ebooks = next(x for x in result["libraries"] if x["name"] == "E-Books")
    assert ebooks["detected_as_ebook"] is True
    assert {user["username"] for user in result["users"]} == {"Alice", "Bob"}
    assert result["listening_now"] == []


async def test_diagnostics_includes_a_live_session(
    hass, mock_config_entry, mock_abs_client, mock_rest
) -> None:
    """A session in progress is reported, without its identifiers."""
    mock_rest.async_get_open_sessions.return_value = [open_session(user_id="u1")]
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    alice = next(u for u in result["users"] if u["username"] == "Alice")
    assert alice["has_open_session"] is True
    assert alice["session_is_live"] is True
    assert alice["session"]["title"] == "A Book"
    assert result["listening_now"] == ["Alice"]
