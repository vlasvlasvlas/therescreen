#!/usr/bin/env python3
"""Therescreen: lid-angle theremin + screen/keyboard reactive control + web UI."""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import copy
import errno
import json
import logging
import math
import mimetypes
import os
import shlex
import signal
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

np = None
LOGGER = logging.getLogger("therescreen")


def setup_logging(log_file: Path, level_name: str) -> None:
    level = getattr(logging, str(level_name).upper(), logging.INFO)
    LOGGER.setLevel(level)
    LOGGER.handlers.clear()
    LOGGER.propagate = False

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(threadName)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)
    LOGGER.addHandler(stream_handler)

    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=2_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        LOGGER.addHandler(file_handler)
        LOGGER.info("Logging to %s (level=%s)", log_file, logging.getLevelName(level))
    except Exception as exc:
        LOGGER.warning("Could not open log file %s: %s", log_file, exc)


def _find_listener_pids(port: int) -> list[int]:
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{int(port)}", "-sTCP:LISTEN", "-t"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=2.0,
            check=False,
        )
    except FileNotFoundError:
        LOGGER.warning("`lsof` not found; cannot auto-clean busy port %s", port)
        return []
    except Exception as exc:
        LOGGER.warning("Could not inspect busy port %s: %s", port, exc)
        return []

    pids: list[int] = []
    for line in (result.stdout or "").splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            pids.append(int(raw))
        except ValueError:
            continue
    return sorted(set(pids))


def _process_command(pid: int) -> str:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=2.0,
            check=False,
        )
    except Exception:
        return ""
    return (result.stdout or "").strip()


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_pid(pid: int, timeout_s: float = 2.0) -> bool:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    except Exception:
        return False

    deadline = time.time() + max(0.2, float(timeout_s))
    while time.time() < deadline:
        if not _pid_is_alive(pid):
            return True
        time.sleep(0.05)

    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    except Exception:
        return False

    time.sleep(0.08)
    return not _pid_is_alive(pid)


def cleanup_existing_therescreen_on_port(port: int) -> bool:
    pids = _find_listener_pids(port)
    if not pids:
        return False

    cleaned = False
    this_pid = os.getpid()

    for pid in pids:
        if pid == this_pid:
            continue
        cmd = _process_command(pid)
        if "therescreen.py" not in cmd:
            LOGGER.error(
                "Port %s is in use by PID %s (%s). Not terminating automatically.",
                port,
                pid,
                cmd or "unknown process",
            )
            continue

        LOGGER.warning("Terminating stale therescreen process PID %s on port %s", pid, port)
        if _terminate_pid(pid):
            LOGGER.info("Terminated PID %s", pid)
            cleaned = True
        else:
            LOGGER.error("Could not terminate PID %s. Try manually with sudo.", pid)

    return cleaned

DEFAULT_CONFIG: dict[str, Any] = {
    "sensor": {
        "angleMin": 0.0,
        "angleMax": 125.0,
        "gateOpenDeg": 5.0,
        "timeoutSec": 0.35,
        "useAmbient": True,
    },
    "visual": {
        "colorA": "#2bd0ff",
        "colorB": "#ff6a00",
        "invertColor": False,
        "invertBrightness": False,
        "showPitchInfo": False,
        "minOpacity": 0.08,
        "maxOpacity": 1.0,
    },
    "synth": {
        "waveform": "sine",
        "minHz": 130.81,
        "maxHz": 1760.0,
        "lowVolume": 0.03,
        "highVolume": 0.66,
        "attackMs": 8.0,
        "releaseMs": 120.0,
        "glideMs": 24.0,
        "vibratoRateHz": 6.1,
        "vibratoDepthCents": 8.0,
        "vibratoAmbientCents": 16.0,
        "cutoffHz": 4200.0,
        "cutoffFollow": 0.30,
        "delayMs": 360.0,
        "delayFeedback": 0.22,
        "delayMix": 0.14,
        "reverbMix": 0.30,
        "masterGain": 0.90,
    },
    "brightness": {
        "enabled": True,
        "updateHz": 12.0,
        "keyboardEnabled": True,
        "keyboardInvert": False,
        "keyboardMin": 0,
        "keyboardMax": 100,
        "screenEnabled": True,
        "screenInvert": False,
        "screenMin": 0,
        "screenMax": 100,
    },
    "commands": {
        "lid": "lid-angle",
        "ambient": "ambient-light",
        "speaker": "speaker --block-size 128",
        "keyboardSet": "keyboard-brightness --set={value}",
        "screenSet": "screen-brightness --set={value}",
    },
    "synthPreset": "classic_theremin",
}

WAVEFORMS = {"sine", "triangle", "saw", "square"}
SYNTH_PARAM_ORDER = (
    "waveform",
    "minHz",
    "maxHz",
    "lowVolume",
    "highVolume",
    "attackMs",
    "releaseMs",
    "glideMs",
    "vibratoRateHz",
    "vibratoDepthCents",
    "vibratoAmbientCents",
    "cutoffHz",
    "cutoffFollow",
    "delayMs",
    "delayFeedback",
    "delayMix",
    "reverbMix",
    "masterGain",
)
DEFAULT_SYNTH_PRESETS: dict[str, dict[str, Any]] = {
    "classic_theremin": {
        "waveform": "sine",
        "minHz": 130.81,
        "maxHz": 1760.0,
        "lowVolume": 0.03,
        "highVolume": 0.66,
        "attackMs": 8.0,
        "releaseMs": 120.0,
        "glideMs": 24.0,
        "vibratoRateHz": 6.1,
        "vibratoDepthCents": 8.0,
        "vibratoAmbientCents": 16.0,
        "cutoffHz": 4200.0,
        "cutoffFollow": 0.30,
        "delayMs": 360.0,
        "delayFeedback": 0.22,
        "delayMix": 0.14,
        "reverbMix": 0.30,
        "masterGain": 0.90,
    },
    "space_voice": {
        "waveform": "triangle",
        "minHz": 98.0,
        "maxHz": 1480.0,
        "lowVolume": 0.02,
        "highVolume": 0.72,
        "attackMs": 12.0,
        "releaseMs": 220.0,
        "glideMs": 38.0,
        "vibratoRateHz": 5.4,
        "vibratoDepthCents": 15.0,
        "vibratoAmbientCents": 12.0,
        "cutoffHz": 2900.0,
        "cutoffFollow": 0.45,
        "delayMs": 440.0,
        "delayFeedback": 0.32,
        "delayMix": 0.18,
        "reverbMix": 0.38,
        "masterGain": 0.82,
    },
    "glass_lead": {
        "waveform": "saw",
        "minHz": 164.81,
        "maxHz": 2637.0,
        "lowVolume": 0.01,
        "highVolume": 0.58,
        "attackMs": 4.0,
        "releaseMs": 90.0,
        "glideMs": 16.0,
        "vibratoRateHz": 6.8,
        "vibratoDepthCents": 6.0,
        "vibratoAmbientCents": 10.0,
        "cutoffHz": 5200.0,
        "cutoffFollow": 0.25,
        "delayMs": 220.0,
        "delayFeedback": 0.18,
        "delayMix": 0.10,
        "reverbMix": 0.22,
        "masterGain": 0.78,
    },
}


@dataclass
class SensorSnapshot:
    lid_level: float
    lid_angle_deg: float
    ambient_level: float
    last_lid_time: float


class SharedState:
    def __init__(self, config_file: Path) -> None:
        self.config_file = config_file
        self.lock = threading.Lock()
        self.config = copy.deepcopy(DEFAULT_CONFIG)

        self.sensor = SensorSnapshot(
            lid_level=0.0,
            lid_angle_deg=float(self.config["sensor"]["angleMin"]),
            ambient_level=0.5,
            last_lid_time=0.0,
        )
        self.started_at = time.time()

        self._load_config_file()

    def _load_config_file(self) -> None:
        if not self.config_file.exists():
            return
        try:
            data = json.loads(self.config_file.read_text(encoding="utf-8"))
        except Exception as exc:
            LOGGER.warning("Could not read config file %s: %s", self.config_file, exc)
            return
        if isinstance(data, dict):
            with self.lock:
                merged = deep_merge(copy.deepcopy(self.config), data)
                self.config = sanitize_config(merged)

    def _save_config_file(self) -> None:
        try:
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            self.config_file.write_text(
                json.dumps(self.config, indent=2, sort_keys=True), encoding="utf-8"
            )
        except Exception as exc:
            LOGGER.warning("Could not save config file %s: %s", self.config_file, exc)

    def apply_patch(self, patch: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            merged = deep_merge(copy.deepcopy(self.config), patch)
            self.config = sanitize_config(merged)
            config_copy = copy.deepcopy(self.config)
        self._save_config_file()
        return config_copy

    def replace_commands_from_args(self, args: argparse.Namespace) -> None:
        patch = {
            "commands": {
                "lid": args.lid_command,
                "ambient": args.ambient_command,
                "speaker": args.speaker_command,
                "keyboardSet": args.keyboard_set_command,
                "screenSet": args.screen_set_command,
            }
        }
        self.apply_patch(patch)

    def set_lid(self, angle_deg: float, level: float) -> None:
        now = time.time()
        with self.lock:
            angle = clamp(angle_deg, 0.0, 180.0)
            self.sensor = SensorSnapshot(
                lid_level=clamp01(level),
                lid_angle_deg=angle,
                ambient_level=self.sensor.ambient_level,
                last_lid_time=now,
            )

    def set_ambient(self, ambient_level: float) -> None:
        with self.lock:
            self.sensor = SensorSnapshot(
                lid_level=self.sensor.lid_level,
                lid_angle_deg=self.sensor.lid_angle_deg,
                ambient_level=clamp01(ambient_level),
                last_lid_time=self.sensor.last_lid_time,
            )

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            cfg = copy.deepcopy(self.config)
            sensor = self.sensor
        now = time.time()
        stale = sensor.last_lid_time <= 0.0 or (now - sensor.last_lid_time) > float(
            cfg["sensor"]["timeoutSec"]
        )
        computed_level = 0.0 if stale else lid_level_from_angle(sensor.lid_angle_deg, cfg["sensor"])
        return {
            "ts": now,
            "uptimeSec": max(0.0, now - self.started_at),
            "sensor": {
                "lidLevel": computed_level,
                "lidLevelRaw": sensor.lid_level,
                "lidAngleDeg": sensor.lid_angle_deg,
                "ambientLevel": sensor.ambient_level,
                "lastLidTime": sensor.last_lid_time,
                "stale": stale,
            },
            "config": cfg,
        }

    def config_copy(self) -> dict[str, Any]:
        with self.lock:
            return copy.deepcopy(self.config)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def clamp01(value: float) -> float:
    return clamp(float(value), 0.0, 1.0)


def normalize(value: float, low: float, high: float) -> float:
    lo = float(low)
    hi = float(high)
    if hi <= lo:
        return 0.0
    return clamp01((float(value) - lo) / (hi - lo))


def lid_level_from_angle(angle_deg: float, sensor_cfg: dict[str, Any]) -> float:
    return normalize(float(angle_deg), float(sensor_cfg["angleMin"]), float(sensor_cfg["angleMax"]))


def alpha_from_ms(sample_rate: float, time_ms: float) -> float:
    t = max(1e-4, float(time_ms) / 1000.0)
    return 1.0 - math.exp(-1.0 / (max(1.0, sample_rate) * t))


def smooth_curve(previous: float, target: float, alpha: float, frames: int) -> tuple[Any, float]:
    n = max(1, int(frames))
    a = clamp(alpha, 0.0, 1.0)
    if a <= 0.0:
        arr = np.full(n, float(previous), dtype=np.float64)
        return arr, float(previous)
    if a >= 1.0:
        arr = np.full(n, float(target), dtype=np.float64)
        return arr, float(target)
    decay = np.power(1.0 - a, np.arange(1, n + 1, dtype=np.float64))
    out = float(target) + (float(previous) - float(target)) * decay
    return out, float(out[-1])


def parse_color_hex(value: Any, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    text = value.strip()
    if len(text) == 7 and text.startswith("#"):
        body = text[1:]
        try:
            int(body, 16)
            return text.lower()
        except ValueError:
            return fallback
    return fallback


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def sanitize_synth_config(raw_synth: dict[str, Any] | None) -> dict[str, Any]:
    source = dict(raw_synth or {})
    synth = copy.deepcopy(DEFAULT_CONFIG["synth"])
    synth.update(source)

    synth["waveform"] = str(synth.get("waveform", "sine")).lower()
    if synth["waveform"] not in WAVEFORMS:
        synth["waveform"] = "sine"
    synth["minHz"] = clamp(float(synth.get("minHz", 130.81)), 20.0, 4000.0)
    synth["maxHz"] = clamp(float(synth.get("maxHz", 1760.0)), synth["minHz"] + 10.0, 12000.0)
    synth["lowVolume"] = clamp(float(synth.get("lowVolume", 0.04)), 0.0, 1.0)
    synth["highVolume"] = clamp(float(synth.get("highVolume", 0.62)), synth["lowVolume"], 1.0)
    synth["attackMs"] = clamp(float(synth.get("attackMs", 80.0)), 0.0, 2000.0)
    synth["releaseMs"] = clamp(float(synth.get("releaseMs", 300.0)), 10.0, 5000.0)
    synth["glideMs"] = clamp(float(synth.get("glideMs", 220.0)), 0.0, 5000.0)
    synth["vibratoRateHz"] = clamp(float(synth.get("vibratoRateHz", 6.1)), 0.0, 20.0)
    synth["vibratoDepthCents"] = clamp(float(synth.get("vibratoDepthCents", 8.0)), 0.0, 200.0)
    synth["vibratoAmbientCents"] = clamp(float(synth.get("vibratoAmbientCents", 16.0)), 0.0, 200.0)
    synth["cutoffHz"] = clamp(float(synth.get("cutoffHz", 4200.0)), 50.0, 18000.0)
    synth["cutoffFollow"] = clamp(float(synth.get("cutoffFollow", 0.30)), 0.0, 1.0)
    synth["delayMs"] = clamp(float(synth.get("delayMs", 360.0)), 1.0, 2000.0)
    synth["delayFeedback"] = clamp(float(synth.get("delayFeedback", 0.22)), 0.0, 0.95)
    synth["delayMix"] = clamp(float(synth.get("delayMix", 0.14)), 0.0, 1.0)
    synth["reverbMix"] = clamp(float(synth.get("reverbMix", 0.30)), 0.0, 1.0)
    synth["masterGain"] = clamp(float(synth.get("masterGain", 0.90)), 0.0, 2.0)
    return synth


def _yaml_parse_scalar(raw: str) -> Any:
    text = raw.strip()
    if not text:
        return ""
    low = text.lower()
    if low in ("true", "false"):
        return low == "true"
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        return text[1:-1]
    try:
        if "." in text or "e" in low:
            return float(text)
        return int(text)
    except ValueError:
        return text


def parse_synth_presets_yaml(text: str) -> dict[str, dict[str, Any]]:
    presets: dict[str, dict[str, Any]] = {}
    current: str | None = None
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent == 0:
            if not stripped.endswith(":"):
                raise ValueError(f"line {lineno}: expected `preset_name:`")
            current = stripped[:-1].strip()
            if not current:
                raise ValueError(f"line {lineno}: empty preset name")
            presets[current] = {}
            continue
        if indent != 2 or current is None:
            raise ValueError(f"line {lineno}: invalid indentation")
        if ":" not in stripped:
            raise ValueError(f"line {lineno}: expected `key: value`")
        key, val = stripped.split(":", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"line {lineno}: empty key")
        presets[current][key] = _yaml_parse_scalar(val)
    return presets


def _yaml_dump_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if any(ch in text for ch in ("#", ":", '"', "'")) or text.strip() != text:
        escaped = text.replace('"', '\\"')
        return f'"{escaped}"'
    return text


def dump_synth_presets_yaml(presets: dict[str, dict[str, Any]]) -> str:
    lines: list[str] = [
        "# Therescreen synth presets",
        "# One preset per top-level key",
        "",
    ]
    for name, synth in presets.items():
        lines.append(f"{name}:")
        clean = sanitize_synth_config(synth)
        for key in SYNTH_PARAM_ORDER:
            lines.append(f"  {key}: {_yaml_dump_scalar(clean[key])}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def normalize_preset_name(name: str) -> str:
    text = "".join(ch if (ch.isalnum() or ch in "_-") else "_" for ch in str(name).strip().lower())
    text = text.strip("_")
    return text or "preset"


class SynthPresetStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.presets: dict[str, dict[str, Any]] = {}

    def load(self) -> None:
        with self.lock:
            self._load_unlocked()

    def _load_unlocked(self) -> None:
        if not self.path.exists():
            self.presets = {
                name: sanitize_synth_config(values) for name, values in DEFAULT_SYNTH_PRESETS.items()
            }
            self._save_unlocked()
            LOGGER.info("Created default synth presets file at %s", self.path)
            return
        try:
            raw = self.path.read_text(encoding="utf-8")
            loaded = parse_synth_presets_yaml(raw)
        except Exception as exc:
            LOGGER.error("Failed to parse presets YAML %s: %s", self.path, exc)
            loaded = {}
        if not loaded:
            loaded = copy.deepcopy(DEFAULT_SYNTH_PRESETS)
        self.presets = {name: sanitize_synth_config(values) for name, values in loaded.items()}
        LOGGER.info("Loaded %s synth presets from %s", len(self.presets), self.path)

    def _save_unlocked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = dump_synth_presets_yaml(self.presets)
        self.path.write_text(body, encoding="utf-8")

    def save(self) -> None:
        with self.lock:
            self._save_unlocked()

    def list_names(self) -> list[str]:
        with self.lock:
            return sorted(self.presets.keys())

    def to_payload(self, selected: str | None) -> dict[str, Any]:
        with self.lock:
            names = sorted(self.presets.keys())
            return {
                "presets": names,
                "selected": selected if selected in self.presets else (names[0] if names else None),
                "path": str(self.path),
            }

    def get(self, name: str) -> dict[str, Any] | None:
        key = str(name).strip()
        with self.lock:
            preset = self.presets.get(key)
            return copy.deepcopy(preset) if preset else None

    def upsert(self, name: str, synth: dict[str, Any]) -> str:
        key = normalize_preset_name(name)
        with self.lock:
            self.presets[key] = sanitize_synth_config(synth)
            self._save_unlocked()
        LOGGER.info("Saved synth preset `%s` to %s", key, self.path)
        return key

def sanitize_config(cfg: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(cfg)

    sensor = out.get("sensor", {})
    sensor["angleMin"] = float(sensor.get("angleMin", 0.0))
    sensor["angleMax"] = float(sensor.get("angleMax", 125.0))
    if sensor["angleMax"] <= sensor["angleMin"]:
        sensor["angleMax"] = sensor["angleMin"] + 1.0
    sensor["gateOpenDeg"] = clamp(float(sensor.get("gateOpenDeg", 5.0)), sensor["angleMin"], sensor["angleMax"])
    sensor["timeoutSec"] = clamp(float(sensor.get("timeoutSec", 1.0)), 0.1, 10.0)
    sensor["useAmbient"] = bool(sensor.get("useAmbient", True))
    out["sensor"] = sensor

    visual = out.get("visual", {})
    visual["colorA"] = parse_color_hex(visual.get("colorA"), "#2bd0ff")
    visual["colorB"] = parse_color_hex(visual.get("colorB"), "#ff6a00")
    visual["invertColor"] = bool(visual.get("invertColor", False))
    visual["invertBrightness"] = bool(visual.get("invertBrightness", False))
    visual["showPitchInfo"] = bool(visual.get("showPitchInfo", False))
    visual["minOpacity"] = clamp(float(visual.get("minOpacity", 0.08)), 0.0, 1.0)
    visual["maxOpacity"] = clamp(float(visual.get("maxOpacity", 1.0)), visual["minOpacity"], 1.0)
    out["visual"] = visual

    out["synth"] = sanitize_synth_config(out.get("synth"))
    out["synthPreset"] = normalize_preset_name(str(out.get("synthPreset", "classic_theremin")))

    brightness = out.get("brightness", {})
    brightness["enabled"] = bool(brightness.get("enabled", True))
    brightness["updateHz"] = clamp(float(brightness.get("updateHz", 4.0)), 0.5, 30.0)
    brightness["keyboardEnabled"] = bool(brightness.get("keyboardEnabled", True))
    brightness["keyboardInvert"] = bool(brightness.get("keyboardInvert", False))
    brightness["screenEnabled"] = bool(brightness.get("screenEnabled", True))
    brightness["screenInvert"] = bool(brightness.get("screenInvert", False))
    brightness["keyboardMin"] = int(clamp(float(brightness.get("keyboardMin", 0)), 0, 100))
    brightness["keyboardMax"] = int(clamp(float(brightness.get("keyboardMax", 100)), brightness["keyboardMin"], 100))
    brightness["screenMin"] = int(clamp(float(brightness.get("screenMin", 0)), 0, 100))
    brightness["screenMax"] = int(clamp(float(brightness.get("screenMax", 100)), brightness["screenMin"], 100))
    out["brightness"] = brightness

    commands = out.get("commands", {})
    commands["lid"] = str(commands.get("lid", "lid-angle")).strip() or "lid-angle"
    commands["ambient"] = str(commands.get("ambient", "ambient-light")).strip() or "ambient-light"
    commands["speaker"] = str(commands.get("speaker", "speaker")).strip() or "speaker"
    commands["keyboardSet"] = str(commands.get("keyboardSet", "keyboard-brightness --set={value}"))
    commands["screenSet"] = str(commands.get("screenSet", "screen-brightness --set={value}"))
    out["commands"] = commands

    return out


class SPUReportStream:
    """Low-latency raw HID report stream for AppleSPUHID sensors."""

    def __init__(
        self,
        *,
        usage_page: int,
        usage: int,
        report_buffer_size: int = 4096,
        report_interval_us: int = 1000,
    ) -> None:
        self.usage_page = int(usage_page)
        self.usage = int(usage)
        self.report_buffer_size = max(64, int(report_buffer_size))
        self.report_interval_us = max(1, int(report_interval_us))
        self._reports: list[tuple[float, bytes]] = []
        self._lock = threading.Lock()
        self._hid = None

        iokit_name = ctypes.util.find_library("IOKit")
        cf_name = ctypes.util.find_library("CoreFoundation")
        if not iokit_name or not cf_name:
            raise RuntimeError("unable to locate IOKit/CoreFoundation")

        self._iokit = ctypes.cdll.LoadLibrary(iokit_name)
        self._cf = ctypes.cdll.LoadLibrary(cf_name)

        self._k_cf_allocator_default = ctypes.c_void_p.in_dll(self._cf, "kCFAllocatorDefault")
        self._k_cf_run_loop_default_mode = ctypes.c_void_p.in_dll(
            self._cf, "kCFRunLoopDefaultMode"
        )
        self._setup_ffi()
        self._wake_drivers()
        self._open_matching_device()

    def _setup_ffi(self) -> None:
        self._iokit.IOServiceMatching.restype = ctypes.c_void_p
        self._iokit.IOServiceMatching.argtypes = [ctypes.c_char_p]
        self._iokit.IOServiceGetMatchingServices.restype = ctypes.c_int
        self._iokit.IOServiceGetMatchingServices.argtypes = [
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint),
        ]
        self._iokit.IOIteratorNext.restype = ctypes.c_uint
        self._iokit.IOIteratorNext.argtypes = [ctypes.c_uint]
        self._iokit.IOObjectRelease.argtypes = [ctypes.c_uint]
        self._iokit.IORegistryEntryCreateCFProperty.restype = ctypes.c_void_p
        self._iokit.IORegistryEntryCreateCFProperty.argtypes = [
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint,
        ]
        self._iokit.IORegistryEntrySetCFProperty.restype = ctypes.c_int
        self._iokit.IORegistryEntrySetCFProperty.argtypes = [
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        self._iokit.IOHIDDeviceCreate.restype = ctypes.c_void_p
        self._iokit.IOHIDDeviceCreate.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        self._iokit.IOHIDDeviceOpen.restype = ctypes.c_int
        self._iokit.IOHIDDeviceOpen.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._iokit.IOHIDDeviceClose.restype = ctypes.c_int
        self._iokit.IOHIDDeviceClose.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._iokit.IOHIDDeviceRegisterInputReportCallback.restype = None
        self._iokit.IOHIDDeviceRegisterInputReportCallback.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        self._iokit.IOHIDDeviceScheduleWithRunLoop.restype = None
        self._iokit.IOHIDDeviceScheduleWithRunLoop.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]

        self._cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        self._cf.CFStringCreateWithCString.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_uint32,
        ]
        self._cf.CFNumberCreate.restype = ctypes.c_void_p
        self._cf.CFNumberCreate.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        self._cf.CFNumberGetValue.restype = ctypes.c_bool
        self._cf.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        self._cf.CFRunLoopGetCurrent.restype = ctypes.c_void_p
        self._cf.CFRunLoopRunInMode.restype = ctypes.c_int32
        self._cf.CFRunLoopRunInMode.argtypes = [
            ctypes.c_void_p,
            ctypes.c_double,
            ctypes.c_bool,
        ]

    def _cfstr(self, text: str) -> ctypes.c_void_p:
        return self._cf.CFStringCreateWithCString(None, text.encode("utf-8"), 0x08000100)

    def _cfnum32(self, value: int) -> ctypes.c_void_p:
        val = ctypes.c_int32(int(value))
        return self._cf.CFNumberCreate(None, 3, ctypes.byref(val))

    def _prop_int(self, service: int, key: str) -> int | None:
        ref = self._iokit.IORegistryEntryCreateCFProperty(service, self._cfstr(key), None, 0)
        if not ref:
            return None
        val = ctypes.c_long()
        if not self._cf.CFNumberGetValue(ref, 4, ctypes.byref(val)):
            return None
        return int(val.value)

    def _wake_drivers(self) -> None:
        matching = self._iokit.IOServiceMatching(b"AppleSPUHIDDriver")
        iterator = ctypes.c_uint()
        self._iokit.IOServiceGetMatchingServices(0, matching, ctypes.byref(iterator))
        while True:
            service = self._iokit.IOIteratorNext(iterator.value)
            if not service:
                break
            for key, value in (
                ("SensorPropertyReportingState", 1),
                ("SensorPropertyPowerState", 1),
                ("ReportInterval", self.report_interval_us),
            ):
                _ = self._iokit.IORegistryEntrySetCFProperty(
                    service,
                    self._cfstr(key),
                    self._cfnum32(value),
                )
            self._iokit.IOObjectRelease(service)

    def _open_matching_device(self) -> None:
        matching = self._iokit.IOServiceMatching(b"AppleSPUHIDDevice")
        iterator = ctypes.c_uint()
        self._iokit.IOServiceGetMatchingServices(0, matching, ctypes.byref(iterator))

        chosen_hid = None
        while True:
            service = self._iokit.IOIteratorNext(iterator.value)
            if not service:
                break
            page = self._prop_int(service, "PrimaryUsagePage") or -1
            use = self._prop_int(service, "PrimaryUsage") or -1
            if page == self.usage_page and use == self.usage:
                hid = self._iokit.IOHIDDeviceCreate(self._k_cf_allocator_default, service)
                if hid and self._iokit.IOHIDDeviceOpen(hid, 0) == 0:
                    chosen_hid = hid
                    self._iokit.IOObjectRelease(service)
                    break
            self._iokit.IOObjectRelease(service)

        if chosen_hid is None:
            raise RuntimeError(
                f"SPU sensor usage page 0x{self.usage_page:04X} usage 0x{self.usage:04X} not found"
            )

        self._hid = chosen_hid
        self._report_buffer = (ctypes.c_uint8 * self.report_buffer_size)()
        report_cb_type = ctypes.CFUNCTYPE(
            None,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_long,
        )

        def _on_report(
            _ctx: object,
            _result: int,
            _sender: object,
            _rtype: int,
            _rid: int,
            report: ctypes.POINTER(ctypes.c_uint8),
            length: int,
        ) -> None:
            n = int(length)
            if n <= 0:
                return
            with self._lock:
                self._reports.append((time.time(), bytes(report[:n])))

        self._cb_ref = report_cb_type(_on_report)
        self._iokit.IOHIDDeviceRegisterInputReportCallback(
            self._hid,
            self._report_buffer,
            self.report_buffer_size,
            self._cb_ref,
            None,
        )
        self._iokit.IOHIDDeviceScheduleWithRunLoop(
            self._hid,
            self._cf.CFRunLoopGetCurrent(),
            self._k_cf_run_loop_default_mode,
        )

    def poll(self, timeout_s: float) -> None:
        self._cf.CFRunLoopRunInMode(
            self._k_cf_run_loop_default_mode,
            max(0.0, float(timeout_s)),
            False,
        )

    def pop_reports(self) -> list[tuple[float, bytes]]:
        with self._lock:
            if not self._reports:
                return []
            out = self._reports[:]
            self._reports.clear()
            return out

    def close(self) -> None:
        if self._hid is not None:
            try:
                _ = self._iokit.IOHIDDeviceClose(self._hid, 0)
            except Exception:
                pass
            self._hid = None


class SensorBridge:
    def __init__(self, state: SharedState, stop_event: threading.Event) -> None:
        self.state = state
        self.stop_event = stop_event
        self.threads: list[threading.Thread] = []
        self.procs: list[subprocess.Popen[str]] = []

    def start(self) -> None:
        cfg = self.state.config_copy()
        self._spawn_direct_lid()

        if bool(cfg["sensor"]["useAmbient"]):
            self._spawn_direct_ambient()

    def _spawn_direct_lid(self) -> bool:
        def run() -> None:
            stream: SPUReportStream | None = None
            try:
                stream = SPUReportStream(usage_page=0x0020, usage=0x008A, report_interval_us=750)
            except Exception as exc:
                LOGGER.warning(
                    "Direct lid reader unavailable, fallback to command reader: %s", exc
                )
                cfg = self.state.config_copy()
                lid_cmd = shlex.split(cfg["commands"]["lid"]) + ["--json"]
                self._spawn_reader("lid-angle", lid_cmd, self._on_lid_payload)
                return

            try:
                while not self.stop_event.is_set():
                    stream.poll(0.005)
                    for _ts, report in stream.pop_reports():
                        if len(report) < 3 or int(report[0]) != 1:
                            continue
                        raw_angle = int(report[1]) | ((int(report[2]) & 0x01) << 8)
                        angle = float(raw_angle)
                        cfg = self.state.config_copy()
                        level = lid_level_from_angle(angle, cfg["sensor"])
                        self.state.set_lid(angle, level)
            except Exception as exc:
                if not self.stop_event.is_set():
                    LOGGER.exception("Direct lid reader stopped unexpectedly: %s", exc)
            finally:
                if stream is not None:
                    stream.close()

        thread = threading.Thread(target=run, daemon=True, name="sensor-lid-direct")
        self.threads.append(thread)
        thread.start()
        return True

    def _spawn_direct_ambient(self) -> bool:
        def run() -> None:
            stream: SPUReportStream | None = None
            try:
                stream = SPUReportStream(usage_page=0xFF00, usage=0x0004, report_interval_us=2000)
            except Exception as exc:
                LOGGER.warning(
                    "Direct ambient reader unavailable, fallback to command reader: %s", exc
                )
                cfg = self.state.config_copy()
                ambient_cmd = shlex.split(cfg["commands"]["ambient"]) + ["--json"]
                self._spawn_reader("ambient-light", ambient_cmd, self._on_ambient_payload)
                return

            try:
                while not self.stop_event.is_set():
                    stream.poll(0.02)
                    for _ts, report in stream.pop_reports():
                        if len(report) < 44:
                            continue
                        try:
                            raw = float(struct.unpack_from("<f", report, 40)[0])
                        except (struct.error, ValueError):
                            continue
                        if not math.isfinite(raw):
                            continue
                        self.state.set_ambient(clamp01(raw))
            except Exception as exc:
                if not self.stop_event.is_set():
                    LOGGER.exception("Direct ambient reader stopped unexpectedly: %s", exc)
            finally:
                if stream is not None:
                    stream.close()

        thread = threading.Thread(target=run, daemon=True, name="sensor-ambient-direct")
        self.threads.append(thread)
        thread.start()
        return True

    def _spawn_reader(self, name: str, cmd: list[str], on_payload: Any) -> None:
        def run() -> None:
            while not self.stop_event.is_set():
                try:
                    proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        stdin=subprocess.DEVNULL,
                        text=True,
                        bufsize=1,
                    )
                except FileNotFoundError:
                    LOGGER.error("Command not found for %s: %r", name, cmd[0])
                    self.stop_event.set()
                    return
                except Exception as exc:
                    LOGGER.exception("Failed to start %s reader: %s", name, exc)
                    time.sleep(1.0)
                    continue

                self.procs.append(proc)
                stderr_lines: list[str] = []
                try:
                    if proc.stdout is None:
                        raise RuntimeError(f"{name} stdout is unavailable")
                    for raw_line in proc.stdout:
                        if self.stop_event.is_set():
                            break
                        line = raw_line.strip()
                        if not line:
                            continue
                        try:
                            payload = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        on_payload(payload)
                finally:
                    if proc.poll() is None:
                        proc.terminate()
                    try:
                        _, stderr_text = proc.communicate(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        _, stderr_text = proc.communicate(timeout=1.0)
                    if stderr_text:
                        stderr_lines.extend([ln for ln in stderr_text.splitlines() if ln.strip()])

                if self.stop_event.is_set():
                    return
                err_msg = stderr_lines[-1] if stderr_lines else ""
                if err_msg:
                    LOGGER.warning("%s reader stopped: %s", name, err_msg)
                else:
                    LOGGER.warning("%s reader stopped, restarting...", name)
                time.sleep(1.0)

        thread = threading.Thread(target=run, daemon=True, name=f"sensor-{name}")
        self.threads.append(thread)
        thread.start()

    def _on_lid_payload(self, payload: dict[str, Any]) -> None:
        try:
            angle = float(payload.get("angle_deg", payload.get("angle_clamped_deg", 0.0)))
            level = float(payload.get("level", math.nan))
        except (TypeError, ValueError):
            return
        if not math.isfinite(level):
            cfg = self.state.config_copy()
            level = lid_level_from_angle(angle, cfg["sensor"])
        self.state.set_lid(angle, level)

    def _on_ambient_payload(self, payload: dict[str, Any]) -> None:
        try:
            ambient = float(payload.get("intensity", payload.get("level", 0.5)))
        except (TypeError, ValueError):
            return
        self.state.set_ambient(ambient)

    def stop(self) -> None:
        self.stop_event.set()
        for proc in self.procs:
            if proc.poll() is None:
                proc.terminate()
        for proc in self.procs:
            if proc.poll() is None:
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
        for thread in self.threads:
            thread.join(timeout=1.0)


class ThereminDSP:
    def __init__(self, sample_rate: float, block_size: int) -> None:
        self.sample_rate = float(sample_rate)
        self.block_size = int(block_size)

        self.freq = 220.0
        self.env = 0.0
        self.phase = 0.0
        self.lfo_phase = 0.0
        self.lp_state = 0.0

        self.delay = np.zeros(1, dtype=np.float32)
        self.delay_idx = 0

        self.reverb_buffers = [
            np.zeros(max(1, int(round(self.sample_rate * ms / 1000.0))), dtype=np.float32)
            for ms in (29.7, 37.1, 53.3)
        ]
        self.reverb_idxs = [0, 0, 0]
        self.reverb_feedback = (0.76, 0.72, 0.68)

    def _ensure_delay(self, delay_ms: float) -> None:
        samples = max(1, int(round(self.sample_rate * delay_ms / 1000.0)))
        if self.delay.shape[0] == samples:
            return
        self.delay = np.zeros(samples, dtype=np.float32)
        self.delay_idx = 0

    def _osc(self, phase: Any, waveform: str) -> Any:
        if waveform == "triangle":
            return (2.0 / math.pi) * np.arcsin(np.sin(phase))
        if waveform == "saw":
            turn = phase / (2.0 * math.pi)
            return 2.0 * (turn - np.floor(turn + 0.5))
        if waveform == "square":
            return np.where(np.sin(phase) >= 0.0, 1.0, -1.0)
        return np.sin(phase)

    def render(self, level: float, gate: float, ambient: float, synth_cfg: dict[str, Any]) -> Any:
        n = self.block_size
        level = clamp01(level)
        gate = clamp01(gate)
        ambient = clamp01(ambient)

        min_hz = float(synth_cfg["minHz"])
        max_hz = float(synth_cfg["maxHz"])
        ratio = max(1.0001, max_hz / min_hz)
        target_freq = min_hz * math.pow(ratio, level)

        glide_alpha = alpha_from_ms(self.sample_rate, float(synth_cfg["glideMs"]))
        freq_curve, self.freq = smooth_curve(self.freq, target_freq, glide_alpha, n)

        attack_alpha = alpha_from_ms(self.sample_rate, float(synth_cfg["attackMs"]))
        release_alpha = alpha_from_ms(self.sample_rate, float(synth_cfg["releaseMs"]))
        env_alpha = attack_alpha if gate > self.env else release_alpha
        env_curve, self.env = smooth_curve(self.env, gate, env_alpha, n)

        base_amp = float(synth_cfg["lowVolume"]) + (
            float(synth_cfg["highVolume"]) - float(synth_cfg["lowVolume"])
        ) * level
        amp_curve = env_curve * base_amp

        lfo_rate = float(synth_cfg["vibratoRateHz"])
        depth_cents = float(synth_cfg["vibratoDepthCents"]) + ambient * float(
            synth_cfg["vibratoAmbientCents"]
        )
        lfo_step = 2.0 * math.pi * lfo_rate / self.sample_rate
        lfo_phase_line = self.lfo_phase + lfo_step * np.arange(n, dtype=np.float64)
        vib = np.power(2.0, (depth_cents / 1200.0) * np.sin(lfo_phase_line))
        self.lfo_phase = float((self.lfo_phase + lfo_step * n) % (2.0 * math.pi))

        freq_mod = freq_curve * vib
        phase_step = (2.0 * math.pi) / self.sample_rate
        phase_inc = phase_step * freq_mod
        phase = self.phase + np.cumsum(phase_inc, dtype=np.float64)
        self.phase = float(phase[-1] % (2.0 * math.pi))

        waveform = str(synth_cfg["waveform"]).lower()
        osc = self._osc(phase, waveform)
        dry = (osc * amp_curve).astype(np.float32, copy=False)

        cutoff_base = float(synth_cfg["cutoffHz"])
        cutoff_follow = float(synth_cfg["cutoffFollow"])
        cutoff = cutoff_base + cutoff_follow * level * (self.sample_rate * 0.45 - cutoff_base)
        cutoff = clamp(cutoff, 50.0, self.sample_rate * 0.49)
        lp_alpha = 1.0 - math.exp(-2.0 * math.pi * cutoff / self.sample_rate)

        delay_ms = float(synth_cfg["delayMs"])
        delay_feedback = float(synth_cfg["delayFeedback"])
        delay_mix = float(synth_cfg["delayMix"])
        reverb_mix = float(synth_cfg["reverbMix"])
        dry_gain = max(0.0, 1.0 - delay_mix - reverb_mix)

        self._ensure_delay(delay_ms)
        out = np.empty(n, dtype=np.float32)

        for i, sample in enumerate(dry):
            self.lp_state += lp_alpha * (float(sample) - self.lp_state)
            filtered = self.lp_state

            d = float(self.delay[self.delay_idx])
            self.delay[self.delay_idx] = float(filtered + d * delay_feedback)
            self.delay_idx += 1
            if self.delay_idx >= self.delay.shape[0]:
                self.delay_idx = 0

            rv = 0.0
            for idx, buf in enumerate(self.reverb_buffers):
                r_i = self.reverb_idxs[idx]
                tap = float(buf[r_i])
                buf[r_i] = float(filtered + tap * self.reverb_feedback[idx])
                r_i += 1
                if r_i >= buf.shape[0]:
                    r_i = 0
                self.reverb_idxs[idx] = r_i
                rv += tap
            rv /= float(len(self.reverb_buffers))

            mixed = dry_gain * filtered + delay_mix * d + reverb_mix * rv
            out[i] = float(mixed)

        out *= float(synth_cfg["masterGain"])
        return np.clip(out, -1.0, 1.0).astype(np.float32, copy=False)


class AudioEngine:
    def __init__(self, state: SharedState, stop_event: threading.Event, sample_rate: float, block_size: int) -> None:
        self.state = state
        self.stop_event = stop_event
        self.sample_rate = float(sample_rate)
        self.block_size = int(block_size)
        self.thread: threading.Thread | None = None

        self.speaker_proc: subprocess.Popen[bytes] | None = None
        self.dsp: ThereminDSP | None = None
        self._gate_state = 0.0

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, daemon=True, name="audio-engine")
        self.thread.start()

    def _start_speaker(self, speaker_cmd: str) -> bool:
        cmd = shlex.split(speaker_cmd)
        if not cmd:
            LOGGER.error("Empty speaker command")
            return False
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except FileNotFoundError:
            LOGGER.error("Speaker command not found: %r", cmd[0])
            return False
        except Exception as exc:
            LOGGER.exception("Failed to start speaker process: %s", exc)
            return False

        if proc.stdin is None:
            LOGGER.error("Speaker process has no stdin")
            proc.kill()
            return False

        proc.stdin.write(f"MSIG1 {int(round(self.sample_rate))}\n".encode("ascii"))
        proc.stdin.flush()

        self.speaker_proc = proc
        return True

    def _close_speaker(self) -> None:
        if self.speaker_proc is None:
            return
        proc = self.speaker_proc
        self.speaker_proc = None
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except Exception as exc:
            LOGGER.debug("Error closing speaker stdin: %s", exc)
            pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                proc.kill()

    def _run(self) -> None:
        self.dsp = ThereminDSP(self.sample_rate, self.block_size)

        while not self.stop_event.is_set():
            snap = self.state.snapshot()
            cfg = snap["config"]
            sensor_cfg = cfg["sensor"]
            synth_cfg = cfg["synth"]
            sensor = snap["sensor"]

            now_ts = snap["ts"]
            stale = sensor["lastLidTime"] <= 0.0 or (now_ts - sensor["lastLidTime"]) > float(
                sensor_cfg["timeoutSec"]
            )
            level = 0.0 if stale else float(sensor["lidLevel"])
            angle = float(sensor_cfg["angleMin"]) if stale else float(sensor["lidAngleDeg"])
            gate_open = float(sensor_cfg["gateOpenDeg"])
            gate_close = max(float(sensor_cfg["angleMin"]), gate_open - 1.5)
            if self._gate_state >= 0.5:
                self._gate_state = 0.0 if angle <= gate_close else 1.0
            else:
                self._gate_state = 1.0 if angle >= gate_open else 0.0
            gate = self._gate_state
            ambient = 0.5
            if bool(sensor_cfg["useAmbient"]):
                ambient = float(sensor["ambientLevel"])

            block = self.dsp.render(level=level, gate=gate, ambient=ambient, synth_cfg=synth_cfg)

            if self.speaker_proc is None:
                if not self._start_speaker(str(cfg["commands"]["speaker"])):
                    time.sleep(0.25)
                    continue

            proc = self.speaker_proc
            if proc is None or proc.stdin is None:
                time.sleep(0.05)
                continue

            try:
                proc.stdin.write(block.tobytes(order="C"))
            except (BrokenPipeError, OSError):
                self._close_speaker()
                time.sleep(0.05)

        self._close_speaker()

    def stop(self) -> None:
        self.stop_event.set()
        self._close_speaker()
        if self.thread is not None:
            self.thread.join(timeout=2.0)


class BrightnessEngine:
    def __init__(self, state: SharedState, stop_event: threading.Event) -> None:
        self.state = state
        self.stop_event = stop_event
        self.thread: threading.Thread | None = None
        self.last_keyboard: int | None = None
        self.last_screen: int | None = None
        self.last_err_ts = 0.0

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, daemon=True, name="brightness-engine")
        self.thread.start()

    def _run_command(self, template: str, value: int, label: str) -> None:
        cmd_text = template.format(value=value)
        try:
            cmd = shlex.split(cmd_text)
        except ValueError as exc:
            self._log_err(f"bad {label} command template: {exc}")
            return

        if not cmd:
            self._log_err(f"empty {label} command")
            return

        try:
            result = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=4.0,
                check=False,
            )
        except FileNotFoundError:
            self._log_err(f"{label} command not found: {cmd[0]!r}")
            return
        except Exception as exc:
            self._log_err(f"{label} command failed: {exc}")
            return

        if result.returncode != 0:
            tail = (result.stderr or "").strip().splitlines()
            last = tail[-1] if tail else f"exit {result.returncode}"
            self._log_err(f"{label} command error: {last}")

    def _log_err(self, msg: str) -> None:
        now = time.time()
        if now - self.last_err_ts < 3.0:
            return
        self.last_err_ts = now
        LOGGER.error("%s", msg)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            snap = self.state.snapshot()
            cfg = snap["config"]
            sensor = snap["sensor"]
            sensor_cfg = cfg["sensor"]
            br = cfg["brightness"]

            hz = float(br["updateHz"])
            sleep_s = 1.0 / max(0.5, hz)

            if not bool(br["enabled"]):
                time.sleep(sleep_s)
                continue

            stale = sensor["lastLidTime"] <= 0.0 or (snap["ts"] - sensor["lastLidTime"]) > float(
                sensor_cfg["timeoutSec"]
            )
            lid = 0.0 if stale else clamp01(float(sensor["lidLevel"]))

            if bool(br["keyboardEnabled"]):
                k_level = 1.0 - lid if bool(br["keyboardInvert"]) else lid
                k_min = int(br["keyboardMin"])
                k_max = int(br["keyboardMax"])
                kval = int(round(k_min + (k_max - k_min) * k_level))
                if self.last_keyboard is None or abs(kval - self.last_keyboard) >= 1:
                    self.last_keyboard = kval
                    self._run_command(str(cfg["commands"]["keyboardSet"]), kval, "keyboard")

            if bool(br["screenEnabled"]):
                s_level = 1.0 - lid if bool(br["screenInvert"]) else lid
                s_min = int(br["screenMin"])
                s_max = int(br["screenMax"])
                sval = int(round(s_min + (s_max - s_min) * s_level))
                if self.last_screen is None or abs(sval - self.last_screen) >= 1:
                    self.last_screen = sval
                    self._run_command(str(cfg["commands"]["screenSet"]), sval, "screen")

            time.sleep(sleep_s)

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=2.0)


class AppRequestHandler(BaseHTTPRequestHandler):
    state: SharedState | None = None
    static_dir: Path | None = None
    preset_store: SynthPresetStore | None = None

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/state":
            assert self.state is not None
            self._write_json(200, self.state.snapshot())
            return

        if path == "/api/config":
            assert self.state is not None
            self._write_json(200, self.state.config_copy())
            return
        if path == "/api/synth-presets":
            assert self.state is not None
            assert self.preset_store is not None
            selected = str(self.state.config_copy().get("synthPreset", ""))
            self._write_json(200, self.preset_store.to_payload(selected))
            return

        self._serve_static(path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/synth-presets/select":
            self._handle_select_preset()
            return
        if parsed.path == "/api/synth-presets/save":
            self._handle_save_preset()
            return
        if parsed.path == "/api/synth-presets/reload":
            self._handle_reload_presets()
            return
        if parsed.path != "/api/config":
            self._write_json(404, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except json.JSONDecodeError:
            self._write_json(400, {"error": "invalid json"})
            return

        if not isinstance(payload, dict):
            self._write_json(400, {"error": "config patch must be an object"})
            return

        assert self.state is not None
        cfg = self.state.apply_patch(payload)
        self._write_json(200, {"ok": True, "config": cfg})

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

    def _handle_select_preset(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return
        preset_name = normalize_preset_name(str(payload.get("name", "")))
        assert self.state is not None
        assert self.preset_store is not None
        preset = self.preset_store.get(preset_name)
        if preset is None:
            self._write_json(404, {"error": f"preset not found: {preset_name}"})
            return
        cfg = self.state.apply_patch({"synthPreset": preset_name, "synth": preset})
        self._write_json(
            200,
            {
                "ok": True,
                "config": cfg,
                "presets": self.preset_store.to_payload(preset_name),
            },
        )

    def _handle_save_preset(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return
        name = normalize_preset_name(str(payload.get("name", "")))
        if not name:
            self._write_json(400, {"error": "name is required"})
            return
        assert self.state is not None
        assert self.preset_store is not None
        current_synth = self.state.config_copy().get("synth", {})
        saved_name = self.preset_store.upsert(name, current_synth)
        cfg = self.state.apply_patch({"synthPreset": saved_name})
        self._write_json(
            200,
            {
                "ok": True,
                "name": saved_name,
                "config": cfg,
                "presets": self.preset_store.to_payload(saved_name),
            },
        )

    def _handle_reload_presets(self) -> None:
        assert self.state is not None
        assert self.preset_store is not None
        self.preset_store.load()
        cfg_current = self.state.config_copy()
        selected = normalize_preset_name(str(cfg_current.get("synthPreset", "")))
        preset = self.preset_store.get(selected)
        if preset is not None:
            cfg = self.state.apply_patch({"synthPreset": selected, "synth": preset})
            selected_name = selected
        else:
            names = self.preset_store.list_names()
            if names:
                selected_name = names[0]
                cfg = self.state.apply_patch(
                    {"synthPreset": selected_name, "synth": self.preset_store.get(selected_name)}
                )
            else:
                selected_name = ""
                cfg = cfg_current
        self._write_json(
            200,
            {
                "ok": True,
                "config": cfg,
                "presets": self.preset_store.to_payload(selected_name),
            },
        )

    def _serve_static(self, raw_path: str) -> None:
        assert self.static_dir is not None
        path = raw_path
        if path == "/":
            path = "/index.html"

        rel = path.lstrip("/")
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


class AppHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Therescreen: lid-angle theremin + reactive keyboard/screen + web control panel"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--sample-rate", type=float, default=48_000.0)
    parser.add_argument("--block-size", type=int, default=96)
    parser.add_argument("--config-file", default="therescreen_config.json")
    parser.add_argument("--presets-file", default="synth_presets.yaml")
    parser.add_argument("--ui-dir", default="ui")
    parser.add_argument("--log-file", default="therescreen.log")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )

    parser.add_argument("--lid-command", default="lid-angle")
    parser.add_argument("--ambient-command", default="ambient-light")
    parser.add_argument("--speaker-command", default="speaker")
    parser.add_argument("--keyboard-set-command", default="keyboard-brightness --set={value}")
    parser.add_argument("--screen-set-command", default="screen-brightness --set={value}")

    parser.add_argument("--no-audio", action="store_true")
    parser.add_argument("--no-brightness", action="store_true")
    parser.add_argument("--no-ambient", action="store_true")

    args = parser.parse_args()
    if args.sample_rate <= 0:
        raise SystemExit("--sample-rate must be > 0")
    if args.block_size <= 0:
        raise SystemExit("--block-size must be > 0")
    if not (1 <= args.port <= 65535):
        raise SystemExit("--port must be in 1..65535")
    return args


def main() -> int:
    global np

    args = parse_args()
    root = Path(__file__).resolve().parent

    log_file = Path(args.log_file)
    if not log_file.is_absolute():
        log_file = (root / log_file).resolve()
    setup_logging(log_file=log_file, level_name=args.log_level)

    LOGGER.info("Starting therescreen")

    try:
        import numpy as _np
        np = _np
    except ModuleNotFoundError:
        if not args.no_audio:
            LOGGER.error(
                "Missing dependency: numpy. Install with `pip install mac-hardware-toys` or `pip install numpy`."
            )
            return 1
        np = None

    config_file = Path(args.config_file)
    if not config_file.is_absolute():
        config_file = (root / config_file).resolve()

    presets_file = Path(args.presets_file)
    if not presets_file.is_absolute():
        presets_file = (root / presets_file).resolve()

    ui_dir = Path(args.ui_dir)
    if not ui_dir.is_absolute():
        ui_dir = (root / ui_dir).resolve()
    if not ui_dir.exists():
        LOGGER.error("UI directory not found: %s", ui_dir)
        return 1

    state = SharedState(config_file=config_file)
    state.replace_commands_from_args(args)
    if args.no_ambient:
        state.apply_patch({"sensor": {"useAmbient": False}})

    preset_store = SynthPresetStore(path=presets_file)
    preset_store.load()
    cfg_boot = state.config_copy()
    selected_preset = normalize_preset_name(str(cfg_boot.get("synthPreset", "classic_theremin")))
    selected_values = preset_store.get(selected_preset)
    if selected_values is None:
        names = preset_store.list_names()
        if names:
            selected_preset = names[0]
            selected_values = preset_store.get(selected_preset)
    if selected_values is not None:
        state.apply_patch({"synthPreset": selected_preset, "synth": selected_values})
        LOGGER.info("Active synth preset: %s", selected_preset)
    else:
        LOGGER.warning("No synth presets loaded; keeping current synth config")

    stop_event = threading.Event()

    def _stop(_sig: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    sensor_bridge = SensorBridge(state=state, stop_event=stop_event)
    sensor_bridge.start()

    audio_engine: AudioEngine | None = None
    if not args.no_audio:
        audio_engine = AudioEngine(
            state=state,
            stop_event=stop_event,
            sample_rate=args.sample_rate,
            block_size=args.block_size,
        )
        audio_engine.start()

    brightness_engine: BrightnessEngine | None = None
    if not args.no_brightness:
        brightness_engine = BrightnessEngine(state=state, stop_event=stop_event)
        brightness_engine.start()

    AppRequestHandler.state = state
    AppRequestHandler.static_dir = ui_dir
    AppRequestHandler.preset_store = preset_store

    server: AppHTTPServer | None = None
    try:
        server = AppHTTPServer((args.host, args.port), AppRequestHandler)
    except OSError as exc:
        if exc.errno in (errno.EADDRINUSE, 48, 98):
            LOGGER.warning("Port %s is busy; trying to clean stale therescreen process", args.port)
            cleaned = cleanup_existing_therescreen_on_port(args.port)
            if cleaned:
                try:
                    server = AppHTTPServer((args.host, args.port), AppRequestHandler)
                except OSError as retry_exc:
                    LOGGER.error("Port %s still busy after cleanup: %s", args.port, retry_exc)
                    return 1
            else:
                LOGGER.error(
                    "Port %s already in use. Run with another port, e.g. "
                    "`sudo python3 therescreen.py --port %s`",
                    args.port,
                    args.port + 1,
                )
                return 1
        else:
            raise
    if server is None:
        LOGGER.error("Internal error: server was not initialized")
        return 1
    server.timeout = 0.5

    LOGGER.info("UI: http://%s:%s", args.host, args.port)
    LOGGER.info("Press Ctrl+C to stop")
    LOGGER.info("Tip: run with sudo if lid/ambient commands fail due permissions")

    try:
        while not stop_event.is_set():
            server.handle_request()
    except Exception as exc:
        LOGGER.exception("Fatal server loop error: %s", exc)
        return 1
    finally:
        stop_event.set()
        try:
            server.server_close()
        except Exception as exc:
            LOGGER.warning("Error while closing server: %s", exc)
        sensor_bridge.stop()
        if audio_engine is not None:
            audio_engine.stop()
        if brightness_engine is not None:
            brightness_engine.stop()

    LOGGER.info("Therescreen stopped cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
