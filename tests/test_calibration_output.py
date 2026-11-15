"""New-profile publication and CLI refusals with synthetic files only."""
import contextlib
import errno
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from aion2_assist import cli
from aion2_assist.ingest import fixtures
from aion2_assist.png import load_frame
from aion2_assist.profile import load_profile, save_profile
from aion2_assist.synth import synthetic_profile

PROFILE = synthetic_profile()


class ProfilePublication(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.out = self.folder / "profile.json"

    def assert_no_stage(self):
        self.assertEqual(list(self.folder.glob(".aion2-profile-*.tmp")), [])

    def test_new_profile_is_complete_before_publication_and_roundtrips(self):
        link = os.link
        def publish(source, destination):
            self.assertFalse(self.out.exists())
            self.assertEqual(load_profile(source).hash, PROFILE.hash)
            return link(source, destination)
        with patch("aion2_assist.profile.os.link", side_effect=publish):
            save_profile(PROFILE, self.out)
        self.assertEqual(load_profile(self.out).hash, PROFILE.hash)
        self.assert_no_stage()

    def test_existing_file_and_directory_are_unchanged(self):
        self.out.write_bytes(b"synthetic existing-file sentinel\n")
        with self.assertRaises(FileExistsError):
            save_profile(PROFILE, self.out)
        self.assertEqual(self.out.read_bytes(), b"synthetic existing-file sentinel\n")
        directory = self.folder / "existing-directory"
        directory.mkdir()
        with self.assertRaises(FileExistsError):
            save_profile(PROFILE, directory)
        self.assertTrue(directory.is_dir())
        self.assert_no_stage()

    def test_existing_and_dangling_symlinks_are_unchanged(self):
        for exists in (True, False):
            with self.subTest(target_exists=exists):
                target = self.folder / f"target-{exists}.json"
                dest = self.folder / f"link-{exists}.json"
                if exists:
                    target.write_text("synthetic target sentinel\n")
                dest.symlink_to(target.name)
                with self.assertRaises(FileExistsError):
                    save_profile(PROFILE, dest)
                self.assertTrue(dest.is_symlink())
                self.assertEqual(os.readlink(dest), target.name)
                self.assertEqual(target.exists(), exists)
                if exists:
                    self.assertEqual(target.read_text(), "synthetic target sentinel\n")
                self.assert_no_stage()

    def test_destination_created_during_save_wins_without_being_removed(self):
        link = os.link
        def concurrent_writer(source, destination):
            self.out.write_text("synthetic concurrent writer\n")
            return link(source, destination)
        with patch("aion2_assist.profile.os.link", side_effect=concurrent_writer):
            with self.assertRaises(FileExistsError):
                save_profile(PROFILE, self.out)
        self.assertEqual(self.out.read_text(), "synthetic concurrent writer\n")
        self.assert_no_stage()

    def test_serialization_failure_creates_nothing(self):
        with self.assertRaises(TypeError):
            save_profile(SimpleNamespace(data={"not_json": object()}), self.out)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_partial_staged_write_failure_publishes_nothing(self):
        create = tempfile.NamedTemporaryFile
        def fail_after_some_bytes(**kwargs):
            staged = create(**kwargs)
            write = staged.write
            def fail(data):
                write(data[:20])
                raise OSError(errno.ENOSPC, "synthetic disk-full failure")
            staged.write = fail
            return staged
        with patch("aion2_assist.profile.tempfile.NamedTemporaryFile", side_effect=fail_after_some_bytes):
            with self.assertRaises(OSError):
                save_profile(PROFILE, self.out)
        self.assertFalse(self.out.exists())
        self.assert_no_stage()

    def test_fsync_or_link_failure_publishes_nothing(self):
        for operation in ("fsync", "link"):
            with self.subTest(operation=operation):
                with patch(f"aion2_assist.profile.os.{operation}", side_effect=OSError("synthetic I/O failure")):
                    with self.assertRaises(OSError):
                        save_profile(PROFILE, self.out)
                self.assertFalse(self.out.exists())
                self.assert_no_stage()


class CalibrationCli(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.seed = Path(temporary.name) / "synthetic"
        fixtures(cls.seed)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name) / "bundle"
        shutil.copytree(self.seed, self.folder)
        self.spec = self.folder / "regions.json"
        self.out = self.folder / "new-profile.json"
        self.shots = [f"{role}={self.folder / ('synthetic-' + role + '.png')}"
                      for role in ("normal", "cooldown", "no_target")]

    def run_cli(self, shots=None, out=None):
        args = ["calibrate", "--regions", str(self.spec), "--out", str(out or self.out)]
        for shot in self.shots if shots is None else shots:
            args += ["--shot", shot]
        stdout, stderr = io.StringIO(), io.StringIO()
        # Exercise the supported stdlib path regardless of optional Pillow installation.
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), patch(
                "aion2_assist.cli.load_frame", side_effect=lambda path: load_frame(path, force_stdlib=True)):
            code = cli.main(args)
        return code, stdout.getvalue(), stderr.getvalue()

    def assert_refusal(self, result, message="calibration refused"):
        code, stdout, stderr = result
        self.assertEqual(code, 2, (stdout, stderr))
        self.assertEqual(stdout, "")
        self.assertIn(message, stderr)
        self.assertNotIn("Traceback", stderr)

    def test_new_output_and_image_path_containing_equals_work(self):
        source = self.folder / "synthetic-normal.png"
        alternate = self.folder / "synthetic=normal.png"
        shutil.copyfile(source, alternate)
        code, stdout, stderr = self.run_cli([f"normal={alternate}", *self.shots[1:]])
        self.assertEqual(code, 0, stderr)
        self.assertIn(load_profile(self.out).hash, stdout)

    def test_configured_custom_state_labels_remain_supported(self):
        spec = json.loads(self.spec.read_text())
        spec["regions"]["states"] = {"custom_dialog": spec["regions"]["states"]["dialog"]}
        self.spec.write_text(json.dumps(spec))
        code, _, stderr = self.run_cli([*self.shots, f"custom_dialog={self.folder / 'synthetic-dialog.png'}"])
        self.assertEqual(code, 0, stderr)
        self.assertEqual(set(load_profile(self.out)["regions"]["states"]), {"custom_dialog"})

    def test_existing_profile_regions_and_png_outputs_are_preserved(self):
        self.out.write_text("synthetic previous-profile sentinel\n")
        for path in (self.out, self.spec, self.folder / "synthetic-normal.png"):
            with self.subTest(path=path.name):
                before = path.read_bytes()
                self.assert_refusal(self.run_cli(out=path), "choose a new --out path")
                self.assertEqual(path.read_bytes(), before)

    def test_malformed_duplicate_and_unknown_shot_labels_are_refused(self):
        for shots, message in ((["normal"], "NAME=PNG"), (["=file.png"], "NAME=PNG"),
                               (["normal="], "NAME=PNG"),
                               ([*self.shots, self.shots[0]], "duplicate screenshot label"),
                               ([*self.shots, "typo=file.png"], "unknown screenshot label")):
            with self.subTest(shots=shots):
                self.assert_refusal(self.run_cli(shots), message)
                self.assertFalse(self.out.exists())

    def test_invalid_regions_missing_files_and_truncated_png_are_clean_refusals(self):
        original = self.spec.read_bytes()
        for data in (b"{", b"[]", b'{"regions":[]}', b'{"regions":{"states":[]}}',
                     b'{"frame_size":1,"regions":{}}'):
            with self.subTest(data=data):
                self.spec.write_bytes(data)
                self.assert_refusal(self.run_cli())
                self.assertFalse(self.out.exists())
        self.spec.write_bytes(original)
        self.assert_refusal(self.run_cli(["normal=DOES-NOT-EXIST.png", *self.shots[1:]]))
        (self.folder / "synthetic-normal.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00")
        self.assert_refusal(self.run_cli())
        self.assertFalse(self.out.exists())

    def test_output_failure_has_clear_error_and_no_final_profile(self):
        self.assert_refusal(self.run_cli(out=self.folder / "missing-parent/profile.json"))
        with patch("aion2_assist.profile.os.fsync", side_effect=OSError("synthetic disk failure")):
            self.assert_refusal(self.run_cli(), "synthetic disk failure")
        self.assertFalse(self.out.exists())
        self.assertEqual(list(self.folder.glob(".aion2-profile-*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
