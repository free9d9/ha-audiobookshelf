"""Tells you when a newer Audiobookshelf has been released.

There is deliberately no install button. Audiobookshelf runs as a container that
you own: pulling a new image, migrating its database and restarting it are your
call and your rollback plan, not something an integration should do behind your
back. This entity reports, and links you to the release notes.
"""

from __future__ import annotations

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
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
    """Set up the server update entity."""
    async_add_entities([AudiobookshelfUpdate(entry.runtime_data)])


class AudiobookshelfUpdate(AudiobookshelfEntity, UpdateEntity):
    """Installed Audiobookshelf version against the newest published release."""

    _attr_supported_features = UpdateEntityFeature.RELEASE_NOTES
    _attr_translation_key = "server"

    def __init__(self, coordinator: AudiobookshelfCoordinator) -> None:
        """Initialise the entity."""
        super().__init__(coordinator, "update")

    @property
    def installed_version(self) -> str | None:
        """The version the server reports about itself."""
        return self.coordinator.data.server_version or None

    @property
    def latest_version(self) -> str | None:
        """The newest release on GitHub.

        Until the daily check succeeds once there is nothing to compare against.
        Reporting the installed version keeps the entity from claiming an update
        is available when we simply do not know yet.
        """
        if (release := self.coordinator.data.latest_release) is None:
            return self.installed_version
        return release.version

    @property
    def release_url(self) -> str | None:
        """Link to the release on GitHub."""
        if (release := self.coordinator.data.latest_release) is None:
            return None
        return release.url or None

    async def async_release_notes(self) -> str | None:
        """Return the full changelog.

        `release_summary` is capped at 255 characters, which an Audiobookshelf
        release blows through in its first section.
        """
        if (release := self.coordinator.data.latest_release) is None:
            return None
        return release.notes or None
