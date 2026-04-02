const overlay = document.getElementById("colorOverlay");
const pitchOverlay = document.getElementById("pitchOverlay");
const bootSplash = document.getElementById("bootSplash");

const infoBtn = document.getElementById("infoBtn");
const settingsBtn = document.getElementById("settingsBtn");
const infoModal = document.getElementById("infoModal");
const closeInfoBtn = document.getElementById("closeInfoBtn");
const sidebar = document.getElementById("sidebar");
const closeSidebarBtn = document.getElementById("closeSidebarBtn");
const presetSelect = document.getElementById("presetSelect");
const presetLoadBtn = document.getElementById("presetLoadBtn");
const presetReloadBtn = document.getElementById("presetReloadBtn");
const presetSaveBtn = document.getElementById("presetSaveBtn");
const presetNameInput = document.getElementById("presetNameInput");
const presetStatus = document.getElementById("presetStatus");

const tabButtons = Array.from(document.querySelectorAll(".tabBtn"));
const tabPanels = Array.from(document.querySelectorAll(".tabPanel"));

let lastConfig = null;
let syncLock = false;
let presetsState = null;

const bindings = [
  ["screenPower", ["brightness", "enabled"], "bool"],
  ["screenEnabled", ["brightness", "screenEnabled"], "bool"],
  ["screenInvert", ["brightness", "screenInvert"], "bool"],
  ["screenMin", ["brightness", "screenMin"], "num", "screenMinOut"],
  ["screenMax", ["brightness", "screenMax"], "num", "screenMaxOut"],

  ["colorA", ["visual", "colorA"], "str"],
  ["colorB", ["visual", "colorB"], "str"],
  ["invertColor", ["visual", "invertColor"], "bool"],
  ["invertBrightness", ["visual", "invertBrightness"], "bool"],
  ["showPitchInfo", ["visual", "showPitchInfo"], "bool"],

  ["angleMin", ["sensor", "angleMin"], "num", "angleMinOut"],
  ["angleMax", ["sensor", "angleMax"], "num", "angleMaxOut"],

  ["keyboardEnabled", ["brightness", "keyboardEnabled"], "bool"],
  ["keyboardInvert", ["brightness", "keyboardInvert"], "bool"],
  ["keyboardMin", ["brightness", "keyboardMin"], "num", "keyboardMinOut"],
  ["keyboardMax", ["brightness", "keyboardMax"], "num", "keyboardMaxOut"],

  ["waveform", ["synth", "waveform"], "str"],
  ["minHz", ["synth", "minHz"], "num", "minHzOut"],
  ["maxHz", ["synth", "maxHz"], "num", "maxHzOut"],
  ["glideMs", ["synth", "glideMs"], "num", "glideMsOut"],
  ["attackMs", ["synth", "attackMs"], "num", "attackMsOut"],
  ["releaseMs", ["synth", "releaseMs"], "num", "releaseMsOut"],
  ["lowVolume", ["synth", "lowVolume"], "num", "lowVolumeOut"],
  ["highVolume", ["synth", "highVolume"], "num", "highVolumeOut"],
  ["cutoffHz", ["synth", "cutoffHz"], "num", "cutoffHzOut"],
  ["cutoffFollow", ["synth", "cutoffFollow"], "num", "cutoffFollowOut"],
  ["vibratoRateHz", ["synth", "vibratoRateHz"], "num", "vibratoRateHzOut"],
  ["vibratoDepthCents", ["synth", "vibratoDepthCents"], "num", "vibratoDepthCentsOut"],
  ["vibratoAmbientCents", ["synth", "vibratoAmbientCents"], "num", "vibratoAmbientCentsOut"],
  ["delayMs", ["synth", "delayMs"], "num", "delayMsOut"],
  ["delayFeedback", ["synth", "delayFeedback"], "num", "delayFeedbackOut"],
  ["delayMix", ["synth", "delayMix"], "num", "delayMixOut"],
  ["reverbMix", ["synth", "reverbMix"], "num", "reverbMixOut"],
  ["masterGain", ["synth", "masterGain"], "num", "masterGainOut"],
];

function getByPath(obj, path) {
  let cur = obj;
  for (const key of path) {
    if (!cur || !(key in cur)) {
      return undefined;
    }
    cur = cur[key];
  }
  return cur;
}

function setByPath(obj, path, value) {
  let cur = obj;
  for (let i = 0; i < path.length - 1; i += 1) {
    const key = path[i];
    if (!cur[key] || typeof cur[key] !== "object") {
      cur[key] = {};
    }
    cur = cur[key];
  }
  cur[path[path.length - 1]] = value;
}

function hexToRgb(hex) {
  const normalized = (hex || "#000000").replace("#", "");
  return {
    r: parseInt(normalized.slice(0, 2), 16) || 0,
    g: parseInt(normalized.slice(2, 4), 16) || 0,
    b: parseInt(normalized.slice(4, 6), 16) || 0,
  };
}

function mixColor(a, b, t) {
  const clamped = Math.max(0, Math.min(1, t));
  return {
    r: Math.round(a.r + (b.r - a.r) * clamped),
    g: Math.round(a.g + (b.g - a.g) * clamped),
    b: Math.round(a.b + (b.b - a.b) * clamped),
  };
}

function formatNumber(v) {
  const n = Number(v);
  if (Math.abs(n) >= 1000) {
    return n.toFixed(0);
  }
  if (Math.abs(n) >= 100) {
    return n.toFixed(1);
  }
  if (Number.isInteger(n)) {
    return n.toFixed(0);
  }
  return n.toFixed(2);
}

function freqToNote(freqHz) {
  const hz = Number(freqHz);
  if (!Number.isFinite(hz) || hz <= 0) {
    return "--";
  }
  const noteNames = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  const midi = Math.round(69 + (12 * Math.log2(hz / 440)));
  const name = noteNames[((midi % 12) + 12) % 12];
  const octave = Math.floor(midi / 12) - 1;
  return `${name}${octave}`;
}

async function patchConfig(patch) {
  try {
    const res = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    if (!res.ok) {
      return;
    }
    const data = await res.json();
    if (data && data.config) {
      lastConfig = data.config;
      syncControls(lastConfig);
    }
  } catch (_err) {
    // ignore
  }
}

function setPresetStatus(text) {
  if (!presetStatus) {
    return;
  }
  presetStatus.textContent = text || "";
}

function renderPresetSelect(payload) {
  if (!presetSelect || !payload) {
    return;
  }
  const names = Array.isArray(payload.presets) ? payload.presets : [];
  const selected = payload.selected || "";

  presetSelect.innerHTML = "";
  for (const name of names) {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    if (name === selected) {
      option.selected = true;
    }
    presetSelect.appendChild(option);
  }
}

async function pullPresets() {
  try {
    const res = await fetch("/api/synth-presets", { cache: "no-store" });
    if (!res.ok) {
      return;
    }
    const payload = await res.json();
    presetsState = payload;
    renderPresetSelect(payload);
    if (payload.path) {
      setPresetStatus(`YAML: ${payload.path}`);
    }
  } catch (_err) {
    // ignore
  }
}

async function apiPreset(action, payload) {
  try {
    const res = await fetch(`/api/synth-presets/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}),
    });
    if (!res.ok) {
      const text = await res.text();
      setPresetStatus(`Error ${res.status}: ${text.slice(0, 120)}`);
      return null;
    }
    const data = await res.json();
    if (data.config) {
      lastConfig = data.config;
      syncControls(lastConfig);
    }
    if (data.presets) {
      presetsState = data.presets;
      renderPresetSelect(data.presets);
      if (data.presets.path) {
        setPresetStatus(`YAML: ${data.presets.path}`);
      }
    }
    return data;
  } catch (_err) {
    setPresetStatus("No se pudo comunicar con el backend");
    return null;
  }
}

function bindPresetControls() {
  if (!presetSelect || !presetLoadBtn || !presetSaveBtn || !presetReloadBtn || !presetNameInput) {
    return;
  }

  presetLoadBtn.addEventListener("click", async () => {
    const name = String(presetSelect.value || "").trim();
    if (!name) {
      setPresetStatus("No hay preset seleccionado");
      return;
    }
    const data = await apiPreset("select", { name });
    if (data && data.ok) {
      setPresetStatus(`Preset cargado: ${name}`);
    }
  });

  presetSaveBtn.addEventListener("click", async () => {
    const raw = String(presetNameInput.value || "").trim();
    if (!raw) {
      setPresetStatus("Escribi un nombre para guardar");
      return;
    }
    const data = await apiPreset("save", { name: raw });
    if (data && data.ok) {
      presetNameInput.value = "";
      setPresetStatus(`Preset guardado: ${data.name}`);
    }
  });

  presetReloadBtn.addEventListener("click", async () => {
    const data = await apiPreset("reload", {});
    if (data && data.ok) {
      setPresetStatus("Presets recargados desde YAML");
    }
  });
}

function bindControls() {
  for (const [id, path, kind, outId] of bindings) {
    const el = document.getElementById(id);
    if (!el) {
      continue;
    }

    const onChange = () => {
      if (syncLock) {
        return;
      }

      let value;
      if (kind === "bool") {
        value = !!el.checked;
      } else if (kind === "num") {
        value = Number(el.value);
      } else {
        value = String(el.value);
      }

      const patch = {};
      setByPath(patch, path, value);
      if (outId) {
        const out = document.getElementById(outId);
        if (out) {
          out.textContent = formatNumber(value);
        }
      }
      patchConfig(patch);
    };

    el.addEventListener("change", onChange);
    if (kind === "num") {
      el.addEventListener("input", onChange);
    }
  }
}

function syncControls(config) {
  syncLock = true;
  try {
    for (const [id, path, kind, outId] of bindings) {
      const el = document.getElementById(id);
      if (!el) {
        continue;
      }
      const value = getByPath(config, path);
      if (value === undefined) {
        continue;
      }

      if (kind === "bool") {
        el.checked = !!value;
      } else {
        el.value = String(value);
      }

      if (outId) {
        const out = document.getElementById(outId);
        if (out) {
          out.textContent = formatNumber(value);
        }
      }
    }
  } finally {
    syncLock = false;
  }
}

function renderFromState(state) {
  if (!state || !state.config || !state.sensor) {
    return;
  }

  const config = state.config;
  lastConfig = config;
  if (presetSelect && config.synthPreset && presetSelect.options.length > 0) {
    const wanted = String(config.synthPreset);
    if (presetSelect.value !== wanted) {
      for (const option of presetSelect.options) {
        if (option.value === wanted) {
          presetSelect.value = wanted;
          break;
        }
      }
    }
  }

  const lid = Number(state.sensor.lidLevel || 0);
  const stale = !!state.sensor.stale;
  const visual = config.visual || {};
  const synth = config.synth || {};
  const colorA = hexToRgb(visual.colorA || "#2bd0ff");
  const colorB = hexToRgb(visual.colorB || "#ff6a00");

  const colorT = visual.invertColor ? 1 - lid : lid;
  const mixed = mixColor(colorB, colorA, colorT);

  const brightT = visual.invertBrightness ? 1 - lid : lid;
  const minOpacity = Number(visual.minOpacity ?? 0.08);
  const maxOpacity = Number(visual.maxOpacity ?? 1.0);
  const opacity = minOpacity + (maxOpacity - minOpacity) * brightT;

  overlay.style.backgroundColor = `rgb(${mixed.r}, ${mixed.g}, ${mixed.b})`;
  overlay.style.opacity = String(Math.max(0, Math.min(1, opacity)));

  if (pitchOverlay) {
    const showPitch = !!visual.showPitchInfo;
    if (!showPitch || stale) {
      pitchOverlay.classList.add("hidden");
    } else {
      const minHz = Number(synth.minHz || 130.81);
      const maxHz = Number(synth.maxHz || 1760.0);
      const ratio = maxHz > minHz ? maxHz / minHz : 1.0001;
      const freq = minHz * Math.pow(ratio, Math.max(0, Math.min(1, lid)));
      const note = freqToNote(freq);
      pitchOverlay.textContent = `${freq.toFixed(1)} Hz · ${note}`;
      pitchOverlay.classList.remove("hidden");
    }
  }
}

async function pullState() {
  try {
    const res = await fetch("/api/state", { cache: "no-store" });
    if (!res.ok) {
      return;
    }
    const state = await res.json();
    renderFromState(state);
    if (!syncLock && lastConfig === null && state.config) {
      syncControls(state.config);
      lastConfig = state.config;
    }
  } catch (_err) {
    // ignore transient network errors
  }
}

function selectTab(name) {
  for (const btn of tabButtons) {
    const active = btn.dataset.tab === name;
    btn.classList.toggle("active", active);
  }
  for (const panel of tabPanels) {
    const active = panel.id === `tab-${name}`;
    panel.classList.toggle("active", active);
  }
}

function bindTabs() {
  for (const btn of tabButtons) {
    btn.addEventListener("click", () => {
      selectTab(btn.dataset.tab);
    });
  }
}

function bindChrome() {
  const openInfo = () => {
    infoModal.classList.remove("hidden");
  };

  const closeInfo = () => {
    infoModal.classList.add("hidden");
  };

  const toggleInfo = () => {
    infoModal.classList.toggle("hidden");
  };

  const openSidebar = () => {
    sidebar.classList.add("open");
    sidebar.setAttribute("aria-hidden", "false");
  };

  const closeSidebar = () => {
    sidebar.classList.remove("open");
    sidebar.setAttribute("aria-hidden", "true");
  };

  const toggleSidebar = () => {
    if (sidebar.classList.contains("open")) {
      closeSidebar();
    } else {
      openSidebar();
    }
  };

  const isTypingTarget = (target) => {
    if (!target) {
      return false;
    }
    const tag = (target.tagName || "").toLowerCase();
    return (
      tag === "input" ||
      tag === "textarea" ||
      tag === "select" ||
      target.isContentEditable === true
    );
  };

  infoBtn.addEventListener("click", openInfo);
  closeInfoBtn.addEventListener("click", closeInfo);

  infoModal.addEventListener("click", (ev) => {
    if (ev.target === infoModal) {
      closeInfo();
    }
  });

  settingsBtn.addEventListener("click", toggleSidebar);
  closeSidebarBtn.addEventListener("click", closeSidebar);

  document.addEventListener("keydown", (ev) => {
    if (ev.ctrlKey || ev.metaKey || ev.altKey) {
      return;
    }
    if (isTypingTarget(ev.target)) {
      return;
    }

    const isOne = ev.key === "1" || ev.code === "Digit1" || ev.code === "Numpad1";
    const isTwo = ev.key === "2" || ev.code === "Digit2" || ev.code === "Numpad2";

    if (isOne) {
      ev.preventDefault();
      toggleInfo();
      return;
    }
    if (isTwo) {
      ev.preventDefault();
      toggleSidebar();
    }
  });
}

function boot() {
  if (bootSplash) {
    window.setTimeout(() => {
      bootSplash.classList.add("hidden");
    }, 2000);
  }
  bindChrome();
  bindTabs();
  bindControls();
  bindPresetControls();
  pullPresets();
  pullState();
  window.setInterval(pullState, 70);
}

boot();
