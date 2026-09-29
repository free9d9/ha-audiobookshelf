"""Coordinator for Audiobookshelf: Socket.IO push with a REST fallback poll."""

from __future__ import annotations

import base64
import binascii
import json
import logging
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import aioaudiobookshelf_plus as absapi
from aioaudiobookshelf_plus.client import AdminClient, SocketClient
from aioaudiobookshelf_plus.client.session_configuration import SessionConfiguration
from aioaudiobookshelf_plus.exceptions import LoginError, TokenIsMissingError
from aiohttp import ClientError, ClientSession
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import AudiobookshelfRest, AudiobookshelfRestError
from .const import (
    DOMAIN,
    EVENT_PLAYBACK_STARTED,
    EVENT_PLAYBACK_STOPPED,
    EVENT_SCAN_COMPLETED,
    ISSUE_KEY_EXPIRING,
    KEY_EXPIRY_WARN_DAYS,
    NEW_ITEM_DAYS,
    PARSE_DICT_AUDIOBOOK,
    PARSE_DICT_EBOOK,
    RECENT_LIMIT,
    REFRESH_COOLDOWN_SECONDS,
    RELEASE_CHECK_INTERVAL,
    SCAN_INTERVAL,
    SCAN_INTERVAL_LISTENING,
    SESSION_FRESH_SECONDS,
    STATS_INTERVAL,
    TASK_LIBRARY_SCAN,
)
from .cover_proxy import signed_cover_url
from .release import ReleaseInfo, async_get_latest_release

_LOGGER = logging.getLogger(__name__)

type AudiobookshelfConfigEntry = ConfigEntry["AudiobookshelfCoordinator"]


@dataclass(slots=True)
class ListeningStats:
    """How long someone has listened, in calendar buckets.

    Audiobookshelf gives a `days` map of ISO date to seconds, plus an all-time
    `totalTime`. The buckets are computed from that map against Home Assistant's
    local date, so they line up with the calendar the user is looking at.
    """

    today: float = 0.0
    week: float = 0.0
    month: float = 0.0
    year: float = 0.0
    all_time: float = 0.0
    by_weekday: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class SessionData:
    """One playback session."""

    session_id: str
    item_id: str
    title: str
    author: str
    duration: float
    current_time: float
    updated_at: datetime
    is_podcast: bool
    device: str

    @property
    def is_live(self) -> bool:
        """Whether this session is actually playing right now.

        Audiobookshelf never closes sessions when a client stops, so presence in
        /api/sessions/open means nothing. A playing client syncs every ~15s.
        """
        age = (datetime.now(UTC) - self.updated_at).total_seconds()
        return age <= SESSION_FRESH_SECONDS


@dataclass(slots=True)
class LatestSession:
    """A user's most recently touched playback session.

    Unlike an open session, Audiobookshelf reports this even for downloaded and
    offline playback, which never opens a live session on the server. It is
    therefore the only signal that lets an idle media player still show what
    someone was last listening to, and how far in they are.
    """

    item_id: str
    title: str
    author: str
    duration: float
    current_time: float
    updated_at: datetime


@dataclass(slots=True)
class UserData:
    """One Audiobookshelf user."""

    user_id: str
    username: str
    user_type: str
    session: SessionData | None = None
    last_listened: datetime | None = None
    stats: ListeningStats | None = None
    latest_session: LatestSession | None = None


@dataclass(slots=True)
class LibraryData:
    """One Audiobookshelf library."""

    library_id: str
    name: str
    media_type: str
    is_ebook: bool
    total_items: int = 0
    total_duration: float = 0.0
    total_size: int = 0
    # Items whose files are missing from disk or cannot be read.
    issues: int = 0
    last_scan: datetime | None = None
    recent: list[dict[str, Any]] = field(default_factory=list)
    newest_added: datetime | None = None


@dataclass(slots=True)
class AudiobookshelfData:
    """Everything the entities read."""

    libraries: dict[str, LibraryData] = field(default_factory=dict)
    users: dict[str, UserData] = field(default_factory=dict)
    # Users with a live connection to the server. Being online is not the same as
    # listening: it means an app or the web UI is open.
    users_online: list[str] = field(default_factory=list)
    server_version: str = ""
    latest_release: ReleaseInfo | None = None
    # Library ids currently being scanned, from the generic task events.
    scanning: set[str] = field(default_factory=set)
    connected: bool = False

    @property
    def listening_now(self) -> list[UserData]:
        """Users actually playing something right now."""
        return [u for u in self.users.values() if u.session and u.session.is_live]


class AudiobookshelfCoordinator(DataUpdateCoordinator[AudiobookshelfData]):
    """Push-first coordinator.

    Audiobookshelf accepts an API key on its REST API but rejects it on the
    Socket.IO handshake. GET /api/me, authenticated with the API key, returns a
    user token that the socket does accept -- so the realtime connection is
    bootstrapped from the same single secret the user typed. No password needed.

    What the socket does and does not give us (verified against 2.35.1 source):

      * ``item_added`` / ``items_added`` / ``item_updated`` / ``item_removed``
        -> everyone with access. Drives the recently-added feeds.
      * ``user_stream_update`` -> admins only, and only when a session OPENS or
        CLOSES. Drives media player state instantly.
      * ``user_item_progress_updated`` -> the owning user's sockets only. An
        admin never sees other people's position ticks, so playback position is
        extrapolated by Home Assistant from media_position_updated_at.

    Nothing at all is emitted on pause, which is why we poll faster while
    something is playing.
    """

    def __init__(self, hass: HomeAssistant, entry: AudiobookshelfConfigEntry) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=SCAN_INTERVAL,
            # A library scan fires one socket event per item. Without this, a
            # 500-book scan would queue 500 refreshes.
            request_refresh_debouncer=Debouncer(
                hass, _LOGGER, cooldown=REFRESH_COOLDOWN_SECONDS, immediate=False
            ),
        )
        # DataUpdateCoordinator types `config_entry` as optional; ours never is.
        self.entry: AudiobookshelfConfigEntry = entry
        self.entry_id: str = entry.entry_id
        self.base_url: str = entry.data[CONF_URL].rstrip("/")
        self._api_key: str = entry.data[CONF_API_KEY]
        self._client: AdminClient | None = None
        self._socket: SocketClient | None = None
        self.rest: AudiobookshelfRest | None = None
        self._stats: dict[str, ListeningStats] = {}
        self._stats_fetched: float = 0.0
        self._release: ReleaseInfo | None = None
        self._release_checked: float = 0.0
        self._scanning: set[str] = set()

    @property
    def connected(self) -> bool:
        """Whether the realtime socket is up."""
        return bool(self._socket and self._socket.client.connected)

    # ------------------------------------------------------------------ setup

    async def _async_setup(self) -> None:
        """Create the REST clients and open the realtime socket."""
        session = async_get_clientsession(self.hass)
        self.rest = AudiobookshelfRest(session, self.base_url, self._api_key)

        rest_config = SessionConfiguration(
            session=session,
            url=self.base_url,
            token=self._api_key,
            logger=_LOGGER,  # else the library calls logging.basicConfig(DEBUG)
        )
        try:
            self._client = await absapi.get_admin_client_by_token(
                session_config=rest_config
            )
        except (LoginError, TokenIsMissingError) as err:
            raise ConfigEntryAuthFailed("Audiobookshelf rejected the API key") from err
        except (ClientError, TimeoutError) as err:
            raise UpdateFailed(f"Cannot reach Audiobookshelf: {err}") from err

        self._async_check_key_expiry()
        await self._async_connect_socket(session)

    def _async_check_key_expiry(self) -> None:
        """Raise a repair issue before an expiring API key actually lapses.

        Audiobookshelf keys can be created with an expiry. When one lapses the
        integration simply starts failing auth, which is a miserable way to find
        out. The expiry is in the key's own JWT payload, so no admin call is
        needed to read it.
        """
        expires = _api_key_expiry(self._api_key)
        issue_id = f"{ISSUE_KEY_EXPIRING}_{self.entry_id}"

        if expires is None or expires - datetime.now(UTC) > timedelta(
            days=KEY_EXPIRY_WARN_DAYS
        ):
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return

        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_KEY_EXPIRING,
            translation_placeholders={
                # A human reads this date, so render it in Home Assistant's own
                # timezone. The claim is UTC, and west of Greenwich an expiry in
                # the small hours UTC falls on the previous local day: telling
                # someone their key dies on the 9th when it dies on the 8th
                # invites them to act a day late.
                "expires": dt_util.as_local(expires).strftime("%Y-%m-%d"),
                "url": self.base_url,
            },
        )

    async def _async_connect_socket(self, session: ClientSession) -> None:
        """Open the Socket.IO connection using a token bootstrapped from /api/me."""
        assert self.rest is not None
        try:
            socket_token = (await self.rest.async_get_me()).get("token")
        except AudiobookshelfRestError as err:
            _LOGGER.debug("Could not fetch realtime token: %s", err)
            socket_token = None

        if not socket_token:
            _LOGGER.warning(
                "Could not obtain a realtime token; falling back to polling every %s",
                SCAN_INTERVAL,
            )
            return

        socket_config = SessionConfiguration(
            session=session, url=self.base_url, token=socket_token, logger=_LOGGER
        )
        self._socket = SocketClient(session_config=socket_config)
        self._socket.set_item_callbacks(
            on_item_added=self._on_library_changed,
            on_item_updated=self._on_library_changed,
            on_item_removed=self._on_library_changed,
            on_items_added=self._on_library_changed,
            on_items_updated=self._on_library_changed,
        )
        try:
            await self._socket.init_client()
        finally:
            # These handlers want the raw payload, so they hang directly off the
            # socketio client rather than the library's typed callbacks. They
            # MUST go on after init_client(): it registers its own dispatcher
            # for all three events, and socketio keeps one handler per event, so
            # registering first meant ours were silently replaced and playback
            # starts and library scans never arrived in real time. Handlers
            # outlive reconnects, so this runs once.
            self._socket.client.on("user_stream_update", self._on_stream_update)
            # Library scans surface as generic tasks. There is no scan_start or
            # scan_complete event, despite what the docs' contents page implies.
            self._socket.client.on("task_started", self._on_task_started)
            self._socket.client.on("task_finished", self._on_task_finished)

    async def async_shutdown(self) -> None:
        """Close the socket on unload."""
        if self._socket is not None:
            await self._socket.logout()
            self._socket = None
        await super().async_shutdown()

    # ----------------------------------------------------------- push handlers

    async def _on_library_changed(self, _event: Any) -> None:
        """Any library mutation triggers a refresh, coalesced by the debouncer."""
        await self.async_request_refresh()

    async def _on_stream_update(self, payload: Any) -> None:
        """Handle a session opening or closing, for any user on the server.

        Audiobookshelf makes this harder than it should be. closeSession() emits
        this event *before* removeSession(), so on a close the payload still
        contains the session that is going away -- byte for byte it looks like a
        start. removeSession() itself emits nothing, and `user_session_closed`
        only reaches the session's own owner, never an admin.

        The one thing that distinguishes them is the session id: a start carries
        a session we have never seen, a close carries the one we already hold. So
        we optimistically show a start immediately, and treat a repeat of a known
        session id as "something changed, go ask /api/sessions/open" -- which is
        authoritative about whether the session still exists.
        """
        if not isinstance(payload, dict) or self.data is None:
            return
        user_id = payload.get("id")
        if user_id is None:
            return

        old = self.data.users.get(user_id)
        known_session = old.session.session_id if old and old.session else None
        session = _parse_session(payload.get("session"))

        if session is not None and session.session_id == known_session:
            # A close (or a re-emit for a session we already track). Let the poll
            # settle it rather than trusting a payload that lies about liveness.
            await self.async_request_refresh()
            return

        users = dict(self.data.users)
        if old is not None:
            users[user_id] = replace(old, session=session)
        else:
            users[user_id] = UserData(
                user_id=user_id,
                username=str(payload.get("username") or user_id),
                user_type=str(payload.get("type") or "user"),
                session=session,
            )

        self._fire_session_events(self.data.users, users)
        new = replace(self.data, users=users, connected=self.connected)
        self.update_interval = self._next_interval(new)
        self.async_set_updated_data(new)

    def _fire_session_events(
        self, old: dict[str, UserData], new: dict[str, UserData]
    ) -> None:
        """Fire playback started/stopped for whatever changed.

        Called from both the push handler and the poll, because the socket alone
        can never tell us a session ended.
        """
        for user_id, user in new.items():
            before = old.get(user_id)
            was = before.session if before else None
            now = user.session

            if now is not None and (was is None or was.session_id != now.session_id):
                self.hass.bus.async_fire(
                    EVENT_PLAYBACK_STARTED,
                    {
                        "user": user.username,
                        "user_id": user_id,
                        "title": now.title,
                        "author": now.author,
                        "item_id": now.item_id,
                        "device": now.device,
                    },
                )
            elif now is None and was is not None:
                self.hass.bus.async_fire(
                    EVENT_PLAYBACK_STOPPED,
                    {
                        "user": user.username,
                        "user_id": user_id,
                        "title": was.title,
                        "author": was.author,
                        "item_id": was.item_id,
                    },
                )

    async def _on_task_started(self, payload: Any) -> None:
        """Note that a background task began. We only care about library scans."""
        library_id = _scan_library_id(payload)
        if library_id is None or self.data is None:
            return
        self._scanning.add(library_id)
        self.async_set_updated_data(replace(self.data, scanning=set(self._scanning)))

    async def _on_task_finished(self, payload: Any) -> None:
        """Announce what a finished library scan found."""
        library_id = _scan_library_id(payload)
        if library_id is None or self.data is None:
            return
        self._scanning.discard(library_id)

        data = payload.get("data") or {}
        results = data.get("scanResults") or {}
        self.hass.bus.async_fire(
            EVENT_SCAN_COMPLETED,
            {
                "library": data.get("libraryName", ""),
                "library_id": library_id,
                "failed": bool(payload.get("isFailed")),
                "added": int(results.get("added", 0)),
                "updated": int(results.get("updated", 0)),
                "missing": int(results.get("missing", 0)),
                "elapsed_ms": int(results.get("elapsed", 0)),
                "summary": results.get("text", ""),
            },
        )
        self.async_set_updated_data(replace(self.data, scanning=set(self._scanning)))
        # A scan changes item counts and issue counts, so pick those up too.
        await self.async_request_refresh()

    def _next_interval(self, data: AudiobookshelfData) -> timedelta:
        """Poll faster while anything is playing, so we notice a pause."""
        return SCAN_INTERVAL_LISTENING if data.listening_now else SCAN_INTERVAL

    # ------------------------------------------------------------------- fetch

    async def _async_update_data(self) -> AudiobookshelfData:
        """Fetch libraries, their recently-added shelves, users and sessions."""
        assert self._client is not None
        assert self.rest is not None
        try:
            raw_libraries = await self.rest.async_get_libraries_with_stats()
            raw_users = await self.rest.async_get_users()
            raw_sessions = await self.rest.async_get_open_sessions()
            raw_online = await self.rest.async_get_users_online()
            status = await self.rest.async_get_status()
        except AudiobookshelfRestError as err:
            raise UpdateFailed(str(err)) from err

        await self._async_refresh_stats(raw_users)
        await self._async_refresh_release()

        sessions: dict[str, SessionData] = {}
        for raw in raw_sessions:
            parsed = _parse_session(raw)
            uid = raw.get("userId")
            # Keep the freshest session if a user somehow has several.
            if (
                parsed is not None
                and uid
                and (
                    uid not in sessions or parsed.updated_at > sessions[uid].updated_at
                )
            ):
                sessions[uid] = parsed

        users = {
            u["id"]: UserData(
                user_id=u["id"],
                username=u.get("username", u["id"]),
                user_type=u.get("type", "user"),
                session=sessions.get(u["id"]),
                last_listened=_ms_to_dt(
                    (u.get("latestSession") or {}).get("updatedAt")
                ),
                stats=self._stats.get(u["id"]),
                latest_session=_parse_latest_session(u.get("latestSession")),
            )
            for u in raw_users
            if u.get("id")
        }

        libraries: dict[str, LibraryData] = {}
        for raw in raw_libraries:
            lib_id = raw["id"]
            stats = raw.get("stats") or {}
            recent = await self._async_recent_items(lib_id)
            issues = await self.rest.async_get_issue_count(lib_id)
            libraries[lib_id] = LibraryData(
                library_id=lib_id,
                name=raw.get("name", lib_id),
                media_type=raw.get("mediaType", "book"),
                is_ebook=_looks_like_ebook_library(recent),
                total_items=int(stats.get("totalItems", 0)),
                total_duration=float(stats.get("totalDuration", 0.0)),
                total_size=int(stats.get("totalSize", 0)),
                issues=issues,
                last_scan=_ms_to_dt(raw.get("lastScan")),
                recent=recent,
                newest_added=_newest(recent),
            )

        # The poll is the only reliable signal that a session ended.
        if self.data is not None:
            self._fire_session_events(self.data.users, users)

        data = AudiobookshelfData(
            libraries=libraries,
            users=users,
            users_online=[u.get("username", "") for u in raw_online],
            server_version=str(status.get("serverVersion") or ""),
            latest_release=self._release,
            scanning=set(self._scanning),
            connected=self.connected,
        )
        self.update_interval = self._next_interval(data)
        return data

    async def _async_refresh_stats(self, raw_users: list[dict[str, Any]]) -> None:
        """Refresh listening statistics, on their own slow cadence.

        One REST call per user, so this must not ride the 30-second poll that
        runs while somebody is listening. Statistics only move when a session
        syncs, and a quarter of an hour late is nobody's problem.

        One user's statistics failing is not worth failing the whole refresh
        over: their sensors simply keep the previous value.
        """
        assert self.rest is not None
        now = time.monotonic()
        if self._stats and now - self._stats_fetched < STATS_INTERVAL.total_seconds():
            return

        today = dt_util.now().date()
        for user in raw_users:
            user_id = user.get("id")
            if not user_id:
                continue
            try:
                raw = await self.rest.async_get_user_stats(user_id)
            except AudiobookshelfRestError as err:
                _LOGGER.debug("Stats unavailable for %s: %s", user.get("username"), err)
                continue
            self._stats[user_id] = _parse_stats(raw, today)
        self._stats_fetched = now

    async def _async_refresh_release(self) -> None:
        """Ask GitHub about new releases, at most once a day.

        The only outbound call in the integration. A failure keeps whatever we
        knew before and never fails the refresh.
        """
        now = time.monotonic()
        if (
            self._release is not None
            and now - self._release_checked < RELEASE_CHECK_INTERVAL.total_seconds()
        ):
            return
        session = async_get_clientsession(self.hass)
        if (release := await async_get_latest_release(session)) is not None:
            self._release = release
        self._release_checked = now

    async def _async_recent_items(self, library_id: str) -> list[dict[str, Any]]:
        """Return the library's Recently Added shelf, card-ready.

        This is the same shelf Audiobookshelf shows on its own home page.
        aioaudiobookshelf's get_library_items() exposes no sort/desc parameters
        yet, so the personalized view is both the cleaner and the only public
        route to it.
        """
        assert self._client is not None
        try:
            shelves = await self._client.get_library_personalized_view(
                library_id=library_id, limit=RECENT_LIMIT
            )
        except (ClientError, TimeoutError) as err:
            raise UpdateFailed(f"Cannot reach Audiobookshelf: {err}") from err

        shelf = next(
            (
                s
                for s in shelves
                if str(getattr(s, "id_", "")).endswith("recently-added")
            ),
            None,
        )
        if shelf is None:
            return []
        return [
            self._entry_for(item)
            for item in getattr(shelf, "entities", [])[:RECENT_LIMIT]
        ]

    def _entry_for(self, item: Any) -> dict[str, Any]:
        """Map an ABS library item to one Upcoming-Media-Card style entry."""
        md = item.media.metadata
        added = _ms_to_dt(item.added_at)
        duration = getattr(item.media, "duration", 0) or 0
        genres = getattr(md, "genres", None) or []
        author = getattr(md, "author_name", "") or ""

        return {
            "title": md.title or "",
            "poster": signed_cover_url(
                self.hass, self.entry_id, item.id_, item.updated_at
            ),
            "fanart": "",
            # Upcoming-Media-Card conventions
            "number": author,
            "airdate": added.isoformat() if added else "",
            "aired": added.isoformat() if added else "",
            "release": str(getattr(md, "published_year", "") or ""),
            "studio": getattr(md, "publisher", "") or "",
            "genres": ", ".join(genres[:3]),
            "runtime": int(duration // 60) if duration else 0,
            "flag": _is_new(added),
            "deep_link": f"{self.base_url}/item/{item.id_}",
            # Extras, useful in templates and in our own card
            "author": author,
            "narrator": getattr(md, "narrator_name", "") or "",
            "series": getattr(md, "series_name", "") or "",
            "subtitle": getattr(md, "subtitle", "") or "",
            "media_type": "ebook" if not duration else "audiobook",
            # Deliberately omitted: description. It is long, and every attribute
            # is written to the recorder database on each state change.
        }


def _parse_session(raw: Any) -> SessionData | None:
    """Build a SessionData from an ABS session object, if there is one."""
    if not isinstance(raw, dict) or not raw.get("id"):
        return None
    updated = _ms_to_dt(raw.get("updatedAt"))
    if updated is None:
        return None
    device = raw.get("mediaPlayer") or ""
    info = raw.get("deviceInfo") or {}
    if model := info.get("model"):
        device = f"{info.get('manufacturer', '')} {model}".strip()
    return SessionData(
        session_id=raw["id"],
        item_id=raw.get("libraryItemId", ""),
        title=raw.get("displayTitle") or "",
        author=raw.get("displayAuthor") or "",
        duration=float(raw.get("duration") or 0.0),
        current_time=float(raw.get("currentTime") or 0.0),
        updated_at=updated,
        is_podcast=bool(raw.get("episodeId")),
        device=device,
    )


def _parse_latest_session(raw: Any) -> LatestSession | None:
    """Build a LatestSession from a user's `latestSession`, if it names an item.

    Audiobookshelf reports this for downloaded and offline playback too, so it is
    kept even when no live session is open. Without a library item there is
    nothing to show, so those are treated as absent.
    """
    if not isinstance(raw, dict) or not raw.get("libraryItemId"):
        return None
    updated = _ms_to_dt(raw.get("updatedAt"))
    if updated is None:
        return None
    return LatestSession(
        item_id=str(raw["libraryItemId"]),
        title=raw.get("displayTitle") or "",
        author=raw.get("displayAuthor") or "",
        duration=float(raw.get("duration") or 0.0),
        current_time=float(raw.get("currentTime") or 0.0),
        updated_at=updated,
    )


def _scan_library_id(payload: Any) -> str | None:
    """Return the library a task belongs to, if that task is a library scan.

    Audiobookshelf routes every background job through the same two events, so
    the action has to be checked rather than the event name.
    """
    if not isinstance(payload, dict) or payload.get("action") != TASK_LIBRARY_SCAN:
        return None
    library_id = (payload.get("data") or {}).get("libraryId")
    return str(library_id) if library_id else None


def _parse_stats(raw: dict[str, Any], today: date) -> ListeningStats:
    """Bucket Audiobookshelf's `days` map into calendar periods.

    `days` maps an ISO date to seconds listened. Boundaries are taken from Home
    Assistant's local date, which is what the person reading the dashboard means
    by "today"; the two agree unless the server sits in another timezone.
    """
    days: dict[str, Any] = raw.get("days") or {}
    parsed: list[tuple[date, float]] = []
    for key, seconds in days.items():
        try:
            parsed.append((date.fromisoformat(key), float(seconds)))
        except (TypeError, ValueError):
            continue

    # Monday-based week, matching ISO.
    week_start = today - timedelta(days=today.weekday())
    return ListeningStats(
        today=sum(v for d, v in parsed if d == today),
        week=sum(v for d, v in parsed if d >= week_start),
        month=sum(
            v for d, v in parsed if (d.year, d.month) == (today.year, today.month)
        ),
        year=sum(v for d, v in parsed if d.year == today.year),
        # totalTime is all of history. `days` may not reach far enough back to
        # reproduce it, so it is taken as given rather than summed.
        all_time=float(raw.get("totalTime") or 0.0),
        by_weekday={k: float(v) for k, v in (raw.get("dayOfWeek") or {}).items()},
    )


def _api_key_expiry(api_key: str) -> datetime | None:
    """Read the `exp` claim out of an Audiobookshelf API key.

    The key is a JWT signed by the server. We only read it -- verification is the
    server's job -- so an unparseable key simply means "no known expiry".
    """
    try:
        payload = api_key.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError, binascii.Error):
        return None
    if (exp := claims.get("exp")) is None:
        return None
    try:
        return datetime.fromtimestamp(int(exp), tz=UTC)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _ms_to_dt(value: Any) -> datetime | None:
    """Audiobookshelf reports timestamps as epoch milliseconds."""
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _is_new(added: datetime | None) -> bool:
    """Whether an item is recent enough to earn a NEW badge."""
    if added is None:
        return False
    return datetime.now(UTC) - added < timedelta(days=NEW_ITEM_DAYS)


def _newest(entries: list[dict[str, Any]]) -> datetime | None:
    """Timestamp of the most recently added item, for the sensor state."""
    stamps = [datetime.fromisoformat(e["airdate"]) for e in entries if e.get("airdate")]
    return max(stamps) if stamps else None


def _looks_like_ebook_library(entries: list[dict[str, Any]]) -> bool:
    """ABS types both audiobook and e-book libraries as `book`.

    They are distinguishable only by their contents: e-books carry no audio
    duration. Cover aspect ratio differs (1:1 vs 2:3), so the card needs to know.
    """
    if not entries:
        return False
    return all(e["media_type"] == "ebook" for e in entries)


def parse_dict_for(library: LibraryData) -> dict[str, str]:
    """Header object the Upcoming Media Card spec expects at data[0]."""
    return PARSE_DICT_EBOOK if library.is_ebook else PARSE_DICT_AUDIOBOOK
