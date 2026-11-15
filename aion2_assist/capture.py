"""Record full-frame calibration screenshots through the same ScreenCast + PipeWire path that `run` uses.

Read-only on purpose. It opens a ScreenCast session only: the owner picks the screen in the portal's consent
dialog, and the session is not persisted. It never opens a RemoteDesktop session, so it cannot send input.
GStreamer writes each frame with lossless `pngenc` straight from the stream at its native size (there is no
scaling element), after the same `videoconvert` to RGB as the live reader, so the pixels match what `run` sees.

    python3 -m aion2_assist capture [--out shots] [--every 3] [--seconds 180]

Frames go into a NEW folder <out>/<UTC stamp>/frame-00000.png and so on, and no existing file is ever reused.
Recording stops at --seconds, on Ctrl-C, when GStreamer exits, or when the portal closes the session.
"""
import datetime
import fractions
import json
import os
import shutil
import signal
import struct
import subprocess
import time

from .portal import PORTAL, PORTAL_PATH, SESSION_IFACE, PortalDenied

SCREENCAST = "org.freedesktop.portal.ScreenCast"
EVERY_RANGE = (0.2, 60.0)
SECONDS_RANGE = (1.0, 3600.0)


def gst_capture_argv(node_id, fd, every_s, pattern):
    rate = fractions.Fraction(1.0 / every_s).limit_denominator(1000)
    return ["gst-launch-1.0", "-q", "pipewiresrc", f"fd={fd}", f"path={node_id}", "do-timestamp=true",
            "!", "videorate", "!", f"video/x-raw,framerate={rate.numerator}/{rate.denominator}",
            "!", "videoconvert", "!", "video/x-raw,format=RGB", "!", "pngenc", "!",
            "multifilesink", f"location={pattern}"]


def png_size(path):
    """(width, height) from a PNG's IHDR, or None if the file is not a complete PNG header yet."""
    with open(path, "rb") as f:
        head = f.read(24)
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", head[16:24])


def _frames(folder):
    return sorted(n for n in os.listdir(folder) if n.startswith("frame-") and n.endswith(".png"))


def run_capture(out_dir, every_s, seconds, *, transport_factory, spawn=subprocess.Popen, clock=time.monotonic,
                sleep=time.sleep, which=shutil.which, now=None, emit=print):
    """Returns 0 when frames were written without error, 2 for invalid input, 3 for portal refusal,
    or 4 for capture failure/no frames. Saved frames are preserved even when capture fails.
    """
    if not (EVERY_RANGE[0] <= every_s <= EVERY_RANGE[1]) or not (SECONDS_RANGE[0] <= seconds <= SECONDS_RANGE[1]):
        emit(f"capture refused: --every must be {EVERY_RANGE[0]}-{EVERY_RANGE[1]} s and --seconds "
             f"{SECONDS_RANGE[0]:g}-{SECONDS_RANGE[1]:g} s")
        return 2
    if not which("gst-launch-1.0"):
        emit("capture refused: gst-launch-1.0 not found (needs GStreamer with gst-plugin-pipewire)")
        return 2
    stamp = (now or (lambda: datetime.datetime.now(datetime.timezone.utc)))().strftime("%Y%m%dT%H%M%SZ")
    folder = os.path.join(out_dir, stamp)
    try:
        os.makedirs(folder)
    except FileExistsError:
        emit(f"capture refused: {folder} already exists (frames are never overwritten)")
        return 2
    emit(f"capture: the portal will ask which screen to share; pick the one Aion 2 is on. One frame every "
         f"{every_s:g} s for up to {seconds:g} s into {folder}. Nothing is sent to the game. Ctrl-C stops early.")
    transport = session = proc = fd = None
    reason, code = "duration reached", 0
    cleanup_errors = []
    started = clock()
    try:
        transport = transport_factory()
        session = transport.request(PORTAL, PORTAL_PATH, SCREENCAST, "CreateSession", "a{sv}", [],
                                    {"session_handle_token": "aion2assist_shots"})["session_handle"]
        transport.request(PORTAL, PORTAL_PATH, SCREENCAST, "SelectSources", "oa{sv}", [session],
                          {"types": 1, "multiple": False, "cursor_mode": 1, "persist_mode": 0})
        streams = transport.request(PORTAL, PORTAL_PATH, SCREENCAST, "Start", "osa{sv}", [session, ""], {}).get(
            "streams") or []
        if len(streams) != 1:
            raise RuntimeError(f"expected exactly one stream, got {len(streams)}")
        fd = transport.call(PORTAL, PORTAL_PATH, SCREENCAST, "OpenPipeWireRemote", "oa{sv}", [session, {}])
        raw = fd.fileno() if hasattr(fd, "fileno") else int(fd)
        argv = gst_capture_argv(streams[0][0], raw, every_s, os.path.join(folder, "frame-%05d.png"))
        with open(os.path.join(folder, "gst-stderr.log"), "wb") as log:
            proc = spawn(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log, pass_fds=(raw,))
        started = clock()
        deadline, next_note = started + seconds, started + 10.0
        while True:
            if proc.poll() is not None:
                reason = f"gstreamer exited with code {proc.returncode} (see gst-stderr.log)"
                if proc.returncode != 0:
                    code = 4
                break
            if transport.is_closed(session):
                reason = "the portal closed the screen-share session"
                break
            t = clock()
            if t >= deadline:
                break
            if t >= next_note:
                emit(f"capture: {len(_frames(folder))} frames so far")
                next_note = t + 10.0
            sleep(min(0.5, max(0.0, deadline - t)))
    except PortalDenied as e:
        reason, code = f"portal refused: {e}", 3
    except KeyboardInterrupt:
        reason = "stopped by the owner"
    except Exception as e:  # report the error; cleanup below attempts each independent resource
        reason, code = f"capture failed: {e}", 4
    finally:
        try:
            if proc is not None:
                try:
                    if proc.poll() is None:
                        proc.terminate()
                        proc.wait(timeout=5)
                except Exception as e:
                    if not isinstance(e, subprocess.TimeoutExpired):
                        cleanup_errors.append(f"gstreamer cleanup failed: {e}")
                    try:
                        proc.kill()
                        proc.wait(timeout=5)
                    except Exception as e:
                        cleanup_errors.append(f"gstreamer kill/wait cleanup failed: {e}")
        finally:
            if transport is not None:
                try:
                    if session is not None:
                        try:
                            transport.call(PORTAL, session, SESSION_IFACE, "Close", "", [])
                        except Exception:
                            pass  # session may already be gone; still drop the connection
                finally:
                    try:
                        transport.close()
                    except Exception as e:
                        cleanup_errors.append(f"transport cleanup failed: {e}")
    if cleanup_errors:
        code = code or 4
        reason += "; " + "; ".join(cleanup_errors)
    frames = _frames(folder)
    sizes = {png_size(os.path.join(folder, n)) for n in frames}
    sizes.discard(None)
    if code == 0 and not frames:
        code = 4
    if not os.listdir(folder):
        os.rmdir(folder)  # nothing was started; a GStreamer log, if any, is kept for diagnosis
    emit(json.dumps({"event": "capture_summary", "folder": folder, "frames": len(frames),
                     "frame_size": sorted(sizes)[0] if len(sizes) == 1 else sorted(sizes),
                     "seconds": round(clock() - started, 1), "every_s": every_s, "reason": reason,
                     "input_sent": False}, default=list))
    return code


def _open_bus():
    try:
        from .session_bus import open_session_bus  # needs jeepney from the project venv
    except ImportError as e:
        raise RuntimeError(f"{e}: run with .venv/bin/python (./deploy-alienware.sh --setup-venv creates it)") from e
    return open_session_bus()


def main(args):
    previous = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP)}
    for s in previous:
        signal.signal(s, signal.default_int_handler)  # same clean stop as Ctrl-C
    try:
        return run_capture(args.out, args.every, args.seconds, transport_factory=_open_bus,
                           emit=lambda m: print(m, flush=True))
    finally:
        for s, h in previous.items():
            signal.signal(s, h)


def add_parser(sub):
    c = sub.add_parser("capture", help="record full-frame calibration screenshots (screen share only; sends no input)",
                       description=__doc__.split("\n\n")[1])
    c.add_argument("--out", default="shots", help="parent folder; each run makes a new timestamped folder (default: %(default)s)")
    c.add_argument("--every", type=float, default=3.0, help="seconds between frames, 0.2-60 (default: %(default)s)")
    c.add_argument("--seconds", type=float, default=180.0, help="how long to record, 1-3600 (default: %(default)s)")
    c.set_defaults(fn=main)
