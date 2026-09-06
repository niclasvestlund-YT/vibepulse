#!/usr/bin/env python3
"""This existing numbers transport never carries activity.

The separate interaction transport is explicit opt-in and end-to-end
encrypted. This file protects the old public numbers Worker/publisher and the
direct LAN client from accidentally becoming an activity transport.
"""

from pathlib import Path
import plistlib
import re

root = Path(__file__).resolve().parents[1]
read = lambda p: (root / p).read_text(encoding="utf-8")

config = read("components/app_tokens/app_tokens_config.h")
net = read("components/app_tokens/net.c")
github = read("components/app_tokens/github_net.c")
agent = read("components/app_tokens/agent_net.c")
needs_you = read("components/app_tokens/needs_you_net.c")
http = read("components/torget_net/torget_http.c")
log_target = read("components/torget_net/net_log_target.c")
relay_doc = read("docs/relay.md")
run_sh = read("test/run.sh")
example = read("secrets.h.example")
readme = read("README.md")
agent_setup = read("docs/agent-setup.md")
tokenserver_readme = read("tools/tokenserver/README.md")
interaction_guide = read("docs/interaction-relay.md")
interaction_worker_readme = read("tools/interaction-relay/README.md")
observability = read("docs/observability.md")
platform_support = read("docs/platform-support.md")
windows_validation = read("docs/windows-validation.md")
windows_host_report = read(
    "docs/superpowers/reviews/2026-08-27-windows-v0.7.1-read-only.md")
ci = read(".github/workflows/ci.yml")
mac_service = read("tools/tokenserver/se.torget.tokenserver.plist")
windows_service = read("tools/tokenserver/install-windows-task.ps1")
windows_runner = read("tools/tokenserver/run-windows-task.ps1")
windows_runner_test = read("test/windows-task-runner.ps1")

# --- What may cross: numbers -------------------------------------------
for name in ("TK_TOKENS_RELAY_URL", "TK_MAX_TRACKER_RELAY_URL",
             "TK_GITHUB_RELAY_URL"):
    assert name in config, f"{name} must be derived in app_tokens_config.h"

# Undefined base must compile to NULL, so a clone without the opt-in keeps
# exactly today's behaviour instead of fetching a malformed URL.
assert "#else" in config and "TK_TOKENS_RELAY_URL      NULL" in config, (
    "without TK_VIBEPULSE_RELAY_URL every relay address must be NULL"
)
assert 'torget_http_get_service("/api/tokens", TK_TOKENS_URL' in net
assert 'torget_http_get_service("/api/max-tracker", TK_MAX_TRACKER_URL' in net
assert 'torget_http_get_service("/api/github", TK_GITHUB_URL' in github

# --- What may not cross the numbers transport: anything naming your work --
assert "RELAY" not in agent.upper()
assert "failover" not in agent
assert "failover" not in needs_you, (
    "direct verdicts must not enter the plaintext numbers failover helper"
)
assert "TK_VIBEPULSE_RELAY_URL" not in needs_you
assert "torget_cloud_io" not in needs_you
assert "tk_interaction_relay_queue_verdict" in needs_you, (
    "the only cloud handoff here is the separately encrypted transport"
)

# The direct verdict route stays on LAN; remote delivery belongs only to the
# separate encrypted client and must not enter the numbers failover helper.
assert "tokens_agent_direct_origin" in needs_you, (
    "the Needs You verdict must return to the LAN host that supplied it"
)

# --- The failover helper itself -----------------------------------------
# A missing relay must be a plain fetch, not a special case each caller
# has to remember.
assert "if (!relay_url || !relay_url[0])" in http, (
    "a NULL/empty relay must degrade to a plain torget_http_get"
)
# Only the LAN attempt may fall onwards; a dead relay must not bounce back
# or two dead addresses spin a fetch in circles.
assert "tg_net_source_may_fall_back(source)" in http

# --- The secret URL may never reach a log --------------------------------
# The relay's path IS its access control: `/u/<secret>` reads the panel's
# figures and overwrites them. A serial capture or a pasted observability
# transcript is shared far more casually than the address itself, so the
# fetch client must never hand a raw address to ESP_LOG. The redaction sits
# in ONE helper, so a fourth failure path inherits it instead of having to
# remember it.
assert "Never print the secret URL in logs or a shared transcript." in \
    " ".join(relay_doc.split()), (
        "docs/relay.md must keep stating the rule this guard enforces"
    )
assert "tg_net_log_target(target, sizeof target, url, cloud)" in http, (
    "every logged fetch target must go through the redaction helper"
)
address_names = (
    r"\b(url|lan_url|relay_url|configured_url|discovered|alternate)\b"
)
for call in re.findall(r"ESP_LOG[A-Z]\((?:[^;]*?)\);", http, re.DOTALL):
    assert not re.search(address_names, call), (
        "a fetch log must name the redacted target, never a raw address:\n"
        + call
    )

# The helper keeps only what diagnoses — scheme, host, and which way was
# tried — and it does so unconditionally, without asking whether this
# particular address happens to be the secret one.
assert 'strcspn(authority, "/?#")' in log_target, (
    "path, query and fragment must all be cut, not just the path"
)
assert "if (authority[i] == '@') host_start = i + 1;" in log_target, (
    "userinfo is a credential too and must never be logged"
)
assert 'relay ? "relä" : "LAN"' in log_target, (
    "a redacted line must still say which route was taken"
)
assert "test_net_log_target.c" in run_sh, (
    "the redaction core is pure logic and must be host-tested in the gate"
)

# --- The opt-in stays opt-in -------------------------------------------
assert "/* #define TK_VIBEPULSE_RELAY_URL" in example, (
    "the relay must ship commented out — a fresh clone stays LAN-only"
)

# --- The same boundary, from the service side ---------------------------
# The publisher must have no way to send the activity endpoints, and the
# Worker must not accept them.  Three directions, one rule.
publisher = read("tools/tokenserver/publisher.py")
worker = read("tools/relay/worker.js")
for source, name in ((publisher, "publisher.py"), (worker, "worker.js")):
    assert "agent-status" not in source and "interaction" not in source, (
        f"{name} must never touch the activity endpoints"
    )
assert '"/api/tokens", "/api/max-tracker", "/api/github"' in worker, (
    "the Worker's endpoint allowlist must stay exactly the three number paths"
)

# --- Open-source feature choices stay independent and default-off --------
# These sentences are part of the setup contract, not marketing shorthand:
# installing one adapter must never silently widen what leaves the computer.
for heading in ("Claude interactions", "Codex interactions", "Numbers relay",
                "Interaction relay", "Live agent status relay", "GitHub"):
    assert heading in readme, (
        f"README must list {heading} as one independent switch"
    )
assert "Installing the Codex plugin does not enable Codex interactions" in readme
assert "--interactions` is a legacy alias for Claude only" in readme
assert "encrypted interaction relay" in readme.lower()
assert "off by default" in readme.lower()

# The live rows path is an independent, explicit E2E opt-in. It may name
# activity only inside fixed-size ciphertext and must never broaden the old
# plaintext numbers relay.
for source, name in ((readme, "README"),
                     (interaction_guide, "interaction guide")):
    for required in (
            "Live agent status relay", "computer must be awake",
            "end-to-end encrypted", "off by default"):
        assert required.lower() in source.lower(), (
            f"{name} must explain {required}"
        )
for required in (
        "relay enable-status --yes-e2e-cloud",
        "relay disable-status",
        "CONFIG_TK_VIBEPULSE_AGENT_STATUS_RELAY=y",
        "2,816-byte", "15 seconds", "20 seconds", "five seconds",
        "project basenames"):
    assert required.lower() in interaction_guide.lower(), (
        f"interaction guide must pin live-status contract: {required}"
    )
for required in (
        "PUT /v1/mailboxes/{box}/status",
        "GET /v1/mailboxes/{box}/status",
        "2,816", "20 seconds"):
    assert required in interaction_worker_readme, (
        f"Worker README must document status route/retention: {required}"
    )
for required in (
        "TK_VIBEPULSE_AGENT_STATUS_RELAY", "relay enable-status",
        "fixed-size ciphertext"):
    assert required in example, (
        f"secrets example must document live status: {required}"
    )
assert "status_polls_ok" in observability
assert "status_applied" in observability
assert "status_cleared" in observability

host_config = read("tools/tokenserver/vibepulse_config.py")
kconfig = read("main/Kconfig.projbuild")
assert "agent_status_relay: bool = False" in host_config
status_option = kconfig.split(
    "config TK_VIBEPULSE_AGENT_STATUS_RELAY", 1)[1]
assert "default n" in status_option.split("endmenu", 1)[0]

# A login service loads the saved interaction choices. Provider/detail choices
# do not belong in a visible, stale process command line.
plist = plistlib.loads(mac_service.encode("utf-8"))
assert plist["ProgramArguments"] == [
    "/Users/niclasvestlund/Torget/.venv/bin/python", "-u", "tokenserver.py"
]
for source, name in ((mac_service, "macOS launchd template"),
                     (windows_service, "Windows Task Scheduler installer"),
                     (windows_runner, "Windows Task Scheduler runner")):
    for forbidden in ("--interactions", "--claude-interactions",
                      "--codex-interactions", "--interaction-detail",
                      "--legacy-claude-panel-v1"):
        assert forbidden not in source, (
            f"{name} must load saved feature switches, not bake {forbidden} "
            "into its command line"
        )
    assert "saved config" in source.lower(), (
        f"{name} must explain that it starts from saved config"
    )

for guide, name in ((agent_setup, "agent setup"),
                    (tokenserver_readme, "tokenserver README")):
    assert "computer must be awake" in guide.lower(), (
        f"{name} must state the computer-on requirement plainly"
    )

# Windows autostart ships in-tree. The public guides must not keep telling
# people to hand-build a Task Scheduler entry after that installer exists.
assert "install-windows-task.ps1" in readme, (
    "README must link the shipped Windows Task Scheduler installer"
)
assert "install-windows-task.ps1" in tokenserver_readme, (
    "tokenserver README must document the Windows autostart installer"
)
assert "What is missing is **autostart**" not in readme
assert "Kvar på Windows: **autostart**" not in tokenserver_readme

# Platform claims must stay narrower than the evidence. Windows has a real,
# non-mutating installer gate; Ubuntu unit tests alone must never be promoted
# to Linux support while #2's host/service work remains open.
for required in (
        "Automated evidence", "Real-host evidence", "Physical end to end",
        "Windows", "Linux", "Not supported yet"):
    assert required.lower() in platform_support.lower(), (
        f"platform support matrix must explain {required}"
    )
for required in (
        "-ValidateOnly", "Private", "Task Scheduler", "Ser du APPROVE?",
        "Silence", "reboot"):
    assert required.lower() in windows_validation.lower(), (
        f"Windows release gate must include {required}"
    )
assert "[Host platform support](docs/platform-support.md)" in readme
assert "[Windows validation gate](docs/windows-validation.md)" in readme
assert "-ValidateOnly" in windows_service
assert "no Task Scheduler changes were made" in windows_service
assert "%LOCALAPPDATA%\\VibePulse\\Logs\\torget-tokenserver.log" in readme
assert "torget-tokenserver.log" in windows_runner
assert "$LogCapBytes = 5MB" in windows_runner
assert "$LogTailBytes = 256KB" in windows_runner
assert "Write-VibePulseLogLine" in windows_runner
assert "run-windows-task.ps1" in windows_service
assert "-MultipleInstances IgnoreNew" in windows_service
assert "Stop-ScheduledTask -TaskName $TaskName" in windows_service
assert "$RestartCount = 255" in windows_service
assert "$WatchdogTrigger = New-ScheduledTaskTrigger -Once" in windows_service
assert "-RepetitionInterval (New-TimeSpan -Minutes 5)" in windows_service
assert windows_service.index("New-ScheduledTaskSettingsSet") < \
    windows_service.index("if ($ValidateOnly) {")
assert "task objects: runtime construction passed" in windows_service
assert "Validate the Windows Task Scheduler installer without installing" in ci
assert ".\\tools\\tokenserver\\install-windows-task.ps1 -ValidateOnly" in ci
assert ".\\test\\windows-task-runner.ps1" in ci
assert "'test\\windows-task-runner.ps1'" in ci
assert "Run setup integration tests on Windows" in ci
assert "python test/test_vibepulse_codex_plugin.py" in ci
assert "VibePulse runner å" in windows_runner_test
assert "stdout was not captured" in windows_runner_test
assert "stderr was not captured" in windows_runner_test
assert "negative child exit was not normalized to 1" in windows_runner_test
assert "Codex bin directory was not prepended to PATH" in windows_runner_test
assert "Codex home was not passed to the child process" in windows_runner_test
assert "GitHub and subscription display inputs were not forwarded" in \
    windows_runner_test
for required in (
        "GithubRepo", "ClaudePlan", "CodexPlan", "ClaudePlanCostUsd",
        "CodexPlanCostUsd"):
    assert required in windows_service
    assert required in windows_runner
    assert required in tokenserver_readme
assert "Resolve-VibePulseCodexBinDir" in windows_service
assert "Resolve-VibePulseCodexHome" in windows_service
assert "Ubuntu" in readme and "not a support claim" in readme
assert "778 tests" in windows_host_report
assert "Panel end to end | FAIL" in windows_host_report
assert "Codex quota source | FAIL" in windows_host_report
assert "No credential, token, account identifier" in windows_host_report

print("OK: the numbers relay carries only numbers; activity uses a separate encrypted boundary")
