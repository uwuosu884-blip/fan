# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression test: the launcher must work through an install-style symlink
(/usr/local/bin/omen88d2 -> /opt/omen88d2/bin/omen88d2). Uses --dry-run,
no root or hardware needed."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestLauncherSymlink(unittest.TestCase):
    def test_via_symlink(self):
        tmp = tempfile.mkdtemp(prefix="omen88d2-linktest-")
        try:
            fakeopt = os.path.join(tmp, "opt", "omen88d2")
            shutil.copytree(PROJECT_ROOT, fakeopt,
                            ignore=shutil.ignore_patterns(
                                "__pycache__", ".git", "tests"))
            linkdir = os.path.join(tmp, "usr", "local", "bin")
            os.makedirs(linkdir)
            link = os.path.join(linkdir, "omen88d2")
            target = os.path.join(fakeopt, "bin", "omen88d2")
            self.assertTrue(os.access(target, os.X_OK),
                            "bin/omen88d2 must be executable in the tree")
            os.symlink(target, link)
            # Invoke via the interpreter: the regression under test is
            # symlink (__file__) resolution, not the shebang/exec bit.
            p = subprocess.run([sys.executable, link, "--dry-run", "status"],
                               capture_output=True, text=True, timeout=30)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("mode=", p.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
