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
from time import time
from typing import Any

from aiohttp import ClientError, ClientTimeout, web
from homeassistant.components.http.auth import async_sign_path
from homeassistant.components.http.view import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import COVER_SIGN_RENEW, COVER_SIGN_TTL, COVER_URL

_LOGGER = logging.getLogger(__name__)

# raw_path -> (signed_url, expires_epoch). Keeps the signed URL stable across
# refreshes so the browser is not handed a new URL (and a cache miss) every poll.
_SIGNED_CACHE: dict[str, tuple[str, float]] = {}


def signed_cover_url(
    hass: HomeAssistant, entry_id: str, item_id: str, version: Any
) -> str:
    """Return a stable, signed HA URL for an item's cover."""
    raw = COVER_URL.format(entry_id=entry_id, item_id=item_id) + f"?v={version}"
    now = time()
    if (cached := _SIGNED_CACHE.get(raw)) is not None:
        signed, expires = cached
        if expires - now > COVER_SIGN_RENEW.total_seconds():
            return signed
    signed = async_sign_path(hass, raw, COVER_SIGN_TTL)
    _SIGNED_CACHE[raw] = (signed, now + COVER_SIGN_TTL.total_seconds())
    return signed


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
