import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

from aion2_assist import cli
from aion2_assist.frame import Frame
from aion2_assist.png import load_frame, write_png
from aion2_assist.profile import CalibrationError, Profile, ProfileMismatch, build_profile
from aion2_assist.synth import REGIONS, make_frame
from tests.helpers import PERCEIVER, PROFILE, obs


class Perception(unittest.TestCase):
    def test_bar_fill_accuracy(self):
        for v in (0.0, 0.05, 0.37, 0.5, 0.83, 1.0):
            o = obs(hp=v, mp=1 - v if v < 1 else 0.2)
            self.assertAlmostEqual(o.hp.value, v, delta=0.011, msg=f"hp {v}")
            self.assertAlmostEqual(o.mp.value, 1 - v if v < 1 else 0.2, delta=0.011)
            self.assertEqual(o.hp.ts, 0.0)

    def test_bar_confidence(self):
        self.assertGreaterEqual(obs(hp=0.5).hp.confidence, 0.99)
        self.assertLess(obs(mp=0.3, mp_stray=True).mp.confidence, 0.7)
        self.assertLessEqual(obs(hp=0.0).hp.confidence, 0.5)  # empty is ambiguous

    def test_slot_states(self):
        o = obs(cooldown=("s2", "s4"))
        for sid, want in {"s1": "ready", "s2": "cooldown", "s3": "ready", "s4": "cooldown", "s5": "ready"}.items():
            self.assertEqual(o.slots[sid].value, want)
            self.assertGreater(o.slots[sid].confidence, 0.9)

    def test_partial_cooldown_sweep_is_low_confidence(self):
        f = make_frame(0.0)
        x, y, w, h = REGIONS["regions"]["slots"]["s1"]
        rows = [bytearray(r) for r in f.rows]
        for yy in range(y, y + h):  # slot halfway between ready colour and cooldown grey
            rows[yy][3 * x:3 * (x + w)] = bytes((145, 65, 67)) * w
        o = PERCEIVER.read(Frame(f.width, f.height, [bytes(r) for r in rows], 0.0))
        self.assertLess(o.slots["s1"].confidence, 0.7)

    def test_target_kinds(self):
        for kind in ("enemy", "none", "friendly"):
            r = obs(target=kind).target
            self.assertEqual((r.value, r.confidence > 0.9), (kind, True))

    def test_anchor_and_states(self):
        self.assertTrue(obs().anchor.value)
        self.assertFalse(obs(anchor=False).anchor.value)
        self.assertIsNone(obs().state.value)
        for st in ("dialog", "loading", "death"):
            r = obs(state=st).state
            self.assertEqual(r.value, st)
            self.assertGreater(r.confidence, 0.9)


class PngReader(unittest.TestCase):
    def test_stdlib_reader_all_filter_types(self):
        f = make_frame(0.0, hp=0.4, cooldown=("s1",))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.png")
            write_png(p, f)  # rows cycle through filters 0..4
            g = load_frame(p, force_stdlib=True)
        self.assertEqual((g.width, g.height), (f.width, f.height))
        self.assertEqual(list(g.rows), list(f.rows))


class Calibration(unittest.TestCase):
    def test_hash_stable_and_tamper_detected(self):
        self.assertEqual(PROFILE.hash, Profile(json.loads(json.dumps(PROFILE.data))).hash)
        bad = json.loads(json.dumps(PROFILE.data))
        bad["frame_size"] = [1280, 720]
        with self.assertRaises(ProfileMismatch):
            Profile(bad)

    def test_frame_size_mismatch_refused(self):
        with self.assertRaises(ProfileMismatch):
            PERCEIVER.read(make_frame(0.0, size=(640, 360)))

    def test_records_source_frame_size_and_scale(self):
        self.assertEqual(PROFILE["frame_size"], [320, 180])
        self.assertEqual(PROFILE["ui_scale"], 1.0)

    def test_crop_screenshots_refused(self):
        shots = {k: make_frame(0.0) for k in ("normal", "cooldown", "no_target")}
        shots["normal"] = Frame(100, 50, [bytes(300)] * 50, 0.0)  # a crop, not a full frame
        with self.assertRaises(CalibrationError):
            build_profile(REGIONS, shots)

    def test_indistinguishable_shots_refused(self):
        shots = {k: make_frame(0.0) for k in ("normal", "cooldown", "no_target")}
        with self.assertRaises(CalibrationError):
            build_profile(REGIONS, shots)

    def test_cli_calibrate_and_read(self):
        with tempfile.TemporaryDirectory() as d:
            shots = {"normal": make_frame(0, hp=.8), "cooldown": make_frame(0, hp=.8, cooldown=tuple(REGIONS["regions"]["slots"])),
                     "no_target": make_frame(0, hp=.8, target="none"), "dialog": make_frame(0, state="dialog")}
            args = ["calibrate", "--regions", os.path.join(d, "r.json"), "--out", os.path.join(d, "p.json")]
            with open(args[2], "w") as f:
                json.dump(REGIONS, f)
            for k, f in shots.items():
                write_png(os.path.join(d, k + ".png"), f)
                args += ["--shot", f"{k}={os.path.join(d, k + '.png')}"]
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(cli.main(args), 0)
                self.assertEqual(cli.main(["read", "--profile", args[4], os.path.join(d, "normal.png")]), 0)
            self.assertIn('"hp"', out.getvalue())
            # a wrong-size screenshot is refused by the CLI
            write_png(os.path.join(d, "small.png"), Frame(160, 90, [bytes(480)] * 90, 0.0))
            bad = args[:-2] + ["--shot", f"dialog={os.path.join(d, 'small.png')}"]
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(bad), 2)

    def test_cli_check_config_rejects_placeholders(self):
        root = os.path.dirname(os.path.dirname(__file__))
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(cli.main(["check-config", "--config", os.path.join(root, "config.example.json")]), 2)
        self.assertIn("placeholder", err.getvalue())


if __name__ == "__main__":
    unittest.main()
