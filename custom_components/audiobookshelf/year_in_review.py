"""A listener's year of reading, as an action response.

Audiobookshelf builds this for its own year-in-review screen. It is five
different shapes of data (a count, three ranked lists, a month), it changes
meaningfully once a year, and a server accumulates accounts. Entities would mean
six frozen sensors per user, most of them a list stuffed into an attribute, for
something you look at each December. So it is an action that hands the whole
structure back and stores nothing.

Like `remove_progress`, this reads `/api/me/...`, so it must run as a linked
user. An admin key does not raise here: it answers 200 with the admin's own,
usually empty, year. There is no way to ask the server for someone else's.
"""

from __future__ import annotations

import calendar
import logging
from typing import Any

import voluptuous as vol
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .api import AudiobookshelfRest, AudiobookshelfRestError
from .const import (
    CONF_LINKED_USERS,
    DOMAIN,
    MAX_STATS_YEAR,
    MIN_STATS_YEAR,
    SERVICE_YEAR_IN_REVIEW,
)
from .playback import loaded_entry, resolve_user

_LOGGER = logging.getLogger(__name__)

CONF_USER = "user"
CONF_YEAR = "year"

YEAR_IN_REVIEW_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_USER): cv.string,
        vol.Optional(CONF_YEAR): vol.All(
            vol.Coerce(int), vol.Range(min=MIN_STATS_YEAR, max=MAX_STATS_YEAR)
        ),
    }
)


async def async_setup_year_in_review(hass: HomeAssistant) -> None:
    """Register the get_year_in_review action."""

    async def _handle(call: ServiceCall) -> ServiceResponse:
        return await _async_year_in_review(hass, call)

    hass.services.async_register(
        DOMAIN,
        SERVICE_YEAR_IN_REVIEW,
        _handle,
        schema=YEAR_IN_REVIEW_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )


async def _async_year_in_review(
    hass: HomeAssistant, call: ServiceCall
) -> ServiceResponse:
    """Return one linked user's listening summary for a calendar year."""
    entry = loaded_entry(hass)
    coordinator = entry.runtime_data
    linked = entry.options.get(CONF_LINKED_USERS, {})
    if not linked:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="no_linked_users"
        )

    year = int(call.data.get(CONF_YEAR) or dt_util.now().year)
    abs_user = resolve_user(call, linked)
    rest = AudiobookshelfRest(
        async_get_clientsession(hass),
        coordinator.base_url,
        abs_user["api_key"],
        is_primary=False,
    )

    try:
        raw = await rest.async_get_year_in_review(year)
    except AudiobookshelfRestError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"error": str(err)},
        ) from err

    return _shape(raw, abs_user["username"], year)


def _shape(raw: dict[str, Any], username: str, year: int) -> dict[str, Any]:
    """Turn Audiobookshelf's payload into something a template can read.

    Seconds become hours, because nobody wants to divide by 3600 in Jinja. The
    cover-id lists are dropped: they are only useful to Audiobookshelf's own
    share-image renderer, and the ids alone will not load a cover.
    """
    return {
        "user": username,
        "year": year,
        "books_finished": int(raw.get("numBooksFinished") or 0),
        "books_started": int(raw.get("numBooksListened") or 0),
        "sessions": int(raw.get("totalListeningSessions") or 0),
        "hours": _hours(raw.get("totalListeningTime")),
        "book_hours": _hours(raw.get("totalBookListeningTime")),
        "podcast_hours": _hours(raw.get("totalPodcastListeningTime")),
        "top_authors": [
            {"name": a.get("name", ""), "hours": _hours(a.get("time"))}
            for a in raw.get("topAuthors") or []
        ],
        "top_genres": [
            {"genre": g.get("genre", ""), "hours": _hours(g.get("time"))}
            for g in raw.get("topGenres") or []
        ],
        "top_narrator": _named(raw.get("mostListenedNarrator")),
        "top_month": _month(raw.get("mostListenedMonth")),
        "longest_book": _longest(raw.get("longestAudiobookFinished")),
    }


def _hours(seconds: Any) -> float:
    """Seconds to hours, to one decimal."""
    try:
        return round(float(seconds or 0) / 3600, 1)
    except (TypeError, ValueError):
        return 0.0


def _named(raw: Any) -> dict[str, Any] | None:
    """Return a `{name, hours}` pair, or None when the year holds nothing."""
    if not isinstance(raw, dict) or not raw.get("name"):
        return None
    return {"name": str(raw["name"]), "hours": _hours(raw.get("time"))}


def _month(raw: Any) -> dict[str, Any] | None:
    """Return the busiest month, named.

    Audiobookshelf reports it from JavaScript's `Date.getMonth()`, which counts
    from zero: their own client renders it as `new Date(year, month, 1)`. Reading
    it as a month number gives you April when the answer is May.
    """
    if not isinstance(raw, dict) or raw.get("month") is None:
        return None
    try:
        index = int(raw["month"])
    except (TypeError, ValueError):
        return None
    if not 0 <= index <= 11:
        return None
    return {
        "month": index + 1,
        "name": calendar.month_name[index + 1],
        "hours": _hours(raw.get("time")),
    }


def _longest(raw: Any) -> dict[str, Any] | None:
    """Return the longest book finished this year."""
    if not isinstance(raw, dict) or not raw.get("title"):
        return None
    return {
        "title": str(raw["title"]),
        "item_id": str(raw.get("id") or ""),
        "hours": _hours(raw.get("duration")),
        "finished_at": raw.get("finishedAt"),
    }
