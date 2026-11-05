"""Duco Connectivity Board API adapter backed by the DucoPy client."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import ClientError
from ducopy import DucoPy
from loguru import logger as _ducopy_logger

from .models import DucoBoxInfo, DucoBoxNode
from .utils import format_box_model_name

_LOGGER = logging.getLogger(__name__)

# DucoPy logs every request at INFO level through loguru, which bypasses Home
# Assistant's logging config and floods the log on every poll. Silence it here.
_ducopy_logger.disable("ducopy")


def _get(data: Any, *keys: str) -> Any:
    """Traverse nested dict keys, returning None if any key is missing."""
    for key in keys:
        if isinstance(data, dict):
            data = data.get(key)
        else:
            return None
    return data


def _get_required(data: Any, *keys: str) -> Any:
    """Traverse nested dict keys, raising if the value is missing."""
    value = _get(data, *keys)
    if value is None:
        raise DucoConnectivityBoardApiError(f"Failed to get {keys[-1]}")
    return value


class DucoConnectivityBoardApiError(ClientError):
    """Raised when the API request fails or returns unexpected data."""


class DucoConnectivityBoardApi:
    """Async Home Assistant adapter around DucoPy's synchronous client."""

    def __init__(self, host: str, session: Any | None = None) -> None:
        """Initialize the adapter. The HA session argument is retained for compatibility."""
        del session
        self._base_url = f"http://{host}"
        self._client: DucoPy | None = None
        self._client_lock = asyncio.Lock()
        # DucoPy shares one requests.Session (and its Api-Key header refresh)
        # across calls. requests.Session is not thread-safe, and the main and
        # options coordinators poll concurrently, so run one call at a time.
        self._call_lock = asyncio.Lock()

    async def _async_get_client(self) -> DucoPy:
        """Create DucoPy lazily, away from Home Assistant's event loop."""
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    try:
                        self._client = await asyncio.to_thread(
                            DucoPy, self._base_url, verify=False
                        )
                    except Exception as err:
                        raise DucoConnectivityBoardApiError(
                            f"Could not initialize DucoPy: {err}"
                        ) from err
        return self._client

    async def _async_call(
        self, method_name: str, endpoint: str, **kwargs: Any
    ) -> Any:
        """Run one blocking DucoPy call in a worker thread."""
        client = await self._async_get_client()
        method = getattr(client, method_name)
        try:
            async with self._call_lock:
                return await asyncio.to_thread(method, endpoint, **kwargs)
        except Exception as err:
            raise DucoConnectivityBoardApiError(
                f"DucoPy {method_name} {endpoint} failed: {err}"
            ) from err

    async def async_close(self) -> None:
        """Close DucoPy's owned requests session."""
        if self._client is not None:
            client, self._client = self._client, None
            await asyncio.to_thread(client.close)

    async def async_get_box_info(self) -> DucoBoxInfo:
        """Get identifying information for the DucoBox."""
        # Some board firmware rejects the `parameter` filter because DucoPy's
        # requests-backed client percent-encodes its commas (`%2C`), which
        # those boards 400 on. Fetch the unfiltered response instead.
        data = await self._async_call("raw_get", "/info")
        if not isinstance(data, dict):
            raise DucoConnectivityBoardApiError("Box info response is not an object")

        board = _get(data, "General", "Board") or {}
        lan = _get(data, "General", "Lan") or {}
        model_name = _get_required(board, "BoxName", "Val")
        serial_number = _get_required(board, "SerialDucoBox", "Val")
        mac_address = _get_required(lan, "Mac", "Val")

        return DucoBoxInfo(
            model=format_box_model_name(model_name),
            serial_number=serial_number,
            mac_address=mac_address,
        )

    async def async_get_nodes(self) -> list[DucoBoxNode]:
        """Fetch all nodes and map their reported information to HA models."""
        data = await self._async_call("raw_get", "/info/nodes")
        if not isinstance(data, dict):
            raise DucoConnectivityBoardApiError("Nodes response is not an object")

        nodes: list[DucoBoxNode] = []
        for node in data.get("Nodes", []):
            node_id = _get_required(node, "Node")
            general = node.get("General", {})
            ventilation = node.get("Ventilation", {})
            sensor = node.get("Sensor", {})
            nodes.append(
                DucoBoxNode(
                    node_id=node_id,
                    node_type=_get_required(general, "Type", "Val"),
                    parent_node_id=_get_required(general, "Parent", "Val"),
                    name=_get(general, "Name", "Val"),
                    network_type=_get(general, "NetworkType", "Val"),
                    state=_get(ventilation, "State", "Val"),
                    time_state_remain=_get(ventilation, "TimeStateRemain", "Val"),
                    time_state_end=_get(ventilation, "TimeStateEnd", "Val"),
                    mode=_get(ventilation, "Mode", "Val"),
                    flow_lvl_tgt=_get(ventilation, "FlowLvlTgt", "Val"),
                    rh=_get(sensor, "Rh", "Val"),
                    iaq_rh=_get(sensor, "IaqRh", "Val"),
                    co2=_get(sensor, "Co2", "Val"),
                    iaq_co2=_get(sensor, "IaqCo2", "Val"),
                )
            )
        return nodes

    async def async_get_ventilation_state_options(self) -> dict[int, list[str]]:
        """Get advertised ventilation state options for all nodes."""
        data = await self._async_call(
            "raw_get",
            "/action/nodes",
            params={"action": "SetVentilationState"},
        )
        if not isinstance(data, dict):
            return {}

        options_by_node: dict[int, list[str]] = {}
        for node in data.get("Nodes", []):
            node_id = node.get("Node")
            if node_id is None:
                continue
            for action in node.get("Actions", []):
                if isinstance(action, dict) and action.get("Action") == "SetVentilationState":
                    options = action.get("Enum")
                    if isinstance(options, list) and options:
                        options_by_node[node_id] = options
                    break
        return options_by_node

    async def async_set_ventilation_state(self, node_id: int, state: str) -> bool:
        """Set a node's ventilation state using the advertised action."""
        result = await self._async_call(
            "raw_post",
            f"/action/nodes/{node_id}",
            data={"Action": "SetVentilationState", "Val": state},
        )
        success = isinstance(result, dict) and result.get("Result") == "SUCCESS"
        if not success:
            _LOGGER.warning("Action SetVentilationState for node %s failed", node_id)
        return success

    async def async_set_identify(self, node_id: int) -> bool:
        """Set identify on a node."""
        result = await self._async_call(
            "raw_post",
            f"/action/nodes/{node_id}",
            data={"Action": "SetIdentify", "Val": True},
        )
        success = isinstance(result, dict) and result.get("Result") == "SUCCESS"
        if not success:
            _LOGGER.warning("Action SetIdentify for node %s failed", node_id)
        return success

    async def async_get_action_definitions(
        self, node_id: int | None = None
    ) -> list[dict[str, Any]]:
        """Get the actions advertised by the box or a specific node."""
        if node_id is None:
            data = await self._async_call("raw_get", "/action")
            return data if isinstance(data, list) else []

        data = await self._async_call("raw_get", f"/action/nodes/{node_id}")
        actions = data.get("Actions", []) if isinstance(data, dict) else []
        return actions if isinstance(actions, list) else []

    async def async_execute_action(
        self, action: str, *, node_id: int | None = None, value: Any = None
    ) -> None:
        """Send one advertised box or node action."""
        endpoint = "/action" if node_id is None else f"/action/nodes/{node_id}"
        payload: dict[str, Any] = {"Action": action}
        if value is not None:
            payload["Val"] = value
        await self._async_call("raw_post", endpoint, data=payload)

    async def async_get_config(self, node_id: int | None = None) -> dict[str, Any]:
        """Get box configuration or configuration for one node."""
        endpoint = "/config" if node_id is None else f"/config/nodes/{node_id}"
        data = await self._async_call("raw_get", endpoint)
        if not isinstance(data, dict):
            raise DucoConnectivityBoardApiError("Configuration response is not an object")
        return data

    async def async_patch_config(
        self, path: list[str], value: Any, *, node_id: int | None = None
    ) -> None:
        """Patch one box or node configuration value."""
        if not path:
            raise DucoConnectivityBoardApiError("Configuration path cannot be empty")

        patch: dict[str, Any] = {"Val": value}
        for key in reversed(path):
            patch = {key: patch}

        endpoint = "/config" if node_id is None else f"/config/nodes/{node_id}"
        await self._async_call("raw_patch", endpoint, data=patch)
