#!/usr/bin/env python3
r"""Regressionsvakt för tools/snapshot.sh — backupen AGENTS.md kräver före
varje historikomskrivning.

Verktyget hade noll testtäckning fram till den här filen. Tre
granskningsrundor på PR #87 hittade var sin äkta defekt i det, och varje
defekt hittades av en människa som byggde upp en repoform för hand:

  1. `awk 'BEGIN{RS="\0"}'` läser NUL-poster i mawk och gawk men inte i BSD
     awk (macOS /usr/bin/awk — maintainerns egen maskin), där "\0" blir tom
     sträng och tom RS betyder styckeläge. Vakten "ligger målet inuti en
     utcheckning?" hade då läst fel poster, tyst.
  2. `head -c -1` är en GNU-utvidgning; BSD head vägrar ett negativt antal.
  3. `mktemp` utan mall är en GNU-bekvämlighet; BSD mktemp kräver en — och
     det träffade filens FÖRSTA rad.
  4. Verifieringsfetchen blandade wildcard-refspecar med den EXAKTA
     `+HEAD:refs/p-head/HEAD`. Mot en bundle från ett repo utan giltig HEAD
     avbryts fetchen med "couldn't find remote ref HEAD" — verktyget dömde ut
     ett helt paket som trasigt och raderade backupen. Fel åt fel håll.
  5. I ett bart repo dör `git status` med 128, statusen gick genom `pipefail`
     in i `n=$(...)`, och `set -e` avslutade skriptet EFTER "verifierad" men
     FÖRE återställningsraderna: exit 128 på en backup som låg färdig och
     korrekt på disk.

Testet har därför två halvor, och båda behövs:

  * BETEENDET körs mot riktiga syntetiska repon och gäller på varje
    plattform. Det täcker 4 och 5 var som helst.
  * PORTABILITETEN kontrolleras statiskt, för 1–3 är OSYNLIGA på Linux:
    mawk klarar RS="\0" alldeles utmärkt, och GNU head och mktemp gör precis
    som skriptet ber om. CI:s macOS-jobb kör hela den här filen och är det
    riktiga beviset; de statiska vakterna gör att en återinförd GNU-ism blir
    röd redan på ubuntu, samma minut den skrivs. shellcheck flaggar ingen av
    dem — varken RS="\0" eller en mktemp utan mall är ett skalfel.
"""

import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "snapshot.sh"

# Hermetisk git: maintainerns globala config kan ha hooks, templates, signering
# eller ett annat init.defaultBranch, och inget av det får avgöra en dom här.
BASE_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "Snapshot Test",
    "GIT_AUTHOR_EMAIL": "snapshot@test.invalid",
    "GIT_COMMITTER_NAME": "Snapshot Test",
    "GIT_COMMITTER_EMAIL": "snapshot@test.invalid",
    "GIT_TERMINAL_PROMPT": "0",
}


# --------------------------------------------------------------------------
# Hjälpare
# --------------------------------------------------------------------------

def git(*args, cwd=None, check=True):
    """Kör riktiga git för att bygga en fixtur. Aldrig via skriptet."""
    proc = subprocess.run(
        ["git", *args],
        cwd=None if cwd is None else str(cwd),
        env={**os.environ, **BASE_ENV},
        capture_output=True,
    )
    if check and proc.returncode != 0:
        raise AssertionError(
            "fixturen gick inte att bygga: git {}\n{}".format(
                " ".join(args), proc.stderr.decode("utf-8", "replace")))
    return proc.stdout.decode("utf-8", "replace")


def snapshot(cwd, dest=None, path_prefix=None):
    """Kör tools/snapshot.sh som en människa gör det: via shebangen.

    Utdata avkodas explicit som UTF-8 i stället för med `text=True`. Skriptets
    meddelanden är svenska, och under LC_ALL=C hade Pythons locale-beroende
    avkodning kvävts på första Ä:et i "VÄGRAR" — ett testhaveri som inte hade
    haft ett dugg med snapshot.sh att göra.
    """
    env = {**os.environ, **BASE_ENV}
    env.pop("TG_SNAPSHOT_DIR", None)
    if dest is not None:
        env["TG_SNAPSHOT_DIR"] = str(dest)
    if path_prefix is not None:
        env["PATH"] = "{}{}{}".format(path_prefix, os.pathsep, env["PATH"])
    proc = subprocess.run(
        [str(SCRIPT)], cwd=str(cwd), env=env, capture_output=True)
    proc.out = proc.stdout.decode("utf-8", "replace")
    proc.err = proc.stderr.decode("utf-8", "replace")
    return proc


def detail(label, proc):
    return "{}\n  exit={}\n  stdout:\n{}\n  stderr:\n{}".format(
        label, proc.returncode,
        "\n".join("    " + ln for ln in proc.out.splitlines()) or "    (tomt)",
        "\n".join("    " + ln for ln in proc.err.splitlines()) or "    (tomt)")


def bundles(directory):
    directory = Path(directory)
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.bundle"))


def make_repo(path, payload=1):
    """Ett litet repo med riktigt innehåll, så bundlen får ett riktigt pack."""
    path.mkdir(parents=True, exist_ok=True)
    git("init", "-q", "-b", "main", ".", cwd=path)
    for index in range(payload):
        # Inkompressibelt innehåll: ett pack som DEFLATE kan krympa till
        # nästan ingenting ger byte-vändningstestet för lite att träffa.
        (path / "fil{}.txt".format(index)).write_bytes(os.urandom(512))
    git("add", "-A", cwd=path)
    git("commit", "-q", "-m", "innehall", cwd=path)
    return path


# --------------------------------------------------------------------------
# Halva 1: portabilitet (statisk — defekt 1–3 syns inte på Linux)
# --------------------------------------------------------------------------

def code_of(script_text):
    r"""Bara kodraderna.

    Prosan i snapshot.sh är lång och nämner varje fallgrop vid sitt riktiga
    namn — `RS="\0"` och `mktemp` står ordagrant i kommentarerna. En grep över
    hela filen hade träffat förklaringen i stället för koden.
    """
    return "\n".join(
        line for line in script_text.splitlines()
        if not line.lstrip().startswith("#"))


# Stavningar som bara finns i GNU:s coreutils. Var och en är tyst på
# ubuntu-runnern och dödar filen på maintainerns Mac — alltså exakt den
# klass som ingen körning på Linux kan upptäcka. Listan är avsedd att växa.
GNU_ONLY = (
    (r"""RS\s*=\s*["']?\\+(?:0|x0*0)\b""",
     'awk med NUL som RS: mawk och gawk klarar det, men BSD awk läser "\\0" '
     "som tom sträng, och tom RS betyder styckeläge (defekt 1)"),
    (r"\bhead\b[^|;&\n]*-c\s+-\d",
     "head -c -N är en GNU-utvidgning; BSD head vägrar ett negativt antal "
     "(defekt 2)"),
    (r"\bsed\b[^|;&\n]*\s-i(?=\s|$)",
     "sed -i utan suffixargument: BSD sed läser nästa ord som suffix"),
    (r"\breadlink\b[^|;&\n]*\s-f(?=\s|$)",
     "readlink -f saknas i äldre BSD; skriptet använder `cd && pwd -P`"),
    (r"\bgrep\b[^|;&\n]*\s-P(?=\s|$)",
     "grep -P finns inte i BSD grep"),
    (r"\bstat\b[^|;&\n]*\s-c(?=\s|$)",
     "stat -c är GNU; BSD stat stavar det -f"),
    (r"\bdate\b[^|;&\n]*\s-d(?=\s|$)",
     "date -d är GNU; BSD date stavar det -v/-j"),
)


def scenario_portabilitet(_work):
    assert SCRIPT.is_file(), "tools/snapshot.sh saknas"
    assert os.access(SCRIPT, os.X_OK), (
        "tools/snapshot.sh måste vara körbar — AGENTS.md säger åt en människa "
        "att köra den, inte att lista ut vilket skal den vill ha")

    text = SCRIPT.read_text(encoding="utf-8")
    code = code_of(text)

    for pattern, why in GNU_ONLY:
        found = re.search(pattern, code)
        assert not found, (
            "tools/snapshot.sh: {!r} är inte portabel — {}".format(
                found.group(0), why))

    # Varje mktemp måste ha en egen mall. GNU gör mallen valfri, BSD kräver
    # den, och en mktemp utan mall dör på den FÖRSTA raden som körs — på just
    # den maskin där AGENTS.md gör filen obligatorisk före en omskrivning.
    calls = re.findall(r"\bmktemp\b[^|;&\n)]*", code)
    assert calls, "ingen mktemp kvar i snapshot.sh — vakten mäter inget"
    for call in calls:
        assert "XXXXXX" in call, (
            "tools/snapshot.sh: `{}` saknar mall — BSD mktemp kräver en "
            "(defekt 3)".format(call.strip()))

    # Och den NUL-terminerade worktree-listan ska tolkas i skalet. `-z` är det
    # som gör listan entydig när en sökväg innehåller en radbrytning;
    # `read -r -d ""` är primitiven som läser den utan externt verktyg.
    assert "git worktree list --porcelain -z" in code, (
        "worktree-listan måste läsas NUL-terminerad (-z) — en radorienterad "
        "läsning delar en sökväg med radbrytning mitt itu (defekt 1)")
    assert 'read -r -d ""' in code, (
        "NUL-posterna ska tolkas i skalet med `read -r -d \"\"`, inte av ett "
        "externt verktyg vars NUL-stöd skiljer sig mellan plattformar")

    print("OK: snapshot.sh är fri från GNU-bara stavningar; "
          "varje mktemp har mall och worktree-listan läses NUL-terminerad")


# --------------------------------------------------------------------------
# Halva 2: beteende mot riktiga syntetiska repon
# --------------------------------------------------------------------------

def scenario_vanligt_repo(work):
    repo = make_repo(work / "repo")
    dest = work / "backups"
    proc = snapshot(repo, dest)

    assert proc.returncode == 0, detail("vanligt repo ska lyckas", proc)
    assert "verifierad" in proc.out, detail("saknar verifieringsraden", proc)

    published = bundles(dest)
    assert len(published) == 1, detail(
        "exakt en bundle ska publiceras, hittade {}".format(published), proc)
    bundle = published[0]

    sidecar = bundle.with_suffix(".refs")
    assert sidecar.is_file(), detail(".refs-sidan saknas bredvid bundlen", proc)
    assert sidecar.read_text(encoding="utf-8").strip(), detail(
        ".refs är tom — innehållsförteckningen ska lista bundlens heads", proc)

    mode = stat.S_IMODE(bundle.stat().st_mode)
    assert mode == 0o600, detail(
        "bundlen är HELA repot i en fil och ska vara 0600, inte {:04o} — "
        "backupen får inte bli spridningsvägen".format(mode), proc)
    assert stat.S_IMODE(sidecar.stat().st_mode) & 0o077 == 0, detail(
        ".refs röjer varje grennamn och ska inte vara läsbar för andra", proc)

    assert not list(dest.glob(".incomplete-*")), detail(
        "en halvskriven .incomplete-fil lämnades kvar", proc)

    # Artefakten ska duga till det den finns för: att återställa repot.
    restored = work / "restored"
    git("clone", "-q", str(bundle), str(restored))
    assert (restored / "fil0.txt").is_file(), (
        "den publicerade bundlen gick inte att klona tillbaka")

    print("OK: vanligt repo — bundle + .refs publicerade, läge 0600, "
          "och bundlen går att klona tillbaka")


def scenario_bart_repo(work):
    """Defekt 5: `git status` dör med 128 i ett bart repo."""
    source = make_repo(work / "kalla")
    bare = work / "bart.git"
    git("clone", "-q", "--bare", str(source), str(bare))
    assert git("rev-parse", "--is-bare-repository", cwd=bare).strip() == "true"

    dest = work / "bart-backups"
    proc = snapshot(bare, dest)

    assert proc.returncode == 0, detail(
        "ett bart repo har inget arbetsträd att varna om och ska ge exit 0 "
        "(defekt 5: status 128 gick genom pipefail och dödade skriptet)", proc)
    assert len(bundles(dest)) == 1, detail("ingen bundle publicerades", proc)
    # Det är inte nog att bundlen ligger där: skriptet dog EFTER "verifierad"
    # men FÖRE återställningsraderna. Slutet av utskriften är domen.
    assert "Återställ:" in proc.out, detail(
        "skriptet nådde aldrig återställningsraderna — exakt defekt 5", proc)

    print("OK: bart repo — exit 0 och återställningsraderna skrivs ut")


def scenario_bundle_utan_head(work):
    """Defekt 4: en exakt +HEAD-refspec mot en bundle som saknar HEAD."""
    repo = make_repo(work / "utan-head")
    sha = git("rev-parse", "HEAD", cwd=repo).strip()
    git("tag", "v1", cwd=repo)
    git("update-ref", "refs/remotes/origin/main", sha, cwd=repo)
    git("update-ref", "-d", "refs/heads/main", cwd=repo)

    dest = work / "utan-head-backups"
    proc = snapshot(repo, dest)

    assert proc.returncode == 0, detail(
        "ett repo vars enda refs är remote-refs och taggar ger en bundle utan "
        "HEAD; den är hel och ska verifieras som hel (defekt 4)", proc)
    published = bundles(dest)
    assert len(published) == 1, detail(
        "bundlen dömdes ut som trasig och raderades", proc)

    # Förutsättningen måste hålla, annars testar fallet ingenting: skulle en
    # framtida git ändå annonsera HEAD är +HEAD-refspecen aldrig tom längre.
    heads = published[0].with_suffix(".refs").read_text(encoding="utf-8")
    advertised = [ln.split(" ", 1)[1] for ln in heads.splitlines() if " " in ln]
    assert "HEAD" not in advertised, (
        "fixturen skulle ge en bundle UTAN HEAD, men den annonserar: "
        "{}".format(advertised))

    print("OK: bundle utan HEAD — verifieras som hel i stället för att raderas")


def scenario_shallow(work):
    source = make_repo(work / "djup")
    git("commit", "-q", "--allow-empty", "-m", "tva", cwd=source)
    shallow = work / "avhuggen"
    git("clone", "-q", "--depth", "1", source.as_uri(), str(shallow))
    assert git(
        "rev-parse", "--is-shallow-repository", cwd=shallow).strip() == "true", (
        "fixturen blev inte shallow — fallet hade testat ingenting")

    dest = work / "shallow-backups"
    proc = snapshot(shallow, dest)

    assert proc.returncode == 1, detail(
        "en shallow klon ska vägras: bundlen hade sett komplett ut och "
        "innehållit en femtedel av historiken", proc)
    assert "VÄGRAR" in proc.err, detail("vägran ska sägas högt", proc)
    assert "--unshallow" in proc.err, detail(
        "vägran ska namnge vägen ut: git fetch --unshallow", proc)
    assert not bundles(dest), detail("en shallow bundle publicerades ändå", proc)
    assert not dest.exists(), detail(
        "vägran skedde efter att målkatalogen skapats — kontrollen ska ske "
        "innan något rörs på disk", proc)

    print("OK: shallow klon — vägras med exit 1 och --unshallow i meddelandet")


def scenario_mal_inuti_utcheckning(work):
    repo = make_repo(work / "inuti-repo")
    (work / "utanfor").mkdir()
    linked = work / "inuti-wt"
    git("worktree", "add", "-q", str(linked), "-b", "sidogren", cwd=repo)

    cases = (
        ("direkt i repot", repo / "backups"),
        ("via ..", work / "utanfor" / ".." / "inuti-repo" / "snap"),
        ("via en länkad worktree", linked / "snap"),
    )
    for label, dest in cases:
        proc = snapshot(repo, dest)
        assert proc.returncode == 1, detail(
            "{}: en backup får aldrig bo inuti det den säkerhetskopierar "
            "— en rm -rf av trädet tar då med sig räddningsfilen".format(
                label), proc)
        assert "VÄGRAR" in proc.err, detail(
            "{}: vägran ska sägas högt, inte bli ett tyst exit 1".format(
                label), proc)
        assert "TG_SNAPSHOT_DIR" in proc.err, detail(
            "{}: meddelandet ska namnge vägen ut".format(label), proc)
        assert not list(work.rglob("*.bundle")), detail(
            "{}: en bundle publicerades trots vägran".format(label), proc)

    print("OK: mål inuti en utcheckning vägras — direkt, via .. och via en "
          "länkad worktree")


def scenario_worktree_med_radbrytning(work):
    """Defekt 1: en radorienterad läsning ser bara sökvägens första rad."""
    repo = make_repo(work / "nl-repo")
    linked = work / "wt\nbryt"
    git("worktree", "add", "-q", str(linked), "-b", "radbrytning", cwd=repo)
    listing = git("worktree", "list", "--porcelain", cwd=repo)
    assert "wt\nbryt" in listing, (
        "fixturen fick ingen worktree med radbrytning i sökvägen")

    proc = snapshot(repo, linked / "snap")

    assert proc.returncode == 1, detail(
        "målet ligger inuti worktreen `wt<radbrytning>bryt`. En radorienterad "
        "parser ser bara `wt`, som inte är ett prefix till målet — vakten "
        "släpper då igenom en backup rakt in i utcheckningen (defekt 1)", proc)
    assert "VÄGRAR" in proc.err, detail("vägran ska sägas högt", proc)
    assert not list(work.rglob("*.bundle")), detail(
        "en bundle publicerades inuti worktreen", proc)

    print("OK: worktree med radbrytning i sökvägen — målet inuti den vägras")


def scenario_prunable_worktree(work):
    repo = make_repo(work / "prune-repo")
    gone = work / "borta-wt"
    git("worktree", "add", "-q", str(gone), "-b", "borta", cwd=repo)
    # Katalogen raderas och återskapas som en VANLIG katalog: den passerar
    # `[ -d ]`, medan `git -C` där dör med "not a git repository".
    shutil.rmtree(gone)
    gone.mkdir()
    listing = git("worktree", "list", "--porcelain", cwd=repo)
    assert "prunable" in listing, (
        "fixturen blev inte prunable — git ska rapportera det självt")

    dest = work / "prune-backups"
    proc = snapshot(repo, dest)

    assert proc.returncode == 0, detail(
        "en död worktree-registrering får varken vägra eller krascha", proc)
    assert len(bundles(dest)) == 1, detail("ingen bundle publicerades", proc)
    # Repot och dess levande utcheckning är rena, så en korrekt körning har
    # ingenting att varna om. Dyker det upp en OBS-rad är det den döda
    # registreringen som behandlats som en levande utcheckning — livlighet
    # ska läsas ur gits egen `prunable`, inte gissas ur [ -d ].
    assert "OBS:" not in proc.out, detail(
        "en död worktree-registrering varnades om som vore den levande", proc)
    assert str(gone) not in proc.out, detail(
        "den prunable registreringen behandlades som en levande utcheckning",
        proc)

    print("OK: prunable worktree — varken vägran eller krasch, och den räknas "
          "inte som en levande utcheckning")


def scenario_default_fran_lankad_worktree(work):
    repo = make_repo(work / "huvudrepo")
    linked = work / "lankad"
    git("worktree", "add", "-q", str(linked), "-b", "sido", cwd=repo)

    proc = snapshot(linked, dest=None)

    assert proc.returncode == 0, detail(
        "körning från en länkad worktree ska lyckas", proc)
    beside_main = work / "huvudrepo-backups"
    beside_linked = work / "lankad-backups"
    assert len(bundles(beside_main)) == 1, detail(
        "defaultmålet ska ligga bredvid HUVUDutcheckningen ({})".format(
            beside_main), proc)
    assert not beside_linked.exists(), detail(
        "backupen landade bredvid den länkade worktreen — den ligger under "
        "repot, och en rm -rf av repot tar då med sig räddningsfilen", proc)

    print("OK: körd från en länkad worktree — defaultmålet landar bredvid "
          "huvudutcheckningen")


def scenario_vand_bit(work):
    """En bundle vars pack är korrupt får aldrig publiceras.

    `git bundle verify` duger inte som verifiering: den läser huvudet och
    säger "The bundle is okay" om ett paket vars pack är sönder, medan
    `git clone` dör på "inflate returned -5". Verktyget fetchar därför in i
    ett tomt bart repo, vilket tvingar git att packa upp och kontrollsumma
    varje objekt. Testet mäter VERKTYGETS dom, inte `git bundle verify`:s.

    Biten vänds med ett git-skal först i PATH som skriver sönder paketet i
    samma ögonblick som `bundle create` lämnat det ifrån sig.
    """
    repo = make_repo(work / "korrupt", payload=6)
    shim_dir = work / "skal"
    shim_dir.mkdir()
    marker = shim_dir / "vandes"

    (shim_dir / "shim.py").write_text(
        "import pathlib, subprocess, sys\n"
        "args = sys.argv[1:]\n"
        "rc = subprocess.run([{real!r}] + args).returncode\n"
        "if rc == 0 and 'bundle' in args and 'create' in args:\n"
        "    out = next((pathlib.Path(a) for a in args[args.index('create'):]\n"
        "                if not a.startswith('-') and a != 'create'), None)\n"
        "    if out is not None and out.is_file():\n"
        "        data = bytearray(out.read_bytes())\n"
        "        at = data.find(b'PACK') + 40\n"
        "        if 40 <= at < len(data):\n"
        "            data[at] ^= 0xFF\n"
        "            out.write_bytes(bytes(data))\n"
        "            pathlib.Path({marker!r}).write_text('1')\n"
        "sys.exit(rc)\n".format(real=shutil.which("git"), marker=str(marker)),
        encoding="utf-8")
    shim = shim_dir / "git"
    shim.write_text(
        '#!/bin/sh\nexec {py} {script} "$@"\n'.format(
            py=shlex.quote(sys.executable),
            script=shlex.quote(str(shim_dir / "shim.py"))),
        encoding="utf-8")
    shim.chmod(0o755)

    dest = work / "korrupt-backups"
    proc = snapshot(repo, dest, path_prefix=str(shim_dir))

    # Utan det här hade fallet kunnat bli grönt av att ingenting gick sönder.
    assert marker.is_file(), detail(
        "skalet vände aldrig någon bit — fallet testade ingenting", proc)

    assert proc.returncode != 0, detail(
        "ett korrupt pack ska aldrig kallas verifierat", proc)
    assert "VERIFIERING MISSLYCKADES" in proc.err, detail(
        "verifieringen ska säga ifrån med ord", proc)
    assert not bundles(dest), detail(
        "en korrupt bundle publicerades — falsk trygghet är precis vad "
        "verktyget finns för att undvika", proc)
    assert not list(dest.glob(".incomplete-*")), detail(
        "den halvskrivna filen städades inte bort", proc)

    print("OK: vänd bit i paketet — verifieringen fångar det och ingen bundle "
          "publiceras")


# --------------------------------------------------------------------------

SCENARIOS = (
    scenario_portabilitet,
    scenario_vanligt_repo,
    scenario_bart_repo,
    scenario_bundle_utan_head,
    scenario_shallow,
    scenario_mal_inuti_utcheckning,
    scenario_worktree_med_radbrytning,
    scenario_prunable_worktree,
    scenario_default_fran_lankad_worktree,
    scenario_vand_bit,
)


def main():
    if shutil.which("git") is None:
        print("ERROR: test_snapshot_tool.py kräver git i PATH", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory(
            prefix="tg-snapshot-test-", ignore_cleanup_errors=True) as base:
        # .resolve() är inte kosmetik. På macOS ligger TMPDIR under
        # /var -> /private/var, och skriptet rapporterar `pwd -P`-sökvägar.
        # En oupplöst sökväg på Python-sidan hade aldrig kunnat matcha dem,
        # och en jämförelse som aldrig kan slå till är ett grönt test som
        # inte mäter något.
        root = Path(base).resolve()
        for scenario in SCENARIOS:
            work = root / scenario.__name__
            work.mkdir()
            scenario(work)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
