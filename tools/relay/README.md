# The relay mailbox

A ~150-line Cloudflare Worker that lets the panel fetch its numbers from
anywhere, instead of only from the same LAN as the service. Full design
rationale and what may/may not cross it: [docs/relay.md](../../docs/relay.md).

## Deploy (once, ~10 minutes)

Requires a free Cloudflare account and `wrangler` (`npm i -g wrangler`).

```sh
cd tools/relay
wrangler kv namespace create VIBEPULSE      # note the id it prints

cat > wrangler.toml <<EOF
name = "vibepulse-relay"
main = "worker.js"
compatibility_date = "2026-08-01"

[[kv_namespaces]]
binding = "VIBEPULSE"
id = "<the id from above>"
EOF

python3 -c "import secrets; print(secrets.token_hex(32))"   # the secret
wrangler secret put RELAY_SECRET            # paste the secret
wrangler deploy                             # prints the workers.dev URL
```

Your mailbox address is then:

```
https://vibepulse-relay.<your-subdomain>.workers.dev/u/<the secret>
```

The secret never lives in code or in this repo — it exists in the Worker's
secret store, in your `secrets.h`, and in the service's start command.

## Wire it up

**Service side** (each machine that should publish — one or several):

```sh
python3 tools/tokenserver/tokenserver.py --publish "https://.../u/<secret>"
```

**Panel side** (`secrets.h`, then build + OTA once):

```c
#define TK_VIBEPULSE_RELAY_URL "https://.../u/<secret>"
```

## Verify

```sh
curl -s https://.../u/<secret>/api/tokens | head -c 200   # JSON within 30 s
```

`wrangler tail` shows requests live. The `test.mjs` suite
(`node --test tools/relay/test.mjs`) holds the merge logic still without
any Cloudflare involvement.

## Free-tier arithmetic

KV's free tier is **three** allowances, not one, and only one of them is
generous:

| operation | free per day | what uses it |
|---|---|---|
| read | 100 000 | every panel poll, plus the publisher index |
| write | 1 000 | every publish |
| **list** | **1 000** | *nothing, now* — see below |

The `list` line is the one that bites. Cloudflare bills write, delete and
list in a single class, so a listing costs the same scarce operation a
write does. The read path used to list once per GET, and the panel polls
`/api/tokens` and `/api/github` every 30 s and `/api/max-tracker` every
5 min: **~6 000 listings a day against an allowance of 1 000.** The Worker
keeps a per-endpoint publisher index instead, so a GET is reads only, and
the only listing left runs once per endpoint on a mailbox that predates
the index.

The write side is bounded by construction rather than by hope
(`tools/tokenserver/publisher.py` carries the full table):

| endpoint | floor between sends | heartbeat | quiet day |
|---|---|---|---|
| `/api/tokens` | 180 s | 15 min | 96 |
| `/api/max-tracker` | 600 s | 30 min | 48 |
| `/api/github` | 600 s | 30 min | 48 |

That is 192 writes on a day where nothing happens, and a hard daily budget
of **400** where a busy day stops — so two machines publishing to the same
mailbox land at 800 of the 1 000. A third does not fit the free tier; give
each a lower budget or move to the paid plan.

Reads land at ~12 000/day for one publisher (an index read plus a document
read per poll), comfortably inside 100 000.

Two things the numbers depend on, worth knowing if you change either: the
countdowns (`claudeWeekResetMin` and friends) are aged by the **Worker at
read time**, not republished every minute — that is what lets the heartbeat
be 15 minutes without the glass drifting. And the mailbox reads at most
eight publishers per endpoint; a ninth is stored but never served.

**Changed the Worker?** `wrangler deploy` again — the running Worker keeps
the old code, and the arithmetic above is only true once the deployed
version is the one in this directory.
