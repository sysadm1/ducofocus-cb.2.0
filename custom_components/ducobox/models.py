"""Data models for the DucoBox integration."""

from __future__ import annotations

from dataclasses import dataclass


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
        return (self.maximum - self.minimum) / self.step <= 30


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
