"""PipeWireReader via the system `gst-launch-1.0` (gst-plugin-pipewire). Stdlib only.

Route chosen as least-dependency: no Python binding, one external tool that is normally present on a
desktop with PipeWire. NOT present on the devbox, so never executed here; tests use a fake process.
Requirement on the target: `gst-launch-1.0` with the `pipewiresrc` element (gst-plugin-pipewire), and a
capture width*3 divisible by 4 (GStreamer RGB row padding). Frame size is forced by caps with NO scaling:
if the source resolution differs, negotiation fails, the stream ends and the reader fails closed.
Frame timestamp = time the whole frame was received (clock), not the compositor's capture time, so real
latency is unknown until measured on the target.
"""
import subprocess
import threading
from typing import Callable

from .frame import Frame
from .portal import PipeWireReader


def gst_argv(node_id: int, fd: int, size) -> list:
    w, h = size
    if (w * 3) % 4:
        raise ValueError("width*3 must be divisible by 4 for GStreamer RGB rows")
    return ["gst-launch-1.0", "-q", "pipewiresrc", f"fd={fd}", f"path={node_id}", "do-timestamp=true",
            "!", "videoconvert", "!", f"video/x-raw,format=RGB,width={w},height={h}", "!", "fdsink", "fd=1"]


class GStreamerPipeWireReader(PipeWireReader):
    def __init__(self, clock: Callable[[], float], spawn=subprocess.Popen, start_thread=True):
        self.clock, self.spawn, self.start_thread = clock, spawn, start_thread
        self.proc = self.size = None
        self.latest = None
        self.error = None
        self._lock = threading.Lock()

    def _start(self, node_id, fd, size):
        raw = fd.fileno() if hasattr(fd, "fileno") else int(fd)
        self.size = tuple(size)
        self.proc = self.spawn(gst_argv(node_id, raw, size), stdout=subprocess.PIPE, stdin=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, pass_fds=(raw,))
        if self.start_thread:
            threading.Thread(target=self._read_loop, daemon=True).start()

    def _read_loop(self):
        w, h = self.size
        n, out = 3 * w * h, self.proc.stdout
        try:
            while True:
                buf = b""
                while len(buf) < n:
                    chunk = out.read(n - len(buf))
                    if not chunk:
                        raise EOFError("gst stream ended")
                    buf += chunk
                frame = Frame(w, h, [buf[y * 3 * w:(y + 1) * 3 * w] for y in range(h)], self.clock())
                with self._lock:
                    self.latest = frame  # always keep only the newest frame
        except Exception as e:
            self.error = e

    def read(self, node_id, fd, size) -> Frame:
        if self.proc is None:
            self._start(node_id, fd, size)
        if self.error:
            raise RuntimeError(f"pipewire capture failed: {self.error}")
        with self._lock:
            f = self.latest
        if f is None:
            raise RuntimeError("no frame received yet")
        return f

    def close(self):
        if self.proc:
            self.proc.terminate()
