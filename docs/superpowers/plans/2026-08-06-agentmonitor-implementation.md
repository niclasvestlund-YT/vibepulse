# Agentmonitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Byt Tokenmätarens publika namn till VibePulse och bygg den till en sanningsenlig fysisk agentmonitor som visar Claude Code och Codex som stora animerade pixelkaraktärer i lägena JOBBAR, VÄNTAR, KLAR och FEL, med säker aktivitetsrad och lokal engångsnotis.

**Architecture:** Macens tokenserver följer Claude- och Codex-loggar inkrementellt och publicerar ett litet, sanerat statuskontrakt ur minne. ESP32-appen parsar statusen i en separat statustask med återanvänd HTTP-klient och visar den i en appägd overlay ovanpå befintlig tileview; en lokal tvåminuterslease förhindrar fastfrusen JOBBAR-skärm. Bildassets ligger i flash, animationen invaliderar bara petens yta och ljudet körs i en egen lågprioriterad task.

**Tech Stack:** Python 3 stdlib, C11, cJSON, ESP-IDF 5.5.2, LVGL 9.5, Waveshare BSP 2.0.1, ES8311/esp_codec_dev, SDL2-simulator, IBM Plex Sans.

---

## Obligatorisk förkontroll — före Task 1

- [ ] Läs hela `docs/agentmonitor-granskning.md`.
- [ ] Läs hela `docs/superpowers/specs/2026-08-06-agentmonitor-design.md`.
- [ ] Bekräfta i `main/main.c` att displayens flushhöjd fortfarande är 24 rader; ändra den aldrig som del av detta arbete.
- [ ] Kör baslinjen:

```bash
./test/run.sh
cmake -S sim -B sim/build -G Ninja
ninja -C sim/build
. ~/esp/esp-idf/export.sh
idf.py build
```

Förväntat: alla hosttester gröna, simulatorn länkar och targetbygget slutar med `Project build complete`.

- [ ] Spara före-värden från `idf.py size-components` och de första tre `torget: heap:`-raderna från en fysisk boot. De jämförs vid de två AMOLED-grindarna.

Ingen implementationskod får skrivas innan förkontrollen är klar.

## Filkarta

| Fil | Ansvar |
|---|---|
| `components/app_tokens/agent_status.h` | Delad statusmodell och enumvärden. |
| `components/app_tokens/agent_status_parse.c/.h` | Strikt cJSON-parser för `/api/agent-status`. |
| `components/app_tokens/agent_usage.c/.h` | Hosttestat val av den aktiva gräns som ligger närmast taket. |
| `components/app_tokens/agent_monitor.c/.h` | Overlay, prioritetsval, lokal lease, dismissal och LVGL-animation. |
| `components/app_tokens/agent_assets.c/.h` | Genererade flashlagrade LVGL-bilder; ingen runtime-avkodning. |
| `components/app_tokens/agent_net.c` | Återanvänd 1 Hz-klient för statusendpointen. |
| `components/app_tokens/agent_audio.c/.h` | Kö, NVS-dedup, ES8311 och volymtoggle. |
| `components/app_tokens/agent_audio_assets.c/.h` | Genererade 22 050 Hz mono PCM-arrayer. |
| `components/app_tokens/app.c` | Kopplar befintlig token-UI till monitorn; behåller tileviewen oförändrad. |
| `tools/agent_assets/build-agent-images.py` | Reproducerbar konvertering från godkända PNG-original till LVGL-arrayer. |
| `tools/agent_assets/build-agent-audio.sh` | Reproducerbar lokal svensk TTS- och PCM-konvertering. |
| `tools/tokenserver/agent_status.py` | Inkrementella JSONL-tailers, klassificering, lease och trådsäker snapshot. |
| `tools/tokenserver/test_agent_status.py` | Serverns syntetiska, sekretessfria hosttester. |
| `tools/tokenserver/tokenserver.py` | Startar statuswatchern och exponerar endpointen. |
| `sim-fixtures/agent-status-*.json` | En deterministisk payload per visuell status. |
| `test/test_agent_status.c` | C-parserns kontrakts- och fientliga tester. |
| `test/test_agent_usage.c` | Hosttest av session/vecka/Fable-valet. |
| `sim/main.c` | S-tangent, statuscykel och BMP-runda. |
| `platform/fonts/plex_status_64.c` | Genererad snäv versalfont för huvudorden. |

Det interna komponentnamnet `app_tokens` behålls. Det publika appnamnet,
launcheretiketten och användardokumentationen heter VibePulse.

`platform/torget.h`, `platform/torget_ui.c`, displaystart, rotation och touch ska inte ändras.

### Task 1: Lås statuskontraktet och C-parsern

**Files:**
- Create: `components/app_tokens/agent_status.h`
- Create: `components/app_tokens/agent_status_parse.h`
- Create: `components/app_tokens/agent_status_parse.c`
- Create: `sim-fixtures/agent-status-claude-working.json`
- Create: `test/test_agent_status.c`
- Modify: `test/run.sh:25-29`
- Modify: `components/app_tokens/CMakeLists.txt:4-10`
- Modify: `sim/CMakeLists.txt:27-39`

- [ ] **Step 1: Skapa modellen och den första syntetiska fixturen**

Använd exakt dessa publika typer; senare tasks ska inte byta namn på dem:

```c
#ifndef AGENT_STATUS_H
#define AGENT_STATUS_H

#include <stdint.h>

#define TK_AGENT_ID_CAP 65
#define TK_AGENT_PROJECT_CAP 17

typedef enum {
  TK_AGENT_IDLE,
  TK_AGENT_WORKING,
  TK_AGENT_WAITING,
  TK_AGENT_DONE,
  TK_AGENT_ERROR,
  TK_AGENT_UNKNOWN,
} tk_agent_state;

typedef enum {
  TK_ACTIVITY_NONE,
  TK_ACTIVITY_THINKING,
  TK_ACTIVITY_READING,
  TK_ACTIVITY_EDITING,
  TK_ACTIVITY_SEARCHING,
  TK_ACTIVITY_RUNNING,
  TK_ACTIVITY_TESTING,
  TK_ACTIVITY_BUILDING,
  TK_ACTIVITY_WAITING_INPUT,
  TK_ACTIVITY_WAITING_APPROVAL,
  TK_ACTIVITY_UNKNOWN,
} tk_agent_activity;

typedef struct {
  char task_id[TK_AGENT_ID_CAP];
  char event_id[TK_AGENT_ID_CAP];
  char project[TK_AGENT_PROJECT_CAP];
  tk_agent_state state;
  tk_agent_activity activity;
  uint32_t updated_ms;
} tk_agent_status;

typedef struct {
  uint32_t seq;
  tk_agent_status claude;
  tk_agent_status codex;
} tk_agent_snapshot;

#endif
```

`sim-fixtures/agent-status-claude-working.json` ska vara:

```json
{"v":1,"seq":184,"agents":{"claude":{"task_id":"claude-turn-7","event_id":"claude-event-11","state":"working","project":"Torget","activity":"testing","updated_ms":420},"codex":{"task_id":null,"event_id":null,"state":"idle","project":null,"activity":null,"updated_ms":0}}}
```

- [ ] **Step 2: Skriv det fallerande parsertestet**

`test/test_agent_status.c` ska följa `test/test_tokens.c`-formen och minst kontrollera:

```c
#define PARSE(s, out) tk_agent_status_parse((s), strlen(s), (out))

tk_agent_snapshot s = {0};
check("working-fixturen parsar",
      tk_agent_status_parse(json, len, &s));
check("seq", s.seq == 184);
check("claude working", s.claude.state == TK_AGENT_WORKING);
check("claude testing", s.claude.activity == TK_ACTIVITY_TESTING);
check("projekt", strcmp(s.claude.project, "Torget") == 0);
check("codex idle", s.codex.state == TK_AGENT_IDLE);

tk_agent_snapshot before = s;
check("fel version avvisas",
      !PARSE("{\"v\":2,\"seq\":1,\"agents\":{}}", &s));
check("error-form avvisas", !PARSE("{\"error\":\"nope\"}", &s));
check("negativ updated avvisas",
      !PARSE(NEGATIVE_UPDATED_PAYLOAD, &s));
check("för långt project avvisas",
      !PARSE(LONG_PROJECT_PAYLOAD, &s));
check("kontrolltecken avvisas",
      !PARSE(CONTROL_CHAR_PAYLOAD, &s));
check("okänd state blir unknown",
      PARSE(UNKNOWN_STATE_PAYLOAD, &s)
      && s.claude.state == TK_AGENT_UNKNOWN);
check("okänd activity blir unknown",
      PARSE(UNKNOWN_ACTIVITY_PAYLOAD, &s)
      && s.claude.activity == TK_ACTIVITY_UNKNOWN);
check("avvisning rör inte utdata",
      memcmp(&before, &s, sizeof s) == 0);
```

Definiera payloadmakrona som kompletta v1-objekt och använd `strlen` i `PARSE`, aldrig hårdkodade längder:

```c
#define IDLE_CODEX \
  "\"codex\":{\"task_id\":null,\"event_id\":null,\"state\":\"idle\"," \
  "\"project\":null,\"activity\":null,\"updated_ms\":0}"
#define PAYLOAD(CLAUDE) \
  "{\"v\":1,\"seq\":1,\"agents\":{" CLAUDE "," IDLE_CODEX "}}"
#define NEGATIVE_UPDATED_PAYLOAD PAYLOAD( \
  "\"claude\":{\"task_id\":\"t\",\"event_id\":\"e\"," \
  "\"state\":\"working\",\"project\":\"Torget\"," \
  "\"activity\":\"testing\",\"updated_ms\":-1}")
#define LONG_PROJECT_PAYLOAD PAYLOAD( \
  "\"claude\":{\"task_id\":\"t\",\"event_id\":\"e\"," \
  "\"state\":\"working\",\"project\":\"12345678901234567\"," \
  "\"activity\":\"testing\",\"updated_ms\":1}")
#define CONTROL_CHAR_PAYLOAD PAYLOAD( \
  "\"claude\":{\"task_id\":\"t\",\"event_id\":\"e\"," \
  "\"state\":\"working\",\"project\":\"Tor\\u0001get\"," \
  "\"activity\":\"testing\",\"updated_ms\":1}")
#define UNKNOWN_STATE_PAYLOAD PAYLOAD( \
  "\"claude\":{\"task_id\":\"t\",\"event_id\":\"e\"," \
  "\"state\":\"sleeping\",\"project\":\"Torget\"," \
  "\"activity\":\"testing\",\"updated_ms\":1}")
#define UNKNOWN_ACTIVITY_PAYLOAD PAYLOAD( \
  "\"claude\":{\"task_id\":\"t\",\"event_id\":\"e\"," \
  "\"state\":\"working\",\"project\":\"Torget\"," \
  "\"activity\":\"dancing\",\"updated_ms\":1}")
```

- [ ] **Step 3: Koppla testet och verifiera rött**

Lägg till ett separat clangmål i `test/run.sh`:

```sh
cc -std=c11 -Wall -Wextra -Werror -O1 \
  -DFIXTURES_DIR="\"$(cd ../sim-fixtures && pwd)\"" \
  ../components/app_tokens/agent_status_parse.c \
  test_agent_status.c /tmp/torget-cjson.o \
  -o /tmp/torget-agent-status-test
/tmp/torget-agent-status-test
```

Run: `./test/run.sh`

Expected: FAIL eftersom `agent_status_parse.c/.h` ännu inte finns eller funktionen saknas.

- [ ] **Step 4: Implementera minsta strikta parser**

Exportera:

```c
bool tk_agent_status_parse(const char *json, size_t len,
                           tk_agent_snapshot *out);
```

Parsern ska parsa till en lokal `tk_agent_snapshot next = {0}` och bara göra `*out = next` efter att båda agentobjekten godkänts. `task_id`, `event_id`, `project` är obligatoriska nycklar men får vara JSON `null`; strängar får inte innehålla bytes `< 0x20`, får inte fylla hela destinationsbufferten och ska kopieras med explicit längdkontroll. `seq` och `updated_ms` måste vara heltal i intervallet `0..UINT32_MAX`. Använd fasta tabeller:

```c
static const struct { const char *name; tk_agent_state value; } STATES[] = {
  {"idle", TK_AGENT_IDLE}, {"working", TK_AGENT_WORKING},
  {"waiting", TK_AGENT_WAITING}, {"done", TK_AGENT_DONE},
  {"error", TK_AGENT_ERROR}, {"unknown", TK_AGENT_UNKNOWN},
};

static const struct { const char *name; tk_agent_activity value; } ACTIVITIES[] = {
  {"thinking", TK_ACTIVITY_THINKING}, {"reading", TK_ACTIVITY_READING},
  {"editing", TK_ACTIVITY_EDITING}, {"searching", TK_ACTIVITY_SEARCHING},
  {"running", TK_ACTIVITY_RUNNING}, {"testing", TK_ACTIVITY_TESTING},
  {"building", TK_ACTIVITY_BUILDING},
  {"waiting_input", TK_ACTIVITY_WAITING_INPUT},
  {"waiting_approval", TK_ACTIVITY_WAITING_APPROVAL},
};
```

Okänd state/activity ska mappas till respektive `UNKNOWN`, inte avvisa resten av ett syntaktiskt giltigt paket.

- [ ] **Step 5: Bygg parsern i båda världarna och verifiera grönt**

Lägg `agent_status_parse.c` i appkomponentens `SRCS` och simulatorns source-lista.

Run:

```bash
./test/run.sh
cmake -S sim -B sim/build -G Ninja
ninja -C sim/build
```

Expected: `OK: alla agentstatus-tester gröna` och en länkad `torget-sim`.

- [ ] **Step 6: Commit**

```bash
git add components/app_tokens/agent_status.h \
        components/app_tokens/agent_status_parse.c \
        components/app_tokens/agent_status_parse.h \
        components/app_tokens/CMakeLists.txt sim/CMakeLists.txt \
        sim-fixtures/agent-status-claude-working.json \
        test/test_agent_status.c test/run.sh
git commit -m "Lägg agentstatusens kontrakt och parser"
```

### Task 2: Bygg tokenserverns inkrementella statuswatcher

**Files:**
- Create: `tools/tokenserver/agent_status.py`
- Create: `tools/tokenserver/test_agent_status.py`
- Modify: `tools/tokenserver/tokenserver.py:24-31,395-467`
- Modify: `tools/tokenserver/README.md:1-78`

- [ ] **Step 1: Skriv klassificeringstesterna först**

Använd `unittest`, `tempfile.TemporaryDirectory` och syntetiska dictar. Testnamnen och förväntningarna ska vara:

```python
class ClassificationTests(unittest.TestCase):
    def test_claude_bash_test_maps_to_testing(self):
        event = claude_event("assistant", stop_reason="tool_use",
                             tool_name="Bash",
                             tool_input={"command": "./test/run.sh"})
        self.assertEqual(classify_claude(event).activity, "testing")

    def test_claude_end_turn_waits_and_never_claims_done(self):
        event = claude_event("assistant", stop_reason="end_turn")
        self.assertEqual(classify_claude(event).state, "waiting")

    def test_claude_batch_result_is_explicit_done(self):
        self.assertEqual(classify_claude({"type": "result", "subtype": "success"}).state,
                         "done")

    def test_codex_task_complete_is_done(self):
        event = {"type": "event_msg", "payload": {
            "type": "task_complete", "turn_id": "turn-8",
            "completed_at": "2026-08-06T10:00:00Z"}}
        self.assertEqual(classify_codex(event).state, "done")

    def test_lease_expiry_becomes_unknown_not_done(self):
        store = AgentStatusStore(now=lambda: 121.0)
        store.apply("claude", Event(state="working", activity="editing",
                                    task_id="t", source_id="e", project="Torget"),
                    observed_at=0.0)
        self.assertEqual(store.snapshot()["agents"]["claude"]["state"], "unknown")

    def test_sanitize_drops_control_chars_and_caps_project(self):
        self.assertEqual(sanitize_project("Tor\x00get-med-ett-långt-namn"), "Torget-med-ett-l")
```

Testhjälparen ska bygga samma syntetiska form varje gång:

```python
def claude_event(entry_type, stop_reason=None, tool_name=None, tool_input=None):
    content = []
    if tool_name is not None:
        content.append({"type": "tool_use", "name": tool_name,
                        "input": tool_input or {}})
    return {
        "type": entry_type,
        "sessionId": "session-1",
        "uuid": "event-1",
        "cwd": "/Users/test/Torget",
        "timestamp": "2026-08-06T10:00:00Z",
        "message": {"stop_reason": stop_reason, "content": content},
    }
```

Lägg även test för `AskUserQuestion → waiting_input`, tillståndsfråga/permission denial → `waiting_approval`, `Edit/Write/apply_patch → editing`, `Read/Glob/Grep → reading/searching`, byggkommandon → `building` och vanligt exec → `running`.

Run: `python3 -m unittest tools.tokenserver.test_agent_status -v`

Expected: FAIL med importfel för `agent_status`.

- [ ] **Step 2: Implementera den rena statuskärnan**

`agent_status.py` ska innehålla följande fasta kontrakt:

```python
LEASE_S = 120.0
POLL_S = 0.5
STATES = {"idle", "working", "waiting", "done", "error", "unknown"}
ACTIVITIES = {
    "thinking", "reading", "editing", "searching", "running",
    "testing", "building", "waiting_input", "waiting_approval",
}

@dataclass(frozen=True)
class Event:
    state: str
    activity: str | None
    task_id: str
    source_id: str
    project: str | None

def stable_event_id(provider: str, event: Event) -> str:
    raw = f"{provider}|{event.task_id}|{event.state}|{event.source_id}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]
```

`AgentStatusStore` ska äga ett `threading.Lock`, ett globalt `seq` och två agentposter. `apply()` ökar `seq` bara när state/activity/task/event/project faktiskt ändras. `snapshot()` returnerar nya dictar, räknar `updated_ms` från monotontid, klampar det till `0xFFFFFFFF` och ersätter för gammalt `working` med `unknown` utan att mutera till `done`.

Klassificeraren får läsa ett lokalt verktygs kommando för att välja enum men får aldrig lagra eller returnera kommandot. En ny Claude `type:"user"`-post startar ett nytt `working/thinking`-varv med `sessionId + uuid` som task-id; projektnamn blir det sanerade basnamnet ur `cwd`. Claude `end_turn` i interaktiv session blir `waiting`, aldrig `done`; bara `type:"result"` med lyckad subtype får bli `done`. Codex `task_started` blir `working` och `task_complete` blir `done`.

- [ ] **Step 3: Skriv tailertesterna**

Testa följande med riktiga temporära JSONL-filer:

```python
def test_tailer_reads_only_appended_complete_lines(self):
    path.write_text(json.dumps(first) + "\n" + '{"half":')
    self.assertEqual([first], tailer.read_new(path))
    with path.open("a") as f:
        f.write("true}\n" + json.dumps(second) + "\n")
    self.assertEqual([{"half": True}, second], tailer.read_new(path))

def test_rotation_starts_new_file_without_replaying_old_file(self):
    self.assertEqual(1, service.poll_once())
    new_path.write_text(json.dumps(next_event) + "\n")
    self.assertEqual(1, service.poll_once())
```

Tailern ska hålla byteoffset och en ofullständig rad per path. En fil med minskad storlek nollställer just den filens offset. Globba nyaste filer defensivt och tolerera att en fil försvinner under läsning.

- [ ] **Step 4: Implementera `AgentStatusService` och endpointen**

`AgentStatusService(projects_dir, codex_sessions, now=time.monotonic)` ska ha:

```python
def poll_once(self) -> int: ...       # antal applicerade kompletta events
def snapshot(self) -> dict: ...       # {v:1, seq, agents:{claude,codex}}
def start(self) -> None: ...          # daemon-thread, 0,5 s kadens
def stop(self) -> None: ...           # testbar Event-signal
```

`tokenserver.py` skapar servicen före HTTP-servern:

```python
status_service = AgentStatusService(
    projects_dir=Handler.projects_dir,
    codex_sessions=CODEX_SESSIONS,
)
status_service.poll_once()
status_service.start()
Handler.agent_status = status_service
```

Lägg till i `Handler.do_GET`:

```python
elif self.path == "/api/agent-status":
    self._send(200, self.agent_status.snapshot())
```

Endpointen får bara ta store-låset och serialisera en snapshot; den får inte globba eller läsa filer i requesttråden. Rootsvaret ska lista båda endpoints.

- [ ] **Step 5: Kör serverns tester och en lokal endpointkontroll**

Run:

```bash
python3 -m unittest tools.tokenserver.test_agent_status -v
python3 tools/tokenserver/tokenserver.py --port 8738 &
server_pid=$!
curl --fail --silent http://127.0.0.1:8738/api/agent-status
kill "$server_pid"
```

Expected: alla unittest gröna och curlsvaret har `"v": 1`, båda agenterna och inga promptar, kommandon eller meddelandetexter.

- [ ] **Step 6: Commit**

```bash
git add tools/tokenserver/agent_status.py \
        tools/tokenserver/test_agent_status.py \
        tools/tokenserver/tokenserver.py tools/tokenserver/README.md
git commit -m "Lägg inkrementell agentstatus i tokenservern"
```

### Task 3: Bygg font, riktiga pet-assets och statisk overlay i bänken

**Files:**
- Create: `platform/fonts/plex_status_64.c`
- Create: `components/app_tokens/assets/source/claude-pet-white.png`
- Create: `components/app_tokens/assets/source/codex-icon.png`
- Create: `components/app_tokens/agent_assets.c`
- Create: `components/app_tokens/agent_assets.h`
- Create: `tools/agent_assets/build-agent-images.py`
- Create: `components/app_tokens/agent_usage.c`
- Create: `components/app_tokens/agent_usage.h`
- Create: `components/app_tokens/agent_monitor.c`
- Create: `components/app_tokens/agent_monitor.h`
- Create: `test/test_agent_usage.c`
- Create: `sim-fixtures/agent-status-{idle,claude-working,claude-waiting,claude-done,claude-error,codex-working,codex-waiting,codex-done,codex-error,unknown}.json`
- Modify: `platform/fonts/fetch-and-convert.sh:19-34`
- Modify: `platform/fonts/plex_icon_64.c`
- Modify: `components/app_tokens/app.c:81-102,420-450,455-566`
- Modify: `components/app_tokens/app_tokens.h:1-24`
- Modify: `components/app_tokens/CMakeLists.txt:4-10`
- Modify: `test/run.sh:25-29`
- Modify: `sim/CMakeLists.txt:27-39`
- Modify: `sim/main.c:151-212,214-262`

- [ ] **Step 1: Byt launcheridentitet till VibePulse och generera fonterna**

Byt `tokens_app.name` till `VIBEPULSE`. Ikonen ska använda samma deklarativa
96 px-system som Solelkollen, utan ändring i `torget_icon_t`:

```c
.name = "VIBEPULSE",
.icon = {
  .font = &plex_icon_64,
  .glyph = "V",
  .plate_hex = 0x181636,
  .glyph_hex = 0xFFFFFF,
  .dot_hex = 0x7770FF,
},
```

Ändra launcherfontens intervall från bara `S,T` till `S,T,V`; `T` får ligga
kvar för bakåtkompatibilitet i fontasseten. Regenerera `plex_icon_64.c` och
verifiera i simulatorns launcher att både Solelkollens `S` och VibePulse `V`
visas utan fallback-glyf.

Generera därefter den snäva 64px-statusfonten. Lägg detta i fontskriptet:

```sh
# Agentmonitorns huvudord: JOBBAR, VÄNTAR, KLAR, FEL.
conv Bold 64 "0x41,0x42,0x45,0x46,0x4A,0x4B,0x4C,0x4E,0x4F,0x52,0x54,0x56,0xC4" plex_status_64
```

Run: `platform/fonts/fetch-and-convert.sh`

Expected: `platform/fonts/plex_icon_64.c` innehåller `V`, VibePulse visas med
rätt namn och ikon i launchern, och `platform/fonts/plex_status_64.c` skapas
med `const lv_font_t plex_status_64`.

- [ ] **Step 2: Importera de godkända källbilderna och generera LVGL-assets**

Kopiera de bevarade användaroriginalen, inte mockupsskärmarna:

```bash
mkdir -p components/app_tokens/assets/source
cp /Users/niclasvestlund/Documents/Codex/2026-08-06/ko/outputs/agentmonitor-review/sources/claude-pet-white.png \
   components/app_tokens/assets/source/claude-pet-white.png
cp /Users/niclasvestlund/Documents/Codex/2026-08-06/ko/outputs/agentmonitor-review/sources/codex-icon.png \
   components/app_tokens/assets/source/codex-icon.png
```

Skapa `tools/agent_assets/build-agent-images.py` med Pillow. Scriptet ska beskära den verkliga figuren, skala med `Image.Resampling.NEAREST` till högst 180 × 180 och centrera på transparent 180 × 180. Exportera Claude som `LV_COLOR_FORMAT_A8` och Codex som `LV_COLOR_FORMAT_I4` med högst 15 synliga färger plus transparent index 0. I4-datan består av 16 BGRA-palettposter följt av två index per byte, högsta nibblen först. De genererade deskriptorerna ska använda:

```c
const lv_image_dsc_t tk_img_claude_open = {
  .header = {.magic = LV_IMAGE_HEADER_MAGIC, .cf = LV_COLOR_FORMAT_A8,
             .w = 180, .h = 180, .stride = 180},
  .data_size = sizeof(tk_img_claude_open_map),
  .data = tk_img_claude_open_map,
};
```

Codex får en separat I4-bild för molnet och A8-bilder för `>` och `_`, härledda från originalet. Ingen `lv_canvas`, PNG-avkodning eller runtime-allokering är tillåten.

- [ ] **Step 3: Välj och testa den usage-gräns som ligger närmast taket**

Skriv först `test/test_agent_usage.c` med minst dessa fall och lägg det som ett eget clang-mål i `test/run.sh`:

```c
tk_limit session = {.pct = 21, .has_pct = true};
tk_limit week = {.pct = 49, .has_pct = true};
tk_limit fable = {.pct = 73, .has_pct = true};
tk_agent_usage u = tk_agent_usage_pick(&session, &week, &fable);
check(u.window == TK_USAGE_FABLE && u.pct == 73,
      "Fable ska vinna när den ligger närmast taket");

session.pct = 95;
u = tk_agent_usage_pick(&session, &week, &fable);
check(u.window == TK_USAGE_SESSION && u.pct == 95,
      "sessionen ska kunna vinna över Fable");

fable.has_pct = false;
u = tk_agent_usage_pick(&session, &week, &fable);
check(u.window == TK_USAGE_SESSION,
      "null eller saknad Fable ska ignoreras");
```

Testet ska vara rött innan implementationen skapas. Exportera sedan:

```c
typedef enum {
  TK_USAGE_NONE,
  TK_USAGE_SESSION,
  TK_USAGE_WEEK,
  TK_USAGE_FABLE,
} tk_usage_window;

typedef struct {
  bool has_pct;
  double pct;
  tk_usage_window window;
} tk_agent_usage;

tk_agent_usage tk_agent_usage_pick(const tk_limit *session,
                                    const tk_limit *week,
                                    const tk_limit *fable);
```

Välj högsta giltiga procent. Summera eller medelvärdesbilda aldrig fönstren. `NULL`, `has_pct == false` och icke-finita procenttal ignoreras. Vid exakt lika procent gäller den stabila ordningen Fable > vecka > session. Kör testet igen och se det bli grönt.

- [ ] **Step 4: Skapa overlayns publika API**

`agent_monitor.h` ska exportera exakt:

```c
void tk_agent_monitor_create(lv_obj_t *root);
void tk_agent_monitor_apply(const tk_agent_snapshot *snapshot, int64_t now_us);
void tk_agent_monitor_tick(int64_t now_us);
void tk_agent_monitor_set_usage(bool claude, tk_agent_usage usage);
```

`agent_monitor.c` ska skapa overlayn som syskon till tileviewen i appens `root`, efter tileview och prickar så att den ligger överst. Den börjar med `LV_OBJ_FLAG_HIDDEN`, storlek 480 × 480, svart bakgrund och 24 px säker kant. Lägg till:

```c
lv_obj_add_event_cb(mon.overlay, overlay_long_pressed,
                    LV_EVENT_LONG_PRESSED, NULL);
lv_obj_add_event_cb(mon.overlay, overlay_clicked,
                    LV_EVENT_CLICKED, NULL);
```

`overlay_long_pressed` ska enbart kalla `torget_launcher_open()`. `overlay_clicked` ska lägga aktuell agents `event_id` i en RAM-baserad `dismissed_event_id[2]`, dölja overlayn och aldrig äta långtrycket. Sätt `LV_OBJ_FLAG_EVENT_BUBBLE` på provider- och ljudtouchytorna så långtryck når overlayn; deras egna `LV_EVENT_CLICKED`-callbacks ska kalla `lv_event_stop_bubbling(e)` så agentbyte/mute inte samtidigt kvitterar hela statusen.

Skapa topprad, 180 × 180 `lv_image`, 64px huvudord, tre små aktivitetsrutor, 21px aktivitetsrad och bottenrad/progressbar enligt specens koordinater. Textmappningen ska vara en fast tabell:

```c
static const char *ACTIVITY_TEXT[] = {
  [TK_ACTIVITY_THINKING] = "TÄNKER",
  [TK_ACTIVITY_READING] = "LÄSER KOD",
  [TK_ACTIVITY_EDITING] = "ÄNDRAR FILER",
  [TK_ACTIVITY_SEARCHING] = "SÖKER I PROJEKTET",
  [TK_ACTIVITY_RUNNING] = "KÖR KOMMANDO",
  [TK_ACTIVITY_TESTING] = "KÖR TESTER",
  [TK_ACTIVITY_BUILDING] = "BYGGER PROJEKTET",
  [TK_ACTIVITY_WAITING_INPUT] = "BEHÖVER ETT SVAR",
  [TK_ACTIVITY_WAITING_APPROVAL] = "BEHÖVER DITT GODKÄNNANDE",
};
```

Lås färgerna i samma modul:

```c
#define TK_COL_CLAUDE lv_color_hex(0xD97757)
#define TK_COL_CODEX  lv_color_hex(0x625BFF)
#define TK_COL_WAIT   lv_color_hex(0xFF9F2F)
#define TK_COL_ERROR  lv_color_hex(0xE0635B)
#define TK_COL_WHITE  lv_color_hex(0xFFFFFF)
```

Prioritet ska vara waiting > error > working > done > idle; vid lika prioritet vinner lägst `updated_ms`. Ett avvisat/dismissat `event_id` får inte visas igen förrän eventet ändras. Toppraden ska ha en Claude- och en Codexpunkt med minst 44 × 44 px gemensam touchyta; ett click växlar till den andra aktiva agenten tills en högre prioritet anländer. Reservera också högersta 44 × 44 px för `LV_SYMBOL_VOLUME_MAX` i låg opacity redan nu, så första glasgrinden granskar den slutliga toppradens geometri; Task 8 aktiverar reglaget.

Bottenetiketten ska komma från `usage.window`: `SESSIONEN`, `VECKAN` eller `FABLE`. Saknas giltig usage visas streck och ingen fylld bar. Procent, bar och färg följer alltid det valda fönstret, så Claude Max kan visa exempelvis `73,0 % FABLE` när Fable ligger närmast taket.

- [ ] **Step 5: Koppla overlayn till den befintliga appen**

I slutet av `tk_create(root)`, efter prickarna men före nätstarten:

```c
tk_agent_monitor_create(root);
```

I `tokens_apply()` ska respektive providers närmaste aktiva gräns skickas till monitorn:

```c
tk_agent_monitor_set_usage(
    true,
    tk_agent_usage_pick(&t->claude_session, &t->claude_week,
                        &t->claude_model_week));
tk_agent_monitor_set_usage(
    false,
    tk_agent_usage_pick(&t->codex_session, &t->codex_week, NULL));
```

Fable behålls samtidigt som en egen rad i VibePulse vanliga vy. I appens befintliga 100 ms-timer ska `tk_agent_monitor_tick(now)` anropas. Lägg en wrapper i `app_tokens.h/.c` för simulatorn:

```c
void tokens_apply_agent_status(const tk_agent_snapshot *snapshot) {
  tk_agent_monitor_apply(snapshot, torget_now_us());
}
```

Inkludera `secrets.h` endast under `#ifdef ESP_PLATFORM`. Lägg dessutom ett lokalt fysiskt QA-läge bakom `#if defined(ESP_PLATFORM) && defined(TK_AGENT_DEMO)`. En 5-sekunderstimer ska cykla syntetiska Claude `working → waiting → done → error` med unika event-id:n. Makrot definieras bara i gitignorerade `secrets.h` under den första AMOLED-grinden och påverkar aldrig normal firmware.

- [ ] **Step 6: Lägg statusfixturer och S-cykel i simulatorn**

Varje fixture ska följa kontrakt v1 och ändra exakt en agent. Använd seq 200–209, unika `event_id`, projekt `Torget` och följande state/activity:

| Fixture | Agent | State | Activity |
|---|---|---|---|
| `idle` | båda | idle | null |
| `claude-working` | Claude | working | testing |
| `claude-waiting` | Claude | waiting | waiting_approval |
| `claude-done` | Claude | done | null |
| `claude-error` | Claude | error | null |
| `codex-working` | Codex | working | editing |
| `codex-waiting` | Codex | waiting | waiting_input |
| `codex-done` | Codex | done | null |
| `codex-error` | Codex | error | null |
| `unknown` | Claude | unknown | null |

Utöka `poll_keys` till åtta tangenter och bind `SDL_SCANCODE_S`. Varje ny S-flank läser nästa fixture genom den riktiga C-parsern och anropar `tokens_apply_agent_status`.

Utöka `platform_tour_cb` med statiska BMP:er för Claude/Codex working, waiting och done. Filnamnen ska bli `/tmp/torget-agent-claude-working.bmp` etc.

Lägg också en deterministisk Claude Max-usage i simulatorn: session 21 %, vecka 49 % och Fable 73 %. Claude-overlayn ska då visa `73,0 % FABLE`.

- [ ] **Step 7: Verifiera statiskt i bänken**

Run:

```bash
./test/run.sh
cmake -S sim -B sim/build -G Ninja
ninja -C sim/build
./sim/build/torget-sim
```

Expected: S cyklar alla tillstånd; långtryck på overlayn öppnar launchern;
VibePulse visas med V-ikonen; click döljer eventet; KEY3-motsvarigheten N
byter app och korrekt overlay återkommer när VibePulse visas igen. Kontrollera
även `lv_mem_monitor` efter overlaybygget och logga total/ledig/största block;
ingen canvasallokering får synas.

- [ ] **Step 8: Commit den statiska overlayn**

```bash
git add platform/fonts/fetch-and-convert.sh platform/fonts/plex_icon_64.c \
        platform/fonts/plex_status_64.c \
        components/app_tokens/assets components/app_tokens/agent_assets.c \
        components/app_tokens/agent_assets.h components/app_tokens/agent_usage.c \
        components/app_tokens/agent_usage.h components/app_tokens/agent_monitor.c \
        components/app_tokens/agent_monitor.h components/app_tokens/app.c \
        components/app_tokens/app_tokens.h components/app_tokens/CMakeLists.txt \
        tools/agent_assets/build-agent-images.py \
        test/test_agent_usage.c test/run.sh \
        sim-fixtures/agent-status-*.json sim/CMakeLists.txt sim/main.c
git commit -m "Gör VibePulse till statisk agentmonitor"
```

### Task 4: Första fysiska AMOLED-grinden — före animation

**Files:**
- Modify only if glass review requires it: `components/app_tokens/agent_monitor.c`
- Regenerate only if size is wrong: `components/app_tokens/agent_assets.c`

- [ ] **Step 1: Bygg och flasha den statiska versionen**

Lägg till `#define TK_AGENT_DEMO 1` i gitignorerade `secrets.h`, utan att skriva ut eller stagea filen. Bygg därefter:

```bash
. ~/esp/esp-idf/export.sh
idf.py build
ls /dev/cu.usbmodem*
idf.py -p /dev/cu.usbmodem101 flash monitor
```

Om porten skiljer sig används den exakta port som `ls` visar. Ingen animationskod får påbörjas före denna grind.

- [ ] **Step 2: Granska verkligt glas**

Låt QA-timern cykla Claude working, waiting, done och error. Fotografera rakt framifrån på 1, 2 och 3 meter i normalt rumsljus. Godkänn bara om:

- `JOBBAR`, `VÄNTAR` och `KLAR` läses utan att gå fram till skärmen;
- procenten är synlig men tydligt sekundär;
- Claude Max visar `FABLE` när Fable ligger närmast taket;
- projekt- och aktivitetsrad inte klipps;
- vitt, korall, amber och rött skiljs på panelen;
- inga ljusa kantlinjer eller gamla apppixlar syns efter KEY3/rotation.

- [ ] **Step 3: Justera endast storlek, kontrast och radbrytning**

Ändra koordinater/petstorlek/fontstorlek bara utifrån fotona. Lägg inte till fler element. Kör simulator + targetbuild efter varje justering.

- [ ] **Step 4: Commit glasjusteringen**

Ta bort `TK_AGENT_DEMO` ur lokala `secrets.h` efter granskningen och verifiera ett nytt targetbygge utan demo.

```bash
git add components/app_tokens/agent_monitor.c components/app_tokens/agent_assets.c
git commit -m "Justera agentoverlayn efter AMOLED-granskning"
```

Om glaset inte krävde någon ändring görs ingen tom commit; markera grinden klar i planens checkboxar.

### Task 5: Lägg till resurssnål pet-animation och färgövergångar

**Files:**
- Modify: `components/app_tokens/agent_assets.c/.h`
- Modify: `components/app_tokens/agent_monitor.c/.h`
- Modify: `sim/main.c`

- [ ] **Step 1: Skapa de två nödvändiga bildvarianterna**

Claude behöver `tk_img_claude_open` och `tk_img_claude_blink`; blinkbilden ska vara en redigering av det godkända källassetet, inte en ny figur. Använd bildredigeringsverktyget med originalet och denna exakta riktning: `Behåll exakt samma pixelfigur, canvas, proportioner och färger. Ändra endast de två öppna svarta ögonen till ett enpixelhögt horisontellt blinkläge. Ingen ny detalj, ingen skugga, ingen antialiasing.` Kör sedan samma assetkonverterare. Codex behåller molnassetet och får separata `tk_img_codex_prompt` och `tk_img_codex_cursor` som redan härletts från originalet. Alla ligger i flash.

- [ ] **Step 2: Implementera en 125 ms-animationstimer**

Timerstate ska vara liten och statisk:

```c
typedef struct {
  uint8_t frame;
  bool active;
  bool finishing;
  int64_t next_idle_blink_us;
} tk_pet_anim;
```

Under `working` cyklar fyra steg: y=0/open, y=-3/open, y=-3/blink, y=0/open. De tre aktivitetsrutorna tänds en i taget. Callbacken får bara ändra petobjektets y/source och rutornas opacity; inga texter, allokeringar eller loggar i 8 fps.

Codex rör molnet högst 2 px och blinkar `_`; `>` står still. `waiting`, `done` och `error` har bara en blinkning var fjärde sekund.

- [ ] **Step 3: Implementera KLAR-övergången inom petens yta**

Använd två flashbilder av samma pet i en 180 × 180-parent: vit bas och färgad överbild i ett barn med clipping. Animera clipbarnets höjd 0 → 180 på 600 ms, gör sedan en enda y-studs -8 → 0. Invalidera aldrig overlayn eller skärmen uttryckligen; låt LVGL invalidera de förändrade objekten.

- [ ] **Step 4: Verifiera dirty-area och simulatorbilder**

Run:

```bash
ninja -C sim/build
./sim/build/torget-sim
```

Expected: endast petens ungefärliga 220 × 220-yta förändras mellan frames; texten står stabil; ingen frame skapar eller frigör heap. BMP-rundan ska fortfarande fånga läsbara slutlägen efter att övergången landat.

- [ ] **Step 5: Commit**

```bash
git add components/app_tokens/agent_assets.c components/app_tokens/agent_assets.h \
        components/app_tokens/agent_monitor.c components/app_tokens/agent_monitor.h \
        sim/main.c
git commit -m "Animera Claude och Codex på agentoverlayn"
```

### Task 6: Koppla 1 Hz-status med keep-alive och lokal lease

**Files:**
- Create: `components/app_tokens/agent_net.c`
- Modify: `components/app_tokens/app.c:420-450,455-566`
- Modify: `components/app_tokens/app_tokens.h`
- Modify: `components/app_tokens/CMakeLists.txt`
- Modify: `components/app_tokens/idf_component.yml`
- Modify: `secrets.h.example:24-28`

- [ ] **Step 1: Lägg endpointkonfigurationen utan att röra hemligheter**

Lägg i `secrets.h.example`:

```c
/* #define TK_AGENT_STATUS_URL \
   "http://192.168.1.XX:8737/api/agent-status" */
```

Uppdatera den gitignorerade lokala `secrets.h` med samma Mac-IP som `TK_TOKENS_URL`, men skriv aldrig ut filens innehåll och stagea den aldrig.

- [ ] **Step 2: Implementera den långlivade klienten**

`agent_net.c` ska skapa exakt en `esp_http_client_handle_t` efter `torget_net_wait()` och återanvända den:

```c
esp_http_client_config_t cfg = {
  .url = TK_AGENT_STATUS_URL,
  .timeout_ms = 2500,
  .keep_alive_enable = true,
  .keep_alive_idle = 5,
  .keep_alive_interval = 5,
  .keep_alive_count = 3,
  .event_handler = status_http_event,
  .user_data = &response,
};
esp_http_client_handle_t client = esp_http_client_init(&cfg);
```

Responsebufferten ska vara statisk `.bss`, 1536 bytes, och eventhandlern ska avvisa overflow. Före varje `esp_http_client_perform` nollställs längd/status men klienten förstörs inte. Endast HTTP 200 + hel parserframgång får anropa:

```c
torget_ui_lock();
tokens_apply_agent_status(&snapshot);
torget_ui_unlock();
```

Exportera `void tokens_agent_net_start(void);` från `agent_net.c`. Starta tasken en gång från `tk_create` bredvid befintliga `tokens_net_start()`, vänta 3 s efter nätready och polla var 1 000 ms. Använd 6144 bytes stack och prioritet 5. Misslyckanden lämnar senaste goda status orörd. Lägg `agent_net.c` i appkomponentens `SRCS`, aldrig i simulatorn.

Ändra appkomponentens privata beroenden i denna task till:

```cmake
PRIV_REQUIRES torget_net esp_http_client
```

- [ ] **Step 3: Aktivera den lokala tvåminutersleasen**

`tk_agent_monitor_apply(snapshot, now_us)` stämplar `last_packet_us = now_us` och beräknar varje agents observerade eventtid som `now_us - updated_ms * 1000`. `tk_agent_monitor_tick(now_us)` ska dölja overlayn och behandla working som unknown om antingen inga giltiga paket kommit på två minuter eller eventets egen ålder passerat två minuter:

```c
(has_status && now_us - last_packet_us > 120LL * 1000000LL) ||
(selected.state == TK_AGENT_WORKING && selected.updated_ms > 120000U)
```

Den får aldrig skapa `DONE` ur timeout. Under giltigt `working` ska tick/apply kalla `torget_keep_awake()` under UI-låset. Terminala lägen kallar inte keep-awake och dämpas därför efter plattformens 15 minuter.

- [ ] **Step 4: Verifiera serveravbrott och återanslutning**

Kör tokenservern, visa JOBBAR, stoppa servern och accelerera leasen i ett testbygge till 5 s. Expected: overlayn lämnas efter 5 s och vanliga tokenvyer lever vidare. Starta servern igen. Expected: samma keep-alive-klient återansluter och ny status visas utan reboot. Återställ 120 s före commit.

Följ `torget: heap:` under minst fem minuter. Expected: ingen nedåtgående trend och största DMA-block över 24 KB; socketantalet får inte växa med ett per poll.

- [ ] **Step 5: Kör hela byggkedjan och commit**

```bash
./test/run.sh
python3 -m unittest tools.tokenserver.test_agent_status -v
ninja -C sim/build
. ~/esp/esp-idf/export.sh
idf.py build
git add components/app_tokens/agent_net.c components/app_tokens/app.c \
        components/app_tokens/app_tokens.h components/app_tokens/CMakeLists.txt \
        components/app_tokens/idf_component.yml secrets.h.example
git commit -m "Koppla agentstatus med återanvänd HTTP-klient"
```

### Task 7: Andra fysiska AMOLED-grinden — animation och polling

**Files:**
- Modify only if measurements demand it: `components/app_tokens/agent_monitor.c`, `components/app_tokens/agent_net.c`

- [ ] **Step 1: Flash och kör 30-minutersprotokollet**

Flash targetet och låt `working` vara aktivt i 30 minuter med riktig 1 Hz-poll. Spara `torget: heap:` var tionde sekund. Godkänt kräver:

- ingen växande LVGL- eller internheapallokering;
- största DMA-block aldrig under 24 KB;
- touch, rotation, KEY3 och launcher svarar hela tiden;
- inga fullskärmsinvalidations utom plattformens pixeldrift en gång/minut;
- jämn 6–8 fps utan printf-spam;
- korrekt återkomst till samma overlay efter appbyte.

- [ ] **Step 2: Jämför storlek och flashmarginal**

```bash
idf.py size-components
```

Jämför mot förkontrollens värden. Dokumentera agentassets, font och appens totala ökning. Appartitionen ska fortfarande ha minst 1 MB marginal före ljud.

- [ ] **Step 3: Fixa endast uppmätta problem och commit**

Om fps är för tung: sänk till 6 fps före minskad läsbarhet. Om DMA-blocket kryper: minska statusbody/taskstack efter faktisk high-water-mark, aldrig displayens 24-radsbuffer. Kör om hela 30-minuterstestet efter en fix.

```bash
git add components/app_tokens/agent_monitor.c components/app_tokens/agent_net.c
git commit -m "Stabilisera agentanimationen på AMOLED"
```

Ingen tom commit om inga ändringar behövs.

### Task 8: Lägg lokal röst och engångsdeduplicering

**Files:**
- Create: `components/app_tokens/agent_audio.c`
- Create: `components/app_tokens/agent_audio.h`
- Create: `components/app_tokens/agent_audio_assets.c`
- Create: `components/app_tokens/agent_audio_assets.h`
- Create: `tools/agent_assets/build-agent-audio.sh`
- Modify: `components/app_tokens/agent_monitor.c`
- Modify: `components/app_tokens/CMakeLists.txt`
- Modify: `components/app_tokens/idf_component.yml`
- Modify: `sim/CMakeLists.txt`

- [ ] **Step 1: Generera korta svenska PCM-assets lokalt**

Scriptet ska använda installerade svenska rösten Alva och konvertera till exakt signed 16-bit little-endian, mono, 22 050 Hz:

```bash
say -v Alva -r 190 -o /tmp/claude-done.aiff \
  "Claude är klar och väntar på dig."
ffmpeg -y -i /tmp/claude-done.aiff -ar 22050 -ac 1 -f s16le \
  /tmp/claude-done.pcm
```

Gör motsvarande för `Codex är klar och väntar på dig`, `Claude väntar på dig` och `Codex väntar på dig`. Generera en kort låg felton utan tal. Scriptet ska sedan skapa `const uint8_t`-arrayer och exakta `size_t`-längder i `agent_audio_assets.c/.h`. Totalen ska mätas och hållas under 500 KB.

Använd `xxd` med fasta symbolnamn, exempelvis:

```bash
xxd -i -n tk_audio_claude_done /tmp/claude-done.pcm >> "$out_c"
```

Headern ska deklarera samma symboler som `extern const unsigned char[]` samt respektive `extern const unsigned int ..._len`; inga WAV/AIFF-headrar ska bäddas in.

- [ ] **Step 2: Implementera ljudtasken med lazy init**

Exportera:

```c
typedef enum { TK_PROVIDER_CLAUDE, TK_PROVIDER_CODEX } tk_provider;

void tk_agent_audio_start(void);
void tk_agent_audio_notify(tk_provider provider, tk_agent_state state,
                           const char *event_id);
bool tk_agent_audio_enabled(void);
void tk_agent_audio_set_enabled(bool enabled);
```

`tk_agent_audio_start` skapar en kö med fyra poster och en task med 4096 bytes stack, prioritet 3. Högtalaren initieras först när första ljudet ska spelas:

```c
esp_codec_dev_handle_t speaker = bsp_audio_codec_speaker_init();
esp_codec_dev_sample_info_t fs = {
  .sample_rate = 22050,
  .channel = 1,
  .bits_per_sample = 16,
};
ESP_ERROR_CHECK(esp_codec_dev_set_out_vol(speaker, 30));
ESP_ERROR_CHECK(esp_codec_dev_open(speaker, &fs));
```

Allokera I2S/codec en gång, inte per fras. Spela med `esp_codec_dev_write` i ljudtasken, aldrig under UI-låset.

Omslut BSP-, NVS- och codec-koden med `#ifdef ESP_PLATFORM` och implementera samma API som en liten minnesbaserad no-op i simulatorn. Lägg `agent_audio.c`, men inte de stora PCM-arrayerna, i `sim/CMakeLists.txt`; då kan overlayns mute-reglage provas utan att länka hårdvarukod eller ljuddata.

Lägg BSP:n som direkt appberoende i `components/app_tokens/idf_component.yml`:

```yaml
  waveshare/esp32_s3_touch_amoled_2_16: "^2.0.1"
```

Utöka appkomponentens privata CMake-beroenden till `torget_net esp_http_client nvs_flash esp_codec_dev waveshare__esp32_s3_touch_amoled_2_16`.

- [ ] **Step 3: Lägg NVS-dedup och ljudtoggle**

Använd namespace `tk_agent` och nycklarna `sound_on`, `claude_evt`, `codex_evt`. Eventet köas bara om `event_id` är icke-tomt, skiljer sig från senast köade i RAM och skiljer sig från senast spelade i NVS. Skriv event-id till NVS först efter lyckad uppspelning.

Skapa en 44 × 44 px klickyta längst till höger i overlayns topprad. Använd LVGL:s befintliga volymglyf om den finns i aktiv font; annars använd ett riktigt A8-iconasset. Klicket växlar `sound_on`, uppdaterar glyphens opacity och kvitterar inte statusoverlayn.

- [ ] **Step 4: Koppla endast terminala nya events**

När `agent_monitor_apply` ser ett nytt odismissat `event_id`:

```c
if (state == TK_AGENT_DONE || state == TK_AGENT_WAITING ||
    state == TK_AGENT_ERROR) {
  tk_agent_audio_notify(provider, state, status->event_id);
}
```

`working`, `idle`, `unknown`, repoll av samma event och server-/enhetsomstart får inte spela ljud.

- [ ] **Step 5: Verifiera ljud och internminne på enheten**

Testa done/waiting/error för båda leverantörerna, mute/unmute och reboot med samma event. Expected: exakt ett ljud per nytt event, inget efter reboot, låg tydlig volym och oförändrat visuellt tillstånd om ljudinit misslyckas. Kontrollera heap-raden direkt före och efter lazy init; största DMA-block ska fortfarande vara över 24 KB.

- [ ] **Step 6: Commit**

```bash
git add components/app_tokens/agent_audio.c components/app_tokens/agent_audio.h \
        components/app_tokens/agent_audio_assets.c \
        components/app_tokens/agent_audio_assets.h \
        components/app_tokens/agent_monitor.c components/app_tokens/CMakeLists.txt \
        components/app_tokens/idf_component.yml sim/CMakeLists.txt \
        tools/agent_assets/build-agent-audio.sh
git commit -m "Lägg lokal röst för agentnotiser"
```

### Task 9: Full regression, dokumentation och slutlig fysisk verifiering

**Files:**
- Modify: `README.md:54-85`
- Modify: `tools/tokenserver/README.md`
- Modify: `sim-fixtures/README.md`
- Modify: `secrets.h.example`

- [ ] **Step 1: Kör alla automatiska kontroller**

```bash
./test/run.sh
python3 -m unittest tools.tokenserver.test_agent_status -v
cmake -S sim -B sim/build -G Ninja
ninja -C sim/build
./sim/build/torget-sim &
sim_pid=$!
sleep 15
kill "$sim_pid"
wait "$sim_pid" 2>/dev/null || true
. ~/esp/esp-idf/export.sh
idf.py build
idf.py size-components
git diff --check
```

Expected: alla tester gröna, alla agent-BMP:er skapade, targetbygge klart och ingen whitespace-varning.

- [ ] **Step 2: Gör en visuell jämförelse**

Jämför simulatorns Claude working/done och Codex working/done sida vid sida med de godkända referenserna i:

- `/Users/niclasvestlund/Documents/Codex/2026-08-06/ko/outputs/agentmonitor-review/01-jobbar.png`
- `/Users/niclasvestlund/Documents/Codex/2026-08-06/ko/outputs/agentmonitor-review/02-klar.png`

Kontrollera petstorlek, svart botten, hierarki, radbrytning, procentens läsbarhet och att inga dashboardelement smugit sig in.

- [ ] **Step 3: Kör slutlig fysisk scenariotur**

På glaset, i ordning:

1. idle visar ordinarie Claude/Codex/volymvyer;
2. Claude working visar JOBBAR och korrekt aktivitet;
3. Codex working tar över enligt senaste aktivitet;
4. waiting prioriteras över working och spelar en notis;
5. done ligger kvar, dämpas efter 15 minuter och spelar inte om;
6. serveravbrott lämnar working efter två minuter utan falskt KLAR;
7. KEY3, svep, långtryckslauncher, rotation och touch fungerar;
8. fel i ljudet påverkar inte skärmen.

- [ ] **Step 4: Uppdatera dokumentationen med det verkliga beteendet**

README ska dokumentera S-tangenten och agent-BMP:erna. Tokenserverns README ska visa `/api/agent-status`, sanerade fält, varför interaktiv Claude `end_turn` blir VÄNTAR och hur tjänsten startas om. Fixture-README ska lista alla statusfixtures. `secrets.h.example` ska visa båda LAN-endpoints utan riktig IP.

- [ ] **Step 5: Commit och slutkontroll**

```bash
git add README.md tools/tokenserver/README.md sim-fixtures/README.md secrets.h.example
git commit -m "Dokumentera VibePulse agentmonitor"
git status --short
git log --oneline -12
```

Expected: ren worktree och en separat, begriplig commit per task/grind.
