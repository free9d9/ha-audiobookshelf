"""Binary sensors for Audiobookshelf."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import AudiobookshelfConfigEntry, AudiobookshelfCoordinator
from .entity import AudiobookshelfEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AudiobookshelfConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Audiobookshelf binary sensors."""
    async_add_entities([AudiobookshelfRealtimeSensor(entry.runtime_data)])


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
