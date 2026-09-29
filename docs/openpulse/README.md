# OpenPulse (experimental Mac branch)

OpenRouter spend on the round Waveshare 1.75 (466 × 466). An optional **OPENROUTER** switch in VibePulse Labs adds the page while keeping
Claude Code, Codex and existing Labs choices. A separate Python service fetches
data; the panel receives only normalized numbers. The first
page puts month spend and the display budget inside the circular glass. Tap
the page indicator/background for key allowance and optional account credits.
Tap the key name to cycle configured keys. Touch on physical hardware is not
verified in this work.

![Native shared LVGL spend page, synthetic data](../img/openpulse/round-spend.png)
![Native shared LVGL details page, synthetic data](../img/openpulse/round-details.png)

## Enable on the round panel

In a build with `TORGET_OPENPULSE=ON`, hold BOOT for about three seconds, then
choose **LABS → MORE → MORE → OPENROUTER ON → RESTART NOW**. Swipe horizontally
between enabled provider pages. Tap OpenPulse's bottom indicator for spend or
details. OFF and RESTART NOW removes its page and poller. The normal VibePulse
app registry is retained; this is not a replacement app or a separate launcher.

Existing six- and eight-switch Labs records migrate without changing their
choices. OpenRouter is off by default. For this owner's explicitly requested
installation, the ignored `openpulse_panel_config.h` sets
`OPENPULSE_FIRST_INSTALL_ON=1` to seed it on once during migration, and
`OPENPULSE_START_ON_BOOT=1` to open it on boot when enabled. A later saved OFF
wins. No other provider switch is changed. Recovery to older firmware must use
the verified full backup, including its older Labs record.

## Quick start on this Mac

From this checkout, using Python 3.11+ (3.12 tested):

```sh
python3.12 -m tools.openpulse.service --demo
```

Open <http://127.0.0.1:8738>. Demo never reads credentials or contacts OpenRouter.
It explicitly labels synthetic values. Ctrl-C stops this foreground service.
No launchd job, Windows task, VibePulse config or existing installation changes.

For real data, launch the local connection page on a different port:

```sh
python3.12 -m tools.openpulse.service --connect \
  --config .openpulse/config.json --port 8739
```

Open <http://127.0.0.1:8739/connect>. Paste the **existing API key whose usage you
want to see**, enter a display name and monthly display budget, and save. A new
API key has its own new usage; it will not inherit another key's history. The
form saves secrets via macOS Security.framework in Keychain, under service
`org.openpulse.openrouter` and the configured key id. Keys never appear in
process arguments, firmware, fixtures, JSON configuration or request logs.
A denied/locked Keychain produces a safe error; it never falls back to plain text.

The connection form is optional, loopback-only and same-origin. It is absent in
demo mode and cannot be enabled while binding to the LAN. Its default uses a
single key. Browser login alone cannot supply the API secret; OpenRouter masks
previously created keys. Never paste secrets into chat.

The optional management-key field enables account credits. Start without it if
you only want key spend and allowance. A management key has broader privileges;
OpenPulse only issues GET /credits with it, never key-management mutations.
Saved config is non-secret and mode 0600. Changing provider API limits, buying
credits, making model requests and scraping browser sessions are out of scope.

## Configuration and isolation

`tools/openpulse/config.example.json` is the non-secret schema. Without an
explicit `--config`, live configuration is looked up at
`~/Library/Application Support/OpenPulse/config.json`. This task uses the
checkout's ignored `.openpulse/config.json`, with no VibePulse paths or services.

- Up to eight explicitly configured key ids; each has its own display budget.
- Default budget $50/month; warning at 75%, critical at 90%. Edit `warning_at`
  and `critical_at` in the config to choose thresholds. `monthly_budget_usd: null`
  disables the display budget. Reload the foreground process after config edits.
- The form intentionally starts with one key. Add further key entries to the
  config, and save each secret using the hidden terminal prompt below.
- Optional account view uses the separate Keychain account `management`.
- No local spend history or persisted source cache in v0.1. Restart starts with
  dashes until a new response, avoiding stale cache/credential mixups.
- The service polls each source every 60 seconds, with bounded 8-second network
  calls and 30–600 second error backoff. HTTP readers copy snapshots without
  waiting for provider I/O. Key and account freshness/error states are separate.
- Foreground only: no automatic start/sleep-resume certification yet.

```sh
python3.12 -m tools.openpulse.service --save-key default
python3.12 -m tools.openpulse.service --save-key management  # optional
```

The terminal prompt hides input. For temporary development only, a namespaced
`OPENPULSE_KEY_<ID IN UPPERCASE>` environment value can override a saved key;
never write it in shell history, Git or a launch script. Keychain is preferred.

## Meaning of the numbers (official documentation checked 2026-09-29)

| UI value | Source / scope |
| --- | --- |
| Today, week, month | [GET /key](https://openrouter.ai/docs/api/api-reference/api-keys/get-current-api-key): `usage_daily`, `usage_weekly`, `usage_monthly`, OpenRouter credit usage for **that key**, USD. UTC day/month; week Monday–Sunday. |
| Key remaining | `/key` `limit_remaining`, respecting provider reset/BYOK rules; never derived from lifetime usage. Explicit `limit: null` means unlimited; absent/invalid means unknown. Zero is a real zero. |
| Display budget | A local monthly reminder compared only with that key's monthly OpenRouter credit spend. Does not enforce or change a provider cap. |
| Account credits | [GET /credits](https://openrouter.ai/docs/api/api-reference/credits/get-remaining-credits), management key required: `total_credits - total_usage`. Separate account-wide balance, not a key allowance. Negative balance remains negative. |
| BYOK | `/key` external BYOK fields remain separate and are excluded from the display budget. Details state whether OpenRouter includes BYOK in the key cap. |

[GET /activity](https://openrouter.ai/docs/api/api-reference/analytics/get-user-activity-grouped-by-endpoint)
requires a management key and covers 30 **completed UTC days**. It is not live
activity and is not fetched in v0.1. Model breakdowns and forecasting are deferred.

A successful fetch is a recent observation of OpenRouter's counters, not proof
that every inference is instantly reflected. At 180 seconds readings are stale;
connection/auth failures are shown immediately while retaining last known values.
Current-period values become unknown across a UTC boundary until refreshed.
The panel also ages data locally if the host disappears. It does not advance
provider counters or simulate spend between polls. Source/account status is
independent of the platform's Wi-Fi association icon.

## Native preview and development checks

```sh
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt -r requirements-interaction-relay.txt
cmake -S sim -B sim/build-openpulse-round -G Ninja \
  -DTORGET_BOARD=waveshare_175 -DTORGET_BUILD_OPENPULSE_SIM=ON \
  -DTORGET_SOLELKOLLEN_DIR=/nonexistent \
  -DTORGET_WITH_BUDDY=OFF
cmake --build sim/build-openpulse-round --target openpulse-sim --parallel 2
sim/build-openpulse-round/openpulse-sim --url http://127.0.0.1:8738
.venv/bin/python -m tools.openpulse.preview --out .openpulse/round-previews
.venv/bin/python -m unittest tools.openpulse.test_openpulse
PYTHON_BIN="$PWD/.venv/bin/python" ./test/run.sh
```

The renderer and parser are identical C sources in the simulator and firmware.
Captures include the existing neutral Wi-Fi asset at the platform's actual
position. The fixture tool checks text widths and every pixel outside the
466px disk, on both pages and eight states. It produces sample data only, never
real account screenshots in the repository. Use the native macOS SDL driver;
this Mac's offscreen/dummy SDL exits before capture. Linux CI may use offscreen.

The legacy host visual test detects an installed Solelkollen companion under
HOME. On this Mac, the **legacy test simulator** must keep its default companion
path for those existing tests. The OpenPulse simulator and firmware explicitly
exclude companions; the Labs build retains VibePulse's normal application registry.

## Firmware build and authorized installation

The first target is `waveshare_175`; `waveshare_216` can also compile the same
composition, but the round profile is the reviewed layout. Other OpenPulse board
profiles fail early. Reuse Torget's existing board/display/Wi-Fi/HTTP support.
The standard VibePulse registry stays unchanged. The OpenRouter view and poller
are created only when the compiled-in Labs option is active.

```sh
cp secrets.h.example secrets.h                 # only in this fresh checkout
cp openpulse_panel_config.h.example openpulse_panel_config.h
# Edit only the local Mac origin in openpulse_panel_config.h, no OpenRouter keys.
. ~/esp/esp-idf/export.sh
idf.py -B build-openpulse-round -D TORGET_OPENPULSE=ON \
  -D TORGET_BOARD=waveshare_175 -D SDKCONFIG=sdkconfig.openpulse.175 \
  -D 'SDKCONFIG_DEFAULTS=sdkconfig.defaults;sdkconfig.defaults.175' \
  -D TORGET_SOLELKOLLEN_DIR=/nonexistent -D TORGET_WITH_BUDDY=OFF reconfigure
cmake --build build-openpulse-round --parallel 2
```

For an eventual physical connection, run the live service with an explicit
private LAN bind and without `--connect`. The endpoint is plaintext LAN HTTP
and contains financial summaries; use only a trusted private network, never
port-forward it to the internet. Setup must remain on loopback. The panel polls
the explicitly configured OpenPulse origin every ten seconds; it does not use
VibePulse discovery, relays or API keys. USB currently supplies power, not the
OpenPulse data transport.

Physical installation requires explicit authorization, exact ROM identity and
a verified full backup. USB-down display orientation, touch alignment, memory/network
soak and the whole physical data path need their separate acceptance. Existing VibePulse physical
verification is not inherited by OpenPulse. No release or Windows support claim.

The integrated round Labs test is `python test/test_openpulse_labs.py`. It drives
ON/OFF, the restart action, the existing Codex page and both OpenPulse pages
through shared LVGL. The policy tests cover all 512 masks, migration and durable
OFF behavior. `sim/build-openpulse-round/openpulse-sim` remains a focused design
preview; the actual panel runs the complete VibePulse app with the optional page.
