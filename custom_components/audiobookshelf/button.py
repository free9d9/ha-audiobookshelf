"""Buttons for Audiobookshelf."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import AudiobookshelfRestError
from .const import DOMAIN
from .coordinator import (
    AudiobookshelfConfigEntry,
    AudiobookshelfCoordinator,
    LibraryData,
)
from .entity import AudiobookshelfLibraryEntity, async_setup_dynamic_entities

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AudiobookshelfConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a scan button per library, including libraries added later."""
    coordinator = entry.runtime_data
    async_setup_dynamic_entities(
        coordinator,
        async_add_entities,
        lambda data: data.libraries,
        lambda library: [AudiobookshelfScanButton(coordinator, library)],
    )


class AudiobookshelfScanButton(AudiobookshelfLibraryEntity, ButtonEntity):
    """Trigger a scan of one library."""

    _attr_translation_key = "scan_library"

    def __init__(
        self, coordinator: AudiobookshelfCoordinator, library: LibraryData
    ) -> None:
        """Initialise the button."""
        super().__init__(coordinator, library, "scan_library")

    async def async_press(self) -> None:
        """Ask Audiobookshelf to rescan this library.

        Any items the scan turns up arrive over the socket as item_added events,
        which the coordinator coalesces into a single refresh.
        """
        assert self.coordinator.rest is not None
        try:
            await self.coordinator.rest.async_scan_library(self._library_id)
        except AudiobookshelfRestError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="scan_failed",
                translation_placeholders={"error": str(err)},
            ) from err
