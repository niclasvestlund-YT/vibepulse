# VibePulse display onboarding: next design

The 1.8 V2 port closed a useful hardware loop and exposed a product gap: a
verified board is still not easy to identify, install and connect for a new
owner. The 1.8 unit's owner specifically found the iPhone QR → temporary Wi-Fi
→ captive portal flow cumbersome.

## Proposed first-run journey

1. **Scan one permanent QR on the package or board.** It identifies the exact
   model/revision and opens a mobile-first VibePulse page with the verified
   setup guide, latest board status and troubleshooting. The QR contains no
   Wi-Fi password, serial number or unique device secret.
2. **Install with guided board detection.** The page asks for model/revision
   when it cannot infer it. On a computer, prefer a browser installer where
   supported; otherwise give one copyable command for the exact firmware
   profile. Always show a backup step before a write and verify the connected
   target.
3. **Provision Wi-Fi in one focused screen.** Prototype Espressif's BLE
   provisioning transport and a branded iOS companion flow. Keep the user in
   the app, discover the nearby panel, list visible 2.4 GHz networks, accept
   credentials and show connection progress on both phone and display. Keep
   the current SoftAP portal as fallback.
4. **Verify the real service path.** A final screen confirms Wi-Fi, host
   discovery/reachability and fresh data separately, and points to the one
   likely next fix instead of ending at “connected”.

## iOS constraint and design implication

Apple documents that both persistent and join-once Wi-Fi configuration
operations require the user's explicit authorization. A QR code cannot
silently switch networks. Espressif's BLE provisioning path can keep the user
inside a purpose-built phone app instead of sending them to iOS Settings to
join the panel's temporary SoftAP; it still cannot suppress Apple's approval
for changing Wi-Fi settings. See [Apple Wi-Fi configuration](https://developer.apple.com/documentation/networkextension/wi-fi-configuration)
and [Espressif unified provisioning](https://docs.espressif.com/projects/esp-idf/en/v5.5/esp32/api-reference/provisioning/wifi_provisioning.html).

The QR should therefore do useful work before provisioning (exact-board
selection, setup help, installer) and clearly explain the one OS approval
that remains. Do not promise a silent or one-scan Wi-Fi connection.

## Acceptance criteria for a later implementation

- A first-time iPhone owner reaches a connected panel without manually
  guessing the model, SSID or portal address.
- Network choice, consent, progress, timeout and retry are visible and
  accessible. The user can cancel without losing saved networks.
- Provisioning credentials use Espressif's authenticated provisioning
  protocol and are not embedded in a permanent QR or site URL.
- The old QR/SoftAP path remains as an explicit recovery option.
- Verify install, iPhone and Android onboarding on one unit from each supported
  model family before claiming the onboarding flow itself is supported.
- Report timing, failed-step rate and user-recovery path on a clean setup; a
  compile or simulator alone is not acceptance.
