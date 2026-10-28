# SPDX-License-Identifier: GPL-3.0-or-later
"""Simulator-backed tests. No hardware needed. Run from the project root:
  python3 -m unittest discover -s tests -v
"""
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omen88d2 import calibrate as cal_mod
from omen88d2.config import Config, ConfigError, interp, load as load_config
from omen88d2.control import Controller
from omen88d2.daemon import EXIT_EMERGENCY, EXIT_OK, EXIT_TEMPFAIL, run_loop
from omen88d2.fakeec import FakeEC
from omen88d2.probe import gather, verify
from omen88d2.temps import Temps


def scripted_temps(*pairs):
    """get_temps() callable yielding (cpu, gpu) then repeating the last."""
    it = iter(pairs)
    last = pairs[-1]

    def gen():
        nonlocal last
        try:
            last = next(it)
        except StopIteration:
            pass
        return Temps(cpu=last[0], gpu=last[1], cpu_source="t", gpu_source="t")
    return gen


class TestCurve(unittest.TestCase):
    PTS = [(40.0, 6), (60.0, 18), (80.0, 40)]

    def test_clamp_ends(self):
        self.assertEqual(interp(self.PTS, 20.0), 6)
        self.assertEqual(interp(self.PTS, 100.0), 40)

    def test_exact_points(self):
        self.assertEqual(interp(self.PTS, 60.0), 18)

    def test_midpoint(self):
        self.assertEqual(interp(self.PTS, 50.0), 12)  # halfway 6->18
        self.assertEqual(interp(self.PTS, 70.0), 29)  # halfway 18->40

    def test_config_validation(self):
        with self.assertRaises(ConfigError):
            load_config("/nonexistent.toml")
        cfg = Config()
        cfg.cpu_curve = [(50.0, 10), (40.0, 6)]  # descending
        # target_levels sorts implicitly via interp; explicit parse check:
        from omen88d2.config import _parse_curve
        with self.assertRaises(ConfigError):
            _parse_curve([[50, 10], [40, 6]], "cpu_curve")
        with self.assertRaises(ConfigError):
            _parse_curve([[50, 99]], "cpu_curve")


class TestFakeEC(unittest.TestCase):
    def test_manual_flow_and_tachs(self):
        ec = FakeEC()
        self.assertFalse(ec.manual)
        self.assertEqual(ec.read(0x62), 0x00)
        ec.write(0x34, 20)
        ec.write(0x35, 30)
        ec.write(0x62, 0x06)
        self.assertTrue(ec.manual)
        lo, hi = ec.read(0xB0), ec.read(0xB1)
        self.assertEqual((hi << 8) | lo, 2000)
        lo, hi = ec.read(0xB2), ec.read(0xB3)
        self.assertEqual((hi << 8) | lo, 3000)
        # rate mirrors track levels (20/55->36%, 30/55->55%)
        self.assertEqual(ec.read(0x2E), round(20 * 100 / 55))
        self.assertEqual(ec.read(0x2F), round(30 * 100 / 55))

    def test_countdown_failsafe(self):
        ec = FakeEC(time_scale=500.0)
        ec.write(0x62, 0x06)
        ec.write(0x63, 120)  # 120 firmware-s -> 0.24 wall-s
        self.assertTrue(ec.manual)
        import time
        time.sleep(0.4)
        self.assertFalse(ec.manual)  # EC reverted by itself
        self.assertEqual(ec.read(0x62), 0x00)

    def test_keepalive_prevents_revert(self):
        ec = FakeEC(time_scale=50.0)
        ec.write(0x62, 0x06)
        import time
        for _ in range(5):
            ec.write(0x63, 120)
            time.sleep(0.2)
        self.assertTrue(ec.manual)


class TestController(unittest.TestCase):
    def test_enable_disable(self):
        ec = FakeEC()
        ctrl = Controller(ec)
        st = ctrl.set_manual(True)
        self.assertEqual(st["mode"], "manual")
        st = ctrl.set_levels(25, 26)
        self.assertEqual((st["level_cpu"], st["level_gpu"]), (25, 26))
        self.assertEqual(st["rpm_cpu"], 2500)
        st = ctrl.restore_auto()
        self.assertEqual(st["mode"], "auto(bios)")
        self.assertEqual(st["rpm_cpu"], 1250)  # back to auto baseline

    def test_seeds_from_current_tach(self):
        ec = FakeEC(auto_cpu_rpm=2200, auto_gpu_rpm=2100)
        ctrl = Controller(ec)
        st = ctrl.set_manual(True)
        self.assertEqual(st["level_cpu"], 22)  # no fan jump on takeover
        ctrl.restore_auto()

    def test_keepalive(self):
        ec = FakeEC(time_scale=100.0)
        ctrl = Controller(ec)
        ctrl.set_manual(True)
        import time
        time.sleep(0.3)  # ~30 firmware-s elapse
        self.assertTrue(ctrl.keepalive(below=100, set_to=120))
        self.assertGreater(ctrl.status()["countdown"], 100)
        ctrl.restore_auto()


class TestDaemon(unittest.TestCase):
    def _cfg(self):
        cfg = Config()
        cfg.poll_interval = 0.05
        cfg.keepalive_below = 100
        cfg.keepalive_set = 120
        cfg.log_every = 1000
        cfg.max_step_up = 55
        cfg.max_step_down = 55
        return cfg

    def test_follows_curve_then_restores(self):
        ec = FakeEC(time_scale=20.0)
        ctrl = Controller(ec)
        cfg = self._cfg()
        # 45C->~8, 75C->~34, back to 50C->10
        temps = scripted_temps((45, 45), (75, 75), (75, 75), (50, 50))
        rc = run_loop(ctrl, cfg, temps, threading.Event(), max_iterations=4)
        self.assertEqual(rc, EXIT_OK)
        self.assertFalse(ec.manual)  # restored to BIOS
        self.assertEqual(ec.read(0x62), 0x00)

    def test_curve_values(self):
        cfg = Config()
        self.assertEqual(cfg.target_levels(40, 40), (6, 6))
        self.assertEqual(cfg.target_levels(95, 95), (52, 52))
        lo, hi = cfg.target_levels(65, 65)
        self.assertTrue(18 < lo < 28)

    def test_critical_forces_max(self):
        ec = FakeEC(time_scale=20.0)
        ctrl = Controller(ec)
        cfg = self._cfg()
        cfg.critical_temp = 90.0
        cfg.emergency_temp = 105.0
        temps = scripted_temps((95, 60))
        run_loop(ctrl, cfg, temps, threading.Event(), max_iterations=2)
        # levels were forced to max during the run (then restored to auto)
        self.assertFalse(ec.manual)

    def test_temp_failure_releases(self):
        ec = FakeEC(time_scale=20.0)
        ctrl = Controller(ec)
        cfg = self._cfg()
        cfg.temp_fail_max = 2

        from omen88d2.temps import TempError

        def dead():
            raise TempError("no sensors")
        rc = run_loop(ctrl, cfg, dead, threading.Event(), max_iterations=10)
        self.assertEqual(rc, EXIT_TEMPFAIL)
        self.assertFalse(ec.manual)

    def test_emergency_releases(self):
        ec = FakeEC(time_scale=20.0)
        ctrl = Controller(ec)
        cfg = self._cfg()
        cfg.emergency_temp = 100.0
        temps = scripted_temps((101, 60))
        rc = run_loop(ctrl, cfg, temps, threading.Event(), max_iterations=5)
        self.assertEqual(rc, EXIT_EMERGENCY)
        self.assertFalse(ec.manual)


class TestCalibrate(unittest.TestCase):
    def test_pass_on_simulator(self):
        ec = FakeEC(time_scale=200.0)
        ctrl = Controller(ec)
        passed, report = cal_mod.run(ctrl, settle=0.3, cd_check_sleep=0.2)
        self.assertTrue(passed, report)
        self.assertIn("CALIBRATION: PASS", report)
        self.assertFalse(ec.manual)


class TestProbe(unittest.TestCase):
    def test_report_renders(self):
        ec = FakeEC()
        data = gather(ec)
        report = "\n".join(verify(data))
        self.assertIn("0xB0", report)
        from omen88d2.probe import format_report
        text = format_report(data, verify(data))
        self.assertIn("full EC dump", text)
        self.assertIn("1250", text)  # auto baseline tach visible


if __name__ == "__main__":
    unittest.main()
