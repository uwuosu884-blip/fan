# SPDX-License-Identifier: GPL-3.0-or-later
"""Simulated 88D2 Embedded Controller for --dry-run, demos and tests.

Behaviour mirrors the documented 88D2 map (OmenMon-Reborn issue #181 EC
dumps + pankha type2 layout):

* 0x62 (OMCC): 0x06 = manual, 0x00 = BIOS auto.
* 0x63 (XFCD): countdown in seconds; when it reaches 0 while manual, the EC
  clears 0x62 back to auto by itself (the firmware failsafe).
* 0x34/0x35: per-fan setpoints, ~100 RPM per unit, 0-55.
* 0x2C/0x2D: rate-write mirror (%), 0x2E/0x2F: rate-read mirror (%).
* 0xB0/0xB2: tachometers, 16-bit LE RPM.
* 0x57/0x58: temperatures in C.

``time_scale`` accelerates the countdown clock (120 = 120 firmware-seconds
per wall-second) so tests don't have to wait two minutes.
"""
from __future__ import annotations

import time

from .ec import ECBackend, ECError, EC_SIZE, _check

# Real 0%-fan EC dump of an 88D2 board (OmenMon-Reborn #181), used as the
# power-on baseline. Bytes that differ in auto mode are fixed up below.
_BASELINE_0PCT = bytes.fromhex(
    "0080161a31004f4e303730584c2d4134"
    "34480001000000000000000000000000"
    "00000000040000000000000000000000"
    "9c101c02000011000000000000000000"
    "0b010414041000002f34000000000000"
    "8000010f0005003c3e34000000000000"
    "0000066b000000000074000600080800"
    "fc17a9131e2d803333332d32432d3245"
    "2d41027d098210153200000001800000"
    "d1d1a913053154ca10643200006a0c00"
    "08804488012000000015000000010006"
    "00000000003f05300000000700000000"
    "03000000008001000000000000000000"
    "540000002e06000154001d0300000000"
    "00000200000000000000080000890000"
    "08002000000000000100373537353030"
)

REG_MANUAL = 0x62
REG_COUNTDOWN = 0x63
REG_LVL_CPU = 0x34
REG_LVL_GPU = 0x35
REG_RATE_W_CPU = 0x2C
REG_RATE_W_GPU = 0x2D
REG_RATE_R_CPU = 0x2E
REG_RATE_R_GPU = 0x2F
REG_TACH_CPU = 0xB0
REG_TACH_GPU = 0xB2
REG_TEMP_CPU = 0x57
REG_TEMP_GPU = 0x58

MANUAL_ON = 0x06
MANUAL_OFF = 0x00
LEVEL_MAX = 55


class FakeEC(ECBackend):
    name = "fake"

    def __init__(self, time_scale: float = 1.0,
                 auto_cpu_rpm: int = 1250, auto_gpu_rpm: int = 1180) -> None:
        if len(_BASELINE_0PCT) != EC_SIZE:
            raise ECError("baseline dump corrupt")
        self.regs = bytearray(_BASELINE_0PCT)
        self.time_scale = time_scale
        self.auto_cpu_rpm = auto_cpu_rpm
        self.auto_gpu_rpm = auto_gpu_rpm
        self.rpm_jitter = 0  # optional +/- wobble for demos (tests use 0)
        # Fresh-boot state: BIOS auto, fans on low, plausible temps.
        self.regs[REG_MANUAL] = MANUAL_OFF
        self.regs[REG_COUNTDOWN] = 0x00
        self.regs[REG_LVL_CPU] = 0
        self.regs[REG_LVL_GPU] = 0
        self.regs[REG_TEMP_CPU] = 56
        self.regs[REG_TEMP_GPU] = 54
        self._deadline: float | None = None  # monotonic() when countdown ends
        self._jitter_state = 0
        self._tick()

    # -- test/demo helpers -------------------------------------------------
    def set_temps(self, cpu_c: int, gpu_c: int) -> None:
        self.regs[REG_TEMP_CPU] = cpu_c & 0xFF
        self.regs[REG_TEMP_GPU] = gpu_c & 0xFF

    @property
    def manual(self) -> bool:
        self._tick()
        return self.regs[REG_MANUAL] == MANUAL_ON

    # -- internals ----------------------------------------------------------
    def _now(self) -> float:
        return time.monotonic()

    def _wobble(self) -> int:
        if not self.rpm_jitter:
            return 0
        # Deterministic triangle wobble, no random module needed.
        self._jitter_state = (self._jitter_state + 7) % (4 * self.rpm_jitter + 1)
        return self._jitter_state - 2 * self.rpm_jitter

    def _apply_fans(self) -> None:
        if self.regs[REG_MANUAL] == MANUAL_ON:
            lvl_cpu = self.regs[REG_LVL_CPU]
            lvl_gpu = self.regs[REG_LVL_GPU]
            cpu = min(9999, lvl_cpu * 100 + self._wobble())
            gpu = min(9999, lvl_gpu * 100 + self._wobble())
            rate_cpu = round(lvl_cpu * 100 / LEVEL_MAX)
            rate_gpu = round(lvl_gpu * 100 / LEVEL_MAX)
        else:
            cpu, gpu = self.auto_cpu_rpm, self.auto_gpu_rpm
            rate_cpu = rate_gpu = 0
        self.regs[REG_TACH_CPU] = cpu & 0xFF
        self.regs[REG_TACH_CPU + 1] = (cpu >> 8) & 0xFF
        self.regs[REG_TACH_GPU] = gpu & 0xFF
        self.regs[REG_TACH_GPU + 1] = (gpu >> 8) & 0xFF
        self.regs[REG_RATE_R_CPU] = rate_cpu & 0xFF
        self.regs[REG_RATE_R_GPU] = rate_gpu & 0xFF
        self.regs[REG_RATE_W_CPU] = rate_cpu & 0xFF
        self.regs[REG_RATE_W_GPU] = rate_gpu & 0xFF

    def _tick(self) -> None:
        if self.regs[REG_MANUAL] == MANUAL_ON and self._deadline is not None:
            remaining = self._deadline - self._now()
            if remaining <= 0:
                # Firmware failsafe: countdown expired -> back to BIOS auto.
                self.regs[REG_MANUAL] = MANUAL_OFF
                self.regs[REG_COUNTDOWN] = 0x00
                self._deadline = None
            else:
                fw_seconds = remaining * self.time_scale
                self.regs[REG_COUNTDOWN] = max(0, min(255, int(fw_seconds)))
        self._apply_fans()

    # -- backend interface ---------------------------------------------------
    def read(self, offset: int) -> int:
        _check(offset)
        self._tick()
        return self.regs[offset]

    def write(self, offset: int, value: int) -> None:
        _check(offset, value)
        self._tick()
        if offset == REG_MANUAL:
            self.regs[offset] = value
            if value == MANUAL_ON:
                if self._deadline is None or self._deadline <= self._now():
                    self._deadline = self._now() + 120.0 / self.time_scale
            else:
                self._deadline = None
        elif offset == REG_COUNTDOWN:
            self.regs[offset] = value
            if self.regs[REG_MANUAL] == MANUAL_ON:
                self._deadline = self._now() + value / self.time_scale
        elif offset in (REG_LVL_CPU, REG_LVL_GPU):
            self.regs[offset] = min(value, LEVEL_MAX)
        elif offset in (REG_RATE_W_CPU, REG_RATE_W_GPU):
            # Some tools drive the rate (%) regs; the EC derives levels.
            self.regs[offset] = value
            lvl = round(min(value, 100) * LEVEL_MAX / 100)
            self.regs[REG_LVL_CPU if offset == REG_RATE_W_CPU else REG_LVL_GPU] = lvl
        else:
            self.regs[offset] = value
        self._tick()
