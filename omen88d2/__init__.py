# SPDX-License-Identifier: GPL-3.0-or-later
"""omen88d2 — fan control for HP OMEN 15-en1xxx (board 88D2) on Linux.

Userspace tool driving the Embedded Controller directly via ec_sys:
manual-mode switch, per-fan setpoints, countdown keepalive, tachometer
and temperature reads. See README.md for the register map and safety notes.
"""

__version__ = "0.1.0"
__supported_board__ = "88D2"
