# The numbers relay — numbers from anywhere

The panel normally reads numbers directly from the tokenserver over the LAN.
That path cannot cross client isolation, an IoT VLAN, a sleeping computer's old
address, or unrelated Wi-Fi networks. The optional numbers relay supplies a
cloud fallback without widening the data boundary: it carries numbers only and
is off by default. This numbers transport never carries activity.

This is an open-source, user-owned feature. A fresh clone has no relay address,
Cloudflare account, namespace, or secret configured. Enabling it is a separate
choice from Claude interactions, Codex interactions, the encrypted interaction
relay, and the encrypted live-status relay. OAuth tokens, prompts, commands,
file contents, project names, and verdicts never enter this transport.

## Active architecture

```text
 Mac publisher ──POST──► public Worker ──RPC──► one SQLite-backed
 PC publisher  ──POST──► auth + validation       NumbersMailbox Durable Object
                               ▲                           │
 panel on any Wi-Fi ───GET─────┘◄──────────────────────────┘

 VIBEPULSE KV ── bootstrap and rollback only; inactive in worker.js
```

The public Worker owns the existing wire contract. It authenticates the secret
URL, accepts only `/api/tokens`, `/api/max-tracker`, and `/api/github`, validates
and bounds POST bodies, sanitizes publisher names, and returns the same JSON,
status codes, and content types the panel already understands. Every accepted
request selects one deterministic mailbox through the local
`NUMBERS_MAILBOX` binding.

The SQLite-backed `NumbersMailbox` Durable Object is the coordinator. It owns:

- a publisher registry capped at eight names;
- one latest document per endpoint and publisher; and
- a singleton monotonic receipt counter used to order whole documents.

Registration, counter increment, and document storage commit in one synchronous
SQLite transaction. A ninth publisher is rejected without moving any existing
publisher. GET reads the known document rows directly; one corrupt row is
skipped without hiding healthy publishers. The active `worker.js` request path
does not read or write KV and never calls KV list. The existing `VIBEPULSE`
binding and data are retained only for bootstrap and rollback.

The panel still prefers its direct LAN URL. It falls back to the public Worker
only when LAN access fails, so home traffic stays local when the direct source
is healthy and the same numbers remain available on any ordinary internet
Wi-Fi when it is not.

## The boundary: numbers only

The same boundary is enforced in three places: firmware
(`test/test_relay_boundary.py`), the tokenserver publisher, and the public
Worker's three-path allowlist.

| Over this numbers relay | Never in this transport |
|---|---|
| `/api/tokens` — quota, reset, burn rate | `/api/agent-status` — activity and project names |
| `/api/max-tracker` — numeric history | Needs You questions and commands |
| `/api/github` — public repository counts | answers, verdicts, or device keys |

Access control is a long secret URL, like a private share link. That is a
reasonable boundary for percentages and public counts, not for work content.
Remote questions, verdicts, or live activity require the separate explicit
end-to-end encrypted features described in
[docs/interaction-relay.md](interaction-relay.md). Enabling this numbers relay
never enables either encrypted activity feature.

Because the URL *is* the credential, the panel treats it as one. A failed
fetch used to log the whole address, so a serial capture or a pasted
observability transcript handed over both read and write access to the
figures. `torget_http.c` now sends every failure line through
`tg_net_log_target()` (`components/torget_net/net_log_target.c`), which keeps
what diagnoses — scheme, host, and whether LAN or the relay was tried — and
drops the path, the query, the fragment and any userinfo. The redaction is
unconditional rather than relay-only, so a future call site inherits it
instead of having to remember it; `test/test_net_log_target.c` and the log
guard in `test/test_relay_boundary.py` hold both halves of that.

## Multiple publishers

Every quota pool carries its own observation timestamp
(`claudeWeekObservedAt`, `claudeModelWeekObservedAt`,
`codexWeekObservedAt`, and similar fields). `/api/tokens` merges each pool from
the publisher that observed that pool most recently. Claude numbers can
therefore come from an always-on PC while Codex numbers come from the Mac where
Codex ran. Cached stale values retain their original observation time, so a
recently published old cache cannot outrank a genuinely newer reading.

Max Tracker and GitHub retain whole-document semantics. For those endpoints,
the mailbox-owned receipt counter makes the most recently stored publication
win. Max Tracker history is per machine; combining days from different
publishers would invent a history no machine observed.

The honest limits remain:

- Codex quota updates only after Codex runs on a publishing machine.
- A computer must be awake and its tokenserver must be running to publish.
- Agent activity remains local unless its separate encrypted relay is enabled.

## Daily successful-request and conservative row budget

The panel polls each of three endpoints every 30 seconds. One day therefore has
2,880 poll cycles and 8,640 panel GETs. Every GET is one public Worker request
and one Durable Object RPC.

Successfully admitted cloud publications have independent hard ceilings, even
when payload fields change on every local 30-second check:

| Endpoint | Minimum interval | Maximum per publisher per day |
|---|---:|---:|
| `/api/tokens` | 5 minutes | 288 token publications |
| `/api/max-tracker` | 30 minutes | 48 Max Tracker publications |
| `/api/github` | 30 minutes | 48 GitHub publications |
| **Total** | | **384 publications** |

Two publishers can therefore make at most 768 publications per day. On the
healthy-success path, one continuously changing publisher makes 9,024 public
Worker requests and 9,024 Durable Object RPCs per day; two make 9,408 of each.
A failed publication leaves its send time unchanged and can retry on the next
30-second publisher check, so failed-attempt traffic is not bounded by those
healthy-success totals. A sustained failure can attempt each of the three POSTs
on every check until the relay accepts them.

With complete endpoint coverage, the bounded GET queries return at most 8,640
document rows per day for one publisher or 17,280 document rows for two. Eight
publishers is the hard upper bound, so neither storage nor reads can grow with
untrusted publisher names.

Every admitted publication changes two application-table rows: the monotonic
counter and one document. Cloudflare's
[SQLite billing rules](https://developers.cloudflare.com/durable-objects/api/sqlite-storage-api/)
also count index maintenance as billed row writes. For a conservative ceiling,
allow a data row and its primary-key index row for each change: up to four
billed row writes per publication, plus up to two when a publisher is first
registered. These are conservative upper bounds, not exact billed-row counts.
The actual SQLite primary-key index rows updated depend on the statement and
table layout.

At the scheduled maximum, that budget remains under 1,600 billed row writes per
day for one publisher and under 3,100 billed row writes for two, including
their first registrations. Both are far below the SQLite Durable Object
100,000-row daily free limit (see Cloudflare's
[current pricing](https://developers.cloudflare.com/durable-objects/platform/pricing/)).
Schema initialization and internal metadata are small one-time costs, not part
of those daily publication estimates. These are SQLite operations, not KV
writes; the active path spends no KV operation quota.

## Setup and the two-stage lifecycle

The exact private configuration and guarded commands live in
[tools/relay/README.md](../tools/relay/README.md). The sequence matters because
Cloudflare cannot roll a Worker version backward across a Durable Object class
lifecycle creation.

1. Keep the existing real `VIBEPULSE` namespace and secret. Deploy
   `bootstrap.js` first with the local `NUMBERS_MAILBOX` binding and sole
   SQLite `NumbersMailbox` export. The class is created, but the public request
   path still uses the old KV data.
2. Verify the old public contract and capture the bootstrap version. This is
   the only emergency rollback target after class creation.
3. Change only the private config's `main` value and switch to `worker.js`.
   Run the guarded dry build before the guarded deployment.
4. The new Durable Object mailbox starts empty; restart each active tokenserver
   so its immediate first publish pass sends all three endpoints:
   `/api/tokens`, `/api/max-tracker`, and `/api/github`.

Never roll back directly to a pre-Durable-Object version. If the `worker.js`
stage fails, rollback only to the captured bootstrap version, which is already
on the created-class side of the lifecycle boundary. Bootstrap deliberately
restores the old KV list/get/put behavior, including its exhausted list-quota
failure mode. Do not delete or rewrite the retained KV data.

## Recovery checks

After activating `worker.js` and restarting publishers:

1. Confirm the local tokenserver reports `usage_http_200 + ok` and that its
   Claude fields are not stale.
2. Fetch all three secret cloud URLs and compare only the non-secret numeric
   fields with the local endpoints. Never print the secret URL in logs or a
   shared transcript.
3. Wait one panel polling interval (30 seconds) and confirm `STALE` clears on
   the glass while LAN failover is active.
4. If the mailbox returns 503, use the Worker's sanitized `publish` or `read`
   diagnostic. It intentionally contains no secret, publisher, body, or RPC
   error text.

### Repairing corrupt mailbox state

A corrupt document normally self-heals on that publisher's next valid
publication. Reads already isolate malformed document JSON, so restart the
affected tokenserver and check the three endpoints before touching storage.

If a bad row continues to block publications, use Cloudflare's official
[Durable Objects Data Studio](https://developers.cloudflare.com/durable-objects/observability/data-studio/).
It requires the Workers Platform Admin role. In the Cloudflare dashboard, open
the `NumbersMailbox` Durable Object namespace, start Data Studio, and identify
the single object by its unique name, `numbers-mailbox-v1`. Inspect the
`publishers`, `documents`, and `mailbox_state` tables, repair only the confirmed
corrupt row, then restart the affected tokenserver so valid values are
republished. Data Studio operates on the remote object, consumes normal usage,
and audit-logs its queries, so keep the change narrow and reviewed.

Do not delete the entire Durable Object or all mailbox rows. If scoped repair
is unsafe or the damage cannot be identified, use a reviewed fresh mailbox-name
rollout instead: change the deterministic mailbox name in reviewed code, deploy
the `worker.js` change through the guarded dry run and deployment, and restart
publishers to fill the new empty object. Preserve the old object until the
replacement is verified.

No firmware or OTA change is required for this storage repair. The public URLs
and response bodies remain unchanged.

## Operational decisions worth keeping

- The publisher identifies itself honestly as `vibepulse-publisher/1`; the
  separate Anthropic probe keeps its tested Claude CLI user agent.
- Change detection reduces ordinary traffic, but the endpoint ceilings above
  are the hard safety boundary. Tokens can publish at most every 5 minutes;
  Max Tracker and GitHub can publish at most every 30 minutes.
- The mailbox data is replaceable, but the Worker lifecycle is not disposable
  after class creation. Preserve the bootstrap version and retained KV data
  for as long as rollback to the old request path is required.
