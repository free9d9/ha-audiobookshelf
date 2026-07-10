"""The get_year_in_review action.

The payload here is copied from a live Audiobookshelf 2.35.1, not from the docs.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from freezegun import freeze_time
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.audiobookshelf.api import (
    AudiobookshelfRest,
    AudiobookshelfRestError,
)
from custom_components.audiobookshelf.const import (
    CONF_LINKED_USERS,
    DOMAIN,
    SERVICE_YEAR_IN_REVIEW,
)
from custom_components.audiobookshelf.year_in_review import _hours, _month

from .conftest import URL, make_api_key

# Trimmed from a real response. Note `month: 4`, which is May.
YEAR_2026 = {
    "totalListeningSessions": 837,
    "totalListeningTime": 1990739,
    "totalBookListeningTime": 1990739,
    "totalPodcastListeningTime": 0,
    "topAuthors": [
        {"name": "C. Mantis", "time": 962173},
        {"name": "Matt Dinniman", "time": 500000},
    ],
    "topGenres": [{"genre": "Science Fiction & Fantasy", "time": 1915179}],
    "mostListenedNarrator": {"name": "J.S. Arquin", "time": 962173},
    "mostListenedMonth": {"month": 4, "time": 399768},
    "numBooksFinished": 25,
    "numBooksListened": 20,
    "longestAudiobookFinished": {
        "id": "bfa5df16",
        "title": "This Inevitable Ruin",
        "duration": 103229,
        "finishedAt": "2026-06-28T05:16:32.525Z",
    },
    "booksWithCovers": ["e2ddbe0a", "0bfd99b6"],
    "finishedBooksWithCovers": ["0bfd99b6"],
}


@pytest.fixture
def linked(hass, init_integration):
    hass.config_entries.async_update_entry(
        init_integration,
        options={
            CONF_LINKED_USERS: {
                "u1": {
                    "username": "Alice",
                    "api_key": make_api_key(),
                    "key_id": "key-1",
                    "ha_user_id": "ha-alice",
                }
            }
        },
    )
    return init_integration


@pytest.fixture
def user_rest():
    rest = MagicMock()
    rest.async_get_year_in_review = AsyncMock(return_value=YEAR_2026)
    with patch(
        "custom_components.audiobookshelf.year_in_review.AudiobookshelfRest",
        return_value=rest,
    ):
        yield rest


async def call(hass, **data):
    return await hass.services.async_call(
        DOMAIN,
        SERVICE_YEAR_IN_REVIEW,
        data,
        blocking=True,
        return_response=True,
        context=Context(user_id="ha-alice"),
    )


# ------------------------------------------------------------------- shaping


async def test_the_whole_year(hass, linked, user_rest) -> None:
    result = await call(hass, year=2026)

    assert result["user"] == "Alice"
    assert result["year"] == 2026
    assert result["books_finished"] == 25
    assert result["books_started"] == 20
    assert result["sessions"] == 837
    assert result["hours"] == 553.0  # 1990739 seconds, to one decimal
    user_rest.async_get_year_in_review.assert_awaited_once_with(2026)


async def test_seconds_become_hours(hass, linked, user_rest) -> None:
    """Nobody wants to divide by 3600 in a Jinja template."""
    result = await call(hass, year=2026)
    assert result["hours"] == 553.0
    assert result["book_hours"] == 553.0
    assert result["podcast_hours"] == 0.0
    assert result["top_authors"][0] == {"name": "C. Mantis", "hours": 267.3}
    assert result["top_genres"][0]["genre"] == "Science Fiction & Fantasy"
    assert result["top_narrator"] == {"name": "J.S. Arquin", "hours": 267.3}


async def test_the_busiest_month_is_not_off_by_one(hass, linked, user_rest) -> None:
    """`month: 4` is May. Audiobookshelf counts months from zero.

    Their own client renders it with `new Date(year, month, 1)`. Reading it as a
    month number gives April, and nobody would ever notice.
    """
    result = await call(hass, year=2026)
    assert result["top_month"] == {"month": 5, "name": "May", "hours": 111.0}


async def test_longest_book(hass, linked, user_rest) -> None:
    result = await call(hass, year=2026)
    assert result["longest_book"] == {
        "title": "This Inevitable Ruin",
        "item_id": "bfa5df16",
        "hours": 28.7,
        "finished_at": "2026-06-28T05:16:32.525Z",
    }


async def test_cover_id_lists_are_dropped(hass, linked, user_rest) -> None:
    """They only feed Audiobookshelf's share-image renderer."""
    result = await call(hass, year=2026)
    assert "booksWithCovers" not in result
    assert "finishedBooksWithCovers" not in result


@freeze_time("2026-07-10 12:00:00")
async def test_year_defaults_to_this_year(hass, linked, user_rest) -> None:
    await call(hass)
    user_rest.async_get_year_in_review.assert_awaited_once_with(2026)


# -------------------------------------------------------------- empty & junk


async def test_a_year_with_no_listening(hass, linked, user_rest) -> None:
    """A new account, or a year before they joined. Zeroes, not exceptions."""
    user_rest.async_get_year_in_review.return_value = {
        "totalListeningSessions": 0,
        "topAuthors": [],
        "topGenres": [],
        "mostListenedNarrator": None,
        "mostListenedMonth": None,
        "longestAudiobookFinished": None,
    }
    result = await call(hass, year=2001)

    assert result["books_finished"] == 0
    assert result["hours"] == 0.0
    assert result["top_authors"] == []
    assert result["top_narrator"] is None
    assert result["top_month"] is None
    assert result["longest_book"] is None


async def test_an_empty_payload(hass, linked, user_rest) -> None:
    user_rest.async_get_year_in_review.return_value = {}
    result = await call(hass, year=2026)
    assert result["sessions"] == 0
    assert result["top_month"] is None


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "nonsense",
        {},
        {"month": None},
        {"month": "May"},
        {"month": 12},  # out of range: zero-based, so 11 is December
        {"month": -1},
    ],
)
def test_month_survives_junk(raw) -> None:
    assert _month(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (3600, 1.0),
        ("3600", 1.0),  # the server has shipped numbers as strings before
        (None, 0.0),
        (0, 0.0),
        ("not a number", 0.0),
        ([], 0.0),
    ],
)
def test_hours_survives_junk(raw, expected) -> None:
    assert _hours(raw) == expected


def test_month_boundaries() -> None:
    assert _month({"month": 0, "time": 0})["name"] == "January"
    assert _month({"month": 11, "time": 0})["name"] == "December"


# ------------------------------------------------------------------ validation


async def test_no_linked_users(hass, init_integration, user_rest) -> None:
    with pytest.raises(ServiceValidationError, match="No Audiobookshelf users"):
        await call(hass, year=2026)


async def test_unknown_user(hass, linked, user_rest) -> None:
    with pytest.raises(ServiceValidationError, match="not a linked"):
        await call(hass, user="Nobody")


@pytest.mark.parametrize("year", [1999, 10000])
async def test_years_the_server_would_reject(hass, linked, user_rest, year) -> None:
    """Audiobookshelf answers 400 outside 2000-9999. Catch it before the round trip."""
    with pytest.raises(vol.Invalid):
        await call(hass, year=year)
    user_rest.async_get_year_in_review.assert_not_awaited()


async def test_server_error_surfaces(hass, linked, user_rest) -> None:
    user_rest.async_get_year_in_review.side_effect = AudiobookshelfRestError("down")
    with pytest.raises(HomeAssistantError, match="Could not reach"):
        await call(hass, year=2026)


# ------------------------------------------------------------------------ rest


async def test_rest_hits_the_me_route(hass, aioclient_mock) -> None:
    """`/api/me/...` is the key owner, which is why this needs a per-user key."""
    aioclient_mock.get(f"{URL}/api/me/stats/year/2026", json=YEAR_2026)
    rest = AudiobookshelfRest(
        async_get_clientsession(hass), URL, "user-key", is_primary=False
    )
    assert (await rest.async_get_year_in_review(2026))["numBooksFinished"] == 25


async def test_rest_unexpected_shape(hass, aioclient_mock) -> None:
    aioclient_mock.get(f"{URL}/api/me/stats/year/2026", json=[])
    rest = AudiobookshelfRest(async_get_clientsession(hass), URL, "k", is_primary=False)
    assert await rest.async_get_year_in_review(2026) == {}
