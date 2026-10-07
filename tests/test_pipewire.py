import io
import unittest

from aion2_assist.pipewire import GStreamerPipeWireReader, gst_argv


class FakeProc:
    def __init__(self, data):
        self.stdout, self.terminated = io.BytesIO(data), False

    def terminate(self):
        self.terminated = True


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


if __name__ == "__main__":
    unittest.main()
