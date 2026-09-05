#!/usr/bin/env python3
"""The contracts the WiFi setup window shares across three languages.

The firmware, the Mac script and the consent model each hold one half of an
agreement.  Nothing catches these at compile time, so they are asserted
here — the drift they prevent is silent and only shows up on a hotel room
floor with a panel that will not join anything.
"""

import hashlib
import re
import struct
from pathlib import Path

root = Path(__file__).resolve().parents[1]
setup_c = (root / "components/torget_wifi/wifi_setup.c").read_text(encoding="utf-8")
main_c = (root / "main/main.c").read_text(encoding="utf-8")
script = (root / "tools/wifi-here.sh").read_text(encoding="utf-8")
slots_h = (root / "components/torget_wifi/wifi_slots.h").read_text(encoding="utf-8")
readme = (root / "README.md").read_text(encoding="utf-8")
wifi_doc = (root / "docs/wifi.md").read_text(encoding="utf-8")

# --- The access point's identity must match on both sides ------------------
# The script joins by name; a rename on one side alone strands the panel.
ap_ssid = re.search(r'#define AP_SSID\s+"([^"]+)"', setup_c).group(1)
assert f"AP_SSID={ap_ssid}" in script, (
    f"tools/wifi-here.sh joins a different SSID than the firmware raises ({ap_ssid})"
)

ap_host = re.search(r'#define AP_ADDRESS\s+"([^"]+)"', setup_c).group(1)
assert f"AP_HOST={ap_host}" in script, (
    "the script posts to a different address than the firmware serves"
)

# --- The derived access-point password ------------------------------------
# Firmware: sha256(domain || TG_OTA_TOKEN), first 6 bytes as 12 hex chars.
# Script:   the same digest, cut to 12 characters.  Both are asserted against
# a worked example so neither the domain string nor the length can drift.
domain_c = re.search(r'static const char domain\[\] = "([^"]+)"', setup_c).group(1)
domain_sh = re.search(r"^DOMAIN=(\S+)", script, re.M).group(1)
assert domain_c == domain_sh, (
    f"domain separation string differs: firmware {domain_c!r}, script {domain_sh!r}"
)

# The firmware writes 6 bytes as "%02x" pairs; the script cuts 12 characters.
assert re.search(r"for \(int i = 0; i < 6; i\+\+\)\s*\n\s*snprintf\(s_ap_pass \+ i \* 2, 3, \"%02x\"", setup_c), (
    "the firmware no longer writes exactly 6 bytes of the digest as hex"
)
assert "cut -c1-12" in script, "the script no longer cuts the digest to 12 characters"

token = "a" * 64
expected = hashlib.sha256((domain_c + token).encode()).hexdigest()[:12]
assert len(expected) == 12 and expected.isalnum()
# WPA2 refuses anything shorter than 8 characters — a 12-char PSK has margin.
assert len(expected) >= 8, "a derived AP password below 8 chars cannot be a WPA2 PSK"

# --- The consent model ------------------------------------------------------
# docs/ota.md promises the OTA maintenance window opens ONLY from the device.
# The decision chain moved out of main/main.c into the pure, shared
# platform/button_arbitration.c — so the assertions moved with the mechanism
# again, and got stricter, because the arbitration can now be checked for what
# it CANNOT do rather than for the shape of an if/else.
arb_c = (root / "platform/button_arbitration.c").read_text(encoding="utf-8")
arb_h = (root / "platform/button_arbitration.h").read_text(encoding="utf-8")

# Purity is the guarantee, not a comment about one: the arbitration reaches no
# service at all. A decision that cannot call anything cannot be tricked into
# opening a window, and both hosts feed the same function.
for forbidden in ("torget_ota_service", "torget_wifi_setup_request",
                  "torget_settings_", "torget_app_next", "tk_needs_you"):
    assert forbidden not in arb_c, (
        f"the arbitration must stay pure; it calls {forbidden}"
    )
# There is no output that opens the maintenance window. The window is reachable
# only through the menu's UPDATE row, which needs a finger on the glass.
assert "open_maintenance" not in arb_h, (
    "the arbitration must expose no output that opens the maintenance window"
)
assert "request_setup_open" in arb_h and "open_menu" in arb_h

# Neither host decides anything: they read inputs and apply outputs. If a host
# names a button ACTION it is deciding again, which is the violation this whole
# module exists to end (AGENTS.md: "UI-beteende hör hemma i appen/platform/,
# aldrig i main/main.c eller sim/main.c").
sim_c = (root / "sim/main.c").read_text(encoding="utf-8")
for host_name, host in (("main/main.c", main_c), ("sim/main.c", sim_c)):
    assert "tg_button_arbitrate(" in host, (
        f"{host_name} must route KEY3 through the shared arbitration"
    )
    assert "TG_BUTTON_" not in host, (
        f"{host_name} must not branch on button actions; that is a decision"
    )

# The hold reaches a menu and nothing else. main.c applies open_menu; the OTA
# window is opened in exactly one place, and only from the menu's intent.
hold_apply = main_c.split("if (key3_out.open_menu) {", 1)
assert len(hold_apply) == 2, "the KEY3 hold must apply the arbitration's open_menu"
hold_apply = hold_apply[1].split("\n  }", 1)[0]
assert "torget_settings_open(" in hold_apply, "the KEY3 hold must open SETTINGS"
assert "torget_ota_service_open_maintenance" not in hold_apply, (
    "the hold must not reach past the menu into the OTA window"
)
assert main_c.count("torget_ota_service_open_maintenance();") == 1, (
    "the maintenance window may be opened from exactly one place"
)
intent_switch = main_c.split("switch (torget_settings_take_intent())", 1)
assert len(intent_switch) == 2, "the settings intent switch moved"
assert "torget_ota_service_open_maintenance();" in intent_switch[1].split("\n  }", 1)[0], (
    "that one place must be the menu's UPDATE intent"
)
# The no-IP protection survives as a menu that cannot offer UPDATE: main.c
# passes NULL for the address, and the menu refuses the row on that.
assert re.search(r"have_ip\s*\?\s*ip\s*:\s*NULL", hold_apply), (
    "a panel without an address must be told so, not handed a stale one"
)
# ...and the address is COPIED under the spinlock, never read in place: the
# string is rewritten by the event loop on a renewed lease or a changed
# address, on another core, with no disconnect in between.
assert "ip_text_copy(ip, sizeof ip)" in hold_apply, (
    "the menu must take its own copy of the address, not alias the writer's"
)
settings_c = (root / "platform/settings_menu.c").read_text(encoding="utf-8")
assert "if (!ui.ip[0]) break;" in settings_c, (
    "UPDATE must be refused when there is no address to receive an upload"
)

# The double hold: a second full hold while the OTA window is open must
# REQUEST the setup window, never close-and-open — the port-80 handover belongs
# to the setup guard's window_open().  Two request sites in main.c: the
# arbitration's request_setup_open and the menu's WIFI intent.
assert main_c.count("torget_wifi_setup_request_open();") == 2, (
    "expected exactly the arbitration's request and the menu's WIFI intent"
)
maintenance_branch = arb_c.split("} else if (in->maintenance_open) {")[1]
maintenance_branch = maintenance_branch.split("} else if (menu_open) {")[0]
assert "out->request_setup_open = true;" in maintenance_branch, (
    "the hold-again switch must live inside the maintenance-open branch"
)
# The switch arm itself must produce ONLY the request: split at the hold arm
# and assert the close is not part of it.
switch_arm = maintenance_branch.split("TG_BUTTON_OPEN_MAINTENANCE)", 1)[1]
assert "close_maintenance" not in switch_arm, (
    "the LVGL task must not close the OTA window itself when switching"
)

# --- Immediate KEY3 ownership ---------------------------------------------
# request_open is called by the LVGL task but all slow radio work belongs to
# the setup guard. The request must atomically claim KEY3 and wake that task;
# otherwise the release is seen as NEXT_APP during the old 500 ms polling gap.
assert "TaskHandle_t s_guard_task" in setup_c, (
    "the setup guard handle is required for immediate wakeup"
)
request_open = setup_c.split("void torget_wifi_setup_request_open(void)")[1]
request_open = request_open.split("void torget_wifi_setup_request_close", 1)[0]
guard_ready = request_open.find("if (!s_guard_task) return;")
phase_claim = request_open.find("TG_WIFI_PHASE_STARTING")
assert 0 <= guard_ready < phase_claim, (
    "a missing guard task must fail without trapping KEY3 in STARTING"
)
assert "TG_WIFI_PHASE_STARTING" in request_open, (
    "request_open must claim the STARTING phase synchronously"
)
assert "xTaskNotifyGive(s_guard_task)" in request_open, (
    "request_open must wake the guard instead of waiting for its poll"
)
assert "ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(500))" in setup_c, (
    "the guard must combine immediate notification with the existing poll"
)

guard = setup_c.split("static void guard_task(void *arg)")[1]
starting_render = guard.find("torget_wifi_ui_set(TG_WIFI_UI_STARTING")
slow_open = guard.find("window_open();")
assert 0 <= starting_render < slow_open, (
    "STARTING must be rendered before scanning, APSTA and portal startup"
)

ownership_branch = arb_c.find("if (in->setup_owns_input) {")
ota_branch = arb_c.find("} else if (in->maintenance_open) {")
app_switch = arb_c.find("out->next_app = true;", ota_branch)
panic = arb_c.find("out->panic = true;", ota_branch)
assert 0 <= ownership_branch < ota_branch < app_switch < panic, (
    "setup input ownership must be checked before OTA, app switching and panic"
)
assert "torget_wifi_setup_is_open()" not in main_c, (
    "main must not expose the STARTING gap by checking AP-open state only"
)

# The setup window may never become a firmware path.
assert "esp_ota" not in setup_c and "ota_ops" not in setup_c, (
    "the WiFi setup window must never gain a firmware-writing surface"
)

# --- The immutable floor ----------------------------------------------------
# secrets.h is what makes a bad network entry recoverable without USB.  The
# candidate list must always be built with the compiled-in networks appended.
assert "tg_wifi_candidates(remembered, TG_WIFI_SLOTS, fixed, 2" in main_c, (
    "the compiled-in secrets.h networks must stay as the candidate floor"
)
assert "TG_WIFI_SSID" in main_c and "TG_WIFI2_SSID" in main_c

# --- The DMA gates ----------------------------------------------------------
# The access point was the one surface whose memory cost had never been
# measured on hardware, and its first physical run wedged the panel
# (2026-08-17).  The gates must bracket the expensive step: refuse before
# anything happens, abort after the APSTA switch, both against the flush's
# contiguous-DMA floor.  A wedge-proof window is one that would rather stay
# closed than freeze the glass.
open_gate = setup_c.find("tg_wifi_setup_dma_ok_to_open")
apsta = setup_c.find("esp_wifi_set_mode(WIFI_MODE_APSTA)")
cont_gates = [
    match.start()
    for match in re.finditer(r"tg_wifi_setup_dma_ok_to_continue", setup_c)
]
httpd_start_at = setup_c.find("server_start();")
publish_open_at = setup_c.find("atomic_store(&s_open, true)")
assert len(cont_gates) == 2, (
    "window_open must check DMA after APSTA and after the portal tasks start"
)
assert (
    0 < open_gate < apsta < cont_gates[0] < httpd_start_at
    < cont_gates[1] < publish_open_at
), (
    "window_open must gate before APSTA, before HTTP/DNS, and once more "
    "after their real allocation before publishing the setup window"
)
assert "flush_dma_bytes" in main_c, (
    "main.c must hand the flush's DMA floor to the setup hooks"
)

# --- The lazy surface -------------------------------------------------------
# The 2026-08-14 freeze lesson: a boot that never opens the window must not
# pay for it.  The AP, the HTTP server and the DNS task all live in
# window_open(), never in the start path.
start_fn = setup_c.split("void torget_wifi_setup_start(")[1]
for forbidden in ("httpd_start", "esp_netif_create_default_wifi_ap", "dns_task"):
    assert forbidden not in start_fn, (
        f"{forbidden} must not run at boot — the setup surface is lazy"
    )

# --- Password handling ------------------------------------------------------
# A password must never reach the log; the SSID may, because it is the only
# way to debug a network from a serial console.
for line in setup_c.splitlines():
    if "ESP_LOG" in line:
        assert "s_ap_pass" not in line and "pass" not in line.split("ESP_LOG")[1], (
            f"a password must never be logged: {line.strip()}"
        )

# --- Window timing ----------------------------------------------------------
# The window must close itself; an open AP left up overnight is a surface.
assert "TG_WIFI_SETUP_WINDOW_US   (600LL" in slots_h, (
    "the setup window must stay bounded at ten minutes"
)

# --- Trial first, remember only after IP ----------------------------------
join_post = setup_c.split("static esp_err_t join_post(")[1]
join_post = join_post.split("static esp_err_t catch_all_get", 1)[0]
assert "tg_wifi_creds_remember" not in join_post, (
    "POST /join must never write an unproven password to NVS"
)
assert "s_join_submission.seq" in join_post, (
    "every POST must publish a fresh versioned in-memory submission"
)
assert "xSemaphoreTake(s_join_lock" in join_post, (
    "SSID, password and submission sequence must be copied atomically"
)
assert "xTaskNotifyGive(s_guard_task)" in join_post, (
    "a phone submission should wake the guard instead of waiting 500 ms"
)

guard = setup_c.split("static void guard_task(void *arg)")[1]
trial_at = guard.find("s_hooks->try_credentials")
have_ip_at = guard.find("if (!applied_now && have_ip)", trial_at)
remember_at = guard.find("tg_wifi_creds_remember", trial_at)
accepted_at = guard.find("s_hooks->credentials_accepted", remember_at)
assert 0 <= trial_at < have_ip_at < remember_at < accepted_at, (
    "the guard must trial credentials, observe IP, persist, then accept"
)
assert "tg_wifi_join_should_apply" in guard, (
    "the guard must apply each submission sequence at most once"
)
assert "bool applied_now = false" in guard
assert "if (!applied_now && have_ip)" in guard, (
    "an IP sample taken before a new trial starts must not validate it"
)
assert "s_hooks->last_disconnect_reason" in guard, (
    "retry status must come from the radio's numeric disconnect reason"
)

assert "bool (*try_credentials)(const char *ssid, const char *password)" \
       in (root / "components/torget_wifi/wifi_setup.h").read_text(
           encoding="utf-8"), (
    "the setup adapter needs an explicit in-memory trial hook"
)
assert "hook_try_credentials" in main_c and "s_trial_active" in main_c, (
    "main must keep trial credentials out of the remembered candidate list"
)
assert "last_disconnect_reason" in main_c, (
    "the setup guard needs the exact disconnect reason, not display copy"
)
try_hook = main_c.split("static bool hook_try_credentials(")[1]
try_hook = try_hook.split("static void hook_credentials_accepted", 1)[0]
clear_at = try_hook.find("xEventGroupClearBits(s_net_events, WIFI_GOT_IP)")
apply_at = try_hook.find("wifi_apply_current()")
assert 0 <= clear_at < apply_at, (
    "a trial must clear the old IP proof before applying new credentials"
)
assert "disconnect_err == ESP_OK" in try_hook, (
    "only a disconnect that actually started may suppress its event"
)

status_get = setup_c.split("static esp_err_t status_get(")[1]
status_get = status_get.split("static esp_err_t catch_all_get", 1)[0]
assert '\\"connecting\\"' in status_get
assert '\\"connected\\"' in status_get
assert '\\"retry\\"' in status_get
assert '\\"password\\"' in status_get
assert '\\"not-found\\"' in status_get
assert "s_join_submission.ssid" not in status_get
assert "s_join_submission.pass" not in status_get
assert '"/status"' in setup_c and "setInterval" in setup_c, (
    "the joining page must poll an honest, secret-free status endpoint"
)
assert "2.4 GHz only" in setup_c and 'for=\\"pass\\"' in setup_c, (
    "the phone form must explain the radio limit and label the password"
)
assert "data-secured" in setup_c, (
    "every scanned option must tell the phone whether a password is required"
)
assert "Password for " in setup_c and "No password required" in setup_c, (
    "the phone form must name the selected network and explain open networks"
)
assert "pass.required = secured" in setup_c and "passWrap.hidden = !secured" in setup_c, (
    "the phone must require passwords only for secured selections"
)
assert "hasNetwork" in setup_c and "join.disabled=!hasNetwork" in setup_c, (
    "an empty scan must hide password guidance and disable Join"
)
assert '<option disabled selected>No 2.4 GHz networks found</option>' in setup_c, (
    "an empty scan must produce a disabled, explanatory selection"
)
assert 'placeholder=\\"Enter Wi-Fi password\\"' in setup_c, (
    "the form must not tell secured-network users to leave the password blank"
)
assert "Password (leave blank if open)" not in setup_c
assert "s_scan.authmode" in setup_c, (
    "the scan must retain authentication truth through HTML generation and POST"
)
join_auth = join_post.split("tg_wifi_ssid_valid", 1)[0]
assert "WIFI_AUTH_OPEN" in setup_c and "WIFI_AUTH_OWE" in setup_c, (
    "open and Enhanced Open networks must both be treated as password-free"
)
assert "authmode_requires_password" in join_auth, (
    "the POST must use the same authentication classifier as the form"
)
assert "pass[0] == '\\0'" in join_auth, (
    "POST /join must reject an empty password for a secured scanned network"
)
assert "pass[0] = '\\0'" in join_auth, (
    "POST /join must discard a forged password for an open scanned network"
)
assert "onsubmit=" in setup_c and ".disabled=true" in setup_c, (
    "the phone form must block accidental duplicate submission"
)

# --- Open-source onboarding must be visible, not only described ----------
# The README and release reuse exact simulator frames. Pin both checked-in
# files to the panel's native size without adding an image-library dependency
# to this cross-language wiring test.
for image_name in (
    "vibepulse-wifi-searching.png",
    "vibepulse-wifi-setup.png",
    "vibepulse-wifi-signal.png",
):
    image_path = root / "docs/img" / image_name
    assert image_path.is_file(), f"missing WiFi onboarding image: {image_name}"
    image_bytes = image_path.read_bytes()
    assert image_bytes[:8] == b"\x89PNG\r\n\x1a\n", (
        f"WiFi onboarding image is not PNG: {image_name}"
    )
    assert struct.unpack(">II", image_bytes[16:24]) == (480, 480), (
        f"WiFi onboarding image is not native 480x480: {image_name}"
    )
    assert f"docs/img/{image_name}" in readme, (
        f"README does not show WiFi onboarding image: {image_name}"
    )
    assert f"img/{image_name}" in wifi_doc, (
        f"docs/wifi.md does not show WiFi onboarding image: {image_name}"
    )

for guide, name in ((readme, "README"), (wifi_doc, "WiFi guide")):
    lower = " ".join(guide.lower().split())
    for claim in (
        "scan the qr",
        "not secure",
        "192.168.4.1",
        "2.4 ghz",
        "only after the panel connects",
        "does not mean internet",
    ):
        assert claim in lower, f"{name} must explain {claim!r}"
    assert "old saved networks remain available" in lower, (
        f"{name} must explain failed joins preserve fallback networks"
    )
    assert "manual setup" in lower, (
        f"{name} must explain where temporary credentials moved"
    )
    assert "password field" in lower and "open network" in lower, (
        f"{name} must explain the secure/open phone form behavior"
    )

print("OK: WiFi setup window, Mac script and consent model agree")
