# Lovable Pulse

An optional VibePulse page for people who build with Lovable: credits left,
plan and how fresh the number is. It sits in the same carousel as
Claude and Codex and leaves those pages unchanged.

## Flow

```text
Lovable MCP (get_workspace, OAuth, read-only)
        │  every few minutes, on the Mac
tokenserver --lovable  ── /api/lovable (numbers only, LAN) ──▶ panel, every 60 s
```

- **Source:** the official MCP server `https://mcp.lovable.dev`. Login uses
  OAuth 2.1 with PKCE, dynamic client registration and a loopback redirect;
  scopes `workspaces:read offline`. `list_workspaces` runs once at login to pick
  a workspace; after that only `get_workspace` is called.
- **Secrets:** tokens live in the macOS Keychain (service
  `se.torget.vibepulse.lovable`, account `oauth`). The panel never gets them,
  and the relay never publishes Lovable data.
- **Honesty:** only values the source actually returns are shown. When
  `get_workspace` names a total grant and a reset/expiry date, the Mac
  forwards `grantTenths`, `resetSeconds` and (with a period start)
  `periodSeconds`; square panels can show `OF 100 · RESETS IN 61 DAYS` and
  count down themselves between polls. Without those fields: balance only.
  A separately named daily balance appears as `5 DAILY LEFT`; a named daily
  allowance without a balance appears as `5 DAILY INCLUDED`. The value is
  never inferred from `PRO` or another plan name, so other plans work without
  hard-coded assumptions. Old values are marked `CACHED`; missing ones show a dash.

## Round 1.75 glass

The round board is deliberately simpler:

- **Large number:** credits left in the main period.
- **One orange bezel ring:** time left until reset, of the whole period. It is
  only shown when both reset and period values are explicitly available.
- **Compact daily row:** separately named daily credits left. It has no ring
  and stays hidden when the source does not provide a daily value.

The workspace name and reset sentence are omitted from the round face. The
plan sits below the Lovable identity, while freshness has its own quiet row.

The ring drains clockwise from twelve and dims for `CACHED`. A full ring keeps
a tiny gap rather than hiding its end cap. Square boards retain the detailed
text layout.

## Payload

```json
{"v":1,"enabled":true,"stale":false,"creditsTenths":12400,
 "ageSeconds":130,"plan":"PRO","workspace":"NICLAS STUDIO"}
```

Optional, only with a balance and only when named by the source:
`"grantTenths":1000,"resetSeconds":5270400,"periodSeconds":7862400`,
`"dailyCreditsTenths":50,"dailyGrantTenths":50`.

On the round face, a positive daily balance is pink and zero is neutral gray.
The row only shows the daily amount left; usage is not displayed.

Before the first read, `creditsTenths`/`ageSeconds` are left out; `"login":true`
means the Mac needs `lovable_monitor.py login`. The panel parser rejects any
other field.

## Setup

1. `python3 tools/tokenserver/lovable_monitor.py login`
2. Start the tokenserver with `--lovable` (or `VIBEPULSE_LOVABLE=1` in launchd).
3. In `secrets.h`, add `TK_LOVABLE_URL` (see `secrets.h.example`), build and
   flash as usual.
4. On the panel: SETTINGS → LABS → MORE → LOVABLE PAGE ON.

## Preview

```sh
TORGET_LABS_MASK=32 sim/build/torget-sim --vibepulse-lovable-qa
# round glass:
cmake -S sim -B sim/build175 -G Ninja -DTORGET_BOARD=waveshare_175
cmake --build sim/build175
TORGET_LABS_MASK=32 sim/build175/torget-sim --vibepulse-lovable-qa
```

Writes waiting, login, live, cached, daily-empty, large and small 480×480 captures from
`sim-fixtures/lovable*.json`.
