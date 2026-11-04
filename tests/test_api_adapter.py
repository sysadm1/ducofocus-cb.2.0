"""Contract tests for the DucoPy-backed API adapter.

All network calls are replaced with a fake DucoPy client; these tests never
contact a Connectivity Board.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from custom_components.ducobox.api import (
    DucoConnectivityBoardApi,
    DucoConnectivityBoardApiError,
)


class FakeDucoPy:
    """Capture adapter calls and return configured responses."""

    def __init__(self) -> None:
        self.responses: dict[tuple[str, str], object] = {}
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def raw_get(self, endpoint: str, **kwargs: object) -> object:
        self.calls.append(("GET", endpoint, kwargs))
        return self.responses.get(("GET", endpoint), {})

    def raw_post(self, endpoint: str, **kwargs: object) -> object:
        self.calls.append(("POST", endpoint, kwargs))
        return self.responses.get(("POST", endpoint), {})

    def raw_patch(self, endpoint: str, **kwargs: object) -> object:
        self.calls.append(("PATCH", endpoint, kwargs))
        return self.responses.get(("PATCH", endpoint), {})

    def close(self) -> None:
        """Mirror the session cleanup method used by the adapter."""


class DucoConnectivityBoardApiTests(unittest.IsolatedAsyncioTestCase):
    """Verify parsing and request contracts without a live board."""

    def setUp(self) -> None:
        self.api = DucoConnectivityBoardApi("192.0.2.1")
        self.client = FakeDucoPy()
        self.api._client = self.client

        async def run_in_thread(function, *args, **kwargs):
            return function(*args, **kwargs)

        thread_patch = patch(
            "custom_components.ducobox.api.asyncio.to_thread",
            new=run_in_thread,
        )
        thread_patch.start()
        self.addCleanup(thread_patch.stop)

    async def test_node_mapping_preserves_distinct_sensors_and_zero_end_time(self):
        self.client.responses[("GET", "/info/nodes")] = {
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

        nodes = await self.api.async_get_nodes()

        self.assertEqual([node.node_type for node in nodes], ["VLVRH", "VLVCO2"])
        self.assertEqual(nodes[0].rh, 81)
        self.assertEqual(nodes[0].iaq_rh, 24)
        self.assertEqual(nodes[0].time_state_end, 0)
        self.assertEqual(nodes[0].flow_lvl_tgt, 60)
        self.assertEqual(nodes[1].co2, 532)
        self.assertEqual(self.client.calls, [("GET", "/info/nodes", {})])

    async def test_reads_actions_advertised_for_one_node(self):
        actions = [
            {
                "Action": "SetVentilationState",
                "ValType": "Enum",
                "Enum": ["AUTO", "MAN1", "MAN2", "MAN3"],
            },
            {"Action": "SetIdentify", "ValType": "Boolean"},
        ]
        self.client.responses[("GET", "/action/nodes/4")] = {
            "Node": 4,
            "Actions": actions,
        }

        result = await self.api.async_get_action_definitions(node_id=4)

        self.assertEqual(result, actions)
        self.assertEqual(self.client.calls, [("GET", "/action/nodes/4", {})])

    async def test_posts_ventilation_state_using_ducopy(self):
        self.client.responses[("POST", "/action/nodes/4")] = {"Result": "SUCCESS"}

        succeeded = await self.api.async_set_ventilation_state(4, "MAN1")

        self.assertTrue(succeeded)
        self.assertEqual(
            self.client.calls,
            [
                (
                    "POST",
                    "/action/nodes/4",
                    {"data": {"Action": "SetVentilationState", "Val": "MAN1"}},
                )
            ],
        )

    async def test_rejects_unexpected_node_config_response(self):
        self.client.responses[("GET", "/config/nodes/4")] = ["unexpected"]

        with self.assertRaises(DucoConnectivityBoardApiError):
            await self.api.async_get_config(4)

    async def test_patches_only_requested_nested_config_value(self):
        await self.api.async_patch_config(["Name"], "Toilet", node_id=4)

        self.assertEqual(
            self.client.calls,
            [
                (
                    "PATCH",
                    "/config/nodes/4",
                    {"data": {"Name": {"Val": "Toilet"}}},
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
