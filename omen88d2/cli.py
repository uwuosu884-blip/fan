# SPDX-License-Identifier: GPL-3.0-or-later
"""Command line interface. See README.md for the full guide."""
from __future__ import annotations

import argparse
import datetime
import fcntl
import logging
import os
import signal
import sys
import threading
import time

from . import __supported_board__, __version__
from . import calibrate as cal
from . import daemon as daemon_mod
from . import probe as probe_mod
from .calibrate import run as run_calibration
from .config import ConfigError, load as load_config
from .control import LEVEL_MAX, Controller
from .daemon import run_loop
from .ec import ECBackend, ECError, EcSysBackend
from .fakeec import FakeEC
from .probe import format_report, gather, verify
from .temps import TempError, get_temps

log = logging.getLogger("omen88d2")

LOCK_PATH = "/run/omen88d2.lock"
CONFIG_SEARCH = ("/etc/omen88d2/config.toml", "./config.toml",
                 "./config.example.toml")
EXIT_BOARD = 3
EXIT_LOCKED = 4

_lock_fd: int | None = None


def _take_lock() -> None:
    global _lock_fd
    try:
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o644)
    except OSError:
        fd = os.open("/tmp/omen88d2.lock", os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise SystemExit(f"another omen88d2 control process holds the lock "
                         f"({LOCK_PATH}). Stop it first.")
    try:
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
    except OSError:
        pass
    _lock_fd = fd  # held until process exit


def _read_board() -> str:
    try:
        with open("/sys/class/dmi/id/board_name") as f:
            return f.read().strip()
    except OSError:
        return "unknown"


def _open_ec(args, readonly: bool) -> ECBackend:
    if args.dry_run:
        jitter = 15 if getattr(args, "demo", False) else 0
        ec = FakeEC(time_scale=1.0)
        ec.rpm_jitter = jitter
        log.info("dry-run: using simulated 88D2 EC (no hardware touched)")
        return ec
    if os.geteuid() != 0:
        raise SystemExit("EC access needs root — re-run with sudo "
                         "(or use --dry-run to simulate).")
    board = _read_board()
    if board != __supported_board__ and not args.force_board:
        raise SystemExit(
            f"DMI board is '{board}', this tool targets {__supported_board__} only.\n"
            f"EC layouts differ per board — refusing to run. Override (dangerous!)\n"
            f"with --force-board if you really know what you are doing.")
    if board != __supported_board__:
        log.warning("board override: running on '%s' at your own risk", board)
    return EcSysBackend(readonly=readonly)


def _fmt_status(st: dict) -> str:
    return (f"mode={st['mode']} cd={st['countdown']}s "
            f"lvl={st['level_cpu']}/{st['level_gpu']} "
            f"rpm={st['rpm_cpu']}/{st['rpm_gpu']} "
            f"ecT={st['ec_temp_cpu']}/{st['ec_temp_gpu']}C")


def cmd_probe(args) -> int:
    ec = _open_ec(args, readonly=True)
    try:
        data = gather(ec)
        report = format_report(data, verify(data))
    finally:
        ec.close()
    print(report)
    if args.save:
        with open(args.save, "w") as f:
            f.write(report)
        print(f"saved to {args.save}")
    return 0


def cmd_status(args) -> int:
    ec = _open_ec(args, readonly=True)
    try:
        print(_fmt_status(Controller(ec).status()))
    finally:
        ec.close()
    return 0


def cmd_monitor(args) -> int:
    ec = _open_ec(args, readonly=True)
    ctrl = Controller(ec)
    try:
        while True:
            print(f"{datetime.datetime.now():%H:%M:%S} {_fmt_status(ctrl.status())}",
                  flush=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        ec.close()
    return 0


def _stop_event() -> threading.Event:
    stop = threading.Event()

    def handler(signum, frame):
        log.info("signal %d, shutting down…", signum)
        stop.set()

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)
    return stop


def cmd_manual(args) -> int:
    _take_lock()
    ec = _open_ec(args, readonly=False)
    ctrl = Controller(ec)
    stop = _stop_event()
    deadline = time.monotonic() + args.seconds if args.seconds > 0 else None
    try:
        st = ctrl.set_levels(args.cpu, args.gpu)
        log.info("manual: %s", _fmt_status(st))
        while not stop.is_set():
            if deadline is not None and time.monotonic() >= deadline:
                break
            stop.wait(2.0)
            if stop.is_set():
                break
            ctrl.keepalive()
            log.info(_fmt_status(ctrl.status()))
    finally:
        try:
            ctrl.restore_auto()
        except ECError as e:
            log.error("restore failed: %s", e)
        ec.close()
    return 0


def _demo_temps():
    """Scripted temp ramp for --dry-run demos: 45 -> 95 -> 55C."""
    from .temps import Temps
    t0 = time.monotonic()

    def gen():
        e = time.monotonic() - t0
        phase = (e % 90.0) / 90.0
        cpu = 45 + 50 * (phase if phase < 0.5 else 1.0 - (phase - 0.5) * 1.6)
        return Temps(cpu=cpu, gpu=cpu - 3, cpu_source="demo",
                     gpu_source="demo")
    return gen


def _find_config(path: str | None) -> str | None:
    if path:
        return path
    for cand in CONFIG_SEARCH:
        if os.path.isfile(cand):
            return cand
    return None


def cmd_auto(args) -> int:
    _take_lock()
    try:
        cfg, src = load_config(_find_config(args.config))
    except ConfigError as e:
        raise SystemExit(f"config error: {e}")
    log.info("config: %s", src)
    ec = _open_ec(args, readonly=False)
    if args.demo or args.dry_run and args.demo_temps:
        get = _demo_temps()
    else:
        def get():
            return get_temps(ec)
    stop = _stop_event()
    try:
        rc = run_loop(Controller(ec), cfg, get, stop)
    except (ECError, TempError) as e:
        log.error("%s", e)
        rc = 1
    finally:
        ec.close()
    return rc


def cmd_calibrate(args) -> int:
    if not args.confirm:
        raise SystemExit("calibrate writes to the EC. Re-run with --confirm "
                         "to acknowledge (read README.md safety notes first).")
    _take_lock()
    ec = _open_ec(args, readonly=False)
    try:
        passed, report = run_calibration(Controller(ec), settle=args.settle)
    finally:
        ec.close()
    print(report)
    if args.save:
        with open(args.save, "w") as f:
            f.write(report)
        print(f"saved to {args.save}")
    return 0 if passed else 1


def cmd_restore(args) -> int:
    _take_lock()
    ec = _open_ec(args, readonly=False)
    try:
        Controller(ec).restore_auto()
    finally:
        ec.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="omen88d2",
        description=f"Fan control for HP OMEN 15-en1xxx (board {__supported_board__})")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--dry-run", action="store_true",
                   help="simulate a 88D2 EC, touch no hardware (no root needed)")
    p.add_argument("--force-board", action="store_true",
                   help="run even if DMI board != 88D2 (DANGEROUS)")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("-q", "--quiet", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("probe", help="read-only diagnostics + EC dump (safe)")
    s.add_argument("--save", metavar="FILE")
    s.set_defaults(func=cmd_probe)

    s = sub.add_parser("status", help="one-shot read-only fan status")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("monitor", help="live read-only status loop")
    s.add_argument("--interval", type=float, default=1.0)
    s.set_defaults(func=cmd_monitor)

    s = sub.add_parser("manual", help="hold fixed fan levels until Ctrl+C")
    s.add_argument("--cpu", type=int, required=True, help=f"0-{LEVEL_MAX} (~100rpm each)")
    s.add_argument("--gpu", type=int, required=True, help=f"0-{LEVEL_MAX} (~100rpm each)")
    s.add_argument("--seconds", type=float, default=0, help="auto-release after N s (0=until Ctrl+C)")
    s.set_defaults(func=cmd_manual)

    s = sub.add_parser("auto", help="run the curve-following daemon (foreground)")
    s.add_argument("--config", metavar="FILE", help="TOML config (default: search standard paths)")
    s.add_argument("--demo", action="store_true", help="scripted temp ramp (implies dry-run behaviour)")
    s.add_argument("--demo-temps", action="store_true", help=argparse.SUPPRESS)
    s.set_defaults(func=cmd_auto)

    s = sub.add_parser("calibrate", help="verify the EC map on YOUR machine (writes EC)")
    s.add_argument("--confirm", action="store_true", help="acknowledge EC writes")
    s.add_argument("--settle", type=float, default=6.0, help="seconds to settle per step")
    s.add_argument("--save", metavar="FILE")
    s.set_defaults(func=cmd_calibrate)

    s = sub.add_parser("restore", help="force fans back to BIOS auto")
    s.set_defaults(func=cmd_restore)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    level = logging.DEBUG if args.verbose else (
        logging.WARNING if args.quiet else logging.INFO)
    logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S", level=level)
    if getattr(args, "demo", False):
        args.dry_run = True
        args.demo_temps = True
    try:
        return args.func(args)
    except ECError as e:
        log.error("EC: %s", e)
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
