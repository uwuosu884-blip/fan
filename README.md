# omen88d2 — fan control for HP OMEN 15-en1xxx (board 88D2)

Userspace fan control with **per-fan curves** for the HP OMEN 15-en1xxx
(DMI board `88D2`, e.g. 15-en1003ur / 15-en1004ns) on Linux. No kernel module
to compile: it talks to the Embedded Controller through the stock `ec_sys`
module.

## How it works (the 88D2 EC map)

| EC reg | Name | Meaning |
|---|---|---|
| `0x62` | manual switch | `0x06` = manual, `0x00` = BIOS auto |
| `0x63` | countdown | seconds until the EC auto-reverts to BIOS (failsafe) |
| `0x34` / `0x35` | setpoints | CPU/GPU fan, ~100 RPM per unit, 0–55 |
| `0xB0` / `0xB2` | tachometers | true RPM, 16-bit little-endian |
| `0x57` / `0x58` | temps | CPU/GPU °C (mirror) |
| `0x95` | profile | thermal profile (`0x31` = performance) |

The daemon reads temps (hwmon/k10temp, nvidia-smi, EC mirrors), maps them
through your curves to levels, writes `0x34`/`0x35`, and refreshes the `0x63`
countdown so manual mode stays alive. **If the program ever dies, the
countdown expires and the BIOS takes the fans back by itself.** Only these
five fan registers are ever written; everything else is read-only.

Map sources: OmenMon-Reborn issue #181 (real 88D2 EC dumps at 0/30/70/100%),
its committed 88D2 model entry, and pankha's 88D2-tested driver.

## Safety — read this

- This writes to your laptop's Embedded Controller. The design leans on the
  firmware's own countdown failsafe, caps levels at 55, keeps min-speed
  floors, and releases to BIOS on critical temps / sensor failure / any exit.
  Still: run at your own risk, keep an eye on temps the first days.
- The tool **refuses to run on any board but 88D2** (check:
  `cat /sys/class/dmi/id/board_name`). `--force-board` bypasses this and can
  brick a machine with a different EC layout. Don't.
- Requirements: root, `ec_sys` with `write_support=1` (installer sets it up),
  kernel lockdown off (i.e. Secure Boot off on stock Arch kernels).
- First run order below matters: `probe` (read-only) → `calibrate` (verifies
  YOUR machine matches the map) → `auto`.

## Install

```bash
tar xzf omen88d2-0.1.0.tar.gz && cd omen88d2
sudo ./install.sh                 # + --enable-service to also start at boot
```

or build an Arch package: `makepkg -si` (uses the bundled PKGBUILD).

Needs `lm_sensors` (`sensors`) for the best CPU temps and `nvidia-utils`
(`nvidia-smi`) for GPU temps — otherwise the EC mirrors are used.

## Usage

```bash
sudo omen88d2 probe                       # read-only diagnostics + EC dump
sudo omen88d2 probe --save probe.txt      # save it to paste back for review
sudo omen88d2 status                      # one-shot fan status
sudo omen88d2 monitor                     # live status (Ctrl+C to stop)
sudo omen88d2 calibrate --confirm         # verify map on YOUR hardware (writes EC)
sudo omen88d2 manual --cpu 30 --gpu 25    # hold fixed levels (Ctrl+C releases)
sudo omen88d2 manual --cpu 55 --gpu 55 --seconds 60   # max for 60s, then auto
sudo omen88d2 auto                        # curve daemon, foreground
sudo omen88d2 restore                     # force back to BIOS auto, now
omen88d2 auto --demo                      # no hardware: watch curves on fake temps
```

Curves live in `/etc/omen88d2/config.toml` (see `config.example.toml`).
Enable at boot only after probe+calibrate+auto all look sane:

```bash
sudo systemctl enable --now omen88d2.service
```

Uninstall: `sudo systemctl disable --now omen88d2.service`, delete
`/opt/omen88d2`, `/usr/local/bin/omen88d2`, `/etc/omen88d2`,
`/etc/systemd/system/omen88d2.service`, `/etc/modules-load.d/omen88d2.conf`,
`/etc/modprobe.d/omen88d2.conf`, then `sudo modprobe -r ec_sys` (optional).

## Development / tests

Pure stdlib Python (≥3.11), no dependencies:

```bash
python3 -m unittest discover -s tests -v   # simulator-backed test suite
./bin/omen88d2 --dry-run probe             # everything works against FakeEC
./bin/omen88d2 auto --demo                 # watch the daemon follow fake temps
```

## License

GPL-3.0-or-later.
