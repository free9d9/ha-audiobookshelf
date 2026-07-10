"""Diagnostics for Audiobookshelf."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant

from .const import CONF_LINKED_USERS
from .coordinator import AudiobookshelfConfigEntry

# The entry's own admin key, plus every per-user key the options flow minted.
# All of them are full account credentials.
TO_REDACT = {CONF_API_KEY, "api_key", "token", "ha_user_id", "user_id", "key_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AudiobookshelfConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": {
                CONF_LINKED_USERS: [
                    {
                        "username": user["username"],
                        "mapped_to_ha_user": bool(user.get("ha_user_id")),
                    }
                    for user in entry.options.get(CONF_LINKED_USERS, {}).values()
                ]
            },
        },
        "connection": {
            "realtime_connected": coordinator.connected,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
            "last_update_success": coordinator.last_update_success,
        },
        "libraries": [
            {
                "name": library.name,
                "media_type": library.media_type,
                "detected_as_ebook": library.is_ebook,
                "total_items": library.total_items,
                "total_duration_hours": round(library.total_duration / 3600, 1),
                "total_size_gb": round(library.total_size / 1_000_000_000, 1),
                "recently_added_count": len(library.recent),
                "newest_added": (
                    library.newest_added.isoformat() if library.newest_added else None
                ),
            }
            for library in data.libraries.values()
        ],
        # Usernames are the whole point of these entities, so they stay. Ids and
        # tokens do not.
        "users": [
            {
                "username": user.username,
                "type": user.user_type,
                "has_open_session": user.session is not None,
                "session_is_live": user.session.is_live if user.session else None,
                "last_listened": (
                    user.last_listened.isoformat() if user.last_listened else None
                ),
                "session": (
                    async_redact_data(asdict(user.session), TO_REDACT)
                    if user.session
                    else None
                ),
            }
            for user in data.users.values()
        ],
        "listening_now": [user.username for user in data.listening_now],
    }
