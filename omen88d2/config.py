# SPDX-License-Identifier: GPL-3.0-or-later
"""TOML configuration + fan-curve interpolation.

A curve is a list of [temperature C, level 0-55] points; between points we
linearly interpolate, outside the ends we clamp. Levels are ~100 RPM each.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field

from .control import LEVEL_MAX


class ConfigError(Exception):
    pass


CurvePoint = tuple[float, int]


def interp(points: list[CurvePoint], temp: float) -> int:
    pts = sorted(points, key=lambda p: p[0])
    if temp <= pts[0][0]:
        return pts[0][1]
    if temp >= pts[-1][0]:
        return pts[-1][1]
    for (t0, l0), (t1, l1) in zip(pts, pts[1:]):
        if t0 <= temp <= t1:
            span = t1 - t0
            frac = 0.0 if span <= 0 else (temp - t0) / span
            return round(l0 + frac * (l1 - l0))
    return pts[-1][1]  # unreachable


@dataclass
class Config:
    poll_interval: float = 2.0
    keepalive_below: int = 60
    keepalive_set: int = 120
    temp_fail_max: int = 3
    min_level: int = 5
    max_level: int = LEVEL_MAX
    max_step_up: int = 6
    max_step_down: int = 4
    critical_temp: float = 95.0
    emergency_temp: float = 100.0
    log_every: int = 15  # info-level status line every N polls
    cpu_curve: list[CurvePoint] = field(default_factory=lambda: [
        (40.0, 6), (50.0, 10), (60.0, 18), (70.0, 28), (80.0, 40), (90.0, 52),
    ])
    gpu_curve: list[CurvePoint] = field(default_factory=lambda: [
        (40.0, 6), (50.0, 10), (60.0, 18), (70.0, 30), (80.0, 42), (90.0, 52),
    ])

    def target_levels(self, cpu_temp: float, gpu_temp: float) -> tuple[int, int]:
        cpu = max(self.min_level, min(self.max_level, interp(self.cpu_curve, cpu_temp)))
        gpu = max(self.min_level, min(self.max_level, interp(self.gpu_curve, gpu_temp)))
        return cpu, gpu


def _parse_curve(raw, name: str) -> list[CurvePoint]:
    if not isinstance(raw, list) or not raw:
        raise ConfigError(f"{name}: need a non-empty list of [temp, level] points")
    pts: list[CurvePoint] = []
    for i, p in enumerate(raw):
        if (not isinstance(p, (list, tuple)) or len(p) != 2
                or not isinstance(p[0], (int, float))
                or not isinstance(p[1], (int, float))):
            raise ConfigError(f"{name}[{i}]: expected [temp_c, level], got {p!r}")
        t, lvl = float(p[0]), int(round(p[1]))
        if not -20.0 <= t <= 120.0:
            raise ConfigError(f"{name}[{i}]: temp {t} out of range")
        if not 0 <= lvl <= LEVEL_MAX:
            raise ConfigError(f"{name}[{i}]: level {lvl} out of 0-{LEVEL_MAX}")
        pts.append((t, lvl))
    temps = [t for t, _ in pts]
    if any(b < a for a, b in zip(temps, temps[1:])):
        raise ConfigError(f"{name}: temps must be ascending: {temps}")
    return pts


def load(path: str | None) -> tuple[Config, str]:
    """Load config file (or defaults if path is None). Returns (cfg, source)."""
    cfg = Config()
    if path is None:
        return cfg, "<built-in defaults>"
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}")
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"config {path}: invalid TOML: {e}")
    if not isinstance(data, dict):
        raise ConfigError(f"config {path}: top level must be a table")

    def num(key: str, lo: float, hi: float, cast=float):
        if key in data:
            v = data[key]
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                raise ConfigError(f"{key}: expected a number, got {v!r}")
            if not lo <= v <= hi:
                raise ConfigError(f"{key}: {v} out of range [{lo}, {hi}]")
            setattr(cfg, key, cast(v))

    num("poll_interval", 0.5, 30.0)
    num("keepalive_below", 5, 250, int)
    num("keepalive_set", 10, 255, int)
    num("temp_fail_max", 1, 20, int)
    num("min_level", 0, LEVEL_MAX, int)
    num("max_level", 0, LEVEL_MAX, int)
    num("max_step_up", 1, LEVEL_MAX, int)
    num("max_step_down", 1, LEVEL_MAX, int)
    num("critical_temp", 70.0, 105.0)
    num("emergency_temp", 70.0, 105.0)
    num("log_every", 1, 100000, int)

    if "cpu_curve" in data:
        cfg.cpu_curve = _parse_curve(data["cpu_curve"], "cpu_curve")
    if "gpu_curve" in data:
        cfg.gpu_curve = _parse_curve(data["gpu_curve"], "gpu_curve")

    if cfg.min_level > cfg.max_level:
        raise ConfigError("min_level may not exceed max_level")
    if cfg.keepalive_below >= cfg.keepalive_set:
        raise ConfigError("keepalive_below must be < keepalive_set")
    if cfg.emergency_temp <= cfg.critical_temp:
        raise ConfigError("emergency_temp must be > critical_temp")
    return cfg, path
