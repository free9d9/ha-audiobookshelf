"""Delete a listener's saved progress on Audiobookshelf.

Progress is per-user, and only that user's own key may delete it: every route on
the server is `/me/progress/...`. So this acts as a linked user, the same way
`continue_listening` does.

This is destructive and not undoable. It exists because parents ask for it, so
that a child can start a series again from the beginning.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AudiobookshelfRest, AudiobookshelfRestError
from .const import CONF_LINKED_USERS, DOMAIN, SERVICE_REMOVE_PROGRESS
from .playback import loaded_entry, resolve_user

_LOGGER = logging.getLogger(__name__)

CONF_USER = "user"
CONF_SERIES = "series"
CONF_ITEM_ID = "item_id"

REMOVE_PROGRESS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_USER): cv.string,
        vol.Optional(CONF_SERIES): cv.string,
        vol.Optional(CONF_ITEM_ID): cv.string,
    }
)


async def async_setup_remove_progress(hass: HomeAssistant) -> None:
    """Register the remove_progress action."""

    async def _handle(call: ServiceCall) -> ServiceResponse:
        return await _async_remove_progress(hass, call)

    hass.services.async_register(
        DOMAIN,
        SERVICE_REMOVE_PROGRESS,
        _handle,
        schema=REMOVE_PROGRESS_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )


async def _async_remove_progress(
    hass: HomeAssistant, call: ServiceCall
) -> ServiceResponse:
    """Remove a linked user's progress on one item, or on a whole series."""
    entry = loaded_entry(hass)
    coordinator = entry.runtime_data
    linked = entry.options.get(CONF_LINKED_USERS, {})
    if not linked:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="no_linked_users"
        )

    series = call.data.get(CONF_SERIES)
    item_id = call.data.get(CONF_ITEM_ID)
    if bool(series) == bool(item_id):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="need_series_or_item"
        )

    abs_user = resolve_user(call, linked)
    rest = AudiobookshelfRest(
        async_get_clientsession(hass),
        coordinator.base_url,
        abs_user["api_key"],
        is_primary=False,
    )

    try:
        removed = (
            await _async_remove_one(rest, item_id)
            if item_id
            else await _async_remove_series(rest, str(series))
        )
    except AudiobookshelfRestError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"error": str(err)},
        ) from err

    _LOGGER.info(
        "Removed %d progress record(s) for %s", len(removed), abs_user["username"]
    )
    await coordinator.async_request_refresh()
    return {"user": abs_user["username"], "removed": removed, "count": len(removed)}


async def _async_remove_one(rest: AudiobookshelfRest, item_id: str) -> list[str]:
    """Delete this user's progress on a single item."""
    progress = await rest.async_get_progress(item_id)
    if not progress.get("id"):
        return []
    await rest.async_delete_progress(progress["id"])
    item = await rest.async_get_item(item_id)
    return [_title_of(item) or item_id]


async def _async_remove_series(rest: AudiobookshelfRest, series: str) -> list[str]:
    """Delete this user's progress on every book in a series.

    Only items the user has progress on are inspected, which is a handful rather
    than the whole library.

    Note the comparison. wolffshots' equivalent reads
    `if metadata.series_name is str and ...`, comparing a string value against
    the *type* `str`. That is always False, so nothing was ever deleted.
    """
    me = await rest.async_get_me()
    wanted = series.casefold()
    removed: list[str] = []

    for record in me.get("mediaProgress") or []:
        item_id = record.get("libraryItemId")
        progress_id = record.get("id")
        if not item_id or not progress_id:
            continue
        item = await rest.async_get_item(item_id)
        if not any(name.casefold() == wanted for name in _series_names(item)):
            continue
        await rest.async_delete_progress(progress_id)
        removed.append(_title_of(item) or item_id)

    return removed


def _series_names(item: dict[str, Any]) -> list[str]:
    """Return every series an item belongs to.

    An expanded item carries `metadata.series` as objects; a minified one carries
    `seriesName`, which appends the sequence number ("Wax and Wayne #4"). Both
    shapes are handled so the caller can match on a plain series name.
    """
    metadata = (item.get("media") or {}).get("metadata") or {}
    names = [s["name"] for s in metadata.get("series") or [] if s.get("name")]
    if not names and (flat := metadata.get("seriesName")):
        names = [part.split("#")[0].strip() for part in str(flat).split(",")]
    return names


def _title_of(item: dict[str, Any]) -> str:
    """Return an item's title, for the log line and the action's response."""
    metadata = (item.get("media") or {}).get("metadata") or {}
    return str(metadata.get("title") or "")
