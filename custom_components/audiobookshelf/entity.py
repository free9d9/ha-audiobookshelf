"""Base entity for Audiobookshelf."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, USER_ENABLE_DAYS
from .coordinator import (
    AudiobookshelfCoordinator,
    AudiobookshelfData,
    LibraryData,
    UserData,
)


@callback
def async_setup_dynamic_entities(
    coordinator: AudiobookshelfCoordinator,
    async_add_entities: AddEntitiesCallback,
    select: Callable[[AudiobookshelfData], Mapping[str, Any]],
    build: Callable[[Any], list[Any]],
) -> None:
    """Add entities for things that appear on the server after setup.

    Libraries get created and users get invited while Home Assistant is running.
    Watching the coordinator means a new library shows up without a restart.
    """
    known: set[str] = set()

    @callback
    def _check_for_new() -> None:
        current = select(coordinator.data)
        added = [key for key in current if key not in known]
        if not added:
            return
        known.update(added)
        entities: list[Entity] = []
        for key in added:
            entities.extend(build(current[key]))
        async_add_entities(entities)

    _check_for_new()
    coordinator.entry.async_on_unload(coordinator.async_add_listener(_check_for_new))


class AudiobookshelfEntity(CoordinatorEntity[AudiobookshelfCoordinator]):
    """An entity attached to the Audiobookshelf server device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AudiobookshelfCoordinator, key: str) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        entry_id = coordinator.entry_id
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            entry_type=DeviceEntryType.SERVICE,
            manufacturer="Audiobookshelf",
            name="Audiobookshelf",
            configuration_url=coordinator.base_url,
        )


class AudiobookshelfLibraryEntity(CoordinatorEntity[AudiobookshelfCoordinator]):
    """An entity attached to one Audiobookshelf library device."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: AudiobookshelfCoordinator, library: LibraryData, key: str
    ) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        entry_id = coordinator.entry_id
        self._library_id = library.library_id
        self._attr_unique_id = f"{entry_id}_{library.library_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_{library.library_id}")},
            entry_type=DeviceEntryType.SERVICE,
            manufacturer="Audiobookshelf",
            model="Library",
            name=library.name,
            via_device=(DOMAIN, entry_id),
            configuration_url=f"{coordinator.base_url}/library/{library.library_id}",
        )

    @property
    def library(self) -> LibraryData | None:
        """The library this entity tracks, or None if it disappeared."""
        return self.coordinator.data.libraries.get(self._library_id)

    @property
    def available(self) -> bool:
        """Unavailable if the library is gone from the server."""
        return super().available and self.library is not None


def listened_recently(user: UserData) -> bool:
    """Whether this account has listened to anything lately.

    Audiobookshelf servers accumulate accounts. Entities for people who stopped
    listening years ago are created but disabled, so they can be switched on
    without a restart if anyone wants them.
    """
    if user.session is not None:
        return True
    if user.last_listened is None:
        return False
    return datetime.now(UTC) - user.last_listened < timedelta(days=USER_ENABLE_DAYS)


class AudiobookshelfUserEntity(CoordinatorEntity[AudiobookshelfCoordinator]):
    """An entity attached to one Audiobookshelf user device."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: AudiobookshelfCoordinator, user: UserData, key: str
    ) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        entry_id = coordinator.entry_id
        self._user_id = user.user_id
        self._attr_unique_id = f"{entry_id}_{user.user_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_{user.user_id}")},
            entry_type=DeviceEntryType.SERVICE,
            manufacturer="Audiobookshelf",
            model="User",
            name=f"Audiobookshelf {user.username}",
            via_device=(DOMAIN, entry_id),
        )

    @property
    def user(self) -> UserData | None:
        """The user this entity tracks, or None if the account was removed."""
        return self.coordinator.data.users.get(self._user_id)

    @property
    def available(self) -> bool:
        """Unavailable if the user is gone from the server."""
        return super().available and self.user is not None
