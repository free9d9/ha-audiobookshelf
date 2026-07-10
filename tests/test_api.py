"""The thin REST layer for the endpoints aioaudiobookshelf does not wrap."""

from __future__ import annotations

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.audiobookshelf.api import (
    AudiobookshelfRest,
    AudiobookshelfRestError,
)

from .conftest import URL

ITEM = "item-1"


def _rest(hass, is_primary=True):
    return AudiobookshelfRest(
        async_get_clientsession(hass), URL + "/", "the-key", is_primary=is_primary
    )


async def test_base_url_is_normalised(hass) -> None:
    """A trailing slash must not double up in every path."""
    assert _rest(hass).base_url == URL


async def test_get_me(hass, aioclient_mock) -> None:
    """The socket token comes from here, because the API key is rejected on it."""
    aioclient_mock.get(f"{URL}/api/me", json={"token": "socket-token"})
    assert (await _rest(hass).async_get_me())["token"] == "socket-token"


async def test_libraries_with_stats(hass, aioclient_mock) -> None:
    aioclient_mock.get(
        f"{URL}/api/libraries",
        json={"libraries": [{"id": "l1", "stats": {"totalItems": 3}}]},
    )
    libraries = await _rest(hass).async_get_libraries_with_stats()
    assert libraries[0]["stats"]["totalItems"] == 3


async def test_libraries_unexpected_shape(hass, aioclient_mock) -> None:
    """A list where a dict was expected must not explode."""
    aioclient_mock.get(f"{URL}/api/libraries", json=[])
    assert await _rest(hass).async_get_libraries_with_stats() == []


async def test_open_sessions(hass, aioclient_mock) -> None:
    aioclient_mock.get(f"{URL}/api/sessions/open", json={"sessions": [{"id": "s"}]})
    assert await _rest(hass).async_get_open_sessions() == [{"id": "s"}]


async def test_users(hass, aioclient_mock) -> None:
    aioclient_mock.get(f"{URL}/api/users", json={"users": [{"id": "u"}]})
    assert await _rest(hass).async_get_users() == [{"id": "u"}]


async def test_users_online(hass, aioclient_mock) -> None:
    """Online means connected, which is a different thing from listening."""
    aioclient_mock.get(
        f"{URL}/api/users/online", json={"usersOnline": [{"username": "Alice"}]}
    )
    users = await _rest(hass).async_get_users_online()
    assert users == [{"username": "Alice"}]


async def test_users_online_unexpected_shape(hass, aioclient_mock) -> None:
    aioclient_mock.get(f"{URL}/api/users/online", json=[])
    assert await _rest(hass).async_get_users_online() == []


async def test_user_stats(hass, aioclient_mock) -> None:
    aioclient_mock.get(
        f"{URL}/api/users/u1/listening-stats", json={"totalTime": 3600, "days": {}}
    )
    stats = await _rest(hass).async_get_user_stats("u1")
    assert stats["totalTime"] == 3600


async def test_user_stats_unexpected_shape(hass, aioclient_mock) -> None:
    aioclient_mock.get(f"{URL}/api/users/u1/listening-stats", json=[])
    assert await _rest(hass).async_get_user_stats("u1") == {}


async def test_get_item(hass, aioclient_mock) -> None:
    aioclient_mock.get(f"{URL}/api/items/{ITEM}", json={"id": ITEM})
    assert (await _rest(hass).async_get_item(ITEM))["id"] == ITEM


async def test_get_item_unexpected_shape(hass, aioclient_mock) -> None:
    aioclient_mock.get(f"{URL}/api/items/{ITEM}", json=[])
    assert await _rest(hass).async_get_item(ITEM) == {}


async def test_delete_progress(hass, aioclient_mock) -> None:
    aioclient_mock.delete(f"{URL}/api/me/progress/prog-1", json={})
    await _rest(hass).async_delete_progress("prog-1")
    assert aioclient_mock.call_count == 1


async def test_scan_library(hass, aioclient_mock) -> None:
    aioclient_mock.post(f"{URL}/api/libraries/l1/scan", json={})
    await _rest(hass).async_scan_library("l1")
    assert aioclient_mock.call_count == 1


async def test_create_api_key(hass, aioclient_mock) -> None:
    """An admin key mints a key for someone else, so no password is needed."""
    aioclient_mock.post(
        f"{URL}/api/api-keys", json={"apiKey": {"id": "k1", "apiKey": "secret"}}
    )
    minted = await _rest(hass).async_create_api_key("u1", "Home Assistant (Alice)")
    assert minted == {"id": "k1", "api_key": "secret"}


async def test_create_api_key_flat_response(hass, aioclient_mock) -> None:
    """Some servers answer without the wrapper."""
    aioclient_mock.post(f"{URL}/api/api-keys", json={"id": "k1", "apiKey": "secret"})
    assert (await _rest(hass).async_create_api_key("u1", "n"))["api_key"] == "secret"


async def test_delete_api_key(hass, aioclient_mock) -> None:
    aioclient_mock.delete(f"{URL}/api/api-keys/k1", json={})
    await _rest(hass).async_delete_api_key("k1")
    assert aioclient_mock.call_count == 1


async def test_items_in_progress(hass, aioclient_mock) -> None:
    aioclient_mock.get(
        f"{URL}/api/me/items-in-progress", json={"libraryItems": [{"id": ITEM}]}
    )
    assert await _rest(hass).async_items_in_progress() == [{"id": ITEM}]


async def test_get_progress(hass, aioclient_mock) -> None:
    aioclient_mock.get(f"{URL}/api/me/progress/{ITEM}", json={"currentTime": 42.0})
    assert (await _rest(hass).async_get_progress(ITEM))["currentTime"] == 42.0


async def test_get_progress_absent(hass, aioclient_mock) -> None:
    """No progress yet is an empty dict, not an error."""
    aioclient_mock.get(f"{URL}/api/me/progress/{ITEM}", status=404)
    assert await _rest(hass, is_primary=False).async_get_progress(ITEM) == {}


async def test_play_item(hass, aioclient_mock) -> None:
    aioclient_mock.post(f"{URL}/api/items/{ITEM}/play", json={"id": "sess"})
    assert (await _rest(hass).async_play_item(ITEM))["id"] == "sess"


async def test_play_podcast_episode(hass, aioclient_mock) -> None:
    aioclient_mock.post(f"{URL}/api/items/{ITEM}/play/ep-1", json={"id": "sess"})
    assert (await _rest(hass).async_play_item(ITEM, "ep-1"))["id"] == "sess"


async def test_sync_session(hass, aioclient_mock) -> None:
    aioclient_mock.post(f"{URL}/api/session/s1/sync", json={})
    await _rest(hass).async_sync_session("s1", 100.0, 20.0, 3600.0)
    assert aioclient_mock.mock_calls[0][2] == {
        "currentTime": 100.0,
        "timeListened": 20.0,
        "duration": 3600.0,
    }


async def test_close_session(hass, aioclient_mock) -> None:
    aioclient_mock.post(f"{URL}/api/session/s1/close", json={})
    await _rest(hass).async_close_session("s1", 100.0, 20.0, 3600.0)
    assert aioclient_mock.mock_calls[0][2]["currentTime"] == 100.0


async def test_close_session_without_sync(hass, aioclient_mock) -> None:
    """An empty body means: keep whatever position you already had."""
    aioclient_mock.post(f"{URL}/api/session/s1/close", json={})
    await _rest(hass).async_close_session_without_sync("s1")
    assert aioclient_mock.mock_calls[0][2] == {}


# ------------------------------------------------------------------- errors


async def test_primary_401_triggers_reauth(hass, aioclient_mock) -> None:
    """The entry key going bad is an entry-level problem."""
    aioclient_mock.get(f"{URL}/api/me", status=401)
    with pytest.raises(ConfigEntryAuthFailed):
        await _rest(hass).async_get_me()


async def test_per_user_401_is_not_reauth(hass, aioclient_mock) -> None:
    """One user's key going bad must not drag the whole integration into reauth."""
    aioclient_mock.get(f"{URL}/api/me", status=403)
    with pytest.raises(AudiobookshelfRestError, match="rejected this user"):
        await _rest(hass, is_primary=False).async_get_me()


async def test_unexpected_status(hass, aioclient_mock) -> None:
    aioclient_mock.get(f"{URL}/api/me", status=500)
    with pytest.raises(AudiobookshelfRestError, match="HTTP 500"):
        await _rest(hass).async_get_me()


async def test_created_status_is_accepted(hass, aioclient_mock) -> None:
    """201 is a success for key creation."""
    aioclient_mock.post(f"{URL}/api/api-keys", status=201, json={"id": "k"})
    assert (await _rest(hass).async_create_api_key("u", "n"))["id"] == "k"


async def test_connection_error(hass, aioclient_mock) -> None:
    aioclient_mock.get(f"{URL}/api/me", exc=TimeoutError)
    with pytest.raises(AudiobookshelfRestError, match="failed"):
        await _rest(hass).async_get_me()


async def test_non_json_response(hass, aioclient_mock) -> None:
    """A text body comes back as text rather than blowing up the decoder."""
    aioclient_mock.post(f"{URL}/api/libraries/l1/scan", text="OK")
    await _rest(hass).async_scan_library("l1")
    assert aioclient_mock.call_count == 1
