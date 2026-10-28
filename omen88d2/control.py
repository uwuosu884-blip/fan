# SPDX-License-Identifier: GPL-3.0-or-later
"""High-level fan control for the 88D2 EC layout.

All EC writes go through this module, and only to the five documented fan
registers (0x62, 0x63, 0x34, 0x35 + 0x34/0x35 mirrors). Everything else is
read-only.
"""
from __future__ import annotations

import logging
import time

from .ec import ECBackend, ECError

REG_MANUAL = 0x62
REG_COUNTDOWN = 0x63
REG_LVL_CPU = 0x34
REG_LVL_GPU = 0x35
REG_RATE_R_CPU = 0x2E
REG_RATE_R_GPU = 0x2F
REG_TACH_CPU = 0xB0
REG_TACH_GPU = 0xB2
REG_TEMP_CPU = 0x57
REG_TEMP_GPU = 0x58
REG_MODE = 0x95

MANUAL_ON = 0x06
MANUAL_OFF = 0x00
LEVEL_MAX = 55
COUNTDOWN_DEFAULT = 120

log = logging.getLogger("omen88d2")


def _clamp_level(v: int) -> int:
    return max(0, min(LEVEL_MAX, int(v)))


class Controller:
    def __init__(self, ec: ECBackend) -> None:
        self.ec = ec

    # -- reads ---------------------------------------------------------------
    def _tach(self, reg: int) -> int:
        lo = self.ec.read(reg)
        hi = self.ec.read(reg + 1)
        return (hi << 8) | lo

    def status(self) -> dict:
        manual_raw = self.ec.read(REG_MANUAL)
        if manual_raw == MANUAL_ON:
            mode = "manual"
        elif manual_raw == MANUAL_OFF:
            mode = "auto(bios)"
        else:
            mode = f"unknown(0x{manual_raw:02X})"
        return {
            "mode": mode,
            "manual_raw": manual_raw,
            "countdown": self.ec.read(REG_COUNTDOWN),
            "level_cpu": self.ec.read(REG_LVL_CPU),
            "level_gpu": self.ec.read(REG_LVL_GPU),
            "rate_cpu": self.ec.read(REG_RATE_R_CPU),
            "rate_gpu": self.ec.read(REG_RATE_R_GPU),
            "rpm_cpu": self._tach(REG_TACH_CPU),
            "rpm_gpu": self._tach(REG_TACH_GPU),
            "ec_temp_cpu": self.ec.read(REG_TEMP_CPU),
            "ec_temp_gpu": self.ec.read(REG_TEMP_GPU),
            "profile_mode": self.ec.read(REG_MODE),
        }

    # -- control --------------------------------------------------------------
    def set_manual(self, on: bool, start_cpu: int | None = None,
                   start_gpu: int | None = None) -> dict:
        """Enter (True) or leave (False) manual fan control.

        When entering, setpoints are programmed *before* flipping the manual
        switch so the fans never run a stale/zero level. Starting levels
        default to the currently observed tach (~100 RPM per unit).
        """
        if on:
            st = self.status()
            if start_cpu is None:
                start_cpu = max(6, min(LEVEL_MAX, st["rpm_cpu"] // 100))
            if start_gpu is None:
                start_gpu = max(6, min(LEVEL_MAX, st["rpm_gpu"] // 100))
            cpu, gpu = _clamp_level(start_cpu), _clamp_level(start_gpu)
            log.debug("manual on: seeding levels cpu=%d gpu=%d", cpu, gpu)
            self.ec.write(REG_LVL_CPU, cpu)
            self.ec.write(REG_LVL_GPU, gpu)
            self.ec.write(REG_MANUAL, MANUAL_ON)
            self.ec.write(REG_COUNTDOWN, COUNTDOWN_DEFAULT)
            st = self.status()
            if st["manual_raw"] != MANUAL_ON:
                raise ECError(
                    f"EC refused manual mode (0x62 reads back 0x{st['manual_raw']:02X})")
            log.info("manual mode on (seed cpu=%d gpu=%d, countdown=%ds)",
                     st["level_cpu"], st["level_gpu"], st["countdown"])
            return st
        else:
            return self.restore_auto()

    def set_levels(self, cpu: int, gpu: int) -> dict:
        """Set per-fan levels (0-55, ~100 RPM each). Enables manual if needed."""
        cpu, gpu = _clamp_level(cpu), _clamp_level(gpu)
        if self.ec.read(REG_MANUAL) != MANUAL_ON:
            return self.set_manual(True, start_cpu=cpu, start_gpu=gpu)
        cur_cpu = self.ec.read(REG_LVL_CPU)
        cur_gpu = self.ec.read(REG_LVL_GPU)
        if cpu != cur_cpu:
            self.ec.write(REG_LVL_CPU, cpu)
        if gpu != cur_gpu:
            self.ec.write(REG_LVL_GPU, gpu)
        return self.status()

    def keepalive(self, below: int = 60, set_to: int = COUNTDOWN_DEFAULT) -> bool:
        """Refresh the firmware countdown before it expires.

        Returns True if a refresh write was done. If our process dies, the
        countdown hits 0 and the EC returns the fans to BIOS auto by itself.
        """
        st = self.status()
        if st["mode"] != "manual":
            return False
        if st["countdown"] <= below:
            self.ec.write(REG_COUNTDOWN, max(1, min(255, set_to)))
            log.debug("countdown refreshed to %ds", set_to)
            return True
        return False

    def restore_auto(self) -> dict:
        """Hand the fans back to the BIOS. Verifies the switch readback."""
        self.ec.write(REG_LVL_CPU, 0)
        self.ec.write(REG_LVL_GPU, 0)
        self.ec.write(REG_MANUAL, MANUAL_OFF)
        time.sleep(0.05)
        st = self.status()
        if st["manual_raw"] != MANUAL_OFF:
            raise ECError(
                f"EC did not release to auto (0x62 reads back 0x{st['manual_raw']:02X})")
        log.info("fans restored to BIOS auto")
        return st
