"""Offline ingest contracts. Every PNG here is synthetic, including source-declaration tests."""
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

from aion2_assist.ingest import fixtures, validate


class Ingest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.seed.cleanup)
        cls.seed_path = Path(cls.seed.name) / "synthetic"
        fixtures(cls.seed_path)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.bundle = self.base / "bundle"
        shutil.copytree(self.seed_path, self.bundle)
        self.manifest_path = self.bundle / "manifest.json"
        self.spec_path = self.bundle / "regions.json"
        self.manifest = json.loads(self.manifest_path.read_text())

    def check(self):
        self.manifest_path.write_text(json.dumps(self.manifest))
        result = validate(self.spec_path, self.manifest_path)
        self.assertFalse(result["owner_evidence_satisfied"])
        self.assertFalse(result["live_verified"])
        self.assertFalse(result["input_sent"])
        return result

    def test_synthetic_roundtrip_and_validation_does_not_write(self):
        def snapshot():
            return {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in self.bundle.rglob("*") if p.is_file()}
        before = snapshot()
        result = validate(self.spec_path, self.manifest_path)
        self.assertEqual(before, snapshot())
        self.assertTrue(result["valid"])
        self.assertTrue(result["calibration_buildable"])
        self.assertTrue(result["evidence_coverage_complete"])
        self.assertTrue(result["synthetic"])
        self.assertIsNone(result["calibrate_argv"])
        self.assertFalse(result["owner_evidence_satisfied"])
        self.assertFalse(result["live_verified"])
        self.assertFalse(result["input_sent"])
        self.assertEqual(len(result["files"]), 13)
        self.assertTrue(all(f["decoded"] and len(f["sha256"]) == 64 for f in result["files"]))
        self.assertEqual(result["manifest_sha256"], before[str(self.manifest_path)])
        self.assertEqual(result["regions_sha256"], before[str(self.spec_path)])

    def test_relabeling_synthetic_bundle_is_refused(self):
        self.manifest["source"] = "owner_capture"
        for keep_marker in (True, False):
            if not keep_marker:
                (self.bundle / "SYNTHETIC.json").unlink()
            result = self.check()
            self.assertFalse(result["valid"])
            self.assertTrue(result["synthetic"])
            self.assertIsNone(result["calibrate_argv"])
            self.assertIn("cannot be relabeled", " ".join(result["errors"]))

    def test_unmarked_source_declaration_only_produces_manual_calibration_arguments(self):
        # Exercise the declaration branch using synthetic test data, not authenticated owner evidence.
        (self.bundle / "SYNTHETIC.json").unlink()
        for path in self.bundle.glob("synthetic-*.png"):
            path.rename(path.with_name(path.name.replace("synthetic-", "test-", 1)))
        self.manifest = json.loads(json.dumps(self.manifest).replace("synthetic-", "test-"))
        self.manifest["source"] = "owner_capture"
        result = self.check()
        self.assertTrue(result["valid"])
        self.assertFalse(result["synthetic"])
        self.assertEqual(result["calibrate_argv"][:4], ["python3", "-m", "aion2_assist", "calibrate"])
        self.assertEqual(result["calibrate_argv"][-2:], ["--out", "<new-profile.json>"])
        self.assertTrue(result["manual_review_required"])
        self.assertFalse((self.bundle / "profile.json").exists())

    def test_missing_target_is_reported_without_pixel_decoding(self):
        spec = json.loads(self.spec_path.read_text())
        del spec["regions"]["target"]
        self.spec_path.write_text(json.dumps(spec))
        with patch("aion2_assist.ingest.load_frame") as load:
            result = self.check()
        load.assert_not_called()
        self.assertFalse(result["valid"])
        self.assertIn("missing regions: target", " ".join(result["errors"]))

    def test_missing_shots_and_evidence_have_separate_reports(self):
        del self.manifest["shots"]["cooldown"]
        self.manifest["evidence"]["q_stack"] = []
        result = self.check()
        self.assertEqual(result["missing_shots"], ["cooldown"])
        self.assertIn("q_stack: need 2", " ".join(result["missing_evidence"]))
        self.assertFalse(result["calibration_buildable"])
        self.assertFalse(result["evidence_coverage_complete"])

    def test_evidence_missing_does_not_hide_buildable_calibration(self):
        self.manifest["evidence"]["hp_levels"] = []
        result = self.check()
        self.assertTrue(result["calibration_buildable"])
        self.assertFalse(result["evidence_coverage_complete"])
        self.assertFalse(result["valid"])

    def test_path_aliases_do_not_count_as_distinct_evidence(self):
        name = self.manifest["evidence"]["q_stack"][0]
        self.manifest["evidence"]["q_stack"] = [name, "./" + name]
        result = self.check()
        self.assertFalse(result["valid"])
        self.assertIn("q_stack: need 2", " ".join(result["missing_evidence"]))

    def test_identical_pixels_in_distinct_stack_frames_are_allowed(self):
        a, b = self.manifest["evidence"]["q_stack"]
        shutil.copyfile(self.bundle / a, self.bundle / b)
        self.assertTrue(self.check()["valid"])

    def test_absolute_traversal_and_escaping_symlink_are_refused(self):
        outside = self.base / "outside.png"
        shutil.copyfile(self.bundle / self.manifest["shots"]["normal"], outside)
        (self.bundle / "escape.png").symlink_to(outside)
        for name in (str(outside), "../outside.png", "escape.png"):
            with self.subTest(name=name):
                self.manifest["shots"]["normal"] = name
                result = self.check()
                self.assertFalse(result["valid"])
                self.assertIn("manifest directory", " ".join(result["errors"]))

    def test_truncated_and_bad_crc_pngs_are_refused(self):
        path = self.bundle / self.manifest["shots"]["normal"]
        original = path.read_bytes()
        for content in (original[:-12], original[:29] + bytes(4) + original[33:]):
            path.write_bytes(content)
            result = self.check()
            self.assertFalse(result["valid"])
            self.assertIn("chunk structure/checksum", " ".join(result["errors"]))

    def test_bad_pixels_with_valid_checksums_are_refused(self):
        path = self.bundle / self.manifest["shots"]["normal"]
        data, chunks, pos = path.read_bytes(), [], 8
        while pos < len(data):
            length, kind = struct.unpack(">I4s", data[pos:pos + 8])
            body = data[pos + 8:pos + 8 + length]
            if kind == b"IDAT":
                pixels = bytearray(zlib.decompress(body))
                pixels[0] = 5
                body = zlib.compress(pixels)
            chunks.append(struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body)))
            pos += length + 12
        path.write_bytes(data[:8] + b"".join(chunks))
        result = self.check()
        self.assertFalse(result["valid"])
        self.assertIn("unsupported PNG scanline filter", " ".join(result["errors"]))

    def test_wrong_size_is_refused_before_decoding(self):
        spec = json.loads(self.spec_path.read_text())
        spec["frame_size"] = [640, 360]
        self.spec_path.write_text(json.dumps(spec))
        with patch("aion2_assist.ingest.load_frame") as load:
            result = self.check()
        load.assert_not_called()
        self.assertFalse(result["valid"])
        self.assertIn("crops are refused", " ".join(result["errors"]))

    def test_indistinguishable_calibration_is_refused(self):
        self.manifest["shots"]["cooldown"] = self.manifest["shots"]["normal"]
        result = self.check()
        self.assertFalse(result["calibration_buildable"])
        self.assertIn("indistinguishable", " ".join(result["errors"]))

    def test_optional_missing_state_is_explicit(self):
        del self.manifest["shots"]["death"]
        result = self.check()
        self.assertTrue(result["valid"])
        self.assertIn("'death'", " ".join(result["warnings"]))

    def test_malformed_json_and_schema_are_clean_errors(self):
        for text in ('[]', '{"version":1,"version":1}', '{',
                     '{"version":1,"source":"owner_capture","shots":[],"evidence":{}}'):
            with self.subTest(text=text):
                self.manifest_path.write_text(text)
                result = validate(self.spec_path, self.manifest_path)
                self.assertFalse(result["valid"])
                self.assertTrue(result["errors"])
        self.manifest["evidence"]["q_stack"] = "wrong"
        self.manifest["shots"]["typo"] = self.manifest["shots"]["normal"]
        result = self.check()
        self.assertIn("unknown shot roles", " ".join(result["errors"]))
        self.assertIn("ordered list", " ".join(result["errors"]))

    def test_existing_fixture_destination_is_unchanged(self):
        before = self.manifest_path.read_bytes()
        with self.assertRaises(FileExistsError):
            fixtures(self.bundle)
        self.assertEqual(self.manifest_path.read_bytes(), before)

    def test_fixture_destination_inside_package_is_refused(self):
        with self.assertRaisesRegex(ValueError, "packaged source"):
            fixtures(Path(__file__).parent / "unwanted-fixtures")

    def test_cli_invalid_bundle_returns_two_and_json(self):
        self.manifest_path.write_text("[]")
        result = subprocess.run([sys.executable, "-m", "aion2_assist", "ingest", "validate", "--regions",
                                 str(self.spec_path), "--manifest", str(self.manifest_path)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "")
        self.assertFalse(json.loads(result.stdout)["valid"])


if __name__ == "__main__":
    unittest.main()
