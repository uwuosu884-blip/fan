# SPDX-License-Identifier: GPL-3.0-or-later
"""Low-level Embedded Controller access.

Real hardware goes through the ``ec_sys`` kernel module, which exposes the
256 EC registers as a random-access file at /sys/kernel/debug/ec/ec0/io
(one byte per register; pread/pwrite at the register offset).

For simulation / dry-run / tests see fakeec.py (FakeEC implements the same
interface with the documented 88D2 behaviour).
"""
from __future__ import annotations

import errno
import os
import time

EC_IO_PATH = "/sys/kernel/debug/ec/ec0/io"
EC_SIZE = 256
_MAX_RETRIES = 4


class ECError(Exception):
    """Any failure talking to the Embedded Controller."""


class ECBackend:
    """Abstract EC access. Offsets and values are single bytes (0-255)."""

    name = "abstract"

    def read(self, offset: int) -> int:
        raise NotImplementedError

    def write(self, offset: int, value: int) -> None:
        raise NotImplementedError

    def dump(self) -> bytes:
        return bytes(self.read(o) for o in range(EC_SIZE))

    def close(self) -> None:
        pass


def _check(offset: int, value: int | None = None) -> None:
    if not 0 <= offset < EC_SIZE:
        raise ECError(f"bad EC offset 0x{offset:02X}")
    if value is not None and not 0 <= value <= 0xFF:
        raise ECError(f"bad EC value {value}")


class EcSysBackend(ECBackend):
    """EC access via the ec_sys kernel module (needs root).

    ``readonly=True`` opens O_RDONLY (probe/status/monitor). Writes require
    the module loaded with ``write_support=1``.
    """

    name = "ec_sys"

    def __init__(self, path: str = EC_IO_PATH, readonly: bool = False) -> None:
        self.path = path
        self.readonly = readonly
        if not os.path.exists(path):
            raise ECError(
                f"{path} not found. Load the kernel module first:\n"
                f"  sudo modprobe ec_sys{' write_support=1' if not readonly else ''}\n"
                f"(install.sh sets this up permanently)"
            )
        flags = os.O_RDONLY if readonly else os.O_RDWR
        try:
            self.fd = os.open(path, flags)
        except PermissionError:
            raise ECError("EC access needs root. Re-run with sudo.")
        except FileNotFoundError:
            raise ECError(f"{path} disappeared. Is ec_sys loaded?")
        except OSError as e:
            raise ECError(f"cannot open {path}: {e}")
        # Sanity: the register file must be readable end to end.
        try:
            self.read(0x00)
            self.read(0xFF)
        except ECError as e:
            os.close(self.fd)
            raise ECError(f"{path} is not usable: {e}")

    def _retry(self, what: str, fn):
        # The kernel ACPI layer also talks to the EC; a rare busy/IO hiccup
        # is normal — retry a few times before giving up.
        last: OSError | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                return fn()
            except OSError as e:
                last = e
                if e.errno in (errno.EBUSY, errno.EAGAIN, errno.EIO) and attempt + 1 < _MAX_RETRIES:
                    time.sleep(0.02 * (attempt + 1))
                    continue
                break
        raise ECError(f"EC {what} failed: {last}")

    def read(self, offset: int) -> int:
        _check(offset)
        data = self._retry(f"read 0x{offset:02X}",
                           lambda: os.pread(self.fd, 1, offset))
        if len(data) != 1:
            raise ECError(f"EC read 0x{offset:02X} returned {len(data)} bytes")
        return data[0]

    def write(self, offset: int, value: int) -> None:
        _check(offset, value)
        if self.readonly:
            raise ECError("backend opened read-only, refusing write")
        self._retry(f"write 0x{offset:02X}",
                    lambda: os.pwrite(self.fd, bytes([value]), offset))

    def close(self) -> None:
        try:
            os.close(self.fd)
        except OSError:
            pass
