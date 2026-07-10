"""Config, reauth, reconfigure and options flows. Bronze demands 100% here."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aioaudiobookshelf.exceptions import LoginError
from aiohttp import ClientError
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.data_entry_flow import FlowResultType

from custom_components.audiobookshelf_plus.api import AudiobookshelfRestError
from custom_components.audiobookshelf_plus.const import CONF_LINKED_USERS, DOMAIN

from .conftest import API_KEY, URL

STATUS_OK = {"app": "audiobookshelf", "serverVersion": "2.35.1"}


def _status(payload=STATUS_OK, status=200):
    """Patch the unauthenticated /status probe."""
    response = AsyncMock()
    response.status = status
    response.json = AsyncMock(return_value=payload)
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    session = AsyncMock()
    session.get = lambda *a, **kw: response
    return patch(
        "custom_components.audiobookshelf_plus.config_flow.async_get_clientsession",
        return_value=session,
    )


def _validated(side_effect=None):
    return patch(
        "custom_components.audiobookshelf_plus.config_flow.absapi.get_admin_client_by_token",
        AsyncMock(side_effect=side_effect),
    )


async def test_user_flow_creates_entry(hass, mock_abs_client, mock_rest) -> None:
    """The happy path."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    with _status(), _validated():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_URL: URL + "/", CONF_API_KEY: API_KEY}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Audiobookshelf"
    # Trailing slash stripped, host:port used as the unique id.
    assert result["data"] == {CONF_URL: URL, CONF_API_KEY: API_KEY}
    assert result["result"].unique_id == "abs.example.com:13378"


async def test_user_flow_duplicate_aborts(hass, mock_config_entry) -> None:
    """The same server cannot be added twice."""
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with _status(), _validated():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_URL: URL, CONF_API_KEY: API_KEY}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize(
    ("status_kwargs", "validate_error", "expected"),
    [
        ({"status": 500}, None, "cannot_connect"),
        ({"payload": {"app": "something-else"}}, None, "not_audiobookshelf"),
        ({}, LoginError, "invalid_auth"),
        ({}, ClientError, "cannot_connect"),
        ({}, RuntimeError, "unknown"),
    ],
)
async def test_user_flow_errors(hass, status_kwargs, validate_error, expected) -> None:
    """Every failure mode shows an error and lets the user retry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with _status(**status_kwargs), _validated(validate_error):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_URL: URL, CONF_API_KEY: API_KEY}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}


async def test_user_flow_status_unreachable(hass) -> None:
    """A server that refuses the connection outright."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    session = MagicMock()
    session.get = MagicMock(side_effect=ClientError)
    with patch(
        "custom_components.audiobookshelf_plus.config_flow.async_get_clientsession",
        return_value=session,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_URL: URL, CONF_API_KEY: API_KEY}
        )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_reauth_flow(hass, init_integration) -> None:
    """A revoked key prompts for a new one."""
    result = await init_integration.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    new_key = "header.bmV3.sig"
    with _status(), _validated():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: new_key}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert init_integration.data[CONF_API_KEY] == new_key


@pytest.mark.parametrize(
    ("status_kwargs", "validate_error", "expected"),
    [
        ({}, LoginError, "invalid_auth"),
        ({"status": 500}, None, "cannot_connect"),
        ({}, RuntimeError, "unknown"),
    ],
)
async def test_reauth_flow_errors(
    hass, init_integration, status_kwargs, validate_error, expected
) -> None:
    """Reauth surfaces the same errors as setup."""
    result = await init_integration.start_reauth_flow(hass)
    with _status(**status_kwargs), _validated(validate_error):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: "bad"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}


async def test_reconfigure_flow(hass, init_integration) -> None:
    """URL and key can be swapped in place."""
    result = await init_integration.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"

    with _status(), _validated():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_URL: URL, CONF_API_KEY: "header.bmV3.sig"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert init_integration.data[CONF_API_KEY] == "header.bmV3.sig"


async def test_reconfigure_rejects_a_different_server(hass, init_integration) -> None:
    """Pointing an entry at another server is a mistake, not a migration."""
    result = await init_integration.start_reconfigure_flow(hass)
    with _status(), _validated():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_URL: "http://other.example.com:13378", CONF_API_KEY: API_KEY},
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_server"


@pytest.mark.parametrize(
    ("status_kwargs", "validate_error", "expected"),
    [
        ({}, LoginError, "invalid_auth"),
        ({"status": 500}, None, "cannot_connect"),
        ({"payload": {"app": "nope"}}, None, "not_audiobookshelf"),
        ({}, RuntimeError, "unknown"),
    ],
)
async def test_reconfigure_errors(
    hass, init_integration, status_kwargs, validate_error, expected
) -> None:
    """Reconfigure surfaces every error too."""
    result = await init_integration.start_reconfigure_flow(hass)
    with _status(**status_kwargs), _validated(validate_error):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_URL: URL, CONF_API_KEY: "bad"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}


# ------------------------------------------------------------- options flow


async def test_options_flow_links_and_maps_users(hass, init_integration, mock_rest):
    """Linking a user mints an API key for them; mapping records the HA account."""
    ha_user = await hass.auth.async_create_user("Brian")
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_LINKED_USERS: ["u1"]}
    )
    assert result["step_id"] == "map"
    mock_rest.async_create_api_key.assert_awaited_once()
    assert mock_rest.async_create_api_key.await_args.args[0] == "u1"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"u1": ha_user.id}
    )
    await hass.async_block_till_done()

    linked = result["data"][CONF_LINKED_USERS]
    assert linked["u1"]["username"] == "Alice"
    assert linked["u1"]["ha_user_id"] == ha_user.id
    assert linked["u1"]["key_id"] == "key-1"


async def test_options_flow_unmapped_user(hass, init_integration) -> None:
    """Leaving the mapping blank stores None, not an empty string."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_LINKED_USERS: ["u1"]}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"u1": ""}
    )
    await hass.async_block_till_done()
    assert result["data"][CONF_LINKED_USERS]["u1"]["ha_user_id"] is None


async def test_options_flow_unlink_revokes_key(hass, init_integration, mock_rest):
    """Unlinking everyone revokes the keys we minted and skips the mapping step."""
    hass.config_entries.async_update_entry(
        init_integration,
        options={
            CONF_LINKED_USERS: {
                "u1": {
                    "username": "Alice",
                    "api_key": "k",
                    "key_id": "key-1",
                    "ha_user_id": None,
                }
            }
        },
    )
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_LINKED_USERS: []}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_LINKED_USERS] == {}
    mock_rest.async_delete_api_key.assert_awaited_once_with("key-1")


async def test_options_flow_revoke_failure_is_not_fatal(
    hass, init_integration, mock_rest
):
    """A key we cannot revoke is logged, not raised -- the user still gets unlinked."""
    hass.config_entries.async_update_entry(
        init_integration,
        options={
            CONF_LINKED_USERS: {
                "u1": {
                    "username": "Alice",
                    "api_key": "k",
                    "key_id": "key-1",
                    "ha_user_id": None,
                }
            }
        },
    )
    mock_rest.async_delete_api_key.side_effect = AudiobookshelfRestError("gone")

    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_LINKED_USERS: []}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_options_flow_mint_failure_shows_error(hass, init_integration, mock_rest):
    """A non-admin key cannot mint, and the user is told so."""
    mock_rest.async_create_api_key.side_effect = AudiobookshelfRestError("forbidden")
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_LINKED_USERS: ["u1"]}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "key_failed"}


async def test_options_flow_mint_returns_no_key(hass, init_integration, mock_rest):
    """A server that returns 200 but no key is still a failure."""
    mock_rest.async_create_api_key.return_value = {"id": "x", "api_key": None}
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_LINKED_USERS: ["u1"]}
    )
    assert result["errors"] == {"base": "key_failed"}


async def test_options_flow_keeps_existing_link(hass, init_integration, mock_rest):
    """Re-selecting an already-linked user does not mint a second key."""
    ha_user = await hass.auth.async_create_user("Brian")
    existing = {
        "u1": {
            "username": "Alice",
            "api_key": "k",
            "key_id": "key-1",
            "ha_user_id": ha_user.id,
        }
    }
    hass.config_entries.async_update_entry(
        init_integration, options={CONF_LINKED_USERS: existing}
    )
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_LINKED_USERS: ["u1"]}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"u1": ha_user.id}
    )
    await hass.async_block_till_done()
    mock_rest.async_create_api_key.assert_not_awaited()
    assert result["data"][CONF_LINKED_USERS]["u1"]["api_key"] == "k"
