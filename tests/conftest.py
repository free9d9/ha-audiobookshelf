"""Fixtures for the Audiobookshelf tests."""

from __future__ import annotations

import base64
import datetime as dt
import json
import time
from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_API_KEY, CONF_URL
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.audiobookshelf_plus.const import CONF_LINKED_USERS, DOMAIN
from custom_components.audiobookshelf_plus.release import ReleaseInfo

pytest_plugins = "pytest_homeassistant_custom_component"

URL = "http://abs.example.com:13378"
# Item timestamps are historical; a user's "last listened" must be recent, or the
# media player is (correctly) created disabled as a dormant account.
NOW_MS = 1_700_000_000_000


def make_api_key(
    expires_in_days: float | None = None,
    *,
    expires_at: dt.datetime | None = None,
) -> str:
    """Build a JWT-shaped API key, optionally with an `exp` claim.

    Audiobookshelf signs these; we only ever read them, so an unsigned fake with
    the right shape exercises exactly the code path that matters. Pass
    `expires_at` when the exact wall-clock instant matters, `expires_in_days`
    when only the rough distance does.
    """
    claims: dict[str, Any] = {"keyId": "abc", "name": "HA", "type": "api"}
    if expires_at is not None:
        claims["exp"] = int(expires_at.timestamp())
    elif expires_in_days is not None:
        claims["exp"] = int(time.time() + expires_in_days * 86400)
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"header.{body}.signature"


API_KEY = make_api_key()


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load `custom_components/` for every test."""


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """A configured Audiobookshelf entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Audiobookshelf",
        unique_id="abs.example.com:13378",
        data={CONF_URL: URL, CONF_API_KEY: API_KEY},
        options={CONF_LINKED_USERS: {}},
    )


LATEST_RELEASE = ReleaseInfo(
    version="2.36.0",
    url="https://github.com/advplyr/audiobookshelf/releases/tag/v2.36.0",
    # Deliberately over 255 characters, the cap on `release_summary`.
    notes="## Highlights\n\nA very long changelog." + "." * 300,
)
SERVER_VERSION = "2.35.1"


def _library(library_id: str, name: str, media_type: str = "book") -> dict[str, Any]:
    return {
        "id": library_id,
        "name": name,
        "mediaType": media_type,
        "lastScan": NOW_MS,
        "stats": {
            "totalItems": 10,
            "totalDuration": 36000.0,
            "totalSize": 2_000_000_000,
        },
    }


def _item(item_id: str, title: str, duration: float) -> MagicMock:
    """A minified library item as aioaudiobookshelf models it."""
    item = MagicMock()
    item.id_ = item_id
    item.added_at = NOW_MS
    item.updated_at = NOW_MS
    item.media.duration = duration
    item.media.metadata.title = title
    item.media.metadata.author_name = "An Author"
    item.media.metadata.narrator_name = "A Narrator"
    item.media.metadata.series_name = ""
    item.media.metadata.subtitle = ""
    item.media.metadata.publisher = "A Publisher"
    item.media.metadata.published_year = 2024
    item.media.metadata.genres = ["Fantasy", "Fiction"]
    return item


def _shelf(items: list[MagicMock]) -> MagicMock:
    shelf = MagicMock()
    shelf.id_ = "recently-added"
    shelf.entities = items
    return shelf


@pytest.fixture
def mock_abs_client() -> Generator[MagicMock]:
    """Patch aioaudiobookshelf's admin client and socket client."""
    client = MagicMock()
    client.get_all_libraries = AsyncMock(return_value=[])

    async def _personalized(*, library_id: str, limit: int) -> list[MagicMock]:
        if library_id == "lib-books":
            return [_shelf([_item("item-1", "A Book", 3600.0)])]
        if library_id == "lib-ebooks":
            return [_shelf([_item("item-2", "An Ebook", 0.0)])]
        return []

    client.get_library_personalized_view = AsyncMock(side_effect=_personalized)

    socket = MagicMock()
    socket.client.connected = True
    socket.init_client = AsyncMock()
    socket.logout = AsyncMock()
    socket.set_item_callbacks = MagicMock()

    with (
        patch(
            "custom_components.audiobookshelf_plus.coordinator.absapi.get_admin_client_by_token",
            AsyncMock(return_value=client),
        ),
        patch(
            "custom_components.audiobookshelf_plus.coordinator.SocketClient",
            return_value=socket,
        ),
    ):
        client.socket = socket
        yield client


@pytest.fixture
def mock_rest() -> Generator[MagicMock]:
    """Patch our thin REST layer for the endpoints aioaudiobookshelf lacks."""
    rest = MagicMock()
    rest.async_get_me = AsyncMock(return_value={"token": "socket-token"})
    rest.async_get_libraries_with_stats = AsyncMock(
        return_value=[
            _library("lib-books", "Audiobooks"),
            _library("lib-ebooks", "E-Books"),
        ]
    )
    rest.async_get_users = AsyncMock(
        return_value=[
            {
                "id": "u1",
                "username": "Alice",
                "type": "admin",
                "latestSession": {
                    "libraryItemId": "item-latest",
                    "displayTitle": "Last Book",
                    "displayAuthor": "Last Author",
                    "duration": 7200.0,
                    "currentTime": 1800.0,
                    "updatedAt": int(time.time() * 1000),
                },
            },
            {"id": "u2", "username": "Bob", "type": "user", "latestSession": None},
        ]
    )
    rest.async_get_open_sessions = AsyncMock(return_value=[])
    rest.async_get_users_online = AsyncMock(return_value=[{"username": "Alice"}])
    rest.async_get_status = AsyncMock(return_value={"serverVersion": SERVER_VERSION})
    rest.async_get_issue_count = AsyncMock(return_value=0)
    rest.async_get_user_stats = AsyncMock(side_effect=_stats_for)
    rest.async_scan_library = AsyncMock()
    rest.async_create_api_key = AsyncMock(
        return_value={"id": "key-1", "api_key": make_api_key()}
    )
    rest.async_delete_api_key = AsyncMock()

    with patch(
        "custom_components.audiobookshelf_plus.coordinator.AudiobookshelfRest",
        return_value=rest,
    ):
        yield rest


@pytest.fixture(autouse=True)
def mock_release() -> Generator[AsyncMock]:
    """Never let the test suite call GitHub."""
    with patch(
        "custom_components.audiobookshelf_plus.coordinator.async_get_latest_release",
        AsyncMock(return_value=LATEST_RELEASE),
    ) as mock:
        yield mock


@pytest.fixture
async def init_integration(
    hass, mock_config_entry, mock_abs_client, mock_rest
) -> MockConfigEntry:
    """Set up the integration with everything mocked."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry


def stats_payload(today_h: float = 1.0) -> dict[str, Any]:
    """A listening-stats response, with a day map spanning the calendar buckets."""
    now = dt.date.today()
    days = {
        # first of this month, always inside month and year
        now.replace(day=1).isoformat(): 7200.0,
        # yesterday, so it lands in this week only if today is not Monday
        (now - dt.timedelta(days=1)).isoformat(): 3600.0,
        "not-a-date": 999.0,  # must be skipped, not crash
        # Today goes in last so it wins the collisions the keys above can
        # produce: on the first of the month that key IS today, and writing it
        # earlier left the today bucket holding 7200.0 instead of `today_h`.
        now.isoformat(): today_h * 3600,
    }
    return {
        "totalTime": 360000.0,  # 100 h, deliberately more than `days` sums to
        "today": today_h * 3600,
        "days": days,
        "dayOfWeek": {"Monday": 3600.0, "Tuesday": 1800.0},
        "items": {},
        "recentSessions": [],
    }


async def _stats_for(user_id: str) -> dict[str, Any]:
    return stats_payload() if user_id == "u1" else {"totalTime": 0.0, "days": {}}


def open_session(
    user_id: str = "u1",
    session_id: str = "sess-1",
    updated_at: int | None = None,
    title: str = "A Book",
) -> dict[str, Any]:
    """An /api/sessions/open entry."""
    return {
        "id": session_id,
        "userId": user_id,
        "libraryItemId": "item-1",
        "displayTitle": title,
        "displayAuthor": "An Author",
        "duration": 3600.0,
        "currentTime": 120.0,
        "updatedAt": updated_at if updated_at is not None else int(time.time() * 1000),
        "episodeId": None,
        "mediaPlayer": "exo-player",
        "deviceInfo": {"manufacturer": "samsung", "model": "SM-F946U1"},
    }
