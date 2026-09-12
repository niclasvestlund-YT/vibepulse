VibePulse v1.1.0 adds on-screen settings, keeps live quotas available while usage history loads, and makes failures easier to diagnose.

> **Verification status:** The new firmware is CI-built, **not yet flashed or physically verified**. `torget-home-01` still runs `v1.0.0-25-g054db68`. The Windows v1 host claim — core service, physical answer loop, and sign-in/sleep/reboot lifecycle — remains pinned to the v1.0.0 runtime `bee5d8c` and is **not inherited** by v1.1.0.

> **Correction, 2026-09-12:** the inventory this note was cut from was stale. `torget-home-01` had been flashed over USB on 2026-09-06 with `v1.0.0-67-ge51b79f`, a pre-release build that carries SETTINGS; its static on-panel review has not been run, so SETTINGS is on the glass but unverified. Coredump, the reboot ledger, poller backoff and the warm-up placeholders landed after that build and remain **not yet flashed**.

## Settings from the panel

Hold **KEY3** for three seconds to open **SETTINGS**:

- **UPDATE** opens the ten-minute firmware update window.
- **WIFI** starts setup so you can connect the panel using your phone.
- **ABOUT** shows the firmware version and network address.

UPDATE is disabled when the panel has no network address; WIFI stays available to help you connect. A short KEY3 press closes the menu. Opening a maintenance window still requires interaction on the device.

<p align="center">
  <img src="https://raw.githubusercontent.com/niclasvestlund-YT/vibepulse/v1.1.0/docs/img/vibepulse-settings-menu.png" width="40%" alt="Simulator: SETTINGS with UPDATE, WIFI and ABOUT available">
  &nbsp;
  <img src="https://raw.githubusercontent.com/niclasvestlund-YT/vibepulse/v1.1.0/docs/img/vibepulse-settings-no-address.png" width="40%" alt="Simulator: SETTINGS without a network address; UPDATE is disabled and WIFI remains available">
</p>
<p align="center"><em>Simulator captures: connected (left), no network address (right).</em></p>

## Live quotas while history loads

Restarting the host service used to leave the panel waiting while a large usage history was scanned. The service now answers immediately and loads that history in the background.

With the updated firmware, live quota rings can refresh during that scan. Volume counters remain placeholders until measured, so an unfinished scan cannot turn into misleading zeros. Existing firmware keeps its last good values and may still show **STALE** during startup; the improved display behaviour requires the firmware update.

## Clearer diagnostics and recovery

The new firmware adds a **coredump partition** for crash evidence and a **reboot ledger** in NVS to record restarts. Poller backoff reduces repeated requests to an unavailable service, and pinned logging settings keep diagnostics consistent across builds. These changes share the unflashed status above.

The host also gets several practical improvements:

- Corrupt usage-history files are preserved for recovery instead of silently overwritten.
- Claude credential errors and probe status give clearer explanations when data is unavailable.
- Agent rows display readable model names and the recorded effort level.
- Tokenserver logs, help, installer messages and documentation now read in English. Update any saved log searches; the [observability guide](https://github.com/niclasvestlund-YT/vibepulse/blob/v1.1.0/docs/observability.md) lists the current signatures.

## Upgrade

In the checkout used by your host service:

```sh
git fetch --tags origin
git switch --detach v1.1.0
python3 tools/vibepulse_setup.py status
```

Restart the service to load the new code: on macOS, run `launchctl kickstart -k gui/$(id -u)/se.torget.tokenserver`; on Windows, follow the [stop, wait, then start instructions](https://github.com/niclasvestlund-YT/vibepulse/blob/v1.1.0/docs/windows-setup.md#restarting-the-scheduled-task). Then run `python3 tools/tokenserver/smoke.py` to check the running service.

Firmware installation remains a separate, explicitly authorized step. Follow the [flash-session run sheet](https://github.com/niclasvestlund-YT/vibepulse/blob/v1.1.0/docs/flash-session-2026-09.md), but replace the run sheet's `main` checkout with `git switch --detach v1.1.0` so you build this release. The coredump partition also needs a one-time USB `partition-table-flash`: OTA does not update the partition table.

This release is **source-only**. **Do not attach `torget.bin`**: local firmware builds contain Wi-Fi credentials and may contain a private device key.

[Full changelog](https://github.com/niclasvestlund-YT/vibepulse/blob/v1.1.0/CHANGELOG.md) · [Compare v1.0.0...v1.1.0](https://github.com/niclasvestlund-YT/vibepulse/compare/v1.0.0...v1.1.0) · [Windows validation procedure](https://github.com/niclasvestlund-YT/vibepulse/blob/v1.1.0/docs/windows-validation.md)
