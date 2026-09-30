"""Proxy Audiobookshelf cover art through Home Assistant.

Audiobookshelf serves covers unauthenticated, so this view exists for reach, not
for secrecy: the ABS host is usually a private LAN address, and referencing it
directly from a dashboard breaks remote access and trips mixed-content blocking
when HA is served over HTTPS. Routing art through HA fixes both.

ABS sends no ETag, Last-Modified or Cache-Control on covers, so we synthesize
caching from the item's updatedAt (passed as ?v=).

The URLs are signed with our own key rather than Home Assistant's signed paths,
for two reasons. HA keeps its signing secret in memory, so every restart killed
every cover URL a dashboard was holding, and a card that lazy-loads posters
then fetched dead URLs. And HA answers a dead signed path with 401, which its IP
ban counts as a failed login: a few stale posters could ban a wall display. Our
key is stored, so a URL stays valid (and cached) across restarts, and a bad one
gets a 404, which is not a login failure.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from http import HTTPStatus
from typing import Any

from aiohttp import ClientError, ClientTimeout, web
from homeassistant.components.http import KEY_AUTHENTICATED, HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .const import COVER_URL, DOMAIN

_LOGGER = logging.getLogger(__name__)

_SECRET = f"{DOMAIN}_cover_secret"
_STORAGE_KEY = f"{DOMAIN}.cover"
_STORAGE_VERSION = 1
SIG_PARAM = "sig"


async def async_load_cover_secret(hass: HomeAssistant) -> None:
    """Load the cover-signing key, creating it on first run."""
    store = Store[dict[str, str]](hass, _STORAGE_VERSION, _STORAGE_KEY)
    data = await store.async_load()
    if not data or not data.get("secret"):
        data = {"secret": secrets.token_hex(32)}
        await store.async_save(data)
    hass.data[_SECRET] = data["secret"]


def _signature(hass: HomeAssistant, entry_id: str, item_id: str) -> str:
    """Sign which cover this is; the ?v= cache-buster needs no protection."""
    key = str(hass.data[_SECRET]).encode()
    message = f"{entry_id}/{item_id}".encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def signed_cover_url(
    hass: HomeAssistant, entry_id: str, item_id: str, version: Any
) -> str:
    """Return a stable, signed HA URL for an item's cover.

    Stable for as long as the cover is: a new URL every poll would be a cache
    miss in the browser, so the dashboard would re-download every cover it is
    already showing.
    """
    sig = _signature(hass, entry_id, item_id)
    path = COVER_URL.format(entry_id=entry_id, item_id=item_id)
    return f"{path}?v={version}&{SIG_PARAM}={sig}"


class AudiobookshelfCoverView(HomeAssistantView):
    """Serve ABS cover art to a signed URL, or to a logged-in request."""

    url = COVER_URL
    name = "api:audiobookshelf_plus:cover"
    # <img> tags cannot send a token, so the signature is the credential. A
    # request Home Assistant already authenticated (a card fetching with the
    # user's bearer token) needs no signature. Anything else is a 404 rather
    # than a 401 on purpose.
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise the view."""
        self._hass = hass
        self._session = async_get_clientsession(hass)

    def _signed(self, request: web.Request, entry_id: str, item_id: str) -> bool:
        """Whether the URL carries this cover's signature."""
        return self._hass.data.get(_SECRET) is not None and hmac.compare_digest(
            request.query.get(SIG_PARAM, ""),
            _signature(self._hass, entry_id, item_id),
        )

    async def get(
        self, request: web.Request, entry_id: str, item_id: str
    ) -> web.StreamResponse:
        """Proxy one cover image."""
        if not request.get(KEY_AUTHENTICATED) and not self._signed(
            request, entry_id, item_id
        ):
            return web.Response(status=HTTPStatus.NOT_FOUND)
        entry = self._hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.state is not entry.state.LOADED:
            return web.Response(status=HTTPStatus.NOT_FOUND)

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
                    return web.Response(status=HTTPStatus.NOT_FOUND)
                body = await res.read()
        except (ClientError, TimeoutError) as err:
            _LOGGER.debug("Cover fetch failed for %s: %s", item_id, err)
            return web.Response(status=HTTPStatus.BAD_GATEWAY)

        return web.Response(
            body=body,
            headers={
                "Content-Type": ctype,
                "Cache-Control": "public, max-age=31536000, immutable",
                "ETag": etag,
            },
        )
