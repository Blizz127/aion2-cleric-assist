"""Calibration capture against a FAKE portal and a FAKE GStreamer process. Nothing here touches a bus, the game,
an input device, or the network."""
import datetime
import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from aion2_assist import capture
from aion2_assist.frame import Frame
from aion2_assist.png import write_png
from aion2_assist.portal import FakeTransport, PortalDenied

SESSION = "/org/freedesktop/portal/desktop/session/1_1/aion2assist_shots"
STAMP = "20261112T150000Z"


def now():
    return datetime.datetime(2026, 11, 12, 15, 0, 0, tzinfo=datetime.timezone.utc)


def portal(**override):
    replies = {"CreateSession": {"session_handle": SESSION}, "SelectSources": {},
               "Start": {"streams": [(42, {})]}, "OpenPipeWireRemote": 7, "Close": None}
    replies.update(override)
    return FakeTransport(replies)


class FakeProc:
    def __init__(self, exit_code=None):
        self.returncode = exit_code
        self.terminated = self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


class Harness:
    def __init__(self, frames=2, exit_code=None, transport=None, close_session=False, interrupt=False):
        self.t = [0.0]
        self.frames, self.exit_code, self.close_session, self.interrupt = frames, exit_code, close_session, interrupt
        self.transport = transport if transport is not None else portal()
        self.factory_calls = 0
        self.spawned = []
        self.msgs = []

    def clock(self):
        return self.t[0]

    def sleep(self, dt):
        if self.interrupt:
            raise KeyboardInterrupt
        self.t[0] += max(dt, 0.01)

    def factory(self):
        self.factory_calls += 1
        return self.transport

    def spawn(self, argv, **kw):
        pattern = next(a for a in argv if a.startswith("location="))[len("location="):]
        for i in range(self.frames):
            write_png(pattern % i, Frame(8, 4, [bytes(24)] * 4, 0.0))
        proc = FakeProc(self.exit_code)
        self.spawned.append((argv, kw, proc))
        if self.close_session:
            self.transport.closed.add(SESSION)
        return proc

    def run(self, out, every=3.0, seconds=9.0, which=lambda name: "/usr/bin/" + name):
        return capture.run_capture(out, every, seconds, transport_factory=self.factory, spawn=self.spawn,
                                   clock=self.clock, sleep=self.sleep, which=which, now=now, emit=self.msgs.append)

    def summary(self):
        return json.loads(self.msgs[-1])


class Capture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = self.tmp.name
        self.folder = os.path.join(self.out, STAMP)

    def tearDown(self):
        self.tmp.cleanup()

    def test_records_frames_through_screen_share_only(self):
        h = Harness()
        self.assertEqual(h.run(self.out), 0)
        t = h.transport
        self.assertEqual(t.methods(), ["CreateSession", "SelectSources", "Start", "OpenPipeWireRemote", "Close"])
        self.assertFalse([c for c in t.calls if "RemoteDesktop" in (c["iface"] or "")], "must never open input")
        select = t.calls[1]["options"]
        self.assertEqual((select["types"], select["multiple"], select["cursor_mode"], select["persist_mode"]),
                         (1, False, 1, 0))
        argv, kw, proc = h.spawned[0]
        self.assertEqual(argv[0], "gst-launch-1.0")
        for element in ("pipewiresrc", "videorate", "videoconvert", "pngenc", "multifilesink",
                        "video/x-raw,framerate=1/3", "video/x-raw,format=RGB", "fd=7", "path=42"):
            self.assertIn(element, argv)
        self.assertNotIn("videoscale", argv)
        self.assertIn(f"location={os.path.join(self.folder, 'frame-%05d.png')}", argv)
        self.assertEqual(kw["pass_fds"], (7,))
        self.assertTrue(proc.terminated)
        self.assertTrue(t.transport_closed)
        s = h.summary()
        self.assertEqual((s["frames"], s["frame_size"], s["input_sent"], s["reason"]),
                         (2, [8, 4], False, "duration reached"))
        self.assertTrue(os.path.exists(os.path.join(self.folder, "gst-stderr.log")))

    def test_invalid_rate_or_duration_refused_before_the_portal(self):
        for every, seconds in ((0.1, 10), (61, 10), (3, 0), (3, 3601)):
            h = Harness()
            self.assertEqual(h.run(self.out, every=every, seconds=seconds), 2)
            self.assertEqual(h.factory_calls, 0)
        self.assertEqual(os.listdir(self.out), [])

    def test_missing_gstreamer_refused_before_the_portal(self):
        h = Harness()
        self.assertEqual(h.run(self.out, which=lambda name: None), 2)
        self.assertEqual(h.factory_calls, 0)
        self.assertEqual(os.listdir(self.out), [])

    def test_existing_folder_is_never_reused(self):
        os.makedirs(self.folder)
        h = Harness()
        self.assertEqual(h.run(self.out), 2)
        self.assertEqual(h.factory_calls, 0)

    def test_portal_denial_spawns_nothing_and_closes(self):
        h = Harness(transport=portal(SelectSources=PortalDenied("SelectSources: response code 1")))
        self.assertEqual(h.run(self.out), 3)
        self.assertEqual(h.spawned, [])
        self.assertEqual(h.transport.methods()[-1], "Close")
        self.assertTrue(h.transport.transport_closed)
        self.assertFalse(os.path.exists(self.folder), "empty folder is removed")
        self.assertIn("portal refused", h.summary()["reason"])

    def test_wrong_stream_count_refused(self):
        h = Harness(transport=portal(Start={"streams": []}))
        self.assertEqual(h.run(self.out), 4)
        self.assertEqual(h.spawned, [])
        self.assertIn("expected exactly one stream", h.summary()["reason"])
        self.assertEqual(h.transport.methods()[-1], "Close")

    def test_stops_when_the_portal_closes_the_session(self):
        h = Harness(close_session=True)
        self.assertEqual(h.run(self.out), 0)
        self.assertEqual(h.summary()["reason"], "the portal closed the screen-share session")
        self.assertTrue(h.spawned[0][2].terminated)

    def test_gstreamer_failure_keeps_its_log_and_reports_no_frames(self):
        h = Harness(frames=0, exit_code=1)
        self.assertEqual(h.run(self.out), 4)
        self.assertIn("gstreamer exited with code 1", h.summary()["reason"])
        self.assertTrue(os.path.exists(os.path.join(self.folder, "gst-stderr.log")))
        self.assertTrue(h.transport.transport_closed)

    def test_gstreamer_failure_after_frames_is_not_reported_as_success(self):
        for exit_code in (1, -9):
            with self.subTest(exit_code=exit_code):
                out = os.path.join(self.out, str(exit_code))
                h = Harness(frames=2, exit_code=exit_code)
                self.assertEqual(h.run(out), 4)
                self.assertEqual(h.summary()["frames"], 2)
                self.assertIn(f"gstreamer exited with code {exit_code}", h.summary()["reason"])
                for name in ("frame-00000.png", "frame-00001.png", "gst-stderr.log"):
                    self.assertTrue(os.path.isfile(os.path.join(out, STAMP, name)))
                self.assertEqual(h.transport.methods()[-1], "Close")
                self.assertTrue(h.transport.transport_closed)

    def test_gstreamer_clean_exit_with_frames_is_success(self):
        h = Harness(frames=2, exit_code=0)
        self.assertEqual(h.run(self.out), 0)
        self.assertEqual(h.summary()["frames"], 2)
        self.assertIn("gstreamer exited with code 0", h.summary()["reason"])

    def test_terminate_failure_still_kills_and_closes_screen_share(self):
        h = Harness()
        with patch.object(FakeProc, "terminate", side_effect=OSError("terminate failed")):
            self.assertEqual(h.run(self.out), 4)
        self.assertTrue(h.spawned[0][2].killed)
        self.assertEqual(h.transport.methods()[-1], "Close")
        self.assertTrue(h.transport.transport_closed)
        self.assertEqual(h.summary()["frames"], 2)
        self.assertIn("terminate failed", h.summary()["reason"])

    def test_wait_failure_still_closes_screen_share_and_reports_frames(self):
        h = Harness()
        with patch.object(FakeProc, "wait", side_effect=subprocess.TimeoutExpired("gst-launch-1.0", 5)):
            self.assertEqual(h.run(self.out), 4)
        self.assertTrue(h.spawned[0][2].killed)
        self.assertEqual(h.transport.methods()[-1], "Close")
        self.assertTrue(h.transport.transport_closed)
        self.assertEqual(h.summary()["frames"], 2)
        self.assertIn("cleanup failed", h.summary()["reason"])

    def test_terminate_timeout_can_recover_with_kill(self):
        h = Harness()
        with patch.object(FakeProc, "wait", side_effect=[subprocess.TimeoutExpired("gst-launch-1.0", 5), -9]):
            self.assertEqual(h.run(self.out), 0)
        self.assertTrue(h.spawned[0][2].killed)
        self.assertTrue(h.transport.transport_closed)
        self.assertEqual(h.summary()["reason"], "duration reached")

    def test_transport_close_failure_is_reported_without_losing_summary(self):
        h = Harness()
        with patch.object(h.transport, "close", side_effect=OSError("bus close failed")):
            self.assertEqual(h.run(self.out), 4)
        self.assertTrue(h.spawned[0][2].terminated)
        self.assertEqual(h.transport.methods()[-1], "Close")
        self.assertEqual(h.summary()["frames"], 2)
        self.assertIn("bus close failed", h.summary()["reason"])

    def test_transport_close_failure_preserves_portal_denial_result(self):
        h = Harness(transport=portal(SelectSources=PortalDenied("consent denied")))
        with patch.object(h.transport, "close", side_effect=OSError("bus close failed")):
            self.assertEqual(h.run(self.out), 3)
        self.assertEqual(h.spawned, [])
        self.assertIn("portal refused", h.summary()["reason"])
        self.assertIn("bus close failed", h.summary()["reason"])

    def test_ctrl_c_stops_cleanly_and_keeps_frames(self):
        h = Harness(interrupt=True)
        self.assertEqual(h.run(self.out), 0)
        self.assertEqual(h.summary()["reason"], "stopped by the owner")
        self.assertTrue(h.spawned[0][2].terminated)
        self.assertEqual(h.transport.methods()[-1], "Close")

    def test_frame_rate_fraction(self):
        rate = lambda every: next(a for a in capture.gst_capture_argv(1, 3, every, "x") if "framerate" in a)
        self.assertEqual(rate(0.5), "video/x-raw,framerate=2/1")
        self.assertEqual(rate(2.5), "video/x-raw,framerate=2/5")
        self.assertEqual(rate(3.0), "video/x-raw,framerate=1/3")


if __name__ == "__main__":
    unittest.main()
