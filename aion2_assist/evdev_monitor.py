"""Manual-input monitor: STRICTLY READ-ONLY observation of physical keyboard/mouse-button activity.

Owner-authorised (reading own input devices, which blizz can already do via the `input` group; no permission
change). Guarantees, enforced by tests:
  * event nodes are opened O_RDONLY|O_NONBLOCK|O_CLOEXEC only; nothing is ever written to them;
  * no exclusive grab and no virtual input device is created (this module makes no device-control calls at all);
  * privacy: only a COUNT of key/button events is kept. Key codes and names are discarded immediately.

Assumption (LIVE-VERIFICATION GATE): the assist's own keys are injected through the RemoteDesktop portal/libei
into KWin, not through a /dev/input node, so they do not show up here. Offline this cannot be verified.
Consequences if wrong: the assist would pause itself (fail-safe direction).

The assist's own control hotkeys (toggle/emergency/hold) are physical keys too: list their evdev codes in
`ignore_codes` or the owner pressing them would count as manual play.
"""
import os
import selectors
import struct
import threading
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, NamedTuple, Optional, Tuple

from .adapters import OwnerInput, OwnerState

EV_KEY = 1
KEY_ENTER, KEY_A, KEY_SPACE, BTN_LEFT = 28, 30, 57, 0x110
EVENT = struct.Struct("llHHi")  # struct input_event on 64-bit Linux: timeval(2*long), type, code, value
OPEN_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC


def parse_events(buf: bytes) -> Tuple[List[Tuple[int, int, int]], bytes]:
    """-> ([(type, code, value)...], leftover partial bytes)."""
    n = len(buf) // EVENT.size
    out = [EVENT.unpack_from(buf, i * EVENT.size)[2:] for i in range(n)]
    return out, buf[n * EVENT.size:]


def parse_caps(text: str) -> int:
    """sysfs capability bitmask: space-separated hex words of sizeof(long), most significant first."""
    mask = 0
    for w in text.split():
        mask = (mask << (8 * struct.calcsize("l"))) | int(w, 16)
    return mask


def counts_as_manual(code: int) -> bool:
    """Keyboard keys and mouse buttons; not gamepad/digitizer/tool range (0x120-0x15f, e.g. BTN_TOUCH)."""
    return not (0x120 <= code <= 0x15F)


class DeviceInfo(NamedTuple):
    path: str
    name: str
    keyboard: bool
    mouse: bool
    virtual: bool


def classify(ev_caps: str, key_caps: str):
    """-> (keyboard, mouse) from sysfs capabilities."""
    ev, key = parse_caps(ev_caps), parse_caps(key_caps)
    if not ev & (1 << EV_KEY):
        return False, False
    kb = all(key & (1 << b) for b in (KEY_A, KEY_ENTER, KEY_SPACE))
    return kb, bool(key & (1 << BTN_LEFT))


def scan_devices(sysfs="/sys/class/input", devdir="/dev/input") -> Dict[str, DeviceInfo]:
    found = {}
    try:
        names = sorted(os.listdir(sysfs))
    except OSError:
        return found
    for n in names:
        if not n.startswith("event"):
            continue
        base = os.path.join(sysfs, n, "device")
        try:
            rd = lambda f: open(os.path.join(base, f)).read()
            kb, mouse = classify(rd("capabilities/ev"), rd("capabilities/key"))
            name = rd("name").strip() if os.path.exists(os.path.join(base, "name")) else n
        except (OSError, ValueError):
            continue
        if kb or mouse:
            virtual = "/devices/virtual/" in os.path.realpath(os.path.join(sysfs, n))
            path = os.path.join(devdir, n)
            found[path] = DeviceInfo(path, name, kb, mouse, virtual)
    return found


@dataclass
class Status:
    live: bool
    reason: str


class _Open:
    def __init__(self, info, fd):
        self.info, self.fd, self.carry = info, fd, b""


class ManualInputMonitor:
    def __init__(self, clock: Callable[[], float], sysfs="/sys/class/input", devdir="/dev/input",
                 opener=os.open, reader=os.read, closer=os.close, rescan_s=2.0, stall_s=1.0,
                 ignore_codes: Iterable[int] = (), include_virtual=False):
        self.clock, self.sysfs, self.devdir = clock, sysfs, devdir
        self._open, self._read, self._close = opener, reader, closer
        self.rescan_s, self.stall_s = rescan_s, stall_s
        self.ignore, self.include_virtual = set(ignore_codes), include_virtual
        self.devices: Dict[str, _Open] = {}
        self.sel = selectors.DefaultSelector()
        self._lock = threading.Lock()
        self._count = 0
        self.heartbeat: Optional[float] = None
        self.last_scan: Optional[float] = None
        self.error: Optional[BaseException] = None
        self.thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # -- one iteration (the thread just loops this; tests call it directly) ---
    def step(self, timeout=0.0):
        now = self.clock()
        if self.last_scan is None or now - self.last_scan >= self.rescan_s:
            self._rescan()
            self.last_scan = now
        n = 0
        for key, _ in self.sel.select(timeout) if self.devices else ():
            dev: _Open = key.data
            try:
                data = self._read(dev.fd, EVENT.size * 64)
            except BlockingIOError:
                continue
            except OSError:      # unplugged (ENODEV) or otherwise unreadable
                self._drop(dev.info.path)
                continue
            if not data:         # EOF
                self._drop(dev.info.path)
                continue
            evs, dev.carry = parse_events(dev.carry + data)
            n += sum(1 for t, c, v in evs if t == EV_KEY and counts_as_manual(c) and c not in self.ignore)
        if n:
            with self._lock:
                self._count += n
        self.heartbeat = now

    def _rescan(self):
        found = {p: i for p, i in scan_devices(self.sysfs, self.devdir).items() if self.include_virtual or not i.virtual}
        for p in [p for p in self.devices if p not in found]:
            self._drop(p)
        for p, info in found.items():
            if p in self.devices:
                continue
            try:
                fd = self._open(p, OPEN_FLAGS)  # read-only
            except OSError:
                continue  # unreadable: simply not counted as a live device
            dev = _Open(info, fd)
            self.devices[p] = dev
            self.sel.register(fd, selectors.EVENT_READ, dev)

    def _drop(self, path):
        dev = self.devices.pop(path, None)
        if dev:
            try:
                self.sel.unregister(dev.fd)
            except (KeyError, ValueError):
                pass
            try:
                self._close(dev.fd)
            except OSError:
                pass

    # -- public ------------------------------------------------------------------
    def take_activity(self) -> int:
        """Number of manual key/button events since the last call."""
        with self._lock:
            n, self._count = self._count, 0
        return n

    def status(self) -> Status:
        if self.thread is None and self.heartbeat is None:
            return Status(False, "not_started")
        if self.error is not None or (self.thread is not None and not self.thread.is_alive()):
            return Status(False, "monitor_thread_dead")
        if self.heartbeat is None or self.clock() - self.heartbeat > self.stall_s:
            return Status(False, "monitor_stalled")
        if not any(d.info.keyboard for d in self.devices.values()):
            return Status(False, "no_readable_keyboard_device")
        return Status(True, "live")

    def start(self, poll_s=0.05):
        def loop():
            try:
                while not self._stop.is_set():
                    self.step(poll_s)
            except BaseException as e:  # record: status() reports UNKNOWN
                self.error = e
        self.thread = threading.Thread(target=loop, name="manual-input-monitor", daemon=True)
        self.thread.start()

    def stop(self):
        self._stop.set()
        if self.thread:
            self.thread.join(2.0)
        for p in list(self.devices):
            self._drop(p)


class MonitoredOwnerInput(OwnerInput):
    """Hotkey source + manual-input monitor. `provides_manual_input` is True only while the monitor is LIVE."""
    def __init__(self, inner: OwnerInput, monitor: ManualInputMonitor):
        self.inner, self.monitor = inner, monitor

    @property
    def provides_manual_input(self):
        return self.monitor.status().live

    def poll(self) -> OwnerState:
        st = self.inner.poll()
        if self.monitor.take_activity():
            st.manual = True
        return st


@dataclass
class SelfCheckResult:
    ok: bool
    events_seen: int
    sent_by_assist: int
    live_verified: bool  # always False offline: injected-key invisibility needs the live trial
    note: str


def startup_self_check(monitor: ManualInputMonitor, sink, sleep: Callable[[float], None], window_s=1.0) -> SelfCheckResult:
    """Sends NOTHING. Idle for window_s, then confirms the monitor is live and the assist emitted no key.
    Events seen meanwhile came from the owner/other devices (the assist sent none), so they are reported, not
    blamed on the assist. Whether the assist's *injected* keys are invisible to /dev/input is the live gate."""
    before = len(getattr(sink, "events", []))
    monitor.take_activity()
    sleep(window_s)
    seen, sent = monitor.take_activity(), len(getattr(sink, "events", [])) - before
    st = monitor.status()
    ok = st.live and sent == 0
    note = st.reason if not st.live else ("assist sent a key during the idle self-check" if sent else
            ("owner/other input seen while idle (not attributable to the assist)" if seen else "idle, no events"))
    return SelfCheckResult(ok, seen, sent, False, note)
