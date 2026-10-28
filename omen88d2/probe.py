# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only diagnostics: environment + full EC dump + map verification.

Never writes to the EC. The report this produces is what the user pastes
back for remote debugging / calibration confirmation.
"""
from __future__ import annotations

import datetime
import glob
import os
import platform
import shutil
import subprocess

from .control import Controller, REG_MODE
from .ec import ECBackend
from .temps import read_hwmon, read_nvidia


def _read(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _sb_state() -> str:
    if shutil.which("mokutil"):
        try:
            p = subprocess.run(["mokutil", "--sb-state"], capture_output=True,
                               text=True, timeout=5)
            if p.returncode == 0 and p.stdout.strip():
                return p.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    v = _read("/sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c")
    if v is not None:
        return "SecureBoot efivar present (likely enabled)" if v.endswith("1") else "efivar read"
    return "unknown (no mokutil)"


def _hexdump(data: bytes) -> str:
    lines = ["0x " + " ".join(f"_{h:x}" for h in range(16))]
    for row in range(16):
        chunk = data[row * 16:(row + 1) * 16]
        lines.append(f"{row:x}_ " + " ".join(f"{b:02X}" for b in chunk))
    return "\n".join(lines)


def gather(ec: ECBackend) -> dict:
    ctrl = Controller(ec)
    hwmon_dirs = sorted(glob.glob("/sys/devices/platform/hp-wmi/hwmon/hwmon*"))
    hwmon_files = sorted(os.path.basename(p) for d in hwmon_dirs
                         for p in glob.glob(os.path.join(d, "*")) if os.path.isfile(p))
    return {
        "when": f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}",
        "board": _read("/sys/class/dmi/id/board_name") or "unknown",
        "product": _read("/sys/class/dmi/id/product_name") or "unknown",
        "bios": _read("/sys/class/dmi/id/bios_version") or "unknown",
        "kernel": platform.release(),
        "lockdown": _read("/sys/kernel/security/lockdown") or "unknown",
        "secureboot": _sb_state(),
        "platform_profile": _read("/sys/firmware/acpi/platform_profile") or "n/a",
        "platform_choices": _read("/sys/firmware/acpi/platform_profile_choices") or "n/a",
        "hp_wmi_hwmon": hwmon_files or ["(none)"],
        "ec_backend": ec.name,
        "ec_sys_loaded": os.path.isdir("/sys/module/ec_sys"),
        "hwmon": read_hwmon(),
        "nvidia": read_nvidia(),
        "status": ctrl.status(),
        "dump": ec.dump(),
    }


def verify(data: dict) -> list[str]:
    """Plausibility checks of the live EC state against the 88D2 map."""
    st = data["status"]
    out: list[str] = []

    def line(ok: bool, msg: str) -> None:
        out.append(f"[{'OK' if ok else 'WARN'}] {msg}")

    line(data["board"] == "88D2", f"DMI board = {data['board']} (expect 88D2)")
    line(st["profile_mode"] in (0x30, 0x31, 0x50),
         f"thermal profile 0x95 = 0x{st['profile_mode']:02X} (expect 0x30/0x31/0x50)")
    line(st["rpm_cpu"] < 8000, f"cpu tach 0xB0 = {st['rpm_cpu']} rpm (sane <8000)")
    line(st["rpm_gpu"] < 8000, f"gpu tach 0xB2 = {st['rpm_gpu']} rpm (sane <8000)")
    for name, key in (("cpu", "ec_temp_cpu"), ("gpu", "ec_temp_gpu")):
        t = st[key]
        line(10 <= t <= 105, f"EC temp {name} = {t}C (sane 10-105)")
    line(st["manual_raw"] in (0x00, 0x06),
         f"manual switch 0x62 = 0x{st['manual_raw']:02X} (expect 0x00/0x06)")
    if st["mode"] == "manual":
        line(st["rate_cpu"] <= 100 and st["rate_gpu"] <= 100,
             f"rate mirrors = {st['rate_cpu']}/{st['rate_gpu']}% (<=100)")
        exp_cpu = round(st["level_cpu"] * 100 / 55)
        line(abs(st["rate_cpu"] - exp_cpu) <= 8,
             f"cpu level {st['level_cpu']} ~= rate {st['rate_cpu']}% (consistent)")
    else:
        line(True, f"mode = {st['mode']}, countdown = {st['countdown']}s")
    line(bool(data["hwmon"]), f"hwmon temp inputs found: {len(data['hwmon'])}")
    return out


def format_report(data: dict, checks: list[str]) -> str:
    st = data["status"]
    hwmon = "\n".join(f"    {n} = {t:.1f}C" for n, t in data["hwmon"]) or "    (none)"
    nv = f"{data['nvidia']:.0f}C" if data["nvidia"] is not None else "n/a"
    return f"""omen88d2 probe (READ-ONLY) — {data['when']}
backend: {data['ec_backend']}  ec_sys loaded: {data['ec_sys_loaded']}
board: {data['board']}  product: {data['product']}  bios: {data['bios']}
kernel: {data['kernel']}  lockdown: {data['lockdown']}  sb: {data['secureboot']}
platform_profile: {data['platform_profile']}  choices: {data['platform_choices']}
hp-wmi hwmon files: {' '.join(data['hp_wmi_hwmon'])}

live status: mode={st['mode']} countdown={st['countdown']}s
  levels cpu={st['level_cpu']} gpu={st['level_gpu']}  rates {st['rate_cpu']}/{st['rate_gpu']}%
  rpm cpu={st['rpm_cpu']} gpu={st['rpm_gpu']}
  ec_temps cpu={st['ec_temp_cpu']}C gpu={st['ec_temp_gpu']}C  profile=0x{st['profile_mode']:02X}
hwmon:
{hwmon}
nvidia: {nv}

map verification:
{chr(10).join(checks)}

full EC dump:
{_hexdump(data['dump'])}
"""
