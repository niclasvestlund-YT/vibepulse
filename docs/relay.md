# The numbers relay — numbers from anywhere

The panel could only fetch from the tokenserver when both sat on the same
LAN. One evening away from home proved how brittle that is: client
isolation, an IoT VLAN and a drifted IP each blanked the glass while
internet worked fine (`docs/lessons.md`, 2026-08-17 — three entries from
the same evening). The numbers relay removes the *same-LAN* requirement
without widening its own trust boundary: no key leaves your machines and this
numbers transport never carries activity. Encrypted Needs You is a different
default-off service with different credentials; see
[docs/interaction-relay.md](interaction-relay.md).

## The shape

```
 Mac (sleeps sometimes)  ──POST──►┐
                                  ├──►  mailbox (Cloudflare Worker + KV,
 PC (always on)          ──POST──►┘     your account, ~150 lines)
                                              │
                                              ▼  GET, any WiFi anywhere
                                          the panel
```

Both machines run the same tokenserver with the same flag:

```sh
python3 tools/tokenserver/tokenserver.py --publish "https://.../u/<secret>"
```

The panel keeps its LAN URL as the primary source and falls back to the
mailbox only when the LAN does not answer (`net_source_policy`, host-
tested). At home nothing crosses the internet; the moment the LAN dies —
travel, a sleeping Mac, a VLAN boundary — the numbers keep flowing.

## The boundary of this transport: numbers only

Held from three directions — firmware (`test/test_relay_boundary.py`),
service (`publisher.py` has no producers for the activity endpoints), and
the Worker (it only accepts the three number paths):

| Over this numbers relay | Not in this transport |
|---|---|
| `/api/tokens` — quota, burn rate | `/api/agent-status` — project names |
| `/api/max-tracker` — history | Needs You — question text, commands |
| `/api/github` — public repo stats | the device key's answer path |

The reason is the mailbox's trust model: access control is a secret URL —
the same level as a private share link. Right for percentages; wrong for
anything that names what you are working on. It therefore never gains an
activity producer or route. Users who explicitly choose remote decisions use
the separate Worker, which accepts only end-to-end encrypted fixed-size
envelopes and never reuses this secret URL.

## Multi-publisher: freshest wins per pool

Every quota pool already carries its own observation stamp
(`weekObservedAt`, `modelObservedAt`, … — built for the staleness logic).
The mailbox merges on read using exactly those stamps: Claude numbers come
from whichever machine asked Anthropic most recently; Codex from whichever
machine ran Codex last. No primary machine, nothing to configure — run the
publisher on both, the stamps sort it out (`tools/relay/test.mjs` holds
the merge still).

Honest limits:

- **Codex follows the CLI.** The numbers are account-wide but they are
  *written* only where Codex CLI runs. Mobile Codex use appears at the
  next CLI run on a publishing machine.
- **Max Tracker history is per machine** — the newest publisher's document
  wins whole. Day-by-day merging across machines would invent data no
  machine has seen.
- **A dark LAN means a quiet agent row.** Agent status never crosses either
  relay. A Needs You takeover can still arrive if its separate encrypted
  interaction relay is explicitly enabled.

## Setup

Deploy the Worker (once): [tools/relay/README.md](../tools/relay/README.md).
Then:

1. `--publish "https://.../u/<secret>"` on each machine that should feed
   the mailbox. On Windows, register it to survive reboots:
   `tools/tokenserver/install-windows-task.ps1 -PublishUrl "..."`
   (closes issue #3's autostart gap).
2. `TK_VIBEPULSE_RELAY_URL` in `secrets.h`, build, one OTA.

## What the glass shows away from home

| | |
|---|---|
| Claude quota + reset countdown | live — account-wide, any machine's probe sees everything |
| Burn rate, value multiple, Max Tracker | live |
| GitHub pulse | live |
| Codex quota | live while a publishing machine runs Codex |
| Agent row | dark — activity is not a numbers payload |
| NEEDS YOU | dark unless the separate encrypted interaction relay is enabled |

## Decisions worth remembering

- **The Anthropic probe's User-Agent is unchanged.** The publisher wears
  an honest `vibepulse-publisher/1` toward the mailbox (test-enforced),
  but the quota probe still presents itself as claude-cli. Changing that
  risks the probe against an undocumented endpoint and needs a live test —
  a deliberate, separate decision, not an oversight.
- **Write economy is load-bearing, and so is read economy.** KV's free
  tier bills write, delete *and list* from the same 1 000/day bucket —
  only reads get the generous 100 000. Send-on-change was never enough on
  its own: the payload carries countdowns that tick by themselves, so
  "changed" was true every minute, and the read path listed once per poll
  on top of that. Both are fixed and both are now bounded arithmetic
  rather than a hope — the tables live in `tools/relay/README.md` and
  `tools/tokenserver/publisher.py`. A "simpler" always-send loop, or a
  `list()` back in the read path, would exhaust the day by mid-morning
  (it did, 2026-08-21).
- **The mailbox is disposable.** It holds only the latest few JSON bodies.
  Delete the Worker and the panel falls back to LAN-only behaviour;
  rotate the secret by redeploying and updating two places.
