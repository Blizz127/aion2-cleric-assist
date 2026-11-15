"""Private-package checks in an isolated fixture; no network, portals, or real owner files."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest

from tools.build_private import check_deployment_inventory

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = ("aion2_assist", "kwin_script", "tests", "tools")
FILES = ("README.md", "HANDOFF.md", "deploy-alienware.sh", "requirements.txt", "config.example.json",
         "regions.example.json", "config.alienware-draft.json", "regions.alienware-2560x1600.draft.json")


class PrivatePackage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.fixture = Path(cls.temporary.name) / "source"
        cls.fixture.mkdir()
        for name in DIRECTORIES:
            shutil.copytree(ROOT / name, cls.fixture / name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.bak*"))
        for name in FILES:
            shutil.copy2(ROOT / name, cls.fixture / name)
        # Synthetic marker files, never real captures or owner configuration.
        for name in ("config.json", "profile.json", "LIVE-APPROVED", "progress.jsonl", "README.md.bak-test",
                     "shots/private-marker.txt", ".venv/private-marker.txt"):
            path = cls.fixture / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic private packaging marker\n")
        cls.out = Path(cls.temporary.name) / "artifacts"
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        env.pop("PYTHONPATH", None)
        result = subprocess.run([sys.executable, "tools/build_private.py", "--out", str(cls.out)],
                                cwd=cls.fixture, env=env, capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        cls.receipt = json.loads(result.stdout)
        cls.artifact = Path(cls.receipt["artifact"])
        with tarfile.open(cls.artifact) as tar:
            tar.extractall(Path(cls.temporary.name) / "unpacked", filter="data")
        cls.package = next((Path(cls.temporary.name) / "unpacked").iterdir())
        cls.manifest = json.loads((cls.package / "BUILD.json").read_text())

    def test_archive_contains_documented_entrypoints_and_deploys_from_its_own_inventory(self):
        for name in ("HANDOFF.md", "deploy-alienware.sh"):
            self.assertTrue((self.package / name).is_file(), name)
            self.assertIn(name, self.manifest["source_files"])
        self.assertTrue((self.package / "deploy-alienware.sh").stat().st_mode & stat.S_IXUSR)
        result = subprocess.run(["./deploy-alienware.sh", "--check"], cwd=self.package,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(result.stdout.splitlines()), set(FILES + DIRECTORIES))
        for name in result.stdout.splitlines():
            self.assertTrue((self.package / name).exists(), name)

    def test_private_files_are_excluded_and_manifest_matches_extracted_payload(self):
        actual = {str(p.relative_to(self.package)) for p in self.package.rglob("*") if p.is_file()}
        self.assertEqual(actual, set(self.manifest["source_files"]) | {"BUILD.json"})
        self.assertEqual({p.name for p in self.package.iterdir()}, set(FILES + DIRECTORIES) | {"BUILD.json"})
        for name, digest in self.manifest["source_files"].items():
            self.assertEqual(hashlib.sha256((self.package / name).read_bytes()).hexdigest(), digest, name)
        self.assertFalse(self.manifest["live_verified"])
        self.assertFalse(self.manifest["published"])
        self.assertEqual(hashlib.sha256(self.artifact.read_bytes()).hexdigest(), self.receipt["sha256"])
        self.assertEqual(self.receipt["smoke_checks"], 9)
        self.assertTrue(any(c["argv"] == ["sh", "./deploy-alienware.sh", "--check"]
                            for c in self.manifest["smoke_checks"]))

    def test_deployment_inventory_refuses_missing_or_extra_files(self):
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            for name in DIRECTORIES:
                (stage / name).mkdir()
            for name in FILES:
                (stage / name).write_text("fixture\n")
            script = stage / "deploy-alienware.sh"
            listing = "\n".join(FILES + DIRECTORIES)
            script.write_text("#!/bin/sh\ncat <<'FILES'\n" + listing + "\nFILES\n")
            (stage / "HANDOFF.md").unlink()
            with self.assertRaisesRegex(RuntimeError, "missing packaged files"):
                check_deployment_inventory(stage, os.environ)
            (stage / "HANDOFF.md").write_text("fixture\n")
            script.write_text("#!/bin/sh\ncat <<'FILES'\n" + listing + "\nconfig.json\nFILES\n")
            with self.assertRaisesRegex(RuntimeError, "differs from packaged source"):
                check_deployment_inventory(stage, os.environ)


if __name__ == "__main__":
    unittest.main()
