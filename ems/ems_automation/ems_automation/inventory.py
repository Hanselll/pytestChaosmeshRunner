from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .settings import get_inventory_path


def load_inventory(path: Path | None = None) -> dict[str, Any]:
    target = path or get_inventory_path()
    if not target.exists():
        return {}
    return json.loads(target.read_text(encoding="utf-8"))


def resolve_network_element_spec(task: dict[str, Any]) -> dict[str, Any]:
    env_value = os.getenv("EMS_NETWORK_ELEMENT", "").strip()
    if env_value:
        return {"match_texts": [env_value], "display_name": env_value, "ip": env_value}

    if isinstance(task.get("network_element"), str) and task["network_element"].strip():
        value = task["network_element"].strip()
        return {"match_texts": [value], "display_name": value, "ip": value}

    ref = str(task.get("network_element_ref", "")).strip()
    if not ref:
        raise RuntimeError("task.network_element or task.network_element_ref is required.")

    inventory = load_inventory()
    nodes = inventory.get("network_elements", {}) if isinstance(inventory, dict) else {}
    spec = nodes.get(ref)
    if not isinstance(spec, dict):
        raise RuntimeError(f"network element ref not found in inventory: {ref}")

    match_texts: list[str] = []
    for key in ("display_name", "ip", "alias", "match_text"):
        value = spec.get(key)
        if isinstance(value, str) and value.strip():
            match_texts.append(value.strip())
    extra = spec.get("match_texts", [])
    if isinstance(extra, list):
        match_texts.extend(str(item).strip() for item in extra if str(item).strip())
    if not match_texts:
        raise RuntimeError(f"inventory network element spec has no usable match text: {ref}")

    normalized = dict(spec)
    normalized["ref"] = ref
    normalized["match_texts"] = list(dict.fromkeys(match_texts))
    normalized["display_name"] = normalized.get("display_name") or normalized["match_texts"][0]
    return normalized
