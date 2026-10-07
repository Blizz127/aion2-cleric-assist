"""Adapter interfaces + MOCK implementations. Real Wayland adapters are stubs (NotImplementedError)."""
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .frame import Frame


class FrameSource:
    def grab(self) -> Frame:
        raise NotImplementedError


class FocusProbe:
    def active_window(self) -> str:
        """Return a string identifying the active window (title/app id)."""
        raise NotImplementedError


class InputSink:
    def press(self, key: str, t: float) -> None:
        raise NotImplementedError


@dataclass
class OwnerState:
    toggle: bool = False     # edge: enable/disable toggle pressed this poll
    emergency: bool = False  # edge: emergency-stop key
    hold: bool = False       # level: hold-to-run key currently held
    manual: bool = False     # edge: owner pressed a game key by hand (assist's own events excluded)


class OwnerInput:
    def poll(self) -> OwnerState:
        raise NotImplementedError


# ---- mocks ---------------------------------------------------------------
class MockSink(InputSink):
    def __init__(self):
        self.events: List[Tuple[float, str]] = []

    def press(self, key, t):
        self.events.append((t, key))

    @property
    def keys(self):
        return [k for _, k in self.events]


class MockFrameSource(FrameSource):
    """Returns `frame` (a Frame or a callable()->Frame); raises if `error` is set."""
    def __init__(self, frame=None):
        self.frame, self.error = frame, None

    def grab(self):
        if self.error:
            raise self.error
        return self.frame() if callable(self.frame) else self.frame


class MockFocus(FocusProbe):
    def __init__(self, title="AION2 - Client"):
        self.title = title

    def active_window(self):
        return self.title


class MockOwnerInput(OwnerInput):
    """Set fields, or call .press(...) to queue one-shot edges for the next poll."""
    def __init__(self):
        self.hold = False
        self._edges = {"toggle": False, "emergency": False, "manual": False}

    def press(self, name):
        self._edges[name] = True

    def poll(self):
        s = OwnerState(hold=self.hold, **self._edges)
        self._edges = {k: False for k in self._edges}
        return s


# ---- real adapters: STUBS, deliberately unimplemented -----------------------
class PortalScreenCastSource(FrameSource):
    """STUB. Real: xdg-desktop-portal ScreenCast -> PipeWire stream (user consent dialog)."""
    def grab(self):
        raise NotImplementedError("portal ScreenCast/PipeWire capture is not implemented (offline prototype)")


class KWinFocusProbe(FocusProbe):
    """STUB. Real: KWin active-window query over D-Bus/scripting."""
    def active_window(self):
        raise NotImplementedError("KWin active-window query is not implemented (offline prototype)")


class PortalLibeiSink(InputSink):
    """STUB. Real: xdg-desktop-portal RemoteDesktop session + libei (consented input path)."""
    def press(self, key, t):
        raise NotImplementedError("portal RemoteDesktop/libei input is not implemented (offline prototype)")


class HotkeyOwnerInput(OwnerInput):
    """STUB. Real: owner hotkeys (toggle/emergency/hold) and manual-input detection."""
    def poll(self):
        raise NotImplementedError("owner hotkey/manual-input source is not implemented (offline prototype)")
