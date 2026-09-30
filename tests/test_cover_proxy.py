from __future__ import annotations

from http import HTTPStatus

from homeassistant.components.http.ban import KEY_FAILED_LOGIN_ATTEMPTS

from custom_components.audiobookshelf_plus.cover_proxy import (
    _SECRET,
    async_load_cover_secret,
    signed_cover_url,
)

from .conftest import URL


def _sig(hass, entry_id: str, item_id: str = "item-1") -> str:
    return signed_cover_url(hass, entry_id, item_id, 0).split("sig=")[1]


async def test_signed_url_carries_a_signature(hass, init_integration) -> None:
    """Covers are unauthenticated on the server; the proxy is not."""
    url = signed_cover_url(hass, init_integration.entry_id, "item-1", 123)
    assert url.startswith(
        f"/api/audiobookshelf_plus/cover/{init_integration.entry_id}/item-1"
    )
    assert "v=123" in url
    assert "sig=" in url


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


async def test_signed_url_survives_a_restart(hass, init_integration) -> None:
    """The key is stored, so a dashboard's URLs outlive a Home Assistant restart.

    Home Assistant's own signed paths used an in-memory secret, so every
    restart turned every poster a dashboard held into a failed request.
    """
    before = signed_cover_url(hass, init_integration.entry_id, "item-1", 5)
    hass.data.pop(_SECRET)
    await async_load_cover_secret(hass)  # what the next start does
    assert signed_cover_url(hass, init_integration.entry_id, "item-1", 5) == before


async def test_signature_is_per_cover(hass, init_integration) -> None:
    """One cover's signature does not unlock another."""
    entry_id = init_integration.entry_id
    assert _sig(hass, entry_id, "item-1") != _sig(hass, entry_id, "item-2")


async def test_bad_signature_is_a_404_not_a_failed_login(
    hass, init_integration, hass_client_no_auth
) -> None:
    """A stale or forged cover URL must never count toward an IP ban.

    Home Assistant bans an address after a few 401s. A dashboard holding a
    dozen posters with dead signatures used to be exactly that.
    """
    client = await hass_client_no_auth()
    base = f"/api/audiobookshelf_plus/cover/{init_integration.entry_id}/item-1?v=1"
    for query in ("", "&sig=forged", "&authSig=an-old-home-assistant-signature"):
        resp = await client.get(base + query)
        assert resp.status == HTTPStatus.NOT_FOUND
    assert not hass.http.app.get(KEY_FAILED_LOGIN_ATTEMPTS)


async def test_good_signature_serves_the_cover_without_a_login(
    hass, aioclient_mock, init_integration, hass_client_no_auth
) -> None:
    """An <img> tag has no token; the signature alone is enough."""
    aioclient_mock.get(
        f"{URL}/api/items/item-1/cover",
        content=b"imagebytes",
        headers={"Content-Type": "image/jpeg"},
    )
    client = await hass_client_no_auth()
    resp = await client.get(
        signed_cover_url(hass, init_integration.entry_id, "item-1", 7)
    )
    assert resp.status == HTTPStatus.OK
    assert await resp.read() == b"imagebytes"


async def test_a_logged_in_request_needs_no_signature(
    hass, aioclient_mock, init_integration, hass_client
) -> None:
    """A card fetching with the user's bearer token may drop the signature.

    Dashboards fetch same-origin images with hass.fetchWithAuth so that token
    and signature churn cannot trip the IP ban; some strip the signature.
    """
    aioclient_mock.get(
        f"{URL}/api/items/item-1/cover",
        content=b"imagebytes",
        headers={"Content-Type": "image/jpeg"},
    )
    client = await hass_client()
    path = f"/api/audiobookshelf_plus/cover/{init_integration.entry_id}/item-1?v=7"
    resp = await client.get(path)
    assert resp.status == HTTPStatus.OK
    assert await resp.read() == b"imagebytes"


async def test_cover_view_proxies_the_image(hass, init_integration) -> None:
    """The browser gets the bytes, and cache headers the server never sent."""
    from custom_components.audiobookshelf_plus.cover_proxy import (
        AudiobookshelfCoverView,
    )

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

    class _Request(dict):  # an unauthenticated request; aiohttp requests are mappings
        headers: dict[str, str] = {"Accept": "image/webp"}
        query: dict[str, str] = {
            "v": "55",
            "sig": _sig(hass, init_integration.entry_id),
        }

    view._session.get = lambda *a, **kw: _Response()
    result = await view.get(_Request(), init_integration.entry_id, "item-1")

    assert result.status == 200
    assert result.headers["Content-Type"] == "image/webp"
    assert "immutable" in result.headers["Cache-Control"]
    assert result.headers["ETag"] == 'W/"item-1-55"'
    assert result.body == b"imagebytes"


async def test_cover_view_revalidates(hass, init_integration) -> None:
    """A matching ETag returns 304, so the image is not sent twice."""
    from custom_components.audiobookshelf_plus.cover_proxy import (
        AudiobookshelfCoverView,
    )

    view = AudiobookshelfCoverView(hass)

    class _Request(dict):  # an unauthenticated request; aiohttp requests are mappings
        headers = {"If-None-Match": 'W/"item-1-55"'}
        query = {"v": "55", "sig": _sig(hass, init_integration.entry_id)}

    result = await view.get(_Request(), init_integration.entry_id, "item-1")
    assert result.status == 304


async def test_cover_view_unknown_entry(hass, init_integration) -> None:
    """A request for an entry that is gone is a 404, not a crash."""

    from custom_components.audiobookshelf_plus.cover_proxy import (
        AudiobookshelfCoverView,
    )

    view = AudiobookshelfCoverView(hass)

    class _Request(dict):  # an unauthenticated request; aiohttp requests are mappings
        headers: dict[str, str] = {}
        query: dict[str, str] = {"sig": _sig(hass, "no-such-entry")}

    result = await view.get(_Request(), "no-such-entry", "item-1")
    assert result.status == HTTPStatus.NOT_FOUND


async def test_cover_view_upstream_missing(hass, init_integration) -> None:
    """Audiobookshelf answering with something that is not an image is a 404."""

    from custom_components.audiobookshelf_plus.cover_proxy import (
        AudiobookshelfCoverView,
    )

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

    class _Request(dict):  # an unauthenticated request; aiohttp requests are mappings
        headers: dict[str, str] = {}
        query = {"v": "1", "sig": _sig(hass, init_integration.entry_id)}

    view._session.get = lambda *a, **kw: _Response()
    result = await view.get(_Request(), init_integration.entry_id, "item-1")
    assert result.status == HTTPStatus.NOT_FOUND


async def test_cover_view_upstream_error(hass, init_integration) -> None:
    """A dead server is a 502."""
    from aiohttp import ClientError

    from custom_components.audiobookshelf_plus.cover_proxy import (
        AudiobookshelfCoverView,
    )

    view = AudiobookshelfCoverView(hass)

    def _boom(*args, **kwargs):
        raise ClientError

    class _Request(dict):  # an unauthenticated request; aiohttp requests are mappings
        headers: dict[str, str] = {}
        query = {"v": "1", "sig": _sig(hass, init_integration.entry_id)}

    view._session.get = _boom
    result = await view.get(_Request(), init_integration.entry_id, "item-1")
    assert result.status == HTTPStatus.BAD_GATEWAY
