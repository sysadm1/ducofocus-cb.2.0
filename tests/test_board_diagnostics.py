"""Tests for board diagnostics: status flags and ranged numeric settings."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.ducobox.api import (
    DucoConnectivityBoardApi,
    DucoConnectivityBoardApiError,
    parse_board_status,
    parse_numeric_settings,
)
from custom_components.ducobox.models import DucoBoardStatus, DucoNumericSetting


class FakeDucoPy:
    """Capture API calls and return configured responses."""

    def __init__(self) -> None:
        self.responses: dict[tuple[str, str], Any] = {}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def raw_get(self, endpoint: str, **kwargs: Any) -> Any:
        """Capture a GET and return its configured response."""
        self.calls.append(("GET", endpoint, kwargs))
        return self.responses.get(("GET", endpoint), {})


@pytest.fixture
def api_client(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[DucoConnectivityBoardApi, FakeDucoPy]:
    """Return the API adapter with all DucoPy calls intercepted."""
    api = DucoConnectivityBoardApi("192.0.2.1")
    client = FakeDucoPy()

    async def run_in_thread(
        function: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> Any:
        return function(*args, **kwargs)

    monkeypatch.setattr(
        "custom_components.ducobox.api.asyncio.to_thread", run_in_thread
    )
    monkeypatch.setattr(api, "_async_get_client", AsyncMock(return_value=client))
    return api, client


def test_parse_board_status_reads_all_known_fields() -> None:
    """Every diagnostic field is pulled from its documented /info path."""
    data = {
        "General": {
            "Lan": {"RssiWifi": {"Val": -52}},
            "PublicApi": {"WriteReqCntRemain": {"Val": 199}},
            "Modbus": {"WriteReqCntRemain": {"Val": 100}},
            "Cloud": {
                "RegistrationMode": {"Val": True},
                "AllowRemoteIntervention": {"Val": False},
            },
        }
    }

    status = parse_board_status(data)

    assert status == DucoBoardStatus(
        rssi_wifi=-52,
        api_writes_remaining=199,
        modbus_writes_remaining=100,
        cloud_registration=True,
        cloud_allow_remote=False,
    )


def test_parse_board_status_defaults_missing_fields_to_none() -> None:
    """A response missing the diagnostic block yields an all-None status."""
    assert parse_board_status({"General": {}}) == DucoBoardStatus()


def test_parse_board_status_ignores_non_boolean_cloud_values() -> None:
    """A malformed Cloud flag is treated as missing, not coerced."""
    data = {"General": {"Cloud": {"RegistrationMode": {"Val": "yes"}}}}

    status = parse_board_status(data)

    assert status.cloud_registration is None


def test_parse_board_status_reads_diag_status_using_the_real_component_key() -> None:
    """The real board uses "Component", not the documented "SubSystem" key."""
    data = {"Diag": {"SubSystems": [{"Component": "Ventilation", "Status": "Ok"}]}}

    status = parse_board_status(data)

    assert status.ventilation_diag_status == "ok"


def test_parse_board_status_also_accepts_the_documented_subsystem_key() -> None:
    """Fall back to the OpenAPI-documented key, in case that's ever used."""
    data = {"Diag": {"SubSystems": [{"SubSystem": "Ventilation", "Status": "Error"}]}}

    status = parse_board_status(data)

    assert status.ventilation_diag_status == "error"


def test_parse_board_status_defaults_diag_status_to_none_when_absent() -> None:
    """No Diag block (or no matching entry) yields None, not a crash."""
    assert parse_board_status({}).ventilation_diag_status is None
    assert (
        parse_board_status(
            {"Diag": {"SubSystems": [{"Component": "Other", "Status": "Ok"}]}}
        ).ventilation_diag_status
        is None
    )


def test_parse_board_status_reads_weather_station_fields() -> None:
    """Weather station values are read when a DUCO weather station is attached."""
    data = {
        "WeatherHandler": {
            "WeatherStation": {
                "TempOutside": {"Val": 18},
                "SpeedWind": {"Val": 120},
                "Precip": {"Val": True},
                "IrradSouth": {"Val": 23},
                "IrradEast": {"Val": 50},
                "IrradWest": {"Val": 25},
            }
        }
    }

    status = parse_board_status(data)

    assert status.weather_temp_outside == 18
    assert status.weather_wind_speed_dmps == 120
    assert status.weather_precipitation is True
    assert status.weather_irradiance_south_klx == 23
    assert status.weather_irradiance_east_klx == 50
    assert status.weather_irradiance_west_klx == 25


def test_parse_board_status_defaults_weather_fields_to_none_without_a_station() -> None:
    """Boxes without a weather station report None, not an error."""
    status = parse_board_status({})

    assert status.weather_temp_outside is None
    assert status.weather_wind_speed_dmps is None
    assert status.weather_precipitation is None


def test_parse_board_status_rejects_non_object_response() -> None:
    """A non-dict /info response is a clear API error, not a silent default."""
    with pytest.raises(DucoConnectivityBoardApiError):
        parse_board_status(["unexpected"])


def test_parse_numeric_settings_finds_ranged_integer_values() -> None:
    """A field with Val/Min/Max becomes a setting, keyed by its dotted path."""
    data = {"General": {"Time": {"TimeZone": {"Val": 1, "Min": -12, "Max": 14}}}}

    settings = parse_numeric_settings(data)

    assert settings == {
        "General.Time.TimeZone": DucoNumericSetting(
            path=("General", "Time", "TimeZone"),
            value=1,
            minimum=-12,
            maximum=14,
            step=1,
        )
    }


def test_parse_numeric_settings_uses_advertised_step() -> None:
    """Inc, when present, becomes the setting's step instead of the default 1."""
    data = {"Node": {"FlowLvlTgt": {"Val": 50, "Min": 0, "Max": 100, "Inc": 5}}}

    settings = parse_numeric_settings(data)

    assert settings["Node.FlowLvlTgt"].step == 5


def test_parse_numeric_settings_skips_lan_settings() -> None:
    """LAN values are skipped so a bad write can't disconnect the board."""
    data = {"General": {"Lan": {"Ip": {"Val": 1, "Min": 0, "Max": 255}}}}

    assert parse_numeric_settings(data) == {}


def test_parse_numeric_settings_skips_key_like_fields() -> None:
    """Any field whose path mentions 'key' is skipped, even if it looks ranged."""
    data = {"General": {"ApiKey": {"Val": 1, "Min": 0, "Max": 9}}}

    assert parse_numeric_settings(data) == {}


def test_parse_numeric_settings_skips_fields_without_a_range() -> None:
    """A field with a value but no Min/Max, such as a name, is not a setting."""
    data = {"General": {"Board": {"BoxName": {"Val": "FOCUS"}}}}

    assert parse_numeric_settings(data) == {}


def test_gets_board_status_from_the_unfiltered_info_endpoint(
    api_client: tuple[DucoConnectivityBoardApi, FakeDucoPy],
) -> None:
    """The adapter method fetches /info and parses it into a DucoBoardStatus."""
    api, client = api_client
    client.responses[("GET", "/info")] = {
        "General": {"Lan": {"RssiWifi": {"Val": -60}}}
    }

    status = asyncio.run(api.async_get_board_status())

    assert status.rssi_wifi == -60
    assert client.calls == [("GET", "/info", {})]


def test_gets_numeric_settings_for_the_box(
    api_client: tuple[DucoConnectivityBoardApi, FakeDucoPy],
) -> None:
    """With no node_id, settings are read from the box-level /config."""
    api, client = api_client
    client.responses[("GET", "/config")] = {
        "General": {"Time": {"TimeZone": {"Val": 1, "Min": -12, "Max": 14}}}
    }

    settings = asyncio.run(api.async_get_numeric_settings())

    assert "General.Time.TimeZone" in settings
    assert client.calls == [("GET", "/config", {})]


def test_gets_numeric_settings_for_one_node(
    api_client: tuple[DucoConnectivityBoardApi, FakeDucoPy],
) -> None:
    """With a node_id, settings are read from that node's /config."""
    api, client = api_client
    client.responses[("GET", "/config/nodes/4")] = {
        "FlowLvlTgt": {"Val": 50, "Min": 0, "Max": 100}
    }

    settings = asyncio.run(api.async_get_numeric_settings(node_id=4))

    assert "FlowLvlTgt" in settings
    assert client.calls == [("GET", "/config/nodes/4", {})]
