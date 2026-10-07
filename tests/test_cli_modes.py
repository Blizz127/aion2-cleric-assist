import contextlib
import io
import json
import os
import tempfile
import unittest

from aion2_assist import cli
from aion2_assist.png import write_png
from aion2_assist.synth import make_frame
from tests.helpers import PROFILE

ROOT = os.path.dirname(os.path.dirname(__file__))
DEMO = os.path.join(ROOT, "aion2_assist", "demo")
EXPECT = "[(0.0, '3'), (1.0, '4'), (2.0, '1'), (3.0, '2'), (4.0, '4'), (6.1, '4')]"


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = cli.main(list(argv))
        except SystemExit as e:
            rc = e.code
    return rc, out.getvalue(), err.getvalue()


class Modes(unittest.TestCase):
    def test_replay_demo_prints_decisions_suppressions_and_sequence(self):
        rc, out, _ = run("replay", "--demo")
        self.assertEqual(rc, 0)
        self.assertIn("emergency_heal:hp=0.30<0.35 action=s1 key=1", out)
        for reason in ("unsafe_state:dialog", "anchor_missing", "stale_frame", "manual_pause"):
            self.assertIn(f"reason={reason}", out)
        self.assertIn("key sequence: " + EXPECT, out)
        self.assertIn("MOCK adapters", out)

    def test_replay_json_mode_emits_structured_records(self):
        rc, out, _ = run("replay", "--demo", "--json")
        recs = [json.loads(l) for l in out.splitlines() if l.startswith("{")]
        self.assertEqual(len(recs), 95)
        self.assertTrue(all({"t", "event", "reason"} <= set(r) for r in recs))

    def test_replay_explicit_files_and_missing_args(self):
        with tempfile.TemporaryDirectory() as d:
            prof = os.path.join(d, "p.json")
            json.dump(PROFILE.data, open(prof, "w"))
            rc, out, _ = run("replay", os.path.join(DEMO, "timeline.json"), "--config", os.path.join(DEMO, "config.json"),
                             "--profile", prof)
            self.assertEqual(rc, 0)
            self.assertIn("key sequence: " + EXPECT, out)
        rc, _, err = run("replay", os.path.join(DEMO, "timeline.json"))
        self.assertEqual(rc, 2)
        self.assertIn("needs", err)

    def test_replay_over_frames_dir(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as f:
            prof = os.path.join(d, "p.json")
            json.dump(PROFILE.data, open(prof, "w"))
            write_png(os.path.join(f, "a.png"), make_frame(0.0))
            write_png(os.path.join(f, "b.png"), make_frame(0.0, hp=0.2))
            rc, out, _ = run("replay", f, "--config", os.path.join(DEMO, "config.json"), "--profile", prof, "--dt", "1.5")
            self.assertEqual(rc, 0)
            self.assertIn("key sequence: [(0.0, '3'), (1.5, '1')]", out)

    def test_dry_run_demo(self):
        rc, out, _ = run("dry-run", "--demo")
        self.assertEqual(rc, 0)
        for s in ("upkeep:shield", "emergency_heal", "unsafe_state:dialog", "anchor_missing", "no_target"):
            self.assertIn(s, out)
        self.assertIn("[(0.0, '3'), (1.1, '1')]", out)

    def test_dry_run_dir_and_wrong_size_frame_fails_closed(self):
        from aion2_assist.frame import Frame
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as f:
            prof = os.path.join(d, "p.json")
            json.dump(PROFILE.data, open(prof, "w"))
            write_png(os.path.join(f, "a.png"), Frame(64, 36, [bytes(192)] * 36, 0.0))
            rc, out, _ = run("dry-run", "--frames", f, "--config", os.path.join(DEMO, "config.json"), "--profile", prof)
            self.assertEqual(rc, 0)
            self.assertIn("reason=profile_mismatch", out)
            self.assertIn("keys_emitted=0", out)
        self.assertEqual(run("dry-run")[0], 2)

    def test_demo_config_is_not_owner_binds(self):
        doc = json.load(open(os.path.join(DEMO, "config.json")))
        self.assertIn("DEMO ONLY", doc["_comment"])


if __name__ == "__main__":
    unittest.main()
