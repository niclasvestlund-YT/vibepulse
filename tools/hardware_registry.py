import argparse
import sys
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
        if not isinstance(item, dict):
            raise RegistryError(f"{label} entry must be a mapping")
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id.strip():
            raise RegistryError(f"{label} id must be a nonempty string")
        if item_id in result:
            raise RegistryError(f"duplicate {label} {item_id}")
        result[item_id] = item
    return result


def resolve_claim(evidence, sources):
    if not evidence:
        raise RegistryError("claim has no evidence")
    ordered = sorted(evidence, key=lambda item: sources[item["source"]]["rank"])
    winner = ordered[0]
    winning_rank = sources[winner["source"]]["rank"]
    if any(sources[item["source"]]["rank"] == winning_rank
           and item["value"] != winner["value"] for item in ordered[1:]):
        raise RegistryError(f"ambiguous claim at rank {winning_rank}")
    conflicts = [item["source"] for item in ordered[1:]
                 if item["value"] != winner["value"]]
    return winner["value"], conflicts


def _collection(document, key, filename):
    if key not in document:
        raise RegistryError(f"{filename}: missing {key}")
    value = document[key]
    if not isinstance(value, list):
        raise RegistryError(f"{filename}: {key} must be a list")
    return value


def _secret_field(value):
    if isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(key, str) and any(
                    part in key.lower() for part in SECRET_FIELD_PARTS):
                return key
            found = _secret_field(nested)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _secret_field(item)
            if found:
                return found
    return None


def load_registry(root):
    root = Path(root)
    source_doc = _read(root / "hardware-sources.yaml")
    capability_doc = _read(root / "hardware-capabilities.yaml")
    unit_doc = _read(root / "device-units.yaml")
    registry_board = capability_doc.get("board")
    sources = _unique(
        _collection(source_doc, "sources", "hardware-sources.yaml"),
        "source",
    )
    units = _unique(
        _collection(unit_doc, "units", "device-units.yaml"),
        "unit",
    )
    capabilities = _unique(
        _collection(
            capability_doc, "capabilities", "hardware-capabilities.yaml",
        ),
        "capability",
    )

    for source_id, source in sources.items():
        for key in ("kind", "rank", "title", "publisher", "locator",
                    "revision", "accessed"):
            if source.get(key) in (None, ""):
                raise RegistryError(f"source {source_id}: missing {key}")
        rank = source["rank"]
        if not isinstance(rank, int) or isinstance(rank, bool):
            raise RegistryError(f"source {source_id}: rank must be an integer")
        if source["kind"] == "physical-test":
            unit_id = source.get("unit")
            tests = source.get("tests")
            if (not isinstance(unit_id, str) or unit_id not in units
                    or not isinstance(tests, list) or not tests
                    or any(not isinstance(test, str) or not test.strip()
                           for test in tests)
                    or len(set(tests)) != len(tests)):
                raise RegistryError(
                    f"physical-test source {source_id}: "
                    "needs known unit and nonempty unique tests",
                )

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
        for key, value in unit.items():
            if key == "secrets":
                continue
            secret_field = _secret_field({key: value})
            if secret_field:
                raise RegistryError(
                    f"unit {unit_id}: secret field {secret_field}",
                )

    for capability_id, capability in capabilities.items():
        for key in ("name", "resources", "constraints", "conflicts",
                    "opportunities", "sources", "evidence", "last_verified"):
            if key not in capability:
                raise RegistryError(f"{capability_id}: missing {key}")
        for key in ("resources", "constraints", "conflicts", "opportunities",
                    "sources", "evidence"):
            if not isinstance(capability[key], list):
                raise RegistryError(f"{capability_id}: {key} must be a list")
        states = capability.get("states")
        if not isinstance(states, dict) or set(states) != STATE_KEYS:
            raise RegistryError(
                f"{capability_id}: states must be {sorted(STATE_KEYS)}",
            )
        for key, value in states.items():
            if not isinstance(value, str) or value not in STATE_VALUES:
                raise RegistryError(f"{capability_id}: invalid {key}={value}")
        if states["unit_verified"] == "yes" and states["board_wired"] != "yes":
            raise RegistryError(
                f"{capability_id}: verified capability must be board_wired",
            )
        if states["firmware_enabled"] == "yes" and states["bsp_support"] == "no":
            raise RegistryError(
                f"{capability_id}: enabled capability has no software support",
            )
        confidence = capability.get("confidence")
        if (not isinstance(confidence, str)
                or confidence not in CONFIDENCE_VALUES):
            raise RegistryError(f"{capability_id}: invalid confidence")
        for source_id in capability["sources"]:
            if not isinstance(source_id, str) or source_id not in sources:
                raise RegistryError(f"{capability_id}: unknown source {source_id}")
        for finding in capability["evidence"]:
            if not isinstance(finding, dict):
                raise RegistryError(
                    f"{capability_id}: evidence entry must be a mapping",
                )
            missing = {"field", "value", "source"} - set(finding)
            if missing:
                raise RegistryError(
                    f"{capability_id}: evidence missing {sorted(missing)}",
                )
            field = finding["field"]
            value = finding["value"]
            source_id = finding["source"]
            if not isinstance(field, str) or field not in STATE_KEYS:
                raise RegistryError(f"{capability_id}: invalid evidence field")
            if not isinstance(value, str) or value not in STATE_VALUES:
                raise RegistryError(
                    f"{capability_id}: invalid evidence value {value}",
                )
            if not isinstance(source_id, str) or source_id not in sources:
                raise RegistryError(
                    f"{capability_id}: invalid evidence source {source_id}",
                )
            if source_id not in capability["sources"]:
                raise RegistryError(
                    f"{capability_id}: evidence source {source_id} "
                    "not listed in sources",
                )

        structured_conflicts = []
        for conflict in capability["conflicts"]:
            if isinstance(conflict, str):
                continue
            if not isinstance(conflict, dict):
                raise RegistryError(
                    f"{capability_id}: conflict must be prose or a mapping",
                )
            field = conflict.get("field")
            conflict_sources = conflict.get("sources")
            if (not isinstance(field, str) or field not in STATE_KEYS
                    or not isinstance(conflict_sources, list)
                    or not conflict_sources
                    or any(not isinstance(source_id, str)
                           or source_id not in capability["sources"]
                           for source_id in conflict_sources)
                    or len(set(conflict_sources)) != len(conflict_sources)):
                raise RegistryError(
                    f"{capability_id}: invalid structured conflict",
                )
            structured_conflicts.append(conflict)

        disagreements = {}
        for field in STATE_KEYS:
            field_evidence = [
                finding for finding in capability["evidence"]
                if finding["field"] == field
            ]
            if len({finding["value"] for finding in field_evidence}) > 1:
                disagreements[field] = {
                    finding["source"] for finding in field_evidence
                }
        for conflict in structured_conflicts:
            expected_sources = disagreements.get(conflict["field"])
            if (expected_sources is None
                    or set(conflict["sources"]) != expected_sources):
                raise RegistryError(
                    f"{capability_id}: structured conflict does not match "
                    "ranked evidence",
                )
        for field, evidence_sources in disagreements.items():
            if not any(
                    conflict["field"] == field
                    and set(conflict["sources"]) == evidence_sources
                    for conflict in structured_conflicts):
                raise RegistryError(
                    f"{capability_id}: contradictory {field} evidence "
                    "needs structured conflict metadata",
                )

        for field in STATE_KEYS:
            evidence = [item for item in capability["evidence"]
                        if item.get("field") == field]
            if states[field] != "unknown" and not evidence:
                raise RegistryError(f"{capability_id}: {field} has no evidence")
            if evidence:
                resolved, _ = resolve_claim(evidence, sources)
                if resolved != states[field]:
                    raise RegistryError(
                        f"{capability_id}: {field} contradicts ranked evidence",
                    )
        if ("verification" in capability
                and not isinstance(capability["verification"], dict)):
            raise RegistryError(f"{capability_id}: verification must be a mapping")
        if states["unit_verified"] == "yes":
            verification = capability.get("verification") or {}
            unit_id = verification.get("unit")
            test = verification.get("test")
            if (not isinstance(unit_id, str) or unit_id not in units
                    or not isinstance(test, str) or not test.strip()):
                raise RegistryError(
                    f"{capability_id}: verification needs known unit and test",
                )
            if units[unit_id]["board"] != registry_board:
                raise RegistryError(
                    f"{capability_id}: verification unit board does not match "
                    "registry board",
                )
            matching_physical_source = any(
                finding["field"] == "unit_verified"
                and finding["value"] == "yes"
                and sources[finding["source"]]["kind"] == "physical-test"
                and sources[finding["source"]]["unit"] == unit_id
                and test in sources[finding["source"]]["tests"]
                for finding in capability["evidence"]
            )
            if not matching_physical_source:
                raise RegistryError(
                    f"{capability_id}: verification needs matching "
                    "physical-test source",
                )

    return HardwareRegistry(sources=sources, capabilities=capabilities, units=units)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    args = parser.parse_args(argv)
    registry = load_registry(args.path)
    print(
        f"OK: {len(registry.capabilities)} capabilities, "
        f"{len(registry.sources)} sources, {len(registry.units)} units",
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RegistryError, yaml.YAMLError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
