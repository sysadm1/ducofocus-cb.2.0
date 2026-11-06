"""Number entities for ranged DUCO configuration values."""

from __future__ import annotations

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DucoBoxConfigEntry
from .const import DUCOBOX_NODE_TYPE_BOX
from .coordinator import DucoBoxCoordinator, DucoBoxOptionsCoordinator
from .entity import DucoBoxEntity
from .models import DucoBoxNode, DucoNumericSetting

BOX_SETTINGS: dict[str, NumberEntityDescription] = {
    "General.Time.TimeZone": NumberEntityDescription(
        key="time_zone",
        translation_key="time_zone",
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.CONFIG,
    ),
    "General.Time.Dst": NumberEntityDescription(
        key="dst",
        translation_key="dst",
        entity_category=EntityCategory.CONFIG,
    ),
    "General.Modbus.Addr": NumberEntityDescription(
        key="modbus_address",
        translation_key="modbus_address",
        entity_category=EntityCategory.CONFIG,
    ),
    "General.Modbus.Offset": NumberEntityDescription(
        key="modbus_register_offset",
        translation_key="modbus_register_offset",
        entity_category=EntityCategory.CONFIG,
    ),
    "General.AutoRebootComm.Period": NumberEntityDescription(
        key="auto_reboot_period",
        translation_key="auto_reboot_period",
        native_unit_of_measurement=UnitOfTime.DAYS,
        entity_category=EntityCategory.CONFIG,
    ),
    "General.AutoRebootComm.Time": NumberEntityDescription(
        key="auto_reboot_time",
        translation_key="auto_reboot_time",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.CONFIG,
    ),
}


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: DucoBoxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up number entities for settings the board advertises with a range."""
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
    entities: list[DucoBoxNumberEntity] = []
    if box is not None:
        entities.extend(
            DucoBoxNumberEntity(
                coordinator,
                options_coordinator,
                box,
                setting,
                config_node_id=None,
                description=BOX_SETTINGS[setting.key],
            )
            for setting in options_coordinator.box_settings.values()
            if setting.key in BOX_SETTINGS
        )

    for node in coordinator.data.values():
        entities.extend(
            DucoBoxNumberEntity(
                coordinator,
                options_coordinator,
                node,
                setting,
                config_node_id=node.node_id,
            )
            for setting in options_coordinator.node_settings.get(
                node.node_id, {}
            ).values()
        )
    async_add_entities(entities)


class DucoBoxNumberEntity(DucoBoxEntity, NumberEntity):
    """Writable integer setting advertised by the Connectivity Board."""

    def __init__(
        self,
        coordinator: DucoBoxCoordinator,
        options_coordinator: DucoBoxOptionsCoordinator,
        node: DucoBoxNode,
        setting: DucoNumericSetting,
        config_node_id: int | None,
        description: NumberEntityDescription | None = None,
    ) -> None:
        """Initialize the number entity from one advertised setting."""
        super().__init__(coordinator, node)
        self._options_coordinator = options_coordinator
        self._setting_key = setting.key
        self._config_node_id = config_node_id
        self.entity_description = description or NumberEntityDescription(
            key=setting.key,
            name=setting.key,
            entity_category=EntityCategory.CONFIG,
        )
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{node.node_id}_{setting.key}"
        )
        self._attr_native_min_value = setting.minimum
        self._attr_native_max_value = setting.maximum
        self._attr_native_step = setting.step
        self._attr_mode = (
            NumberMode.SLIDER if setting.prefers_slider() else NumberMode.BOX
        )

    @property
    def available(self) -> bool:
        """Hide the control when the board stops advertising the setting."""
        return super().available and self._current() is not None

    @property
    def native_value(self) -> float | None:
        """Return the current advertised value."""
        setting = self._current()
        return None if setting is None else setting.value

    def _current(self) -> DucoNumericSetting | None:
        """Return the latest advertised setting for this entity."""
        if self._config_node_id is None:
            return self._options_coordinator.box_settings.get(self._setting_key)
        return self._options_coordinator.node_settings.get(
            self._config_node_id, {}
        ).get(self._setting_key)

    async def async_set_native_value(self, value: float) -> None:
        """Write one ranged setting back to the board."""
        setting = self._current()
        if setting is None:
            return
        await self.coordinator.async_patch_config(
            list(setting.path),
            int(value),
            node_id=self._config_node_id,
        )
        # The written value lives in the hourly options coordinator, not the
        # 30s node coordinator that async_patch_config() already refreshed.
        # Refresh it too so the UI reflects the write immediately.
        await self._options_coordinator.async_request_refresh()
