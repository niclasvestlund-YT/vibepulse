"use strict";

const SVG_NS = "http://www.w3.org/2000/svg";
const MIN_TEXT_ROW_STEP = 26;
const MIN_SECTION_GAP = 8;
// Browser SVG and LVGL use different font rasterizers. These measured offsets
// align visible IBM Plex Sans ink with the LVGL simulator without changing the
// firmware layout tokens that remain the geometry authority.
const LVGL_WEB_FONT_METRIC_Y = Object.freeze({
  provider: 5,
  model: 7,
  quota: 5,
  today: 8,
  percent: -14,
  reset: 5,
  status: 2,
});
const EXPORT_NAMES = Object.freeze([
  "claude-hero",
  "codex-hero",
  "claude-details",
  "overview",
  "claude-hero-stale",
  "codex-hero-stale",
  "claude-hero-missing",
  "codex-hero-missing",
]);

const CONTROL_SPECS = Object.freeze([
  { name: "safeX", label: "Safe inset" },
  { name: "providerY", label: "Provider Y" },
  { name: "quotaY", label: "Quota Y" },
  { name: "percentY", label: "Percent Y" },
  { name: "barY", label: "Bar Y" },
  { name: "barHeight", label: "Bar height" },
  { name: "resetY", label: "Reset Y" },
  { name: "statusY", label: "Status Y" },
  { name: "statusHeight", label: "Status height" },
]);

const state = {
  design: null,
  hardware: null,
  headerDigest: null,
  selection: {provider: "claude", condition: "live"},
  scale: 1,
  mutationToken: "",
  exportFontCss: null,
  operationActive: false,
};

function takeMutationToken() {
  const rawFragment = window.location.hash.startsWith("#")
    ? window.location.hash.slice(1)
    : "";
  const params = new URLSearchParams(rawFragment);
  const token = params.get("mutation-token") || "";
  if (token) {
    const cleanUrl = `${window.location.pathname}${window.location.search}`;
    window.history.replaceState(null, document.title, cleanUrl);
  }
  return token;
}

function mutationHeaders(base = {}) {
  const headers = {...base};
  if (state.mutationToken) {
    headers["X-VibePulse-Studio-Token"] = state.mutationToken;
  }
  return headers;
}

function setOperationStatus(message, kind = "neutral") {
  const output = document.querySelector("#operation-status");
  output.textContent = message;
  output.dataset.kind = kind;
}

async function responsePayload(response) {
  try {
    return await response.json();
  } catch (_error) {
    return {error: `Server returned HTTP ${response.status}`};
  }
}

function requireSuccessful(response, payload) {
  if (!response.ok) {
    throw new Error(payload.error || `Server returned HTTP ${response.status}`);
  }
  return payload;
}

function svgNode(tagName, attributes = {}, text = null) {
  const node = document.createElementNS(SVG_NS, tagName);
  for (const [name, value] of Object.entries(attributes)) {
    node.setAttribute(name, String(value));
  }
  if (text !== null) {
    node.textContent = String(text);
  }
  return node;
}

function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, value));
}

function heroIsServerValid(hero, width, height) {
  const names = [
    "safeX", "contentWidth", "providerY", "quotaY", "percentY",
    "percentFontPx", "barY", "barHeight", "resetY", "statusY",
    "statusHeight",
  ];
  if (!names.every((name) => Number.isInteger(hero[name]))) {
    return false;
  }
  if (hero.safeX < 16 || hero.safeX > 40
      || hero.contentWidth !== width - 2 * hero.safeX
      || hero.contentWidth < 1 || hero.contentWidth > width
      || hero.percentFontPx < 1 || hero.percentFontPx > height
      || hero.barHeight < 12 || hero.barHeight > 24
      || hero.statusHeight < 1 || hero.statusHeight > height) {
    return false;
  }
  const positions = [
    hero.providerY, hero.quotaY, hero.percentY, hero.barY,
    hero.resetY, hero.statusY,
  ];
  if (!positions.every((value) => value >= 0 && value < height)) {
    return false;
  }
  return hero.providerY + MIN_TEXT_ROW_STEP <= hero.quotaY
    && hero.quotaY + MIN_TEXT_ROW_STEP <= hero.percentY
    && hero.percentY + hero.percentFontPx + MIN_SECTION_GAP <= hero.barY
    && hero.barY + hero.barHeight <= height
    && hero.barY + hero.barHeight + MIN_SECTION_GAP <= hero.resetY
    && hero.resetY + MIN_TEXT_ROW_STEP <= hero.statusY
    && hero.statusY + hero.statusHeight <= height;
}

function heroBounds(hero, name, width, height) {
  switch (name) {
    case "safeX":
      return {min: 16, max: 40};
    case "providerY":
      return {min: 0, max: hero.quotaY - MIN_TEXT_ROW_STEP};
    case "quotaY":
      return {
        min: hero.providerY + MIN_TEXT_ROW_STEP,
        max: hero.percentY - MIN_TEXT_ROW_STEP,
      };
    case "percentY":
      return {
        min: hero.quotaY + MIN_TEXT_ROW_STEP,
        max: hero.barY - hero.percentFontPx - MIN_SECTION_GAP,
      };
    case "barY":
      return {
        min: hero.percentY + hero.percentFontPx + MIN_SECTION_GAP,
        max: hero.resetY - hero.barHeight - MIN_SECTION_GAP,
      };
    case "barHeight":
      return {
        min: 12,
        max: Math.min(24, hero.resetY - hero.barY - MIN_SECTION_GAP),
      };
    case "resetY":
      return {
        min: hero.barY + hero.barHeight + MIN_SECTION_GAP,
        max: hero.statusY - MIN_TEXT_ROW_STEP,
      };
    case "statusY":
      return {
        min: hero.resetY + MIN_TEXT_ROW_STEP,
        max: height - hero.statusHeight,
      };
    case "statusHeight":
      return {min: 1, max: height - hero.statusY};
    default:
      return null;
  }
}

function applyHeroChange(hero, name, requested, width, height) {
  const bounds = heroBounds(hero, name, width, height);
  if (!bounds || !Number.isInteger(requested) || bounds.min > bounds.max) {
    return {hero: {...hero}, bounds, accepted: false};
  }
  const value = clamp(requested, bounds.min, bounds.max);
  const candidate = {...hero, [name]: value};
  if (name === "safeX") {
    candidate.contentWidth = width - 2 * candidate.safeX;
  }
  if (!heroIsServerValid(candidate, width, height)) {
    return {hero: {...hero}, bounds, accepted: false};
  }
  return {hero: candidate, bounds, accepted: true};
}

function createGeometryControls() {
  const container = document.querySelector("#geometry-controls");
  const fragment = document.createDocumentFragment();
  for (const specification of CONTROL_SPECS) {
    const label = document.createElement("label");
    label.className = "geometry-field";
    label.htmlFor = `geometry-${specification.name}`;
    label.append(document.createTextNode(specification.label));

    const input = document.createElement("input");
    input.type = "number";
    input.id = `geometry-${specification.name}`;
    input.name = specification.name;
    input.step = "1";
    input.addEventListener("input", () => {
      if (state.operationActive) {
        refreshGeometryControls();
        return;
      }
      const {width, height} = state.hardware.display;
      const result = applyHeroChange(
        state.design.hero,
        specification.name,
        input.valueAsNumber,
        width,
        height,
      );
      if (result.accepted) {
        state.design = {...state.design, hero: result.hero};
      }
      refreshGeometryControls();
      render(state.design, state.selection);
    });
    label.append(input);
    fragment.append(label);
  }
  container.replaceChildren(fragment);
  refreshGeometryControls();
}

function refreshGeometryControls() {
  const {width, height} = state.hardware.display;
  for (const specification of CONTROL_SPECS) {
    const input = document.querySelector(`#geometry-${specification.name}`);
    const bounds = heroBounds(
      state.design.hero,
      specification.name,
      width,
      height,
    );
    input.min = String(bounds.min);
    input.max = String(bounds.max);
    input.value = String(state.design.hero[specification.name]);
  }
}

function textStyle(size, fill, weight = 600) {
  return {
    fill,
    "font-family": "IBM Plex Sans Local, sans-serif",
    "font-size": size,
    "font-weight": weight,
    "dominant-baseline": "hanging",
  };
}

function statusDotGeometry(hero) {
  const dotRadius = Math.max(3, Math.floor(hero.barHeight / 4));
  const haloRadius = dotRadius + 2;
  return {
    dotRadius,
    haloRadius,
    centerX: hero.safeX + haloRadius,
  };
}

function lvglTextY(baseY, role) {
  return baseY + LVGL_WEB_FONT_METRIC_Y[role];
}

function heroCopy(fixture, condition) {
  if (condition === "missing") {
    return {
      quotaText: "WEEKLY",
      percentageText: "–",
      todayText: "– TODAY",
      resetText: "USAGE UNAVAILABLE",
      statusText: "NO DATA",
    };
  }
  return {
    quotaText: fixture.quota,
    percentageText: `${fixture.percent}%`,
    todayText: `+${fixture.today}% TODAY`,
    resetText: fixture.reset,
    statusText: condition === "stale" ? "STALE" : "LIVE",
  };
}

function render(design, selection) {
  if (!design || !state.hardware) {
    return;
  }
  const svg = document.querySelector("#device-preview");
  const background = document.querySelector("#screen-background");
  const content = document.querySelector("#hero-content");
  const {width, height} = state.hardware.display;
  const hero = design.hero;
  const fixture = design.fixtures[selection.provider];
  const providerColor = design.palette[selection.provider];
  const isMissing = selection.condition === "missing";
  const isStale = selection.condition === "stale";
  const visiblePercent = isMissing ? 0 : fixture.percent;
  const {quotaText, percentageText, todayText, resetText, statusText} =
    heroCopy(fixture, selection.condition);
  const labelSize = Math.max(18, Math.round(hero.percentFontPx * 0.15));
  const smallSize = Math.max(13, Math.round(labelSize * 0.72));
  const percentSize = isMissing ? labelSize : hero.percentFontPx;
  const contentRight = hero.safeX + hero.contentWidth;
  const barRadius = Math.floor(hero.barHeight / 2);
  const statusCenter = hero.statusY + Math.floor(hero.statusHeight / 2);
  const statusDot = statusDotGeometry(hero);

  svg.style.setProperty("--background", design.palette.background);
  document.querySelector("#preview-svg-title").textContent =
    `${fixture.provider} ${statusText}`;
  document.querySelector("#preview-svg-description").textContent =
    `${quotaText}, ${percentageText}, ${todayText}, ${resetText}.`;
  background.setAttribute("fill", design.palette.background);
  background.setAttribute("width", String(width));
  background.setAttribute("height", String(height));

  const nodes = [
    svgNode("text", {
      x: hero.safeX,
      y: lvglTextY(hero.providerY, "provider"),
      ...textStyle(labelSize, design.palette.text, 700),
      "letter-spacing": Math.max(1, Math.round(labelSize * 0.08)),
    }, fixture.provider),
    svgNode("text", {
      x: contentRight,
      y: lvglTextY(hero.providerY, "model"),
      ...textStyle(smallSize, design.palette.muted),
      "text-anchor": "end",
      "letter-spacing": Math.max(1, Math.round(smallSize * 0.07)),
    }, `${fixture.model} · ${fixture.effort}`),
    svgNode("text", {
      x: hero.safeX,
      y: lvglTextY(hero.quotaY, "quota"),
      ...textStyle(labelSize, design.palette.muted),
      "letter-spacing": Math.max(1, Math.round(labelSize * 0.06)),
    }, quotaText),
    svgNode("text", {
      x: contentRight,
      y: lvglTextY(hero.quotaY, "today"),
      ...textStyle(smallSize, providerColor),
      "text-anchor": "end",
      "letter-spacing": Math.max(1, Math.round(smallSize * 0.06)),
    }, todayText),
    svgNode("text", {
      x: hero.safeX,
      y: lvglTextY(hero.percentY, "percent"),
      ...textStyle(percentSize, design.palette.text, 700),
      "letter-spacing": Math.round(percentSize * -0.04),
    }, percentageText),
    svgNode("rect", {
      x: hero.safeX,
      y: hero.barY,
      width: hero.contentWidth,
      height: hero.barHeight,
      rx: barRadius,
      fill: design.palette.track,
    }),
    svgNode("rect", {
      x: hero.safeX,
      y: hero.barY,
      width: Math.round(hero.contentWidth * visiblePercent / 100),
      height: hero.barHeight,
      rx: barRadius,
      fill: providerColor,
    }),
    svgNode("text", {
      x: hero.safeX,
      y: lvglTextY(hero.resetY, "reset"),
      ...textStyle(labelSize, design.palette.text),
      "letter-spacing": Math.max(1, Math.round(labelSize * 0.05)),
    }, resetText),
    svgNode("circle", {
      cx: statusDot.centerX,
      cy: statusCenter,
      r: statusDot.haloRadius,
      fill: design.palette.hairline,
    }),
    svgNode("circle", {
      cx: statusDot.centerX,
      cy: statusCenter,
      r: statusDot.dotRadius,
      fill: isMissing ? design.palette.muted : providerColor,
    }),
    svgNode("text", {
      x: statusDot.centerX + statusDot.haloRadius + statusDot.dotRadius,
      y: lvglTextY(statusCenter, "status"),
      ...textStyle(smallSize, isMissing || isStale
        ? design.palette.muted
        : providerColor),
      "dominant-baseline": "middle",
      "letter-spacing": Math.max(1, Math.round(smallSize * 0.08)),
    }, statusText),
  ];
  content.replaceChildren(...nodes);
  document.querySelector("#state-summary").textContent =
    `${fixture.provider} · ${statusText}`;
  updatePressedStates();
}

function configureCanvas() {
  const svg = document.querySelector("#device-preview");
  const background = document.querySelector("#screen-background");
  const {width, height} = state.hardware.display;
  svg.setAttribute("width", String(width));
  svg.setAttribute("height", String(height));
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  background.setAttribute("width", String(width));
  background.setAttribute("height", String(height));
  document.querySelector("#preview-title").textContent =
    `Preview: ${width} × ${height}`;
  setScale(state.scale);
}

function setScale(scale) {
  if (!state.hardware || state.operationActive) {
    return;
  }
  const {width, height} = state.hardware.display;
  const frame = document.querySelector("#preview-frame");
  state.scale = scale === 2 ? 2 : 1;
  frame.className = `scale-${state.scale}`;
  frame.style.width = `${width * state.scale}px`;
  frame.style.height = `${height * state.scale}px`;
  document.querySelector("#zoom-warning").hidden = state.scale === 1;
  for (const button of document.querySelectorAll("[data-scale]")) {
    button.setAttribute("aria-pressed", String(Number(button.dataset.scale) === state.scale));
  }
}

function updatePressedStates() {
  for (const button of document.querySelectorAll("[data-state]")) {
    const buttonState = button.dataset.state;
    const pressed = buttonState === state.selection.condition
      || (state.selection.condition === "live" && buttonState === state.selection.provider);
    button.setAttribute("aria-pressed", String(pressed));
  }
}

function bytesToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  const chunks = [];
  const chunkSize = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    chunks.push(String.fromCharCode(...bytes.subarray(offset, offset + chunkSize)));
  }
  return window.btoa(chunks.join(""));
}

async function embeddedFontFace(path, weight) {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(`Could not load local export font (${response.status})`);
  }
  const buffer = await response.arrayBuffer();
  const signatureBytes = new Uint8Array(buffer, 0, Math.min(4, buffer.byteLength));
  const fontSignature = String.fromCharCode(...signatureBytes);
  if (fontSignature !== "wOF2") {
    throw new Error("Local export font is not a valid WOFF2 file");
  }
  const encoded = bytesToBase64(buffer);
  return `@font-face{font-family:'IBM Plex Sans Local';src:url(data:font/woff2;base64,${encoded}) format('woff2');font-style:normal;font-weight:${weight};}`;
}

async function loadExportFontCss() {
  const faces = await Promise.all([
    embeddedFontFace("/fonts/IBMPlexSans-SemiBold.woff2", 600),
    embeddedFontFace("/fonts/IBMPlexSans-Bold.woff2", 700),
  ]);
  return faces.join("");
}

function loadExportFontCssSafely() {
  return loadExportFontCss().then(
    (value) => ({ok: true, value}),
    (error) => ({
      ok: false,
      error: error instanceof Error ? error : new Error(String(error)),
    }),
  );
}

function setMutatingControlsDisabled(disabled) {
  const selector = "[data-state], [data-scale], #geometry-controls input, "
    + "#save-design, #export-png";
  for (const control of document.querySelectorAll(selector)) {
    control.disabled = disabled;
  }
}

function beginOperation() {
  if (state.operationActive) {
    return false;
  }
  state.operationActive = true;
  setMutatingControlsDisabled(true);
  return true;
}

function finishOperation() {
  state.operationActive = false;
  setMutatingControlsDisabled(false);
}

function currentExportName(selection) {
  const suffix = selection.condition === "live"
    ? ""
    : `-${selection.condition}`;
  return `${selection.provider}-hero${suffix}`;
}

async function saveDesign() {
  if (state.operationActive) {
    return;
  }
  const {width, height} = state.hardware.display;
  if (!heroIsServerValid(state.design.hero, width, height)) {
    setOperationStatus("Save blocked: geometry is outside the server contract.", "error");
    return;
  }
  if (!beginOperation()) {
    return;
  }
  const designToSave = JSON.parse(JSON.stringify(state.design));
  setOperationStatus("Saving reviewed tokens…", "pending");
  try {
    const response = await fetch("/api/design", {
      method: "PUT",
      headers: mutationHeaders({"Content-Type": "application/json"}),
      body: JSON.stringify(designToSave),
    });
    const received = await responsePayload(response);
    const payload = requireSuccessful(response, received);
    state.design = payload.design;
    state.headerDigest = payload.headerDigest;
    createGeometryControls();
    render(state.design, state.selection);
    setOperationStatus(
      `Saved. Header ${state.headerDigest.slice(0, 10)}… is synchronized.`,
      "success",
    );
  } catch (error) {
    setOperationStatus(`Save failed: ${error.message}`, "error");
  } finally {
    finishOperation();
  }
}

async function exportPng(name) {
  if (state.operationActive) {
    return;
  }
  if (!EXPORT_NAMES.includes(name)) {
    setOperationStatus("Export failed: state name is not approved.", "error");
    return;
  }
  if (!beginOperation()) {
    return;
  }
  setOperationStatus(`Exporting ${name}…`, "pending");
  let decoded = null;
  try {
    await document.fonts.ready;
    const fontResult = await state.exportFontCss;
    if (!fontResult.ok) {
      throw new Error(
        `Local export fonts unavailable: ${fontResult.error.message}`,
      );
    }
    const fontCss = fontResult.value;
    const {width, height} = state.hardware.display;
    const source = prepareStandaloneSvg(width, height, fontCss);
    const svg = new XMLSerializer().serializeToString(source);
    const blob = new Blob([svg], {type: "image/svg+xml;charset=utf-8"});
    decoded = await decodeStandaloneSvg(blob);
    const canvas = Object.assign(document.createElement("canvas"), {width, height});
    const context = canvas.getContext("2d", {alpha: false});
    if (!context) {
      throw new Error("Browser could not create a canvas context");
    }
    context.drawImage(decoded.image, 0, 0, width, height);
    const png = await new Promise((resolve, reject) => {
      canvas.toBlob((result) => {
        if (result) {
          resolve(result);
        } else {
          reject(new Error("Browser could not encode the PNG"));
        }
      }, "image/png");
    });
    const response = await fetch(`/api/export/${name}`, {
      method: "POST",
      headers: mutationHeaders({"Content-Type": "image/png"}),
      body: png,
    });
    const received = await responsePayload(response);
    const payload = requireSuccessful(response, received);
    setOperationStatus(
      `Exported ${payload.state} · ${payload.width} × ${payload.height}.`,
      "success",
    );
  } catch (error) {
    setOperationStatus(`Export failed: ${error.message}`, "error");
  } finally {
    if (decoded) {
      decoded.release();
    }
    finishOperation();
  }
}

function resolvePreviewStyles(live, source) {
  const liveNodes = [live, ...live.querySelectorAll("*")];
  const sourceNodes = [source, ...source.querySelectorAll("*")];
  for (let index = 0; index < liveNodes.length; index += 1) {
    const liveNode = liveNodes[index];
    const sourceNode = sourceNodes[index];
    const computed = window.getComputedStyle(liveNode);
    if (sourceNode.tagName.toLowerCase() === "text") {
      sourceNode.setAttribute("fill", computed.fill);
      sourceNode.setAttribute("font-family", computed.fontFamily);
      sourceNode.setAttribute("font-size", computed.fontSize);
      sourceNode.setAttribute("font-weight", computed.fontWeight);
      sourceNode.setAttribute("letter-spacing", computed.letterSpacing);
    } else if (sourceNode.hasAttribute("fill")) {
      sourceNode.setAttribute("fill", computed.fill);
    }
  }
}

function prepareStandaloneSvg(width, height, fontCss) {
  const live = document.querySelector("#device-preview");
  const source = live.cloneNode(true);
  resolvePreviewStyles(live, source);
  source.removeAttribute("style");
  source.setAttribute("xmlns", SVG_NS);
  source.setAttribute("width", String(width));
  source.setAttribute("height", String(height));
  source.setAttribute("viewBox", `0 0 ${width} ${height}`);
  const definitions = svgNode("defs");
  const style = svgNode("style", {type: "text/css"}, fontCss);
  definitions.append(style);
  source.insertBefore(definitions, source.firstChild);
  return source;
}

function decodeSvgWithImage(blob) {
  const objectUrl = URL.createObjectURL(blob);
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve({
      image,
      release: () => URL.revokeObjectURL(objectUrl),
    });
    image.onerror = () => {
      URL.revokeObjectURL(objectUrl);
      reject(new Error("Browser could not decode the standalone SVG"));
    };
    image.src = objectUrl;
  });
}

async function decodeStandaloneSvg(blob) {
  if (typeof createImageBitmap === "function") {
    try {
      const bitmap = await createImageBitmap(blob);
      return {image: bitmap, release: () => bitmap.close()};
    } catch (_error) {
      // Chromium can reject SVG blobs containing embedded fonts. The image
      // element decoder uses a separate path and retains the same local blob.
    }
  }
  return decodeSvgWithImage(blob);
}

function bindControls() {
  for (const button of document.querySelectorAll("[data-state]")) {
    button.addEventListener("click", () => {
      if (state.operationActive) {
        return;
      }
      const next = button.dataset.state;
      if (next === "claude" || next === "codex") {
        state.selection = {provider: next, condition: "live"};
      } else {
        state.selection = {...state.selection, condition: next};
      }
      render(state.design, state.selection);
    });
  }
  for (const button of document.querySelectorAll("[data-scale]")) {
    button.addEventListener("click", () => {
      if (state.operationActive) {
        return;
      }
      setScale(Number(button.dataset.scale));
    });
  }
  document.querySelector("#save-design").addEventListener("click", saveDesign);
  document.querySelector("#export-png").addEventListener("click", () => {
    exportPng(currentExportName(state.selection));
  });
}

async function loadJson(path) {
  const response = await fetch(path);
  const payload = await responsePayload(response);
  return requireSuccessful(response, payload);
}

async function initialize() {
  state.mutationToken = takeMutationToken();
  bindControls();
  state.exportFontCss = loadExportFontCssSafely();
  try {
    const [design, hardware] = await Promise.all([
      loadJson("/api/design"),
      loadJson("/api/hardware"),
    ]);
    state.design = design;
    state.hardware = hardware;
    configureCanvas();
    createGeometryControls();
    render(state.design, state.selection);
    setOperationStatus("Ready at true 1:1 panel pixels.", "success");
  } catch (error) {
    setOperationStatus(`Studio failed to load: ${error.message}`, "error");
    setMutatingControlsDisabled(true);
  }
}

if (typeof window !== "undefined" && typeof document !== "undefined") {
  initialize();
}
