"""Binary sensors for Audiobookshelf."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import (
    AudiobookshelfConfigEntry,
    AudiobookshelfCoordinator,
    LibraryData,
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
    """Set up the Audiobookshelf binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities([AudiobookshelfRealtimeSensor(coordinator)])
    async_setup_dynamic_entities(
        coordinator,
        async_add_entities,
        lambda data: data.libraries,
        lambda library: [AudiobookshelfScanningSensor(coordinator, library)],
    )


class AudiobookshelfRealtimeSensor(AudiobookshelfEntity, BinarySensorEntity):
    """Whether the realtime Socket.IO connection is up.

    Off does not mean the server is unreachable -- the integration falls back to
    polling. It means updates are delayed rather than instant.
    """

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "realtime"

    def __init__(self, coordinator: AudiobookshelfCoordinator) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, "realtime")

    @property
    def is_on(self) -> bool:
        """True while the socket is connected."""
        return self.coordinator.connected


class AudiobookshelfScanningSensor(AudiobookshelfLibraryEntity, BinarySensorEntity):
    """Whether this library is being scanned right now.

    Driven entirely by the socket. A scan that starts while the connection is
    down is invisible, but so is everything else it does, and the item counts
    will still catch up on the next poll.
    """

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_translation_key = "scanning"

    def __init__(
        self, coordinator: AudiobookshelfCoordinator, library: LibraryData
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, library, "scanning")

    @property
    def is_on(self) -> bool:
        """True between task_started and task_finished for this library."""
        return self._library_id in self.coordinator.data.scanning
