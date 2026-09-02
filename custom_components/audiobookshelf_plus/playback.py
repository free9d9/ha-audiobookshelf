"""Resume an Audiobookshelf book on a Home Assistant media player.

Audiobookshelf cannot be told to play anything -- it has no remote-control API.
What it *can* do is open a playback session and expose the audio over a URL. So
this module hands that URL to a speaker Home Assistant already controls, seeks to
wherever the listener left off, and then does the part nobody else does: syncs
the position back to Audiobookshelf **as the right person**.

That last point is the whole design constraint. Every progress route on the
server is `/me/progress/...`; there is no admin route to write someone else's
progress. So a single shared "Home Assistant" account would silently accumulate
its own bookmarks and never touch yours. Instead, the admin key mints one API key
per linked user (no passwords required), and playback acts as them.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components.media_player.const import (
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    ATTR_MEDIA_POSITION,
    ATTR_MEDIA_POSITION_UPDATED_AT,
    ATTR_MEDIA_SEEK_POSITION,
    SERVICE_PLAY_MEDIA,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.components.media_player.const import (
    DOMAIN as MEDIA_PLAYER_DOMAIN,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    SERVICE_MEDIA_SEEK,
)
from homeassistant.core import Event, HomeAssistant, ServiceCall, State, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import (
    EventStateChangedData,
    async_track_state_change_event,
    async_track_time_interval,
)

from .api import AudiobookshelfRest, AudiobookshelfRestError
from .const import (
    CONF_CONFIG_ENTRY,
    CONF_LINKED_USERS,
    DOMAIN,
    PROGRESS_SYNC_INTERVAL,
    SERVICE_CONTINUE_LISTENING,
)

if TYPE_CHECKING:
    from .coordinator import AudiobookshelfConfigEntry

_LOGGER = logging.getLogger(__name__)

CONF_USER = "user"
CONF_SEEK = "seek"

CONTINUE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional(CONF_USER): cv.string,
        vol.Optional(CONF_CONFIG_ENTRY): cv.string,
        vol.Optional(CONF_SEEK, default=True): cv.boolean,
    }
)

# States that mean the speaker is done with our stream. (STANDBY is deprecated
# and removed in HA 2026.8; OFF and IDLE cover it.)
_FINISHED = (MediaPlayerState.IDLE, MediaPlayerState.OFF)


@dataclass
class ActiveSession:
    """A session this integration opened, and the speaker playing it."""

    session_id: str
    rest: AudiobookshelfRest
    entity_id: str
    entry_id: str
    username: str
    duration: float
    track_offset: float
    content_id: str
    last_synced: float
    # Absolute position in the book, last time the speaker told us one. Never
    # falls back to zero: a stopped player reports no position, and writing zero
    # would rewind the listener's book to the beginning.
    last_position: float | None = None
    # False when we could not seek to the saved position. Playback then starts
    # from the top of the track, and syncing that back would destroy progress.
    sync_enabled: bool = True
    unsub_timer: Any = None
    unsub_state: Any = None


def _sessions(hass: HomeAssistant) -> dict[str, ActiveSession]:
    """Active sessions, keyed by the media player entity id."""
    sessions: dict[str, ActiveSession] = hass.data.setdefault(f"{DOMAIN}_sessions", {})
    return sessions


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register the continue_listening action once, at integration setup."""

    async def _handle(call: ServiceCall) -> None:
        await _async_continue_listening(hass, call)

    hass.services.async_register(
        DOMAIN, SERVICE_CONTINUE_LISTENING, _handle, schema=CONTINUE_SCHEMA
    )


async def async_teardown(hass: HomeAssistant, entry_id: str | None = None) -> None:
    """Close the sessions one config entry opened, on unload.

    The session map is global (it is keyed by speaker, and a speaker plays one
    thing), so unloading one server must not close a session running against
    another. Pass `entry_id` to close only that server's sessions; omit it to
    close everything, which is what a full shutdown wants.
    """
    for entity_id, active in list(_sessions(hass).items()):
        if entry_id is None or active.entry_id == entry_id:
            await _async_end_session(hass, entity_id, closing=True)


# ------------------------------------------------------------------ the action


async def _async_continue_listening(hass: HomeAssistant, call: ServiceCall) -> None:
    """Resume a linked user's current book on a media player."""
    resolved = resolve_target(hass, call)
    entry = resolved.entry
    coordinator = entry.runtime_data
    abs_user = resolved.user
    entity_id = call.data[ATTR_ENTITY_ID]

    target = hass.states.get(entity_id)
    if target is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entity_not_found",
            translation_placeholders={"entity_id": entity_id},
        )
    _validate_target(entity_id, target)

    session = async_get_clientsession(hass)
    rest = AudiobookshelfRest(
        session, coordinator.base_url, abs_user["api_key"], is_primary=False
    )

    try:
        in_progress = await rest.async_items_in_progress(limit=1)
    except AudiobookshelfRestError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"error": str(err)},
        ) from err

    if not in_progress:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="nothing_in_progress",
            translation_placeholders={"user": abs_user["username"]},
        )

    item = in_progress[0]
    item_id = item["id"]
    episode_id = (item.get("recentEpisode") or {}).get("id")

    progress = await rest.async_get_progress(item_id)
    position = float(progress.get("currentTime") or 0.0)

    # If they already finished it, start over rather than resuming past the end.
    if progress.get("isFinished"):
        position = 0.0

    try:
        abs_session = await rest.async_play_item(item_id, episode_id)
    except AudiobookshelfRestError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="session_failed",
            translation_placeholders={"error": str(err)},
        ) from err

    tracks = abs_session.get("audioTracks") or []
    if not tracks:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="no_audio_tracks"
        )

    track = _track_for_position(tracks, position)
    offset = max(0.0, position - float(track.get("startOffset") or 0.0))

    # /public/session/{id}/track/{index} streams unauthenticated and supports
    # Range, so the speaker never sees a token. Note the index is 1-based.
    abs_session_id = abs_session["id"]
    track_index = track["index"]
    url = f"{coordinator.base_url}/public/session/{abs_session_id}/track/{track_index}"

    await _async_end_session(hass, entity_id, closing=True)

    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN,
        SERVICE_PLAY_MEDIA,
        {
            ATTR_ENTITY_ID: entity_id,
            ATTR_MEDIA_CONTENT_ID: url,
            ATTR_MEDIA_CONTENT_TYPE: MediaType.MUSIC,
        },
        blocking=True,
    )

    sync_enabled = True
    needs_seek = call.data[CONF_SEEK] and offset > 1

    if needs_seek:
        # A player's SEEK capability is not knowable until it has media loaded --
        # a Chromecast sitting idle advertises no SEEK, and gains it the moment
        # it starts playing. Checking before play_media silently skipped the
        # seek, and the resulting sync then rewound the listener's book to zero.
        can_seek = await _async_wait_until_seekable(hass, entity_id)
        if can_seek:
            await hass.services.async_call(
                MEDIA_PLAYER_DOMAIN,
                SERVICE_MEDIA_SEEK,
                {ATTR_ENTITY_ID: entity_id, ATTR_MEDIA_SEEK_POSITION: offset},
                blocking=True,
            )
        else:
            # Playing from the top of the track is a recoverable annoyance.
            # Writing that position back to Audiobookshelf is not: it would
            # destroy the listener's place in the book. So we play, and stay
            # read-only for this session.
            sync_enabled = False
            _LOGGER.warning(
                "%s cannot seek, so %s is playing from the start of the track "
                "instead of resuming at %.0fs. Progress will NOT be written back "
                "to Audiobookshelf, to avoid overwriting their saved position",
                entity_id,
                abs_user["username"],
                offset,
            )

    active = ActiveSession(
        session_id=abs_session["id"],
        rest=rest,
        entity_id=entity_id,
        entry_id=entry.entry_id,
        username=abs_user["username"],
        duration=float(abs_session.get("duration") or 0.0),
        track_offset=float(track.get("startOffset") or 0.0),
        content_id=url,
        last_synced=time.monotonic(),
        sync_enabled=sync_enabled,
    )
    _sessions(hass)[entity_id] = active

    active.unsub_timer = async_track_time_interval(
        hass, _make_ticker(hass, entity_id), PROGRESS_SYNC_INTERVAL
    )
    active.unsub_state = async_track_state_change_event(
        hass, [entity_id], _make_watcher(hass, entity_id)
    )

    _LOGGER.info(
        "Resuming %r for %s on %s at %.0fs (%s)",
        item["media"]["metadata"].get("title"),
        abs_user["username"],
        entity_id,
        position,
        entry.title,
    )


# ------------------------------------------------------------ progress sync


def _make_ticker(
    hass: HomeAssistant, entity_id: str
) -> Callable[[datetime], Coroutine[Any, Any, None]]:
    """Periodically push the speaker's position back to Audiobookshelf."""

    async def _tick(_now: datetime) -> None:
        active = _sessions(hass).get(entity_id)
        if active is None:
            return
        state = hass.states.get(entity_id)
        if state is None:
            return

        # Remember the last real position even while paused, so that closing a
        # stopped player -- which reports no position at all -- does not write a
        # zero over the listener's place in the book.
        position = _extrapolated_position(state)
        if position is not None:
            active.last_position = active.track_offset + position

        if state.state != MediaPlayerState.PLAYING or position is None:
            return
        if not active.sync_enabled:
            return

        elapsed = time.monotonic() - active.last_synced
        active.last_synced = time.monotonic()
        assert active.last_position is not None
        try:
            await active.rest.async_sync_session(
                active.session_id, active.last_position, elapsed, active.duration
            )
        except AudiobookshelfRestError as err:
            _LOGGER.debug("Progress sync failed for %s: %s", entity_id, err)

    return _tick


def _make_watcher(
    hass: HomeAssistant, entity_id: str
) -> Callable[[Event[EventStateChangedData]], None]:
    """Close the session as soon as the speaker stops or moves on."""

    @callback
    def _changed(event: Event[EventStateChangedData]) -> None:
        active = _sessions(hass).get(entity_id)
        if active is None:
            return
        new = event.data.get("new_state")
        if new is None:
            hass.async_create_task(_async_end_session(hass, entity_id, closing=True))
            return

        # Capture the position from the *old* state: by the time a player reports
        # `off` it has already forgotten where it was.
        old = event.data.get("old_state")
        if old is not None and (position := _extrapolated_position(old)) is not None:
            active.last_position = active.track_offset + position

        moved_on = new.attributes.get(ATTR_MEDIA_CONTENT_ID) not in (
            active.content_id,
            None,
        )
        if new.state in _FINISHED or moved_on:
            hass.async_create_task(_async_end_session(hass, entity_id, closing=True))

    return _changed


async def _async_end_session(
    hass: HomeAssistant, entity_id: str, *, closing: bool
) -> None:
    """Close an Audiobookshelf session and stop tracking the speaker."""
    active = _sessions(hass).pop(entity_id, None)
    if active is None:
        return
    if active.unsub_timer:
        active.unsub_timer()
    if active.unsub_state:
        active.unsub_state()
    if not closing:
        return

    state = hass.states.get(entity_id)
    if state is not None and (position := _extrapolated_position(state)) is not None:
        active.last_position = active.track_offset + position

    # Close without a position unless we are certain of one. A stopped player
    # reports none, and Audiobookshelf treats a missing syncData as "just save
    # what you already had" -- which is exactly right. Sending 0.0 here would
    # rewind the book.
    final = active.last_position if active.sync_enabled else None
    elapsed = time.monotonic() - active.last_synced
    try:
        if final is None:
            await active.rest.async_close_session_without_sync(active.session_id)
            _LOGGER.debug(
                "Closed Audiobookshelf session for %s without writing a position",
                active.username,
            )
        else:
            await active.rest.async_close_session(
                active.session_id, final, elapsed, active.duration
            )
            _LOGGER.debug(
                "Closed Audiobookshelf session for %s at %.0fs", active.username, final
            )
    except AudiobookshelfRestError as err:
        _LOGGER.debug("Could not close session %s: %s", active.session_id, err)


# ----------------------------------------------------------------- helpers


def _features(state: State) -> MediaPlayerEntityFeature:
    """Supported features of a media player state."""
    return MediaPlayerEntityFeature(
        int(state.attributes.get(ATTR_SUPPORTED_FEATURES, 0))
    )


def _validate_target(entity_id: str, state: State) -> None:
    """Refuse to send an audiobook somewhere it does not belong.

    Plenty of things register as a `media_player` with `device_class: speaker`
    without being anything you would listen to a book on -- doorbell chimes,
    and the talkback speakers on security cameras, which exist to carry a few
    seconds of your voice into the driveway.

    They give themselves away by what they cannot do: PLAY_MEDIA and STOP, but
    no PAUSE. Unlike SEEK, PAUSE is advertised while the player is still idle, so
    it can be checked before a single second of audio is sent anywhere.
    """
    features = _features(state)
    if MediaPlayerEntityFeature.PLAY_MEDIA not in features:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="cannot_play_media",
            translation_placeholders={"entity_id": entity_id},
        )
    if MediaPlayerEntityFeature.PAUSE not in features:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="cannot_pause",
            translation_placeholders={"entity_id": entity_id},
        )


async def _async_wait_until_seekable(
    hass: HomeAssistant, entity_id: str, timeout: float = 25.0
) -> bool:
    """Wait for a player to load media, then report whether it can seek.

    Players do not advertise SEEK until something is playing -- a Chromecast
    sitting idle reports none, and gains it a second after the stream starts. It
    also cannot act on a seek until it has buffered. So we wait for it to reach
    a playing state and then ask.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = hass.states.get(entity_id)
        if state is not None and state.state == MediaPlayerState.PLAYING:
            return MediaPlayerEntityFeature.SEEK in _features(state)
        await asyncio.sleep(0.5)

    state = hass.states.get(entity_id)
    _LOGGER.debug(
        "%s never reached playing within %.0fs (state=%s)",
        entity_id,
        timeout,
        state.state if state else "gone",
    )
    return False


def _extrapolated_position(state: State) -> float | None:
    """Where the speaker is now, allowing for time since it last reported."""
    position = state.attributes.get(ATTR_MEDIA_POSITION)
    if position is None:
        return None
    updated_at = state.attributes.get(ATTR_MEDIA_POSITION_UPDATED_AT)
    if isinstance(updated_at, datetime) and state.state == MediaPlayerState.PLAYING:
        drift = (datetime.now(UTC) - updated_at).total_seconds()
        return float(position) + max(0.0, drift)
    return float(position)


def _track_for_position(
    tracks: list[dict[str, Any]], position: float
) -> dict[str, Any]:
    """Which audio file contains this position.

    Most modern audiobooks are a single m4b, but multi-file rips are common and
    each track carries its own startOffset into the whole book.
    """
    for track in tracks:
        start = float(track.get("startOffset") or 0.0)
        end = start + float(track.get("duration") or 0.0)
        if start <= position < end:
            return track
    first: dict[str, Any] = tracks[0]
    return first


def resolve_user(call: ServiceCall, linked: dict[str, Any]) -> dict[str, Any]:
    """Work out which Audiobookshelf account this call is for.

    Explicit `user:` wins. Otherwise fall back to whoever pressed the button --
    Home Assistant puts their user id on the call context, which is the only
    per-person signal available. Automations have no such context, so a single
    linked user is used when there is exactly one.

    `user:` deliberately overrides the caller's context. Home Assistant's action
    layer has no per-user permissions, so this is no wider than any other action
    a dashboard can call -- but it does mean anyone who can call
    `remove_progress` can name anyone linked, so keep those buttons off shared
    dashboards you would not hand the keys to.
    """
    users: list[dict[str, Any]] = list(linked.values())
    if requested := call.data.get(CONF_USER):
        for user in users:
            if user["username"].casefold() == requested.casefold():
                return user
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="unknown_user",
            translation_placeholders={
                "user": requested,
                "linked": ", ".join(u["username"] for u in users),
            },
        )

    if call.context.user_id:
        for user in users:
            if user.get("ha_user_id") == call.context.user_id:
                return user

    if len(users) == 1:
        return users[0]

    raise ServiceValidationError(
        translation_domain=DOMAIN, translation_key="ambiguous_user"
    )


# ------------------------------------------------------- which server, and who


@dataclass(frozen=True)
class ServiceTarget:
    """The server an action call is for, and the listener on it."""

    entry: AudiobookshelfConfigEntry
    user: dict[str, Any]


def loaded_entries(hass: HomeAssistant) -> list[AudiobookshelfConfigEntry]:
    """Every Audiobookshelf config entry that finished setting up."""
    return [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if getattr(entry, "runtime_data", None) is not None
    ]


def resolve_target(hass: HomeAssistant, call: ServiceCall) -> ServiceTarget:
    """Work out which server *and* which listener an action call means.

    One Audiobookshelf server is the normal case and nothing has to be said. But
    two can be configured (the unique id is host:port), and `remove_progress` is
    destructive and not undoable, so guessing is not acceptable: picking
    "whichever entry loaded first" is how you delete the wrong child's place in
    a book on the wrong server.

    So the two questions are answered together. An explicit `config_entry` picks
    the server outright. Otherwise every loaded server is asked who the call is
    for, and the answer stands only if exactly one of them can name a listener --
    which is what happens with one server, and what usually happens with two,
    since a person is generally linked on one of them. Anything less certain
    raises and asks for `config_entry` rather than acting.
    """
    entries = loaded_entries(hass)
    if not entries:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="not_loaded"
        )

    if requested := call.data.get(CONF_CONFIG_ENTRY):
        entries = [_requested_entry(hass, entries, requested)]

    matches: list[ServiceTarget] = []
    problems: list[ServiceValidationError] = []
    for entry in entries:
        try:
            matches.append(ServiceTarget(entry, _user_on(call, entry)))
        except ServiceValidationError as err:
            problems.append(err)

    if len(matches) == 1:
        return matches[0]

    if matches:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="ambiguous_server",
            translation_placeholders={
                "servers": ", ".join(match.entry.title for match in matches)
            },
        )

    # Nothing matched. With one server in play its own error is the useful one
    # ("no linked users", "unknown user"); with several, none of those is the
    # whole story, so say that plainly instead of picking one at random.
    if len(problems) == 1:
        raise problems[0]
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="no_matching_server",
        translation_placeholders={
            "servers": ", ".join(entry.title for entry in entries)
        },
    )


def _requested_entry(
    hass: HomeAssistant,
    loaded: list[AudiobookshelfConfigEntry],
    entry_id: str,
) -> AudiobookshelfConfigEntry:
    """Return the entry a caller named explicitly, if it is usable."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="unknown_config_entry",
            translation_placeholders={"config_entry": entry_id},
        )
    if entry not in loaded:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_loaded",
            translation_placeholders={"server": entry.title},
        )
    return entry


def _user_on(call: ServiceCall, entry: AudiobookshelfConfigEntry) -> dict[str, Any]:
    """Return the linked user this call means on one particular server."""
    linked = entry.options.get(CONF_LINKED_USERS, {})
    if not linked:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="no_linked_users"
        )
    return resolve_user(call, linked)
