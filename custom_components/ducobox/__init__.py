"""The DucoBox integration."""

from __future__ import annotations

from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.ducobox.const import PLATFORMS

from .api import DucoConnectivityBoardApi
from .coordinator import (
    DucoBoxConfigEntry,
    DucoBoxCoordinator,
    DucoBoxOptionsCoordinator,
    DucoBoxRuntimeData,
)
from .services import async_setup_services


async def async_setup(hass: HomeAssistant, config: dict) -> bool:  # noqa: ARG001
    """Set up DucoBox services."""
    await async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: DucoBoxConfigEntry) -> bool:
    """Set up DucoBox from a config entry."""
    api = DucoConnectivityBoardApi(entry.data[CONF_HOST])
    try:
        options_coordinator = DucoBoxOptionsCoordinator(hass, entry, api)
        await options_coordinator.async_config_entry_first_refresh()

        coordinator = DucoBoxCoordinator(hass, entry, api, options_coordinator)
        await coordinator.async_setup()
        await coordinator.async_config_entry_first_refresh()

        entry.runtime_data = DucoBoxRuntimeData(
            coordinator=coordinator,
            options_coordinator=options_coordinator,
        )

        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except UpdateFailed as err:
        await api.async_close()
        raise ConfigEntryNotReady(err) from err
    except Exception:
        await api.async_close()
        raise

    return True


async def async_unload_entry(hass: HomeAssistant, entry: DucoBoxConfigEntry) -> bool:
    """Unload a config entry and release its DucoPy session."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.coordinator.api.async_close()
    return unload_ok
