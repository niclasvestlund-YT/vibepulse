# Lessons log

What has bitten this project, why, and the rule each bite taught. The
full narratives live in the commit messages (keep writing them there —
that practice is the best thing this repo does); this file is the index
that makes them findable without `git log -p`, so the same mistake
doesn't need to be paid for twice.

**When to add an entry:** any fix whose commit message tells a
root-cause story, any comb finding (see
[observability.md](observability.md)) that turned into an "oh, *that's*
why", any physical-hardware surprise. Format:

```
## YYYY-MM-DD · Title that names the mistake
What happened · Root cause · The rule now · Guards (commits, tests, fixtures) · Watch for
```

Keep entries under ~12 lines. If a guard doesn't exist yet, say so and
point at the backlog item.

---

## 2026-08-30 · One STALE label hid three different broken links

**What happened:** repeated incidents were troubleshot from scratch because
Claude-source failure, an old tokenserver checkout, and a live host with a
stalled panel HTTP path all rendered as `STALE`. The Codex `SessionStart` hook
existed but only injected static usage guidance; it measured no health. A
wall-powered watchdog restart also left no evidence without USB serial.
**The rule:** classify source, host provenance, and glass transport before
choosing a repair, and make recovery observable on its normal power source.
**Guards:** plugin 0.1.7 performs a bounded loopback startup classification,
pins the matching tokenserver source fingerprint, and tests every fault class;
firmware reports an exact content-free recovery-boot marker that tokenserver
accepts only after two LAN polls. **Watch for:** treating the marker as a
physical PASS, or expecting an already running Codex task to load a newly
released plugin without update plus a new task.

## 2026-08-30 · A disconnect-only watchdog could not clear wedged HTTP state

**What happened:** the first recovery candidate cleared `STALE` after boot,
then the wall-powered panel became stale again. Claude, the local API, and the
ESP32-compatible relay request remained fresh, the board answered ICMP, and
two later questions ended in computer fallback/timeout. **Confirmed failure
boundary:** the firmware's one Wi-Fi disconnect had no escalation when no real
quota success followed; the exact lower-level TLS/client trigger was not
captured without serial on wall power. **The rule:** recovery must be staged
and a real data success—not a reconnect attempt—must close the incident.
**Guards:** after an initial success, relay-configured firmware recycles Wi-Fi
at 60 seconds, wakes the quota task, waits for a new IP before retrying, and
restarts once after a further 45 seconds without success. Cold boot, LAN-only,
disconnected, and clock-regression states stay fail-closed; host tests pin the
transitions.
**Watch for:** calling the hard-recovery candidate fixed before it passes the
dedicated-power stale window and a repeated physical question.

## 2026-08-30 · First DNS-SD result was not the user's active computer

**What happened:** after the HTTP watchdog image kept quota data fresh beyond
the old failure point, the canonical panel question still timed out. Two
healthy `_vibepulse._tcp` services—Mac and Windows—were advertised on the same
LAN, while the flashed image's encrypted interaction relay was disabled.
**Root cause:** sticky DNS-SD discovery is suitable for choosing a data host,
but result order cannot express which computer owns a new interactive prompt.
**The rule:** diagnose numbers and questions as separate transports; a shared
panel must use the end-to-end encrypted interaction relay for questions.
**Guards:** agent setup and plugin 0.1.5 now name the multi-host signature and
require fresh flash consent. **Watch for:** interpreting a healthy poll against
the wrong tokenserver as proof that the current computer reached the glass.

## 2026-08-30 · A boot-time PASS hid a five-minute HTTP stall

**What happened:** after the discovery-capable firmware was flashed and moved
to dedicated power, the panel cleared `STALE` and completed one physical
local-LAN APPROVE round trip. Several minutes later `STALE` returned. The host and the
ESP32-shaped numbers-relay request were still fresh and the board still
answered ICMP, but direct application polls had stopped and a second
interaction timed out. **Confirmed failure boundary:** network-interface
liveness and one successful boot-time request had been mistaken for sustained
application-HTTP progress. The exact lower-level trigger was not captured
without serial on wall power; the always-powered panel also retained ESP-IDF's
default modem-sleep policy and had no bounded transport recovery. **The rule:** a physical network
PASS must outlive the stale window and repeat the interactive round trip.
**Guards:** the quota transport now records last success and, only after an
initial success, only while associated, and only when a redundant numbers
relay is configured, begins a bounded staged recovery before the glass becomes
stale. Target Wi-Fi disables modem sleep because this is a wall-powered live
display. The policy is host-tested and fail-closed for cold start, LAN-only
installs, disconnects, and clock regression. **Watch for:** calling ping,
fresh server JSON, or a single post-boot interaction proof that the panel will
stay fresh.

## 2026-08-30 · A fresh feed did not prove fresh glass

**What happened:** the Mac API and the numbers relay both served fresh Claude,
Fable, and Codex values while the physical panel still showed `STALE` and made
no confirmed LAN poll. **Root cause class:** provider freshness had been proved,
but the device-side hop had not; the installed firmware predated automatic
Mac/Windows discovery and the running board consumed the computer USB port's
full 500 mA budget, a known unreliable condition for Wi-Fi bursts. A Python
relay probe added noise by receiving a User-Agent-specific Cloudflare `403`
while the ESP32-compatible request was healthy. **The rule:** prove source,
local API, relay with the real client shape, direct panel polling, firmware
generation, and power separately. **Guards:** doctor now reports direct panel
evidence without treating relay-only operation as failure; plugin 0.1.3 carries
the decision tree. **Watch for:** calling fresh JSON a healthy screen or a
generic HTTP client equivalent to the panel.

## 2026-08-29 · A dead saved credential and a live quota source coexisted

**What happened:** Fable went stale after the saved Claude credential expired;
later the usage probe recovered through a newly started Claude client, but
doctor still described the saved credential as if the current source were
dead. **Root cause:** active process candidates have no readable expiry, while
the content-free guard correctly retained the expired saved-Keychain state;
the diagnostics then collapsed those two truths and prescribed a needless
server restart. **The rule:** diagnose active source outcome, saved recovery
readiness, and served stale flags separately. **Guards:** plugin 0.1.2 skill,
doctor/smoke regression tests, and the 15-second local reread. **Watch for:**
calling an expired fallback the current outage when `claudeProbe` is healthy.

## 2026-08-28 · Codex cancelled the panel before the panel deadline

**What happened:** the canonical physical question reached the VibePulse MCP
but returned computer fallback after roughly thirty seconds. **Root cause:**
the bridge and permission hook allowed 125 seconds for the panel's bounded
120-second hold, while `codex mcp add` left `tool_timeout_sec` unset and the
CLI's shorter default won. **The rule:** every nested timeout must exceed the
deadline it encloses, and setup must verify the persisted external value rather
than its own constant. **Guards:** setup now atomically pins the owned MCP row
to 130 seconds, doctor rejects the legacy missing value, and the Windows
release runbook checks the real CLI listing. **Watch for:** Codex changing the
MCP listing schema or maximum timeout.

## 2026-08-28 · USB-värden var inte panelens datavärd

**What happened:** panelen flyttades från Macens USB-port till Windows och
fortsatte visa Macens gamla kvot. **Root cause:** USB gav bara ström; firmware
pollade fortfarande en ensam kompilerad tokenserveradress. **The rule:** lokal
värdidentitet ska upptäckas som en tjänst, hållas sticky medan den är frisk och
falla tillbaka till den explicita URL:en när multicast saknas. **Guards:**
valfri innehållsfri DNS-SD-annons, strikt IPv4/port-policy, bounded mDNS-query,
NVS last-known-good och Mac/Windows-failoverprov. **Watch for:** att kalla en
strömförflyttning för ett källbyte eller att blanda endpoints från två värdar.

## 2026-08-28 · A healthy scheduled Codex source failed in the interactive shell

**What happened:** a direct Windows probe failed while the scheduled API
returned a fresh numeric Codex observation. **Root cause:** the task prepended
the verified standalone Codex bin directory, while the interactive shell did
not; login and `CODEX_HOME` alone did not make the environments equivalent.
**The rule:** diagnose a background source in the exact service environment
before calling it stale. **Guards:** the installer pins the verified Codex bin
and optional `CODEX_HOME`; the Windows runbook checks the scheduled API.
**Watch for:** using `Get-Command` or an unpinned shell probe as task evidence.

## 2026-08-28 · Real Windows startup outlived the optimistic Codex deadline

**What happened:** the background Codex week could remain stale even though
the standalone CLI and its task environment were valid. **Root cause:** a real
Windows `codex app-server` startup could take longer than the original bounded
probe allowance. **The rule:** keep the read bounded, but choose its deadline
from the slow real-host boundary and verify the production refresh path uses
that value. **Guards:** PR #37 raises the local allowance to 15 seconds and a
regression test exercises the production caller. **Watch for:** shortening a
provider deadline from fast unit-process timings or interactive warm starts.

## 2026-08-28 · A disconnected validation host is NOT TESTED, not green

**What happened:** the real-PC run stopped after useful host evidence but
before the final merged commit, lifecycle transitions, and physical question.
**Root cause:** remote-host reachability is independent of tokenserver health,
and an in-progress task cannot leave new evidence after its PC disconnects.
**The rule:** pin every PASS to its exact commit, persist only sanitized
checkpoints, and keep every unfinished row FAIL or NOT TESTED. **Guards:** the
current-main Windows checkpoint and support matrix link each claim to its
evidence boundary. **Watch for:** treating repeated retries, old PASS results,
or an active remote task as proof that the current candidate passed.

## 2026-08-27 · Codex CLI provenance changed shape without changing owner

**What happened:** Windows setup registered the exact release checkout with
Codex CLI 0.150.1, then rolled back because its own post-install verification
classified the plugin and marketplace as foreign. **Root cause:** the newer CLI
omits `marketplaceSource` from marketplace-list rows and reports the plugin's
local marketplace cache as its source, while the executable plugin path and
marketplace root still identify the requested checkout exactly. **The rule:**
version external JSON contracts by observed shape and keep ownership checks on
the fields that still name executable code and the registered root. **Guards:**
both strict legacy and 0.150 schemas have provenance tests; unexpected fields,
foreign roots, and foreign plugin paths remain fail-closed. **Watch for:** a
future Codex CLI schema adding another shape without a fixture from the real
Windows boundary.

## 2026-08-27 · Windows boundaries disagreed with portable-looking tests

**What happened:** setup doctor rejected Python 3.12, Unicode hook JSON used
the active Windows code page, and the task installer passed parser validation
but failed before registration. **Root cause:** the production reader preserved
`\r\n`, text-mode hook output inherited a code page, and the Task Scheduler
XML value `StopExisting` was passed to a PowerShell cmdlet that only accepts
`Parallel`, `Queue`, or `IgnoreNew`; its restart count was also 999
although the schema limit is 255. **The rule:** test machine protocols through
the production boundary on every claimed host OS, write explicit UTF-8, and
keep scheduler values inside the XML schema even if a cmdlet accepts more.
Execute non-mutating object construction in `-ValidateOnly`. **Guards:**
Windows setup integration CI, strict LF/CRLF and Unicode tests, three-script
parsing, runner tests, portable task settings, and a real forced-process
restart. Native failures are normalized to exit 1 because Task Scheduler did
not retry the long-lived action reliably even after PowerShell's forwarded
`-1`/`0xFFFFFFFF` was normalized. A five-minute repeating trigger is the
explicit watchdog; `IgnoreNew` prevents duplicates while healthy. **Watch
for:** schema values PowerShell omits or fails to constrain, and assuming
RestartOnFailure covers a successfully started long-lived action on every
Windows release. Task Scheduler also has a smaller PATH than an interactive
shell, so the installer verifies the optional Codex executable and passes only
its bin directory to the wrapper. Its service can also retain a pre-login
environment snapshot, so an existing custom `CODEX_HOME` is passed explicitly
to the child without changing it. Never infer background CLI readiness from the
installer's interactive environment alone.

## 2026-08-27 · A visible Windows Codex command was not a runnable CLI

**What happened:** Codex worked in the Windows desktop app and `Get-Command`
could resolve `codex`, but the VibePulse background read failed and a local
wrapper reported that the CLI was missing. **Root cause:** Windows exposed a
Store-managed `WindowsApps` alias that was not executable from the scheduled
background context; Task Scheduler also inherits a smaller `PATH` than an
interactive shell. **The rule:** Windows provider validation must execute the
standalone CLI from its stable per-user path and prove the app-server quota
read, not merely resolve a command name. **Guards:** executable discovery now
prefers `%LOCALAPPDATA%\Programs\OpenAI\Codex\bin\codex.exe`, ignores
`WindowsApps`, doctor tests execution, and the public Windows runbook separates
desktop login, CLI execution, and fresh quota evidence. **Watch for:** wrappers
or scheduled tasks that reintroduce interactive-`PATH` assumptions.

## 2026-08-27 · A green build from an old tree hid the panel test

**What happened:** the panel first showed **UPDATE READY**, then questions with
only **LEAVE IT** or a buttonless private screen, while localized project text
contained boxes. **Root cause:** a valid but older worktree was flashed, the
attract label used an uppercase-only font, and the diagnostic questions did
not satisfy the one-recommendation/physical-fit contract. **The rule:**
preview, test, build, and flash from one identified checkout, then verify the
whole Codex → panel → touch → Codex round trip with one canonical short
question. **Guards:** the post-flash smoke recipe in `docs/agent-setup.md`, the
physical review dated 2026-08-27, plugin documentation assertions, and the
hardware registry. **Watch for:** treating a successful build, a visible
waiting screen, silence, or computer fallback as an end-to-end pass.

## 2026-08-26 · Logged in did not mean the exported usage token was fresh

**What happened:** Claude kept working all evening while Fable alone became
stale; `claude auth status` still said logged in. **Root cause:** Claude
Desktop's long-lived child retained a frozen process token, while the separate
Keychain access token VibePulse can read expired at 21:52; the passive general
week fallback masked the split. **The rule:** login state and an out-of-process
usage credential are separate health dimensions, and expiry must be warned
before the first failed request. **Guards:** content-free `claudeCredential`
readiness on `GET /`, a 30-minute startup/doctor/smoke warning, 15-second local
recovery checks, and honest stale rendering. **Watch for:** hidden keepalive
prompts or undocumented refresh-token calls—neither is an acceptable fix.

## 2026-08-23 · Claude kept working after every readable token copy had died

**What happened:** the panel showed Claude `STALE` while Claude Desktop was
actively consuming the plan on both Mac and PC. **Root cause:** Desktop can
refresh and use credentials inside its running client without replacing the
launch-time environment token or the expired Claude Code keychain record that
the tokenserver is allowed to read. The previous recovery assumed real client
use always replaced one of those copies. **The rule:** authentication health
and quota-observation health are separate truths; use a bounded official local
usage artifact when it proves freshness, but never infer a named model pool or
reset it does not contain. **Guards:** strict v2/size/age/percentage parsing,
authenticated-reset reuse, OAuth-newer precedence, and regression coverage in
`ClaudePlanUsageFallbackTests`. **Watch for:** Claude changing the local file's
version, cadence, path, or `fh`/`sd` fields.

## 2026-08-19 · `sdkconfig.defaults` did not migrate the existing LVGL pool

**What happened:** v0.6 froze on any full redraw while the LVGL task held
the adapter lock. JTAG caught the exact failure rendering `61%` with
`plex_num_164`: LVGL rounded the 144×119 A4 glyph to a 144×128, 18,432-byte
temporary buffer, its allocator returned NULL, and LVGL's default malloc
assert entered `while(1)`. **Root cause:** the checked-in default had already
moved the PSRAM-backed LVGL pool to 256 KiB, but the existing generated
`sdkconfig` was still 96 KiB; defaults seed new configs and do not migrate old
ones. The always-created v0.6 WiFi overlay made that stale budget fail.
**The rule:** treat critical Kconfig values as build invariants, not defaults.
**Guards:** root CMake now rejects LVGL pools below 256 KiB and
`test_lvgl_memory_config.py` covers both sides. Verify the effective value in
`build/config/sdkconfig.h`. **Watch for:** changing `sdkconfig.defaults`
without regenerating or explicitly updating every existing build config.

## 2026-08-17 · The compiled-in IP address pointed at a network that no longer existed

**What happened:** away from home, VibePulse showed dashes while
Solelkollen on the same glass fetched happily. Hours of network
debugging followed — IoT VLANs, client isolation, router admin — before
the actual cause surfaced. **Root cause:** `TK_VIBEPULSE_BASE_URL` in
`secrets.h` was a raw DHCP address (`http://192.168.1.50:8737`) from a
network the Mac was no longer on. The runbook
(`docs/agent-setup.md` step 1) had said to use the Bonjour name all
along — "so the same binary works at home and on a phone hotspot" — but
nothing *enforced* it, and an IP typed in once during setup worked for
weeks before silently going stale. **The rule:** an address compiled
into the firmware must be a *name*, never a number; a number is a
snapshot of a DHCP lease. More generally: every compiled-in endpoint is
the next travel failure — WiFi credentials were made data
(`components/torget_wifi`), and the service address got a relay fallback
(`net_source_policy`) for the reachability class no rename can fix.
**Guards:** the runbook rule already existed; the relay fallback
(`test_relay_boundary.py`) covers the cross-network case; the verify step
in `docs/agent-setup.md` step 1 now greps for `http://[0-9]` and warns.
**Watch for:** companion-app endpoints (`SG_GLANCE_URL` and friends)
and any future `TK_*_URL` configured as an IP "just for now".

## 2026-08-17 · A global auth threshold refused every open network

**What happened:** on the road the panel would never join a café or
airport network. The serial log showed the SSID being tried and a
disconnect, with nothing pointing at why. **Root cause:**
`wifi_apply()` set `cfg.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK`
for *every* network. The threshold means "refuse anything weaker", and
open (`WIFI_AUTH_OPEN`) is weaker — so an open network was rejected
before it was ever attempted. The line was written when there were
exactly two networks, both WPA2; it silently became a policy about all
future networks. **The rule:** a per-connection setting derived from
one network's properties must move with the network, not sit as a
global. Here the threshold now follows each candidate: WPA2 where there
is a password, open where there is not. **Guards:**
`wifi_apply_current()` in `main/main.c` derives it per slot;
`tg_wifi_pass_valid` treats an empty password as a valid open network
(`test/test_wifi_slots.c`). **Watch for:** any other `cfg.sta.*` field
set once at boot that describes *a* network rather than *the* radio.

## 2026-08-17 · The escape hatch needed the network it was escaping

**What happened:** designing WiFi provisioning, the first instinct was
to deliver it over the air like everything else. **Root cause:** OTA
needs the network the panel cannot reach — a fix for "no network" can
never arrive through the network, so the feature had to be recoverable
from the device alone. **The rule now:** the compiled-in `secrets.h`
networks stay an **immutable floor** that stored credentials can only be
added on top of, never replace. No entry written at a hotel can strand
the panel, so the worst case is "it does not join here", never "it needs
a USB flash to come home". **Guards:**
`tg_wifi_candidates()` always appends the fixed networks
(`test/test_wifi_slots.c` has an explicit empty-store case);
`test/test_wifi_setup_wiring.py` asserts the floor stays in the
candidate build. **Watch for:** any future store that *replaces* a
compiled-in fallback instead of layering over it.

## 2026-08-16 · LVGL's pool starved the flush's DMA and the glass froze

**What happened:** the Needs You build froze the panel intermittently on
hardware — silent, no panic: UI and polling dead, ICMP crawling, but the
idle task alive so no watchdog fired. A diagnostic build (heap heartbeat +
`vTaskList` on stuck lock + WDT-panic + poisoning) caught the `lvgl` task
**Running while holding the LVGL lock** with every other task blocked
behind it, at internal-heap `min=16 B`, `DMA-largest≈4.6 KB` — **below the
flush's 11 520 B** (`DISPLAY_FLUSH_ROWS×480×2`). **Root cause:** LVGL's
builtin allocator puts its `LV_MEM_SIZE` pool (96 KB) as a static array in
*internal* BSS; that plus the takeover's objects pushed the largest
contiguous DMA-capable block under the flush buffer → flush dies in
NO_MEM, render wedges holding the lock, whole glass freezes. Same class as
[One byte over budget froze the display] and the 2026-08-14 Vibbe freeze,
new starvation source. First fix guess (`SPIRAM_TRY_ALLOCATE_WIFI_LWIP`)
made it *worse* (min→16 B) — reverted. **The rule:** LVGL's pool belongs
in PSRAM, not internal BSS; internal RAM is for DMA/WiFi. Measure the
*largest DMA block* against the flush size, never total free — frag hides
in the total. **Guards:** `LV_MEM_POOL_ALLOC`→`heap_caps_malloc(SPIRAM)`
via `main/lv_psram_pool.h` wired to the lvgl component in root CMakeLists,
pool enlarged to 256 KB (internal free 22→147 KB, DMA block 7.7→53 KB);
new `LÅGT DMA-block` warning in `tick_cb`'s heap probe fires before the
freeze threshold. **Watch for:** any large LVGL/const array landing in
internal BSS; the flush buffer size changing without re-checking the DMA
headroom; CMake silently dropping function-style `-D` macros (the pool
macro must live in a header).

## 2026-08-15 · kickstart restarts the process, not the plist

**What happened:** the tokenserver kept dying with `unrecognized arguments:
--plan claude=100`, restart after restart, taking the whole panel down.
**Root cause:** the running launchd process was pre-GitHub code that predated a
CLI change; editing the plist and running `launchctl kickstart -k` restarted it
with launchd's *cached* ProgramArguments, never re-reading the edited file.
**The rule:** `kickstart -k` is for code changes (same args, updated
`tokenserver.py`); argument/plist edits need `bootout` + `bootstrap`.
**Guards:** none yet — the plist story lives in
[github-pulse.md](github-pulse.md); CLAUDE.md's OTA note still only mentions
kickstart. **Watch for:** any plist edit that "doesn't take" after a restart.

## 2026-08-15 · An optional feature was enabled in the wrong layer

**What happened:** the GitHub screen rendered in QA but never on the glass, then
came up with no data. **Root cause:** two separate opt-ins were missing —
`TK_GITHUB_SCREEN_ENABLED` (the view compiled out) and `TK_GITHUB_URL` (the poll
task compiled out). Both belong in the per-install `secrets.h` (see
`secrets.h.example`), but the screen flag was fixed by hardcoding it into
`components/app_tokens/CMakeLists.txt` (`b5c5a7a`) instead. **The rule:** enable
per-install feature flags where the design put them — local `secrets.h`, not the
shared firmware CMake; a build define collides with a template-based secrets.h
and silently wins the wrong way. **Guards:** `secrets.h.example` already carries
the full block; cleanup tracked in
[github-pulse.md](github-pulse.md#known-follow-ups). **Watch for:** any
`target_compile_definitions` that duplicates a `#ifndef`-guarded config default.

## 2026-08-13 · The expired token that outranked a fresh login

**What happened:** the screen sat on `usage_http_401` for hours after a
perfectly good re-login. **Root cause:** the probe read the OAuth token
from a running process's environment — which reflects launch time, not
now. A Claude Desktop child process outlived its token and kept
"winning" over the fresh keychain credential. A second bug compounded
it: the probe demanded an active session window, so between windows it
discarded valid weekly data and reported the *fallback's* 401 as the
whole story — blaming auth while auth was fine. **The rule:** report
the status of the source that actually decided, and never require more
data than the answer needs. **Guards:** keychain fallback + candidate
ordering (`7d213ec`), probe succeeds without a session window
(`8d3b4b3`), runbook row updated (`2002725`). **Watch for:** any new
credential source silently outranking a fresher one.

## 2026-08-13 · The 429 night: the probe fed its own penalty

**What happened:** a debugging evening ended rate-limited, repeatedly.
**Root cause:** on 429 the probe fell through to the header fallback
(one more request) and retried the full multi-request cycle two minutes
later — each retry extending the penalty it was caught in. A dead token
had the same shape: two doomed requests every 120 s for hours. **The
rule: failure must slow you down.** Every poller needs backoff, and a
rate-limit response is an instruction, not an error to retry. **Guards:**
10-min cooldown honoring `Retry-After`, cycle aborts on 429
(`c5510b5`); failure-streak slowdown 120→240→480 s (`8f6b8bd`); tests in
`test_tokenserver.py`. **Watch for:** the firmware pollers, which never
got this medicine — fixed cadences, agent-status at 1 Hz (OBS-13). Also:
don't restart the server to "fix" a 429 — that resets the cooldown and
repeats the mistake.

## 2026-08-13 · A stale worktree served old code for an hour

**What happened:** an hour of process archaeology because the launchd
service was quietly running from a different checkout than the one being
edited. **Root cause:** the plist hardcodes its `WorkingDirectory`; no
artifact said which code was live. **The rule:** every long-running
artifact must be able to answer "what revision are you?" in one command.
**Guards:** `GET /` reports `rev` + `startedAt` (`8f6b8bd`) and a
startup `srcFingerprint` (content hash — catches dirty worktrees and
post-start edits that share HEAD with the checkout); the smoke test
compares both against your checkout; the firmware boot banner logs its
version and reset reason (OBS-01, done). **Watch for:** the plist's
hardcoded `WorkingDirectory` is still the root cause waiting to recur —
the guards make it visible, not impossible.

**2026-08-28 recurrence:** the live tokenserver, Codex plugin, MCP registration,
and marketplace had independently retained absolute paths to older checkouts.
All could look installed while executing different revisions. Moving the
plist required `bootout` + `bootstrap`; `kickstart` would have reused launchd's
cached paths. The repair rule is now stricter: install all four integrations
from one clean, durable checkout, run setup doctor, verify live `rev` and
`srcFingerprint` with smoke against the actual port, and start a new Codex task.
Historical tracebacks remain in the bounded log, so a post-repair verdict must
also prove that no new error appeared after the new timestamp.

## 2026-08-13 · Stale data replayed as breaking news

**What happened:** after a tokenserver outage, the revived agent feed's
hours-old "waiting for you" states each seized the whole screen as
full-screen alerts. Separately, stale waiting/error records displaced
newly observed work. **Root cause:** alerting and eviction logic trusted
*content* without checking *age*. **The rule:** freshness is part of the
data, and anything that interrupts the user must prove it first.
**Guards:** alerts gated on freshness incl. post-boot (`89f161f`),
cached quotas rendered stale (`ca55c30`), expired records evicted first
(`ef85bf3`). **Watch for:** the device's freshness clock is fed by one
endpoint only — other feeds can still show `LIVE` while dead (OBS-09).

## 2026-08-13 · Upstream data is hostile: the NUL-truncation escalation

**What happened:** four commits in one arc as each fix revealed the next
hole: quota labels arriving NUL-truncated could confuse parsing, then
detection, then forecast trust, then key matching. **Root cause:** the
JSONL and API payloads are *someone else's* output format, unversioned,
and they change and break mid-write; anything not explicitly validated
will eventually lie. **The rule:** parsers are contract-strict — reject
the whole payload on any violation, keep last-known-good, and when a
value can't be trusted, don't display a "probably". **Guards:**
`bbcd5ec` → `254f774` → `3d3825c` → `1d371dc`; empty Codex limit names
(`fa44802`); named vs general quota separation so Spark can never
replace WEEK (`0a0e98e`); hostile-input C tests in `test/test_tokens.c`.
**Watch for:** strictness without diagnostics — a rejection today logs
nothing about *what* offended (OBS-22).

## 2026-08-13 · One byte over budget froze the display

**What happened:** the screen went permanently stale against a healthy
server. **Root cause:** the worst-case v2 payload was 1 058 bytes; the
firmware's all-or-nothing body cap was 1 024. Nobody had ever computed
the worst case. **The rule:** an all-or-nothing contract needs a
capacity gate — a test that constructs the worst-case payload and proves
it fits, so growth fails in CI, not on the shelf. **Guards:** headroom
raised + capacity gate (`c0016d9`, `a90b6f2`,
`test/test_token_body_capacity.py`). **Watch for:** adding any payload
field without touching the capacity test.

## 2026-08-13 · Round before you serialize

**What happened:** the device rejected entire max-tracker responses.
**Root cause:** the server emitted float day-peaks; the device parses
them into `int8_t` and — correctly, per contract — rejected the whole
payload. Truth must be shaped *before* the wire, not after. **Guards:**
server rounds day peaks (`97dd531`); the recorded-live-shape fixture
`sim-fixtures/max-tracker-live-shape.json` locks the real contract into
the sim and tests. **The rule:** when a bug comes from real recorded
data, freeze that data as a fixture forever.

## 2026-08-13 · The log-tail state machine took four rounds

**What happened:** the max-tracker backfill (offsets, watermarks,
carried lines) was "fixed" four times; round 2's fix was rejected by two
new empirical repros. Failure modes included permanent starvation
(oversized line never consumed, offset frozen) and double-counting
(keying on `(inode, size)`). **The rule:** incremental-file-reader state
is the hardest state in this codebase — every claimed fix needs a
reproducing test *before* the fix, and reviewers should assume the next
hole exists. **Guards:** `6e8ebba`/`733bf9c`, `c5eae88`, `09badc2`,
`c7c95e5` → `4a5d761`, each with its repro test. **Watch for:** the
backfill loop still swallows runtime exceptions at 2 Hz (OBS-19).

## 2026-08-13 · Threads shared a list without a story

**What happened:** `ThreadingHTTPServer` request threads shared the
usage-history store with unguarded swap/sort/rollback. **The rule:**
every store touched by the HTTP threads needs an explicit lock story,
and slow work (disk, recompute) moves off the request path. **Guards:**
thread-safe history (`6966957`), nonblocking cache reads (`c0fddcf`),
async persistence (`4041f16`). **Watch for:** `_probe_status` is still
read torn-able without its lock (OBS-18c).

## 2026-08-13 · Inverted defaults shipped a stranger someone else's app

**What happened:** an outside user flashed "VibePulse" and got Swedish
electricity prices — and their board began polling the maintainer's
other project's website every 30 s (~2 880 req/day per device). **Root
cause:** companion apps were opt-out instead of opt-in; the governance
test passed both ways, so it guarded nothing. **The rule: the defaults
are the product.** A fresh clone must build exactly one app, and any
test guarding a default must *fail* on the harmful configuration —
tighten the test in the same commit as the fix. **Guards:** `57e00ba`
(defaults flipped + registry test tightened), `2ec791d` (the two-pass
CMake guard divergence that made "macro on, include dir off" possible).
**Watch for:** any new build-time gate needs both halves guarded — the
compile definition *and* the sources it implies.

## 2026-08-13 · A computer USB port cannot run this panel

**What happened:** flashing worked but the running board bounced off the
USB bus or hung; it looked exactly like a flaky cable or bad firmware.
**Root cause:** the AMOLED draw exceeds what a computer port reliably
supplies. **The rule:** flash in download mode (screen dark), run from a
dedicated PSU; interpret enumeration bouncing as a power symptom first.
**Guards:** documented in `docs/agent-setup.md` and README
(`9ddf387`); full narrative in
`docs/superpowers/reviews/2026-08-13-max-tracker-physical-static.md`.
**Watch for:** serial-monitoring a *running* board is still unverified —
it needs a powered hub or PSU/data split, which also caps how much the
firmware log can help until solved.

## 2026-08-13 · CI's fresh VM exposed a boot-blind throttle

**What happened:** the first CI run of the new logging failed a test
that passed everywhere locally: the throttled save-error log never
fired. Codex's PR review flagged the same line independently. **Root
cause:** the throttle used `0.0` as "long ago" with `time.monotonic()`
— which counts from *boot*. On any machine up less than the 5-minute
window (CI VMs always; a Mac right after restart), `now - 0.0` never
reaches the threshold, so the **first error after boot is exactly the
one that gets swallowed** — in production, not just in tests. **The
rule:** "never happened yet" is a state, not a timestamp zero; with
monotonic clocks use a `None` sentinel, because monotonic's epoch is
arbitrary and *recent* on fresh machines. **Guards:** `None` sentinels
in both error-log throttles; the writer test now exercises the
first-error path on CI's short-uptime VMs by construction. **Watch
for:** any new `last_*_logged` / `last_*_at` throttle seeded with `0.0`.

## 2026-08-13 · Docs drifted from code five times in two days

**What happened:** five separate correction commits: setup steps telling
users to uncomment a block that ships active (at the most expensive
possible step), a README advertising a page that didn't exist, an
under-claimed privacy surface, stale AGENTS.md claims confusing a
stranger's agent, and a verification step that "always passes and proves
nothing". **Root cause:** prose claims have no failure mode — nothing
red happens when they rot. **The rule:** prefer doc claims a command can
verify; when code changes behavior, grep the docs for the old claim in
the same commit; a verification that cannot fail is not a verification.
**Guards:** `439481a`, `3e2eb3c`, `289fea1`, `e353371`, `668f4c0`; the
two doc-content tests (`test_shared_amoled_skill.py`,
`tools/test_hardware_registry.py`) are the only mechanical ones — and
`design-qa.md` has already drifted again (OBS-26). **Watch for:** every
new doc in this observability set is prose too; comb step 7 includes
checking that these files still tell the truth.

## 2026-08-21 · A changing ring is not a changing screen

**What happened:** the Needs You countdown belongs to one arc, but its
`ring_permille` lived in the full-screen render key. At 10 Hz, every new
ring value hid every group, restyled the provider, reassigned all labels and
buttons, then moved the root foreground again. That amplified draw traffic on
the same partial-buffer ESP32 path that previously wedged in glyph rendering.
**The rule:** separate stable semantic state from small animated state; update
the smallest LVGL object that actually changed. **Guard:** the simulator now
advances twenty deterministic ticks and requires one full paint, multiple
ring-only updates, and unchanged ticks. The target's ten-second heap heartbeat
logs and resets content-free `full/ring/unchanged` counters. **Watch for:** any
timer field added to a `memcmp` render key can silently turn a cheap animation
into a full-tree repaint.

## 2026-08-21 · Internet access is not end-to-end reachability

**What happened:** quota and GitHub data were fresh through the numbers relay,
but Claude/Codex activity stayed frozen. The panel and Mac both had working
internet and were even on the same subnet, yet Wi-Fi client isolation dropped
their direct traffic. **Root cause:** “the Wi-Fi icon is connected” and “one
feed is fresh” were treated as evidence that every feed could reach its source;
agent status still had only the direct `.local` LAN path. **The rule:** model
freshness per data path, not per radio or app. A cloud fallback must keep its
own explicit privacy switch, expiry, source precedence, and honest stale clear.
**Guards:** live agent status is a separate default-off E2E-encrypted feature;
direct LAN wins for five seconds, authenticated relay rows expire, and an old
relay-owned activity view clears once instead of pretending to be live.
**Watch for:** adding another screen to a shared “connected” indicator without
testing the exact transport and stale boundary that feed actually uses.

## 2026-08-21 · Change detection is not a cloud write budget

**What happened:** the numbers Worker began returning error 1101 and every
cross-network feed stayed stale even though its code and KV binding were
healthy. **Root cause:** the publisher sent on every payload change; the
quota body includes a current-time field and minute countdowns, so it wrote
about every 30 seconds until Cloudflare KV's account-wide 1,000-write daily
free allowance was exhausted. The old arithmetic counted only five-minute
heartbeats and ignored real changes. **The rule:** a metered sink needs an
absolute rate ceiling, not only change detection. **Guards:** quotas are
capped at one write per five minutes and Max Tracker/GitHub at one per thirty
minutes; a regression simulates two continuously changing publishers for a
full day and requires at most 768 writes. **Watch for:** adding an endpoint or
shortening a ceiling without recomputing the account-wide two-publisher bound.

## 2026-08-22 · KV list requests have their own quota

**What happened:** after publisher writes were capped, the numbers Worker still
returned error 1101; Cloudflare identified quota code 10048. **Root cause:**
every panel GET called KV `list()`, so three endpoints at a 30-second cadence
spent 8,640 list operations per day. The KV list-request quota is independent
of KV read and write quotas; healthy `get()`/`put()` arithmetic said nothing
about it. **The rule:** budget every metered operation independently, including
discovery calls hidden inside reads. **Guards:** the active Worker contains no
KV operation and real-runtime tests make 100 repeated GETs without touching KV.
**Watch for:** replacing one explicit list with another discovery primitive and
counting only the document reads it leads to.

## 2026-08-22 · An eventually consistent index is not coordination

**What happened:** the first list-free design proposed one KV publisher-index
record so GET could fetch known keys directly. **Root cause:** KV is eventually
consistent, and registering a publisher is a read-modify-write operation. Two
concurrent first publishers can read the same old array, each write a different
replacement, and cause a lost update or displace a valid publisher. **The
rule:** a dynamic registry with capacity and atomic document storage needs one
strongly consistent owner, not an index convention. **Guards:** one
`NumbersMailbox` Durable Object serializes registration, counter, and document
storage in one SQLite transaction; real-runtime tests race eight publishers and
strictly reject the ninth. **Watch for:** any shared KV JSON record updated by
multiple writers, even when its maximum size is small.

## 2026-08-21 · One simulator pixel is not AMOLED-safe spacing

**What happened:** the shared Wi-Fi indicator passed pixel-count and placement
tests and looked plausible in the SDL simulator, but three bright rounded arcs
had only about one black pixel between their strokes. The physical AMOLED's
antialiasing and bloom merged them into one cloud-shaped blob. Its page header
divider also continued beneath the global slot, making the top-layer object
feel pasted over the screen. **The rule:** small bright status marks need
deliberate multi-pixel negative space at final native size, and shared chrome
needs a reserved lane in every underlying page—not merely a high z-order.
**Guards:** four muted 20×18 native assets render separated signal bands; the
raster test counts real connected components, while every app capture keeps
the eighteen-pixel lane to its left black. The one image is owned by the same
translated page shell as the header, so burn-in drift cannot make it appear
pasted above a page or takeover. **Watch for:** approving tiny rounded shapes
from enlarged simulator previews or testing only bounding boxes and total lit
pixels.
