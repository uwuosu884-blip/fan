# SPDX-License-Identifier: GPL-3.0-or-later
"""Temperature sources: hwmon (k10temp), nvidia-smi, EC mirrors (0x57/0x58).

CPU temp prefers k10temp (fast, precise), falling back to EC 0x57.
GPU temp prefers nvidia-smi, then hwmon GPU sensors, then EC 0x58, and as a
last resort the CPU temperature (conservative: the GPU fan then follows the
hottest known value rather than going blind).
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
from dataclasses import dataclass

from .control import REG_TEMP_CPU, REG_TEMP_GPU
from .ec import ECBackend

_SENTINEL_8BIT = 0xFF  # EC "no sensor" value seen on some boards


class TempError(Exception):
    """No usable temperature source."""


@dataclass
class Temps:
    cpu: float
    gpu: float
    cpu_source: str
    gpu_source: str


def _read_file(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def read_hwmon() -> list[tuple[str, float]]:
    """All hwmon temperature inputs as (display name, degrees C)."""
    out: list[tuple[str, float]] = []
    for hw in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        name = _read_file(os.path.join(hw, "name")) or os.path.basename(hw)
        for inp in sorted(glob.glob(os.path.join(hw, "temp*_input"))):
            raw = _read_file(inp)
            if raw is None:
                continue
            try:
                temp = int(raw) / 1000.0
            except ValueError:
                continue
            if not -40.0 <= temp <= 130.0:
                continue
            base = inp[: -len("_input")]
            label = _read_file(base + "_label") or os.path.basename(base)
            out.append((f"{name}:{label}", temp))
    return out


def read_nvidia() -> float | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        p = subprocess.run([exe, "--query-gpu=temperature.gpu",
                            "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    try:
        temp = float(p.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return None
    return temp if -40.0 <= temp <= 130.0 else None


def _pick(cands: list[tuple[str, float]], *keywords: str) -> tuple[float, str] | None:
    matches = [(n, t) for n, t in cands
               if any(k in n.lower() for k in keywords)]
    if not matches:
        return None
    # Hottest match wins (Tctl over Tccd over package, etc.).
    name, temp = max(matches, key=lambda nt: nt[1])
    return temp, f"hwmon:{name}"


def get_temps(ec: ECBackend) -> Temps:
    hwmon = read_hwmon()

    ec_cpu_raw = ec.read(REG_TEMP_CPU)
    ec_gpu_raw = ec.read(REG_TEMP_GPU)
    ec_cpu = None if ec_cpu_raw in (_SENTINEL_8BIT,) or ec_cpu_raw > 125 else float(ec_cpu_raw)
    ec_gpu = None if ec_gpu_raw in (_SENTINEL_8BIT,) or ec_gpu_raw > 125 else float(ec_gpu_raw)

    cpu = _pick(hwmon, "k10temp", "tctl", "tccd", "package", "cpu", "core", "acpitz")
    if cpu is None and ec_cpu is not None:
        cpu = (ec_cpu, "ec:0x57")
    if cpu is None:
        raise TempError("no CPU temperature source (hwmon empty, EC 0x57 invalid)")

    gpu_temp = read_nvidia()
    if gpu_temp is not None:
        gpu = (gpu_temp, "nvidia-smi")
    else:
        gpu = _pick(hwmon, "gpu", "amdgpu", "nvidia", "edge", "junction", "hotspot")
        if gpu is None and ec_gpu is not None:
            gpu = (ec_gpu, "ec:0x58")
        if gpu is None:
            # Conservative fallback: drive the GPU fan from the CPU temp
            # rather than leaving it blind.
            gpu = (cpu[0], cpu[1] + "+gpu-fallback")

    return Temps(cpu=cpu[0], gpu=gpu[0], cpu_source=cpu[1], gpu_source=gpu[1])
