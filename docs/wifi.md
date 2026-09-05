# WiFi on the road — how the panel follows you

The panel used to carry exactly two networks, both compiled into
`secrets.h`. A new place meant editing a header, rebuilding, and flashing
over USB — and OTA could not help, because OTA needs the network the panel
cannot reach. This is the reference for what replaced that.

## The short version

```
new place, panel finds nothing
        │
        ├─ 60 s ─► the glass says WHY (network hunted, radio's own reason)
        │
        ├─ 90 s ─► the panel raises VibePulse-setup and shows a clean QR screen
        │          (3 s KEY3 hold → SETTINGS → WIFI opens it at once)
        │
        ├─ from a phone:    scan the QR, portal opens ← normal path
        └─ optional Mac:    tools/wifi-here.sh        ← one-command shortcut
                │
                └─► remembered in NVS. Next time you are here, it just joins.
```

Six places are remembered. The one that worked most recently is tried
first, and the least recently working one is evicted when a seventh
arrives. The networks in `secrets.h` are appended underneath as an
**immutable floor**: the setup window can add places, never remove your
home network. That is what keeps a bad entry from turning into a USB
rescue.

<p align="center">
  <img src="img/vibepulse-wifi-searching.png" width="31%" alt="The panel explains that a saved hotspot is not visible and that it needs 2.4 GHz Wi-Fi">
  &nbsp;
  <img src="img/vibepulse-wifi-setup.png" width="31%" alt="The temporary VibePulse Wi-Fi setup network with a phone-scannable QR and one Manual Setup control">
  &nbsp;
  <img src="img/vibepulse-wifi-signal.png" width="31%" alt="The launcher with the global neutral three-bar Wi-Fi indicator">
</p>
<p align="center"><em>Exact 480×480 captures from the same shared LVGL trees compiled into the panel firmware—not design mockups.</em></p>

## What actually changed on the road

Four things about travel that the old firmware got wrong:

| Before | Now |
|---|---|
| Two networks, compile-time | Six remembered in NVS + the compiled-in floor |
| `threshold.authmode = WIFI_AUTH_WPA2_PSK` for every network — open café networks were refused in silence | The threshold follows each network: open where the password is blank |
| No network → dashes forever, no reason anywhere | The glass names the network it is hunting and what the radio answered |
| A new place meant rebuild + USB flash | One command on the Mac, or a phone and the panel's own portal |

## The two ways in

### Recommended: from a phone — the portal

1. **Scan the QR** shown on the glass. It contains only the temporary
   `VibePulse-setup` access-point name and temporary password. It never embeds
   the destination network password. The primary screen shows no credential
   clutter; **Manual Setup** reveals the temporary name, password, and
   `192.168.4.1` only when the fallback is needed.
2. The panel's DNS responder points captive-portal checks at the local setup
   page. If iOS or Android does not open it, browse to
   `http://192.168.4.1/`. **Not Secure is expected**: there is no certificate
   because this is a ten-minute local page served directly by the panel, with
   no internet route.
3. Pick the destination network and press Join once. For a secured selection,
   the password field says `Password for <network>` and is required. For an
   open network the password field is hidden and the page says that no
   password is required. The list is **strongest first, and it is the panel's
   radio that decides**. The ESP32-S3 supports 2.4 GHz only; it cannot hear a
   5 GHz-only network even when the phone shows full signal.
4. Leave the page open while the glass says JOINING. The browser follows a
   small secret-free status endpoint and reports whether the panel connected
   or needs another try.

The submitted SSID/password is a temporary trial first. It is copied to NVS
**only after the panel connects** and receives a fresh IP using that exact
trial. A stale IP from the previous network cannot validate it. On a wrong
password, missing network, timeout, reset, or power loss, the trial is not
saved; **old saved networks remain available**, including the immutable
`secrets.h` floor. Open networks are supported automatically; their password
field is hidden because no password is required.

If QR generation ever fails, the manual details view appears automatically
with the setup SSID, temporary password, and `192.168.4.1`; recovery does not
depend on the QR renderer.

### Optional: from a Mac — `tools/wifi-here.sh`

One command. It reads the Mac's current SSID, pulls that network's password
out of the **system keychain** (macOS prompts you — that prompt is the
consent), hops to the panel's access point, hands the credentials over, and
releases the Mac's WiFi. The Mac is offline for roughly twenty seconds. This
is a convenience; the phone flow above is the universal setup path.

The access point's password is **derived** from `TG_OTA_TOKEN` in
`secrets.h` — `sha256("vibepulse-softap-v1" + token)`, first 12 hex
characters — so the script computes it without reading anything off the
glass. This grants nothing new: whoever holds that token can already write
firmware to the panel. Without the token the password is random per window
and lives only on the screen; pass it in explicitly:

```sh
TG_AP_PASS=<what the glass shows> tools/wifi-here.sh
```

`test/test_wifi_setup_wiring.py` asserts the domain string, the digest
length and the AP name match between the firmware and the script. They
cannot drift apart silently.

### What the top-right Wi-Fi symbol means

The one neutral 28-pixel symbol is shared by the launcher, every app, Needs
You, OTA, and Wi-Fi setup:

- slash + faint silhouette: disconnected;
- one, two, or three bright bars: local access-point signal strength;
- complete bright symbol while the setup window is open: setup mode.

It deliberately **does not mean internet** access, DNS success, tokenserver
reachability, Cloudflare relay health, or that the destination join has
already succeeded. It stays neutral white/grey rather than borrowing Claude,
Codex, or app colors. The boot screen hides it until the normal UI is ready.

## The consent model

The OTA window's three factors (physical presence, knowledge, time) are
unchanged. The setup window inherits two of them and deliberately relaxes
one:

1. **Physical presence** — the access point's password is on the glass.
   Whoever cannot see the screen (or hold `secrets.h` on their Mac) cannot
   get in.
2. **Time** — ten minutes, then it closes itself and hands back every byte
   it cost. The AP, the HTTP server and the DNS task do not exist outside
   an open window (the lazy-surface rule from the 2026-08-14 freeze).
3. **The window may open itself** after 90 s without an IP. This weakens
   nothing: with no network there is no remote that could have opened it,
   and a panel in a hotel room should not require knowing a secret gesture
   to become useful again.

**The setup window can never write firmware.** It touches the network list
in NVS and nothing else; OTA keeps its own gate and its own token. The
wiring test asserts no OTA symbol ever appears in `wifi_setup.c`.

### What KEY3 means now

A 3 s hold opens **SETTINGS**, and you pick:

```
hold KEY3 ~3 s  →  SETTINGS
  ├─ UPDATE   the OTA maintenance window, unchanged
  ├─ WIFI     this setup window, unchanged
  └─ ABOUT    firmware version and address (a dash for either if unknown)
```

The hold used to guess from network state; now it asks. **Without an IP,
UPDATE is greyed out and cannot be picked** — an OTA window with no
address could never receive an upload. UPDATE is the only row that goes
dark, so WIFI is the one that can fix it, which is exactly where a stranded
panel needs to go. ABOUT shows the address as a dash, so the reason is on
the glass rather than implied.

**Hold again to switch windows.** A second full 3 s hold while the update
window is open closes it and opens WIFI SETUP instead. That is how you
teach a panel that already *has* a network a new one — pre-loading the
phone hotspot at home before a trip, for instance — without waiting for it
to be stranded first. The port-80 handover is owned by the setup guard, so
the two HTTP servers never collide.

While either window owns the glass, *any* KEY3 release before three
seconds closes it — the same escape hatch the OTA window grew after
2026-08-16. Only a deliberate, completed hold switches; you cannot fail
to close by pressing. SETTINGS inherits the same rule, and goes further:
*any* KEY3 event closes the menu, completed hold included, so it can
never stack on top of itself.

## Where the credentials live

NVS, one blob, namespace `tgwifi`, key `slots`. One write, one commit — no
half-written list if power drops mid-save. A blob of the wrong size is
treated as empty rather than parsed at the wrong offsets, so an older or
newer format degrades to "run on the `secrets.h` floor" instead of
misreading.

**NVS is not encrypted.** The passwords sit in flash in the clear. That is
the same exposure `secrets.h` already had (the README says it plainly: a
lost screen leaks your WiFi password) — not a new class of risk, but the
reason a lost panel means rotating those networks. OTA never writes NVS, so
the list survives every update.

## What this does *not* fix

Being honest about travel networks, because the failure modes are not
firmware bugs:

- **Captive portals.** The panel cannot click "I agree". Most hotel and
  café networks stay out of reach no matter how easy joining them is.
- **Client isolation.** Plenty of guest networks block device-to-device
  traffic, so the panel associates fine and still cannot reach
  `your-mac.local:8737`.
- **WPA2-Enterprise.** Office and campus networks with a username are not
  handled.
- **5 GHz.** Still invisible to the ESP32-S3, forever. The setup portal's
  list is the panel's own truth about what exists.

**The network that always works on the road is the one you bring.** Turn on
the phone hotspot (iPhone: *Maximize Compatibility*, or it broadcasts only
5 GHz), put the Mac on it, and run `tools/wifi-here.sh` once. The panel
remembers the hotspot from then on and rejoins it in every city.

For the reachability half of these failure modes — the panel is *online*
but cannot reach the service across a network boundary — two independent,
default-off relays exist. The numbers relay carries quota/Max Tracker/GitHub
data. The interaction relay carries only fixed-size, end-to-end encrypted
Needs You views and verdicts, so its mailbox cannot read the question text.
Both sides make outbound HTTPS connections; no router change or public Mac is
needed. See `docs/interaction-relay.md` for the explicit privacy opt-in.

## Physical verification status

Per `spec/hardware.md`'s rule about claiming hardware truth:

| Capability | Silicon | Board | Firmware | Verified on `torget-home-01` |
|---|---|---|---|---|
| 2.4 GHz station mode | yes | yes | yes | yes (2026-08-06) |
| SoftAP / APSTA | yes | yes | yes | portal reached from a phone (2026-08-21); candidate QR/join still pending |
| NVS read/write | yes | yes | yes | yes (boot-health probe) |

The first physical attempt on 2026-08-17 wedged twice after a KEY3 hold-hold
from the OTA window and was rolled back over USB. A later phone test reached
the panel's portal, proving the SoftAP/DNS/HTTP path can start, but it did not
verify the current QR screen, destination join, remembered-network reboot, or
global icon. Those remain explicit candidate checks rather than inferred
successes.

Since then `window_open()` is bracketed by two host-tested DMA gates
(`tg_wifi_setup_dma_ok_to_open` / `_continue`): it refuses to open unless
the largest free DMA block clears 3x the flush (calibrated against the measured healthy baseline of 40-47 kB on v0.5.0), aborts and tears down if
the post-APSTA measurement falls under 2x, and logs the largest block at
every stage so the next incident names the hungry step. The 196×196 I1 QR
canvas itself is 4,908 bytes (4,900 pixel bytes plus its 8-byte palette) and
is created once, then reused. **The gates and code-derived canvas size are
defensive evidence, not a full verification** — the current candidate still
needs a supervised run with serial attached to record LVGL free/largest,
internal free/low-water, DMA before/after APSTA/portal, and the QR encode peak.

## Files

| Path | What it owns |
|---|---|
| `components/torget_wifi/wifi_slots.c` | Pure policy: validation, eviction, candidate order, window timing. Host-tested. |
| `components/torget_wifi/wifi_form.c` | Pure parsing: percent-decoding, form fields, HTML escaping. Host-tested. |
| `components/torget_wifi/wifi_creds.c` | The NVS blob, and nothing else. |
| `components/torget_wifi/wifi_setup.c` | The window: AP, portal, DNS responder, guard task. |
| `components/torget_wifi/wifi_setup_ui.c` | The glass: the honest network screen and the setup screen. |
| `main/main.c` | Still owns the radio. Builds the candidate list, routes KEY3. |
| `tools/wifi-here.sh` | The Mac's one command. |
