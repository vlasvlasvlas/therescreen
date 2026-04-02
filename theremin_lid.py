#!/usr/bin/env python3
"""Classic theremin controlled by MacBook lid-angle and ambient light."""

from __future__ import annotations

import argparse
import json
import math
import shlex
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

np = None


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def clamp01(value: float) -> float:
    return clamp(value, 0.0, 1.0)


def smooth_curve(
    previous: float,
    target: float,
    alpha: float,
    frames: int,
) -> tuple[np.ndarray, float]:
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


def alpha_from_ms(sample_rate: float, time_ms: float) -> float:
    t = max(1e-4, float(time_ms) / 1000.0)
    return 1.0 - math.exp(-1.0 / (max(1.0, float(sample_rate)) * t))


@dataclass
class SensorSnapshot:
    angle_deg: float
    level: float
    ambient: float
    last_lid_time: float


class SensorHub:
    def __init__(self, args: argparse.Namespace, stop_event: threading.Event) -> None:
        self.args = args
        self.stop_event = stop_event
        self.lock = threading.Lock()
        self.procs: list[subprocess.Popen[str]] = []
        self.threads: list[threading.Thread] = []

        self.angle_deg = args.angle_min
        self.level = 0.0
        self.ambient = 0.5
        self.last_lid_time = 0.0

    def _debug(self, msg: str) -> None:
        if self.args.debug:
            print(f"[theremin] {msg}", file=sys.stderr, flush=True)

    def _fail(self, msg: str) -> None:
        print(f"[theremin] {msg}", file=sys.stderr, flush=True)
        self.stop_event.set()

    def _spawn_json_reader(
        self,
        name: str,
        cmd: list[str],
        on_payload: callable,
    ) -> None:
        def run() -> None:
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=None,
                    text=True,
                    bufsize=1,
                )
            except FileNotFoundError:
                self._fail(
                    f"command not found: {cmd[0]!r}. Install mac-hardware-toys or set --{name}-command."
                )
                return
            except Exception as exc:  # pragma: no cover
                self._fail(f"failed to start {name}: {exc}")
                return

            self._debug(f"started {name}: {' '.join(shlex.quote(part) for part in cmd)}")
            self.procs.append(proc)

            if proc.stdout is None:
                self._fail(f"{name} has no stdout pipe")
                return

            try:
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
                    code = proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    code = proc.wait(timeout=1.0)
                if code != 0 and not self.stop_event.is_set():
                    self._fail(f"{name} exited with code {code}")

        thread = threading.Thread(target=run, daemon=True, name=f"{name}-reader")
        self.threads.append(thread)
        thread.start()

    def start(self) -> None:
        lid_cmd = shlex.split(self.args.lid_command) + [
            "--json",
            "--angle-min",
            str(self.args.angle_min),
            "--angle-max",
            str(self.args.angle_max),
        ]
        self._spawn_json_reader("lid", lid_cmd, self._on_lid_payload)

        if not self.args.no_ambient:
            ambient_cmd = shlex.split(self.args.ambient_command) + ["--json"]
            self._spawn_json_reader("ambient", ambient_cmd, self._on_ambient_payload)

    def _on_lid_payload(self, payload: dict[str, object]) -> None:
        try:
            angle_raw = float(
                payload.get("angle_clamped_deg", payload.get("angle_deg", self.args.angle_min))
            )
            level_raw = float(payload.get("level", 0.0))
        except (TypeError, ValueError):
            return

        with self.lock:
            self.angle_deg = clamp(angle_raw, self.args.angle_min, self.args.angle_max)
            self.level = clamp01(level_raw)
            self.last_lid_time = time.time()

    def _on_ambient_payload(self, payload: dict[str, object]) -> None:
        try:
            ambient_raw = float(payload.get("intensity", payload.get("level", 0.5)))
        except (TypeError, ValueError):
            return
        with self.lock:
            self.ambient = clamp01(ambient_raw)

    def snapshot(self) -> SensorSnapshot:
        with self.lock:
            return SensorSnapshot(
                angle_deg=self.angle_deg,
                level=self.level,
                ambient=self.ambient,
                last_lid_time=self.last_lid_time,
            )

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


class ThereminSynth:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.sample_rate = float(args.sample_rate)
        self.block_size = int(args.block_size)

        self.freq = float(args.min_hz)
        self.env = 0.0
        self.phase = 0.0
        self.lfo_phase = 0.0
        self.ratio = float(args.max_hz) / float(args.min_hz)

        self.glide_alpha = alpha_from_ms(self.sample_rate, args.glide_ms)
        self.attack_alpha = alpha_from_ms(self.sample_rate, args.attack_ms)
        self.release_alpha = alpha_from_ms(self.sample_rate, args.release_ms)
        self.lfo_step = 2.0 * math.pi * float(args.vibrato_rate_hz) / self.sample_rate
        self.phase_step = 2.0 * math.pi / self.sample_rate

        delay_samples = max(1, int(round(self.sample_rate * args.delay_ms / 1000.0)))
        self.delay = np.zeros(delay_samples, dtype=np.float32)
        self.delay_idx = 0

        self.reverb_buffers = [
            np.zeros(max(1, int(round(self.sample_rate * ms / 1000.0))), dtype=np.float32)
            for ms in (29.7, 37.1, 53.3)
        ]
        self.reverb_idxs = [0, 0, 0]
        self.reverb_feedback = (0.76, 0.72, 0.68)

    def _apply_fx(self, dry: np.ndarray) -> np.ndarray:
        if self.args.delay_mix <= 0.0 and self.args.reverb_mix <= 0.0:
            return dry

        dry_gain = max(0.0, 1.0 - self.args.delay_mix - self.args.reverb_mix)
        out = np.empty_like(dry)

        for i, sample in enumerate(dry):
            d = self.delay[self.delay_idx]
            self.delay[self.delay_idx] = sample + d * self.args.delay_feedback
            self.delay_idx += 1
            if self.delay_idx >= self.delay.shape[0]:
                self.delay_idx = 0

            rv = 0.0
            for idx, buf in enumerate(self.reverb_buffers):
                r_i = self.reverb_idxs[idx]
                tap = buf[r_i]
                buf[r_i] = sample + tap * self.reverb_feedback[idx]
                r_i += 1
                if r_i >= buf.shape[0]:
                    r_i = 0
                self.reverb_idxs[idx] = r_i
                rv += tap
            rv /= float(len(self.reverb_buffers))

            out[i] = (
                dry_gain * sample
                + self.args.delay_mix * d
                + self.args.reverb_mix * rv
            )
        return out

    def render(self, snapshot: SensorSnapshot, now_ts: float) -> np.ndarray:
        stale = snapshot.last_lid_time <= 0.0 or (now_ts - snapshot.last_lid_time) > self.args.sensor_timeout
        level = 0.0 if stale else clamp01(snapshot.level)
        angle_deg = self.args.angle_min if stale else snapshot.angle_deg
        ambient = 0.5 if self.args.no_ambient else clamp01(snapshot.ambient)

        target_freq = float(self.args.min_hz) * math.pow(self.ratio, level)
        freq_curve, self.freq = smooth_curve(
            self.freq,
            target_freq,
            self.glide_alpha,
            self.block_size,
        )

        gate_target = 1.0 if angle_deg >= self.args.gate_open_deg else 0.0
        env_alpha = self.attack_alpha if gate_target > self.env else self.release_alpha
        env_curve, self.env = smooth_curve(self.env, gate_target, env_alpha, self.block_size)

        base_amp = self.args.low_volume + (self.args.high_volume - self.args.low_volume) * level
        ambient_gain = self.args.ambient_min_gain + ambient * (1.0 - self.args.ambient_min_gain)
        amp_curve = env_curve * base_amp * ambient_gain

        depth_cents = self.args.vibrato_depth_cents + ambient * self.args.vibrato_ambient_cents
        lfo_phase = self.lfo_phase + self.lfo_step * np.arange(self.block_size, dtype=np.float64)
        vib = np.power(2.0, (depth_cents / 1200.0) * np.sin(lfo_phase))
        self.lfo_phase = float((self.lfo_phase + self.lfo_step * self.block_size) % (2.0 * math.pi))

        freq_mod = freq_curve * vib
        phase_inc = self.phase_step * freq_mod
        phase = self.phase + np.cumsum(phase_inc, dtype=np.float64)
        self.phase = float(phase[-1] % (2.0 * math.pi))

        dry = (np.sin(phase) * amp_curve).astype(np.float32, copy=False)
        wet = self._apply_fx(dry)
        wet *= float(self.args.master_gain)
        return np.clip(wet, -1.0, 1.0).astype(np.float32, copy=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a classic-style theremin tone using Mac lid-angle "
            "for pitch and ambient-light for expression."
        )
    )
    parser.add_argument("--sample-rate", type=float, default=24_000.0)
    parser.add_argument("--block-size", type=int, default=256)

    parser.add_argument("--angle-min", type=float, default=0.0)
    parser.add_argument("--angle-max", type=float, default=125.0)
    parser.add_argument("--gate-open-deg", type=float, default=5.0)
    parser.add_argument("--sensor-timeout", type=float, default=1.0)

    parser.add_argument("--min-hz", type=float, default=130.81, help="C3")
    parser.add_argument("--max-hz", type=float, default=1760.0, help="A6")
    parser.add_argument("--low-volume", type=float, default=0.04)
    parser.add_argument("--high-volume", type=float, default=0.62)

    parser.add_argument("--attack-ms", type=float, default=80.0)
    parser.add_argument("--release-ms", type=float, default=300.0)
    parser.add_argument("--glide-ms", type=float, default=220.0)

    parser.add_argument("--vibrato-rate-hz", type=float, default=6.1)
    parser.add_argument("--vibrato-depth-cents", type=float, default=7.5)
    parser.add_argument("--vibrato-ambient-cents", type=float, default=16.0)
    parser.add_argument("--ambient-min-gain", type=float, default=0.65)
    parser.add_argument("--no-ambient", action="store_true")

    parser.add_argument("--delay-ms", type=float, default=360.0)
    parser.add_argument("--delay-feedback", type=float, default=0.22)
    parser.add_argument("--delay-mix", type=float, default=0.14)
    parser.add_argument("--reverb-mix", type=float, default=0.30)
    parser.add_argument("--master-gain", type=float, default=0.90)

    parser.add_argument("--lid-command", default="lid-angle")
    parser.add_argument("--ambient-command", default="ambient-light")
    parser.add_argument("--debug", action="store_true")

    args = parser.parse_args()

    if args.sample_rate <= 0:
        raise SystemExit("--sample-rate must be > 0")
    if args.block_size <= 0:
        raise SystemExit("--block-size must be > 0")
    if args.min_hz <= 0 or args.max_hz <= 0:
        raise SystemExit("--min-hz and --max-hz must be > 0")
    if args.max_hz <= args.min_hz:
        raise SystemExit("--max-hz must be greater than --min-hz")
    if args.angle_max <= args.angle_min:
        raise SystemExit("--angle-max must be greater than --angle-min")
    if args.gate_open_deg < args.angle_min or args.gate_open_deg > args.angle_max:
        raise SystemExit("--gate-open-deg must be within angle range")

    for field_name in (
        "low_volume",
        "high_volume",
        "ambient_min_gain",
        "delay_feedback",
        "delay_mix",
        "reverb_mix",
        "master_gain",
    ):
        value = getattr(args, field_name)
        if value < 0:
            raise SystemExit(f"--{field_name.replace('_', '-')} must be >= 0")

    if args.low_volume > 1.0 or args.high_volume > 1.0:
        raise SystemExit("--low-volume and --high-volume must be in 0..1")
    if args.ambient_min_gain > 1.0:
        raise SystemExit("--ambient-min-gain must be in 0..1")
    if args.delay_feedback >= 1.0:
        raise SystemExit("--delay-feedback should be < 1.0 to avoid runaway feedback")

    return args


def main() -> int:
    args = parse_args()
    global np
    try:
        import numpy as _np
    except ModuleNotFoundError:
        raise SystemExit(
            "Missing dependency: numpy. Install with `pip install mac-hardware-toys`."
        )
    np = _np

    stop_event = threading.Event()

    def _stop(_sig: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    if sys.stdout.isatty():
        print(
            "[theremin] stdout is a terminal. Pipe to `speaker` to hear audio.",
            file=sys.stderr,
            flush=True,
        )

    hub = SensorHub(args, stop_event)
    synth = ThereminSynth(args)

    try:
        hub.start()

        out = sys.stdout.buffer
        out.write(f"MSIG1 {int(round(args.sample_rate))}\n".encode("ascii"))
        out.flush()

        while not stop_event.is_set():
            snapshot = hub.snapshot()
            block = synth.render(snapshot, time.time())
            out.write(block.tobytes(order="C"))
            out.flush()

    except BrokenPipeError:
        return 0
    finally:
        hub.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
