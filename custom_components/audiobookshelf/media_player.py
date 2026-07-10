"""Now-playing media players for Audiobookshelf users.

These are deliberately read-only. Audiobookshelf has no remote-control API for
its playing clients -- there is no way to pause someone's phone from Home
Assistant -- so this entity reports state and never pretends to command it. For
actual playback, use Music Assistant's Audiobookshelf provider.
"""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.media_player import MediaPlayerEntity
from homeassistant.components.media_player.const import (
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import AudiobookshelfConfigEntry, AudiobookshelfCoordinator, UserData
from .entity import (
    AudiobookshelfUserEntity,
    async_setup_dynamic_entities,
    listened_recently,
)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AudiobookshelfConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one media player per user, including users invited later."""
    coordinator = entry.runtime_data
    async_setup_dynamic_entities(
        coordinator,
        async_add_entities,
        lambda data: data.users,
        lambda user: [AudiobookshelfMediaPlayer(coordinator, user)],
    )


class AudiobookshelfMediaPlayer(AudiobookshelfUserEntity, MediaPlayerEntity):
    """What one Audiobookshelf user is listening to."""

    _attr_supported_features = MediaPlayerEntityFeature(0)
    _attr_name = None
    _attr_media_image_remotely_accessible = False

    def __init__(self, coordinator: AudiobookshelfCoordinator, user: UserData) -> None:
        """Initialise the media player."""
        super().__init__(coordinator, user, "media_player")
        # A server can have dozens of accounts, most of them dormant. Create them
        # all so they can be switched on, but only enable the recent listeners.
        self._attr_entity_registry_enabled_default = listened_recently(user)

    @property
    def state(self) -> MediaPlayerState:
        """Playing, paused, or idle.

        Audiobookshelf emits nothing when a client pauses, and never closes the
        session. A live session is therefore one whose position was synced
        recently; a stale open session means paused or abandoned.
        """
        user = self.user
        if user is None or user.session is None:
            return MediaPlayerState.IDLE
        return (
            MediaPlayerState.PLAYING
            if user.session.is_live
            else MediaPlayerState.PAUSED
        )

    @property
    def media_content_type(self) -> MediaType | None:
        """Audiobookshelf serves books and podcasts.

        Home Assistant has no audiobook media type, so books are reported as
        music -- the closest thing that behaves correctly in the UI.
        """
        user = self.user
        if user is None or user.session is None:
            return None
        return MediaType.PODCAST if user.session.is_podcast else MediaType.MUSIC

    @property
    def media_title(self) -> str | None:
        """Title of the book or episode."""
        user = self.user
        return user.session.title if user and user.session else None

    @property
    def media_artist(self) -> str | None:
        """Author or podcast."""
        user = self.user
        return user.session.author if user and user.session else None

    @property
    def media_duration(self) -> int | None:
        """Total length, in seconds."""
        user = self.user
        if user is None or user.session is None or not user.session.duration:
            return None
        return int(user.session.duration)

    @property
    def media_position(self) -> int | None:
        """Position at the time of the last sync.

        Only the owning user's sockets receive position ticks, so an admin never
        sees them for anyone else. Home Assistant extrapolates from here using
        media_position_updated_at while the state is playing.
        """
        user = self.user
        if user is None or user.session is None:
            return None
        return int(user.session.current_time)

    @property
    def media_position_updated_at(self) -> datetime | None:
        """When media_position was last known to be accurate."""
        user = self.user
        if user is None or user.session is None:
            return None
        return user.session.updated_at

    @property
    def media_image_url(self) -> str | None:
        """Cover art.

        Audiobookshelf serves covers without authentication, and
        media_image_remotely_accessible is False, so Home Assistant fetches this
        itself and proxies it to the browser. The Audiobookshelf host is never
        exposed to the frontend.
        """
        user = self.user
        if user is None or user.session is None or not user.session.item_id:
            return None
        return f"{self.coordinator.base_url}/api/items/{user.session.item_id}/cover"

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Which device the session is on."""
        user = self.user
        if user is None or user.session is None:
            return None
        return {"device": user.session.device, "session_id": user.session.session_id}
