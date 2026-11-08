"""Data models for the DucoBox integration."""

from __future__ import annotations

from dataclasses import dataclass

_MAX_SLIDER_STEPS = 30


@dataclass
class DucoBoxInfo:
    """Information about the DucoBox."""

    model: str
    serial_number: str
    mac_address: str


@dataclass(frozen=True)
class DucoBoardStatus:
    """Diagnostic values reported by GET /info."""

    rssi_wifi: int | None = None
    api_writes_remaining: int | None = None
    modbus_writes_remaining: int | None = None
    cloud_registration: bool | None = None
    cloud_allow_remote: bool | None = None

    # Overall health per subsystem, as reported by Diag.SubSystems: "ok",
    # "disable" or "error", lowercased from the board's response. Not every
    # box reports every subsystem (VentCool/SunCtrl require those features).
    ventilation_diag_status: str | None = None
    filter_diag_status: str | None = None
    ventcool_diag_status: str | None = None
    suncontrol_diag_status: str | None = None

    # Only present with a DUCO weather station accessory attached.
    weather_temp_outside: float | None = None
    weather_wind_speed_dmps: float | None = None
    weather_precipitation: bool | None = None
    weather_irradiance_south_klx: float | None = None
    weather_irradiance_east_klx: float | None = None
    weather_irradiance_west_klx: float | None = None


@dataclass(frozen=True)
class DucoNumericSetting:
    """One integer configuration value advertised with a range."""

    path: tuple[str, ...]
    value: int
    minimum: int
    maximum: int
    step: int

    @property
    def key(self) -> str:
        """Return the dotted path used by the configuration API."""
        return ".".join(self.path)

    def prefers_slider(self) -> bool:
        """Use a slider for short ranges and a box for wide ones."""
        return (self.maximum - self.minimum) / self.step <= _MAX_SLIDER_STEPS


@dataclass
class DucoBoxNode:
    """A Duco node."""

    node_id: int
    node_type: str
    parent_node_id: int

    name: str | None = None
    network_type: str | None = None

    state: str | None = None
    time_state_remain: int | None = None
    time_state_end: int | None = None
    mode: str | None = None
    flow_lvl_tgt: int | None = None

    rh: int | None = None
    iaq_rh: int | None = None

    co2: int | None = None
    iaq_co2: int | None = None

    voc: int | None = None
    iaq_voc: int | None = None

    # DucoBox Energy only (heat recovery with air temperature sensors and a
    # filter timer); None on models that don't report these.
    temp_oda: float | None = None
    temp_sup: float | None = None
    temp_eta: float | None = None
    temp_eha: float | None = None
    filter_time_remain: int | None = None
