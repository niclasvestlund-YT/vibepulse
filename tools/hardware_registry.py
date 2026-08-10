from dataclasses import dataclass
from pathlib import Path

import yaml

STATE_VALUES = {"yes", "no", "unknown", "not_applicable"}
STATE_KEYS = {
    "soc_capable", "board_wired", "bsp_support",
    "firmware_enabled", "unit_verified",
}
CONFIDENCE_VALUES = {
    "measured", "schematic", "source_inspected",
    "vendor_claimed", "unverified",
}
SECRET_FIELD_PARTS = {"secret", "password", "pass", "token", "ssid", "key"}


class RegistryError(ValueError):
    pass


@dataclass(frozen=True)
class HardwareRegistry:
    sources: dict
    capabilities: dict
    units: dict


def _read(path):
    value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise RegistryError(f"{path}: schema_version must be 1")
    return value


def _unique(items, label):
    result = {}
    for item in items:
        item_id = item.get("id") if isinstance(item, dict) else None
        if not item_id:
            raise RegistryError(f"{label} entry has no id")
        if item_id in result:
            raise RegistryError(f"duplicate {label} {item_id}")
        result[item_id] = item
    return result


def resolve_claim(evidence, sources):
    ordered = sorted(evidence, key=lambda item: sources[item["source"]]["rank"])
    if not ordered:
        raise RegistryError("claim has no evidence")
    winner = ordered[0]
    conflicts = [item["source"] for item in ordered[1:]
                 if item["value"] != winner["value"]]
    return winner["value"], conflicts


def load_registry(root):
    root = Path(root)
    source_doc = _read(root / "hardware-sources.yaml")
    capability_doc = _read(root / "hardware-capabilities.yaml")
    unit_doc = _read(root / "device-units.yaml")
    sources = _unique(source_doc.get("sources", []), "source")
    units = _unique(unit_doc.get("units", []), "unit")
    raw_capabilities = capability_doc.get("capabilities", [])
    if isinstance(raw_capabilities, dict):
        raw_capabilities = [raw_capabilities]
    capabilities = _unique(raw_capabilities, "capability")

    for source_id, source in sources.items():
        for key in ("kind", "rank", "title", "publisher", "locator",
                    "revision", "accessed"):
            if source.get(key) in (None, ""):
                raise RegistryError(f"source {source_id}: missing {key}")

    required_unit_keys = {
        "friendly_name", "board", "sku_evidence", "board_revision",
        "enclosure", "speaker", "battery", "microsd", "antenna",
        "installed_firmware", "last_physical_verification", "secrets",
    }
    for unit_id, unit in units.items():
        missing = required_unit_keys - set(unit)
        if missing:
            raise RegistryError(f"unit {unit_id}: missing {sorted(missing)}")
        if unit["secrets"] is not False:
            raise RegistryError(f"unit {unit_id}: secrets must be false")
        for key in unit:
            if key != "secrets" and any(part in key.lower()
                                        for part in SECRET_FIELD_PARTS):
                raise RegistryError(f"unit {unit_id}: secret field {key}")

    for capability_id, capability in capabilities.items():
        states = capability.get("states")
        if not isinstance(states, dict) or set(states) != STATE_KEYS:
            raise RegistryError(f"{capability_id}: states must be {sorted(STATE_KEYS)}")
        for key, value in states.items():
            if value not in STATE_VALUES:
                raise RegistryError(f"{capability_id}: invalid {key}={value}")
        if capability.get("confidence") not in CONFIDENCE_VALUES:
            raise RegistryError(f"{capability_id}: invalid confidence")
        for source_id in capability.get("sources", []):
            if source_id not in sources:
                raise RegistryError(f"{capability_id}: unknown source {source_id}")
        for finding in capability.get("evidence", []):
            field = finding.get("field")
            source_id = finding.get("source")
            if field not in STATE_KEYS or source_id not in sources:
                raise RegistryError(f"{capability_id}: invalid evidence")
        for field in STATE_KEYS:
            evidence = [item for item in capability.get("evidence", [])
                        if item.get("field") == field]
            if states[field] != "unknown" and not evidence:
                raise RegistryError(f"{capability_id}: {field} has no evidence")
            if evidence:
                resolved, _ = resolve_claim(evidence, sources)
                if resolved != states[field]:
                    raise RegistryError(f"{capability_id}: {field} contradicts ranked evidence")
        if states["unit_verified"] == "yes":
            verification = capability.get("verification") or {}
            if verification.get("unit") not in units or not verification.get("test"):
                raise RegistryError(f"{capability_id}: verification needs known unit and test")
        for key in ("name", "resources", "constraints", "conflicts",
                    "opportunities", "sources", "evidence", "last_verified"):
            if key not in capability:
                raise RegistryError(f"{capability_id}: missing {key}")

    return HardwareRegistry(sources=sources, capabilities=capabilities, units=units)
