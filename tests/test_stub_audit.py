"""Audit: there are no NotImplementedError stubs left; interfaces are abstract and cannot be instantiated."""
import glob
import os
import unittest

from aion2_assist.adapters import FocusProbe, FrameSource, InputSink, OwnerInput
from aion2_assist.portal import DBusTransport, PipeWireReader

ROOT = os.path.dirname(os.path.dirname(__file__))


class Audit(unittest.TestCase):
    def test_no_notimplementederror_anywhere_in_package(self):
        for f in glob.glob(os.path.join(ROOT, "aion2_assist", "**", "*.py"), recursive=True):
            self.assertNotIn("NotImplementedError", open(f).read(), f)

    def test_interfaces_are_abstract(self):
        for cls in (FrameSource, FocusProbe, InputSink, OwnerInput, DBusTransport, PipeWireReader):
            with self.assertRaises(TypeError, msg=cls.__name__):
                cls()

    def test_partial_subclass_cannot_instantiate(self):
        class Half(InputSink):
            def key_down(self, key):
                pass
        with self.assertRaises(TypeError):
            Half()

    def test_kwin_script_package_matches_verified_kwin_loader(self):
        """KWin source (scripting.cpp): scripts are keyed by KPlugin Id; main file is contents/code/main.js;
        X-Plasma-API must be javascript; <Id>Enabled in kwinrc [Plugins]."""
        import json
        meta = json.load(open(os.path.join(ROOT, "kwin_script/aion2assist_focus/metadata.json")))
        self.assertEqual(meta["KPlugin"]["Id"], "aion2assist_focus")
        self.assertEqual(meta["X-Plasma-API"], "javascript")
        self.assertTrue(os.path.exists(os.path.join(ROOT, "kwin_script/aion2assist_focus/contents/code/main.js")))
        from aion2_assist.portal import KWIN_SCRIPT_ID
        self.assertEqual(KWIN_SCRIPT_ID, meta["KPlugin"]["Id"])


if __name__ == "__main__":
    unittest.main()
