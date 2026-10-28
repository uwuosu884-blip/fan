# SPDX-License-Identifier: GPL-3.0-or-later
"""Curve-following daemon: temps -> per-fan levels, with watchdogs.

Safety behaviour:
* temp sensors failing N times in a row -> release to BIOS, exit 1
  (systemd may restart us; until then the firmware is in charge).
* critical temp -> force both fans to max_level, keep running, loud logs.
* emergency temp -> release to BIOS, exit 2 (firmware + kernel thermal
  protection take over).
* any exit path (signal, exception, max_iterations) restores BIOS auto.
* if the process is SIGKILLed, the EC countdown expires on its own and the
  firmware takes over anyway.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from .config import Config
from .control import Controller
from .ec import ECError
from .temps import TempError, Temps

log = logging.getLogger("omen88d2")

EXIT_OK = 0
EXIT_TEMPFAIL = 1
EXIT_EMERGENCY = 2


def _approach(cur: int, target: int, up: int, down: int) -> int:
    if target > cur:
        return min(target, cur + up)
    if target < cur:
        return max(target, cur - down)
    return cur


def run_loop(ctrl: Controller, cfg: Config,
             get_temps: Callable[[], Temps],
             stop: threading.Event,
             max_iterations: int | None = None) -> int:
    st = ctrl.set_manual(True)
    cur_cpu, cur_gpu = st["level_cpu"], st["level_gpu"]
    log.info("daemon started: poll=%.1fs curves cpu=%s gpu=%s",
             cfg.poll_interval, cfg.cpu_curve, cfg.gpu_curve)
    failures = 0
    polls = 0
    last_crit_log = 0.0
    try:
        while not stop.is_set():
            if max_iterations is not None and polls >= max_iterations:
                break
            polls += 1
            try:
                temps = get_temps()
            except (TempError, ECError, OSError) as e:
                failures += 1
                log.warning("temp read failed (%d/%d): %s",
                            failures, cfg.temp_fail_max, e)
                if failures >= cfg.temp_fail_max:
                    log.error("sensors dead, releasing fans to BIOS")
                    return EXIT_TEMPFAIL
                stop.wait(cfg.poll_interval)
                continue
            failures = 0

            hottest = max(temps.cpu, temps.gpu)
            if hottest >= cfg.emergency_temp:
                log.critical("EMERGENCY %.1fC >= %.1fC, releasing to BIOS",
                             hottest, cfg.emergency_temp)
                return EXIT_EMERGENCY
            if hottest >= cfg.critical_temp:
                now = time.monotonic()
                if now - last_crit_log > 30:
                    log.error("CRITICAL %.1fC >= %.1fC, forcing max fans",
                              hottest, cfg.critical_temp)
                    last_crit_log = now
                tgt_cpu = tgt_gpu = cfg.max_level
            else:
                tgt_cpu, tgt_gpu = cfg.target_levels(temps.cpu, temps.gpu)

            cur_cpu = _approach(cur_cpu, tgt_cpu, cfg.max_step_up, cfg.max_step_down)
            cur_gpu = _approach(cur_gpu, tgt_gpu, cfg.max_step_up, cfg.max_step_down)
            try:
                st = ctrl.set_levels(cur_cpu, cur_gpu)
                ctrl.keepalive(cfg.keepalive_below, cfg.keepalive_set)
            except (ECError, OSError) as e:
                failures += 1
                log.warning("EC write failed (%d/%d): %s",
                            failures, cfg.temp_fail_max, e)
                if failures >= cfg.temp_fail_max:
                    log.error("EC unresponsive, releasing fans to BIOS")
                    return EXIT_TEMPFAIL
                stop.wait(cfg.poll_interval)
                continue

            if polls == 1 or polls % cfg.log_every == 0:
                log.info("cpu %.0fC (%s) gpu %.0fC (%s) -> lvl %d/%d, "
                         "rpm %d/%d, cd %ds",
                         temps.cpu, temps.cpu_source, temps.gpu, temps.gpu_source,
                         st["level_cpu"], st["level_gpu"],
                         st["rpm_cpu"], st["rpm_gpu"], st["countdown"])
            else:
                log.debug("cpu %.1f gpu %.1f -> lvl %d/%d rpm %d/%d cd %d",
                          temps.cpu, temps.gpu, st["level_cpu"], st["level_gpu"],
                          st["rpm_cpu"], st["rpm_gpu"], st["countdown"])
            stop.wait(cfg.poll_interval)
    finally:
        try:
            ctrl.restore_auto()
        except Exception as e:  # never mask the real exit path
            log.error("restore-to-auto failed: %s", e)
    return EXIT_OK
