import contextlib
import io
import json
import os
import tempfile
import unittest

from aion2_assist import cli
from aion2_assist.binds import make_template, validate_binds
from aion2_assist.keys import normalize_key
from tests.helpers import PROFILE


def filled(**over):
    t = make_template(PROFILE)
    t["slots"]["s1"].update(key="1", role="heal", enabled=True)
    t["slots"]["s2"].update(key="F5", role="hot", enabled=True)
    for sid, d in over.items():
        t["slots"][sid].update(d)
    return t


class Binds(unittest.TestCase):
    def test_template_lists_positions_and_blank_keys(self):
        t = make_template(PROFILE)
        self.assertEqual(sorted(t["slots"]), ["s1", "s2", "s3", "s4", "s5"])
        s1 = t["slots"]["s1"]
        self.assertEqual((s1["key"], s1["role"], s1["enabled"]), ("", "", False))
        self.assertEqual(s1["_position_xywh"], PROFILE["regions"]["slots"]["s1"])
        self.assertEqual(t["_profile_hash"], PROFILE.hash)

    def test_untouched_template_has_no_enabled_slots_but_validates_as_blank(self):
        self.assertEqual(validate_binds(make_template(PROFILE), PROFILE), [])  # nothing enabled, nothing invented

    def test_filled_file_ok_and_key_names_normalized(self):
        self.assertEqual(validate_binds(filled(s3=dict(key="Q", role="dmg", enabled=True)), PROFILE), [])
        self.assertEqual((normalize_key("Q"), normalize_key("f5"), normalize_key(" 7 ")), ("q", "F5", "7"))

    def test_refusals(self):
        cases = {
            "blank": filled(s3=dict(key="", role="dmg", enabled=True)),
            "UNSET": filled(s3=dict(key="UNSET_3", role="dmg", enabled=True)),
            "not a key": filled(s3=dict(key="CTRL+1", role="dmg", enabled=True)),
            "duplicate": filled(s3=dict(key="1", role="dmg", enabled=True)),
            "bad role": filled(s3=dict(key="e", role="nuke", enabled=True)),
        }
        for why, doc in cases.items():
            self.assertTrue(validate_binds(doc, PROFILE), why)
        bad = filled()
        bad["slots"]["zz"] = {"key": "9", "role": "heal", "enabled": True}
        self.assertTrue(any("no such slot" in p for p in validate_binds(bad, PROFILE)))
        other = filled()
        other["_profile_hash"] = "0" * 64
        self.assertTrue(any("different profile" in p for p in validate_binds(other, PROFILE)))

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            prof, out = os.path.join(d, "p.json"), os.path.join(d, "binds.json")
            json.dump(PROFILE.data, open(prof, "w"))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["binds", "template", out, "--profile", prof]), 0)
                self.assertEqual(cli.main(["binds", "validate", out, "--profile", prof]), 0)
                json.dump(filled(), open(out, "w"))
                self.assertEqual(cli.main(["binds", "validate", out, "--profile", prof]), 0)
                json.dump(filled(s3=dict(key="UNSET", role="dmg", enabled=True)), open(out, "w"))
                with contextlib.redirect_stderr(io.StringIO()) as err:
                    self.assertEqual(cli.main(["binds", "validate", out]), 2)
            self.assertIn("blank/UNSET", err.getvalue())


if __name__ == "__main__":
    unittest.main()
