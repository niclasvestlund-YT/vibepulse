# VibePulse interaction mailbox

This is the optional, user-owned Cloudflare Worker + SQLite-backed Durable
Object for encrypted Claude/Codex “Needs You” delivery. It is not the older
numbers relay. The service never receives a decryption key and its source has
no question, command, project, hook, or session schema.

The same Worker can also hold the independent, default-off Live agent status
relay: one fixed encrypted latest-value snapshot of the minimized
Claude/Codex rows. Project basenames and activity stay inside E2E ciphertext.

Start with the full privacy/setup guide:
[`docs/interaction-relay.md`](../../docs/interaction-relay.md).

## Prerequisites and local verification

- Node.js 22 or newer
- a Cloudflare account with Workers and SQLite Durable Objects available
- Wrangler authentication for the account that will own the mailbox

```sh
cd tools/interaction-relay
npm ci
npm test
npm run typecheck
npm run deploy:dry
npx wrangler login
```

The versions are pinned in `package-lock.json`. Do not substitute a global
Wrangler during setup; `tools/vibepulse_setup.py` resolves only the pinned
`node_modules/.bin/wrangler`.

## Recommended deployment

From the repository root, first enable at least one local provider and bounded
detail with the guided installer, then provide the HTTPS origin that this
Worker will use:

```sh
python3 tools/vibepulse_setup.py install
python3 tools/vibepulse_setup.py relay install \
  --url https://vibepulse-interaction-relay.YOUR-SUBDOMAIN.workers.dev \
  --yes-e2e-cloud
python3 tools/vibepulse_setup.py relay enable-status --yes-e2e-cloud
python3 tools/vibepulse_setup.py relay status
python3 tools/vibepulse_setup.py relay doctor
```

The relay installer generates a random mailbox identifier and separate random
Mac/panel bearer tokens, uploads only those two role tokens as Worker Secrets,
deploys with the mailbox ID, stores the Mac token in a private local file, and
writes the panel token/mailbox/origin into the generated block in gitignored
`secrets.h`. It never prints any credential.

Enabling the Worker does not enable the panel firmware task. That remains a
separate default-off Kconfig choice and requires a reviewed rebuild.

## API

All successful and error responses include `Cache-Control: no-store`. There
is no CORS allowlist. Authentication failures deliberately look like 404.

| Route | Credential |
|---|---|
| `PUT /v1/mailboxes/{box}/requests/{id}` | Mac token |
| `GET /v1/mailboxes/{box}/requests/next` | panel token |
| `POST /v1/mailboxes/{box}/requests/{id}/verdict` | panel token |
| `GET /v1/mailboxes/{box}/verdicts` | Mac token |
| `DELETE /v1/mailboxes/{box}/requests/{id}` | Mac token |
| `PUT /v1/mailboxes/{box}/status` | Mac token |
| `GET /v1/mailboxes/{box}/status` | panel token |

Bodies are canonical `{v,nonce,ciphertext}` JSON envelopes. Request
ciphertext is exactly 2,064 bytes (2,048 padded bytes plus GCM tag); verdict
ciphertext is exactly 1,040 bytes. The Durable Object keeps no more than eight
live rows and removes each no later than 120 seconds after first receipt.
Create and verdict retries must be byte-identical; conflicting bodies return
409.

Status uses a fixed **2,816-byte** authenticated plaintext frame plus its GCM
tag and one latest-value row. The host replaces it about every ten seconds;
the encrypted content expires after 20 seconds and the Worker removes the row
after no more than **20 seconds**. The Worker can see timing, connection IPs,
mailbox ID and fixed size, but it has no content key.

## Operations

```sh
python3 tools/vibepulse_setup.py relay status
python3 tools/vibepulse_setup.py relay doctor
python3 tools/vibepulse_setup.py relay disable
python3 tools/vibepulse_setup.py relay disable-status
python3 tools/vibepulse_setup.py relay uninstall --keep-worker
python3 tools/vibepulse_setup.py relay uninstall --delete-worker
```

`disable` stops tokenserver publication/polling but retains credentials for a
later restart. `uninstall --keep-worker` removes only local relay settings.
`uninstall --delete-worker` first deletes the deployment; if deletion fails,
the local credentials are preserved so the operation can be retried safely.

For rotation or revocation, delete the Worker and reinstall so all routing
credentials change. If the paired device key was exposed, rotate it on both
computer and panel before reinstalling.

Current platform prices and quotas are intentionally not copied here. Check
Cloudflare's official [Durable Objects pricing](https://developers.cloudflare.com/durable-objects/platform/pricing/),
[Workers limits](https://developers.cloudflare.com/workers/platform/limits/),
and [Durable Objects limits](https://developers.cloudflare.com/durable-objects/platform/limits/)
before deployment.
