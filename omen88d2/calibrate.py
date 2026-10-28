# SPDX-License-Identifier: GPL-3.0-or-later
"""Interactive EC-map verification (OmenMon-style auto-calibration).

Sweeps several fan levels, checks that both tachometers respond, verifies
the countdown timer is live, then returns to BIOS auto and checks the fans
come back down (return-to-idle). Anything unexpected -> FAIL verdict, and
the fans are always handed back to the BIOS at the end.

This WRITES to the EC (manual switch + setpoints). It only touches the five
documented fan registers and stays within level 0-55.
"""
from __future__ import annotations

import datetime
import logging
import time

from .control import Controller, LEVEL_MAX
from .ec import ECError

log = logging.getLogger("omen88d2")

SWEEP_LEVELS = (8, 20, 32, 44, 55)
MIN_RESPONSE_RPM = 400   # sweep max-min must exceed this, else FAIL
IDLE_TOLERANCE_RPM = 300  # return-to-idle slack vs baseline


def _sample_tachs(ctrl: Controller, n: int = 3, gap: float = 0.4) -> tuple[int, int]:
    cpu = gpu = 0
    for _ in range(n):
        st = ctrl.status()
        cpu += st["rpm_cpu"]
        gpu += st["rpm_gpu"]
        time.sleep(gap)
    return round(cpu / n), round(gpu / n)


def run(ctrl: Controller, settle: float = 6.0,
        cd_check_sleep: float = 2.0) -> tuple[bool, str]:
    """Run the sweep. Returns (passed, report_text). Always restores auto."""
    lines: list[str] = []
    ok = True

    def say(s: str) -> None:
        lines.append(s)
        log.info(s)

    say(f"omen88d2 calibration — {datetime.datetime.now():%Y-%m-%d %H:%M:%S}")
    try:
        # 1. Baseline in BIOS auto.
        ctrl.restore_auto()
        time.sleep(2.0)
        st0 = ctrl.status()
        base_cpu, base_gpu = _sample_tachs(ctrl)
        say(f"baseline (BIOS auto): cpu={base_cpu} rpm gpu={base_gpu} rpm, "
            f"ec_temps={st0['ec_temp_cpu']}/{st0['ec_temp_gpu']}C")

        # 2. Enter manual, verify the switch + a live countdown.
        st = ctrl.set_manual(True, start_cpu=8, start_gpu=8)
        say(f"manual entered: 0x62=0x{st['manual_raw']:02X} countdown={st['countdown']}s")
        cd1 = ctrl.status()["countdown"]
        time.sleep(cd_check_sleep)
        cd2 = ctrl.status()["countdown"]
        if cd2 < cd1:
            say(f"countdown live: {cd1}s -> {cd2}s (failsafe confirmed)")
        else:
            say(f"WARNING: countdown did not tick ({cd1}s -> {cd2}s)")
            ok = False

        # 3. Sweep.
        say("level | cpu_rpm | gpu_rpm")
        results: list[tuple[int, int, int]] = []
        for lvl in SWEEP_LEVELS:
            ctrl.set_levels(lvl, lvl)
            waited = 0.0
            while waited < settle:  # keepalive while settling
                time.sleep(min(1.0, settle - waited))
                ctrl.keepalive()
                waited += 1.0
            cpu, gpu = _sample_tachs(ctrl)
            results.append((lvl, cpu, gpu))
            say(f"  {lvl:3d} | {cpu:7d} | {gpu:7d}")
        cpu_span = max(r[1] for r in results) - min(r[1] for r in results)
        gpu_span = max(r[2] for r in results) - min(r[2] for r in results)
        say(f"response span: cpu={cpu_span} rpm gpu={gpu_span} rpm "
            f"(need >{MIN_RESPONSE_RPM})")
        if cpu_span < MIN_RESPONSE_RPM or gpu_span < MIN_RESPONSE_RPM:
            say("FAIL: tachometers did not follow the setpoints")
            ok = False
        # Saturation at the top step (like OmenMon's 70->100% note) is fine,
        # a big *drop* at higher levels is not.
        for (l0, c0, g0), (l1, c1, g1) in zip(results, results[1:]):
            if c1 < c0 - 400 or g1 < g0 - 400:
                say(f"WARNING: rpm dropped from level {l0} to {l1} "
                    f"(cpu {c0}->{c1}, gpu {g0}->{g1})")
                ok = False
        if max(r[1] for r in results) > LEVEL_MAX * 100 + 1500:
            say("WARNING: cpu tach far above 5500rpm — map may be off")
            ok = False
    except ECError as e:
        say(f"FAIL: EC error: {e}")
        ok = False
    finally:
        # 4. Always hand control back, then verify return-to-idle.
        try:
            ctrl.restore_auto()
            time.sleep(4.0)
            cpu, gpu = _sample_tachs(ctrl, n=2)
            say(f"after release: cpu={cpu} rpm gpu={gpu} rpm "
                f"(baseline was {base_cpu}/{base_gpu})")
            worst = max(r[1] for r in results) if results else 0
            if cpu > base_cpu + IDLE_TOLERANCE_RPM and cpu > worst * 0.5:
                say("WARNING: cpu fan did not come back down")
                ok = False
        except ECError as e:
            say(f"FAIL: could not restore auto: {e}")
            ok = False

    say("CALIBRATION: " + ("PASS — map matches your machine"
                           if ok else "FAIL — do not run the daemon, send this log"))
    return ok, "\n".join(lines) + "\n"
