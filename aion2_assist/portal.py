"""Wayland adapters written against an injectable D-Bus transport (user-session APIs only).

OFFLINE: only FakeTransport is implemented. SessionBusTransport is a STUB, so nothing here can run
for real. Portal sessions live as long as the *connection* that created them, so a real transport
needs ONE persistent session-bus connection that can also receive signals and own a bus name.
Only `gdbus`/`busctl` CLIs exist on the target (one connection per call) and installing a D-Bus
library is out of scope, hence the stub.
"""
import itertools
from typing import Callable, Dict, List, Optional

from .adapters import FocusProbe, FrameSource, InputSink, OwnerInput, OwnerState
from .frame import Frame

PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
KWIN = "org.kde.KWin"


class DBusTransport:
    supports_receive = False  # can own a name / receive method calls and signals on one connection

    def call(self, dest, path, iface, method, signature, args):
        raise NotImplementedError

    def request(self, dest, path, iface, method, signature, args, options: dict) -> dict:
        """Portal Request pattern: call with a handle_token option, wait for Response, return results.
        Raise on a non-zero response code (user cancelled / denied)."""
        raise NotImplementedError

    def pop_signals(self, iface, member) -> list:
        raise NotImplementedError

    def pop_calls(self, member) -> list:
        raise NotImplementedError


class SessionBusTransport(DBusTransport):
    """STUB: real persistent session-bus client. Deliberately unimplemented (see module docstring)."""
    def _no(self, *a, **k):
        raise NotImplementedError("real D-Bus transport not implemented (offline prototype)")

    call = request = pop_signals = pop_calls = _no


class FakeTransport(DBusTransport):
    """Records every call; replies come from `replies[method]` (value or callable(args))."""
    def __init__(self, replies: Optional[Dict[str, object]] = None, supports_receive=True):
        self.calls: List[dict] = []
        self.replies = replies or {}
        self.signals: Dict[tuple, list] = {}
        self.incoming: Dict[str, list] = {}
        self.supports_receive = supports_receive
        self._tok = itertools.count(1)

    def _reply(self, method, args):
        r = self.replies.get(method, {})
        return r(args) if callable(r) else r

    def call(self, dest, path, iface, method, signature, args):
        self.calls.append(dict(dest=dest, path=path, iface=iface, method=method, signature=signature, args=args))
        return self._reply(method, args)

    def request(self, dest, path, iface, method, signature, args, options):
        options = {"handle_token": f"t{next(self._tok)}", **options}
        self.calls.append(dict(dest=dest, path=path, iface=iface, method=method, signature=signature,
                               args=args, options=options))
        return self._reply(method, args)

    def pop_signals(self, iface, member):
        return self.signals.pop((iface, member), [])

    def pop_calls(self, member):
        return self.incoming.pop(member, [])

    def methods(self):
        return [c["method"] for c in self.calls]


# ---- ScreenCast capture -------------------------------------------------------
class PipeWireReader:
    """Turns a PipeWire node/fd into Frames. STUB: needs libpipewire/GStreamer, not available offline."""
    def read(self, node_id: int, fd, size) -> Frame:
        raise NotImplementedError("PipeWire frame reader not implemented (offline prototype)")


class PortalScreenCastSource(FrameSource):
    IFACE = "org.freedesktop.portal.ScreenCast"

    def __init__(self, transport: DBusTransport, reader: PipeWireReader, size, clock: Callable[[], float]):
        self.t, self.reader, self.size, self.clock = transport, reader, size, clock
        self.session = self.node = self.fd = None

    def open(self):
        """Shows the portal's consent dialog (real run). Monitor source, cursor hidden."""
        t, I = self.t, self.IFACE
        self.session = t.request(PORTAL, PORTAL_PATH, I, "CreateSession", "a{sv}", [], {"session_handle_token": "aion2assist"})["session_handle"]
        t.request(PORTAL, PORTAL_PATH, I, "SelectSources", "oa{sv}", [self.session], {"types": 1, "multiple": False, "cursor_mode": 1})
        res = t.request(PORTAL, PORTAL_PATH, I, "Start", "osa{sv}", [self.session, ""], {})
        streams = res.get("streams") or []
        if len(streams) != 1:
            raise RuntimeError(f"expected exactly one stream, got {len(streams)}")
        self.node = streams[0][0]
        self.fd = t.call(PORTAL, PORTAL_PATH, I, "OpenPipeWireRemote", "oa{sv}", [self.session, {}])

    def grab(self) -> Frame:
        if self.node is None:
            raise RuntimeError("screencast not opened")
        frame = self.reader.read(self.node, self.fd, self.size)
        # Timestamp: the reader should carry the buffer's capture time; fall back to now.
        return frame if frame.ts else Frame(frame.width, frame.height, frame.rows, self.clock())


# ---- RemoteDesktop input ------------------------------------------------------
def _keycodes():
    k = {str(d): 2 + (d - 1) for d in range(1, 10)}
    k["0"] = 11
    for row, base in (("qwertyuiop", 16), ("asdfghjkl", 30), ("zxcvbnm", 44)):
        k.update({c: base + i for i, c in enumerate(row)})
    k.update({f"F{i}": 58 + i for i in range(1, 11)})
    k.update({"F11": 87, "F12": 88})
    return k


EVDEV_KEYCODES = _keycodes()  # Linux input-event-codes.h values for standard keys


class PortalRemoteDesktopSink(InputSink):
    IFACE = "org.freedesktop.portal.RemoteDesktop"

    def __init__(self, transport: DBusTransport, keycodes=None, hold_s=0.03, sleep: Callable[[float], None] = lambda s: None):
        self.t, self.codes, self.hold_s, self._sleep = transport, keycodes or EVDEV_KEYCODES, hold_s, sleep
        self.session = None
        self.down = set()

    def open(self):
        t, I = self.t, self.IFACE
        self.session = t.request(PORTAL, PORTAL_PATH, I, "CreateSession", "a{sv}", [], {"session_handle_token": "aion2assist_in"})["session_handle"]
        t.request(PORTAL, PORTAL_PATH, I, "SelectDevices", "oa{sv}", [self.session], {"types": 1})  # keyboard only
        t.request(PORTAL, PORTAL_PATH, I, "Start", "osa{sv}", [self.session, ""], {})

    def _notify(self, key, state):
        self.t.call(PORTAL, PORTAL_PATH, self.IFACE, "NotifyKeyboardKeycode", "oa{sv}iu",
                    [self.session, {}, self.codes[key], state])

    def key_down(self, key):
        if self.session is None:
            raise RuntimeError("remote desktop session not open")
        if key not in self.codes:
            raise KeyError(f"no keycode for {key!r}")  # refuse before touching the bus
        self.down.add(key)  # mark first: if the call fails midway we still try to release
        self._notify(key, 1)

    def key_up(self, key):
        try:
            self._notify(key, 0)
        finally:
            self.down.discard(key)

    def release_all(self):
        err = None
        for k in sorted(self.down):
            try:
                self.key_up(k)
            except Exception as e:  # keep releasing the rest
                err = err or e
        if err:
            raise err

    def hold(self):
        self._sleep(self.hold_s)  # constant, deterministic

    def close(self):
        try:
            self.release_all()
        finally:
            if self.session:
                self.t.call(PORTAL, self.session, "org.freedesktop.portal.Session", "Close", "", [])


# ---- KWin focus ---------------------------------------------------------------
FOCUS_BUS_NAME, FOCUS_OBJ, FOCUS_IFACE = "org.aion2assist.focus", "/focus", "org.aion2assist.Focus"
FOCUS_SCRIPT = f"""\
var w = workspace.activeWindow || workspace.activeClient;
callDBus("{FOCUS_BUS_NAME}", "{FOCUS_OBJ}", "{FOCUS_IFACE}", "Report",
         w ? (w.resourceClass + "|" + w.caption) : "");
"""


class KWinFocusProbe(FocusProbe):
    """Runs a one-shot KWin script that reports the active window back to us over D-Bus.
    Needs a transport that can own FOCUS_BUS_NAME and receive calls (supports_receive)."""
    def __init__(self, transport: DBusTransport, script_path: str, name="aion2assist_focus"):
        self.t, self.path, self.name = transport, script_path, name
        with open(script_path, "w") as f:
            f.write(FOCUS_SCRIPT)

    @property
    def available(self):
        return bool(self.t.supports_receive)

    def active_window(self):
        if not self.available:
            raise RuntimeError("transport cannot receive KWin script callbacks")
        t = self.t
        self.t.pop_calls("Report")  # drop stale reports
        sid = t.call(KWIN, "/Scripting", "org.kde.kwin.Scripting", "loadScript", "ss", [self.path, self.name])
        try:
            t.call(KWIN, f"/Scripting/Script{sid}", "org.kde.kwin.Script", "run", "", [])
            got = t.pop_calls("Report")
        finally:
            t.call(KWIN, "/Scripting", "org.kde.kwin.Scripting", "unloadScript", "s", [self.name])
        if not got:
            raise RuntimeError("KWin script gave no focus report")
        return got[-1]


# ---- Owner hotkeys via GlobalShortcuts portal --------------------------------
class GlobalShortcutsOwnerInput(OwnerInput):
    """toggle / emergency / hold from the GlobalShortcuts portal (Activated/Deactivated signals).
    CANNOT see the owner's ordinary game keypresses: provides_manual_input is False, so the
    controller refuses to enable. No portal API reports other applications' key events."""
    IFACE = "org.freedesktop.portal.GlobalShortcuts"
    provides_manual_input = False

    def __init__(self, transport: DBusTransport, triggers: Optional[Dict[str, str]] = None):
        self.t, self.triggers, self.session, self.hold = transport, triggers or {}, None, False

    def open(self):
        t, I = self.t, self.IFACE
        self.session = t.request(PORTAL, PORTAL_PATH, I, "CreateSession", "a{sv}", [], {"session_handle_token": "aion2assist_keys"})["session_handle"]
        shortcuts = [(sid, {"description": desc, **({"preferred_trigger": self.triggers[sid]} if sid in self.triggers else {})})
                     for sid, desc in (("toggle", "Assist on/off"), ("emergency", "Assist emergency stop"), ("hold", "Assist hold-to-run"))]
        t.request(PORTAL, PORTAL_PATH, I, "BindShortcuts", "oa(sa{sv})sa{sv}", [self.session, shortcuts, ""], {})

    def poll(self):
        st = OwnerState()
        events = [(1, a) for a in self.t.pop_signals(self.IFACE, "Activated")] + \
                 [(0, a) for a in self.t.pop_signals(self.IFACE, "Deactivated")]
        for on, sig in sorted(events, key=lambda e: e[1][2]):  # sig = (session, shortcut_id, timestamp, opts)
            name = sig[1]
            if name == "toggle" and on:
                st.toggle = True
            elif name == "emergency" and on:
                st.emergency = True
            elif name == "hold":
                self.hold = bool(on)
        st.hold = self.hold
        return st
