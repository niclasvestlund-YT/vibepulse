# Grok Pulse (host preview)

An optional, default-off tokenserver source for Grok usage and bot activity.
The host endpoints and panel page are separate, default-off options
(see [Panel page](#panel-page-build-time-opt-in)). macOS and Windows use the same stdlib implementation. Automated tests
are separate from real-host and physical-panel verification.

Enable with `--grok` (or `VIBEPULSE_GROK=1`) on your existing tokenserver
command. `--grok-source auto` is the default; `VIBEPULSE_GROK_SOURCE` accepts
`auto`, `subscription`, `xai`, `openrouter`, or `bots` (`manual` remains an alias of `subscription`). Restart the existing service after changing
code or its environment: use the restart instructions in
[macOS setup](agent-setup.md) or [Windows setup](windows-setup.md#restarting-the-scheduled-task).
Do not rerun the Windows installer merely to restart the task.

## Sources

### Subscription plan (default)

Most Grok users have a subscription plan. There is **no public usage API for
Grok subscriptions or Grok Bot**. This source never claims a live subscription
reading. Select `--grok-source subscription`, or leave the default `auto`.
Its payload is labelled `source: "plan"`.

Use `state_files.state_dir()` for the service account:
`~/Library/Application Support/VibePulse` on macOS,
`%LOCALAPPDATA%\VibePulse` on Windows. Write exactly one of these three
forms to `grok.json`:

1. **Copied percentage** from grok.com **Settings → Usage**:

   ```json
   {"v":1,"usedPct":42,"resetAt":1790200000,"label":"WEEKLY","plan":"SUPERGROK","window":"weekly"}
   ```

   `usedPct` must be an integer 0–100. Only `v` and `usedPct` are required.
   This is a manual observation with its file age, `estimated:false`.

2. **User-counted usage against your own plan limit**:

   ```json
   {"v":1,"plan":"SUPERGROK","window":"weekly","limit":100,"used":42}
   ```

   `window`, `limit` and `used` are required. The limit is user-set, never
   inferred from a subscription name. `limit` is an integer 1–1,000,000 and
   `used` an integer 0–10,000,000. The percentage is floored and capped at
   100. This carries file age and `estimated:true` (display as **EST**).

3. **Estimate from Grok Bot activity**:

   ```json
   {"v":1,"plan":"SUPERGROK","window":"daily","limit":100,"estimateFrom":"bots"}
   ```

   Uses the bot store's completed tasks today in the computer's local timezone,
   against your user-set limit (the same bound as above). Only `daily` is
   accepted: weekly estimates would need durable history. This is **EST**
   (`estimated:true`), with a live local count, `stale:false` and no file age.
   It does not measure provider usage; service restarts reset the count.

For all forms, `plan` is optional and displayed as uppercase ASCII, at most
12 characters (`planName`). `window` is `daily` or `weekly` and supplies
`windowSeconds` (86400 or 604800) and the default DAILY/WEEKLY label.
The copied-percentage form also accepts a custom `label` up to 16 characters.
Display text folds accents and drops unsupported characters.
`resetAt` is optional epoch seconds from 0 through 2^32 in forms 1 and 2;
otherwise daily windows count down to the next **local midnight** (including
local daylight-saving changes). Weekly windows omit an unknown reset.
Forms 2 and 3 include `planUsed` and `planLimit`.

Unknown keys, mixed forms and invalid numbers are rejected with one sanitized
warning. Files are capped at 64 KiB and read on each check; only changed content is
validated and applied. Replace them atomically to avoid partial reads. Invalid
replacements retain the last valid reading marked stale; a successful parse
clears that invalid state, and deleting the file clears its reading. Bot file
records are applied only after a new valid parse, preventing retained records
from being replayed. Copied observations and user counts become stale after
600 seconds or when their declared resetAt is reached; re-saving unchanged valid plan content refreshes its file age. No automatic grok.com access is implied.

### Source selection

`auto` selects a valid plan file first, then **xAI API**, then **OpenRouter**,
then bot activity once a bot has reported. Without a source it reports `none`.
API sources must be configured and have a fresh or cached reading; until then
they fall through. An API failure retains its last successful values marked
stale; API readings also become stale after 600 seconds. An explicit unavailable
source reports `source: "none"` and omits its numbers. Missing values are
never filled with invented zeroes. Bot activity alone is not a quota reading.

### xAI API (optional secondary source)

This measures the postpaid invoice amount due this billing month, not subscription usage.
Usage paid from prepaid credits is excluded. The host label is `XAI POSTPAID`;
the panel captions are POSTPAID · MONTH and POSTPAID LIMIT (POSTPAID where space is limited). It uses the
[Management API invoice preview](https://docs.x.ai/developers/rest-api-reference/management/billing).

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
polls immediately at startup, then every 300 seconds after success, with a
15-second timeout. Failures back off exponentially from 300 seconds to one hour.
For HTTP 429/503, a numeric Retry-After can extend that delay up to six hours
(the larger of the backoff and Retry-After, capped at six hours). Success resets
the backoff.
It reads `coreInvoice.totalWithCorr.val` (falling back to `amountAfterVat`),
negates `prepaidCredits.val` for the available credit balance, and uses a
positive postpaid `effectiveSpendingLimit` only. The label is `XAI POSTPAID`:
`spentCents` is the postpaid invoice amount due for the current billing month,
paired with that postpaid spending limit. Usage paid from prepaid credits is
not included, so the amount due stays zero while prepaid credits cover usage.
Credits are reported in `/api/grok` as `prepaidCents` for host consumers and
are not drawn on the panel page.
No limit is inferred from a plan.

### OpenRouter (optional secondary source)

Set `OPENROUTER_API_KEY` in the service environment and restart the service.
Select `--grok-source openrouter` explicitly, or use `auto`. Optionally set
`OPENROUTER_MANAGEMENT_KEY` to enable account credits; an inference API key
cannot read that endpoint. Credentials come only from these environment
variables; this source does not read Keychain or credential files.

The source polls immediately, then every five minutes after success, with the
same failure backoff and HTTP 429/503 Retry-After handling as xAI above, using fixed read-only
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

Readings with a daily, weekly or monthly window also carry `windowSeconds`, so
the panel can name the window and place its pace marker; lifetime budgets and
never-resetting limits omit it. This is per-key spend, not account-wide spend. Credits are optional
account-wide remaining credits (`total_credits - total_usage`), requiring
non-negative operands and a non-negative balance, reported in `/api/grok` as
`prepaidCents` for host consumers only with the management key. They are not
drawn on the panel page. A credits failure omits credits and marks the reading
stale. Once a cached accounting window reaches its reset instant, its reading
is stale with resetSeconds zero until a new poll succeeds. Unknown countdowns and missing numbers are omitted. All monetary values
are rounded to USD cents. No limit is inferred from a plan.

## Grok Bot status and local scripts

Grok Bot has no public usage API. These local reports supply activity status
and, only when configured above, a daily plan estimate.

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
LAN reads of the host preview endpoint `/api/grok` do not count as physical-panel
contact; the route remains listed in diagnostics.
The POST receiver accepts only loopback clients with a valid loopback Host,
no Origin header, JSON content type, known fields and bodies up to 1024 bytes.
It offers no browser CORS access. Local processes can submit statuses.

Grok data is never sent to VibePulse relays. Management keys stay on the host
and are never returned or logged; only the corresponding provider billing requests carry its key.
Bot names/activity and plan labels are visible to LAN clients, so avoid
sensitive task descriptions. Neither prompts nor chat history are collected.

## Panel page (build-time opt-in)

The optional **GROK** page defaults to the subscription plan. Its hero is
percent of the plan; **EST** marks a count or bot-task estimate. Copied readings
show **PLAN · GROK.COM** with their age; estimates show **PLAN · EST** with age
or **PLAN · EST FROM BOTS** when estimated from current bot tasks. There is no
public usage API for Grok subscriptions or Grok Bot; these are observations or
estimates, never live usage measurements. Counts show used and remaining values
when provided, and the reset counts down locally. Optional secondary xAI and
OpenRouter sources show percent of budget with spent and limit dollars below,
or a dollar hero without a positive limit. Dollar heroes and spent amounts retain
cents even above $1,000; secondary limit/remaining amounts keep their compact
format. Bot activity remains visible. Unknown
numbers show a dash; a later unavailable reading retains the last real value.
Stale values dim. If the panel gets no successful `/api/grok` read for 90
seconds, the bot rows dim and the provenance says `CACHED` (or `NO
CONNECTION`), so old WORKING/NEEDS YOU states never look live. A retained
number that the latest payload no longer carries keeps its own source label
and is shown dimmed. Plan resets count down locally. Bot states include
WORKING, NEEDS YOU, DONE, IDLE and ERROR; this page cannot approve actions.
Aggregate ACTIVE counts include both working and waiting bots, even when the
host truncates the displayed bot rows. NEEDS YOU shows `N+` when every
transmitted row is waiting and ACTIVE exceeds the row count; more bots may be waiting.

Enable `#define TK_GROK_SCREEN_ENABLED 1` in `secrets.h`, or configure firmware
with `-D TORGET_GROK=ON`. The default is **0**. An optional `TK_GROK_URL`
can name the LAN `/api/grok` endpoint; otherwise service discovery is used.
The computer must separately enable `--grok`. This is a build option, **not a
Labs switch**, and does not change saved Labs settings. Polling starts after
30 seconds, repeats every 15 seconds and backs off to two minutes on failure.

| Board | Composition |
| --- | --- |
| `waveshare_216` | 480×480, quota bar and two stats above up to three bot rows |
| `waveshare_241_v2` | 600×450, centred 480 composition |
| `waveshare_18_v2` | 368×448, platform-scaled 480 composition |
| `waveshare_175` | 466×466 round, shared edge ring, centred hero/stats and one priority bot line |
| `waveshare_191_touch` | 536×240, 480×240 viewport with metric left and up to three compact bot rows right |

Render each profile with the actual `torget-sim` binary:

```sh
cmake -S sim -B sim/build-grok -G Ninja -DTORGET_BOARD=<board> -DTORGET_GROK=ON
ninja -C sim/build-grok
SDL_VIDEODRIVER=dummy SDL_RENDER_DRIVER=software ./sim/build-grok/torget-sim --vibepulse-grok-qa
```

On a desktop, the SDL environment variables can be omitted. Offline builds can add
`-DFETCHCONTENT_SOURCE_DIR_LVGL=/path/to/cached/lvgl-src` to CMake.
The fixtures produce `/tmp/torget-vibepulse-grok-<state>.bmp` for
`startup` (before any Grok payload), `empty`, `xai`, `openrouter`, `plan`, `plan-est`, `plan-bots`, `bots`, `stale`, `disabled` and `live` (estimated plan percentage
plus four bots), followed by `offline` (91 s without a read) and `retained`
(a payload without numbers after real readings). Additional captures cover
retained bot/plan readings, plan age unknown, maximum wire values,
`huge-percent` (INT32_MAX cents against a one-cent budget), and `unlimited`
(the dollar-hero fallback with no limit).
`test/test_grok_render.py` builds all five profiles and checks these transitions,
caption and money widths/glyphs, and truncated NEEDS YOU counts. Additional
QA captures include `window-expired` (a plan reading 11 seconds after a
10-second reset, with the feed still fresh), `restored` (fresh values after retention) and
`dollar-millions` (a dollar hero retaining cents).
Set `TORGET_CAPTURE_DIR` to an existing directory to retain captures from
multiple board builds. QA returns nonzero with a clear message if Grok was
not compiled in. Empty is rendered first to verify the initial dash;
disabled after real readings exercises retention.

Evidence is limited to simulator output and build coverage. CI builds every
board with Grok off, plus the Grok simulator in the host gate. Grok-on firmware
builds were verified locally with ESP-IDF v5.5.2 for all five profiles.
**Grok is not physically verified on any
panel.** Native simulator captures do not certify touch, rotation, OTA or
physical AMOLED rendering.

Before the first successful poll, every profile shows a dash and WAITING, with
bot rows and the usage bar hidden. No snapshot is recorded by initialization.
OpenRouter captions use DAY, WEEK or MONTH for reported 1-day, 7-day or
28–31-day windows: DAILY/WEEKLY/MONTHLY BUDGET with a limit,
SPEND · DAY/WEEK/MONTH without one. Other or missing windows show BUDGET
or SPEND respectively. Optional `windowSeconds`
is an integer from 1 to 2678400. Pace uses this window when supplied. Without `windowSeconds` no pace marker
is drawn, including on the round ring. xAI never sends it; plan supplies it
only with `window`.
