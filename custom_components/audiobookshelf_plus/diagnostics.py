"""Diagnostics for Audiobookshelf."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.core import HomeAssistant

from .const import CONF_LINKED_USERS
from .coordinator import AudiobookshelfConfigEntry

# The entry's own admin key, plus every per-user key the options flow minted.
# All of them are full account credentials. The server URL goes too: people
# attach diagnostics to public issues, and it names their host. So do account
# usernames, which name the household; those become neutral labels below.
TO_REDACT = {
    CONF_API_KEY,
    CONF_URL,
    "api_key",
    "token",
    "ha_user_id",
    "user_id",
    "key_id",
    "username",
}


def _user_labels(
    server_user_ids: list[str], linked_user_ids: list[str]
) -> dict[str, str]:
    """Give every account a neutral label, the same one in every section.

    A dump still has to show that the linked account is the one that is
    listening, so each Audiobookshelf user id maps to one `user_N`.
    """
    labels: dict[str, str] = {}
    for user_id in [*server_user_ids, *linked_user_ids]:
        labels.setdefault(user_id, f"user_{len(labels) + 1}")
    return labels


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AudiobookshelfConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data
    linked: dict[str, Any] = entry.options.get(CONF_LINKED_USERS, {})
    labels = _user_labels(list(data.users), list(linked))

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": {
                CONF_LINKED_USERS: [
                    {
                        "user": labels[user_id],
                        "mapped_to_ha_user": bool(user.get("ha_user_id")),
                    }
                    for user_id, user in linked.items()
                ]
            },
        },
        "connection": {
            "server_version": data.server_version or None,
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
        "users": [
            {
                "user": labels[user_id],
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
            for user_id, user in data.users.items()
        ],
        "listening_now": [labels[user.user_id] for user in data.listening_now],
    }
