"""The handful of Audiobookshelf endpoints aioaudiobookshelf does not wrap yet.

Everything else goes through the library. These four are missing upstream:

  * ``/api/libraries?include=stats``   -- per-library item/duration/size totals
  * ``/api/sessions/open``             -- currently open playback sessions
  * ``/api/users?include=latestSession`` -- users and when each last listened
  * ``/api/libraries/{id}/scan``       -- trigger a library scan

They are small, stable, and on the list to upstream.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout
from homeassistant.exceptions import ConfigEntryAuthFailed

_LOGGER = logging.getLogger(__name__)

TIMEOUT = ClientTimeout(total=20)


class AudiobookshelfRestError(Exception):
    """A REST call failed."""


class AudiobookshelfRest:
    """Thin authenticated wrapper over the raw Audiobookshelf REST API.

    One instance per identity. The config entry's admin key gets `is_primary`,
    so a 401 from it means the integration itself needs reauthenticating. A
    per-user key that goes bad is a service-level failure, not an entry-level
    one, and must not drag the whole integration into a reauth flow.
    """

    def __init__(
        self,
        session: ClientSession,
        base_url: str,
        api_key: str,
        *,
        is_primary: bool = True,
    ) -> None:
        """Initialise the wrapper."""
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._is_primary = is_primary

    @property
    def base_url(self) -> str:
        """The server's base URL."""
        return self._base_url

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self._base_url}{path}"
        try:
            async with self._session.request(
                method, url, headers=self._headers, timeout=TIMEOUT, **kwargs
            ) as res:
                if res.status in (401, 403):
                    if self._is_primary:
                        raise ConfigEntryAuthFailed(
                            "Audiobookshelf rejected the API key"
                        )
                    raise AudiobookshelfRestError(
                        f"Audiobookshelf rejected this user's API key ({res.status})"
                    )
                if res.status not in (200, 201):
                    raise AudiobookshelfRestError(
                        f"{method} {path} -> HTTP {res.status}"
                    )
                # Audiobookshelf is inconsistent about Content-Type -- some
                # endpoints return JSON labelled text/plain. Parse the body, and
                # fall back to the raw text if it is not JSON.
                body = await res.text()
                if not body:
                    return None
                try:
                    return json.loads(body)
                except ValueError:
                    return body
        except (ClientError, TimeoutError) as err:
            raise AudiobookshelfRestError(f"{method} {path} failed: {err}") from err

    async def async_get_me(self) -> dict[str, Any]:
        """Return the user behind the API key.

        Its ``token`` field is the legacy socket token. Audiobookshelf before
        2.37.0 rejects API keys on the Socket.IO handshake and accepts only this.
        """
        me: dict[str, Any] = await self._request("GET", "/api/me")
        return me

    async def async_authorize(self) -> dict[str, Any]:
        """Return the user and server settings, as the web app gets them on login.

        From 2.36.0 ``serverSettings.timeZone`` names the server host's zone.
        """
        payload = await self._request("POST", "/api/authorize")
        return payload if isinstance(payload, dict) else {}

    async def async_get_libraries_with_stats(self) -> list[dict[str, Any]]:
        """Libraries, each with a `stats` object."""
        payload = await self._request(
            "GET", "/api/libraries", params={"include": "stats"}
        )
        return payload.get("libraries", []) if isinstance(payload, dict) else []

    async def async_get_open_sessions(self) -> list[dict[str, Any]]:
        """Playback sessions Audiobookshelf considers open.

        Careful: Audiobookshelf does not close sessions when a client stops, so
        this list contains sessions that are hours or days stale. Judge liveness
        by `updatedAt`, never by presence in this list.
        """
        payload = await self._request("GET", "/api/sessions/open")
        return payload.get("sessions", []) if isinstance(payload, dict) else []

    async def async_get_users(self) -> list[dict[str, Any]]:
        """Users, each with their `latestSession` if they have ever listened."""
        payload = await self._request(
            "GET", "/api/users", params={"include": "latestSession"}
        )
        return payload.get("users", []) if isinstance(payload, dict) else []

    async def async_scan_library(self, library_id: str) -> None:
        """Trigger a scan of one library."""
        await self._request("POST", f"/api/libraries/{library_id}/scan")

    async def async_get_status(self) -> dict[str, Any]:
        """Server status. Unauthenticated, and outside /api."""
        status = await self._request("GET", "/status")
        return status if isinstance(status, dict) else {}

    async def async_get_issue_count(self, library_id: str) -> int:
        """Count items whose files are missing or invalid.

        `?include=filterdata` also reports this, but returns a hundred kilobytes
        of facet data to do it. The `issues` filter is the same query
        (`isMissing OR isInvalid`) and answers in a couple of hundred bytes.
        """
        payload = await self._request(
            "GET",
            f"/api/libraries/{library_id}/items",
            params={"filter": "issues", "limit": 1, "minified": 1},
        )
        return int(payload.get("total", 0)) if isinstance(payload, dict) else 0

    async def async_get_year_in_review(self, year: int) -> dict[str, Any]:
        """Return the key owner's listening summary for a calendar year.

        `/api/me/...` means the account that owns the key, so this must be called
        with a linked user's key. An admin key answers 200 with zeroes, which is
        the admin's own (empty) year rather than an error.
        """
        payload = await self._request("GET", f"/api/me/stats/year/{year}")
        return payload if isinstance(payload, dict) else {}

    async def async_get_users_online(self) -> list[dict[str, Any]]:
        """Users with a live connection to the server.

        Not the same as listening: this is who has the web UI or an app open.
        """
        payload = await self._request("GET", "/api/users/online")
        return payload.get("usersOnline", []) if isinstance(payload, dict) else []

    async def async_get_user_stats(self, user_id: str) -> dict[str, Any]:
        """Return listening statistics for one user. Admin only.

        `days` maps an ISO date to seconds listened, which is how the calendar
        buckets are computed. `totalTime` is all of history.
        """
        stats = await self._request("GET", f"/api/users/{user_id}/listening-stats")
        return stats if isinstance(stats, dict) else {}

    # -------------------------------------------------- removing progress

    async def async_get_item(self, item_id: str) -> dict[str, Any]:
        """One library item, expanded."""
        item = await self._request("GET", f"/api/items/{item_id}")
        return item if isinstance(item, dict) else {}

    async def async_delete_progress(self, progress_id: str) -> None:
        """Delete one media-progress record belonging to this user."""
        await self._request("DELETE", f"/api/me/progress/{progress_id}")

    # --------------------------------------------------------- API key minting

    async def async_create_api_key(self, user_id: str, name: str) -> dict[str, Any]:
        """Mint an API key that authenticates as another user.

        Admin-only. This is why the integration never has to ask a household for
        their Audiobookshelf passwords: the admin key can issue one key per
        person. Returns {"id": ..., "apiKey": ...}; the secret is only ever
        shown here, so it must be stored now.
        """
        payload = await self._request(
            "POST",
            "/api/api-keys",
            json={"name": name, "userId": user_id, "isActive": True},
        )
        key = payload if isinstance(payload, dict) else {}
        # Newer servers nest the key under "apiKey"; older ones return it flat.
        # Careful: on a flat response `payload["apiKey"]` is the secret string.
        if isinstance(nested := key.get("apiKey"), dict):
            key = nested
        return {"id": key.get("id"), "api_key": key.get("apiKey")}

    async def async_delete_api_key(self, key_id: str) -> None:
        """Revoke a key we previously minted."""
        await self._request("DELETE", f"/api/api-keys/{key_id}")

    # ------------------------------------------------- acting as a linked user

    async def async_items_in_progress(self, limit: int = 1) -> list[dict[str, Any]]:
        """Return what this user is part-way through, most recent first."""
        payload = await self._request(
            "GET", "/api/me/items-in-progress", params={"limit": limit}
        )
        return payload.get("libraryItems", []) if isinstance(payload, dict) else []

    async def async_get_progress(self, item_id: str) -> dict[str, Any]:
        """Return the progress on one item, or an empty dict if there is none."""
        try:
            payload = await self._request("GET", f"/api/me/progress/{item_id}")
        except AudiobookshelfRestError:
            return {}
        return payload if isinstance(payload, dict) else {}

    async def async_play_item(
        self, item_id: str, episode_id: str | None = None
    ) -> dict[str, Any]:
        """Open a playback session and get its audio tracks.

        The tracks' contentUrl needs authentication, but the session also exposes
        /public/session/{id}/track/{index}, which streams unauthenticated and
        honours Range requests. That is the URL to hand a speaker: no token ever
        leaves Home Assistant.
        """
        path = f"/api/items/{item_id}/play"
        if episode_id:
            path += f"/{episode_id}"
        session: dict[str, Any] = await self._request(
            "POST",
            path,
            json={
                "deviceInfo": {
                    "clientName": "Home Assistant",
                    "deviceId": "home-assistant",
                },
                "mediaPlayer": "home-assistant",
                "forceDirectPlay": True,
                "supportedMimeTypes": [
                    "audio/mp4",
                    "audio/mpeg",
                    "audio/flac",
                    "audio/ogg",
                ],
            },
        )
        return session

    async def async_sync_session(
        self,
        session_id: str,
        current_time: float,
        time_listened: float,
        duration: float,
    ) -> None:
        """Push playback position back into Audiobookshelf."""
        await self._request(
            "POST",
            f"/api/session/{session_id}/sync",
            json={
                "currentTime": current_time,
                "timeListened": time_listened,
                "duration": duration,
            },
        )

    async def async_close_session(
        self,
        session_id: str,
        current_time: float,
        time_listened: float,
        duration: float,
    ) -> None:
        """Close a session, syncing a final position."""
        await self._request(
            "POST",
            f"/api/session/{session_id}/close",
            json={
                "currentTime": current_time,
                "timeListened": time_listened,
                "duration": duration,
            },
        )

    async def async_close_session_without_sync(self, session_id: str) -> None:
        """Close a session without touching the listener's saved position.

        Audiobookshelf's closeSession() only writes progress when it is given
        sync data; with none it just saves what the session already had. That is
        what we want whenever we cannot vouch for the position -- a stopped
        player reports none, and sending 0.0 would rewind the book.
        """
        await self._request("POST", f"/api/session/{session_id}/close", json={})
