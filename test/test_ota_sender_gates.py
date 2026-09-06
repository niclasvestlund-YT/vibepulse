#!/usr/bin/env python3
"""Regression guard: the OTA pusher's sender gates must never erode.

Läxorna 2026-08-14: en hårdkodad byggkatalog och ett filval vid skriptstart
sköt en arkiverad frysbinär till glaset; -dirty-byggen och byggen utan grön
CI ska aldrig kunna levereras av misstag. Enheten bevisar att en avbild är
GILTIG — skriptet bevisar att den är RÄTT.

Läxan 2026-09-06 (svarsgrinden): curl sätter inte exitkod på HTTP-fel utan
--fail, så ett avslag från enheten läste sig som en lyckad flash och
skriptet sa "vald för nästa boot" till en operatör som stod vid panelen.
Den grinden testas inte med en strängsökning utan genom att KÖRA skriptet
mot en påhittad enhet som svarar det enheten verkligen kan svara."""

import http.server
import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path


root = Path(__file__).resolve().parents[1]
script_path = root / "tools" / "ota-flash.sh"
script = script_path.read_text(encoding="utf-8")

# ---------------------------------------------------------------- statiskt

wait_idx = script.index('"maintenance_open":true')
pick_idx = script.index("ls -t build*/torget.bin")
assert pick_idx > wait_idx, (
    "binärvalet ska ske EFTER fönsterväntan (uppladdningsögonblicket) — "
    "ett val vid skriptstart valde spökbinären medan bygget skrev sin fil"
)
assert 'BIN_VERSION=$(dd if="$BIN" bs=1 skip=48 count=32' in script, (
    "versionen ska läsas ur avbildens egen appbeskrivning och deklareras"
)
assert "TG_OTA_ALLOW_DIRTY" in script and "*-dirty*" in script, (
    "-dirty-byggen ska vägras utan uttrycklig TG_OTA_ALLOW_DIRTY=1"
)
assert "TG_OTA_ALLOW_NO_CI" in script and "gh run list --commit" in script, (
    "CI-bryggan: byggen utan grön CI för sin commit ska vägras, med "
    "TG_OTA_ALLOW_NO_CI=1 som enda nödventil"
)

ci = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
assert "branches: ['**']" in ci, (
    "CI ska köra på alla brancher — annars kan bryggan aldrig se grönt "
    "för branchbyggen och blockerar det dagliga flödet"
)

# Enhetens handler äger statuskontraktet; skriptet ska kunna översätta HELA
# det kontraktet till en mening på terminalen. Får handlern en ny avslagsväg
# faller det här testet tills avsändaren också vet vad den betyder.
handler = (root / "components" / "torget_ota" / "ota_service.c").read_text(
    encoding="utf-8"
)
device_statuses = {m.group(1) for m in re.finditer(r'"(\d{3}) [A-Z]', handler)}
assert "202" in device_statuses, "202 Accepted är enhetens enda framgång"
for status in sorted(device_statuses - {"202"}):
    assert re.search(rf"^\s*{status}\)", script, re.MULTILINE), (
        f"enheten kan svara {status} men tools/ota-flash.sh förklarar inte "
        f"vad det betyder — kända avslag: {sorted(device_statuses - {'202'})}"
    )

# ------------------------------------------------------------ beteendetest

TOKEN = "a1" * 32
IMAGE_VERSION = "1.0.0-gdeadbee"
SUCCESS_LINE = "vald för nästa boot"

# Vad servern ska göra med nästa POST /api/ota/firmware.
plan = {"status": 202, "error": None, "read_body": True, "answer": True}


class FakeDevice(http.server.BaseHTTPRequestHandler):
    """Så mycket av enheten som avsändaren kan se: ett öppet fönster och
    ett svar på uppladdningen."""

    protocol_version = "HTTP/1.1"

    def _send(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):  # noqa: N802 — http.server-API
        self._send(200, b'{"project":"torget","maintenance_open":true}')

    def do_POST(self):  # noqa: N802 — http.server-API
        length = int(self.headers.get("Content-Length", 0))
        if plan["read_body"]:
            left = length
            while left > 0:
                chunk = self.rfile.read(min(left, 65536))
                if not chunk:
                    break
                left -= len(chunk)
        else:
            # Enheten avvisar på headrarna, före kroppen (403/401/413 gör
            # det), och stänger anslutningen med kroppen okonsumerad.
            self.close_connection = True
        if not plan["answer"]:
            self.close_connection = True
            return  # inget svar alls: curl ser ett tomt svar, status 000
        if plan["status"] == 202:
            body = ('{"sha256":"%s","version":"%s"}' % ("0" * 64, IMAGE_VERSION))
        else:
            body = '{"error":"%s"}' % plan["error"]
        self._send(plan["status"], body.encode())
        if not plan["read_body"]:
            self.close_connection = True

    def log_message(self, *args):
        pass  # tyst: testets utskrifter ska vara testets


class QuietServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass  # en klient som försvinner mitt i är ett scenario, inte ett fel


def run_pusher(tmp, port):
    """Kör skriptet mot den påhittade enheten och lämna tillbaka utfallet."""
    tools = tmp / "tools"
    tools.mkdir(exist_ok=True)
    shutil.copy2(script_path, tools / "ota-flash.sh")
    (tmp / "secrets.h").write_text(
        f'#define TG_OTA_TOKEN "{TOKEN}"\n', encoding="utf-8"
    )
    (tmp / ".ota-device").write_text(f"127.0.0.1:{port}\n", encoding="utf-8")
    build = tmp / "build"
    build.mkdir(exist_ok=True)
    image = bytearray(4096)
    image[48 : 48 + len(IMAGE_VERSION)] = IMAGE_VERSION.encode()
    (build / "torget.bin").write_bytes(bytes(image))

    env = dict(os.environ)
    # CI-bryggan kräver gh + nät och har sina egna vakter ovan; det som
    # prövas här är vad skriptet gör med enhetens SVAR.
    env["TG_OTA_ALLOW_NO_CI"] = "1"
    if shutil.which("shasum") is None:
        # Linux-CI utan perl: skriptet är ett Mac-verktyg, och SHA-vägen är
        # inte det som testas. En stubbe håller testet ärligt körbart.
        stub_dir = tmp / "stub-bin"
        stub_dir.mkdir(exist_ok=True)
        stub = stub_dir / "shasum"
        stub.write_text(
            "#!/bin/sh\n"
            'exec python3 -c "import hashlib,sys;'
            "print(hashlib.sha256(open(sys.argv[-1],'rb').read()).hexdigest()"
            "+'  '+sys.argv[-1])\" \"$@\"\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
        env["PATH"] = f"{stub_dir}{os.pathsep}{env['PATH']}"

    return subprocess.run(
        ["sh", "tools/ota-flash.sh"],
        cwd=tmp,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


assert shutil.which("curl"), "beteendetestet behöver curl (avsändarens verktyg)"

server = QuietServer(("127.0.0.1", 0), FakeDevice)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    # 1. Enheten svarar 202: precis som förut — uppladdningen lyckades.
    plan.update(status=202, error=None, read_body=True, answer=True)
    with tempfile.TemporaryDirectory() as d:
        ok = run_pusher(Path(d), port)
    assert ok.returncode == 0, f"202 ska lyckas, fick {ok.returncode}: {ok.stderr}"
    assert SUCCESS_LINE in ok.stdout, ok.stdout
    assert IMAGE_VERSION in ok.stdout, "enhetens svar ska synas: " + ok.stdout

    # 2. Varje dokumenterat avslag ska STOPPA skriptet och namnge statusen.
    #    Det var precis det här som gick igenom tyst: curl utan --fail
    #    returnerar 0 på 400/401/403/500/503, och raden om nästa boot
    #    skrevs ut ändå.
    rejections = [
        (400, "not a torget esp32s3 image", True),
        (401, "bad or missing token", False),
        (403, "maintenance window closed", False),
        (408, "upload interrupted", True),
        (413, "image size out of bounds", False),
        (500, "flash write failed", True),
        (503, "upload already running", False),
    ]
    for status, error, read_body in rejections:
        plan.update(status=status, error=error, read_body=read_body, answer=True)
        with tempfile.TemporaryDirectory() as d:
            bad = run_pusher(Path(d), port)
        both = bad.stdout + bad.stderr
        assert bad.returncode != 0, (
            f"HTTP {status} måste avbryta pushen — skriptet slutade med "
            f"{bad.returncode} och sa:\n{both}"
        )
        assert str(status) in bad.stderr, (
            f"avbrottet ska namnge vad enheten svarade ({status}):\n{bad.stderr}"
        )
        assert SUCCESS_LINE not in both, (
            f"HTTP {status} fick skriptet att påstå att avbilden valdes för "
            f"boot:\n{both}"
        )

    # 3. Inget svar alls (enheten föll bort mitt i): också ett stopp, aldrig
    #    en flash som "gick igenom".
    plan.update(status=0, error=None, read_body=True, answer=False)
    with tempfile.TemporaryDirectory() as d:
        gone = run_pusher(Path(d), port)
    both = gone.stdout + gone.stderr
    assert gone.returncode != 0, "ett uteblivet svar ska avbryta:\n" + both
    assert SUCCESS_LINE not in both, both
    assert "VÄGRAR" in gone.stderr, gone.stderr
finally:
    server.shutdown()
    server.server_close()

print(
    "OK: avsändargrindarna står — rätt fil, deklarerad version, ren build, "
    "grön CI, och bara ett 202 från enheten räknas som en flash"
)
