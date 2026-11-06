"""Binary sensors for board status flags."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DucoBoxConfigEntry
from .const import DUCOBOX_NODE_TYPE_BOX
from .coordinator import DucoBoxCoordinator, DucoBoxOptionsCoordinator
from .entity import DucoBoxEntity
from .models import DucoBoardStatus, DucoBoxNode


@dataclass(frozen=True, kw_only=True)
class DucoBoxBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes a board status flag."""

    value_fn: Callable[[DucoBoardStatus], bool | None]


BOARD_BINARY_SENSORS: tuple[DucoBoxBinarySensorEntityDescription, ...] = (
    DucoBoxBinarySensorEntityDescription(
        key="cloud_registration",
        translation_key="cloud_registration",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda status: status.cloud_registration,
    ),
    DucoBoxBinarySensorEntityDescription(
        key="cloud_allow_remote",
        translation_key="cloud_allow_remote",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda status: status.cloud_allow_remote,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: DucoBoxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up board status flags on the box device."""
    coordinator = entry.runtime_data.coordinator
    options_coordinator = entry.runtime_data.options_coordinator
    box = next(
        (
            node
            for node in coordinator.data.values()
            if node.node_type == DUCOBOX_NODE_TYPE_BOX
        ),
        None,
    )
    if box is None or options_coordinator.board_status is None:
        return
    async_add_entities(
        DucoBoxBinarySensorEntity(coordinator, options_coordinator, box, description)
        for description in BOARD_BINARY_SENSORS
    )


class DucoBoxBinarySensorEntity(DucoBoxEntity, BinarySensorEntity):
    """Board status flag reported by GET /info."""

    entity_description: DucoBoxBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: DucoBoxCoordinator,
        options_coordinator: DucoBoxOptionsCoordinator,
        node: DucoBoxNode,
        description: DucoBoxBinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, node)
        self._options_coordinator = options_coordinator
        self.entity_description = description
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{node.node_id}_{description.key}"
        )

    @property
    def is_on(self) -> bool | None:
        """Return the current flag."""
        status = self._options_coordinator.board_status
        if status is None:
            return None
        return self.entity_description.value_fn(status)
