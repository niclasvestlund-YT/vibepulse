#!/usr/bin/env python3
"""Target/simulator wiring and lifecycle guard for agent status polling."""

from pathlib import Path
import re


root = Path(__file__).resolve().parent.parent
agent_net_path = root / "components/app_tokens/agent_net.c"
assert agent_net_path.exists(), "agent_net.c must exist in the target component"

source = agent_net_path.read_text(encoding="utf-8")
app = (root / "components/app_tokens/app.c").read_text(encoding="utf-8")
target_cmake = (root / "components/app_tokens/CMakeLists.txt").read_text(
    encoding="utf-8"
)
sim_cmake = (root / "sim/CMakeLists.txt").read_text(encoding="utf-8")

assert '"agent_net.c"' in target_cmake, "target must compile agent_net.c"
assert "../components/app_tokens/agent_net.c" not in sim_cmake, (
    "simulator must never compile agent_net.c"
)
assert re.search(
    r"PRIV_REQUIRES[^)]*\btorget_net\b[^)]*\besp_http_client\b",
    target_cmake,
    re.DOTALL,
), "target component must privately require torget_net and esp_http_client"

assert app.count("tokens_agent_net_start();") == 1, (
    "tk_create must start the agent network task exactly once"
)
assert re.search(
    r"tokens_net_start\(\);\s*tokens_agent_net_start\(\);", app
), "agent task start must sit next to the existing VibePulse task start"

required_config = (
    ".url = TK_AGENT_STATUS_URL",
    ".timeout_ms = 2500",
    ".keep_alive_enable = true",
    ".keep_alive_idle = 5",
    ".keep_alive_interval = 5",
    ".keep_alive_count = 3",
    ".event_handler = status_http_event",
    ".user_data = &response",
)
for line in required_config:
    assert line in source, f"missing required client config: {line}"

assert source.count("esp_http_client_init(") == 1, (
    "agent polling must create exactly one HTTP client"
)
assert "esp_http_client_cleanup(" not in source, (
    "the long-lived polling task must not destroy its client per poll"
)
assert re.search(r"static\s+tk_agent_http_response\s+response\s*;", source), (
    "the 1536-byte response state must live in static .bss"
)
assert re.search(
    r"torget_ui_lock\(\);\s*tokens_apply_agent_status\(&snapshot\);\s*"
    r"torget_ui_unlock\(\);",
    source,
), "a valid snapshot must be applied under the UI lock"

print("OK: agentnätets targetkoppling och klientlivscykel är inkopplade")
