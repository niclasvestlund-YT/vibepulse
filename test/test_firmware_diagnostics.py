#!/usr/bin/env python3
"""Pins for the firmware's crash and reboot evidence (OBS-02, OBS-03, OBS-28,
OBS-35). These are source-level checks in the style of test_ota_partition.py:
the ESP-IDF build in CI proves the code compiles; this proves nobody
quietly removes a pin, a partition row or the ledger call.

Run from the repository root or the test directory:
    python3 test/test_firmware_diagnostics.py
"""
import re
from pathlib import Path

root = Path(__file__).resolve().parents[1]


def read(rel):
    return (root / rel).read_text(encoding="utf-8")


# --- partitions.csv: a coredump partition, big enough for an ELF dump ---
rows = {}
for raw in read("partitions.csv").splitlines():
    line = raw.split("#", 1)[0].strip()
    if not line:
        continue
    fields = [f.strip() for f in line.split(",")]
    rows[fields[0]] = fields
assert "coredump" in rows, "partitions.csv must carry a coredump partition"
assert rows["coredump"][1] == "data" and rows["coredump"][2] == "coredump"
size = rows["coredump"][4]
match = re.fullmatch(r"(\d+)([KM]?)", size)
assert match, size
size_bytes = int(match.group(1)) * {"": 1, "K": 1024, "M": 1024 * 1024}[match.group(2)]
assert size_bytes >= 128 * 1024, (
    "an ELF coredump with every task's stack needs room; 128K is the floor")

# --- sdkconfig.defaults: the pins, each with the reason beside it ---
config = read("sdkconfig.defaults")
for pin in (
    "CONFIG_ESP_COREDUMP_ENABLE_TO_FLASH=y",
    "CONFIG_ESP_COREDUMP_DATA_FORMAT_ELF=y",
    "CONFIG_ESP_SYSTEM_PANIC_PRINT_REBOOT=y",
    "CONFIG_LOG_DEFAULT_LEVEL_INFO=y",
    # ESP_LOGD compiled OUT: HTTP_CLIENT would print the relay's secret
    # URL at DEBUG (OBS-35). Raising the maximum level reopens that.
    "CONFIG_LOG_MAXIMUM_EQUALS_DEFAULT=y",
    "CONFIG_ESP_TASK_WDT_INIT=y",
    "CONFIG_LV_USE_LOG=y",
):
    assert re.search(rf"^{re.escape(pin)}$", config, re.M), f"missing pin {pin}"
assert "CONFIG_LOG_DEFAULT_LEVEL_DEBUG" not in config, (
    "DEBUG as the default level puts the relay secret on the serial line")

# --- main.c: the reboot ledger and the coredump notice at boot ---
main_c = read("main/main.c")
assert "static void reboot_ledger_note(esp_reset_reason_t rr)" in main_c
assert 'nvs_open("torget_boot", NVS_READWRITE, &ledger)' in main_c, (
    "the ledger lives in its own NVS namespace, never in the WiFi one")
for key in ('"boots"', '"panic"', '"wdt"', '"brownout"'):
    assert key in main_c, f"ledger must count {key}"
assert "esp_core_dump_image_get(&addr, &size)" in main_c
assert "idf.py coredump-info" in main_c, "the notice must say how to read it"
# Called right after NVS is up, before anything that could crash.
nvs_ready = main_c.index("ESP_ERROR_CHECK(nvs);")
assert main_c.index("reboot_ledger_note(rr);") > nvs_ready
assert main_c.index("coredump_note();") > nvs_ready
assert main_c.index("reboot_ledger_note(rr);") < main_c.index(
    "torget_boot_health_start();")
# Never a stop: an unopenable ledger logs and continues.
assert "ESP_ERROR_CHECK(nvs_open" not in main_c
# Never a count that was not proven saved: every NVS call is checked
# (NOT_FOUND is a fresh counter, anything else is reported as a failure).
ledger = main_c[main_c.index("static void reboot_ledger_note"):
                main_c.index("static void coredump_note")]
assert ledger.count("nvs_get_u32(") == 0, "reads go through ledger_read"
assert "ledger_read(ledger," in ledger
assert ledger.count("if (nvs_set_u32(") + ledger.count("(nvs_set_u32(") >= 2
assert "nvs_commit(ledger) != ESP_OK" in ledger
assert "sedan liggaren" in ledger, "the log line names the epoch honestly"
assert "första flash" not in ledger or "inte sedan första flash" in ledger

# --- CMake: the coredump component is an explicit requirement ---
assert "espcoredump" in read("main/CMakeLists.txt")

# --- docs: the runbook names the retrieval command and the blind spot is gone
observability = read("docs/observability.md")
assert "coredump-info" in observability
assert "no coredump partition" not in observability

print("OK: firmware diagnostics pins (coredump, reboot ledger, log level) hold")
