"""Contract tests for the DucoPy-backed API adapter."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.ducobox.api import (
    DucoConnectivityBoardApi,
    DucoConnectivityBoardApiError,
)


class FakeDucoPy:
    """Capture API calls and return configured responses."""

    def __init__(self) -> None:
        self.responses: dict[tuple[str, str], Any] = {}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def raw_get(self, endpoint: str, **kwargs: Any) -> Any:
        """Capture a GET and return its configured response."""
        self.calls.append(("GET", endpoint, kwargs))
        return self.responses.get(("GET", endpoint), {})

    def raw_post(self, endpoint: str, **kwargs: Any) -> Any:
        """Capture a POST and return its configured response."""
        self.calls.append(("POST", endpoint, kwargs))
        return self.responses.get(("POST", endpoint), {})

    def raw_patch(self, endpoint: str, **kwargs: Any) -> Any:
        """Capture a PATCH and return its configured response."""
        self.calls.append(("PATCH", endpoint, kwargs))
        return self.responses.get(("PATCH", endpoint), {})


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
        """Run the fake client's synchronous method immediately."""
        return function(*args, **kwargs)

    monkeypatch.setattr("custom_components.ducobox.api.asyncio.to_thread", run_in_thread)
    monkeypatch.setattr(api, "_async_get_client", AsyncMock(return_value=client))
    return api, client


def test_node_mapping_preserves_distinct_sensors_and_zero_end_time(
    api_client: tuple[DucoConnectivityBoardApi, FakeDucoPy],
) -> None:
    """Keep RH and IAQ RH separate, and preserve the API's zero end-time value."""
    api, client = api_client
    client.responses[("GET", "/info/nodes")] = {
        "Nodes": [
            {
                "Node": 3,
                "General": {
                    "Type": {"Val": "VLVRH"},
                    "Parent": {"Val": 1},
                    "Name": {"Val": "Badkamer"},
                },
                "Ventilation": {
                    "State": {"Val": "AUTO"},
                    "TimeStateEnd": {"Val": 0},
                    "FlowLvlTgt": {"Val": 60},
                },
                "Sensor": {"Rh": {"Val": 81}, "IaqRh": {"Val": 24}},
            },
            {
                "Node": 4,
                "General": {
                    "Type": {"Val": "VLVCO2"},
                    "Parent": {"Val": 1},
                    "Name": {"Val": "Toilet"},
                },
                "Ventilation": {"State": {"Val": "AUTO"}},
                "Sensor": {"Co2": {"Val": 532}},
            },
        ]
    }

    nodes = asyncio.run(api.async_get_nodes())

    assert [node.node_type for node in nodes] == ["VLVRH", "VLVCO2"]
    assert nodes[0].rh == 81
    assert nodes[0].iaq_rh == 24
    assert nodes[0].time_state_end == 0
    assert nodes[0].flow_lvl_tgt == 60
    assert nodes[1].co2 == 532
    assert client.calls == [("GET", "/info/nodes", {})]


def test_reads_actions_advertised_for_one_node(
    api_client: tuple[DucoConnectivityBoardApi, FakeDucoPy],
) -> None:
    """Return only the action definitions advertised for the requested node."""
    api, client = api_client
    actions = [
        {
            "Action": "SetVentilationState",
            "ValType": "Enum",
            "Enum": ["AUTO", "MAN1", "MAN2", "MAN3"],
        },
        {"Action": "SetIdentify", "ValType": "Boolean"},
    ]
    client.responses[("GET", "/action/nodes/4")] = {"Node": 4, "Actions": actions}

    result = asyncio.run(api.async_get_action_definitions(node_id=4))

    assert result == actions
    assert client.calls == [("GET", "/action/nodes/4", {})]


def test_posts_ventilation_state_using_ducopy(
    api_client: tuple[DucoConnectivityBoardApi, FakeDucoPy],
) -> None:
    """Send the advertised ventilation action and value to the node endpoint."""
    api, client = api_client
    client.responses[("POST", "/action/nodes/4")] = {"Result": "SUCCESS"}

    succeeded = asyncio.run(api.async_set_ventilation_state(4, "MAN1"))

    assert succeeded
    assert client.calls == [
        (
            "POST",
            "/action/nodes/4",
            {"data": {"Action": "SetVentilationState", "Val": "MAN1"}},
        )
    ]


def test_rejects_unexpected_node_config_response(
    api_client: tuple[DucoConnectivityBoardApi, FakeDucoPy],
) -> None:
    """Raise a useful API error when node configuration is not an object."""
    api, client = api_client
    client.responses[("GET", "/config/nodes/4")] = ["unexpected"]

    with pytest.raises(DucoConnectivityBoardApiError):
        asyncio.run(api.async_get_config(4))


def test_patches_only_requested_nested_config_value(
    api_client: tuple[DucoConnectivityBoardApi, FakeDucoPy],
) -> None:
    """Nest a config value as expected by the public API PATCH endpoint."""
    api, client = api_client

    asyncio.run(api.async_patch_config(["Name"], "Toilet", node_id=4))

    assert client.calls == [
        (
            "PATCH",
            "/config/nodes/4",
            {"data": {"Name": {"Val": "Toilet"}}},
        )
    ]
