#!/usr/bin/env python3
"""
Exercise DucoPy against a real Duco Connectivity Board, safely.

Default mode is read-only: it calls every GET the integration uses and
reports what each node advertises. Nothing on the box changes.

Write mode (--write NODE) changes one node's ventilation state, verifies
the change in /info/nodes, then restores the state it found. You are
asked to confirm first. --identify NODE blinks a node's LED (harmless).

Captured responses can be saved with --save DIR to refresh test fixtures;
MAC and serial numbers are redacted.

Examples:
    python scripts/live_check.py 192.168.107.115
    python scripts/live_check.py 192.168.107.115 --save tests/fixtures/live
    python scripts/live_check.py 192.168.107.115 --identify 2
    python scripts/live_check.py 192.168.107.115 --write 2 --state MAN1

"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from ducopy import DucoPy
from loguru import logger

REDACT_KEYS = {"Mac", "SerialDucoBox", "SerialBoardBox", "SerialBoardComm", "Ip"}


def redact(data: Any) -> Any:
    """Replace identifying values so captures are safe to commit."""
    if isinstance(data, dict):
        return {
            key: {"Val": "REDACTED"}
            if key in REDACT_KEYS and isinstance(value, dict) and "Val" in value
            else redact(value)
            for key, value in data.items()
        }
    if isinstance(data, list):
        return [redact(item) for item in data]
    return data


class Check:
    """Run one call, print the outcome, and keep the response."""

    def __init__(self, box: DucoPy, save_dir: Path | None) -> None:
        self.box = box
        self.save_dir = save_dir
        self.failures = 0

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        label = endpoint + (f"?{params}" if params else "")
        start = time.monotonic()
        try:
            data = self.box.raw_get(endpoint, params=params)
        except Exception as err:  # noqa: BLE001 - report every failure
            self.failures += 1
            print(f"  FAIL  GET {label}: {err}")
            return None
        print(f"  ok    GET {label} ({time.monotonic() - start:.2f}s)")
        if self.save_dir is not None:
            name = endpoint.strip("/").replace("/", "_") or "root"
            if params:
                name += "_" + "_".join(str(v) for v in params.values())
            path = self.save_dir / f"{name}.json"
            path.write_text(json.dumps(redact(data), indent=2) + "\n")
        return data


def node_state(check: Check, node_id: int) -> str | None:
    """Read a node's current ventilation state from /info/nodes."""
    data = check.box.raw_get("/info/nodes")
    for node in data.get("Nodes", []):
        if node.get("Node") == node_id:
            return node.get("Ventilation", {}).get("State", {}).get("Val")
    return None


def read_only(check: Check) -> list[int]:
    """Call every GET endpoint the integration relies on."""
    print("Read-only checks:")
    check.get("/info", {"parameter": "BoxName,SerialDucoBox,Mac"})
    nodes = check.get("/info/nodes") or {}
    check.get("/action/nodes", {"action": "SetVentilationState"})
    check.get("/action")
    check.get("/config")

    node_ids = [node["Node"] for node in nodes.get("Nodes", []) if "Node" in node]
    print(f"\nNodes found: {node_ids}")
    for node_id in node_ids:
        actions = check.get(f"/action/nodes/{node_id}") or {}
        config = check.get(f"/config/nodes/{node_id}") or {}
        name = config.get("Name", {}).get("Val", "?")
        advertised = [a.get("Action") for a in actions.get("Actions", [])]
        settable = [k for k, v in config.items() if isinstance(v, dict) and "Min" in v]
        print(f"        node {node_id} '{name}': actions={advertised}")
        if settable:
            print(f"        node {node_id} settable config: {settable}")
    return node_ids


def confirm(question: str) -> bool:
    """Ask for an explicit yes."""
    return input(f"{question} [y/N] ").strip().lower() in {"y", "yes"}


def write_test(check: Check, node_id: int, state: str, hold: int) -> None:
    """Change one node's state, verify it, then put the old state back."""
    before = node_state(check, node_id)
    print(f"\nWrite test on node {node_id}: current state {before!r} -> {state!r}")
    if not confirm(f"Change node {node_id} to {state} for ~{hold}s and restore?"):
        print("Skipped.")
        return
    try:
        result = check.box.raw_post(
            f"/action/nodes/{node_id}",
            data={"Action": "SetVentilationState", "Val": state},
        )
        print(f"  POST result: {result}")
        time.sleep(hold)
        after = node_state(check, node_id)
        status = "ok  " if after == state else "FAIL"
        if after != state:
            check.failures += 1
        print(f"  {status}  board now reports {after!r}")
    finally:
        restore = before if before and before.startswith(("AUTO", "AUT")) else "AUTO"
        check.box.raw_post(
            f"/action/nodes/{node_id}",
            data={"Action": "SetVentilationState", "Val": restore},
        )
        print(f"  restored node {node_id} to {restore!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("host", help="IP or hostname of the Connectivity Board")
    parser.add_argument("--save", type=Path, help="write redacted JSON here")
    parser.add_argument("--identify", type=int, metavar="NODE")
    parser.add_argument("--write", type=int, metavar="NODE")
    parser.add_argument("--state", default="MAN1", help="state for --write")
    parser.add_argument("--hold", type=int, default=5, help="seconds before check")
    parser.add_argument("--debug", action="store_true", help="show DucoPy logs")
    args = parser.parse_args()

    if not args.debug:
        logger.disable("ducopy")
    if args.save:
        args.save.mkdir(parents=True, exist_ok=True)

    box = DucoPy(f"http://{args.host}", verify=False)
    check = Check(box, args.save)
    try:
        node_ids = read_only(check)
        if args.identify is not None:
            if args.identify not in node_ids:
                sys.exit(f"Node {args.identify} not found")
            result = box.raw_post(
                f"/action/nodes/{args.identify}",
                data={"Action": "SetIdentify", "Val": True},
            )
            print(f"\nIdentify node {args.identify}: {result}")
        if args.write is not None:
            if args.write not in node_ids:
                sys.exit(f"Node {args.write} not found")
            write_test(check, args.write, args.state, args.hold)
    finally:
        box.close()

    print(f"\nDone, {check.failures} failure(s).")
    return 1 if check.failures else 0


if __name__ == "__main__":
    sys.exit(main())
