"""Config flow for Audiobookshelf."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

import aioaudiobookshelf_plus as absapi
import aiohttp
import voluptuous as vol
from aioaudiobookshelf_plus.client.session_configuration import SessionConfiguration
from aioaudiobookshelf_plus.exceptions import LoginError, TokenIsMissingError
from aiohttp import ClientError
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AudiobookshelfRest, AudiobookshelfRestError
from .const import CONF_LINKED_USERS, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): str,
        vol.Required(CONF_API_KEY): str,
    }
)


async def _async_validate(hass: HomeAssistant, url: str, api_key: str) -> str:
    """Check we can reach Audiobookshelf and that the key works.

    Returns the server version. Raises CannotConnect / InvalidAuth / NotAbs.
    """
    session = async_get_clientsession(hass)

    # /status is unauthenticated and outside /api -- the cheapest way to confirm
    # there is actually an Audiobookshelf on the other end before we send a key.
    try:
        async with session.get(
            f"{url}/status", timeout=aiohttp.ClientTimeout(total=10)
        ) as res:
            if res.status != 200:
                raise CannotConnect
            status = await res.json()
    except (ClientError, TimeoutError, ValueError) as err:
        raise CannotConnect from err

    if status.get("app") != "audiobookshelf":
        raise NotAudiobookshelf

    config = SessionConfiguration(
        session=session, url=url, token=api_key, logger=_LOGGER
    )
    try:
        await absapi.get_admin_client_by_token(session_config=config)
    except (LoginError, TokenIsMissingError) as err:
        raise InvalidAuth from err
    except (ClientError, TimeoutError) as err:
        raise CannotConnect from err

    return str(status.get("serverVersion", ""))


class AudiobookshelfConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Audiobookshelf config flow."""

    VERSION = 1
    MINOR_VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> AudiobookshelfOptionsFlow:
        """Get the options flow."""
        return AudiobookshelfOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the server URL and an API key."""
        errors: dict[str, str] = {}

        if user_input is not None:
            url = user_input[CONF_URL].rstrip("/")
            try:
                await _async_validate(self.hass, url, user_input[CONF_API_KEY])
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except NotAudiobookshelf:
                errors["base"] = "not_audiobookshelf"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error validating Audiobookshelf")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(_server_id(url))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=_entry_title(url),
                    data={CONF_URL: url, CONF_API_KEY: user_input[CONF_API_KEY]},
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the server URL or API key without removing the integration."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            url = user_input[CONF_URL].rstrip("/")
            try:
                await _async_validate(self.hass, url, user_input[CONF_API_KEY])
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except NotAudiobookshelf:
                errors["base"] = "not_audiobookshelf"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error validating Audiobookshelf")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(_server_id(url))
                self._abort_if_unique_id_mismatch(reason="wrong_server")
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_URL: url,
                        CONF_API_KEY: user_input[CONF_API_KEY],
                    },
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_URL, default=entry.data[CONF_URL]): str,
                    vol.Required(CONF_API_KEY): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle an expired or revoked API key."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a fresh API key."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            url = entry.data[CONF_URL]
            try:
                await _async_validate(self.hass, url, user_input[CONF_API_KEY])
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error validating Audiobookshelf")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_API_KEY: user_input[CONF_API_KEY]}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): str}),
            errors=errors,
        )


def _entry_title(url: str) -> str:
    """Name the entry so two servers can be told apart.

    "Audiobookshelf Plus" alone reads fine until a second server is added, and
    then the integrations page, the device list and the `config_entry` picker on
    every action all show the same word twice. The host is the one thing that is
    always different, so it goes in the title.
    """
    host = urlparse(url).hostname or url
    return f"Audiobookshelf Plus ({host})"


def _server_id(url: str) -> str:
    """Stable identity for one server.

    Audiobookshelf exposes no server GUID, so host:port is the best we have.
    """
    parsed = urlparse(url)
    return (
        f"{parsed.hostname}:{parsed.port or (443 if parsed.scheme == 'https' else 80)}"
    )


class AudiobookshelfOptionsFlow(OptionsFlowWithReload):
    """Link Audiobookshelf users so their books can be resumed on a speaker.

    Audiobookshelf only lets an account write its own listening progress -- there
    is no admin route for writing someone else's. Resuming a book therefore has
    to act *as* that person. Rather than collecting passwords, we use the admin
    key already configured to mint one API key per linked user, and revoke it
    again when they are unlinked.
    """

    def __init__(self) -> None:
        """Initialise the options flow."""
        self._linked: dict[str, Any] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which Audiobookshelf users to link."""
        coordinator = self.config_entry.runtime_data
        errors: dict[str, str] = {}
        existing = dict(self.config_entry.options.get(CONF_LINKED_USERS, {}))
        choices = {
            uid: user.username for uid, user in sorted(coordinator.data.users.items())
        }

        if user_input is not None:
            chosen = set(user_input[CONF_LINKED_USERS])
            try:
                self._linked = await self._async_sync_keys(chosen, existing, choices)
            except AudiobookshelfRestError as err:
                _LOGGER.error("Could not manage Audiobookshelf API keys: %s", err)
                errors["base"] = "key_failed"
            else:
                if not self._linked:
                    return self.async_create_entry(data={CONF_LINKED_USERS: {}})
                return await self.async_step_map()

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_LINKED_USERS, default=sorted(existing)
                    ): cv.multi_select(choices)
                }
            ),
            errors=errors,
        )

    async def async_step_map(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Optionally map each linked user to a Home Assistant account.

        Home Assistant puts the invoking user's id on a service call's context,
        so a mapping lets `continue_listening` know whose book to resume when a
        person presses a dashboard button. Automations carry no user context, so
        this is optional.
        """
        ha_users = {
            user.id: user.name
            for user in await self.hass.auth.async_get_users()
            if not user.system_generated and user.is_active
        }
        options = {"": "Not mapped", **ha_users}

        if user_input is not None:
            for abs_id, ha_id in user_input.items():
                if abs_id in self._linked:
                    self._linked[abs_id]["ha_user_id"] = ha_id or None
            return self.async_create_entry(data={CONF_LINKED_USERS: self._linked})

        schema = vol.Schema(
            {
                vol.Optional(abs_id, default=user.get("ha_user_id") or ""): vol.In(
                    options
                )
                for abs_id, user in self._linked.items()
            }
        )
        return self.async_show_form(
            step_id="map",
            data_schema=schema,
            description_placeholders={
                "users": ", ".join(u["username"] for u in self._linked.values())
            },
        )

    async def _async_sync_keys(
        self,
        chosen: set[str],
        existing: dict[str, Any],
        names: dict[str, str],
    ) -> dict[str, Any]:
        """Mint keys for newly linked users, revoke keys for unlinked ones."""
        coordinator = self.config_entry.runtime_data
        admin: AudiobookshelfRest = coordinator.rest
        linked: dict[str, Any] = {}

        for abs_id in chosen:
            if abs_id in existing:
                linked[abs_id] = existing[abs_id]
                continue
            minted = await admin.async_create_api_key(
                abs_id, f"Home Assistant ({names.get(abs_id, abs_id)})"
            )
            if not minted.get("api_key"):
                raise AudiobookshelfRestError(
                    f"Audiobookshelf returned no key for {names.get(abs_id)}"
                )
            linked[abs_id] = {
                "username": names.get(abs_id, abs_id),
                "api_key": minted["api_key"],
                "key_id": minted["id"],
                "ha_user_id": None,
            }

        # Revoke what we minted for anyone who was just unlinked.
        for abs_id, user in existing.items():
            if abs_id not in chosen and user.get("key_id"):
                try:
                    await admin.async_delete_api_key(user["key_id"])
                except AudiobookshelfRestError as err:
                    _LOGGER.warning(
                        "Could not revoke the Audiobookshelf key for %s: %s",
                        user.get("username"),
                        err,
                    )
        return linked


class CannotConnect(Exception):
    """Cannot reach the server."""


class InvalidAuth(Exception):
    """The API key was rejected."""


class NotAudiobookshelf(Exception):
    """Something answered, but it is not Audiobookshelf."""
