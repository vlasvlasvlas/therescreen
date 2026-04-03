/* Therescreen Game UI */

const dom = {
  flashOverlay: document.getElementById("flashOverlay"),
  judgePopup: document.getElementById("judgePopup"),
  comboBadge: document.getElementById("comboBadge"),

  screenSelect: document.getElementById("screenSelect"),
  screenCountdown: document.getElementById("screenCountdown"),
  screenPlay: document.getElementById("screenPlay"),
  screenResults: document.getElementById("screenResults"),

  topStatus: document.getElementById("statusBadge"),
  helpBtn: document.getElementById("helpBtn"),
  settingsBtn: document.getElementById("settingsBtn"),

  songList: document.getElementById("songList"),
  noSongsMsg: document.getElementById("noSongsMsg"),
  detailCard: document.getElementById("detailCard"),
  previewBadge: document.getElementById("previewBadge"),
  diffCard: document.getElementById("diffCard"),
  diffButtons: Array.from(document.querySelectorAll(".diffBtn")),
  startBigBtn: document.getElementById("startBigBtn"),

  countNum: document.getElementById("countNum"),
  countSongName: document.getElementById("countSongName"),

  scoreVal: document.getElementById("scoreVal"),
  comboVal: document.getElementById("comboVal"),
  playerVal: document.getElementById("playerVal"),
  targetVal: document.getElementById("targetVal"),
  judgeVal: document.getElementById("judgeVal"),
  judgeMetaVal: document.getElementById("judgeMetaVal"),
  comboCard: document.getElementById("comboCard"),
  progressFill: document.getElementById("progressFill"),
  pitchMarker: document.getElementById("pitchMarker"),
  stopPlayBtn: document.getElementById("stopPlayBtn"),

  rankBig: document.getElementById("rankBig"),
  resultsSong: document.getElementById("resultsSong"),
  resultsScore: document.getElementById("resultsScore"),
  rPerfect: document.getElementById("rPerfect"),
  rGood: document.getElementById("rGood"),
  rOk: document.getElementById("rOk"),
  rMiss: document.getElementById("rMiss"),
  rAccuracy: document.getElementById("rAccuracy"),
  rMaxCombo: document.getElementById("rMaxCombo"),
  retryBtn: document.getElementById("retryBtn"),
  backSelectBtn: document.getElementById("backSelectBtn"),

  settingsPanel: document.getElementById("settingsPanel"),
  helpModal: document.getElementById("helpModal"),
  closeHelpBtn: document.getElementById("closeHelpBtn"),

  highway: document.getElementById("highway"),
};

const ctx = dom.highway.getContext("2d");

const settingIds = [
  "timingPerfectMs",
  "timingGoodMs",
  "timingOkMs",
  "pitchPerfectCents",
  "pitchGoodCents",
  "pitchOkCents",
  "scrollLookAheadSec",
  "guideVolume",
  "comboStepPercent",
  "comboMaxPercent",
];

const state = {
  currentScreen: "select",
  lastApiState: null,
  songs: [],
  selectedMidi: null,
  selectedPreview: null,
  selectedDifficulty: "normal",
  selectionToken: 0,
  settingsSyncLock: false,
  pollInFlight: false,
  lastJudgement: "",
  lastCombo: 0,
  lastStartTokenScheduled: -1,
  songCache: null,
  resultsShownForToken: -1,
  startingGame: false,
};

const audio = {
  ctx: null,
  previewNodes: [],
  guideNodes: [],
  previewHideTimer: null,
  guideToken: -1,
};

const particles = [];

const JUDGE_CLASS = {
  perfect: "jv-perfect",
  good: "jv-good",
  ok: "jv-ok",
  miss: "jv-miss",
};

function clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, Number(v)));
}

function fmt(v, digits = 1) {
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(digits) : "--";
}

function isBlackMidi(midi) {
  const m = ((Math.round(midi) % 12) + 12) % 12;
  return m === 1 || m === 3 || m === 6 || m === 8 || m === 10;
}

function noteNameFromMidi(midi) {
  const names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  const m = Math.round(Number(midi));
  if (!Number.isFinite(m)) return "--";
  return `${names[((m % 12) + 12) % 12]}${Math.floor(m / 12) - 1}`;
}

function hzToMidi(hz) {
  const f = Number(hz);
  if (!Number.isFinite(f) || f <= 0) return null;
  return 69 + 12 * Math.log2(f / 440);
}

function settingOut(id, value) {
  if (id === "comboStepPercent" || id === "comboMaxPercent") return `${(value * 100).toFixed(0)}%`;
  if (id === "guideVolume") return `${(value * 100).toFixed(0)}%`;
  if (Number.isInteger(value)) return String(value);
  return value.toFixed(2);
}

function api(path, method = "GET", payload = null) {
  const options = { method, cache: "no-store" };
  if (payload !== null) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(payload);
  }
  return fetch(path, options).then(async (res) => {
    if (!res.ok) {
      const txt = await res.text();
      throw new Error(`${res.status}: ${txt.slice(0, 200)}`);
    }
    return res.json();
  });
}

function setScreen(name) {
  state.currentScreen = name;
  dom.screenSelect.classList.toggle("active", name === "select");
  dom.screenCountdown.classList.toggle("active", name === "countdown");
  dom.screenPlay.classList.toggle("active", name === "play");
  dom.screenResults.classList.toggle("active", name === "results");
}

function setStatusBadge(game) {
  dom.topStatus.className = "badge";
  if (!game) {
    dom.topStatus.textContent = "idle";
    return;
  }
  if (game.playing) {
    dom.topStatus.textContent = "playing";
    dom.topStatus.classList.add("playing");
    return;
  }
  if (game.completed) {
    dom.topStatus.textContent = "completed";
    dom.topStatus.classList.add("completed");
    return;
  }
  if (game.loaded) {
    dom.topStatus.textContent = "ready";
    dom.topStatus.classList.add("ready");
    return;
  }
  dom.topStatus.textContent = "idle";
}

function clearSongCards() {
  Array.from(dom.songList.querySelectorAll(".songCard")).forEach((el) => el.remove());
}

function estimateDifficulty(meta) {
  const notes = Number(meta.noteCount || 0);
  const spread = Number(meta.maxMidi || 0) - Number(meta.minMidi || 0);
  const density = Number(meta.durationSec || 1) > 0 ? notes / Number(meta.durationSec) : 0;
  if (notes <= 0) return { name: "N/A", cls: "sb-normal" };
  if (density > 2.8 || spread >= 30) return { name: "Hard", cls: "sb-hard" };
  if (density > 1.8 || spread >= 20) return { name: "Normal", cls: "sb-normal" };
  return { name: "Easy", cls: "sb-easy" };
}

function buildSongCard(meta) {
  const card = document.createElement("button");
  card.className = "songCard";
  card.type = "button";
  card.dataset.name = meta.name;

  const icon = document.createElement("div");
  icon.className = "songIcon";
  icon.textContent = "♪";

  const info = document.createElement("div");
  info.className = "songInfo";

  const name = document.createElement("div");
  name.className = "songName";
  name.textContent = String(meta.displayName || meta.title || meta.name);

  const metaLine = document.createElement("div");
  metaLine.className = "songMeta";
  if (meta.parseError) {
    metaLine.textContent = `No parseable (${meta.parseError})`;
  } else {
    const dur = Number.isFinite(Number(meta.durationSec)) ? `${fmt(meta.durationSec, 1)}s` : "--s";
    const notes = Number.isFinite(Number(meta.noteCount)) ? `${meta.noteCount} notes` : "-- notes";
    const rng = Number.isFinite(Number(meta.minMidi)) && Number.isFinite(Number(meta.maxMidi))
      ? `${noteNameFromMidi(meta.minMidi)}-${noteNameFromMidi(meta.maxMidi)}`
      : "--";
    const gameTag = meta.game ? `${meta.game}` : null;
    metaLine.textContent = [gameTag, dur, notes, rng].filter(Boolean).join(" · ");
  }

  info.appendChild(name);
  info.appendChild(metaLine);

  const badge = document.createElement("span");
  const diff = estimateDifficulty(meta);
  badge.className = `songBadge ${diff.cls}`;
  badge.textContent = diff.name;

  card.appendChild(icon);
  card.appendChild(info);
  card.appendChild(badge);
  card.addEventListener("click", () => {
    void selectMidi(meta.name, true);
  });
  return card;
}

function markSelectedSong() {
  Array.from(dom.songList.querySelectorAll(".songCard")).forEach((el) => {
    el.classList.toggle("selected", el.dataset.name === state.selectedMidi);
  });
}

function renderSongs() {
  clearSongCards();
  if (!state.songs.length) {
    dom.noSongsMsg.style.display = "block";
    dom.startBigBtn.disabled = true;
    dom.diffCard.style.display = "none";
    return;
  }
  dom.noSongsMsg.style.display = "none";
  dom.diffCard.style.display = "block";
  for (const meta of state.songs) {
    dom.songList.appendChild(buildSongCard(meta));
  }
  markSelectedSong();
}

function renderSongDetail(payload, fallbackMeta) {
  if (!payload && !fallbackMeta) {
    dom.detailCard.innerHTML = '<div style="text-align:center; padding:40px; color: var(--muted);">Selecciona una cancion.</div>';
    return;
  }

  const name =
    (payload && (payload.displayName || (payload.catalog && payload.catalog.displayName) || payload.name)) ||
    (fallbackMeta && (fallbackMeta.displayName || fallbackMeta.title || fallbackMeta.name)) ||
    "-";
  const duration = (payload && payload.durationSec) ?? (fallbackMeta && fallbackMeta.durationSec);
  const noteCount = (payload && payload.noteCount) ?? (fallbackMeta && fallbackMeta.noteCount);
  const minMidi = (payload && payload.minMidi) ?? (fallbackMeta && fallbackMeta.minMidi);
  const maxMidi = (payload && payload.maxMidi) ?? (fallbackMeta && fallbackMeta.maxMidi);
  const fileSize = fallbackMeta ? fallbackMeta.sizeKb : null;
  const catalogMeta =
    (payload && payload.catalog && typeof payload.catalog === "object" ? payload.catalog : null) ||
    (fallbackMeta && fallbackMeta.catalog && typeof fallbackMeta.catalog === "object" ? fallbackMeta.catalog : null) ||
    fallbackMeta ||
    {};
  const gameInfo = [catalogMeta.game, catalogMeta.platform].filter(Boolean).join(" · ");
  const composerInfo = catalogMeta.composer ? `Composer: ${catalogMeta.composer}` : "";

  const rangeText = Number.isFinite(Number(minMidi)) && Number.isFinite(Number(maxMidi))
    ? `${noteNameFromMidi(minMidi)} - ${noteNameFromMidi(maxMidi)}`
    : "--";

  const hasPreview = Array.isArray(payload && payload.previewNotes) && payload.previewNotes.length > 0;

  dom.detailCard.innerHTML = `
    <div class="detailTitle">${name}</div>
    <div class="detailMeta">Preview + metadata antes de jugar</div>
    <div style="font-size:11px;color:var(--muted);margin-bottom:10px;">
      ${gameInfo || ""} ${gameInfo && composerInfo ? "·" : ""} ${composerInfo || ""}
    </div>
    <div class="detailGrid">
      <div class="detailStat"><div class="ds-label">Duration</div><div class="ds-val">${Number.isFinite(Number(duration)) ? `${fmt(duration, 1)}s` : "--"}</div></div>
      <div class="detailStat"><div class="ds-label">Notes</div><div class="ds-val">${Number.isFinite(Number(noteCount)) ? String(noteCount) : "--"}</div></div>
      <div class="detailStat"><div class="ds-label">Range</div><div class="ds-val">${rangeText}</div></div>
      <div class="detailStat"><div class="ds-label">Size</div><div class="ds-val">${Number.isFinite(Number(fileSize)) ? `${fmt(fileSize, 1)}kb` : "--"}</div></div>
    </div>
    <div style="font-size:11px;color:var(--muted)">
      ${hasPreview ? "Al seleccionar suena un preview corto para reconocer el tema." : "Este MIDI no tiene preview disponible."}
    </div>
  `;
}

function renderDetailError(message) {
  dom.detailCard.innerHTML = `
    <div class="detailTitle">Error de MIDI</div>
    <div class="detailMeta">No se pudo cargar este archivo para jugar</div>
    <div style="margin-top:10px;color:#c92f2f;font-size:13px;line-height:1.4;">
      ${String(message || "Error desconocido")}
    </div>
  `;
}

function setDifficultyButtons(active) {
  const chosen = ["easy", "normal", "hard", "custom"].includes(active) ? active : "custom";
  state.selectedDifficulty = chosen;
  for (const btn of dom.diffButtons) {
    btn.classList.remove("active-easy", "active-normal", "active-hard", "active-custom");
    if (btn.dataset.diff === chosen) {
      btn.classList.add(`active-${chosen}`);
    }
  }
}

function stopOscNodes(arr) {
  for (const n of arr) {
    try {
      n.osc.stop();
    } catch (_) {
      // ignore
    }
  }
  arr.length = 0;
}

async function ensureAudioContext(userGesture = false) {
  if (!audio.ctx) {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    audio.ctx = new Ctx();
  }
  if (audio.ctx.state === "suspended" && userGesture) {
    try {
      await audio.ctx.resume();
    } catch (_) {
      // ignore
    }
  }
  return audio.ctx;
}

function stopPreviewAudio() {
  stopOscNodes(audio.previewNodes);
  if (audio.previewHideTimer) {
    clearTimeout(audio.previewHideTimer);
    audio.previewHideTimer = null;
  }
  dom.previewBadge.style.display = "none";
}

function stopGuideAudio() {
  stopOscNodes(audio.guideNodes);
  audio.guideToken = -1;
}

async function playPreview(payload) {
  if (!payload || !Array.isArray(payload.previewNotes) || payload.previewNotes.length === 0) {
    stopPreviewAudio();
    return;
  }
  const ctxAudio = await ensureAudioContext(true);
  if (!ctxAudio) return;

  stopPreviewAudio();

  const notes = payload.previewNotes
    .filter((n) => Number(n.startSec) < 12)
    .slice(0, 96);
  if (!notes.length) {
    return;
  }

  const baseT = ctxAudio.currentTime + 0.03;
  const vol = 0.06;
  let maxEnd = baseT;

  for (const n of notes) {
    const hz = Number(n.freqHz);
    const st = Number(n.startSec);
    const dur = clamp(Number(n.durSec), 0.05, 0.55);
    if (!Number.isFinite(hz) || hz <= 0 || !Number.isFinite(st)) continue;

    const osc = ctxAudio.createOscillator();
    const gain = ctxAudio.createGain();
    osc.type = "sine";
    osc.frequency.value = hz;

    const startAt = baseT + st;
    const endAt = startAt + dur;
    maxEnd = Math.max(maxEnd, endAt + 0.05);

    gain.gain.setValueAtTime(0.0, Math.max(ctxAudio.currentTime, startAt - 0.015));
    gain.gain.linearRampToValueAtTime(vol, startAt + 0.012);
    gain.gain.linearRampToValueAtTime(vol * 0.8, Math.max(startAt + 0.03, endAt - 0.02));
    gain.gain.linearRampToValueAtTime(0.0, endAt);

    osc.connect(gain);
    gain.connect(ctxAudio.destination);
    osc.start(startAt);
    osc.stop(endAt + 0.03);
    audio.previewNodes.push({ osc, gain });
  }

  dom.previewBadge.textContent = "♪ Pre-listen...";
  dom.previewBadge.style.display = "block";

  const hideMs = Math.max(300, Math.round((maxEnd - ctxAudio.currentTime) * 1000));
  audio.previewHideTimer = window.setTimeout(() => {
    stopPreviewAudio();
  }, hideMs);
}

async function ensureSongCache() {
  state.songCache = await api("/api/song");
}

function scheduleGuidePlayback(game) {
  if (!audio.ctx || !state.songCache || !state.songCache.loaded) return;
  const token = Number(game.startToken || -1);
  if (token >= 0 && token === audio.guideToken) return;

  stopGuideAudio();

  const notes = Array.isArray(state.songCache.notes) ? state.songCache.notes.slice(0, 1800) : [];
  if (!notes.length) return;

  const guideVolume = clamp(Number(game.settings && game.settings.guideVolume), 0, 1);
  if (guideVolume <= 0) return;
  const playbackRate = clamp(Number(game.settings && game.settings.playbackRate), 0.55, 1.40);

  const delaySec = Math.max(0.01, (Number(game.songStartEpochMs || 0) - Date.now()) / 1000);
  const t0 = audio.ctx.currentTime + delaySec;
  audio.guideToken = token;

  for (const n of notes) {
    const hz = Number(n.freqHz);
    const st = Number(n.startSec);
    const dur = clamp(Number(n.durSec), 0.05, 0.9);
    if (!Number.isFinite(hz) || hz <= 0 || !Number.isFinite(st)) continue;

    const osc = audio.ctx.createOscillator();
    const gain = audio.ctx.createGain();
    osc.type = "triangle";
    osc.frequency.value = hz;

    const startAt = t0 + (st / playbackRate);
    const endAt = startAt + (dur / playbackRate);

    gain.gain.setValueAtTime(0.0, Math.max(audio.ctx.currentTime, startAt - 0.015));
    gain.gain.linearRampToValueAtTime(guideVolume, startAt + 0.012);
    gain.gain.linearRampToValueAtTime(guideVolume * 0.75, Math.max(startAt + 0.02, endAt - 0.03));
    gain.gain.linearRampToValueAtTime(0.0, endAt);

    osc.connect(gain);
    gain.connect(audio.ctx.destination);
    osc.start(startAt);
    osc.stop(endAt + 0.04);

    audio.guideNodes.push({ osc, gain });
  }
}

function showTransientJudgement(judge) {
  const key = String(judge || "").toLowerCase();
  if (!JUDGE_CLASS[key]) return;

  dom.judgePopup.classList.remove("show", "p", "g", "o", "m");
  dom.judgePopup.textContent = key.toUpperCase();
  dom.judgePopup.classList.add(
    key === "perfect" ? "p" : key === "good" ? "g" : key === "ok" ? "o" : "m"
  );
  void dom.judgePopup.offsetWidth;
  dom.judgePopup.classList.add("show");

  dom.flashOverlay.classList.remove("fp", "fg", "fo", "fm");
  dom.flashOverlay.classList.add(
    key === "perfect" ? "fp" : key === "good" ? "fg" : key === "ok" ? "fo" : "fm"
  );

  if (particles.length < 220) {
    const count = key === "perfect" ? 20 : key === "good" ? 14 : key === "ok" ? 10 : 7;
    const w = dom.highway.clientWidth;
    const h = dom.highway.clientHeight;
    const cx = w * 0.5;
    const cy = h * 0.44;
    const palette = key === "perfect" ? "#fbbf24" : key === "good" ? "#34d399" : key === "ok" ? "#fb923c" : "#f43f5e";

    for (let i = 0; i < count; i += 1) {
      const ang = (Math.PI * 2 * i) / count + (Math.random() - 0.5) * 0.5;
      const speed = 40 + Math.random() * 110;
      particles.push({
        x: cx,
        y: cy,
        vx: Math.cos(ang) * speed,
        vy: Math.sin(ang) * speed,
        life: 1,
        decay: 0.025 + Math.random() * 0.03,
        color: palette,
      });
    }
  }
}

function updateHUD(game) {
  if (!game) return;

  const score = Number(game.score || 0);
  if (dom.scoreVal.textContent !== String(score)) {
    dom.scoreVal.textContent = String(score);
    dom.scoreVal.style.transform = "scale(1.12)";
    window.setTimeout(() => {
      dom.scoreVal.style.transform = "";
    }, 100);
  }

  const combo = Number(game.combo || 0);
  dom.comboVal.textContent = String(combo);
  dom.comboCard.classList.remove("c-fire", "c-max");
  if (combo >= 50) dom.comboCard.classList.add("c-max");
  else if (combo >= 15) dom.comboCard.classList.add("c-fire");

  if (combo > state.lastCombo && Number(game.lastMultiplier || 1) > 1) {
    dom.comboBadge.textContent = `x${fmt(game.lastMultiplier, 2)}`;
    dom.comboBadge.classList.add("visible");
    clearTimeout(dom.comboBadge._t);
    dom.comboBadge._t = window.setTimeout(() => dom.comboBadge.classList.remove("visible"), 900);
  }
  state.lastCombo = combo;

  dom.playerVal.textContent = `${fmt(game.playerFreqHz, 1)} Hz · ${game.playerNote || "--"}`;

  const target = game.currentTarget;
  if (target) {
    dom.targetVal.textContent = `${target.note} (${fmt(target.freqHz, 1)} Hz) · ${fmt(target.pitchErrorCents, 0)} cents`;
  } else {
    dom.targetVal.textContent = "Target: --";
  }

  const j = String(game.lastJudgement || "").toLowerCase();
  if (j && j !== state.lastJudgement) {
    state.lastJudgement = j;
    dom.judgeVal.textContent = j.toUpperCase();
    dom.judgeVal.classList.remove("jv-perfect", "jv-good", "jv-ok", "jv-miss");
    dom.judgeVal.classList.add(JUDGE_CLASS[j] || "");
    dom.judgeMetaVal.textContent = `x${fmt(game.lastMultiplier || 1, 2)} · ${Number(game.lastPoints || 0)} pts`;
    showTransientJudgement(j);
  }

  const duration = Number(game.songDurationSec || 0);
  const songTime = Number(game.songTimeSec || 0);
  const pct = duration > 0 ? clamp((songTime / duration) * 100, 0, 100) : 0;
  dom.progressFill.style.width = `${pct}%`;
}

function updateCountdown(game) {
  const epochMs = Number(game.songStartEpochMs || 0);
  if (epochMs <= 0) {
    dom.countNum.textContent = "3";
    return;
  }
  const remain = epochMs - Date.now();
  if (remain <= 0) {
    dom.countNum.textContent = "GO";
    return;
  }
  dom.countNum.textContent = String(Math.max(1, Math.ceil(remain / 1000)));
  dom.countSongName.textContent = state.selectedMidi || game.selectedMidi || "";
}

function showResults(game) {
  const token = Number(game.startToken || -1);
  if (token === state.resultsShownForToken) return;
  state.resultsShownForToken = token;

  const counts = game.counts || {};
  const perfect = Number(counts.perfect || 0);
  const good = Number(counts.good || 0);
  const ok = Number(counts.ok || 0);
  const miss = Number(counts.miss || 0);
  const total = perfect + good + ok + miss;

  const weighted = (perfect * 1 + good * 0.7 + ok * 0.35) / Math.max(1, total);
  const accuracy = weighted * 100;

  let rank = "C";
  if (accuracy >= 96) rank = "S";
  else if (accuracy >= 86) rank = "A";
  else if (accuracy >= 72) rank = "B";

  dom.rankBig.textContent = rank;
  dom.rankBig.className = `rankBig rank-${rank}`;
  dom.resultsSong.textContent = game.selectedMidi || state.selectedMidi || "-";
  dom.resultsScore.textContent = String(game.score || 0);
  dom.rPerfect.textContent = String(perfect);
  dom.rGood.textContent = String(good);
  dom.rOk.textContent = String(ok);
  dom.rMiss.textContent = String(miss);
  dom.rAccuracy.textContent = `${accuracy.toFixed(1)}%`;
  dom.rMaxCombo.textContent = String(game.maxCombo || 0);

  setScreen("results");
}

function syncSettingsUI(settings) {
  if (!settings) return;
  state.settingsSyncLock = true;
  try {
    for (const id of settingIds) {
      const el = document.getElementById(id);
      const out = document.getElementById(`${id}Out`);
      if (!el) continue;
      const value = Number(settings[id]);
      if (!Number.isFinite(value)) continue;
      el.value = String(value);
      if (out) out.textContent = settingOut(id, value);
    }
    setDifficultyButtons(String(settings.difficultyPreset || "custom").toLowerCase());
  } finally {
    state.settingsSyncLock = false;
  }
}

function patchSettings(patch) {
  return api("/api/game/settings", "POST", patch).then((resp) => {
    if (resp && resp.settings) {
      syncSettingsUI(resp.settings);
    }
    return resp;
  });
}

function selectedMidiHasError() {
  const row = state.songs.find((s) => s.name === state.selectedMidi);
  return !!(row && row.parseError);
}

async function selectMidi(name, userRequestedPreview) {
  const target = String(name || "").trim();
  if (!target) return;

  state.selectedMidi = target;
  state.selectionToken += 1;
  const myToken = state.selectionToken;
  markSelectedSong();

  const fallbackMeta = state.songs.find((s) => s.name === target) || null;
  const hasParseError = !!(fallbackMeta && fallbackMeta.parseError);
  renderSongDetail(null, fallbackMeta);
  dom.diffCard.style.display = "block";
  dom.startBigBtn.disabled = hasParseError;
  if (hasParseError) {
    renderDetailError(fallbackMeta.parseError);
    return;
  }

  stopPreviewAudio();

  try {
    await api("/api/game/load", "POST", { name: target });
    const preview = await api(`/api/game/preview?name=${encodeURIComponent(target)}`);

    if (myToken !== state.selectionToken) return;

    state.selectedPreview = preview;
    renderSongDetail(preview, fallbackMeta);

    if (userRequestedPreview) {
      await playPreview(preview);
    }

    await ensureSongCache();
    await pullState();
  } catch (err) {
    if (myToken !== state.selectionToken) return;
    state.selectedPreview = null;
    dom.startBigBtn.disabled = true;
    renderDetailError(String(err.message || err));
  }
}

async function refreshSongs() {
  const prevSelected = state.selectedMidi;
  const data = await api("/api/game/midis");
  const midis = Array.isArray(data.midis) ? data.midis : [];
  state.songs = midis;
  renderSongs();

  if (!midis.length) {
    state.selectedMidi = null;
    state.selectedPreview = null;
    renderSongDetail(null, null);
    return;
  }

  const selected =
    state.selectedMidi ||
    (midis.find((m) => m.selected) || {}).name ||
    midis[0].name;

  if (selected === prevSelected && state.selectedPreview) {
    markSelectedSong();
    return;
  }

  await selectMidi(selected, false);
}

async function startGame() {
  if (!state.selectedMidi || state.startingGame) return;
  state.startingGame = true;
  dom.startBigBtn.disabled = true;

  try {
    await ensureAudioContext(true);
    stopPreviewAudio();
    stopGuideAudio();
    await api("/api/game/load", "POST", { name: state.selectedMidi });
    await ensureSongCache();
    await api("/api/game/start", "POST", {});
    state.resultsShownForToken = -1;
    await pullState();
  } catch (err) {
    dom.startBigBtn.disabled = true;
    renderDetailError(String(err.message || err));
  } finally {
    state.startingGame = false;
    if (state.selectedMidi && !selectedMidiHasError()) {
      dom.startBigBtn.disabled = false;
    }
  }
}

async function stopGameToSelect(resetToo) {
  try {
    await api("/api/game/stop", "POST", {});
    if (resetToo) {
      await api("/api/game/reset", "POST", {});
    }
  } catch (_) {
    // ignore
  }
  stopGuideAudio();
  state.lastStartTokenScheduled = -1;
  setScreen("select");
  await pullState();
}

async function pullState() {
  if (state.pollInFlight) return;
  state.pollInFlight = true;

  try {
    const payload = await api("/api/state");
    state.lastApiState = payload;

    const game = payload.game || {};
    setStatusBadge(game);
    syncSettingsUI(game.settings || {});
    updateHUD(game);

    if (!game.playing) {
      stopGuideAudio();
      state.lastStartTokenScheduled = -1;
    }

    if (game.playing && Number(game.startToken || -1) !== state.lastStartTokenScheduled) {
      await ensureAudioContext(false);
      await ensureSongCache();
      scheduleGuidePlayback(game);
      state.lastStartTokenScheduled = Number(game.startToken || -1);
    }

    if (game.playing) {
      updateCountdown(game);
      if (Date.now() + 30 < Number(game.songStartEpochMs || 0)) {
        setScreen("countdown");
      } else {
        setScreen("play");
      }
    } else if (game.completed) {
      showResults(game);
    } else {
      if (state.currentScreen === "play" || state.currentScreen === "countdown") {
        setScreen("select");
      }
    }
  } catch (_) {
    // transient errors
  } finally {
    state.pollInFlight = false;
  }
}

function bindUI() {
  dom.helpBtn.addEventListener("click", () => {
    dom.helpModal.classList.remove("hidden");
  });
  dom.closeHelpBtn.addEventListener("click", () => {
    dom.helpModal.classList.add("hidden");
  });
  dom.helpModal.addEventListener("click", (ev) => {
    if (ev.target === dom.helpModal) {
      dom.helpModal.classList.add("hidden");
    }
  });

  dom.settingsBtn.addEventListener("click", () => {
    dom.settingsPanel.classList.toggle("hidden");
  });

  dom.startBigBtn.addEventListener("click", () => {
    void startGame();
  });

  dom.stopPlayBtn.addEventListener("click", () => {
    void stopGameToSelect(false);
  });

  dom.retryBtn.addEventListener("click", () => {
    void startGame();
  });

  dom.backSelectBtn.addEventListener("click", () => {
    void stopGameToSelect(true);
  });

  for (const btn of dom.diffButtons) {
    btn.addEventListener("click", async () => {
      const diff = String(btn.dataset.diff || "custom").toLowerCase();
      try {
        await patchSettings({ difficultyPreset: diff });
        await pullState();
      } catch (_) {
        // ignore
      }
    });
  }

  for (const id of settingIds) {
    const el = document.getElementById(id);
    const out = document.getElementById(`${id}Out`);
    if (!el) continue;

    const onValue = async () => {
      const v = Number(el.value);
      if (out) out.textContent = settingOut(id, v);
      if (state.settingsSyncLock) return;

      const patch = { [id]: v };
      if (state.selectedDifficulty !== "custom") {
        patch.difficultyPreset = "custom";
      }

      try {
        await patchSettings(patch);
      } catch (_) {
        // ignore
      }
    };

    el.addEventListener("input", () => {
      const v = Number(el.value);
      if (out) out.textContent = settingOut(id, v);
    });
    el.addEventListener("change", () => {
      void onValue();
    });
  }

  const isTypingTarget = (target) => {
    const tag = (target && target.tagName ? target.tagName : "").toLowerCase();
    return tag === "input" || tag === "textarea" || tag === "select" || (target && target.isContentEditable);
  };

  document.addEventListener("keydown", (ev) => {
    if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
    if (isTypingTarget(ev.target)) return;

    const is1 = ev.key === "1" || ev.code === "Digit1";
    const is2 = ev.key === "2" || ev.code === "Digit2";
    const is3 = ev.key === "3" || ev.code === "Digit3";

    if (is1) {
      ev.preventDefault();
      dom.helpModal.classList.toggle("hidden");
      return;
    }

    if (is2) {
      ev.preventDefault();
      dom.settingsPanel.classList.toggle("hidden");
      return;
    }

    if (is3) {
      ev.preventDefault();
      const playing = Boolean(state.lastApiState && state.lastApiState.game && state.lastApiState.game.playing);
      if (playing) {
        void stopGameToSelect(false);
      } else {
        void startGame();
      }
    }
  });
}

function resizeCanvas() {
  const dpr = Math.max(1, window.devicePixelRatio || 1);
  const rect = dom.highway.getBoundingClientRect();
  dom.highway.width = Math.max(1, Math.floor(rect.width * dpr));
  dom.highway.height = Math.max(1, Math.floor(rect.height * dpr));
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function buildKeyboardLayout(minMidi, maxMidi, width, y, h) {
  const keys = new Map();
  const whiteMidis = [];

  for (let m = minMidi; m <= maxMidi; m += 1) {
    if (!isBlackMidi(m)) whiteMidis.push(m);
  }

  const whiteW = width / Math.max(1, whiteMidis.length);
  let whiteIdx = 0;

  for (let m = minMidi; m <= maxMidi; m += 1) {
    if (!isBlackMidi(m)) {
      const x = whiteIdx * whiteW;
      keys.set(m, {
        midi: m,
        black: false,
        x,
        w: whiteW,
        y,
        h,
        centerX: x + whiteW * 0.5,
      });
      whiteIdx += 1;
    }
  }

  const blackW = whiteW * 0.62;
  const blackH = h * 0.62;
  for (let m = minMidi; m <= maxMidi; m += 1) {
    if (!isBlackMidi(m)) continue;

    let prevWhite = m - 1;
    while (prevWhite >= minMidi && isBlackMidi(prevWhite)) prevWhite -= 1;
    let nextWhite = m + 1;
    while (nextWhite <= maxMidi && isBlackMidi(nextWhite)) nextWhite += 1;

    const prevK = keys.get(prevWhite);
    const nextK = keys.get(nextWhite);
    if (!prevK && !nextK) continue;

    let cx = 0;
    if (prevK && nextK) cx = (prevK.centerX + nextK.centerX) * 0.5;
    else if (prevK) cx = prevK.centerX + whiteW * 0.33;
    else cx = nextK.centerX - whiteW * 0.33;

    const x = cx - blackW * 0.5;
    keys.set(m, {
      midi: m,
      black: true,
      x,
      w: blackW,
      y,
      h: blackH,
      centerX: cx,
    });
  }

  return { minMidi, maxMidi, keys };
}

function midiToX(layout, midi) {
  const m = Number(midi);
  if (!Number.isFinite(m)) return 0;

  const lo = Math.floor(m);
  const hi = Math.ceil(m);
  const klo = layout.keys.get(lo);
  const khi = layout.keys.get(hi);

  if (klo && khi && hi !== lo) {
    const t = clamp((m - lo) / (hi - lo), 0, 1);
    return klo.centerX + (khi.centerX - klo.centerX) * t;
  }
  if (klo) return klo.centerX;
  if (khi) return khi.centerX;

  const first = layout.keys.get(layout.minMidi);
  const last = layout.keys.get(layout.maxMidi);
  if (!first || !last) return 0;
  const p = clamp((m - layout.minMidi) / Math.max(1, layout.maxMidi - layout.minMidi), 0, 1);
  return first.centerX + (last.centerX - first.centerX) * p;
}

function roundedRect(x, y, w, h, r) {
  const rr = Math.min(r, w * 0.5, h * 0.5);
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.lineTo(x + w - rr, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + rr);
  ctx.lineTo(x + w, y + h - rr);
  ctx.quadraticCurveTo(x + w, y + h, x + w - rr, y + h);
  ctx.lineTo(x + rr, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - rr);
  ctx.lineTo(x, y + rr);
  ctx.quadraticCurveTo(x, y, x + rr, y);
  ctx.closePath();
}

function pickRenderRange(game) {
  const range = game && game.songRange ? game.songRange : null;
  let minMidi = Number(range && range.minMidi);
  let maxMidi = Number(range && range.maxMidi);

  if (!Number.isFinite(minMidi) || !Number.isFinite(maxMidi)) {
    minMidi = 48;
    maxMidi = 84;
  }

  const upcoming = Array.isArray(game && game.upcoming) ? game.upcoming : [];
  for (const n of upcoming) {
    const m = Number(n.midi);
    if (!Number.isFinite(m)) continue;
    minMidi = Math.min(minMidi, m);
    maxMidi = Math.max(maxMidi, m);
  }

  minMidi = Math.floor(minMidi) - 3;
  maxMidi = Math.ceil(maxMidi) + 3;

  if ((maxMidi - minMidi) < 18) {
    const mid = (maxMidi + minMidi) * 0.5;
    minMidi = Math.floor(mid - 9);
    maxMidi = Math.ceil(mid + 9);
  }

  return { minMidi, maxMidi };
}

let drawTimePrev = performance.now();

function drawFrame() {
  const now = performance.now();
  const dt = Math.min(0.08, (now - drawTimePrev) / 1000);
  drawTimePrev = now;

  const w = dom.highway.clientWidth;
  const h = dom.highway.clientHeight;

  ctx.clearRect(0, 0, w, h);

  const bg = ctx.createLinearGradient(0, 0, 0, h);
  bg.addColorStop(0, "#e8f6ff");
  bg.addColorStop(1, "#c6e8ff");
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, w, h);

  const game = state.lastApiState && state.lastApiState.game ? state.lastApiState.game : null;
  if (!game) {
    requestAnimationFrame(drawFrame);
    return;
  }

  const renderRange = pickRenderRange(game);
  const keyboardH = Math.max(88, h * 0.24);
  const keyboardY = h - keyboardH;
  const hitY = keyboardY - 16;

  const layout = buildKeyboardLayout(renderRange.minMidi, renderRange.maxMidi, w, keyboardY, keyboardH);

  const lookAhead = clamp(Number(game.settings && game.settings.scrollLookAheadSec), 1.4, 8.0);
  const pxPerSec = (hitY - 14) / Math.max(0.1, lookAhead);

  ctx.strokeStyle = "rgba(255,255,255,0.05)";
  for (let m = layout.minMidi; m <= layout.maxMidi; m += 1) {
    const key = layout.keys.get(m);
    if (!key || key.black) continue;
    ctx.beginPath();
    ctx.moveTo(key.centerX, 0);
    ctx.lineTo(key.centerX, hitY - 2);
    ctx.stroke();
  }

  ctx.strokeStyle = "rgba(225,140,24,0.95)";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(0, hitY);
  ctx.lineTo(w, hitY);
  ctx.stroke();

  const upcoming = Array.isArray(game.upcoming) ? game.upcoming : [];
  for (const n of upcoming) {
    const dtToHit = Number(n.timeToHitSec || 0);
    const yBottom = hitY - dtToHit * pxPerSec;
    const noteH = Math.max(10, Number(n.durSec || 0.12) * pxPerSec);
    const yTop = yBottom - noteH;

    if (yBottom < -30 || yTop > hitY + 30) continue;

    const key = layout.keys.get(Number(n.midi));
    const laneW = key ? (key.black ? key.w * 0.9 : key.w * 0.78) : 14;
    const x = midiToX(layout, Number(n.midi)) - laneW * 0.5;

    const near = clamp(1 - Math.abs(dtToHit) / 0.28, 0, 1);
    const grad = ctx.createLinearGradient(0, yTop, 0, yBottom);
    grad.addColorStop(0, `rgba(56,189,248,${0.24 + near * 0.30})`);
    grad.addColorStop(1, `rgba(72,153,120,${0.42 + near * 0.26})`);

    roundedRect(x, yTop, laneW, noteH, 6);
    ctx.fillStyle = grad;
    ctx.fill();

    if (near > 0.55) {
      ctx.strokeStyle = `rgba(36,84,124,${0.20 + near * 0.35})`;
      ctx.lineWidth = 1.2;
      ctx.stroke();
    }
  }

  const target = game.currentTarget;
  const targetMidi = target && Number.isFinite(Number(target.midi)) ? Number(target.midi) : null;
  const playerMidi = hzToMidi(Number(game.playerFreqHz || 0));
  const playerMidiRounded = Number.isFinite(playerMidi) ? Math.round(playerMidi) : null;

  const pitchBarH = dom.pitchMarker.parentElement ? dom.pitchMarker.parentElement.clientHeight : 0;
  if (Number.isFinite(playerMidi)) {
    const pct = clamp((playerMidi - layout.minMidi) / Math.max(1, layout.maxMidi - layout.minMidi), 0, 1);
    dom.pitchMarker.style.top = `${(1 - pct) * Math.max(0, pitchBarH - 5)}px`;

    const x = midiToX(layout, playerMidi);
    ctx.strokeStyle = "rgba(23,116,177,0.95)";
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(x, hitY - 10);
    ctx.lineTo(x, hitY + 10);
    ctx.stroke();
  }

  for (let m = layout.minMidi; m <= layout.maxMidi; m += 1) {
    const key = layout.keys.get(m);
    if (!key || key.black) continue;

    let fill = "#f5f7fb";
    if (m === targetMidi && m === playerMidiRounded) fill = "#8ff9bf";
    else if (m === targetMidi) fill = "#f7da71";
    else if (m === playerMidiRounded) fill = "#8ad8ff";

    ctx.fillStyle = fill;
    ctx.fillRect(key.x, key.y, key.w, key.h);
    ctx.strokeStyle = "rgba(10,18,28,0.38)";
    ctx.lineWidth = 1;
    ctx.strokeRect(key.x, key.y, key.w, key.h);
  }

  for (let m = layout.minMidi; m <= layout.maxMidi; m += 1) {
    const key = layout.keys.get(m);
    if (!key || !key.black) continue;

    let fill = "#121a2c";
    if (m === targetMidi && m === playerMidiRounded) fill = "#41d98a";
    else if (m === targetMidi) fill = "#dbb83e";
    else if (m === playerMidiRounded) fill = "#3597dd";

    ctx.fillStyle = fill;
    roundedRect(key.x, key.y, key.w, key.h, 4);
    ctx.fill();
  }

  if (targetMidi !== null) {
    const tx = midiToX(layout, targetMidi);
    ctx.strokeStyle = "rgba(251,191,36,0.75)";
    ctx.setLineDash([5, 6]);
    ctx.beginPath();
    ctx.moveTo(tx, 0);
    ctx.lineTo(tx, hitY);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  for (let i = particles.length - 1; i >= 0; i -= 1) {
    const p = particles[i];
    p.x += p.vx * dt;
    p.y += p.vy * dt;
    p.vy += 120 * dt;
    p.life -= p.decay;
    if (p.life <= 0) {
      particles.splice(i, 1);
      continue;
    }
    ctx.globalAlpha = p.life;
    ctx.fillStyle = p.color;
    ctx.beginPath();
    ctx.arc(p.x, p.y, 2 + p.life * 3, 0, Math.PI * 2);
    ctx.fill();
    ctx.globalAlpha = 1;
  }

  requestAnimationFrame(drawFrame);
}

function boot() {
  bindUI();
  resizeCanvas();
  window.addEventListener("resize", resizeCanvas);

  setScreen("select");
  renderSongDetail(null, null);

  void refreshSongs();
  void pullState();

  window.setInterval(() => {
    void pullState();
  }, 70);

  window.setInterval(() => {
    if (state.currentScreen === "select") {
      void refreshSongs();
    }
  }, 6000);

  requestAnimationFrame(drawFrame);
}

boot();
