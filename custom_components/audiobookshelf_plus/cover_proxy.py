"""Proxy Audiobookshelf cover art through Home Assistant.

Audiobookshelf serves covers unauthenticated, so this view exists for reach, not
for secrecy: the ABS host is usually a private LAN address, and referencing it
directly from a dashboard breaks remote access and trips mixed-content blocking
when HA is served over HTTPS. Routing art through HA fixes both.

ABS sends no ETag, Last-Modified or Cache-Control on covers, so we synthesize
caching from the item's updatedAt (passed as ?v=).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from time import time
from typing import Any

from aiohttp import ClientError, ClientTimeout, web
from homeassistant.components.http.auth import async_sign_path
from homeassistant.components.http.view import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import COVER_SIGN_RENEW, COVER_SIGN_TTL, COVER_URL

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Signed:
    """One item's signed cover URL, and when it stops being usable."""

    raw: str
    signed: str
    expires: float


# (entry_id, item_id) -> the signed URL last handed out for it. Keeping it
# stable across refreshes matters: a new URL every poll is a cache miss in the
# browser, so the dashboard re-downloads every cover it is already showing.
#
# Keyed by item rather than by URL, deliberately. The raw URL carries the item's
# updatedAt as ?v=, so an actively-edited library would mint a new key per edit
# and never drop the old ones; one row per item means an edit *replaces* the row
# it supersedes, and the map can never outgrow the number of covers rendered.
_SIGNED_CACHE: dict[tuple[str, str], _Signed] = {}


def signed_cover_url(
    hass: HomeAssistant, entry_id: str, item_id: str, version: Any
) -> str:
    """Return a stable, signed HA URL for an item's cover."""
    raw = COVER_URL.format(entry_id=entry_id, item_id=item_id) + f"?v={version}"
    now = time()
    cached = _SIGNED_CACHE.get((entry_id, item_id))
    if (
        cached is not None
        and cached.raw == raw
        and cached.expires - now > COVER_SIGN_RENEW.total_seconds()
    ):
        return cached.signed

    # Signing is rare -- once per cover per six days -- so this is the cheap
    # place to drop rows for items that have since left the library, which
    # otherwise sit here until Home Assistant restarts.
    _purge_expired(now)

    signed = async_sign_path(hass, raw, COVER_SIGN_TTL)
    _SIGNED_CACHE[(entry_id, item_id)] = _Signed(
        raw, signed, now + COVER_SIGN_TTL.total_seconds()
    )
    return signed


def _purge_expired(now: float) -> None:
    """Drop signed URLs that have lapsed."""
    for key in [key for key, row in _SIGNED_CACHE.items() if row.expires <= now]:
        del _SIGNED_CACHE[key]


def release_signed_urls(entry_id: str) -> None:
    """Forget one config entry's signed URLs, on unload."""
    for key in [key for key in _SIGNED_CACHE if key[0] == entry_id]:
        del _SIGNED_CACHE[key]


class AudiobookshelfCoverView(HomeAssistantView):
    """Serve ABS cover art. Auth is satisfied by the signed path."""

    url = COVER_URL
    name = "api:audiobookshelf_plus:cover"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise the view."""
        self._hass = hass
        self._session = async_get_clientsession(hass)

    async def get(
        self, request: web.Request, entry_id: str, item_id: str
    ) -> web.StreamResponse:
        """Proxy one cover image."""
        entry = self._hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.state is not entry.state.LOADED:
            return web.HTTPNotFound()

        base_url = entry.runtime_data.base_url
        version = request.query.get("v", "")

        # ABS content-negotiates webp vs jpeg off Accept, so pass the browser's through.
        headers = {}
        if accept := request.headers.get("Accept"):
            headers["Accept"] = accept

        etag = f'W/"{item_id}-{version}"'
        if request.headers.get("If-None-Match") == etag:
            return web.Response(status=304)

        try:
            async with self._session.get(
                f"{base_url}/api/items/{item_id}/cover",
                headers=headers,
                timeout=ClientTimeout(total=10),
            ) as res:
                ctype = res.headers.get("Content-Type", "")
                if res.status != 200 or not ctype.startswith("image/"):
                    _LOGGER.debug(
                        "No cover for %s (status=%s type=%s)",
                        item_id,
                        res.status,
                        ctype,
                    )
                    return web.HTTPNotFound()
                body = await res.read()
        except (ClientError, TimeoutError) as err:
            _LOGGER.debug("Cover fetch failed for %s: %s", item_id, err)
            return web.HTTPBadGateway()

        return web.Response(
            body=body,
            headers={
                "Content-Type": ctype,
                "Cache-Control": "public, max-age=31536000, immutable",
                "ETag": etag,
            },
        )
