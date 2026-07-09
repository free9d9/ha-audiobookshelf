"""The Audiobookshelf integration."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .coordinator import AudiobookshelfConfigEntry, AudiobookshelfCoordinator
from .cover_proxy import AudiobookshelfCoverView
from .playback import async_setup_services, async_teardown

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.MEDIA_PLAYER,
    Platform.SENSOR,
]

_VIEW_REGISTERED = f"{DOMAIN}_cover_view"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register actions, which must exist whether or not an entry is loaded."""
    await async_setup_services(hass)
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: AudiobookshelfConfigEntry
) -> bool:
    """Set up Audiobookshelf from a config entry."""
    coordinator = AudiobookshelfCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # The cover view is shared across entries; its path carries the entry id.
    if not hass.data.get(_VIEW_REGISTERED):
        hass.http.register_view(AudiobookshelfCoverView(hass))
        hass.data[_VIEW_REGISTERED] = True

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _async_prune_devices(hass, entry)
    entry.async_on_unload(
        coordinator.async_add_listener(lambda: _async_prune_devices(hass, entry))
    )
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: AudiobookshelfConfigEntry
) -> bool:
    """Unload a config entry, closing any playback sessions we opened."""
    await async_teardown(hass)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


@callback
def _async_prune_devices(hass: HomeAssistant, entry: AudiobookshelfConfigEntry) -> None:
    """Drop devices for libraries deleted or users removed on the server."""
    valid = _valid_identifiers(entry)
    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if not any(ident in valid for ident in device.identifiers):
            registry.async_update_device(
                device.id, remove_config_entry_id=entry.entry_id
            )


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: AudiobookshelfConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow removing a device by hand, but only if the server agrees it is gone."""
    return not any(ident in _valid_identifiers(entry) for ident in device.identifiers)


def _valid_identifiers(entry: AudiobookshelfConfigEntry) -> set[tuple[str, str]]:
    """Device identifiers the server still knows about."""
    coordinator = entry.runtime_data
    data = coordinator.data
    identifiers = {(DOMAIN, entry.entry_id)}
    identifiers |= {
        (DOMAIN, f"{entry.entry_id}_{library_id}") for library_id in data.libraries
    }
    identifiers |= {(DOMAIN, f"{entry.entry_id}_{user_id}") for user_id in data.users}
    return identifiers
