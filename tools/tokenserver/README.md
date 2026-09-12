# tokenserver — VibePulse computer service

> **English quickstart:** `python3 tokenserver.py`. Python 3.11+ is required;
> the core service uses only the standard library,
> nothing to install. It reads your local Claude Code/Codex logs and serves
> `/api/tokens` + `/api/agent-status` + `/api/max-tracker` on port 8737 for
> the screen. Add `--github-repo owner/repository` for the optional public
> GitHub Stars/Forks feed at `/api/github`; it needs no token. Add
> `--claude-plan {pro,max5x,max20x}` and/or `--codex-plan
> {plus,pro}` to show a plan badge on the Max Tracker pages; both flags are
> optional and purely cosmetic (a display label, never used in any
> percentage math). For autostart on login, follow
> [Autostart via launchd](#autostart-via-launchd) and verify the live revision;
> do not copy an old plist between checkouts. Default privacy contract: only percentages
> and counts are served. Optional local interactions can transiently serve
> bounded detail, but prompts, commands and file contents are not stored.
> Full details below in Swedish. Your agent translates.

## Optional panel interactions (English quickstart)

The computer must be awake and the tokenserver must be running for fresh data
or answers. Direct LAN works only when the panel can reach the computer. The
numbers relay is separate and still publishes numbers only. The optional
encrypted interaction relay lets both sides use unrelated Wi-Fi with outbound
HTTPS; it is off by default and is not enabled by the Codex plugin.

From the repository root, use the guided setup:

```sh
python3 tools/vibepulse_setup.py install
python3 tools/vibepulse_setup.py status
python3 tools/vibepulse_setup.py doctor
python3 tools/vibepulse_setup.py disable codex
python3 tools/vibepulse_setup.py uninstall codex
```

Install offers four independent choices: Claude, Codex, both, or neither.
Installing the plugin does not turn Codex on. It also asks separately whether
bounded question/command detail may reach the local panel; no is the default.
After installation, open Codex, run `/hooks`, review the VibePulse
`SessionStart` and `PermissionRequest` hooks, and explicitly trust them. Then
**Start a new Codex task** so the hooks and MCP tool are freshly loaded. Doctor
reports trust/setup problems but does not bypass review.

From plugin `0.1.7`, the trusted `SessionStart` hook also makes a sub-second,
read-only request to fixed loopback endpoints. It reports one sanitized fault
class—provider data, device path, local service, or plugin/server source
drift—instead of letting every failure collapse into the same `STALE` advice.
It never restarts the service or treats a failed check as approval. Detailed
repair still starts with `doctor` and `tools/tokenserver/smoke.py`.

The same doctor path reads a content-free Claude credential guard from
`GET /`: `ready`, `expiring`, `expired`, `unavailable`, or `unknown`, plus
whole minutes remaining when known. It warns 30 minutes before expiry and
never returns or logs an access token, refresh token, account id, or Keychain
body. Read it together with `claudeProbe` and `/api/tokens`: when the probe is
`usage_http_200 + ok` and the relevant stale flag is false, a current Claude
client is feeding live quota even if the saved fallback says `expired`. A new
Claude Code CLI turn is the supported way to refresh that fallback; the
service detects it within 15 seconds without retrying a dead token upstream or
needing a restart.

The Codex safe-command tier only offers **ALLOW ONCE** for recognized
read-only, test, and build commands. Unknown, mutating, secret-bearing, or
truncated commands use the computer fallback. Questions do too unless Codex
provides two or three choices and marks exactly one recommendation. Timeout or
silence always falls back; it never approves.

`python3 tools/vibepulse_setup.py disable codex` keeps the package installed
but disables only Codex. `python3 tools/vibepulse_setup.py uninstall codex`
removes only the VibePulse Codex adapter and preserves Claude, relay, GitHub,
device-key, and unrelated Codex settings. The shared key and repository are not
deleted.

The old tokenserver `--interactions` flag is a legacy alias for Claude only.
Prefer the saved provider choices above. **legacy Claude v1 is insecure**: it
does not bind a verdict to provider and the exact view, so it is off by default,
Claude-only, and never suitable for Codex.

Both autostart launchers start `tokenserver.py` without provider/detail flags.
The service reads its saved config at startup; changing a choice does not
require editing a launchd plist or Task Scheduler command. Restart the service
after changing saved choices if it is already running.

For decisions across isolated Wi-Fi, read
[`docs/interaction-relay.md`](../../docs/interaction-relay.md), then use the
separate lifecycle:

```sh
python3 -m pip install -r requirements-interaction-relay.txt
cd tools/interaction-relay && npm ci && npx wrangler login && cd ../..
python3 tools/vibepulse_setup.py relay install --url HTTPS_ORIGIN --yes-e2e-cloud
python3 tools/vibepulse_setup.py relay status
python3 tools/vibepulse_setup.py relay doctor
python3 tools/vibepulse_setup.py relay disable
python3 tools/vibepulse_setup.py relay uninstall --keep-worker
python3 tools/vibepulse_setup.py relay uninstall --delete-worker
```

Installing the Codex plugin does not enable the encrypted interaction relay.
The tokenserver's `--publish` option remains numbers-only. Its separate
`--interaction-relay HTTPS_ORIGIN` option carries bounded E2E ciphertext;
question, command, project, and verdict content is encrypted locally before
it reaches the user-owned mailbox. Prefer `tools/vibepulse_setup.py`, which
stores these choices without putting secrets or feature flags in a service
command line.

Serves Claude and Codex usage as flat JSON in the glance pattern
(contract v2). The screen fetches `/api/tokens` over the LAN every 30
seconds. Pure Python 3 stdlib -- nothing to install. Three sources:

1. **The volume** -- `~/.claude/projects/**/*.jsonl` is scanned
   incrementally: today's/this month's tokens, burn rate, sessions.
2. **Claude's ceilings** (the Clawdmeter pattern) -- the service reads Claude
   Desktop's active, injected OAuth token or Claude Code's keychain fallback
   (on Windows instead `%USERPROFILE%\.claude\.credentials.json`, the same
   record -- see [Windows](#windows) below) and makes a minimal API request
   (`max_tokens: 0` -- prefill without output, effectively free) every 240
   seconds. The usage panel's three windows -- 5-hour, the week and the week
   for the heaviest model (Fable/Opus) -- come primarily from the usage
   endpoint (`usage_http_200 + ok`). The rate-limit headers are the
   FALLBACK, which logs `ratelimit-header:` when the primary path gave
   nothing mappable; seeing that line in a normal boot is a symptom, not a
   healthy start (OBS-23).
   The token never leaves the computer -- the screen only gets percentages.
   If the token copies the tokenserver can read have expired while the
   official Claude Desktop client is still working, its content-free
   `plan-usage-history.json` is used passively for the general week. The
   file must be at most 20 minutes old and an earlier authenticated cache
   entry must still carry the pool's valid reset. The model week is never
   guessed and therefore stays stale until the OAuth probe recovers.
3. **Codex's ceilings** -- the service asks Codex's local, read-only app
   server via `account/rateLimits/read`, i.e. the same current snapshot the
   Codex panel shows. If the app server is missing a passive fallback is
   used: bounded reading of the 20 newest
   `~/.codex/sessions/**/rollout-*.jsonl` (at most the last MiB per file).
   Only Codex's actual `event_msg`/`token_count` event with a direct
   `payload.rate_limits` is accepted in the fallback.

General weekly ceilings are kept strictly apart from named model quotas.
For Codex, `limit_name` must be absent, null or an empty string and
`window_minutes` must be exactly 10080 minutes. A 30-day window (43200) is
not published under the current WEEKLY contract. Spark and other named
quotas can therefore never replace WEEK. For Claude, exactly `7d` or `week`
is the general week; Fable, Opus, Sonnet and an explicit `model` are the
model week. An unknown name such as `7d_haiku` only becomes a sanitized
name in the root endpoint's diagnostics, never a quota value.

## Try it

```
python3 tokenserver.py
curl http://localhost:8737/api/tokens
```

## Optional GitHub page and star events

A public repo can be monitored without a token:

```
python3 tokenserver.py --github-repo owner/repository
curl http://localhost:8737/api/github
```

The environment variable `VIBEPULSE_GITHUB_REPO=owner/repository` is
equivalent and suits launchd. The first successful poll only sets the
baseline; old stars are not replayed as new. After that the repo metadata
is polled every other minute. On an increase the latest public stargazer is
fetched for the name in the popup; if that read fails the authoritative new
count is published anyway with an anonymous actor.

The GitHub monitor has its own thread, timeout and at least ten minutes of
error backoff. Its last good values are marked `stale`; errors never go
through the token, agent or Max Tracker flow. Without `--github-repo` the
endpoint explicitly answers `{"v": 1, "enabled": false}`. The screen's page
and popup are then enabled independently in `secrets.h`; see
`secrets.h.example`.

## Agent status

`/api/agent-status` is a separate v2 contract for Claude Code's and Codex's
ongoing activity. A background thread follows the most recently active
JSONL files incrementally every 0.5 seconds: Claude under
`~/.claude/projects` and Codex under `~/.codex/sessions`. The HTTP thread
only reads a locked in-memory picture; it never scans or opens session files
on request.

```json
{
  "v": 2,
  "seq": 12,
  "agents": {
    "claude": {
      "active_count": 2,
      "jobs": [{
        "task_id": "opaque-session-hash",
        "event_id": "4e50fb6a90293d167abb52a531c581fd",
        "state": "working",
        "project": "Torget",
        "activity": "testing",
        "model": "FABLE 5",
        "effort": "XHIGH",
        "updated_ms": 240
      }]
    },
    "codex": {
      "active_count": 0,
      "jobs": [{
        "task_id": "turn-id",
        "event_id": "c625c56abf9d8d7f13408a21179198c0",
        "state": "done",
        "project": null,
        "activity": null,
        "model": "GPT-5.6 SOL",
        "effort": "XHIGH",
        "updated_ms": 1900
      }]
    }
  }
}
```

- Each provider holds at most four prioritized public `jobs`, but
  `active_count` counts every known `working`, `waiting` and `error` even
  when the list is full. The server keeps at most 16 metadata jobs per
  provider.
- Jobs are ranked `waiting`, `error`, `working`, `done`; newer jobs come
  first within the same state. `seq` increases when the stored public
  status changes.
- `task_id` is the session log's opaque task identity and `event_id` a
  stable hash id of provider, task, state and source event.
- `state` is `idle`, `working`, `waiting`, `done`, `error` or `unknown`.
  `activity` is a coarse category such as `thinking`, `reading`,
  `editing`, `searching`, `running`, `testing`, `building`,
  `waiting_input` or `waiting_approval`.
- `project` is only a control-character-stripped basename of at most 16
  UTF-8 bytes; `task_id` is an opaque, collision-safe id of at most 64
  UTF-8 bytes. `updated_ms` is the time since the event's safe timestamp
  (the file's mtime is the fallback when a timestamp is missing), not the
  time the server happened to start.
- A `working` job not updated for 120 seconds drops out of the public list
  as unknown. It is never rewritten into an invented `done`, and merely
  reading the status does not increase `seq`.

The privacy boundary is deliberately hard: the classifier may look locally
at tool names and one command to tell tests, builds and ordinary runs
apart, but neither prompts, commands, message text, file content nor raw
log events are stored or exposed. Incomplete last lines are held locally
until the next append, but never beyond 1 MiB. Valid JSONL lines of at most
1 MiB are classified; larger or badly UTF-8-encoded lines are discarded up
to the next line break so a later valid event can still be read. Reading
happens in 64 KiB blocks and takes at most 1 MiB or 256 records per file
and poll; large historical files are therefore drained over several polls
without an unbounded memory peak.

The twelve active candidate files per provider are checked every 0.5
seconds. The recursive discovery of new sessions, however, runs at most
every five seconds, and reuses the same stat results for selection and
identity reconciliation. At most 48 file identities are kept; raw partial
buffers and path aliases for cold files are released. Short rotation or
temporary absence can still reuse the inode-bound offset and digest without
a history replay.

Unchanged fast polls do not open the file. An append only checks a bounded
prefix sample, while a new full SHA-256 verification starts at most once
every five seconds and reads at most 1 MiB per poll. Large prefixes are
therefore verified stepwise.
Shrinks, inode changes and suspicious signatures are handled immediately; a
later full match on a rewrite resets the follower and replays the
replacement exactly once. That gives eventual rewrite detection without
quadratic lifetime I/O, and no finished line content is stored.

Every existing file seen for the first time starts as backfill, even if the
whole file reaches EOF during the first read. The same applies after a
reset, an inode change on the same path or when a previously followed cold
file is rediscovered. Historical intermediate states are not published
meanwhile. Only the safely latest classified metadata event per provider
and path is kept compactly in memory and applied at most once when the
backlog is drained; if the final status is already public neither `seq` nor
its observation time changes. After that new complete append records are
handled normally again. The rediscovery markers are limited to 96 paths and
contain no raw log records.

Local smoke test from the repo root, on an alternative port:

```
python3 tools/tokenserver/tokenserver.py --port 8738
curl http://127.0.0.1:8738/api/agent-status
```

## Max Tracker

`/api/max-tracker` serves contract v1 for the two heatmap pages: the day's
quota peak per provider for the last 20 ISO weeks, plus the STREAK/MAX
WEEKS/AVG PEAK/MAX DAYS aggregates. The flags `--claude-plan
{pro,max5x,max20x}` and `--codex-plan {plus,pro}` are optional and only
render a muted badge ("PRO", "MAX 5X", "MAX 20X", "PLUS") in the page's
corner -- invalid values are rejected outright by argparse (`SystemExit`),
and the label never affects any percentage calculation.

The data comes from two independent channels in
`tools/tokenserver/max_tracker.py` (`MaxTrackerStore`):

- **Backfill**: a background thread runs `backfill_step()` every 0.5
  seconds (the same cadence as the agent-status poll), unbounded in time --
  it keeps ticking forever instead of switching itself off once it has
  caught up, because an empty step only reads the filesystem's stat info
  and is therefore cheap enough to run however often; that lets a brand-new
  rollout or session file be discovered automatically without a restart.
  The Codex quota is reconstructed from
  `~/.codex/sessions/**/rollout-*.jsonl`; the Claude quota cannot be
  reconstructed after the fact, so historical Claude days only get activity
  and a volume level, never a percentage.
- **Live observation**: the same places that already publish a fresh,
  non-cached, non-`stale` percentage to `/api/tokens` (the Claude probe
  every 240 seconds, the Codex read via the app server or the rollout
  fallback, and the existing day-volume tally) also feed the day's peak
  here -- never a `*Stale: true` value from the quota cache. The session
  window (5 h, 300 min) and the general week window (10 080 min) are kept
  apart by the same >600-minute rule already used for the Claude/Codex
  quotas; Codex carries its window in the clear in its own data, Claude is
  classified the same way `/api/tokens` already does it.

The top-level field `stale` mirrors exactly the same rule as
`claudeWeekStale`/`codexWeekStale` above (no new clock) -- true when the
server has not managed to update the general week quota lately.

Persistence: `~/Library/Application Support/VibePulse/max-tracker.json`,
atomic write in mode 0600, 400 days of sparse retention. Saving runs
asynchronously on its own background thread that drains until no change is
pending -- the same pattern as the quota cache's writer, but with a single
merged save instead of one record at a time.

```
curl http://127.0.0.1:8738/api/max-tracker
```

When Claude Desktop is running its fresh process token is used without a
dialog. With standalone Claude Code the first run may ask macOS for keychain
access ("security wants to use ... Claude Code-credentials") -- choose
"Always Allow" so the service can probe without asking again. The startup
log also prints the exact `anthropic-ratelimit-*` headers at the first probe
-- the answer key if the mapping ever needs adjusting.

### Windows

Claude Code has no keychain integration on Windows: `claude login` instead
writes exactly the same `{"claudeAiOauth": {...}}` record to an ordinary
file, `%USERPROFILE%\.claude\.credentials.json`. Run the service on Windows
and it reads that file -- no dialog, no configuration, the same trust
boundary as the keychain read on the Mac. The keychain and Claude Desktop's
process token are not consulted at all there; `security` and `pgrep` do not
exist anyway.

The probe lock -- the machine-wide single-probe guarantee that keeps the 429
penalty box away -- remains on Windows: `fcntl` is missing there, so the
lock is taken with `msvcrt.locking` instead. The same non-blocking gate,
another system call.

The Codex half works too. The quota read starts `codex app-server` and
reads its stdout; that used to be done with `select.select`, which on
Windows only takes sockets -- never pipes. The read now happens in a reader
thread with a queue, the same code on every platform.

The Codex desktop app and the Codex CLI are two different install surfaces
on Windows. The Store app's `codex` alias may show up for `Get-Command` and
still be refused when a background task tries to start it. Therefore install
OpenAI's standalone CLI once in PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://chatgpt.com/codex/install.ps1 | iex"
```

Then open a new PowerShell window and verify `codex --version`. VibePulse
prefers the installer's stable per-user path
`%LOCALAPPDATA%\Programs\OpenAI\Codex\bin\codex.exe` over `PATH` and
deliberately ignores `WindowsApps` aliases. Run
`python tools\vibepulse_setup.py doctor` after the install; a green
`PASS Codex executable` means the same runnable CLI can be used by the setup
tool and the tokenserver's app-server read.

The state files (lock, probe status, quota cache, history, max tracker)
live under `%LOCALAPPDATA%\VibePulse\` instead of macOS's
`~/Library/Application Support/VibePulse`. The paths worked literally before
too -- `Path.home()` resolves -- but put a `Library` tree in the user
profile that nothing else on the machine recognizes.

Autostart is included via Task Scheduler. Run from the repo root in
PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File tools\tokenserver\install-windows-task.ps1
```

Optional display sources must be given to the scheduled service just as at
a manual start. The repo value is public; subscription costs are never
guessed:

```powershell
powershell -ExecutionPolicy Bypass -File tools\tokenserver\install-windows-task.ps1 `
  -GithubRepo "owner/repository" `
  -ClaudePlan max5x -ClaudePlanCostUsd "100" `
  -CodexPlan pro -CodexPlanCostUsd "20"
```

The GitHub page and the star notices additionally need their separate
`TK_GITHUB_*_ENABLED` flags in the panel's gitignored `secrets.h`.

The script registers the service for the logged-in user, starts it at once
and restarts it on failure. It does not bake Claude/Codex or detail choices
into the command line; the same saved tokenserver configuration is used as
at a manual start. A hidden PowerShell wrapper runs the verified Python
3.11+ interpreter and writes stdout/stderr to
`%LOCALAPPDATA%\VibePulse\Logs\torget-tokenserver.log`; the log is rotated
at about 5 MB with a `.old` tail. Check the health with
`curl http://localhost:8737/`. Check the installer without touching Task
Scheduler with `-ValidateOnly`. Uninstall the autostart itself with:

```powershell
powershell -ExecutionPolicy Bypass -File tools\tokenserver\install-windows-task.ps1 -Uninstall
```

Response (the contract the app parses, `components/app_tokens/tokens_parse.c`;
null = honest absence, the screen shows dashes):

```json
{"v": 2, "dayTokens": 48231907, "dayTokensPerHour": 5120000,
 "daySessions": 4, "monthTokens": 612480233,
 "claudeSessionPct": 21.0, "claudeSessionResetMin": 80,
 "claudeWeekPct": 47.0, "claudeWeekResetMin": 850,
 "claudeWeekStale": false,
 "claudeModelWeekPct": 73.0, "claudeModelWeekResetMin": 850,
 "claudeModelWeekLabel": "FABLE · WEEK",
 "claudeModelWeekStale": false,
 "claudeWeekTodayDeltaPct": 6.0,
 "claudeModelWeekTodayDeltaPct": 3.0,
 "claudeSessionHourDeltaPct": 4.0,
 "codexSessionPct": null, "codexSessionResetMin": null,
 "codexWeekPct": 35.0, "codexWeekResetMin": 2210,
 "codexWeekStale": false,
 "codexWeekTodayDeltaPct": 2.0,
 "claudeForecastState": "at_reset",
 "claudeForecastPctAtReset": 85,
 "claudeForecastPaceFactor": 1.4,
 "claudeForecastAt": null, "claudeForecastOffsetMin": null,
 "codexForecastState": "collecting",
 "codexForecastPctAtReset": null,
 "codexForecastPaceFactor": null,
 "codexForecastAt": null, "codexForecastOffsetMin": null}
```

The newer delta and forecast fields are optional for older screen code and
`null` when the basis is missing. The forecast only becomes active after at
least three points, a 90-minute span and one percent of actual movement in
the same reset cycle.

## Startup: placeholders until the first scan is done

The first history scan can take minutes on a large `~/.claude`/`~/.codex`.
It runs in the background; meanwhile `/api/tokens` answers at once, with
the quota percentages live and the `usageTotals` block saying what the
volume counters are: `{"state": "refreshing", "sinceS": N, "placeholder":
true}` until the scan has completed, then `{"state": "ready", "ageS": N,
"placeholder": false}`, and `"failing"` if the recompute crashes (frozen
counters with `ageS`, or placeholders if no scan has succeeded yet;
`usageComputeOk` on `GET /` has the detail). The same block is on `GET /`.

**Placeholders are served only to a client that has said it understands
them.** A client sending `X-VibePulse-Accepts: usage-totals` gets the
answer above with zeros and the block; everyone else gets `503 {"error":
..., "usageTotals": {...}}`, the contract's error form, which the firmware
rejects and keeps its last values on -- so an already flashed panel never
learns zeros. Firmware from 2026-09-10 sends the header, reads
`placeholder` and applies the quota but leaves the value page alone until
the counters are measured. The relay publisher sends no placeholders, so a
panel behind the relay keeps its last real values.

## Quota cache and the stale contract

The latest authoritative Claude and Codex values for the general week and
the model week are saved atomically in
`~/Library/Application Support/VibePulse/quota-cache.json`. The identities
in the file are local SHA-256 values; raw provider ids, session paths,
projects, chats and content are not saved. The session/5 h window is not
cached.

- A successful current observation has a percentage and an absolute reset,
  is written to the cache and served with `*Stale: false`.
- Once the next scheduled probe or scan has failed (even when Codex only
  gave a named model quota) the previous in-memory value must not keep
  looking live. A matching, not yet expired cache entry may then be served
  with `*Stale: true`.
- At exactly the reset time the entry is expired. Then, or without a cache
  hit, the percentage, reset and any label are `null` and `*Stale` is
  `false`.
- `ResetMin` is recomputed from the absolute reset on every answer, so a
  cached value's remaining minutes keep decreasing. Stale values are not
  written to the usage history and are not used for deltas or forecasts.

The booleans `claudeWeekStale`, `claudeModelWeekStale` and `codexWeekStale`
are optional additions to the v2 contract. If the percentage is missing
the corresponding stale is always `false`.

## Local usage history

The service saves the history atomically in
`~/Library/Application Support/VibePulse/usage-history.json` on macOS and
under `%LOCALAPPDATA%\VibePulse\` on Windows. At most one point per
provider, window and 15 minutes is kept, and everything older than eight
days is pruned. Each point has exactly five values: time, `claude`/`codex`,
quota window, percentage and rounded reset cycle. Prompts, answers,
commands, projects, file names, models and token content cannot be written
to the file.

WEEK PACE is computed with a smoothed percentage slope from at most the
last 24 hours in the current week cycle. The result is either `collecting`,
`unavailable`, the projected percentage at reset (`at_reset`) or the
projected time the quota runs out (`exhausts`).

Install the optional local discovery in the same Python environment as the
service:

```sh
python3 -m pip install -r requirements-discovery.txt
```

The service then advertises `_vibepulse._tcp.local` without quotas, project
data or secrets. Current firmware caches a healthy Mac/PC and only switches
after a bounded failure. Without the package the same stdlib service starts
and uses the compiled-in fallback address exactly as before.

So keep pointing the screen's fallback here in the repo root's `secrets.h`.
On macOS the Bonjour name is best; on Windows a DHCP-reserved LAN address
is used:

```c
#define TK_VIBEPULSE_BASE_URL "http://<the-computer's-host-or-lan-ip>:8737"
```

It is the base address that is edited, not the individual endpoints:
`secrets.h.example` derives `TK_TOKENS_URL`, `TK_AGENT_STATUS_URL` and
`TK_MAX_TRACKER_URL` from it. Writing one of them by hand gets you either a
redefinition or three endpoints left on `YOUR-MAC.local`.

The Mac's Bonjour name: `scutil --get LocalHostName` (append `.local`).
Windows LAN IP: `ipconfig`; reserve the chosen IPv4 address in the router.

## Autostart via launchd

Install from the intended clean, durable checkout with the path-safe helper:

```sh
python3 tools/vibepulse_macos_service.py validate
python3 tools/vibepulse_macos_service.py install
```

`validate` is read-only. `install` resolves the current checkout and its
`.venv/bin/python`, writes the LaunchAgent atomically, preserves recognized
existing runtime arguments and environment values without printing them, and
uses `bootout` plus `bootstrap` so launchd cannot retain an older cached
command. It refuses foreign, symlinked, malformed, or unrecognized existing
plists. A short, bounded retry absorbs launchd's transient post-`bootout`
bootstrap race. If every new-service attempt fails, the previous plist is
restored and reloaded with the same bounded retry. Python 3.11+ and the exact
tokenserver source must exist before it changes anything.

Manual installation remains available for unusual layouts:

```sh
cp se.torget.tokenserver.plist ~/Library/LaunchAgents/
plutil -lint ~/Library/LaunchAgents/se.torget.tokenserver.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/se.torget.tokenserver.plist 2>/dev/null || true
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/se.torget.tokenserver.plist
```

The template plist assumes the repo lives in `~/Torget`, that the repo's
Python 3.11+ environment is in `.venv` and that the user is
`niclasvestlund` -- edit the paths otherwise. With the encrypted interaction
relay enabled that environment must also have
`requirements-interaction-relay.txt` installed. The log lands in
`~/Library/Logs/torget-tokenserver.log` (visible in the Console app,
survives a reboot; the server rotates it itself at start if it has grown
past ~5 MB, with the tail preserved in `.old`). The lines carry timestamps
and log transitions, not states: a healthy week is a few lines, not a
thousand.

Use a clean, durable checkout that will not be deleted when a PR is done.
`ProgramArguments[0]` (Python) and `WorkingDirectory` must point at the same
checkout. If any plist line changes, `kickstart` is not enough: launchd
keeps the configuration it has already loaded. Run `bootout` + `bootstrap`
as above. Preserve private arguments and relay settings when only the path
moves.

After an install or a move, three layers must point at the same source:

1. `python3 tools/vibepulse_setup.py doctor` must approve the Codex plugin,
   MCP and the tokenserver. Hook trust is still reviewed manually in
   `/hooks`.
2. `python3 tools/tokenserver/smoke.py --base-url http://127.0.0.1:8737`
   must report the expected `rev`, a matching source fingerprint and zero
   failures. Give the actually configured port if it is not 8737.
3. Start a new Codex task after the plugin/MCP has moved, so the new process
   really is loaded. Silence or an old task is not proof.

A panel restarted by the bounded HTTP-stall watchdog sends the fixed local
header `X-VibePulse-Recovery-Boot: http-stall-v1`. After two confirmed LAN
polls `GET /` shows only the boolean
`interactions.panel.httpStallRecoveryBoot`; no panel address, firmware text,
user or quota comes along. Doctor and the next Codex start can therefore see
a self-heal even on wall power without a serial cable. The marker is
evidence of the restart, not on its own a physical PASS.

The smoke test's totals for tracebacks and starts include preserved history.
After a repair the timestamps must be checked too: exactly one new start and
no new traceback/error lines after the reload is the healthy result.

The quickest health check is the smoke test -- the comb routine's steps 1-4
as one command:

```
python3 tools/tokenserver/smoke.py
```

## Honesty notes

- Tokens = in + out + cache write + cache read, deduplicated on
  message.id + requestId (resumed sessions are not double-counted).
- `dayTokensPerHour` is the last hour's actual consumption -- 0 means a
  pause, and the screen then stops ticking. No invented rates.
- The Codex percentage comes primarily from Codex's own current
  `account/rateLimits/read` snapshot. The general `codex` bucket is kept
  apart from named model quotas such as Spark. Rollout logs are only a
  fallback; a passed `resets_at` or a fallback scan without a general
  observation counts as a source failure and follows the stale contract
  above. The Claude probe costs one empty request every 240 seconds --
  negligible against the windows it measures.
- If the computer is off the screen keeps the last valid figures and marks
  them `CACHED` after two minutes -- they never pretend to be fresh
  (`components/app_tokens/app.c` only sets the stale flag, `usage_screen.c`
  swaps the label). Dashes appear in a different case: when the panel never
  got data at all, since `stale` requires `has_data`. Both are correct
  behaviour.
