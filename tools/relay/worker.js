/*
 * VibePulse-brevlådan: en Cloudflare Worker + KV som håller tjänstens
 * senaste siffror så panelen kan hämta dem från vilket nät som helst.
 *
 * Rollen är medvetet dum: den kan ingenting, vet ingenting och har inga
 * nycklar. Tjänsten (tools/tokenserver --publish) POSTar färdiga JSON-
 * kroppar hit; panelen GETtar dem när LAN:et inte svarar. Det som ligger
 * här är SIFFROR — kvot, burn rate, Max Tracker, GitHub. Agentstatus och
 * Needs You publiceras aldrig (firmwarens test/test_relay_boundary.py och
 * tjänstens publisher.py håller den gränsen från var sitt håll).
 *
 * Åtkomstkontrollen ÄR sökvägen: /u/<hemlighet>/api/... där hemligheten
 * är minst 32 slumpade byte ur secrets.h (TK_VIBEPULSE_RELAY_URL). Samma
 * skyddsnivå som en privat delningslänk — rätt nivå för procentsiffror,
 * och skälet till att inget känsligare än siffror får bo här.
 *
 * Flera avsändare (en Mac som sover, en alltid-på-PC) publicerar till
 * samma brevlåda under eget namn. Läsningen slår ihop dem:
 *
 *   /api/tokens      — färskast VINNER PER POOL: varje kvotpool bär redan
 *                      sin egen observationsstämpel (weekObservedAt,
 *                      modelObservedAt, ... — byggda för stalenesslogiken),
 *                      så Codex-siffran kan komma från Macen som körde
 *                      Codex senast medan Claude-siffran kommer från PC:n
 *                      som frågade Anthropic för tio sekunder sedan.
 *   /api/max-tracker — nyast mottagna dokument vinner helt. Historiken är
 *                      per maskin; en dag-för-dag-sammanslagning vore att
 *                      hitta på data ingen maskin har sett. Ärlig gräns:
 *                      kör du Max Tracker-historik från två maskiner är
 *                      det den senast publicerande som syns.
 *   /api/github      — nyast vinner. Båda frågar samma publika API.
 *
 * KV:S FRIA NIVÅ HAR TVÅ HINKAR, inte en: 100 000 läsningar per dygn, men
 * bara 1 000 skrivningar OCH 1 000 LISTNINGAR (Cloudflares eget prisblad
 * räknar write/delete/list i samma klass). Läsvägen fick därför aldrig
 * lista: panelen pollar /api/tokens och /api/github var 30:e sekund och
 * /api/max-tracker var 5:e minut, vilket är ~6 000 listningar per dygn mot
 * en gräns på 1 000 — kontot slog i taket före lunch (2026-08-21, se
 * docs/lessons.md). I stället håller varje endpoint ett eget INDEX över
 * sina avsändare: en läsning i stället för en listning, och en skrivning
 * bara när avsändarskaran faktiskt ändras.
 *
 * Deploy: se README.md i den här katalogen. KV-bindningen heter VIBEPULSE.
 */

const ENDPOINTS = ["/api/tokens", "/api/max-tracker", "/api/github"];
const MAX_BODY_BYTES = 64 * 1024; // largest honest payload is ~8 kB
const MAX_PUBLISHERS = 8;

/*
 * Indexnyckeln ligger under ett eget prefix, inte under "<endpoint>:", så
 * den aldrig kan förväxlas med en avsändare som råkar heta något visst:
 * avsändarnamn saneras till [A-Za-z0-9._-] och kan alltså aldrig innehålla
 * ett "/", medan varje indexnyckel gör det.
 */
const INDEX_PREFIX = "index:";

function indexKey(endpoint) {
  return INDEX_PREFIX + endpoint;
}

/*
 * Indexets enda regel, som ren funktion så testerna kan hålla den stilla.
 * Returnerar den nya listan när den behöver skrivas, annars null — "null =
 * ingen skrivning" är hela poängen med hinkarna ovan.
 *
 * En FULL brevlåda tar inte in fler. Det är ett medvetet val framför att
 * knuffa ut den äldsta: en utknuffad avsändare hade lagts till igen vid
 * nästa POST, knuffat ut nästa, och så vidare — en skrivstorm i exakt den
 * hink vi försöker skydda. Gränsen är densamma som läsningen alltid haft
 * (åtta dokument), bara ärlig nu: avsändare nio syns inte på glaset.
 */
export function indexAdd(names, publisher) {
  const clean = (names || []).filter((n) => typeof n === "string" && n !== "");
  if (clean.includes(publisher)) return null;
  if (clean.length >= MAX_PUBLISHERS) return null;
  return clean.concat([publisher]);
}

/*
 * Nedräkningarna åldras vid LÄSNING, inte vid publicering.
 *
 * Payloaden bär "minuter kvar till nollning" (claudeWeekResetMin, ...) —
 * ett tal som tickar av sig självt en gång i minuten utan att något
 * verkligt hänt. Publiceras det som en förändring blir det 1 440 skrivningar
 * per dygn av ren aritmetik. Brevlådan vet när dokumentet kom in, så den kan
 * i stället räkna ned det själv: exakt samma subtraktion tjänsten hade gjort,
 * och en nedräkning som stämmer på sekunden hur sällan avsändaren än hör av
 * sig. En nedräkning som passerat noll blir null — samma svar tjänsten ger
 * när fönstret redan nollats (och firmwarens reset_or_null visar streck).
 */
export function ageCountdowns(body, agedSeconds) {
  if (!(agedSeconds > 0)) return body;
  const minutes = Math.round(agedSeconds / 60);
  if (minutes <= 0) return body;

  let out = null;
  for (const key of Object.keys(body)) {
    if (!key.endsWith("ResetMin")) continue;
    const value = body[key];
    if (typeof value !== "number" || !Number.isFinite(value)) continue;
    if (out === null) out = { ...body };
    const left = value - minutes;
    out[key] = left > 0 ? left : null;
  }
  return out === null ? body : out;
}

/*
 * Sammanslagningen för /api/tokens, som ren funktion så testerna kan hålla
 * den stilla (node --test i test.mjs — ingen Cloudflare behövs).
 *
 * Regeln: basen är det nyast MOTTAGNA dokumentet (fält utan stämpel följer
 * det). Sedan får varje observationsstämplad fältgrupp — prefixet framför
 * "ObservedAt", t.ex. week/model/codexWeek — sina fält från det dokument
 * vars stämpel för JUST den gruppen är färskast. En grupp ett dokument
 * saknar lämnas orörd: att en maskin aldrig sett Codex betyder inte att
 * Codex-siffran ska försvinna.
 */
export function mergeTokens(docs) {
  const alive = docs.filter((d) => d && typeof d.body === "object" &&
                                   d.body !== null);
  if (alive.length === 0) return null;
  alive.sort((a, b) => (b.receivedAt || 0) - (a.receivedAt || 0));
  const merged = { ...alive[0].body };

  const groups = new Set();
  for (const doc of alive)
    for (const key of Object.keys(doc.body))
      if (key.endsWith("ObservedAt")) groups.add(key.slice(0, -10));

  for (const group of groups) {
    const stamp = group + "ObservedAt";
    let winner = null;
    for (const doc of alive) {
      const at = doc.body[stamp];
      if (typeof at !== "number") continue;
      if (winner === null || at > winner.body[stamp]) winner = doc;
    }
    if (winner === null) continue;
    for (const key of Object.keys(winner.body))
      if (key === stamp || (key.startsWith(group) &&
                            !key.endsWith("ObservedAt")))
        merged[key] = winner.body[key];
  }
  return merged;
}

/* Nyast mottagna dokument, för endpoints utan sammanslagning. */
export function newestBody(docs) {
  const alive = docs.filter((d) => d && typeof d.body === "object" &&
                                   d.body !== null);
  if (alive.length === 0) return null;
  alive.sort((a, b) => (b.receivedAt || 0) - (a.receivedAt || 0));
  return alive[0].body;
}

function parsePath(url, secret) {
  const prefix = `/u/${secret}`;
  const path = new URL(url).pathname;
  if (!path.startsWith(prefix + "/")) return null;
  const endpoint = path.slice(prefix.length);
  return ENDPOINTS.includes(endpoint) ? endpoint : null;
}

async function readIndex(env, endpoint) {
  const raw = await env.VIBEPULSE.get(indexKey(endpoint));
  if (raw === null) return null;
  try {
    const names = JSON.parse(raw);
    if (!Array.isArray(names)) return null;
    return names.filter((n) => typeof n === "string" && n !== "");
  } catch {
    return null; /* trasigt index behandlas som inget index */
  }
}

/*
 * Den enda listningen som finns kvar, och den körs bara när ett index
 * saknas: en brevlåda som fylldes före den här versionen, eller en vars
 * indexnyckel gått förlorad. POST:en nedan skriver då indexet, och det
 * blir den sista listning endpointen någonsin gör.
 */
async function listPublishers(env, endpoint) {
  const listed = await env.VIBEPULSE.list({ prefix: `${endpoint}:` });
  return listed.keys.map((k) => k.name.slice(endpoint.length + 1))
                    .filter((n) => n !== "")
                    .slice(0, MAX_PUBLISHERS);
}

async function notePublisher(env, endpoint, publisher) {
  let names = await readIndex(env, endpoint);
  if (names === null) names = await listPublishers(env, endpoint);
  const updated = indexAdd(names, publisher);
  if (updated === null) return;
  // KV-läsningar cachas i upp till 60 s vid kanten, så den allra första
  // avsändaren kan hinna skriva indexet ett par gånger innan skrivningen
  // syns för nästa POST. Det är begränsat till en handfull skrivningar en
  // gång per endpoints livstid — priset för att slippa listningen helt.
  await env.VIBEPULSE.put(indexKey(endpoint), JSON.stringify(updated));
}

async function readDocs(env, endpoint, nowSeconds) {
  let names = await readIndex(env, endpoint);
  if (names === null) names = await listPublishers(env, endpoint);

  const docs = [];
  for (const name of names.slice(0, MAX_PUBLISHERS)) {
    const raw = await env.VIBEPULSE.get(`${endpoint}:${name}`);
    if (!raw) continue; /* en avsändare i indexet utan dokument: hoppa */
    try {
      const doc = JSON.parse(raw);
      if (doc && typeof doc.body === "object" && doc.body !== null &&
          typeof doc.receivedAt === "number")
        doc.body = ageCountdowns(doc.body, nowSeconds - doc.receivedAt);
      docs.push(doc);
    } catch {
      /* ett korrupt dokument tystar inte de andra */
    }
  }
  return docs;
}

export default {
  async fetch(request, env) {
    // Hemligheten är en Worker-secret (wrangler secret put RELAY_SECRET),
    // aldrig kod. Utan den svarar brevlådan ingenting alls.
    const secret = env.RELAY_SECRET;
    if (!secret || secret.length < 32)
      return new Response("relay not configured", { status: 503 });

    const endpoint = parsePath(request.url, secret);
    if (endpoint === null) return new Response("not found", { status: 404 });

    if (request.method === "POST" || request.method === "PUT") {
      const publisher =
          (request.headers.get("X-VibePulse-Publisher") || "unnamed")
              .slice(0, 64).replace(/[^A-Za-z0-9._-]/g, "_");
      const raw = await request.text();
      if (raw.length > MAX_BODY_BYTES)
        return new Response("too large", { status: 413 });
      let body;
      try {
        body = JSON.parse(raw);
      } catch {
        return new Response("not json", { status: 400 });
      }
      const doc = JSON.stringify({ receivedAt: Date.now() / 1000,
                                   publisher, body });
      await env.VIBEPULSE.put(`${endpoint}:${publisher}`, doc);
      // Indexet får aldrig fälla en publicering som redan landat: ett
      // misslyckat 200-svar hade fått avsändaren att skicka om SAMMA kropp
      // vid nästa tick och betala en skrivning till. Utan index läser
      // GET:en via listningen tills nästa POST lyckas skriva det.
      try {
        await notePublisher(env, endpoint, publisher);
      } catch {
        /* nästa POST försöker igen */
      }
      return new Response("ok", { status: 200 });
    }

    if (request.method === "GET") {
      const docs = await readDocs(env, endpoint, Date.now() / 1000);
      const merged = endpoint === "/api/tokens" ? mergeTokens(docs)
                                                : newestBody(docs);
      if (merged === null)
        return new Response(JSON.stringify({ error: "no data yet" }),
                            { status: 404,
                              headers: { "Content-Type": "application/json" } });
      return new Response(JSON.stringify(merged),
                          { headers: { "Content-Type": "application/json" } });
    }

    return new Response("method not allowed", { status: 405 });
  },
};
