"""The signed cover proxy."""

from __future__ import annotations

from custom_components.audiobookshelf.cover_proxy import (
    _SIGNED_CACHE,
    signed_cover_url,
)


async def test_signed_url_carries_a_signature(hass, init_integration) -> None:
    """Covers are unauthenticated on the server; the proxy is not."""
    url = signed_cover_url(hass, init_integration.entry_id, "item-1", 123)
    assert url.startswith(
        f"/api/audiobookshelf/cover/{init_integration.entry_id}/item-1"
    )
    assert "v=123" in url
    assert "authSig=" in url


async def test_signed_url_is_stable_across_calls(hass, init_integration) -> None:
    """A new URL on every poll would bust the browser's cache of every poster."""
    first = signed_cover_url(hass, init_integration.entry_id, "item-1", 123)
    second = signed_cover_url(hass, init_integration.entry_id, "item-1", 123)
    assert first == second


async def test_signed_url_changes_when_the_cover_changes(
    hass, init_integration
) -> None:
    """A new updatedAt is a new URL, which is how the browser learns to refetch."""
    first = signed_cover_url(hass, init_integration.entry_id, "item-1", 1)
    second = signed_cover_url(hass, init_integration.entry_id, "item-1", 2)
    assert first != second


async def test_signed_url_is_resigned_near_expiry(hass, init_integration) -> None:
    """The cache re-signs before the signature lapses, not after."""
    _SIGNED_CACHE.clear()
    url = signed_cover_url(hass, init_integration.entry_id, "item-1", 9)
    raw = next(iter(_SIGNED_CACHE))
    # Pretend the cached signature is about to expire.
    _SIGNED_CACHE[raw] = (url, 0.0)
    fresh = signed_cover_url(hass, init_integration.entry_id, "item-1", 9)
    assert _SIGNED_CACHE[raw][1] > 0.0
    assert fresh.startswith("/api/audiobookshelf/cover/")


async def test_cover_view_proxies_the_image(hass, init_integration) -> None:
    """The browser gets the bytes, and cache headers the server never sent."""
    from custom_components.audiobookshelf.cover_proxy import AudiobookshelfCoverView

    view = AudiobookshelfCoverView(hass)

    class _Response:
        status = 200
        headers = {"Content-Type": "image/webp"}

        async def read(self):
            return b"imagebytes"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class _Request:
        headers: dict[str, str] = {"Accept": "image/webp"}
        query: dict[str, str] = {"v": "55"}

    view._session.get = lambda *a, **kw: _Response()
    result = await view.get(_Request(), init_integration.entry_id, "item-1")

    assert result.status == 200
    assert result.headers["Content-Type"] == "image/webp"
    assert "immutable" in result.headers["Cache-Control"]
    assert result.headers["ETag"] == 'W/"item-1-55"'
    assert result.body == b"imagebytes"


async def test_cover_view_revalidates(hass, init_integration) -> None:
    """A matching ETag returns 304, so the image is not sent twice."""
    from custom_components.audiobookshelf.cover_proxy import AudiobookshelfCoverView

    view = AudiobookshelfCoverView(hass)

    class _Request:
        headers = {"If-None-Match": 'W/"item-1-55"'}
        query = {"v": "55"}

    result = await view.get(_Request(), init_integration.entry_id, "item-1")
    assert result.status == 304


async def test_cover_view_unknown_entry(hass, init_integration) -> None:
    """A request for an entry that is gone is a 404, not a crash."""
    from aiohttp import web

    from custom_components.audiobookshelf.cover_proxy import AudiobookshelfCoverView

    view = AudiobookshelfCoverView(hass)

    class _Request:
        headers: dict[str, str] = {}
        query: dict[str, str] = {}

    result = await view.get(_Request(), "no-such-entry", "item-1")
    assert isinstance(result, web.HTTPNotFound)


async def test_cover_view_upstream_missing(hass, init_integration) -> None:
    """Audiobookshelf answering with something that is not an image is a 404."""
    from aiohttp import web

    from custom_components.audiobookshelf.cover_proxy import AudiobookshelfCoverView

    view = AudiobookshelfCoverView(hass)

    class _Response:
        status = 404
        headers = {"Content-Type": "text/html"}

        async def read(self):
            return b""

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class _Request:
        headers: dict[str, str] = {}
        query = {"v": "1"}

    view._session.get = lambda *a, **kw: _Response()
    result = await view.get(_Request(), init_integration.entry_id, "item-1")
    assert isinstance(result, web.HTTPNotFound)


async def test_cover_view_upstream_error(hass, init_integration) -> None:
    """A dead server is a 502."""
    from aiohttp import ClientError, web

    from custom_components.audiobookshelf.cover_proxy import AudiobookshelfCoverView

    view = AudiobookshelfCoverView(hass)

    def _boom(*args, **kwargs):
        raise ClientError

    class _Request:
        headers: dict[str, str] = {}
        query = {"v": "1"}

    view._session.get = _boom
    result = await view.get(_Request(), init_integration.entry_id, "item-1")
    assert isinstance(result, web.HTTPBadGateway)
