# Changelog

All notable changes to omen88d2. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0] - 2026-10-28

First release: fan control with per-fan curves for the HP OMEN 15-en1xxx
(DMI board `88D2`) on Linux. Userspace only — no kernel module to build;
talks to the EC through the stock `ec_sys` module.

### Added

- Curve-following daemon (`auto`): separate CPU/GPU `[tempC, level]` curves
  from a TOML config, linear interpolation, smooth spin-up/down steps,
  min/max level clamps, systemd unit included.
- `manual`: hold fixed per-fan levels (0–55, ~100 RPM each) with timed
  auto-release.
- `calibrate`: OmenMon-style sweep (levels 8→55) verifying tach response,
  a live firmware countdown, and return-to-idle, with PASS/FAIL verdict.
- `probe` (fully read-only): environment report + full EC dump + map checks.
- `status`, `monitor` (read-only), `restore` (instant hand-back to BIOS).
- Temp sources: hwmon/k10temp, `nvidia-smi`, EC mirrors `0x57`/`0x58`,
  with conservative fallbacks.
- FakeEC simulator + `--dry-run` for every command and `auto --demo`
  scripted-temp mode: the whole tool runs without hardware.
- Simulator-backed test suite (17 tests, stdlib `unittest` only).
- `install.sh` (ec_sys setup, install, systemd unit), Arch `PKGBUILD`.

### Safety

- Runs only on DMI board `88D2` (override requires explicit `--force-board`).
- Only the five documented fan registers (`0x62`, `0x63`, `0x34`, `0x35`
  + mirrors) are ever written; all else is read-only.
- Firmware countdown keepalive: if the process dies, the EC returns the
  fans to BIOS auto by itself.
- Watchdogs: critical temp forces max fans; emergency temp, dead sensors,
  or unresponsive EC release to BIOS and exit; every exit path restores
  BIOS auto (verified by readback).
- Single-instance lock for all control commands.

### Notes

- Validated against the simulator (seeded with a real 88D2 EC dump) —
  real-hardware calibration on a 15-en1xxx is still pending. Run
  `probe` → `calibrate --confirm` → `auto`, in that order.
- EC map sources: OmenMon-Reborn issue #181 (88D2 auto-calibration dumps)
  and pankha's 88D2-tested driver.
