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
    parse_dict_for,
)
from .entity import (
    AudiobookshelfEntity,
    AudiobookshelfLibraryEntity,
    async_setup_dynamic_entities,
)

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
            AudiobookshelfUsersOnlineSensor(coordinator),
        ]
    )

    def _library_entities(library: LibraryData) -> list[SensorEntity]:
        return [
            AudiobookshelfRecentlyAddedSensor(coordinator, library),
            AudiobookshelfItemsSensor(coordinator, library),
            AudiobookshelfDurationSensor(coordinator, library),
            AudiobookshelfSizeSensor(coordinator, library),
        ]

    async_setup_dynamic_entities(
        coordinator,
        async_add_entities,
        lambda data: data.libraries,
        _library_entities,
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


class AudiobookshelfUsersOnlineSensor(AudiobookshelfEntity, SensorEntity):
    """How many users have an open playback session, live or stale."""

    _attr_translation_key = "users_online"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AudiobookshelfCoordinator) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, "users_online")

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
