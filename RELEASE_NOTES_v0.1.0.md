# omen88d2 v0.1.0 — first release

Fan control with per-fan curves for the HP OMEN 15-en1xxx (DMI board `88D2`,
e.g. 15-en1003ur) on Linux. Userspace only — no kernel module to build.

## Highlights

- **Curve daemon** (`auto`): separate CPU/GPU temp→fan curves, smooth
  ramping, systemd service
- **`calibrate`**: verifies the EC map on *your* machine before the daemon
  ever runs (sweep + return-to-idle + PASS/FAIL)
- **Failsafe design**: firmware countdown returns fans to BIOS auto if the
  program dies; critical/emergency temp + sensor-failure watchdogs; runs
  only on board 88D2
- **Hardware-free testing**: built-in EC simulator, `--dry-run` for every
  command, 17/17 tests passing

See [CHANGELOG.md](CHANGELOG.md) for the full list.

## Install (Arch)

```bash
tar xzf omen88d2-0.1.0.tar.gz && cd omen88d2
sudo ./install.sh
sudo omen88d2 probe                  # read-only — check first
sudo omen88d2 calibrate --confirm    # verify your hardware
sudo omen88d2 auto                   # run the curves
```

Requires: root, Secure Boot off, `lm_sensors` / `nvidia-utils` recommended.
Read the README safety notes first. Validated on the simulator so far —
real-hardware calibration reports welcome as issues (paste `probe` output).

## Assets

- `omen88d2-0.1.0.tar.gz` — release tarball, built deterministically from
  this tag (`git archive` + `gzip -n`; rebuild: `python3 mk_release.py
  --dry-run`). Verify downloads with `sha256sum`.

## Map sources

- OmenMon-Reborn issue #181 (real 88D2 EC dumps + committed model entry)
- pankha (88D2-tested EC driver)
