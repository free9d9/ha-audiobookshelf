"""Ask GitHub whether a newer Audiobookshelf has been released.

This is the only part of the integration that leaves your network. Audiobookshelf
exposes no version-check endpoint of its own, so there is nowhere else to ask.

It is checked once a day, it never fails a refresh, and disabling
`update.audiobookshelf_plus_server` stops it being used. Unauthenticated GitHub allows
sixty requests an hour per address, which a daily check will never approach, but
a shared address might: a rate-limited or offline check simply leaves the last
known answer in place.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from aiohttp import ClientError, ClientSession, ClientTimeout

from .const import RELEASE_URL

_LOGGER = logging.getLogger(__name__)

TIMEOUT = ClientTimeout(total=15)


@dataclass(slots=True)
class ReleaseInfo:
    """The newest published Audiobookshelf release."""

    version: str
    url: str
    notes: str


async def async_get_latest_release(session: ClientSession) -> ReleaseInfo | None:
    """Return the latest stable release, or None if GitHub cannot be reached.

    `/releases/latest` already excludes drafts and prereleases, so there is
    nothing to filter. Tags carry a leading `v` that the server's own version
    string does not.
    """
    try:
        async with session.get(
            RELEASE_URL,
            headers={"Accept": "application/vnd.github+json"},
            timeout=TIMEOUT,
        ) as response:
            if response.status != 200:
                _LOGGER.debug(
                    "GitHub returned HTTP %s for the release check", response.status
                )
                return None
            payload = await response.json()
    except (ClientError, TimeoutError, ValueError) as err:
        _LOGGER.debug("Could not check for an Audiobookshelf release: %s", err)
        return None

    tag = str(payload.get("tag_name") or "").lstrip("v")
    if not tag:
        return None
    return ReleaseInfo(
        version=tag,
        url=str(payload.get("html_url") or ""),
        notes=str(payload.get("body") or ""),
    )
