"""Sensors for Audiobookshelf."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfInformation, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import (
    AudiobookshelfConfigEntry,
    AudiobookshelfCoordinator,
    LibraryData,
    UserData,
    parse_dict_for,
)
from .entity import (
    AudiobookshelfEntity,
    AudiobookshelfLibraryEntity,
    AudiobookshelfUserEntity,
    async_setup_dynamic_entities,
    listened_recently,
)

# Calendar buckets exposed per user. `all_time` is Audiobookshelf's own
# `totalTime`; the rest are summed from its per-day map.
STATS_PERIODS: tuple[str, ...] = ("today", "week", "month", "year", "all_time")

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AudiobookshelfConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Audiobookshelf sensors, including libraries added later."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            AudiobookshelfListeningNowSensor(coordinator),
            AudiobookshelfOpenSessionsSensor(coordinator),
            AudiobookshelfUsersOnlineSensor(coordinator),
            AudiobookshelfUsersSensor(coordinator),
            AudiobookshelfLibrariesSensor(coordinator),
        ]
    )

    def _library_entities(library: LibraryData) -> list[SensorEntity]:
        return [
            AudiobookshelfRecentlyAddedSensor(coordinator, library),
            AudiobookshelfItemsSensor(coordinator, library),
            AudiobookshelfDurationSensor(coordinator, library),
            AudiobookshelfSizeSensor(coordinator, library),
            AudiobookshelfIssuesSensor(coordinator, library),
            AudiobookshelfLastScanSensor(coordinator, library),
        ]

    def _user_entities(user: UserData) -> list[SensorEntity]:
        return [
            AudiobookshelfListeningTimeSensor(coordinator, user, period)
            for period in STATS_PERIODS
        ]

    async_setup_dynamic_entities(
        coordinator,
        async_add_entities,
        lambda data: data.libraries,
        _library_entities,
    )
    async_setup_dynamic_entities(
        coordinator,
        async_add_entities,
        lambda data: data.users,
        _user_entities,
    )


# --------------------------------------------------------------------- server


class AudiobookshelfListeningNowSensor(AudiobookshelfEntity, SensorEntity):
    """How many people are actually listening right now.

    Not the same as Audiobookshelf's open-session count. Audiobookshelf never
    closes a session when a client stops, so /api/sessions/open is full of
    sessions that are hours or days old. This counts only sessions whose
    position was synced recently.
    """

    _attr_translation_key = "listening_now"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: AudiobookshelfCoordinator) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, "listening_now")

    @property
    def native_value(self) -> int:
        """Number of live sessions."""
        return len(self.coordinator.data.listening_now)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Who, and what."""
        return {
            "listeners": [
                {
                    "user": u.username,
                    "title": u.session.title,
                    "author": u.session.author,
                    "device": u.session.device,
                }
                for u in self.coordinator.data.listening_now
                if u.session
            ]
        }


class AudiobookshelfOpenSessionsSensor(AudiobookshelfEntity, SensorEntity):
    """How many users have an open playback session, live or stale."""

    _attr_translation_key = "open_sessions"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AudiobookshelfCoordinator) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, "open_sessions")

    @property
    def native_value(self) -> int:
        """Number of users with a session open."""
        return sum(1 for u in self.coordinator.data.users.values() if u.session)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Names of users with a session open, and whether it is live."""
        return {
            "users": [
                {"user": u.username, "live": u.session.is_live}
                for u in self.coordinator.data.users.values()
                if u.session
            ]
        }


class AudiobookshelfUsersOnlineSensor(AudiobookshelfEntity, SensorEntity):
    """How many users have a live connection to the server.

    Online is not listening. It means an app or the web UI is open, which is
    what /api/users/online reports.
    """

    _attr_translation_key = "users_online"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AudiobookshelfCoordinator) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, "users_online")

    @property
    def native_value(self) -> int:
        """Number of connected users."""
        return len(self.coordinator.data.users_online)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Who is connected."""
        return {"users": self.coordinator.data.users_online}


class AudiobookshelfUsersSensor(AudiobookshelfEntity, SensorEntity):
    """How many accounts exist on the server."""

    _attr_translation_key = "users"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AudiobookshelfCoordinator) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, "users")

    @property
    def native_value(self) -> int:
        """Total accounts."""
        return len(self.coordinator.data.users)


class AudiobookshelfLibrariesSensor(AudiobookshelfEntity, SensorEntity):
    """How many libraries the server holds."""

    _attr_translation_key = "libraries"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AudiobookshelfCoordinator) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, "libraries")

    @property
    def native_value(self) -> int:
        """Library count."""
        return len(self.coordinator.data.libraries)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Their names."""
        return {
            "libraries": [lib.name for lib in self.coordinator.data.libraries.values()]
        }


# ----------------------------------------------------------------------- user


class AudiobookshelfListeningTimeSensor(AudiobookshelfUserEntity, SensorEntity):
    """How long one person has listened over a calendar period.

    Audiobookshelf keeps a per-day map of seconds listened, which is bucketed
    into today, this week, this month and this year against Home Assistant's
    local date. `all_time` is the server's own running total, because the
    per-day map does not necessarily reach back to the beginning.
    """

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_suggested_display_precision = 1

    def __init__(
        self, coordinator: AudiobookshelfCoordinator, user: UserData, period: str
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, user, f"listening_{period}")
        self._period = period
        self._attr_translation_key = f"listening_{period}"
        # Servers accumulate accounts. Five sensors each for a dozen dormant
        # users is noise, so only recent listeners are enabled by default.
        self._attr_entity_registry_enabled_default = listened_recently(user)

    @property
    def native_value(self) -> float | None:
        """Hours listened in this period."""
        user = self.user
        if user is None or user.stats is None:
            return None
        seconds: float = getattr(user.stats, self._period)
        return round(seconds / 3600, 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """All-time carries the by-weekday breakdown; the others do not."""
        user = self.user
        if self._period != "all_time" or user is None or user.stats is None:
            return None
        return {
            "hours_by_weekday": {
                day: round(secs / 3600, 2)
                for day, secs in user.stats.by_weekday.items()
            }
        }


# -------------------------------------------------------------------- library


class AudiobookshelfRecentlyAddedSensor(AudiobookshelfLibraryEntity, SensorEntity):
    """The most recently added item in a library.

    The state is when the newest item landed. The `data` attribute carries the
    feed itself, in Upcoming-Media-Card format, so poster-wall cards can render
    it directly.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "recently_added"

    def __init__(
        self, coordinator: AudiobookshelfCoordinator, library: LibraryData
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, library, "recently_added")

    @property
    def native_value(self) -> datetime | None:
        """When the newest item was added."""
        library = self.library
        return library.newest_added if library else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The recently-added feed.

        data[0] is the Upcoming-Media-Card header object; items follow. Consumers
        that do not need it slice it off.
        """
        if (library := self.library) is None:
            return {}
        return {
            "data": [parse_dict_for(library), *library.recent],
            "library_id": library.library_id,
            "media_type": "ebook" if library.is_ebook else "audiobook",
            "count": len(library.recent),
        }


class AudiobookshelfItemsSensor(AudiobookshelfLibraryEntity, SensorEntity):
    """How many items a library holds."""

    _attr_translation_key = "items"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: AudiobookshelfCoordinator, library: LibraryData
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, library, "items")

    @property
    def native_value(self) -> int | None:
        """Item count."""
        library = self.library
        return library.total_items if library else None


class AudiobookshelfDurationSensor(AudiobookshelfLibraryEntity, SensorEntity):
    """Total playable length of a library."""

    _attr_translation_key = "duration"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_suggested_display_precision = 0

    def __init__(
        self, coordinator: AudiobookshelfCoordinator, library: LibraryData
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, library, "duration")

    @property
    def native_value(self) -> float | None:
        """Hours of audio."""
        library = self.library
        return round(library.total_duration / 3600, 2) if library else None


class AudiobookshelfSizeSensor(AudiobookshelfLibraryEntity, SensorEntity):
    """Disk space a library occupies."""

    _attr_translation_key = "size"
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_native_unit_of_measurement = UnitOfInformation.GIGABYTES
    _attr_suggested_display_precision = 1
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: AudiobookshelfCoordinator, library: LibraryData
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, library, "size")

    @property
    def native_value(self) -> float | None:
        """Gigabytes on disk."""
        library = self.library
        return round(library.total_size / 1_000_000_000, 2) if library else None


class AudiobookshelfIssuesSensor(AudiobookshelfLibraryEntity, SensorEntity):
    """Items in this library whose files are missing or unreadable.

    Anything above zero means a book that Audiobookshelf still lists but can no
    longer play. A rename or an unmounted share moves this off zero, which is a
    far better alarm than discovering it at bedtime.
    """

    _attr_translation_key = "issues"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: AudiobookshelfCoordinator, library: LibraryData
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, library, "issues")

    @property
    def native_value(self) -> int | None:
        """Count of missing or invalid items."""
        library = self.library
        return library.issues if library else None


class AudiobookshelfLastScanSensor(AudiobookshelfLibraryEntity, SensorEntity):
    """When this library was last scanned."""

    _attr_translation_key = "last_scan"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: AudiobookshelfCoordinator, library: LibraryData
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, library, "last_scan")

    @property
    def native_value(self) -> datetime | None:
        """Timestamp of the last scan, or None if it has never been scanned."""
        library = self.library
        return library.last_scan if library else None
