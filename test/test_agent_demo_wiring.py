#!/usr/bin/env python3
"""Regression guard for the target-only AgentMonitor demo switch."""

from pathlib import Path
import re


app_source = (Path(__file__).parent / "../components/app_tokens/app.c").resolve()
source = app_source.read_text(encoding="utf-8")

guarded_secrets_include = re.search(
    r"#ifdef\s+ESP_PLATFORM\s*\n#include\s+\"secrets\.h\"\s*\n#endif",
    source,
)
assert guarded_secrets_include, (
    "app.c must include secrets.h under ESP_PLATFORM so TK_AGENT_DEMO "
    "reaches the target build"
)

demo_guard = source.find("defined(TK_AGENT_DEMO)")
assert demo_guard >= 0, "app.c must keep the target-only TK_AGENT_DEMO guard"
assert guarded_secrets_include.start() < demo_guard, (
    "secrets.h must be included before TK_AGENT_DEMO is evaluated"
)

print("OK: agentdemons targetkonfiguration är inkopplad")

monitor_source = (
    Path(__file__).parent / "../components/app_tokens/agent_monitor.c"
).resolve().read_text(encoding="utf-8")
usage_source = (
    Path(__file__).parent / "../components/app_tokens/usage_screen.c"
).resolve().read_text(encoding="utf-8")

assert "LV_EVENT_CLICKED" in monitor_source
assert "tk_completion_queue_dismiss" in monitor_source
assert "LV_EVENT_LONG_PRESSED" in monitor_source
assert "torget_launcher_open" in monitor_source
assert usage_source.count("tk_agent_monitor_create(root)") == 1
assert "tk_agent_monitor_create_footer" not in usage_source
