# Contributing to VibePulse

Thanks for helping. Keep changes small, evidence-backed, and honest about which
layer was tested: Python host, simulator, firmware build, real host, or physical
panel.

## Before changing code

- Read [docs/agent-setup.md](docs/agent-setup.md) for a fresh setup and
  [AGENTS.md](AGENTS.md) for maintainer constraints.
- Read [docs/lessons.md](docs/lessons.md) before touching pollers, parsers,
  staleness, persistence, or service-manager setup.
- Read [docs/platform-support.md](docs/platform-support.md) before changing a
  platform claim.
- Never commit `secrets.h`, credentials, device/OTA/relay keys, session
  content, or production payloads.
- Never flash a user's device without their explicit permission.

## Verification

The complete local host gate is:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt \
  -r requirements-interaction-relay.txt
./test/run.sh
```

The gate starts with `ruff check .` (bug-shaped rules only; `pyproject.toml`
explains each). A `try/except/pass` that must stay needs a
`# noqa: S110 - <why>` naming the boundary it protects.

CI also builds the ESP32-S3 firmware and runs the tokenserver suite on Ubuntu,
macOS, and Windows. A green platform runner is automated portability evidence,
not by itself a real-host or physical-panel validation.

For Windows changes, follow
[docs/windows-validation.md](docs/windows-validation.md). Linux remains
unsupported until [issue #2](https://github.com/niclasvestlund-YT/vibepulse/issues/2)
and every gate in the platform matrix are complete.

UI changes must use the exact 480×480 simulator captures and the AMOLED review
workflow described in `AGENTS.md`. Simulator approval never authorizes a flash.

## Pull requests

Open a pull request against `main` from a focused branch in your fork. A small
bug fix can go straight to a PR; discuss a substantial feature or redesign in an
issue first so we can agree on scope before you invest time in it.

Explain the user-visible outcome, the root cause, the exact commands/tests that
passed, and anything not tested. Link real-host or physical evidence when the
change affects a public support claim. Keep release artifacts free of
`torget.bin`: it contains local Wi-Fi and device material.

For bug fixes, include a regression test that demonstrates the failure when
practical. Make sure new tests are actually invoked by `test/run.sh` and any
relevant platform-specific CI step. Most host tests are listed explicitly;
adding a `test_*.py` file alone does not register it. Tokenserver test modules
belong in `test/tokenserver-suite.txt`. For optional features, cover both enabled
and disabled behavior where the change affects those paths.

### Review and follow-up

The maintainer reviews the code and test evidence, enables first-time
contributors' CI runs after inspecting the changes, and leaves actionable
feedback. Approval to run CI is separate from approval to merge.

If changes are requested, update the same PR; there is no need to close it and
start over. Explain what changed and rerun the affected tests. Small integration
fixes may be added by the maintainer when you enable **Allow edits from
maintainers**. Your authorship and contribution credit are preserved.

Failures already present on `main` are investigated separately by the
maintainer. Please report the failing command and evidence rather than removing
checks to make a PR green. A documented local omission explains the evidence;
it does not waive a required GitHub check.

### Merge rules

Changes to `main`, including maintainer changes, go through a PR. Before merging:

- The maintainer has reviewed the final diff, and requested changes and review
  conversations have been resolved.
- All required GitHub Actions checks pass against the latest `main`.
- The documentation describes the resulting behavior and the verification does
  not claim more than was tested.

We normally use **Squash and merge** to keep one focused commit per PR, retaining
the contributor as author and any co-author credits. Merging does not publish a
release or authorize flashing a device.

The enforced settings are recorded in
[`.github/rulesets/main.json`](.github/rulesets/main.json). See
[Maintaining the merge rules](docs/maintaining-contributions.md) for the exact
checks and the single-maintainer review policy.
