"""Wayland adapters written against an injectable D-Bus transport (user-session APIs only).

Real transport: session_bus.JeepneyTransport (needs the venv dep jeepney; tested only with a fake
connection). FakeTransport here is the test double. Portal rules followed (flatpak xdg-desktop-portal docs):
persist_mode 0, keyboard device only (pointer only if configured), no clipboard, nothing auto-accepted:
a denied/cancelled Request raises PortalDenied and is never retried or worked around.
"""
import itertools
from abc import ABC, abstractmethod
from typing import Callable, Dict, List, NamedTuple, Optional

from .adapters import FocusProbe, FrameSource, InputSink, OwnerInput, OwnerState
from .frame import Frame

PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SESSION_IFACE = "org.freedesktop.portal.Session"
KWIN = "org.kde.KWin"

# D-Bus signatures of the a{sv} option values we ever send.
OPTION_SIGS = {"handle_token": "s", "session_handle_token": "s", "types": "u", "multiple": "b",
               "cursor_mode": "u", "persist_mode": "u", "description": "s", "preferred_trigger": "s"}


def vdict(d: dict) -> dict:
    """Plain dict -> a{sv} with explicit variant signatures (unknown option names are refused)."""
    return {k: (OPTION_SIGS[k], v) for k, v in d.items()}


class PortalDenied(Exception):
    """User cancelled / portal denied (Response code != 0). Never retried automatically."""


class Incoming(NamedTuple):
    sender: str   # unique bus name of the caller
    args: tuple


class DBusTransport(ABC):
    supports_receive = False  # can own a name and receive method calls on one persistent connection

    @abstractmethod
    def call(self, dest, path, iface, method, signature, args):
        ...

    @abstractmethod
    def request(self, dest, path, iface, method, signature, args, options: dict) -> dict:
        """Portal Request pattern: call (options dict appended as the last a{sv} argument) with a
        handle_token, wait for Request.Response, return unwrapped results. Raises PortalDenied if code != 0."""

    @abstractmethod
    def subscribe(self, iface, member) -> None:
        """Start collecting a signal for pop_signals()."""

    @abstractmethod
    def pop_signals(self, iface, member) -> list:
        ...

    @abstractmethod
    def own_name(self, name) -> None:
        ...

    @abstractmethod
    def name_owner(self, name) -> str:
        ...

    @abstractmethod
    def pop_calls(self, member) -> List[Incoming]:
        ...

    @abstractmethod
    def is_closed(self, session_path) -> bool:
        """True once Session.Closed was seen for this session."""


class FakeTransport(DBusTransport):
    """Records every call; replies come from `replies[method]` (value or callable(args))."""
    def __init__(self, replies: Optional[Dict[str, object]] = None, supports_receive=True):
        self.calls: List[dict] = []
        self.replies = replies or {}
        self.signals: Dict[tuple, list] = {}
        self.incoming: Dict[str, list] = {}
        self.closed = set()
        self.owners: Dict[str, str] = {}
        self.subscribed = []
        self.owned = []
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
        r = self._reply(method, args)
        if isinstance(r, PortalDenied):
            raise r
        return r

    def subscribe(self, iface, member):
        self.subscribed.append((iface, member))

    def pop_signals(self, iface, member):
        return self.signals.pop((iface, member), [])

    def own_name(self, name):
        self.owned.append(name)

    def name_owner(self, name):
        return self.owners[name]

    def pop_calls(self, member):
        return self.incoming.pop(member, [])

    def is_closed(self, session_path):
        return session_path in self.closed

    def methods(self):
        return [c["method"] for c in self.calls]


# ---- ScreenCast capture -------------------------------------------------------
class PipeWireReader(ABC):
    """Turns a PipeWire node/fd into Frames (see pipewire.py for the gst-launch based one)."""
    @abstractmethod
    def read(self, node_id: int, fd, size) -> Frame:
        ...


class PortalScreenCastSource(FrameSource):
    IFACE = "org.freedesktop.portal.ScreenCast"

    def __init__(self, transport: DBusTransport, reader: PipeWireReader, size, clock: Callable[[], float]):
        self.t, self.reader, self.size, self.clock = transport, reader, size, clock
        self.session = self.node = self.fd = None

    def open(self):
        """Portal shows its consent dialog (real run). Monitor source, cursor hidden, NON-persistent."""
        t, I = self.t, self.IFACE
        self.session = t.request(PORTAL, PORTAL_PATH, I, "CreateSession", "a{sv}", [],
                                 {"session_handle_token": "aion2assist_cap"})["session_handle"]
        t.request(PORTAL, PORTAL_PATH, I, "SelectSources", "oa{sv}", [self.session],
                  {"types": 1, "multiple": False, "cursor_mode": 1, "persist_mode": 0})
        res = t.request(PORTAL, PORTAL_PATH, I, "Start", "osa{sv}", [self.session, ""], {})
        streams = res.get("streams") or []
        if len(streams) != 1:
            self.close()
            raise RuntimeError(f"expected exactly one stream, got {len(streams)}")
        self.node = streams[0][0]
        self.fd = t.call(PORTAL, PORTAL_PATH, I, "OpenPipeWireRemote", "oa{sv}", [self.session, {}])

    def grab(self) -> Frame:
        if self.node is None:
            raise RuntimeError("screencast not opened")
        if self.t.is_closed(self.session):
            raise RuntimeError("screencast session closed by the portal")
        frame = self.reader.read(self.node, self.fd, self.size)
        return frame if frame.ts else Frame(frame.width, frame.height, frame.rows, self.clock())

    def close(self):
        if self.session:
            self.t.call(PORTAL, self.session, SESSION_IFACE, "Close", "", [])
            self.session = None


# ---- RemoteDesktop input (keyboard only) ----------------------------------------
from .keys import KEYCODES as EVDEV_KEYCODES  # noqa: E402

DEVICE_KEYBOARD, DEVICE_POINTER = 1, 2


class PortalRemoteDesktopSink(InputSink):
    IFACE = "org.freedesktop.portal.RemoteDesktop"

    def __init__(self, transport: DBusTransport, keycodes=None, hold_s=0.03,
                 sleep: Callable[[float], None] = lambda s: None, pointer_buttons=False):
        self.t, self.codes, self.hold_s, self._sleep = transport, keycodes or EVDEV_KEYCODES, hold_s, sleep
        self.types = DEVICE_KEYBOARD | (DEVICE_POINTER if pointer_buttons else 0)  # pointer only if configured
        self.session = None
        self.down = set()

    def open(self):
        t, I = self.t, self.IFACE
        self.session = t.request(PORTAL, PORTAL_PATH, I, "CreateSession", "a{sv}", [],
                                 {"session_handle_token": "aion2assist_in"})["session_handle"]
        t.request(PORTAL, PORTAL_PATH, I, "SelectDevices", "oa{sv}", [self.session],
                  {"types": self.types, "persist_mode": 0})
        res = t.request(PORTAL, PORTAL_PATH, I, "Start", "osa{sv}", [self.session, ""], {})
        devices = res.get("devices", 0)
        if (devices & ~self.types) or not devices & DEVICE_KEYBOARD or res.get("clipboard_enabled"):
            self.close()  # granted something we did not ask for (or no keyboard): refuse, never use it
            raise PortalDenied(f"unexpected grant devices={devices} clipboard={res.get('clipboard_enabled')}")

    def _notify(self, key, state):
        self.t.call(PORTAL, PORTAL_PATH, self.IFACE, "NotifyKeyboardKeycode", "oa{sv}iu",
                    [self.session, {}, self.codes[key], state])

    def key_down(self, key):
        if self.session is None:
            raise RuntimeError("remote desktop session not open")
        if self.t.is_closed(self.session):
            raise RuntimeError("remote desktop session closed by the portal")
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
                s, self.session = self.session, None
                self.t.call(PORTAL, s, SESSION_IFACE, "Close", "", [])


# ---- KWin focus ---------------------------------------------------------------
FOCUS_BUS_NAME, FOCUS_OBJ, FOCUS_IFACE = "org.aion2assist.focus", "/focus", "org.aion2assist.Focus"
KWIN_SCRIPT_ID = "aion2assist_focus"


class KWinFocusProbe(FocusProbe):
    """Receives focus reports pushed by the KWin script in kwin_script/ (owner-installed package).

    The script uses ONLY documented KWin scripting API: workspace.windowActivated, workspace.activeWindow,
    Window.resourceClass/desktopFileName/caption, callDBus(service, path, interface, method, args...).
    We do not load/run scripts over D-Bus. Each query: (1) take new reports, accepting only calls whose sender
    owns org.kde.KWin; (2) require that KWin still reports the script loaded (Scripting.isScriptLoaded(pluginName),
    verified in KWin master scripting.h/.cpp, not on the installed Plasma; any error => fail closed); (3) return the latest report."""
    def __init__(self, transport: DBusTransport, script_id: str = KWIN_SCRIPT_ID):
        self.t, self.script_id, self.current, self.opened = transport, script_id, None, False

    @property
    def available(self):
        return bool(self.t.supports_receive) and self.opened

    def open(self):
        self.t.own_name(FOCUS_BUS_NAME)
        self.opened = True

    def active_window(self):
        if not self.available:
            raise RuntimeError("focus probe unavailable (no receiving transport / not opened)")
        owner = self.t.name_owner(KWIN)
        for inc in self.t.pop_calls("Report"):
            if inc.sender == owner and len(inc.args) == 1 and isinstance(inc.args[0], str):
                self.current = inc.args[0]  # reports from anyone else are ignored
        if not self.t.call(KWIN, "/Scripting", "org.kde.kwin.Scripting", "isScriptLoaded", "s", [self.script_id]):
            raise RuntimeError("KWin focus script is not loaded")
        if self.current is None:
            raise RuntimeError("no focus report received yet")
        return self.current


# ---- Owner hotkeys via GlobalShortcuts portal --------------------------------
class GlobalShortcutsOwnerInput(OwnerInput):
    """toggle / emergency / hold from the GlobalShortcuts portal (Activated/Deactivated signals).
    Manual game keypresses are UNKNOWN: no portal API reports other applications' input, so
    provides_manual_input is False and the controller REFUSES to enable (hard gate)."""
    IFACE = "org.freedesktop.portal.GlobalShortcuts"
    provides_manual_input = False
    SHORTCUTS = (("toggle", "Assist on/off"), ("emergency", "Assist emergency stop"), ("hold", "Assist hold-to-run"))

    def __init__(self, transport: DBusTransport, triggers: Optional[Dict[str, str]] = None):
        self.t, self.triggers, self.session, self.hold = transport, triggers or {}, None, False

    def open(self):
        t, I = self.t, self.IFACE
        for m in ("Activated", "Deactivated"):
            t.subscribe(I, m)
        self.session = t.request(PORTAL, PORTAL_PATH, I, "CreateSession", "a{sv}", [],
                                 {"session_handle_token": "aion2assist_keys"})["session_handle"]
        shortcuts = [(sid, vdict({"description": desc, **({"preferred_trigger": self.triggers[sid]} if sid in self.triggers else {})}))
                     for sid, desc in self.SHORTCUTS]
        res = t.request(PORTAL, PORTAL_PATH, I, "BindShortcuts", "oa(sa{sv})sa{sv}", [self.session, shortcuts, ""], {})
        bound = {sc[0] for sc in res.get("shortcuts", [])}
        if bound != {sid for sid, _ in self.SHORTCUTS}:
            raise PortalDenied(f"not all shortcuts bound: {sorted(bound)}")

    def poll(self):
        if self.session is None or self.t.is_closed(self.session):
            raise RuntimeError("global shortcuts session not open / closed")
        st = OwnerState()
        events = [(1, a) for a in self.t.pop_signals(self.IFACE, "Activated")] + \
                 [(0, a) for a in self.t.pop_signals(self.IFACE, "Deactivated")]
        for on, sig in sorted(events, key=lambda e: e[1][2]):  # sig = (session, shortcut_id, timestamp, opts)
            if sig[0] != self.session:
                continue
            name = sig[1]
            if name == "toggle" and on:
                st.toggle = True
            elif name == "emergency" and on:
                st.emergency = True
            elif name == "hold":
                self.hold = bool(on)
        st.hold = self.hold
        return st
