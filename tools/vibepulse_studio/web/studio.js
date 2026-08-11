"use strict";

const SVG_NS = "http://www.w3.org/2000/svg";
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
  mutationToken: takeMutationToken(),
  exportFontCss: null,
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

function boundsFor(name) {
  const height = state.hardware.display.height;
  if (name === "safeX") {
    return {min: 16, max: 40};
  }
  if (name === "barHeight") {
    return {min: 12, max: 24};
  }
  if (name === "statusHeight") {
    return {min: 1, max: height};
  }
  return {min: 0, max: height - 1};
}

function createGeometryControls() {
  const container = document.querySelector("#geometry-controls");
  const fragment = document.createDocumentFragment();
  for (const specification of CONTROL_SPECS) {
    const label = document.createElement("label");
    label.className = "geometry-field";
    label.htmlFor = `geometry-${specification.name}`;
    label.append(document.createTextNode(specification.label));

    const bounds = boundsFor(specification.name);
    const input = document.createElement("input");
    input.type = "number";
    input.id = `geometry-${specification.name}`;
    input.name = specification.name;
    input.min = String(bounds.min);
    input.max = String(bounds.max);
    input.step = "1";
    input.value = String(state.design.hero[specification.name]);
    input.addEventListener("input", () => {
      const parsed = Number.parseInt(input.value, 10);
      if (!Number.isFinite(parsed)) {
        return;
      }
      const next = clamp(parsed, bounds.min, bounds.max);
      input.value = String(next);
      state.design.hero[specification.name] = next;
      if (specification.name === "safeX") {
        const width = state.hardware.display.width;
        state.design.hero.contentWidth = width - 2 * next;
      }
      render(state.design, state.selection);
    });
    label.append(input);
    fragment.append(label);
  }
  container.replaceChildren(fragment);
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
  const percentageText = isMissing ? "—" : `${fixture.percent}%`;
  const todayText = isMissing ? "— TODAY" : `+${fixture.today}% TODAY`;
  const statusText = isMissing ? "NO DATA" : (isStale ? "STALE" : "LIVE");
  const labelSize = Math.max(18, Math.round(hero.percentFontPx * 0.15));
  const smallSize = Math.max(13, Math.round(labelSize * 0.72));
  const contentRight = hero.safeX + hero.contentWidth;
  const barRadius = Math.floor(hero.barHeight / 2);
  const statusCenter = hero.statusY + Math.floor(hero.statusHeight / 2);
  const dotRadius = Math.max(3, Math.floor(hero.barHeight / 4));

  svg.style.setProperty("--background", design.palette.background);
  svg.setAttribute("aria-label", `${fixture.provider} ${statusText} usage preview`);
  background.setAttribute("fill", design.palette.background);
  background.setAttribute("width", String(width));
  background.setAttribute("height", String(height));

  const nodes = [
    svgNode("text", {
      x: hero.safeX,
      y: hero.providerY,
      ...textStyle(labelSize, design.palette.text, 700),
      "letter-spacing": Math.max(1, Math.round(labelSize * 0.08)),
    }, fixture.provider),
    svgNode("text", {
      x: contentRight,
      y: hero.providerY,
      ...textStyle(smallSize, design.palette.muted),
      "text-anchor": "end",
      "letter-spacing": Math.max(1, Math.round(smallSize * 0.07)),
    }, `${fixture.model} · ${fixture.effort}`),
    svgNode("text", {
      x: hero.safeX,
      y: hero.quotaY,
      ...textStyle(labelSize, design.palette.muted),
      "letter-spacing": Math.max(1, Math.round(labelSize * 0.06)),
    }, fixture.quota),
    svgNode("text", {
      x: contentRight,
      y: hero.quotaY,
      ...textStyle(smallSize, providerColor),
      "text-anchor": "end",
      "letter-spacing": Math.max(1, Math.round(smallSize * 0.06)),
    }, todayText),
    svgNode("text", {
      x: hero.safeX,
      y: hero.percentY,
      ...textStyle(hero.percentFontPx, design.palette.text, 700),
      "letter-spacing": Math.round(hero.percentFontPx * -0.04),
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
      y: hero.resetY,
      ...textStyle(labelSize, design.palette.text),
      "letter-spacing": Math.max(1, Math.round(labelSize * 0.05)),
    }, fixture.reset),
    svgNode("circle", {
      cx: hero.safeX + dotRadius,
      cy: statusCenter,
      r: dotRadius + 2,
      fill: design.palette.hairline,
    }),
    svgNode("circle", {
      cx: hero.safeX + dotRadius,
      cy: statusCenter,
      r: dotRadius,
      fill: isMissing ? design.palette.muted : providerColor,
    }),
    svgNode("text", {
      x: hero.safeX + dotRadius * 3,
      y: statusCenter,
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
  const {width, height} = state.hardware.display;
  const frame = document.querySelector("#preview-frame");
  const space = document.querySelector("#preview-space");
  state.scale = scale === 2 ? 2 : 1;
  frame.className = `scale-${state.scale}`;
  frame.style.width = `${width}px`;
  frame.style.height = `${height}px`;
  space.style.width = `${width * state.scale}px`;
  space.style.height = `${height * state.scale}px`;
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
  const encoded = bytesToBase64(await response.arrayBuffer());
  return `@font-face{font-family:'IBM Plex Sans Local';src:url(data:font/woff2;base64,${encoded}) format('woff2');font-style:normal;font-weight:${weight};}`;
}

async function loadExportFontCss() {
  const faces = await Promise.all([
    embeddedFontFace("/fonts/IBMPlexSans-SemiBold.woff2", 600),
    embeddedFontFace("/fonts/IBMPlexSans-Bold.woff2", 700),
  ]);
  return faces.join("");
}

async function saveDesign() {
  setOperationStatus("Saving reviewed tokens…", "pending");
  setActionsDisabled(true);
  try {
    const response = await fetch("/api/design", {
      method: "PUT",
      headers: mutationHeaders({"Content-Type": "application/json"}),
      body: JSON.stringify(state.design),
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
    setActionsDisabled(false);
  }
}

async function exportPng(name) {
  if (!EXPORT_NAMES.includes(name)) {
    setOperationStatus("Export failed: state name is not approved.", "error");
    return;
  }
  setOperationStatus(`Exporting ${name}…`, "pending");
  setActionsDisabled(true);
  let bitmap = null;
  try {
    await document.fonts.ready;
    const fontCss = await state.exportFontCss;
    const source = document.querySelector("#device-preview").cloneNode(true);
    const style = svgNode("style", {}, fontCss);
    source.insertBefore(style, source.firstChild);
    const svg = new XMLSerializer().serializeToString(source);
    const blob = new Blob([svg], {type: "image/svg+xml"});
    bitmap = await createImageBitmap(blob);
    const {width, height} = state.hardware.display;
    const canvas = Object.assign(document.createElement("canvas"), {width, height});
    const context = canvas.getContext("2d", {alpha: false});
    context.drawImage(bitmap, 0, 0, width, height);
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
    if (bitmap) {
      bitmap.close();
    }
    setActionsDisabled(false);
  }
}

function bindControls() {
  for (const button of document.querySelectorAll("[data-state]")) {
    button.addEventListener("click", () => {
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
    button.addEventListener("click", () => setScale(Number(button.dataset.scale)));
  }
  document.querySelector("#save-design").addEventListener("click", saveDesign);
  document.querySelector("#export-png").addEventListener("click", () => {
    const suffix = state.selection.condition === "live"
      ? ""
      : `-${state.selection.condition}`;
    exportPng(`${state.selection.provider}-hero${suffix}`);
  });
}

function setActionsDisabled(disabled) {
  document.querySelector("#save-design").disabled = disabled;
  document.querySelector("#export-png").disabled = disabled;
}

async function loadJson(path) {
  const response = await fetch(path);
  const payload = await responsePayload(response);
  return requireSuccessful(response, payload);
}

async function initialize() {
  bindControls();
  state.exportFontCss = loadExportFontCss();
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
    setActionsDisabled(true);
  }
}

initialize();
