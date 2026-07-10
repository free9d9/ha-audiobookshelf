"""The remove_progress action. Destructive, so it is worth pinning down."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.audiobookshelf.api import AudiobookshelfRestError
from custom_components.audiobookshelf.const import (
    CONF_LINKED_USERS,
    DOMAIN,
    SERVICE_REMOVE_PROGRESS,
)
from custom_components.audiobookshelf.progress import _series_names

from .conftest import make_api_key

ITEM_A = "item-a"
ITEM_B = "item-b"
ITEM_C = "item-c"

ME = {
    "mediaProgress": [
        {"id": "prog-a", "libraryItemId": ITEM_A},
        {"id": "prog-b", "libraryItemId": ITEM_B},
        {"id": "prog-c", "libraryItemId": ITEM_C},
    ]
}

ITEMS = {
    ITEM_A: {
        "media": {"metadata": {"title": "Mistborn 1", "series": [{"name": "Mistborn"}]}}
    },
    ITEM_B: {
        "media": {"metadata": {"title": "Mistborn 2", "series": [{"name": "mistborn"}]}}
    },
    ITEM_C: {"media": {"metadata": {"title": "Elantris", "series": []}}},
}


@pytest.fixture
def linked(hass, init_integration):
    hass.config_entries.async_update_entry(
        init_integration,
        options={
            CONF_LINKED_USERS: {
                "u1": {
                    "username": "Alice",
                    "api_key": make_api_key(),
                    "key_id": "key-1",
                    "ha_user_id": "ha-alice",
                }
            }
        },
    )
    return init_integration


@pytest.fixture
def user_rest():
    rest = MagicMock()
    rest.async_get_me = AsyncMock(return_value=ME)
    rest.async_get_item = AsyncMock(side_effect=lambda item_id: ITEMS[item_id])
    rest.async_get_progress = AsyncMock(return_value={"id": "prog-a"})
    rest.async_delete_progress = AsyncMock()
    with patch(
        "custom_components.audiobookshelf.progress.AudiobookshelfRest",
        return_value=rest,
    ):
        yield rest


async def call(hass, **data):
    return await hass.services.async_call(
        DOMAIN,
        SERVICE_REMOVE_PROGRESS,
        data,
        blocking=True,
        return_response=True,
        context=Context(user_id="ha-alice"),
    )


async def test_remove_a_whole_series(hass, linked, user_rest) -> None:
    """Both Mistborn books go; Elantris stays. Case-insensitive."""
    result = await call(hass, series="Mistborn")

    deleted = [c.args[0] for c in user_rest.async_delete_progress.await_args_list]
    assert sorted(deleted) == ["prog-a", "prog-b"]
    assert result["count"] == 2
    assert sorted(result["removed"]) == ["Mistborn 1", "Mistborn 2"]
    assert result["user"] == "Alice"


async def test_remove_a_single_item(hass, linked, user_rest) -> None:
    result = await call(hass, item_id=ITEM_A)
    user_rest.async_delete_progress.assert_awaited_once_with("prog-a")
    assert result["removed"] == ["Mistborn 1"]


async def test_remove_a_single_item_with_no_progress(hass, linked, user_rest) -> None:
    """Nothing to delete is not an error."""
    user_rest.async_get_progress.return_value = {}
    result = await call(hass, item_id=ITEM_A)
    user_rest.async_delete_progress.assert_not_awaited()
    assert result["count"] == 0


async def test_series_that_matches_nothing(hass, linked, user_rest) -> None:
    result = await call(hass, series="Wheel of Time")
    user_rest.async_delete_progress.assert_not_awaited()
    assert result["count"] == 0


async def test_series_and_item_together_is_rejected(hass, linked, user_rest) -> None:
    with pytest.raises(ServiceValidationError, match="either a series or an item_id"):
        await call(hass, series="Mistborn", item_id=ITEM_A)


async def test_neither_series_nor_item_is_rejected(hass, linked, user_rest) -> None:
    with pytest.raises(ServiceValidationError, match="either a series or an item_id"):
        await call(hass)


async def test_no_linked_users(hass, init_integration, user_rest) -> None:
    with pytest.raises(ServiceValidationError, match="No Audiobookshelf users"):
        await call(hass, series="Mistborn")


async def test_unknown_user(hass, linked, user_rest) -> None:
    with pytest.raises(ServiceValidationError, match="not a linked"):
        await call(hass, series="Mistborn", user="Nobody")


async def test_server_error_surfaces(hass, linked, user_rest) -> None:
    user_rest.async_get_me.side_effect = AudiobookshelfRestError("down")
    with pytest.raises(HomeAssistantError, match="Could not reach"):
        await call(hass, series="Mistborn")


async def test_records_without_ids_are_skipped(hass, linked, user_rest) -> None:
    """A malformed progress record must not abort the whole sweep."""
    user_rest.async_get_me.return_value = {
        "mediaProgress": [
            {"id": None, "libraryItemId": ITEM_A},
            {"id": "prog-b", "libraryItemId": None},
            {"id": "prog-a", "libraryItemId": ITEM_A},
        ]
    }
    result = await call(hass, series="Mistborn")
    assert result["count"] == 1


# ------------------------------------------------------------ series matching


def test_series_names_from_an_expanded_item() -> None:
    assert _series_names(ITEMS[ITEM_A]) == ["Mistborn"]


def test_series_names_from_a_minified_item() -> None:
    """Minified items carry a flat `seriesName` with the sequence number in it."""
    item = {"media": {"metadata": {"seriesName": "Wax and Wayne #4"}}}
    assert _series_names(item) == ["Wax and Wayne"]


def test_series_names_handles_multiple_series() -> None:
    item = {"media": {"metadata": {"seriesName": "Cosmere #1, Mistborn #1"}}}
    assert _series_names(item) == ["Cosmere", "Mistborn"]


def test_series_names_when_there_are_none() -> None:
    assert _series_names(ITEMS[ITEM_C]) == []
    assert _series_names({}) == []
