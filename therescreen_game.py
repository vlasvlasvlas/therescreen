#!/usr/bin/env python3
"""Therescreen Game Mode: rhythm game driven by lid-angle pitch."""

from __future__ import annotations

import argparse
import bisect
import copy
import errno
import json
import logging
import math
import mimetypes
import signal
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import therescreen as core

LOGGER = logging.getLogger("therescreen.game")


@dataclass
class MidiNote:
    start_sec: float
    dur_sec: float
    midi: int
    freq_hz: float
    name: str


@dataclass
class ParsedSong:
    name: str
    notes: list[MidiNote]
    duration_sec: float
    min_midi: int
    max_midi: int


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(v)))


def note_name(midi: int) -> str:
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    m = int(midi)
    return f"{names[m % 12]}{(m // 12) - 1}"


def midi_to_freq(midi: int) -> float:
    return 440.0 * (2.0 ** ((int(midi) - 69) / 12.0))


def cents_error(player_hz: float, target_hz: float) -> float:
    if player_hz <= 0.0 or target_hz <= 0.0:
        return float("inf")
    return 1200.0 * math.log2(player_hz / target_hz)


def _read_u16(b: bytes, off: int) -> int:
    return int.from_bytes(b[off : off + 2], "big", signed=False)


def _read_u32(b: bytes, off: int) -> int:
    return int.from_bytes(b[off : off + 4], "big", signed=False)


def _read_vlq(b: bytes, off: int) -> tuple[int, int]:
    value = 0
    i = off
    while i < len(b):
        c = b[i]
        i += 1
        value = (value << 7) | (c & 0x7F)
        if (c & 0x80) == 0:
            return value, i
    raise ValueError("invalid VLQ")


def _parse_track_events(track_data: bytes) -> tuple[list[tuple[int, int]], list[tuple[int, int, int, int]], int]:
    """Return (tempo_events, note_events, end_tick).

    tempo_events: (tick, tempo_us_per_quarter)
    note_events: (tick, kind(1=on,0=off), channel, note)
    """

    tempos: list[tuple[int, int]] = []
    notes: list[tuple[int, int, int, int]] = []

    i = 0
    tick = 0
    running_status: int | None = None

    while i < len(track_data):
        delta, i = _read_vlq(track_data, i)
        tick += delta
        if i >= len(track_data):
            break

        b0 = track_data[i]
        data: list[int] = []

        if b0 >= 0x80:
            status = b0
            i += 1
            if 0x80 <= status <= 0xEF:
                running_status = status
        else:
            if running_status is None:
                # Some malformed files emit data bytes before first status.
                # Skip and continue instead of aborting full parse.
                i += 1
                continue
            status = running_status
            data.append(b0)
            i += 1

        if status == 0xFF:
            if i >= len(track_data):
                break
            meta_type = track_data[i]
            i += 1
            ln, i = _read_vlq(track_data, i)
            payload = track_data[i : i + ln]
            i += ln
            if meta_type == 0x51 and len(payload) == 3:
                tempo = (payload[0] << 16) | (payload[1] << 8) | payload[2]
                tempos.append((tick, int(tempo)))
            if meta_type == 0x2F:
                break
            continue

        if status in (0xF0, 0xF7):
            ln, i = _read_vlq(track_data, i)
            i += ln
            running_status = None
            continue

        if status < 0x80 or status > 0xEF:
            running_status = None
            continue

        ev = status & 0xF0
        ch = status & 0x0F
        data_len = 1 if ev in (0xC0, 0xD0) else 2

        while len(data) < data_len and i < len(track_data):
            data.append(track_data[i])
            i += 1
        if len(data) < data_len:
            break

        d1 = int(data[0])
        d2 = int(data[1]) if data_len > 1 else 0

        if ch == 9:
            continue

        if ev == 0x90:
            if d2 > 0:
                notes.append((tick, 1, ch, d1))
            else:
                notes.append((tick, 0, ch, d1))
        elif ev == 0x80:
            notes.append((tick, 0, ch, d1))

    return tempos, notes, tick


def _build_tick_to_sec(tempo_events: list[tuple[int, int]], tpq: int) -> tuple[list[int], list[float], list[int]]:
    events = sorted((int(t), int(usq)) for t, usq in tempo_events if usq > 0)
    merged: list[tuple[int, int]] = []
    for t, u in events:
        if merged and merged[-1][0] == t:
            merged[-1] = (t, u)
        else:
            merged.append((t, u))

    ticks = [0]
    secs = [0.0]
    tempos = [500000]

    last_tick = 0
    last_sec = 0.0
    cur_tempo = 500000

    for tick, new_tempo in merged:
        if tick < last_tick:
            continue
        dt = tick - last_tick
        last_sec += (dt * cur_tempo) / (tpq * 1_000_000.0)
        ticks.append(tick)
        secs.append(last_sec)
        tempos.append(new_tempo)
        last_tick = tick
        cur_tempo = new_tempo

    return ticks, secs, tempos


def _tick_to_sec(tick: int, ticks: list[int], secs: list[float], tempos: list[int], tpq: int) -> float:
    i = bisect.bisect_right(ticks, int(tick)) - 1
    i = max(0, min(i, len(ticks) - 1))
    base_tick = ticks[i]
    base_sec = secs[i]
    tempo = tempos[i]
    dt = int(tick) - base_tick
    return base_sec + (dt * tempo) / (tpq * 1_000_000.0)


def _monophonize(notes: list[MidiNote]) -> list[MidiNote]:
    if not notes:
        return []
    notes = sorted(notes, key=lambda n: (n.start_sec, -n.midi))
    grouped: list[MidiNote] = []
    window = 0.040
    cur_group: list[MidiNote] = [notes[0]]

    for n in notes[1:]:
        if abs(n.start_sec - cur_group[0].start_sec) <= window:
            cur_group.append(n)
        else:
            grouped.append(max(cur_group, key=lambda x: x.midi))
            cur_group = [n]
    grouped.append(max(cur_group, key=lambda x: x.midi))

    out: list[MidiNote] = []
    prev_start = -1.0
    for n in grouped:
        s = max(prev_start + 0.030, n.start_sec)
        d = clamp(n.dur_sec, 0.090, 2.600)
        out.append(MidiNote(start_sec=s, dur_sec=d, midi=n.midi, freq_hz=n.freq_hz, name=n.name))
        prev_start = s
    return out


def _normalize_melody_channel(value: Any) -> int | None:
    try:
        ch = int(value)
    except (TypeError, ValueError):
        return None
    # Accept both one-based (1..16) and zero-based (0..15).
    if 1 <= ch <= 16:
        return ch - 1
    if 0 <= ch <= 15:
        return ch
    return None


def parse_midi_file(
    path: Path,
    *,
    melody_track_index: int | None = None,
    melody_channel: int | None = None,
) -> ParsedSong:
    data = path.read_bytes()
    if len(data) < 14 or data[:4] != b"MThd":
        raise ValueError("invalid MIDI header")

    hlen = _read_u32(data, 4)
    if hlen < 6:
        raise ValueError("invalid MIDI header length")

    fmt = _read_u16(data, 8)
    ntrks = _read_u16(data, 10)
    division = _read_u16(data, 12)

    if division & 0x8000:
        raise ValueError("SMPTE time division not supported")
    tpq = division
    if tpq <= 0:
        raise ValueError("invalid ticks-per-quarter")

    off = 8 + hlen
    tracks: list[bytes] = []
    for _ in range(ntrks):
        if off + 8 > len(data) or data[off : off + 4] != b"MTrk":
            raise ValueError("invalid MIDI track chunk")
        ln = _read_u32(data, off + 4)
        off += 8
        if off + ln > len(data):
            raise ValueError("track chunk out of bounds")
        tracks.append(data[off : off + ln])
        off += ln

    all_tempos: list[tuple[int, int]] = []
    track_note_events: list[list[tuple[int, int, int, int]]] = []
    track_end_ticks: list[int] = []

    for tr in tracks:
        tempos, notes, end_tick = _parse_track_events(tr)
        all_tempos.extend(tempos)
        track_note_events.append(notes)
        track_end_ticks.append(end_tick)

    ticks, secs, tempos = _build_tick_to_sec(all_tempos, tpq)

    per_track_notes: list[list[MidiNote]] = []
    per_track_channel_notes: list[dict[int, list[MidiNote]]] = []
    for evs, end_tick in zip(track_note_events, track_end_ticks):
        active: dict[tuple[int, int], list[int]] = {}
        notes_by_channel: dict[int, list[MidiNote]] = {}
        for tick, kind, ch, note in sorted(evs, key=lambda x: x[0]):
            key = (ch, note)
            if kind == 1:
                active.setdefault(key, []).append(tick)
                continue
            starts = active.get(key)
            if not starts:
                continue
            start_tick = starts.pop()
            if not starts:
                active.pop(key, None)
            s = _tick_to_sec(start_tick, ticks, secs, tempos, tpq)
            e = _tick_to_sec(tick, ticks, secs, tempos, tpq)
            d = max(0.030, e - s)
            m = int(note)
            notes_by_channel.setdefault(ch, []).append(
                MidiNote(start_sec=s, dur_sec=d, midi=m, freq_hz=midi_to_freq(m), name=note_name(m))
            )

        track_end_sec = _tick_to_sec(end_tick, ticks, secs, tempos, tpq)
        for (ch, note), starts in active.items():
            for start_tick in starts:
                s = _tick_to_sec(start_tick, ticks, secs, tempos, tpq)
                d = max(0.030, track_end_sec - s)
                m = int(note)
                notes_by_channel.setdefault(ch, []).append(
                    MidiNote(start_sec=s, dur_sec=d, midi=m, freq_hz=midi_to_freq(m), name=note_name(m))
                )

        combined: list[MidiNote] = []
        for ch in list(notes_by_channel.keys()):
            ch_notes = sorted(notes_by_channel[ch], key=lambda n: (n.start_sec, n.midi))
            notes_by_channel[ch] = ch_notes
            combined.extend(ch_notes)
        per_track_channel_notes.append(notes_by_channel)
        per_track_notes.append(sorted(combined, key=lambda n: (n.start_sec, n.midi)))

    def _track_density(track: list[MidiNote]) -> float:
        if not track:
            return 0.0
        start_t = track[0].start_sec
        end_t = max(n.start_sec + n.dur_sec for n in track)
        dur = max(0.05, end_t - start_t)
        return float(len(track)) / dur

    def _track_score(track: list[MidiNote]) -> tuple[float, float, int]:
        if not track:
            return (-1.0, -1.0, 0)
        mids = sorted(n.midi for n in track)
        median = mids[len(mids) // 2]
        spread = mids[-1] - mids[0]
        lo = mids[int(0.10 * (len(mids) - 1))]
        hi = mids[int(0.90 * (len(mids) - 1))]
        spread_trim = max(0, hi - lo)
        unique = len(set(mids))
        count = len(track)
        density = _track_density(track)

        # Prefer melodic lines: wide pitch movement + enough unique notes, avoid dense accompaniment tracks.
        score = 0.0
        score += float(spread_trim) * 9.0
        score += float(unique) * 7.0
        score += float(spread) * 1.5
        score += max(0.0, 22.0 - abs(float(median) - 74.0)) * 2.4
        score -= abs(float(density) - 1.8) * 40.0
        score -= max(0.0, float(count - 450)) * 0.60
        if median < 64:
            score -= float(64 - median) * 12.0
        if median > 92:
            score -= float(median - 92) * 10.0
        return (score, float(median), count)

    def _is_melody_candidate(t: list[MidiNote]) -> bool:
        if len(t) < 8:
            return False
        unique = len(set(n.midi for n in t))
        if unique < 5:
            return False
        density = _track_density(t)
        if density > 8.0:
            return False
        return True

    forced_track_idx: int | None = None
    if melody_track_index is not None:
        try:
            mti = int(melody_track_index)
            if 0 <= mti < len(per_track_notes):
                forced_track_idx = mti
        except (TypeError, ValueError):
            forced_track_idx = None
    forced_channel = _normalize_melody_channel(melody_channel)

    base: list[MidiNote] | None = None

    # 1) If track is forced, use that track first (optionally narrowed to one channel).
    if forced_track_idx is not None:
        by_ch = per_track_channel_notes[forced_track_idx]
        if forced_channel is not None and forced_channel in by_ch and by_ch[forced_channel]:
            base = by_ch[forced_channel]
        else:
            pool = [t for t in by_ch.values() if _is_melody_candidate(t)]
            if pool:
                base = max(pool, key=_track_score)
            else:
                base = per_track_notes[forced_track_idx]

    # 2) If only channel is forced, choose best track carrying that channel.
    if base is None and forced_channel is not None:
        pool = []
        weak_pool = []
        for by_ch in per_track_channel_notes:
            t = by_ch.get(forced_channel)
            if not t:
                continue
            weak_pool.append(t)
            if _is_melody_candidate(t):
                pool.append(t)
        if pool:
            base = max(pool, key=_track_score)
        elif weak_pool:
            base = max(weak_pool, key=_track_score)

    # 3) Automatic melody selection by channel first, then old merged fallback.
    if base is None:
        melody_candidates = []
        for by_ch in per_track_channel_notes:
            for t in by_ch.values():
                if _is_melody_candidate(t):
                    melody_candidates.append(t)

        if melody_candidates:
            higher = [t for t in melody_candidates if sorted(n.midi for n in t)[len(t) // 2] >= 48]
            pool = higher if higher else melody_candidates
            base = max(pool, key=_track_score)
        else:
            merged = []
            for t in per_track_notes:
                merged.extend(t)
            base = merged

    mono = _monophonize(base)
    if len(mono) < 4:
        raise ValueError("MIDI sin notas suficientes para modo juego")

    min_midi = min(n.midi for n in mono)
    max_midi = max(n.midi for n in mono)
    duration = max(n.start_sec + n.dur_sec for n in mono)

    _ = fmt
    return ParsedSong(name=path.name, notes=mono, duration_sec=duration, min_midi=min_midi, max_midi=max_midi)


DEFAULT_GAME_SETTINGS: dict[str, Any] = {
    "difficultyPreset": "normal",
    "timingPerfectMs": 60.0,
    "timingGoodMs": 120.0,
    "timingOkMs": 190.0,
    "pitchPerfectCents": 20.0,
    "pitchGoodCents": 45.0,
    "pitchOkCents": 80.0,
    "scrollLookAheadSec": 3.5,
    "guideVolume": 0.18,
    "playbackRate": 1.0,
    "comboStepPercent": 0.04,
    "comboMaxPercent": 1.50,
    "lidPlayableMinLevel": 0.00,
    "gameMinHz": 659.25,
    "gameMaxHz": 3135.96,
}

GAME_DIFFICULTY_PRESETS: dict[str, dict[str, float]] = {
    "easy": {
        "timingPerfectMs": 90.0,
        "timingGoodMs": 170.0,
        "timingOkMs": 260.0,
        "pitchPerfectCents": 30.0,
        "pitchGoodCents": 65.0,
        "pitchOkCents": 110.0,
        "scrollLookAheadSec": 4.4,
        "playbackRate": 0.75,
    },
    "normal": {
        "timingPerfectMs": 60.0,
        "timingGoodMs": 120.0,
        "timingOkMs": 190.0,
        "pitchPerfectCents": 20.0,
        "pitchGoodCents": 45.0,
        "pitchOkCents": 80.0,
        "scrollLookAheadSec": 3.5,
        "playbackRate": 1.0,
    },
    "hard": {
        "timingPerfectMs": 45.0,
        "timingGoodMs": 90.0,
        "timingOkMs": 150.0,
        "pitchPerfectCents": 14.0,
        "pitchGoodCents": 30.0,
        "pitchOkCents": 55.0,
        "scrollLookAheadSec": 2.8,
        "playbackRate": 1.10,
    },
}


class GameSettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.settings = copy.deepcopy(DEFAULT_GAME_SETTINGS)
        self._load()

    def _sanitize(self, raw: dict[str, Any]) -> dict[str, Any]:
        out = copy.deepcopy(DEFAULT_GAME_SETTINGS)
        out.update(raw or {})
        preset = str(out.get("difficultyPreset", "normal")).strip().lower()
        if preset in GAME_DIFFICULTY_PRESETS:
            out.update(GAME_DIFFICULTY_PRESETS[preset])
            out["difficultyPreset"] = preset
        else:
            out["difficultyPreset"] = "custom"
        out["timingPerfectMs"] = clamp(float(out.get("timingPerfectMs", 60.0)), 20.0, 180.0)
        out["timingGoodMs"] = clamp(float(out.get("timingGoodMs", 120.0)), out["timingPerfectMs"], 300.0)
        out["timingOkMs"] = clamp(float(out.get("timingOkMs", 190.0)), out["timingGoodMs"], 500.0)
        out["pitchPerfectCents"] = clamp(float(out.get("pitchPerfectCents", 20.0)), 5.0, 80.0)
        out["pitchGoodCents"] = clamp(
            float(out.get("pitchGoodCents", 45.0)), out["pitchPerfectCents"], 160.0
        )
        out["pitchOkCents"] = clamp(float(out.get("pitchOkCents", 80.0)), out["pitchGoodCents"], 300.0)
        out["scrollLookAheadSec"] = clamp(float(out.get("scrollLookAheadSec", 3.5)), 1.4, 8.0)
        out["guideVolume"] = clamp(float(out.get("guideVolume", 0.18)), 0.0, 1.0)
        out["playbackRate"] = clamp(float(out.get("playbackRate", 1.0)), 0.55, 1.40)
        out["comboStepPercent"] = clamp(float(out.get("comboStepPercent", 0.04)), 0.0, 0.50)
        out["comboMaxPercent"] = clamp(
            float(out.get("comboMaxPercent", 1.50)), out["comboStepPercent"], 4.00
        )
        out["lidPlayableMinLevel"] = clamp(float(out.get("lidPlayableMinLevel", 0.00)), 0.0, 0.70)
        out["gameMinHz"] = clamp(float(out.get("gameMinHz", 659.25)), 50.0, 4000.0)
        out["gameMaxHz"] = clamp(float(out.get("gameMaxHz", 3135.96)), out["gameMinHz"] + 20.0, 12000.0)
        return out

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            LOGGER.warning("No se pudo leer game settings %s: %s", self.path, exc)
            return
        if isinstance(raw, dict):
            self.settings = self._sanitize(raw)

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.settings, indent=2, sort_keys=True), encoding="utf-8")
        except Exception as exc:
            LOGGER.warning("No se pudo guardar game settings %s: %s", self.path, exc)

    def get(self) -> dict[str, Any]:
        with self.lock:
            return copy.deepcopy(self.settings)

    def patch(self, patch: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            merged = copy.deepcopy(self.settings)
            for k, v in (patch or {}).items():
                merged[k] = v
            self.settings = self._sanitize(merged)
            out = copy.deepcopy(self.settings)
            self._save()
            return out


class GameEngine:
    def __init__(
        self,
        *,
        state: core.SharedState,
        stop_event: threading.Event,
        midis_dir: Path,
        midis_catalog_file: Path,
        settings_store: GameSettingsStore,
    ) -> None:
        self.state = state
        self.stop_event = stop_event
        self.midis_dir = midis_dir
        self.midis_catalog_file = midis_catalog_file
        self.settings_store = settings_store

        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None

        self.available_midis: list[str] = []
        self._midi_meta_cache: dict[str, dict[str, Any]] = {}
        self._midis_catalog_sig: tuple[int, int] | None = None
        self._midis_catalog_cache: dict[str, dict[str, Any]] = {}
        self.song: ParsedSong | None = None
        self.selected_midi: str | None = None
        self.play_window_min_midi: int | None = None
        self.play_window_max_midi: int | None = None
        self.play_window_min_hz: float | None = None
        self.play_window_max_hz: float | None = None

        self.playing = False
        self.completed = False
        self.song_start_mono = 0.0
        self.song_start_epoch_ms = 0
        self.song_time_sec = 0.0
        self.start_token = 0

        self.next_idx = 0
        self.best_hits: dict[int, tuple[str, float, float, int]] = {}

        self.score = 0
        self.combo = 0
        self.max_combo = 0
        self.counts = {"perfect": 0, "good": 0, "ok": 0, "miss": 0}
        self.last_judgement = ""
        self.last_points = 0
        self.last_multiplier = 1.0

        self.player_freq_hz = 0.0
        self.player_note = "--"

    def start(self) -> None:
        self.refresh_midis()
        self._autoload_song_if_needed()
        self.thread = threading.Thread(target=self._run, daemon=True, name="game-engine")
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=2.0)

    def refresh_midis(self) -> list[str]:
        self.midis_dir.mkdir(parents=True, exist_ok=True)
        names: list[str] = []
        for p in sorted(self.midis_dir.iterdir()):
            if not p.is_file():
                continue
            if p.suffix.lower() not in (".mid", ".midi"):
                continue
            names.append(p.name)
        with self.lock:
            self.available_midis = names
            self._midi_meta_cache = {k: v for k, v in self._midi_meta_cache.items() if k in names}
            if self.selected_midi and self.selected_midi not in names:
                self.selected_midi = None
                self.song = None
        return names

    def _load_midis_catalog(self) -> dict[str, dict[str, Any]]:
        p = self.midis_catalog_file
        if not p.exists() or not p.is_file():
            with self.lock:
                self._midis_catalog_sig = None
                self._midis_catalog_cache = {}
            return {}

        try:
            st = p.stat()
            sig = (int(st.st_mtime_ns), int(st.st_size))
        except Exception as exc:
            LOGGER.warning("No se pudo stat catalogo MIDI %s: %s", p, exc)
            return {}

        with self.lock:
            if self._midis_catalog_sig == sig:
                return copy.deepcopy(self._midis_catalog_cache)

        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            LOGGER.warning("No se pudo leer catalogo MIDI %s: %s", p, exc)
            return {}

        entries: list[dict[str, Any]] = []
        if isinstance(raw, list):
            entries = [x for x in raw if isinstance(x, dict)]
        elif isinstance(raw, dict):
            bucket = raw.get("midis")
            if isinstance(bucket, list):
                entries = [x for x in bucket if isinstance(x, dict)]
            else:
                # Alternate shape: {"bubble.mid": {...}, "greenberet.mid": {...}}
                for k, v in raw.items():
                    if not isinstance(v, dict):
                        continue
                    merged = dict(v)
                    merged.setdefault("file", str(k))
                    entries.append(merged)

        allowed_scalar = (
            "displayName",
            "title",
            "game",
            "platform",
            "year",
            "composer",
            "arranger",
            "source",
            "description",
            "difficultyHint",
            "bpm",
            "transposeSemitones",
            "trimStartSec",
            "melodyChannel",
            "melodyTrackIndex",
        )
        merged: dict[str, dict[str, Any]] = {}

        for e in entries:
            file_name = Path(str(e.get("file") or e.get("name") or "")).name.strip()
            if not file_name:
                continue
            clean: dict[str, Any] = {}
            for k in allowed_scalar:
                if k in e:
                    v = e.get(k)
                    if isinstance(v, (str, int, float)):
                        clean[k] = v
            tags = e.get("tags")
            if isinstance(tags, list):
                clean_tags = [str(t).strip() for t in tags if str(t).strip()]
                if clean_tags:
                    clean["tags"] = clean_tags[:16]
            low_name = file_name.lower()
            low_stem = Path(file_name).stem.lower()
            merged[low_name] = clean
            if low_stem and low_stem not in merged:
                merged[low_stem] = clean

        with self.lock:
            self._midis_catalog_sig = sig
            self._midis_catalog_cache = copy.deepcopy(merged)
            return copy.deepcopy(self._midis_catalog_cache)

    def catalog_meta_for(self, name: str) -> dict[str, Any]:
        catalog = self._load_midis_catalog()
        key = Path(str(name)).name.lower()
        stem = Path(str(name)).stem.lower()
        meta = catalog.get(key) or catalog.get(stem) or {}
        return copy.deepcopy(meta)

    def _catalog_melody_select(self, name: str) -> tuple[int | None, int | None]:
        meta = self.catalog_meta_for(name)

        track_idx: int | None = None
        raw_track = meta.get("melodyTrackIndex", None)
        try:
            if raw_track is not None:
                parsed_track = int(raw_track)
                if parsed_track >= 0:
                    track_idx = parsed_track
        except (TypeError, ValueError):
            track_idx = None

        channel = _normalize_melody_channel(meta.get("melodyChannel", None))
        return track_idx, channel

    def _parse_song_file(self, path: Path, name_for_catalog: str) -> ParsedSong:
        track_idx, channel = self._catalog_melody_select(name_for_catalog)
        return parse_midi_file(path, melody_track_index=track_idx, melody_channel=channel)

    def _read_midi_meta(self, name: str) -> dict[str, Any]:
        path = (self.midis_dir / name).resolve()
        if not path.exists() or not path.is_file():
            return {
                "durationSec": None,
                "noteCount": None,
                "minMidi": None,
                "maxMidi": None,
                "parseError": "file not found",
            }

        st = path.stat()
        cache_key = {
            "mtimeNs": int(st.st_mtime_ns),
            "size": int(st.st_size),
        }
        with self.lock:
            cached = self._midi_meta_cache.get(name)
        if cached and cached.get("mtimeNs") == cache_key["mtimeNs"] and cached.get("size") == cache_key["size"]:
            return dict(cached.get("meta", {}))

        try:
            song = self._parse_song_file(path, name)
            song = self._transpose_song_for_range(song)
            play_lo, play_hi = self._song_play_window_midis(song)
            meta = {
                "durationSec": round(song.duration_sec, 1),
                "noteCount": len(song.notes),
                "minMidi": play_lo,
                "maxMidi": play_hi,
                "rawMinMidi": song.min_midi,
                "rawMaxMidi": song.max_midi,
                "minHz": midi_to_freq(play_lo),
                "maxHz": midi_to_freq(play_hi),
                "parseError": None,
            }
        except Exception as exc:
            LOGGER.warning("No se pudo parsear MIDI %s: %s", name, exc)
            meta = {
                "durationSec": None,
                "noteCount": None,
                "minMidi": None,
                "maxMidi": None,
                "rawMinMidi": None,
                "rawMaxMidi": None,
                "minHz": None,
                "maxHz": None,
                "parseError": str(exc),
            }

        with self.lock:
            self._midi_meta_cache[name] = {
                "mtimeNs": cache_key["mtimeNs"],
                "size": cache_key["size"],
                "meta": dict(meta),
            }
        return meta

    def get_midis_meta(self) -> list[dict]:
        """Return enriched metadata for each available MIDI file."""
        self.refresh_midis()
        catalog = self._load_midis_catalog()
        result = []
        with self.lock:
            selected = self.selected_midi
            song = self.song
            names = list(self.available_midis)

        for name in names:
            path = (self.midis_dir / name).resolve()
            size_kb = round(path.stat().st_size / 1024, 1) if path.exists() else 0
            meta = self._read_midi_meta(name)
            note_count = meta.get("noteCount")
            duration_sec = meta.get("durationSec")
            min_midi = meta.get("minMidi")
            max_midi = meta.get("maxMidi")
            raw_min_midi = meta.get("rawMinMidi")
            raw_max_midi = meta.get("rawMaxMidi")
            min_hz = meta.get("minHz")
            max_hz = meta.get("maxHz")
            parse_error = meta.get("parseError")
            if selected == name and song is not None:
                note_count = len(song.notes)
                duration_sec = round(song.duration_sec, 1)
                with self.lock:
                    win_lo = self.play_window_min_midi
                    win_hi = self.play_window_max_midi
                    win_min_hz = self.play_window_min_hz
                    win_max_hz = self.play_window_max_hz
                min_midi = int(win_lo) if win_lo is not None else song.min_midi
                max_midi = int(win_hi) if win_hi is not None else song.max_midi
                raw_min_midi = song.min_midi
                raw_max_midi = song.max_midi
                min_hz = float(win_min_hz) if win_min_hz is not None else midi_to_freq(min_midi)
                max_hz = float(win_max_hz) if win_max_hz is not None else midi_to_freq(max_midi)
                parse_error = None
            manual = catalog.get(name.lower()) or catalog.get(Path(name).stem.lower()) or {}
            row = {
                "name": name,
                "sizeKb": size_kb,
                "noteCount": note_count,
                "durationSec": duration_sec,
                "minMidi": min_midi,
                "maxMidi": max_midi,
                "rawMinMidi": raw_min_midi,
                "rawMaxMidi": raw_max_midi,
                "minHz": min_hz,
                "maxHz": max_hz,
                "parseError": parse_error,
                "selected": name == selected,
                "catalog": manual,
            }
            row.update(manual)
            result.append(row)
        return result

    def _autoload_song_if_needed(self) -> None:
        names = self.refresh_midis()
        if not names:
            return
        with self.lock:
            if self.song is not None:
                return
            selected = names[0]
        try:
            self.load_song(selected)
        except Exception as exc:
            LOGGER.error("No se pudo cargar MIDI inicial %s: %s", selected, exc)

    def _transpose_song_for_range(self, song: ParsedSong) -> ParsedSong:
        meta = self.catalog_meta_for(song.name)
        transpose_raw = meta.get("transposeSemitones", 0)
        trim_raw = meta.get("trimStartSec", 0.0)
        try:
            transpose = int(round(float(transpose_raw)))
        except (TypeError, ValueError):
            transpose = 0
        try:
            trim_start = max(0.0, float(trim_raw))
        except (TypeError, ValueError):
            trim_start = 0.0

        if transpose == 0 and trim_start <= 0.0:
            return song

        transformed: list[MidiNote] = []
        for n in song.notes:
            start = n.start_sec - trim_start
            if start < 0.0:
                continue
            midi = int(clamp(n.midi + transpose, 24, 108))
            transformed.append(
                MidiNote(
                    start_sec=start,
                    dur_sec=n.dur_sec,
                    midi=midi,
                    freq_hz=midi_to_freq(midi),
                    name=note_name(midi),
                )
            )

        if not transformed:
            LOGGER.warning(
                "Catalog transform removed all notes for %s (transpose=%s, trimStartSec=%.2f). Keeping original song.",
                song.name,
                transpose,
                trim_start,
            )
            return song

        transformed.sort(key=lambda x: x.start_sec)
        min_midi = min(n.midi for n in transformed)
        max_midi = max(n.midi for n in transformed)
        duration = max(n.start_sec + n.dur_sec for n in transformed)
        LOGGER.info(
            "Applied catalog transform for %s: transpose=%s, trimStartSec=%.2f, range=%s..%s",
            song.name,
            transpose,
            trim_start,
            min_midi,
            max_midi,
        )
        return ParsedSong(
            name=song.name,
            notes=transformed,
            duration_sec=duration,
            min_midi=min_midi,
            max_midi=max_midi,
        )

    def _song_play_window_midis(self, song: ParsedSong) -> tuple[int, int]:
        if not song.notes:
            return (48, 84)
        # Keep gameplay range directly correlated with song note frequencies.
        lo = max(24, int(song.min_midi))
        hi = min(108, int(song.max_midi))
        if hi <= lo:
            hi = min(108, lo + 1)
        return (int(lo), int(hi))

    def _apply_song_pitch_window(self, song: ParsedSong) -> None:
        if not song.notes:
            return
        low_midi, high_midi = self._song_play_window_midis(song)
        min_hz = midi_to_freq(low_midi)
        max_hz = midi_to_freq(high_midi)
        try:
            self.state.apply_patch(
                {
                    "synth": {
                        "minHz": min_hz,
                        "maxHz": max_hz,
                        "anchorAt90Enabled": False,
                    }
                },
                persist=False,
            )
            with self.lock:
                self.play_window_min_midi = int(low_midi)
                self.play_window_max_midi = int(high_midi)
                self.play_window_min_hz = float(min_hz)
                self.play_window_max_hz = float(max_hz)
            LOGGER.info(
                "Game synth window from MIDI (effective): %s [%s-%s] -> %.2fHz..%.2fHz",
                song.name,
                low_midi,
                high_midi,
                min_hz,
                max_hz,
            )
        except Exception as exc:
            LOGGER.warning("No se pudo aplicar ventana de pitch de juego: %s", exc)

    def load_song(self, name: str) -> ParsedSong:
        safe = Path(str(name)).name
        path = (self.midis_dir / safe).resolve()
        root = self.midis_dir.resolve()
        if root not in path.parents and path != root:
            raise ValueError("ruta MIDI invalida")
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"MIDI no encontrado: {safe}")

        song = self._parse_song_file(path, safe)
        song = self._transpose_song_for_range(song)
        self._apply_song_pitch_window(song)

        with self.lock:
            self.song = song
            self.selected_midi = safe
            self._reset_progress_locked(keep_song=True)
        LOGGER.info("MIDI cargado: %s (%s notas)", safe, len(song.notes))
        return song

    def start_game(self) -> tuple[bool, str, int]:
        with self.lock:
            if self.song is None:
                return False, "No hay MIDI cargado", 0
            self._reset_progress_locked(keep_song=True)
            delay = 1.0
            self.song_start_mono = time.monotonic() + delay
            self.song_start_epoch_ms = int(round(time.time() * 1000.0 + delay * 1000.0))
            self.playing = True
            self.completed = False
            self.start_token += 1
            token = self.start_token
        return True, "Juego iniciado", token

    def stop_game(self) -> None:
        with self.lock:
            self.playing = False

    def reset_game(self) -> None:
        with self.lock:
            self._reset_progress_locked(keep_song=True)

    def _reset_progress_locked(self, *, keep_song: bool) -> None:
        if not keep_song:
            self.song = None
            self.selected_midi = None
            self.play_window_min_midi = None
            self.play_window_max_midi = None
            self.play_window_min_hz = None
            self.play_window_max_hz = None
        self.playing = False
        self.completed = False
        self.song_time_sec = 0.0
        self.song_start_mono = 0.0
        self.song_start_epoch_ms = 0
        self.next_idx = 0
        self.best_hits.clear()
        self.score = 0
        self.combo = 0
        self.max_combo = 0
        self.counts = {"perfect": 0, "good": 0, "ok": 0, "miss": 0}
        self.last_judgement = ""
        self.last_points = 0
        self.last_multiplier = 1.0

    def patch_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        return self.settings_store.patch(patch)

    def _judge(self, timing_ms: float, pitch_cents: float, settings: dict[str, Any]) -> tuple[str, int] | None:
        if timing_ms <= settings["timingPerfectMs"] and pitch_cents <= settings["pitchPerfectCents"]:
            return ("perfect", 300)
        if timing_ms <= settings["timingGoodMs"] and pitch_cents <= settings["pitchGoodCents"]:
            return ("good", 180)
        if timing_ms <= settings["timingOkMs"] and pitch_cents <= settings["pitchOkCents"]:
            return ("ok", 90)
        return None

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self._tick()
            except Exception as exc:
                LOGGER.exception("Game tick error: %s", exc)
                time.sleep(0.03)
            time.sleep(0.010)

    def _tick(self) -> None:
        snap = self.state.snapshot()
        cfg = snap["config"]
        sensor = snap["sensor"]
        sensor_cfg = cfg["sensor"]
        synth_cfg = cfg["synth"]

        stale = bool(sensor.get("stale", False))
        lid_level = 0.0 if stale else float(sensor.get("lidLevel", 0.0))

        settings = self.settings_store.get()
        playable_min = clamp(float(settings.get("lidPlayableMinLevel", 0.0)), 0.0, 0.90)
        if playable_min > 0.0:
            # Remap playable lid range to keep notes in comfortable open angles.
            lid_level = clamp((lid_level - playable_min) / max(1e-6, 1.0 - playable_min), 0.0, 1.0)
        pitch_level = core.map_pitch_level_with_90_anchor(
            lid_level,
            sensor_cfg=sensor_cfg,
            synth_cfg=synth_cfg,
        )
        min_hz = float(synth_cfg.get("minHz", 130.81))
        max_hz = float(synth_cfg.get("maxHz", 1760.0))
        ratio = max(1.0001, max_hz / max(1e-6, min_hz))
        player_hz = min_hz * math.pow(ratio, clamp(pitch_level, 0.0, 1.0))
        player_note = note_name(round(69 + 12 * math.log2(max(1e-6, player_hz) / 440.0)))

        with self.lock:
            self.player_freq_hz = player_hz
            self.player_note = player_note

            song = self.song
            if song is None or not self.playing:
                return

            now = time.monotonic()
            playback_rate = clamp(float(settings.get("playbackRate", 1.0)), 0.55, 1.40)
            song_t = (now - self.song_start_mono) * playback_rate
            self.song_time_sec = song_t

            ok_win_s = float(settings["timingOkMs"]) / 1000.0
            if song_t < -ok_win_s:
                return

            notes = song.notes
            upper_scan = min(len(notes), self.next_idx + 120)
            for idx in range(self.next_idx, upper_scan):
                note = notes[idx]
                dt = song_t - note.start_sec
                if dt < -ok_win_s:
                    break
                if dt > ok_win_s:
                    continue

                timing_ms = abs(dt) * 1000.0
                pitch_c = abs(cents_error(player_hz, note.freq_hz))
                judged = self._judge(timing_ms, pitch_c, settings)
                if judged is None:
                    continue
                j_name, j_score = judged
                rank = {"perfect": 0, "good": 1, "ok": 2}[j_name]
                prev = self.best_hits.get(idx)
                if prev is None:
                    self.best_hits[idx] = (j_name, timing_ms, pitch_c, j_score)
                    continue
                prev_rank = {"perfect": 0, "good": 1, "ok": 2}[prev[0]]
                if rank < prev_rank or (rank == prev_rank and (timing_ms + pitch_c) < (prev[1] + prev[2])):
                    self.best_hits[idx] = (j_name, timing_ms, pitch_c, j_score)

            while self.next_idx < len(notes):
                note = notes[self.next_idx]
                if song_t <= (note.start_sec + ok_win_s):
                    break
                best = self.best_hits.pop(self.next_idx, None)
                if best is None:
                    self.counts["miss"] += 1
                    self.combo = 0
                    self.last_judgement = "miss"
                    self.last_points = 0
                    self.last_multiplier = 1.0
                else:
                    j_name, _t, _p, j_score = best
                    self.counts[j_name] += 1
                    self.combo += 1
                    self.max_combo = max(self.max_combo, self.combo)
                    combo_idx = max(0, self.combo - 1)
                    bonus_pct = min(
                        float(settings.get("comboMaxPercent", 1.50)),
                        combo_idx * float(settings.get("comboStepPercent", 0.04)),
                    )
                    multiplier = 1.0 + bonus_pct
                    points = int(round(float(j_score) * multiplier))
                    self.score += points
                    self.last_judgement = j_name
                    self.last_points = points
                    self.last_multiplier = multiplier
                self.next_idx += 1

            if self.next_idx >= len(notes) and song_t > (song.duration_sec + ok_win_s + 0.30):
                self.playing = False
                self.completed = True

    def _current_target_locked(self) -> dict[str, Any] | None:
        if self.song is None:
            return None
        song_t = self.song_time_sec
        notes = self.song.notes
        if not notes:
            return None
        i0 = max(0, self.next_idx)
        i1 = min(len(notes), i0 + 24)
        best_idx = None
        best_abs_dt = float("inf")
        for i in range(i0, i1):
            dt = notes[i].start_sec - song_t
            adt = abs(dt)
            if adt < best_abs_dt:
                best_abs_dt = adt
                best_idx = i
        if best_idx is None:
            return None
        n = notes[best_idx]
        p_c = abs(cents_error(self.player_freq_hz, n.freq_hz))
        return {
            "index": best_idx,
            "startSec": n.start_sec,
            "freqHz": n.freq_hz,
            "midi": n.midi,
            "note": n.name,
            "timeToHitSec": n.start_sec - song_t,
            "pitchErrorCents": p_c,
        }

    def song_payload(self) -> dict[str, Any]:
        with self.lock:
            if self.song is None:
                return {"loaded": False, "name": None, "notes": []}
            min_midi = (
                int(self.play_window_min_midi)
                if self.play_window_min_midi is not None
                else int(self.song.min_midi)
            )
            max_midi = (
                int(self.play_window_max_midi)
                if self.play_window_max_midi is not None
                else int(self.song.max_midi)
            )
            return {
                "loaded": True,
                "name": self.song.name,
                "durationSec": self.song.duration_sec,
                "minMidi": min_midi,
                "maxMidi": max_midi,
                "rawMinMidi": self.song.min_midi,
                "rawMaxMidi": self.song.max_midi,
                "minHz": midi_to_freq(min_midi),
                "maxHz": midi_to_freq(max_midi),
                "notes": [
                    {
                        "startSec": n.start_sec,
                        "durSec": n.dur_sec,
                        "midi": n.midi,
                        "freqHz": n.freq_hz,
                        "note": n.name,
                    }
                    for n in self.song.notes
                ],
            }

    def snapshot(self, base_snapshot: dict[str, Any]) -> dict[str, Any]:
        settings = self.settings_store.get()
        with self.lock:
            song = self.song
            lookahead = float(settings["scrollLookAheadSec"])
            song_t = self.song_time_sec

            upcoming: list[dict[str, Any]] = []
            if song is not None:
                start_idx = max(0, self.next_idx - 6)
                end_idx = min(len(song.notes), self.next_idx + 140)
                for i in range(start_idx, end_idx):
                    n = song.notes[i]
                    dt = n.start_sec - song_t
                    if dt < -0.45:
                        continue
                    if dt > (lookahead + 0.45):
                        break
                    upcoming.append(
                        {
                            "index": i,
                            "startSec": n.start_sec,
                            "durSec": n.dur_sec,
                            "midi": n.midi,
                            "freqHz": n.freq_hz,
                            "note": n.name,
                            "timeToHitSec": dt,
                        }
                    )

            target = self._current_target_locked()

            return {
                "sensor": base_snapshot["sensor"],
                "config": base_snapshot["config"],
                "game": {
                    "availableMidis": list(self.available_midis),
                    "selectedMidi": self.selected_midi,
                    "loaded": song is not None,
                    "playing": self.playing,
                    "completed": self.completed,
                    "songTimeSec": song_t,
                    "songDurationSec": 0.0 if song is None else song.duration_sec,
                    "songStartEpochMs": self.song_start_epoch_ms,
                    "startToken": self.start_token,
                    "score": self.score,
                    "combo": self.combo,
                    "maxCombo": self.max_combo,
                    "counts": dict(self.counts),
                    "lastJudgement": self.last_judgement,
                    "lastPoints": self.last_points,
                    "lastMultiplier": self.last_multiplier,
                    "playerFreqHz": self.player_freq_hz,
                    "playerNote": self.player_note,
                    "currentTarget": target,
                    "upcoming": upcoming,
                    "songRange": None
                    if song is None
                    else {
                        "minMidi": int(self.play_window_min_midi)
                        if self.play_window_min_midi is not None
                        else int(song.min_midi),
                        "maxMidi": int(self.play_window_max_midi)
                        if self.play_window_max_midi is not None
                        else int(song.max_midi),
                        "rawMinMidi": int(song.min_midi),
                        "rawMaxMidi": int(song.max_midi),
                        "minHz": float(self.play_window_min_hz)
                        if self.play_window_min_hz is not None
                        else midi_to_freq(int(song.min_midi)),
                        "maxHz": float(self.play_window_max_hz)
                        if self.play_window_max_hz is not None
                        else midi_to_freq(int(song.max_midi)),
                    },
                    "difficultyPresets": ["easy", "normal", "hard", "custom"],
                    "settings": settings,
                },
            }


class GameRequestHandler(BaseHTTPRequestHandler):
    state: core.SharedState | None = None
    engine: GameEngine | None = None
    static_dir: Path | None = None

    def _read_json_body(self) -> dict[str, Any] | None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except json.JSONDecodeError:
            self._write_json(400, {"error": "invalid json"})
            return None
        if not isinstance(payload, dict):
            self._write_json(400, {"error": "payload must be an object"})
            return None
        return payload

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/state":
            assert self.state is not None
            assert self.engine is not None
            base = self.state.snapshot()
            payload = self.engine.snapshot(base)
            self._write_json(200, payload)
            return

        if path == "/api/song":
            assert self.engine is not None
            self._write_json(200, self.engine.song_payload())
            return

        if path == "/api/config":
            assert self.state is not None
            self._write_json(200, self.state.config_copy())
            return

        if path == "/api/game/midis":
            assert self.engine is not None
            midis = self.engine.get_midis_meta()
            self._write_json(200, {"midis": midis})
            return

        if path == "/api/game/preview":
            assert self.engine is not None
            from urllib.parse import parse_qs
            qs = parse_qs(urlparse(self.path).query)
            name = str((qs.get("name") or [""])[0]).strip()
            if not name:
                self._write_json(400, {"error": "name required"})
                return
            safe = Path(name).name
            midi_path = (self.engine.midis_dir / safe).resolve()
            root = self.engine.midis_dir.resolve()
            if root not in midi_path.parents and midi_path != root:
                self._write_json(403, {"error": "forbidden"})
                return
            if not midi_path.exists():
                self._write_json(404, {"error": "not found"})
                return
            try:
                song = self.engine._parse_song_file(midi_path, safe)
                song = self.engine._transpose_song_for_range(song)
                play_lo, play_hi = self.engine._song_play_window_midis(song)
            except Exception as exc:
                self._write_json(400, {"error": str(exc)})
                return
            # Return first 20s of notes for preview playback
            preview_notes = [
                {"startSec": n.start_sec, "durSec": n.dur_sec, "freqHz": n.freq_hz, "note": n.name}
                for n in song.notes
                if n.start_sec < 20.0
            ]
            catalog_meta = self.engine.catalog_meta_for(safe)
            self._write_json(200, {
                "name": song.name,
                "displayName": str(catalog_meta.get("displayName") or catalog_meta.get("title") or song.name),
                "durationSec": round(song.duration_sec, 1),
                "noteCount": len(song.notes),
                "minMidi": play_lo,
                "maxMidi": play_hi,
                "rawMinMidi": song.min_midi,
                "rawMaxMidi": song.max_midi,
                "minHz": midi_to_freq(play_lo),
                "maxHz": midi_to_freq(play_hi),
                "catalog": catalog_meta,
                "previewNotes": preview_notes,
            })
            return

        self._serve_static(path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/config":
            payload = self._read_json_body()
            if payload is None:
                return
            assert self.state is not None
            cfg = self.state.apply_patch(payload)
            self._write_json(200, {"ok": True, "config": cfg})
            return

        if path == "/api/runtime-config":
            payload = self._read_json_body()
            if payload is None:
                return
            assert self.state is not None
            cfg = self.state.apply_patch(payload, persist=False)
            self._write_json(200, {"ok": True, "config": cfg})
            return

        if path == "/api/game/settings":
            payload = self._read_json_body()
            if payload is None:
                return
            assert self.engine is not None
            settings = self.engine.patch_settings(payload)
            self._write_json(200, {"ok": True, "settings": settings})
            return

        if path == "/api/game/load":
            payload = self._read_json_body()
            if payload is None:
                return
            assert self.engine is not None
            name = str(payload.get("name", "")).strip()
            if not name:
                self._write_json(400, {"error": "name is required"})
                return
            try:
                song = self.engine.load_song(name)
            except Exception as exc:
                self._write_json(400, {"error": str(exc)})
                return
            self._write_json(
                200,
                {
                    "ok": True,
                    "song": {
                        "name": song.name,
                        "notes": len(song.notes),
                        "durationSec": song.duration_sec,
                        "range": [
                            int(self.engine.play_window_min_midi)
                            if self.engine.play_window_min_midi is not None
                            else int(song.min_midi),
                            int(self.engine.play_window_max_midi)
                            if self.engine.play_window_max_midi is not None
                            else int(song.max_midi),
                        ],
                    },
                },
            )
            return

        if path == "/api/game/start":
            assert self.engine is not None
            ok, msg, token = self.engine.start_game()
            self._write_json(200 if ok else 400, {"ok": ok, "message": msg, "startToken": token})
            return

        if path == "/api/game/stop":
            assert self.engine is not None
            self.engine.stop_game()
            self._write_json(200, {"ok": True})
            return

        if path == "/api/game/reset":
            assert self.engine is not None
            self.engine.reset_game()
            self._write_json(200, {"ok": True})
            return

        self._write_json(404, {"error": "not found"})

    def _serve_static(self, raw_path: str) -> None:
        assert self.static_dir is not None
        p = raw_path
        if p == "/":
            p = "/index.html"

        rel = p.lstrip("/")
        target = (self.static_dir / rel).resolve()
        root = self.static_dir.resolve()
        if root not in target.parents and target != root:
            self._write_json(403, {"error": "forbidden"})
            return
        if not target.exists() or not target.is_file():
            self._write_json(404, {"error": "not found"})
            return

        data = target.read_bytes()
        mime, _ = mimetypes.guess_type(str(target))
        ctype = mime or "application/octet-stream"

        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        _ = fmt
        _ = args
        return


class GameHTTPServer(core.AppHTTPServer):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Therescreen Game Mode")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--sample-rate", type=float, default=48_000.0)
    parser.add_argument("--block-size", type=int, default=96)

    parser.add_argument("--config-file", default="therescreen_config.json")
    parser.add_argument("--game-settings-file", default="therescreen_game_settings.json")
    parser.add_argument("--midis-dir", default="ui_game/midis")
    parser.add_argument("--midis-catalog-file", default="ui_game/midis/catalog.json")
    parser.add_argument("--ui-dir", default="ui_game")

    parser.add_argument("--log-file", default="therescreen_game.log")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))

    parser.add_argument("--lid-command", default="lid-angle")
    parser.add_argument("--ambient-command", default="ambient-light")
    parser.add_argument("--speaker-command", default="speaker")
    parser.add_argument("--keyboard-set-command", default="keyboard-brightness --set={value}")
    parser.add_argument("--screen-set-command", default="screen-brightness --set={value}")

    parser.add_argument("--no-audio", action="store_true")
    parser.add_argument("--no-brightness", action="store_true")
    parser.add_argument(
        "--enable-brightness-mapping",
        action="store_true",
        help="Enable keyboard/screen brightness mapping in game mode (disabled by default).",
    )
    parser.add_argument("--no-ambient", action="store_true")

    args = parser.parse_args()
    if args.sample_rate <= 0:
        raise SystemExit("--sample-rate must be > 0")
    if args.block_size <= 0:
        raise SystemExit("--block-size must be > 0")
    if not (1 <= args.port <= 65535):
        raise SystemExit("--port must be in 1..65535")
    return args


def _resolve_under_root(root: Path, p: str) -> Path:
    candidate = Path(p)
    if not candidate.is_absolute():
        candidate = (root / candidate).resolve()
    return candidate


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parent

    log_file = _resolve_under_root(root, args.log_file)
    core.setup_logging(log_file=log_file, level_name=args.log_level)

    try:
        import numpy as _np

        core.np = _np
    except ModuleNotFoundError:
        if not args.no_audio:
            LOGGER.error("Falta numpy. Instala con `pip install numpy`")
            return 1
        core.np = None

    config_file = _resolve_under_root(root, args.config_file)
    game_settings_file = _resolve_under_root(root, args.game_settings_file)
    midis_dir = _resolve_under_root(root, args.midis_dir)
    midis_catalog_file = _resolve_under_root(root, args.midis_catalog_file)
    ui_dir = _resolve_under_root(root, args.ui_dir)

    if not ui_dir.exists():
        LOGGER.error("UI game directory not found: %s", ui_dir)
        return 1

    state = core.SharedState(config_file=config_file)
    state.replace_commands_from_args(args)
    if args.no_ambient:
        state.apply_patch({"sensor": {"useAmbient": False}})
    # Game mode default: do not map lid movement to hardware brightness.
    if args.no_brightness or (not args.enable_brightness_mapping):
        state.apply_patch(
            {
                "brightness": {
                    "enabled": False,
                    "keyboardEnabled": False,
                    "screenEnabled": False,
                }
            },
            persist=False,
        )
    # Game mode: keep playable range without forcing near-closed lid.
    current_sensor = state.config_copy().get("sensor", {})
    angle_max = float(current_sensor.get("angleMax", 125.0))
    # Prefer playing between ~45 deg and fully open.
    game_angle_min = 45.0 if angle_max >= 55.0 else max(0.0, angle_max - 8.0)
    state.apply_patch({"sensor": {"angleMin": game_angle_min}}, persist=False)

    stop_event = threading.Event()

    def _stop(_sig: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    sensor_bridge = core.SensorBridge(state=state, stop_event=stop_event)
    sensor_bridge.start()

    audio_engine: core.AudioEngine | None = None
    if not args.no_audio:
        audio_engine = core.AudioEngine(
            state=state,
            stop_event=stop_event,
            sample_rate=args.sample_rate,
            block_size=args.block_size,
        )
        audio_engine.start()

    brightness_engine: core.BrightnessEngine | None = None
    if (not args.no_brightness) and args.enable_brightness_mapping:
        brightness_engine = core.BrightnessEngine(state=state, stop_event=stop_event)
        brightness_engine.start()

    game_settings = GameSettingsStore(path=game_settings_file)
    # Runtime migration to comfortable-open-lid gameplay defaults.
    cur_settings = game_settings.get()
    migrate_patch: dict[str, Any] = {}
    if abs(float(cur_settings.get("gameMinHz", 523.25)) - 523.25) < 0.01:
        migrate_patch["gameMinHz"] = 659.25
    if abs(float(cur_settings.get("gameMaxHz", 2093.0)) - 2093.0) < 0.1:
        migrate_patch["gameMaxHz"] = 3135.96
    if float(cur_settings.get("lidPlayableMinLevel", 0.0)) >= 0.18:
        migrate_patch["lidPlayableMinLevel"] = 0.0
    if migrate_patch:
        game_settings.patch(migrate_patch)
    engine = GameEngine(
        state=state,
        stop_event=stop_event,
        midis_dir=midis_dir,
        midis_catalog_file=midis_catalog_file,
        settings_store=game_settings,
    )
    engine.start()

    GameRequestHandler.state = state
    GameRequestHandler.engine = engine
    GameRequestHandler.static_dir = ui_dir

    server: GameHTTPServer | None = None
    try:
        server = GameHTTPServer((args.host, args.port), GameRequestHandler)
    except OSError as exc:
        if exc.errno in (errno.EADDRINUSE, 48, 98):
            LOGGER.warning("Port %s ocupado; intentando limpiar proceso viejo", args.port)
            cleaned = core.cleanup_existing_therescreen_on_port(args.port)
            if cleaned:
                try:
                    server = GameHTTPServer((args.host, args.port), GameRequestHandler)
                except OSError as retry_exc:
                    LOGGER.error("Port %s sigue ocupado: %s", args.port, retry_exc)
                    return 1
            else:
                LOGGER.error(
                    "Port %s already in use. Run with another port, e.g. `sudo python3 therescreen_game.py --port %s`",
                    args.port,
                    args.port + 1,
                )
                return 1
        else:
            raise

    if server is None:
        LOGGER.error("Internal error: game server not initialized")
        return 1

    server.timeout = 0.5

    LOGGER.info("Therescreen Game Mode")
    LOGGER.info("UI: http://%s:%s", args.host, args.port)
    LOGGER.info("MIDI dir: %s", midis_dir)
    LOGGER.info("Stop (misma terminal): Ctrl+C")

    try:
        while not stop_event.is_set():
            server.handle_request()
    except Exception as exc:
        LOGGER.exception("Fatal game server loop error: %s", exc)
        return 1
    finally:
        stop_event.set()
        try:
            server.server_close()
        except Exception as exc:
            LOGGER.warning("Error closing game server: %s", exc)
        sensor_bridge.stop()
        engine.stop()
        if audio_engine is not None:
            audio_engine.stop()
        if brightness_engine is not None:
            brightness_engine.stop()

    LOGGER.info("Therescreen Game Mode stopped cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
