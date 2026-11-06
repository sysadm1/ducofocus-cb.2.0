"""Adapter tests driven by JSON captured from the real Connectivity Board."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.ducobox.api import (
    DucoConnectivityBoardApi,
    DucoConnectivityBoardApiError,
)

FIXTURES = Path(__file__).parent / "fixtures"
ACTION_NODES: dict[str, Any] = json.loads((FIXTURES / "action_nodes.json").read_text())
CONFIG_NODES: dict[str, Any] = json.loads((FIXTURES / "config_nodes.json").read_text())
NODE_IDS = sorted(int(node_id) for node_id in ACTION_NODES)

EXPECTED_STATES = [
    "AUTO", "AUT1", "AUT2", "AUT3", "MAN1", "MAN2", "MAN3", "EMPT",
    "CNT1", "CNT2", "CNT3", "MAN1x2", "MAN2x2", "MAN3x2", "MAN1x3",
    "MAN2x3", "MAN3x3",
]  # fmt: skip


class FakeBox:
    """Stand-in for DucoPy that answers like the captured box."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.post_result: Any = {"Result": "SUCCESS"}
        self.fail_with: Exception | None = None

    def _record(self, method: str, endpoint: str, kwargs: dict[str, Any]) -> None:
        self.calls.append((method, endpoint, kwargs))
        if self.fail_with is not None:
            raise self.fail_with

    def raw_get(self, endpoint: str, **kwargs: Any) -> Any:
        """Serve captured GET responses."""
        self._record("GET", endpoint, kwargs)
        if endpoint == "/action/nodes" and kwargs.get("params"):
            return {"Nodes": [ACTION_NODES[str(n)] for n in NODE_IDS]}
        prefix, _, node_id = endpoint.rpartition("/")
        if prefix == "/action/nodes":
            return ACTION_NODES[node_id]
        if prefix == "/config/nodes":
            return CONFIG_NODES[node_id]
        return {}

    def raw_post(self, endpoint: str, **kwargs: Any) -> Any:
        """Accept POSTs and return the configured result."""
        self._record("POST", endpoint, kwargs)
        return self.post_result

    def raw_patch(self, endpoint: str, **kwargs: Any) -> Any:
        """Accept PATCHes."""
        self._record("PATCH", endpoint, kwargs)
        return {}

    def close(self) -> None:
        """Record session cleanup."""
        self.calls.append(("CLOSE", "", {}))


@pytest.fixture
def box(monkeypatch: pytest.MonkeyPatch) -> tuple[DucoConnectivityBoardApi, FakeBox]:
    """Return the adapter wired to a fake box, running calls inline."""
    api = DucoConnectivityBoardApi("192.0.2.1")
    fake = FakeBox()

    async def run_inline(function: Callable[..., Any], *args: Any, **kw: Any) -> Any:
        return function(*args, **kw)

    monkeypatch.setattr("custom_components.ducobox.api.asyncio.to_thread", run_inline)
    monkeypatch.setattr(api, "_async_get_client", AsyncMock(return_value=fake))
    api._client = fake  # noqa: SLF001 - lets async_close reach the fake
    return api, fake


@pytest.mark.parametrize("node_id", NODE_IDS)
def test_every_node_advertises_full_state_list(
    box: tuple[DucoConnectivityBoardApi, FakeBox], node_id: int
) -> None:
    """Each captured node offers SetVentilationState with all 17 states."""
    api, _ = box
    actions = asyncio.run(api.async_get_action_definitions(node_id=node_id))
    by_name = {action["Action"]: action for action in actions}
    assert by_name["SetVentilationState"]["Enum"] == EXPECTED_STATES
    assert by_name["SetIdentify"]["ValType"] == "Boolean"


def test_state_options_map_all_nodes(
    box: tuple[DucoConnectivityBoardApi, FakeBox],
) -> None:
    """The options coordinator gets a state list for every node."""
    api, fake = box
    options = asyncio.run(api.async_get_ventilation_state_options())
    assert sorted(options) == NODE_IDS
    assert all(states == EXPECTED_STATES for states in options.values())
    assert fake.calls == [
        ("GET", "/action/nodes", {"params": {"action": "SetVentilationState"}})
    ]


@pytest.mark.parametrize(
    ("node_id", "name"),
    [
        (1, "DUCObox Focus"),
        (2, "Keuken"),
        (3, "Badkamer"),
        (4, "Toilet"),
        (5, "Slaapkamer"),
        (6, "bediening toilet"),
        (7, "bediening Badkamer"),
    ],
)
def test_node_config_names(
    box: tuple[DucoConnectivityBoardApi, FakeBox], node_id: int, name: str
) -> None:
    """Node config responses come back unchanged."""
    api, _ = box
    config = asyncio.run(api.async_get_config(node_id))
    assert config["Name"]["Val"] == name


@pytest.mark.parametrize("state", ["AUTO", "MAN3", "MAN2x3", "EMPT"])
def test_set_ventilation_state_payload(
    box: tuple[DucoConnectivityBoardApi, FakeBox], state: str
) -> None:
    """States, including timed ones, are posted verbatim."""
    api, fake = box
    assert asyncio.run(api.async_set_ventilation_state(2, state))
    assert fake.calls == [
        (
            "POST",
            "/action/nodes/2",
            {"data": {"Action": "SetVentilationState", "Val": state}},
        )
    ]


def test_set_ventilation_state_reports_failure(
    box: tuple[DucoConnectivityBoardApi, FakeBox],
) -> None:
    """A non-SUCCESS result returns False instead of raising."""
    api, fake = box
    fake.post_result = {"Result": "FAILED"}
    assert not asyncio.run(api.async_set_ventilation_state(2, "MAN1"))


def test_set_identify_payload(box: tuple[DucoConnectivityBoardApi, FakeBox]) -> None:
    """Identify posts a boolean True."""
    api, fake = box
    assert asyncio.run(api.async_set_identify(5))
    assert fake.calls[-1] == (
        "POST",
        "/action/nodes/5",
        {"data": {"Action": "SetIdentify", "Val": True}},
    )


def test_box_level_action_without_value(
    box: tuple[DucoConnectivityBoardApi, FakeBox],
) -> None:
    """Box actions go to /action and omit Val when none is given."""
    api, fake = box
    asyncio.run(api.async_execute_action("ReconnectWifi"))
    assert fake.calls == [("POST", "/action", {"data": {"Action": "ReconnectWifi"}})]


def test_transport_errors_become_api_errors(
    box: tuple[DucoConnectivityBoardApi, FakeBox],
) -> None:
    """DucoPy/requests exceptions surface as DucoConnectivityBoardApiError."""
    api, fake = box
    fake.fail_with = ConnectionError("box unreachable")
    with pytest.raises(DucoConnectivityBoardApiError, match="box unreachable"):
        asyncio.run(api.async_get_nodes())


def test_close_releases_session_once(
    box: tuple[DucoConnectivityBoardApi, FakeBox],
) -> None:
    """Closing twice only closes the DucoPy session once."""
    api, fake = box
    asyncio.run(api.async_close())
    asyncio.run(api.async_close())
    assert [call for call in fake.calls if call[0] == "CLOSE"] == [("CLOSE", "", {})]


def test_calls_are_serialised(monkeypatch: pytest.MonkeyPatch) -> None:
    """Concurrent coordinator polls never overlap inside DucoPy."""
    api = DucoConnectivityBoardApi("192.0.2.1")
    active = 0
    peak = 0

    class SlowBox(FakeBox):
        def raw_get(self, endpoint: str, **kwargs: Any) -> Any:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            import time  # noqa: PLC0415

            time.sleep(0.05)
            active -= 1
            return super().raw_get(endpoint, **kwargs)

    monkeypatch.setattr(api, "_async_get_client", AsyncMock(return_value=SlowBox()))

    async def poll_concurrently() -> None:
        await asyncio.gather(
            api.async_get_config(2),
            api.async_get_config(3),
            api.async_get_ventilation_state_options(),
        )

    asyncio.run(poll_concurrently())
    assert peak == 1
