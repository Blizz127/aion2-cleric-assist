"""Adapter interfaces (abstract) + MOCK implementations. Real adapters live in portal.py, session_bus.py, pipewire.py, evdev_monitor.py."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .frame import Frame


class FrameSource(ABC):
    @abstractmethod
    def grab(self) -> Frame:
        ...


class FocusProbe(ABC):
    available = True  # False => the controller refuses to enable (no reliable focus signal)

    @abstractmethod
    def active_window(self) -> str:
        """Return a string identifying the active window (title/app id)."""


class InputSink(ABC):
    """key_down/key_up/release_all are the primitives; press() guarantees key-up via finally."""
    @abstractmethod
    def key_down(self, key: str) -> None:
        ...

    @abstractmethod
    def key_up(self, key: str) -> None:
        ...

    @abstractmethod
    def release_all(self) -> None:
        ...

    def hold(self) -> None:
        """Fixed, deterministic key-down duration (no jitter). Real sinks sleep a constant."""

    def press(self, key: str, t: float) -> None:
        self.key_down(key)
        try:
            self.hold()
        finally:
            self.key_up(key)


@dataclass
class OwnerState:
    toggle: bool = False     # edge: enable/disable toggle pressed this poll
    emergency: bool = False  # edge: emergency-stop key
    hold: bool = False       # level: hold-to-run key currently held
    manual: bool = False     # edge: owner pressed a game key by hand (assist's own events excluded)


class OwnerInput(ABC):
    provides_manual_input = True  # False => the controller refuses to enable (cannot see owner keys)

    @abstractmethod
    def poll(self) -> OwnerState:
        ...


# ---- mocks ---------------------------------------------------------------
class MockSink(InputSink):
    def __init__(self):
        self.events: List[Tuple[float, str]] = []  # completed presses (t, key)
        self.raw: List[Tuple[str, str]] = []       # ("down"|"up", key) in order
        self.down = set()
        self.hold_error = None

    def key_down(self, key):
        self.raw.append(("down", key))
        self.down.add(key)

    def key_up(self, key):
        self.raw.append(("up", key))
        self.down.discard(key)

    def release_all(self):
        for k in sorted(self.down):
            self.key_up(k)

    def hold(self):
        if self.hold_error:
            raise self.hold_error

    def press(self, key, t):
        super().press(key, t)
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


# Real adapters (portal call sequences over an injectable D-Bus transport) live in portal.py.
