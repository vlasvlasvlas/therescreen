const overlay = document.getElementById("colorOverlay");
const pianoRoll = document.getElementById("pianoRoll");
const pianoKeys = document.getElementById("pianoKeys");
const pianoNeedle = document.getElementById("pianoNeedle");
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
const audioMuteBtn = document.getElementById("audioMuteBtn");
const audioTrackpadVolumeToggle = document.getElementById("audioTrackpadVolumeEnabled");
const toggleAdvancedSoundBtn = document.getElementById("toggleAdvancedSound");
const advancedSound = document.getElementById("advancedSound");

const tabButtons = Array.from(document.querySelectorAll(".tabBtn"));
const tabPanels = Array.from(document.querySelectorAll(".tabPanel"));

let lastConfig = null;
let syncLock = false;
let presetsState = null;
let controlsInitialized = false;
let advancedSoundOpen = false;
let pianoLayout = null;
let lastActivePianoKey = null;
let lastTrackpadVolumePatchMs = 0;

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
  ["showPianoRoll", ["visual", "showPianoRoll"], "bool"],
  ["showGizmo", ["visual", "showGizmo"], "bool"],

  ["angleMin", ["sensor", "angleMin"], "num", "angleMinOut"],
  ["angleMax", ["sensor", "angleMax"], "num", "angleMaxOut"],

  ["keyboardEnabled", ["brightness", "keyboardEnabled"], "bool"],
  ["keyboardInvert", ["brightness", "keyboardInvert"], "bool"],
  ["keyboardMin", ["brightness", "keyboardMin"], "num", "keyboardMinOut"],
  ["keyboardMax", ["brightness", "keyboardMax"], "num", "keyboardMaxOut"],

  ["audioVolume", ["audio", "volumePercent"], "num", "audioVolumeOut"],
  ["audioTrackpadVolumeEnabled", ["audio", "trackpadVolumeEnabled"], "bool"],

  ["waveform", ["synth", "waveform"], "str"],
  ["minHz", ["synth", "minHz"], "num", "minHzOut"],
  ["maxHz", ["synth", "maxHz"], "num", "maxHzOut"],
  ["anchorAt90Enabled", ["synth", "anchorAt90Enabled"], "bool"],
  ["freqAt90Hz", ["synth", "freqAt90Hz"], "num", "freqAt90HzOut"],
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

function normalize01(value, low, high) {
  const lo = Number(low);
  const hi = Number(high);
  if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi <= lo) {
    return 0;
  }
  const t = (Number(value) - lo) / (hi - lo);
  return Math.max(0, Math.min(1, t));
}

function mapPitchLevelByAnchor(lidLevel, sensor, synth) {
  const lid = Math.max(0, Math.min(1, Number(lidLevel) || 0));
  if (!synth || !synth.anchorAt90Enabled) {
    return lid;
  }
  const minHz = Number(synth.minHz || 130.81);
  const maxHz = Number(synth.maxHz || 1760.0);
  if (!Number.isFinite(minHz) || !Number.isFinite(maxHz) || maxHz <= minHz) {
    return lid;
  }
  const targetRaw = Number(synth.freqAt90Hz || minHz);
  const targetHz = Math.max(minHz, Math.min(maxHz, Number.isFinite(targetRaw) ? targetRaw : minHz));
  const ratio = maxHz / minHz;
  if (!Number.isFinite(ratio) || ratio <= 1.000001) {
    return lid;
  }
  const targetLevel = Math.log(targetHz / minHz) / Math.log(ratio);
  const angleMin = Number((sensor && sensor.angleMin) || 0);
  const angleMax = Number((sensor && sensor.angleMax) || 125);
  const at90 = normalize01(90, angleMin, angleMax);
  const delta = targetLevel - at90;
  return Math.max(0, Math.min(1, lid + delta));
}

function effectivePitchRange(sensor, synth) {
  const minHz = Number((synth && synth.minHz) || 130.81);
  const maxHz = Number((synth && synth.maxHz) || 1760.0);
  if (!Number.isFinite(minHz) || !Number.isFinite(maxHz) || maxHz <= minHz) {
    return { minHz: 130.81, maxHz: 1760.0 };
  }
  const ratio = maxHz / minHz;
  const p0 = mapPitchLevelByAnchor(0, sensor || {}, synth || {});
  const p1 = mapPitchLevelByAnchor(1, sensor || {}, synth || {});
  const lo = Math.max(0, Math.min(1, Math.min(p0, p1)));
  const hi = Math.max(0, Math.min(1, Math.max(p0, p1)));
  const effMin = minHz * Math.pow(ratio, lo);
  const effMax = minHz * Math.pow(ratio, hi);
  return {
    minHz: Math.min(effMin, effMax),
    maxHz: Math.max(effMin, effMax),
  };
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

function freqToMidi(freqHz) {
  const hz = Number(freqHz);
  if (!Number.isFinite(hz) || hz <= 0) {
    return null;
  }
  return 69 + (12 * Math.log2(hz / 440));
}

function isBlackMidi(midi) {
  const pc = ((Number(midi) % 12) + 12) % 12;
  return pc === 1 || pc === 3 || pc === 6 || pc === 8 || pc === 10;
}

function buildPianoLayout(minHz, maxHz) {
  if (!pianoRoll || !pianoKeys) {
    return;
  }
  const minMidiFloat = freqToMidi(minHz);
  const maxMidiFloat = freqToMidi(maxHz);
  if (minMidiFloat === null || maxMidiFloat === null) {
    return;
  }

  let minMidi = Math.max(24, Math.floor(Math.min(minMidiFloat, maxMidiFloat)));
  let maxMidi = Math.min(108, Math.ceil(Math.max(minMidiFloat, maxMidiFloat)));
  if (maxMidi - minMidi < 6) {
    const pad = Math.ceil((6 - (maxMidi - minMidi)) / 2);
    minMidi = Math.max(24, minMidi - pad);
    maxMidi = Math.min(108, maxMidi + pad);
  }
  if (pianoLayout && pianoLayout.minMidi === minMidi && pianoLayout.maxMidi === maxMidi) {
    return;
  }

  pianoKeys.innerHTML = "";
  lastActivePianoKey = null;

  let whiteCount = 0;
  for (let midi = minMidi; midi <= maxMidi; midi += 1) {
    if (!isBlackMidi(midi)) {
      whiteCount += 1;
    }
  }
  if (whiteCount <= 0) {
    return;
  }

  const whiteWidth = 100 / whiteCount;
  const midiToCenter = new Map();
  const midiToElement = new Map();
  let whiteIndex = 0;

  for (let midi = minMidi; midi <= maxMidi; midi += 1) {
    if (isBlackMidi(midi)) {
      continue;
    }
    const el = document.createElement("div");
    el.className = "pKey white";
    el.style.left = `${whiteIndex * whiteWidth}%`;
    el.style.width = `${whiteWidth}%`;
    pianoKeys.appendChild(el);
    midiToElement.set(midi, el);
    midiToCenter.set(midi, (whiteIndex + 0.5) * whiteWidth);
    whiteIndex += 1;
  }

  for (let midi = minMidi; midi <= maxMidi; midi += 1) {
    if (!isBlackMidi(midi)) {
      continue;
    }
    const leftWhite = midi - 1;
    const rightWhite = midi + 1;
    const leftCenter = midiToCenter.get(leftWhite);
    const rightCenter = midiToCenter.get(rightWhite);
    if (leftCenter === undefined || rightCenter === undefined) {
      continue;
    }
    const center = (leftCenter + rightCenter) / 2;
    const width = whiteWidth * 0.62;
    const el = document.createElement("div");
    el.className = "pKey black";
    el.style.left = `${center - (width / 2)}%`;
    el.style.width = `${width}%`;
    pianoKeys.appendChild(el);
    midiToElement.set(midi, el);
    midiToCenter.set(midi, center);
  }

  pianoLayout = {
    minMidi,
    maxMidi,
    midiToCenter,
    midiToElement,
  };
}

function pianoNeedlePercent(midiFloat) {
  if (!pianoLayout || !Number.isFinite(midiFloat)) {
    return 50;
  }
  const minMidi = pianoLayout.minMidi;
  const maxMidi = pianoLayout.maxMidi;
  const centers = pianoLayout.midiToCenter;

  if (midiFloat <= minMidi) {
    return Number(centers.get(minMidi) || 0);
  }
  if (midiFloat >= maxMidi) {
    return Number(centers.get(maxMidi) || 100);
  }

  const lo = Math.floor(midiFloat);
  const hi = Math.ceil(midiFloat);
  const loCenter = Number(centers.get(lo) || 0);
  const hiCenter = Number(centers.get(hi) || 100);
  if (lo === hi) {
    return loCenter;
  }
  const t = midiFloat - lo;
  return loCenter + ((hiCenter - loCenter) * t);
}

function renderPianoRoll(freqHz, sensor, synth, showPianoRoll, stale) {
  if (!pianoRoll || !pianoNeedle) {
    return;
  }
  if (!showPianoRoll) {
    pianoRoll.classList.add("hidden");
    if (pitchOverlay) {
      pitchOverlay.classList.remove("withPiano");
    }
    if (lastActivePianoKey) {
      lastActivePianoKey.classList.remove("active");
      lastActivePianoKey = null;
    }
    return;
  }

  const range = effectivePitchRange(sensor, synth);
  buildPianoLayout(range.minHz, range.maxHz);
  pianoRoll.classList.remove("hidden");
  if (pitchOverlay) {
    pitchOverlay.classList.add("withPiano");
  }

  const midiFloat = freqToMidi(freqHz);
  if (stale || midiFloat === null || !pianoLayout) {
    pianoNeedle.classList.add("stale");
    if (lastActivePianoKey) {
      lastActivePianoKey.classList.remove("active");
      lastActivePianoKey = null;
    }
    return;
  }

  pianoNeedle.classList.remove("stale");
  const left = pianoNeedlePercent(midiFloat);
  pianoNeedle.style.left = `${left}%`;

  const activeMidi = Math.round(midiFloat);
  const nextActive = pianoLayout.midiToElement.get(activeMidi) || null;
  if (lastActivePianoKey && lastActivePianoKey !== nextActive) {
    lastActivePianoKey.classList.remove("active");
  }
  if (nextActive && nextActive !== lastActivePianoKey) {
    nextActive.classList.add("active");
  }
  lastActivePianoKey = nextActive;
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

async function patchRuntimeConfig(patch) {
  try {
    const res = await fetch("/api/runtime-config", {
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
  if (!presetSelect || !presetSaveBtn || !presetReloadBtn || !presetNameInput) {
    return;
  }

  const applySelectedPreset = async () => {
    const name = String(presetSelect.value || "").trim();
    if (!name) {
      setPresetStatus("No hay preset seleccionado");
      return;
    }
    const data = await apiPreset("select", { name });
    if (data && data.ok) {
      const loaded = data.config && data.config.synthPreset ? data.config.synthPreset : name;
      setPresetStatus(`Preset aplicado: ${loaded}`);
    }
  };

  if (presetLoadBtn) {
    presetLoadBtn.addEventListener("click", async () => {
      await applySelectedPreset();
    });
  }

  presetSelect.addEventListener("change", async () => {
    await applySelectedPreset();
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

function renderAdvancedSoundMode() {
  if (!advancedSound || !toggleAdvancedSoundBtn) {
    return;
  }
  advancedSound.classList.toggle("open", advancedSoundOpen);
  toggleAdvancedSoundBtn.textContent = advancedSoundOpen ? "Ocultar avanzado" : "Mostrar avanzado";
}

function bindSoundMode() {
  if (!advancedSound || !toggleAdvancedSoundBtn) {
    return;
  }
  try {
    advancedSoundOpen = window.localStorage.getItem("therescreen.sound.advanced") === "1";
  } catch (_err) {
    advancedSoundOpen = false;
  }
  renderAdvancedSoundMode();
  toggleAdvancedSoundBtn.addEventListener("click", () => {
    advancedSoundOpen = !advancedSoundOpen;
    try {
      window.localStorage.setItem("therescreen.sound.advanced", advancedSoundOpen ? "1" : "0");
    } catch (_err) {
      // ignore
    }
    renderAdvancedSoundMode();
  });
}

function renderAudioMuteState(config) {
  if (!audioMuteBtn) {
    return;
  }
  const audio = (config && config.audio) || {};
  const muted = !!audio.mute;
  audioMuteBtn.classList.toggle("active", muted);
  audioMuteBtn.textContent = muted ? "Muted" : "Mute";
}

function bindAudioControls() {
  if (!audioMuteBtn) {
    return;
  }
  audioMuteBtn.addEventListener("click", () => {
    const currentValue = getByPath(lastConfig || {}, ["audio", "mute"]);
    const current = currentValue === undefined ? false : !!currentValue;
    const next = !current;
    patchConfig({ audio: { mute: next } });
    setPresetStatus(next ? "Audio mute" : "Audio ON");
  });
}

function bindTrackpadVolumeControl() {
  if (!audioTrackpadVolumeToggle) {
    return;
  }

  const isTypingTarget = (target) => {
    if (!target) {
      return false;
    }
    const tag = (target.tagName || "").toLowerCase();
    return (
      tag === "input" ||
      tag === "textarea" ||
      tag === "select" ||
      tag === "button" ||
      target.isContentEditable === true
    );
  };

  const applyFromClientY = (clientY, target) => {
    if (!Number.isFinite(clientY)) {
      return;
    }
    if (isTypingTarget(target)) {
      return;
    }
    const enabled = !!getByPath(lastConfig || {}, ["audio", "trackpadVolumeEnabled"]);
    if (!enabled) {
      return;
    }

    const h = Math.max(1, window.innerHeight || 1);
    const y = Math.max(0, Math.min(h, Number(clientY)));
    const nextVolume = Math.round((1 - (y / h)) * 100);
    const currentVolume = Number(getByPath(lastConfig || {}, ["audio", "volumePercent"]));
    if (Number.isFinite(currentVolume) && Math.abs(currentVolume - nextVolume) < 2) {
      return;
    }

    const now = Date.now();
    if (now - lastTrackpadVolumePatchMs < 80) {
      return;
    }
    lastTrackpadVolumePatchMs = now;

    const vol = document.getElementById("audioVolume");
    const out = document.getElementById("audioVolumeOut");
    if (vol) {
      vol.value = String(nextVolume);
    }
    if (out) {
      out.textContent = formatNumber(nextVolume);
    }
    patchRuntimeConfig({ audio: { volumePercent: nextVolume } });
  };

  document.addEventListener(
    "pointermove",
    (ev) => {
      applyFromClientY(ev.clientY, ev.target);
    },
    { passive: true },
  );

  document.addEventListener(
    "touchmove",
    (ev) => {
      if (!ev.touches || ev.touches.length === 0) {
        return;
      }
      applyFromClientY(ev.touches[0].clientY, ev.target);
    },
    { passive: true },
  );
}

function refreshFreq90Control(config) {
  const slider = document.getElementById("freqAt90Hz");
  const out = document.getElementById("freqAt90HzOut");
  if (!slider) {
    return;
  }
  const synth = (config && config.synth) || {};
  const minHz = Number(synth.minHz || 130.81);
  const maxHz = Number(synth.maxHz || 1760.0);
  const lo = Math.max(20, Math.floor(Math.min(minHz, maxHz)));
  const hi = Math.max(lo + 1, Math.ceil(Math.max(minHz, maxHz)));
  slider.min = String(lo);
  slider.max = String(hi);

  const cur = Number(synth.freqAt90Hz);
  const clamped = Math.max(minHz, Math.min(maxHz, Number.isFinite(cur) ? cur : minHz));
  slider.value = String(Math.round(clamped));
  slider.disabled = !synth.anchorAt90Enabled;
  if (out) {
    out.textContent = formatNumber(clamped);
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
    refreshFreq90Control(config);
    renderAudioMuteState(config);
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
  const sensorCfg = config.sensor || {};
  const synth = config.synth || {};
  const minHz = Number(synth.minHz || 130.81);
  const maxHz = Number(synth.maxHz || 1760.0);
  const ratio = maxHz > minHz ? maxHz / minHz : 1.0001;
  const pitchLid = mapPitchLevelByAnchor(lid, sensorCfg, synth);
  const freq = minHz * Math.pow(ratio, Math.max(0, Math.min(1, pitchLid)));
  const colorA = hexToRgb(visual.colorA || "#ff4fd8");
  const colorB = hexToRgb(visual.colorB || "#ffd400");

  const colorT = visual.invertColor ? 1 - lid : lid;
  const mixed = mixColor(colorB, colorA, colorT);

  const brightT = visual.invertBrightness ? 1 - lid : lid;
  const minOpacity = Number(visual.minOpacity ?? 0.08);
  const maxOpacity = Number(visual.maxOpacity ?? 1.0);
  const opacity = minOpacity + (maxOpacity - minOpacity) * brightT;

  overlay.style.backgroundColor = `rgb(${mixed.r}, ${mixed.g}, ${mixed.b})`;
  overlay.style.opacity = String(Math.max(0, Math.min(1, opacity)));

  const gizmoContainer = document.getElementById("gizmoContainer");
  if (gizmoContainer) {
    if (visual.showGizmo) {
      gizmoContainer.classList.remove("hidden");
      const avatar = document.getElementById("gizmoAvatar");
      const gizmoHead = document.getElementById("gizmoHead");
      const gizmoEyelids = document.getElementById("gizmoEyelids");
      const gizmoPupils = document.getElementById("gizmoPupils");
      const gizmoMouth = document.getElementById("gizmoMouth");
      if (avatar && gizmoHead && gizmoEyelids && gizmoMouth) {
        const lidP = Math.max(0, Math.min(1, lid));
        const freqP = Math.max(0, Math.min(1, (freq - minHz) / (maxHz > minHz ? maxHz - minHz : 1)));

        const bodyY = (1 - lidP) * 16;
        const bodyScaleY = 0.88 + (lidP * 0.16) + (freqP * 0.03);
        const bodyScaleX = 1.02 - (freqP * 0.04);
        avatar.style.transform = `translateY(${bodyY.toFixed(1)}px) scale(${bodyScaleX.toFixed(3)}, ${bodyScaleY.toFixed(3)})`;

        const headY = (1 - lidP) * 14 - (freqP * 8);
        gizmoHead.style.transform = `translateY(${headY.toFixed(1)}px)`;

        // Cerrada (lid=0): ojos cerrados. Abierta (lid=1): ojos abiertos.
        const eyelidOffsetY = -74 * lidP;
        gizmoEyelids.style.transform = `translateY(${eyelidOffsetY.toFixed(1)}px)`;

        if (gizmoPupils) {
          const pupilX = (freqP - 0.5) * 8;
          const pupilY = (0.5 - lidP) * 6;
          gizmoPupils.style.transform = `translate(${pupilX.toFixed(1)}px, ${pupilY.toFixed(1)}px)`;
        }

        const mouthOpen = (1 - lidP) * 2.2 + (freqP * 0.9);
        const mouthScaleY = 1.0 + (mouthOpen * 0.08);
        gizmoMouth.style.transform = `translateY(${mouthOpen.toFixed(1)}px) scaleY(${mouthScaleY.toFixed(3)})`;
      }
    } else {
      gizmoContainer.classList.add("hidden");
    }
  }

  renderPianoRoll(freq, sensorCfg, synth, !!visual.showPianoRoll, stale);

  if (pitchOverlay) {
    const showPitch = !!visual.showPitchInfo;
    if (!showPitch || stale) {
      pitchOverlay.classList.add("hidden");
    } else {
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
    if (!syncLock && !controlsInitialized && state.config) {
      syncControls(state.config);
      controlsInitialized = true;
    }
    renderFromState(state);
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

  const toggleAudioEnabled = () => {
    const currentValue = getByPath(lastConfig || {}, ["audio", "mute"]);
    const current = currentValue === undefined ? false : !!currentValue;
    const next = !current;
    patchConfig({ audio: { mute: next } });
    setPresetStatus(next ? "Audio mute" : "Audio ON");
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
    const isThree = ev.key === "3" || ev.code === "Digit3" || ev.code === "Numpad3";

    if (isOne) {
      ev.preventDefault();
      toggleInfo();
      return;
    }
    if (isTwo) {
      ev.preventDefault();
      toggleSidebar();
      return;
    }
    if (isThree) {
      ev.preventDefault();
      toggleAudioEnabled();
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
  bindAudioControls();
  bindTrackpadVolumeControl();
  bindSoundMode();
  bindPresetControls();
  pullPresets();
  pullState();
  window.setInterval(pullState, 70);
}

boot();
