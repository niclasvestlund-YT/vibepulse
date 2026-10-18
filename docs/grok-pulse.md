# Grok Pulse (host preview)

An optional, default-off tokenserver source for Grok usage and bot activity.
The host endpoints and panel page are separate, default-off options
(see [Panel page](#panel-page-build-time-opt-in)). macOS and Windows use the same stdlib implementation. Automated tests
are separate from real-host and physical-panel verification.

Enable with `--grok` (or `VIBEPULSE_GROK=1`) on your existing tokenserver
command. `--grok-source auto` is the default; `VIBEPULSE_GROK_SOURCE` accepts
`auto`, `xai`, `openrouter`, `manual`, or `bots`. Restart the existing service after changing
code or its environment: use the restart instructions in
[macOS setup](agent-setup.md) or [Windows setup](windows-setup.md#restarting-the-scheduled-task).
Do not rerun the Windows installer merely to restart the task.

## Four sources

- **xai:** xAI API spend from **console.x.ai**, using the
  [documented Management API invoice preview](https://docs.x.ai/developers/rest-api-reference/management/billing).
  It reports month-to-date invoice spend, optional prepaid balance and an
  explicitly supplied positive spending limit, all in USD cents. This is
  **not the X Subscription weekly pool**, which has no documented usage API.
- **openrouter:** per-key UTC month spend, effective budget or key limit, and
  optional account credits from the OpenRouter API.
- **manual:** the percentage you copy from grok.com **Settings → Usage**.
  This remains a manual observation, with age measured from file modification.
- **bots:** current working/waiting count and locally observed completions
  today. Activity is not a quota measurement.

`auto` selects xai, then openrouter, then live bot activity (once any bot
has reported or completed today), then a valid manual file. API sources must
be configured and have a fresh or cached reading; until then they fall through.
An API failure keeps the last successful API values marked stale. An explicit
unavailable source reports `source: "none"` and omits its numbers.
Missing values are omitted, never filled with invented zeroes. API/manual
readings become stale after ten minutes; API failures mark stale immediately.

### xAI Management key

In console.x.ai settings, create a **management key** with billing read access.
This is separate from an inference API key. Set both variables in the
**service environment**, then restart the service:

```sh
export XAI_MANAGEMENT_KEY='<management key>'
export XAI_TEAM_ID='<team ID>'
```

A terminal export alone does not update an already running launchd or Windows
Task Scheduler process. Use your service's environment configuration; keep the
key out of command-line arguments, source control and shared logs. The source
polls immediately at startup, then every 300 seconds, with a 15-second timeout.
It reads `coreInvoice.totalWithCorr.val` (falling back to `amountAfterVat`),
negates `prepaidCredits.val` for the available credit balance, and uses a
positive `effectiveSpendingLimit` only. No limit is inferred from a plan.

### OpenRouter API key

Set `OPENROUTER_API_KEY` in the service environment and restart the service.
Select `--grok-source openrouter` explicitly, or use `auto`. Optionally set
`OPENROUTER_MANAGEMENT_KEY` to enable account credits; an inference API key
cannot read that endpoint. Credentials come only from these environment
variables; this source does not read Keychain or credential files.

The source polls immediately, then every five minutes, using fixed read-only
[key and credits endpoints](https://openrouter.ai/docs/api-reference/limits),
with redirects refused, bounded responses and an eight-second timeout per
request. The label is `OPENROUTER`. Spend and limit always come from the same window:

- With an `effective_budget` (the budget that blocks first), the panel shows
  that budget's `spend_usd` of `limit_usd` and counts down to its `resets_at`
  (no countdown for a lifetime budget).
- Otherwise, with a key `limit`, spend is `usage_daily`, `usage_weekly` or
  `usage_monthly` to match `limit_reset` (all-time `usage` for a limit that
  never resets), counting down to the next UTC day, Monday or month. When the
  key sets `include_byok_in_limit`, the matching `byok_usage*` is added.
- An unlimited key shows **UTC calendar-month spend for that key**
  (`usage_monthly`), with no limit, counting down to the next month.

This is per-key spend, not account-wide spend. Credits are optional
account-wide remaining credits (`total_credits - total_usage`), shown only with
the management key. A credits failure omits credits and marks the reading
stale. Unknown countdowns and missing numbers are omitted. All monetary values
are rounded to USD cents. No limit is inferred from a plan.

### Manual quota file

Use `state_files.state_dir()` for the service account:
`~/Library/Application Support/VibePulse` on macOS,
`%LOCALAPPDATA%\VibePulse` on Windows. Write `grok.json` there:

```json
{"v":1,"usedPct":42,"resetAt":1790200000,"label":"WEEKLY"}
```

`usedPct` is a number from 0 to 100. `resetAt` is optional epoch seconds;
`label` is optional, up to 16 characters (displayed uppercase ASCII; accents
are folded and other characters dropped, because the panel fonts are ASCII). The host reports integer percentages and remaining reset seconds.
Files are reread when their modification time changes. Invalid files are
ignored with one sanitized warning; replacing a file atomically avoids partial
reads. This manual snapshot never claims automatic grok.com access.

## Bot agents and local scripts

An agent or user can post status from the computer running tokenserver:

```sh
python3 tools/tokenserver/grok_status.py --bot Research --state working --activity searching
curl --fail http://127.0.0.1:8737/api/grok/bots \
  -H 'Content-Type: application/json' \
  --data '{"v":1,"bot":"Research","state":"waiting","activity":"APPROVAL"}'
```

The CLI accepts `--port` (default **8737**) and returns nonzero on failure.
States are `working`, `waiting`, `done`, `idle`, `error`; **waiting means
NEEDS YOU**. This status endpoint cannot approve actions or return answers.
Bot names contain 1–32 characters after control removal; name and activity
keep their case as printable ASCII (accents folded, so `Hårdvara` shows as
`Hardvara`; other characters dropped)
and truncated to 16 characters. A name with no displayable character is
rejected.

Alternatively write `grok-bots.json` in the same state directory:

```json
{"v":1,"bots":[{"bot":"Research","state":"working","activity":"SEARCHING","updatedAt":1790000000}]}
```

Use current epoch seconds for `updatedAt`. Working leases expire after ten
minutes, waiting after two hours, and done/error/idle after thirty minutes.
Expired bots disappear. At most sixteen bots are held (oldest evicted), and
up to four fit in each panel payload, ordered waiting, error, working, done,
idle, then newest first. Escaping-heavy text can reduce the published count
to keep JSON below 480 bytes. Active counts include all held working/waiting
bots. Completions count transitions into done observed today in the computer's
local timezone; repeated done updates do not increment it. Counts are in
memory and restart with the service; file input is not a durable history.

## Privacy and transport

`GET /api/grok` supplies the small flat panel payload on the existing LAN
server. When disabled it returns `{"v":1,"enabled":false}`; posting returns
404. Root diagnostics expose only enabled/source/stale and bot count.
The POST receiver accepts only loopback clients with a valid loopback Host,
no Origin header, JSON content type, known fields and bodies up to 1024 bytes.
It offers no browser CORS access. Local processes can submit statuses.

Grok data is never sent to VibePulse relays. Management keys stay on the host
and are never returned or logged; only the corresponding provider billing requests carry its key.
Bot names/activity and manual labels are visible to LAN clients, so avoid
sensitive task descriptions. Neither prompts nor chat history are collected.

## Panel page (build-time opt-in)

The optional **GROK** page displays xAI monthly API spend, a manually observed
quota percentage, or bot activity, with explicit source and freshness. Unknown
numbers show a dash; a later unavailable reading retains the last real value.
Stale values dim. If the panel gets no successful `/api/grok` read for 90
seconds, the bot rows dim and the provenance says `CACHED` (or `NO
CONNECTION`), so old WORKING/NEEDS YOU states never look live. A retained
number that the latest payload no longer carries keeps its own source label
and is shown dimmed. Manual resets count down locally. Bot states include
WORKING, NEEDS YOU, DONE, IDLE and ERROR; this page cannot approve actions.

Enable `#define TK_GROK_SCREEN_ENABLED 1` in `secrets.h`, or configure firmware
with `-D TORGET_GROK=ON`. The default is **0**. An optional `TK_GROK_URL`
can name the LAN `/api/grok` endpoint; otherwise service discovery is used.
The computer must separately enable `--grok`. This is a build option, **not a
Labs switch**, and does not change saved Labs settings. Polling starts after
30 seconds, repeats every 15 seconds and backs off to two minutes on failure.

| Board | Composition |
| --- | --- |
| `waveshare_216` | 480×480, hero above four bot rows |
| `waveshare_241_v2` | 600×450, centred 480 composition |
| `waveshare_18_v2` | 368×448, platform-scaled 480 composition |
| `waveshare_175` | 466×466 round, centred header and three bot rows (name + state; activity hidden to keep clear of the bezel) |
| `waveshare_191_touch` | 536×240, 480×240 viewport with metric left and four bot rows right |

Render each profile with the actual `torget-sim` binary:

```sh
cmake -S sim -B sim/build-grok -G Ninja -DTORGET_BOARD=<board> -DTORGET_GROK=ON
ninja -C sim/build-grok
xvfb-run -a ./sim/build-grok/torget-sim --vibepulse-grok-qa
```

On a desktop, `xvfb-run -a` can be omitted. Offline builds can add
`-DFETCHCONTENT_SOURCE_DIR_LVGL=/path/to/cached/lvgl-src` to CMake.
The fixtures produce `/tmp/torget-vibepulse-grok-<state>.bmp` for
`empty`, `xai`, `manual`, `bots`, `stale`, `disabled` and `live` (API spend
plus four bots), followed by `offline` (91 s without a read) and `retained`
(a payload without numbers after real readings). `test/test_grok_render.py`
builds the 2.16 variant in the host gate and checks those transitions.
Set `TORGET_CAPTURE_DIR` to an existing directory to retain captures from
multiple board builds. QA returns nonzero with a clear message if Grok was
not compiled in. Empty is rendered first to verify the initial dash;
disabled after real readings exercises retention.

Evidence is limited to simulator output and automated build coverage (including
the opt-in firmware path in CI). **Grok is not physically verified on any
panel.** Native simulator captures do not certify touch, rotation, OTA or
physical AMOLED rendering.
