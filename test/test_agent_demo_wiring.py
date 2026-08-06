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
