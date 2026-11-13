"""Right-anchored bars (AION 2's HP bar empties toward its left end) and the 2560x1600 draft files."""
import contextlib
import copy
import io
import json
import os
import tempfile
import unittest

from aion2_assist import cli
from aion2_assist.config import load_config
from aion2_assist.keys import normalize_key
from aion2_assist.perception import Perceiver, read_bar
from aion2_assist.png import write_png
from aion2_assist.profile import CalibrationError, Profile, ProfileMismatch, build_profile, compute_hash
from aion2_assist.synth import HP_C, REGIONS, make_frame, synthetic_profile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RIGHT = {"hp": "right"}


def right_hp_shots(hp=0.55):
    all_slots = tuple(REGIONS["regions"]["slots"])
    return {"normal": make_frame(0.0, hp=hp, mp=0.8, bar_fill=RIGHT),
            "cooldown": make_frame(0.0, hp=hp, mp=0.8, cooldown=all_slots, bar_fill=RIGHT),
            "no_target": make_frame(0.0, hp=hp, mp=0.8, target="none", bar_fill=RIGHT)}


def right_hp_profile(hp=0.55):
    spec = copy.deepcopy(REGIONS)
    spec["bar_fill"] = dict(RIGHT)
    return build_profile(spec, right_hp_shots(hp))


class BarFill(unittest.TestCase):
    def test_right_anchored_bar_reads_its_fill(self):
        perceiver = Perceiver(right_hp_profile())
        for v in (0.05, 0.37, 0.55, 0.83, 1.0):
            o = perceiver.read(make_frame(0.0, hp=v, mp=0.4, bar_fill=RIGHT))
            self.assertAlmostEqual(o.hp.value, v, delta=0.011, msg=f"hp {v}")
            self.assertGreaterEqual(o.hp.confidence, 0.99)
            self.assertAlmostEqual(o.mp.value, 0.4, delta=0.011)  # mp stays left-anchored

    def test_calibration_samples_the_anchored_end(self):
        # A 'normal' shot below full HP: the left end is empty, the right end is filled.
        self.assertEqual(right_hp_profile(0.55)["templates"]["hp_color"], list(HP_C))
        left = build_profile(copy.deepcopy(REGIONS), right_hp_shots(0.55))
        self.assertNotEqual(left["templates"]["hp_color"], list(HP_C))  # the pre-0.3.1 failure mode

    def test_right_anchored_ambiguity_and_empty_stay_low_confidence(self):
        perceiver = Perceiver(right_hp_profile())
        self.assertLess(perceiver.read(make_frame(0.0, hp=0.5, hp_stray=True, bar_fill=RIGHT)).hp.confidence, 0.7)
        self.assertLessEqual(perceiver.read(make_frame(0.0, hp=0.0, bar_fill=RIGHT)).hp.confidence, 0.5)

    def test_profiles_without_bar_fill_are_unchanged(self):
        profile = synthetic_profile()
        self.assertNotIn("bar_fill", profile.data)
        self.assertEqual((profile.bar_fill("hp"), profile.bar_fill("mp")), ("left", "left"))
        self.assertEqual(right_hp_profile().bar_fill("hp"), "right")
        self.assertEqual(right_hp_profile().bar_fill("mp"), "left")

    def test_invalid_bar_fill_refused(self):
        for bad in ({"hp": "up"}, {"xp": "left"}, "right", ["hp", "right"]):
            spec = copy.deepcopy(REGIONS)
            spec["bar_fill"] = bad
            with self.assertRaises(CalibrationError, msg=repr(bad)):
                build_profile(spec, right_hp_shots())
        data = copy.deepcopy(right_hp_profile().data)
        data["bar_fill"] = {"hp": "down"}
        data["hash"] = compute_hash(data)
        with self.assertRaises(ProfileMismatch):
            Profile(data)
        with self.assertRaises(ValueError):
            read_bar(make_frame(0.0), tuple(REGIONS["regions"]["hp_bar"]), HP_C, 30, "down")

    def test_missing_regions_are_refused_with_a_message(self):
        spec = copy.deepcopy(REGIONS)
        del spec["regions"]["target"]
        with self.assertRaisesRegex(CalibrationError, "does not set target"):
            build_profile(spec, right_hp_shots())
        with self.assertRaisesRegex(CalibrationError, "no 'regions'"):
            build_profile({"frame_size": [320, 180]}, right_hp_shots())
        with tempfile.TemporaryDirectory() as d:
            regions = os.path.join(d, "regions.json")
            with open(regions, "w") as f:
                json.dump(spec, f)
            shot = os.path.join(d, "normal.png")
            write_png(shot, make_frame(0.0))
            with contextlib.redirect_stderr(io.StringIO()) as err:
                code = cli.main(["calibrate", "--regions", regions, "--shot", f"normal={shot}",
                                 "--out", os.path.join(d, "profile.json")])
            self.assertEqual(code, 2)
            self.assertIn("calibration refused", err.getvalue())
            self.assertFalse(os.path.exists(os.path.join(d, "profile.json")))


class DraftFiles(unittest.TestCase):
    """The owner's 2560x1600 drafts (from the earlier aion2-cleric-macro) stay consistent and fail closed."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "regions.alienware-2560x1600.draft.json")) as f:
            cls.regions = json.load(f)
        cls.config_path = os.path.join(ROOT, "config.alienware-draft.json")
        with open(cls.config_path) as f:
            cls.config = json.load(f)

    def test_regions_draft_geometry(self):
        spec = self.regions
        self.assertEqual(spec["frame_size"], [2560, 1600])
        self.assertEqual(spec["bar_fill"], {"hp": "right", "mp": "left"})
        reg = spec["regions"]
        self.assertNotIn("target", reg)  # unknown until a targeted frame exists
        self.assertFalse([k for k in reg if k.startswith("_")] + [k for k in reg["slots"] if k.startswith("_")])
        rects = [reg["anchor"], reg["hp_bar"], reg["mp_bar"], *reg["slots"].values(),
                 *(e["rect"] for e in spec["_excluded_slots"].values())]
        for r in rects:
            x, y, w, h = r
            self.assertTrue(all(type(v) is int for v in r), r)
            self.assertTrue(x >= 0 and y >= 0 and w >= 1 and h >= 1 and x + w <= 2560 and y + h <= 1600, r)
        self.assertFalse(set(reg["slots"]) & set(spec["_excluded_slots"]))

    def test_calibrate_refuses_the_draft_until_target_is_measured(self):
        with self.assertRaisesRegex(CalibrationError, "does not set target"):
            build_profile(copy.deepcopy(self.regions), {})

    def test_config_draft_is_valid_and_matches_the_regions(self):
        cfg = load_config(self.config_path)
        cfg.validate_for_run()
        enabled = {s.id: s.key for s in cfg.enabled_slots()}
        self.assertEqual(enabled, {"bar_q": "q", "bar_1": "1", "bar_2": "2", "bar_e": "e"})
        self.assertLessEqual(set(enabled), set(self.regions["regions"]["slots"]))  # every enabled slot is readable
        self.assertEqual(cfg.window_match, "steam_app_3393110")
        self.assertEqual(set(cfg.ignore_codes), {66, 67})  # the F8/F9 hotkeys never count as manual play

    def test_config_draft_never_binds_return_or_speed_scrolls(self):
        keys = {normalize_key(s["key"]) for s in self.config["slots"].values()}
        self.assertFalse(keys & {"F3", "F6"})

    def test_config_draft_carries_no_timing_jitter(self):
        def keys(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    yield k
                    yield from keys(v)
            elif isinstance(node, list):
                for v in node:
                    yield from keys(v)
        banned = [k for k in keys(self.config) if any(b in k.lower() for b in ("jitter", "timing", "hold_m", "gap"))]
        self.assertEqual(banned, [])


if __name__ == "__main__":
    unittest.main()
