/*
 * Brevlådans rena funktioner, hållna stilla med node --test (ingen
 * Cloudflare behövs): färskast vinner PER POOL för /api/tokens, nyast
 * dokument för resten, och döda/korrupta dokument tystar aldrig de andra.
 * Dessutom de två som håller den fria nivån: avsändarindexet (som ersatte
 * listningen i läsvägen) och nedräkningarnas åldrande vid läsning.
 *
 * Körs av test/run.sh när node finns; CI:s tokenserver-jobb kör den via
 * "node --test tools/relay/".
 */
import test from "node:test";
import assert from "node:assert/strict";
import { ageCountdowns, indexAdd, mergeTokens, newestBody }
  from "./worker.js";

test("en ensam avsändare passerar orörd", () => {
  const doc = { receivedAt: 100, publisher: "mac",
                body: { v: 2, weekPct: 73, weekObservedAt: 90 } };
  assert.deepEqual(mergeTokens([doc]), doc.body);
});

test("varje pool tas från den maskin som såg den senast", () => {
  const mac = { receivedAt: 200, publisher: "mac", body: {
    v: 2,
    weekPct: 70, weekObservedAt: 150,        // äldre Claude-observation
    codexWeekPct: 41, codexWeekObservedAt: 190,  // färsk Codex (Macen kör Codex)
  } };
  const pc = { receivedAt: 210, publisher: "pc", body: {
    v: 2,
    weekPct: 73, weekObservedAt: 205,        // färsk Claude (PC:n frågade nyss)
    codexWeekPct: 39, codexWeekObservedAt: 100,  // gammal Codex
  } };
  const merged = mergeTokens([mac, pc]);
  assert.equal(merged.weekPct, 73, "Claude ska komma från PC:n");
  assert.equal(merged.codexWeekPct, 41, "Codex ska komma från Macen");
  assert.equal(merged.codexWeekObservedAt, 190,
               "stämpeln ska följa sin pools vinnare");
});

test("en pool bara den ena maskinen känner överlever", () => {
  const utan = { receivedAt: 300, publisher: "pc",
                 body: { v: 2, weekPct: 73, weekObservedAt: 295 } };
  const med = { receivedAt: 250, publisher: "mac",
                body: { v: 2, weekPct: 60, weekObservedAt: 200,
                        codexWeekPct: 41, codexWeekObservedAt: 240 } };
  const merged = mergeTokens([utan, med]);
  assert.equal(merged.codexWeekPct, 41,
               "att PC:n aldrig sett Codex får inte radera Codex-siffran");
  assert.equal(merged.weekPct, 73);
});

test("ostämplade fält följer det nyast mottagna dokumentet", () => {
  const gammal = { receivedAt: 100, publisher: "mac",
                   body: { v: 2, daySessions: 4 } };
  const ny = { receivedAt: 200, publisher: "pc",
               body: { v: 2, daySessions: 9 } };
  assert.equal(mergeTokens([gammal, ny]).daySessions, 9);
});

test("döda och korrupta dokument tystar inte de andra", () => {
  const frisk = { receivedAt: 100, publisher: "mac",
                  body: { v: 2, weekPct: 73 } };
  assert.equal(mergeTokens([null, { receivedAt: 1, body: null },
                            frisk]).weekPct, 73);
  assert.equal(mergeTokens([]), null);
  assert.equal(mergeTokens([null]), null);
});

test("newestBody är nyast mottagna, inget annat", () => {
  const a = { receivedAt: 100, publisher: "mac", body: { streak: 3 } };
  const b = { receivedAt: 200, publisher: "pc", body: { streak: 1 } };
  assert.equal(newestBody([a, b]).streak, 1);
  assert.equal(newestBody([]), null);
});

/* ---------------------------------------------------------------- index --
 * Indexet finns för att läsvägen aldrig ska lista. Det enda som får kosta
 * en skrivning är en avsändare som faktiskt är ny.
 */

test("en ny avsändare ger en lista att skriva", () => {
  assert.deepEqual(indexAdd(null, "mac"), ["mac"]);
  assert.deepEqual(indexAdd([], "mac"), ["mac"]);
  assert.deepEqual(indexAdd(["mac"], "pc"), ["mac", "pc"]);
});

test("en känd avsändare kostar ingen skrivning", () => {
  assert.equal(indexAdd(["mac", "pc"], "mac"), null);
  assert.equal(indexAdd(["mac"], "mac"), null);
});

test("ett fullt index tar inte in fler — ingen skrivstorm", () => {
  const full = ["a", "b", "c", "d", "e", "f", "g", "h"];
  assert.equal(indexAdd(full, "i"), null,
               "att knuffa ut den äldsta hade lagt tillbaka den vid nästa " +
               "POST och skrivit i all evighet");
  assert.equal(indexAdd(full, "c"), null);
});

test("skräp i indexet städas bort i stället för att skrivas vidare", () => {
  assert.deepEqual(indexAdd(["mac", "", null, 7, "pc"], "ny"),
                   ["mac", "pc", "ny"]);
});

/* ------------------------------------------------------- nedräkningarna --
 * Åldrandet är samma subtraktion tjänsten hade gjort, gjord vid läsning i
 * stället för vid publicering: det är det som gör att avsändaren kan tiga
 * i en kvart utan att glaset ljuger om när kvoten nollas.
 */

test("nedräkningar räknas ned med dokumentets ålder", () => {
  const body = { v: 2, claudeWeekResetMin: 600, codexSessionResetMin: 45 };
  const aged = ageCountdowns(body, 10 * 60);
  assert.equal(aged.claudeWeekResetMin, 590);
  assert.equal(aged.codexSessionResetMin, 35);
  assert.equal(body.claudeWeekResetMin, 600, "originalet ska inte muteras");
});

test("en färsk kropp lämnas orörd", () => {
  const body = { v: 2, claudeWeekResetMin: 600 };
  assert.equal(ageCountdowns(body, 0), body);
  assert.equal(ageCountdowns(body, 25), body, "under en halv minut = noll");
  assert.equal(ageCountdowns(body, -5), body, "klockan bakåt rör ingenting");
});

test("en passerad nedräkning blir null, inte ett negativt tal", () => {
  const aged = ageCountdowns({ claudeSessionResetMin: 5 }, 20 * 60);
  assert.equal(aged.claudeSessionResetMin, null);
});

test("bara ResetMin åldras — stämplar och prognoser är absoluta", () => {
  const body = {
    v: 2,
    claudeWeekPct: 73,
    weekObservedAt: 1_700_000_000,
    claudeForecastAt: 1_700_003_600,
    claudeForecastOffsetMin: -540,
    claudeSessionResetMin: null,
  };
  assert.deepEqual(ageCountdowns(body, 30 * 60), body);
});
