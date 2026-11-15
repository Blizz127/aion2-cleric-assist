import io
import subprocess
import sys
import threading
import unittest
from unittest.mock import Mock, patch

from aion2_assist.pipewire import GStreamerPipeWireReader, gst_argv
from aion2_assist.frame import Frame


class FakeProc:
    def __init__(self, data):
        self.stdout, self.terminated = io.BytesIO(data), False
        self.returncode = None
        self.terminations = self.waits = self.kills = 0

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.terminations += 1
        self.returncode = -15

    def kill(self):
        self.kills += 1
        self.returncode = -9

    def wait(self, timeout=None):
        self.waits += 1
        return self.returncode


class PipeWire(unittest.TestCase):
    def test_argv_pins_size_without_scaling(self):
        a = gst_argv(42, 9, (8, 2))
        self.assertIn("video/x-raw,format=RGB,width=8,height=2", a)
        self.assertEqual((a[0], "pipewiresrc" in a, "fd=9" in a, "path=42" in a, "videoscale" in a), ("gst-launch-1.0", True, True, True, False))
        with self.assertRaises(ValueError):
            gst_argv(1, 1, (1, 1))  # 3 bytes/row is not 4-aligned

    def test_reads_latest_frame_then_fails_closed_on_eof(self):
        w, h = 4, 2
        f1, f2 = bytes([1]) * 24, bytes(range(24))
        spawned = {}

        def spawn(argv, **kw):
            spawned.update(argv=argv, kw=kw)
            return FakeProc(f1 + f2)
        t = iter([10.0, 11.0])
        r = GStreamerPipeWireReader(lambda: next(t), spawn=spawn, start_thread=False)
        with self.assertRaises(RuntimeError):
            r.read(7, 5, (w, h))      # starts the process, no frame yet
        self.assertEqual(spawned["kw"]["pass_fds"], (5,))
        r._read_loop()                # synchronous in tests
        self.assertEqual((r.latest.ts, r.latest.rows[0], r.latest.width), (11.0, bytes(range(12)), 4))
        with self.assertRaises(RuntimeError):
            r.read(7, 5, (w, h))      # stream ended -> fail closed
        r.close()
        self.assertTrue(r.proc.terminated)

    def test_truncated_frame_is_not_delivered(self):
        r = GStreamerPipeWireReader(lambda: 1.0, spawn=lambda a, **k: FakeProc(b"\x00" * 10), start_thread=False)
        with self.assertRaises(RuntimeError):
            r.read(1, 3, (4, 2))
        r._read_loop()
        self.assertIsNone(r.latest)
        self.assertIsInstance(r.error, EOFError)

    def test_close_reaps_helper_closes_stdout_and_refuses_cached_frame(self):
        proc = FakeProc(b"")
        r = GStreamerPipeWireReader(lambda: 1.0, spawn=lambda *a, **k: proc, start_thread=False)
        with self.assertRaises(RuntimeError):
            r.read(1, 3, (4, 2))
        r.latest = Frame(4, 2, [bytes(12)] * 2, 1.0)
        r.close()
        r.close()
        self.assertEqual((proc.terminations, proc.waits, proc.kills), (1, 1, 0))
        self.assertTrue(proc.stdout.closed)
        self.assertIsNone(r.latest)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            r.read(1, 3, (4, 2))

    def test_close_before_read_never_starts_helper(self):
        spawn = Mock()
        r = GStreamerPipeWireReader(lambda: 1.0, spawn=spawn)
        r.close()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            r.read(1, 3, (4, 2))
        spawn.assert_not_called()

    def test_close_kills_helper_that_times_out_on_termination(self):
        proc = FakeProc(b"")
        r = GStreamerPipeWireReader(lambda: 1.0, spawn=lambda *a, **k: proc, start_thread=False)
        with self.assertRaises(RuntimeError):
            r.read(1, 3, (4, 2))
        with patch.object(proc, "wait", side_effect=[subprocess.TimeoutExpired("gst", 1), -9]) as wait:
            r.close()
        self.assertEqual(proc.kills, 1)
        self.assertEqual(wait.call_count, 2)
        self.assertTrue(proc.stdout.closed)

    def test_close_failure_still_closes_stdout_and_latches_closed(self):
        proc = FakeProc(b"")
        r = GStreamerPipeWireReader(lambda: 1.0, spawn=lambda *a, **k: proc, start_thread=False)
        with self.assertRaises(RuntimeError):
            r.read(1, 3, (4, 2))
        with patch.object(proc, "wait", side_effect=subprocess.TimeoutExpired("gst", 1)):
            with self.assertRaisesRegex(RuntimeError, "cleanup"):
                r.close()
        self.assertEqual(proc.kills, 1)
        self.assertTrue(proc.stdout.closed)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            r.read(1, 3, (4, 2))

    def test_terminate_error_still_attempts_kill_and_pipe_cleanup(self):
        proc = FakeProc(b"")
        r = GStreamerPipeWireReader(lambda: 1.0, spawn=lambda *a, **k: proc, start_thread=False)
        with self.assertRaises(RuntimeError):
            r.read(1, 3, (4, 2))
        with patch.object(proc, "terminate", side_effect=OSError("terminate failed")):
            with self.assertRaisesRegex(RuntimeError, "terminate failed"):
                r.close()
        self.assertEqual(proc.kills, 1)
        self.assertTrue(proc.stdout.closed)

    def test_stuck_reader_is_reported_without_closing_its_buffer(self):
        proc = FakeProc(b"")
        r = GStreamerPipeWireReader(lambda: 1.0, spawn=lambda *a, **k: proc, start_thread=False)
        with self.assertRaises(RuntimeError):
            r.read(1, 3, (4, 2))
        r._thread = Mock()
        r._thread.is_alive.return_value = True
        try:
            with self.assertRaisesRegex(RuntimeError, "reader thread did not stop"):
                r.close()
            self.assertFalse(proc.stdout.closed, "closing a buffered pipe may block behind the read thread")
        finally:
            proc.stdout.close()

    def test_frame_in_flight_cannot_be_published_after_close(self):
        frame_ready, release_frame, terminated = threading.Event(), threading.Event(), threading.Event()
        proc = FakeProc(bytes(24))
        terminate = proc.terminate

        def stop():
            terminate()
            terminated.set()

        def clock():
            frame_ready.set()
            if not release_frame.wait(5):
                raise TimeoutError("test frame was not released")
            return 1.0

        proc.terminate = stop
        r = GStreamerPipeWireReader(clock, spawn=lambda *a, **k: proc)
        errors = []

        def close():
            try:
                r.close()
            except Exception as e:
                errors.append(e)

        closer = threading.Thread(target=close)
        try:
            with self.assertRaises(RuntimeError):
                r.read(1, 3, (4, 2))
            self.assertTrue(frame_ready.wait(2))
            closer.start()
            self.assertTrue(terminated.wait(2))
            release_frame.set()
            closer.join(2)
            self.assertFalse(closer.is_alive())
            self.assertEqual(errors, [])
            self.assertIsNone(r.latest)
            self.assertFalse(r._thread.is_alive())
            self.assertTrue(proc.stdout.closed)
        finally:
            release_frame.set()
            if closer.ident is not None:
                closer.join(2)
            r.close()

    def test_close_reaps_real_child_ignoring_terminate_and_joins_reader(self):
        # A harmless local Python pipe writer, never GStreamer or a real adapter.
        received = threading.Event()
        spawned = []

        def spawn(_argv, **kwargs):
            child = subprocess.Popen([sys.executable, "-u", "-c",
                                      "import signal,sys,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                                      "sys.stdout.buffer.write(bytes(24)); sys.stdout.flush(); time.sleep(30)"],
                                     stdin=kwargs["stdin"], stdout=kwargs["stdout"], stderr=kwargs["stderr"])
            spawned.append(child)
            return child

        def clock():
            received.set()
            return 1.0

        r = GStreamerPipeWireReader(clock, spawn=spawn)
        try:
            with self.assertRaises(RuntimeError):
                r.read(1, 3, (4, 2))
            self.assertTrue(received.wait(5), "child must install its handler and deliver one frame")
            r.close()
            self.assertIsNotNone(spawned[0].poll())
            self.assertTrue(spawned[0].stdout.closed)
            self.assertFalse(r._thread.is_alive())
        finally:
            for child in spawned:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=5)
            r.close()


if __name__ == "__main__":
    unittest.main()
