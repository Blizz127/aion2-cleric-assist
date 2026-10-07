"""Fake /sys + pipes as fake event nodes. Nothing here reads a real input device."""
import errno
import fcntl
import os
import struct
import tempfile
import time
import unittest
from unittest import mock

from aion2_assist import evdev_monitor as em
from aion2_assist.adapters import MockFocus, MockFrameSource, MockOwnerInput, MockSink
from aion2_assist.evdev_monitor import (EVENT, ManualInputMonitor, MonitoredOwnerInput, classify, parse_caps,
                                        parse_events, startup_self_check)
from aion2_assist.replay import FakeClock
from aion2_assist.safety import Controller
from aion2_assist.synth import make_frame
from tests.helpers import DMG_ONLY, PROFILE, cfg

KB_KEYS = (em.KEY_A, em.KEY_ENTER, em.KEY_SPACE, 16, 17)
MOUSE_KEYS = (em.BTN_LEFT, 0x111)


def words(bits):
    mask = sum(1 << b for b in bits)
    ws = []
    while mask:
        ws.append(mask & (2 ** 64 - 1))
        mask >>= 64
    return " ".join(format(w, "x") for w in reversed(ws)) or "0"


def ev(t, c, v):
    return EVENT.pack(1, 2, t, c, v)


class FakeSystem:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sys = os.path.join(self.tmp.name, "sys/class/input")
        self.real = os.path.join(self.tmp.name, "sys/devices")
        os.makedirs(self.sys)
        self.pipes, self.opens = {}, []

    def add(self, name, keys, evbits=(0, 1), virtual=False):
        base = os.path.join(self.real, "virtual/input" if virtual else "pci/usb", name)
        os.makedirs(os.path.join(base, "device/capabilities"))
        w = lambda f, t: open(os.path.join(base, "device", f), "w").write(t)
        w("name", f"fake {name}\n")
        w("capabilities/ev", words(evbits))
        w("capabilities/key", words(keys))
        os.symlink(base, os.path.join(self.sys, name))
        r, wfd = os.pipe()
        os.set_blocking(r, False)
        self.pipes["/dev/input/" + name] = (r, wfd)

    def remove(self, name):
        os.unlink(os.path.join(self.sys, name))

    def opener(self, path, flags):
        self.opens.append((path, flags))
        return self.pipes[path][0]

    def send(self, name, *events):
        os.write(self.pipes["/dev/input/" + name][1], b"".join(events))

    def monitor(self, clock=None, **kw):
        return ManualInputMonitor(clock or FakeClock(), sysfs=self.sys, devdir="/dev/input", opener=self.opener,
                                  closer=lambda fd: None, **kw)


class Parsing(unittest.TestCase):
    def test_event_struct_and_partial_carry(self):
        data = ev(1, 30, 1) + ev(0, 0, 0)
        self.assertEqual(parse_events(data + data[:7]), [(1, 30, 1), (0, 0, 0)])
        self.assertEqual(EVENT.size, 24)

    def test_caps_and_classification(self):
        self.assertEqual(parse_caps("120013"), 0x120013)
        self.assertEqual(parse_caps("1 0"), 1 << 64)  # most significant word first
        self.assertEqual(classify(words((0, 1)), words(KB_KEYS)), (True, False))
        self.assertEqual(classify(words((0, 1)), words(MOUSE_KEYS)), (False, True))
        self.assertEqual(classify(words((0, 1)), words((116,))), (False, False))       # power button
        self.assertEqual(classify(words((0, 1)), words((0x130, 0x131))), (False, False))  # gamepad
        self.assertEqual(classify(words((0, 2)), words(KB_KEYS)), (False, False))      # no EV_KEY

    def test_scan_selects_and_flags_virtual(self):
        fs = FakeSystem()
        fs.add("event0", KB_KEYS)
        fs.add("event1", MOUSE_KEYS)
        fs.add("event2", (116,))
        fs.add("event3", KB_KEYS, virtual=True)
        found = em.scan_devices(fs.sys)
        self.assertEqual(sorted(os.path.basename(p) for p in found), ["event0", "event1", "event3"])
        self.assertTrue(found["/dev/input/event3"].virtual and not found["/dev/input/event0"].virtual)


class Monitoring(unittest.TestCase):
    def setUp(self):
        self.fs = FakeSystem()
        self.fs.add("event0", KB_KEYS)
        self.fs.add("event1", MOUSE_KEYS)
        self.fs.add("event9", KB_KEYS, virtual=True)
        self.clock = FakeClock()
        self.m = self.fs.monitor(self.clock)

    def test_key_and_button_events_counted_others_ignored(self):
        self.m.step()
        self.assertEqual(sorted(self.m.devices), ["/dev/input/event0", "/dev/input/event1"])  # virtual excluded
        self.fs.send("event0", ev(1, 30, 1), ev(0, 0, 0), ev(1, 30, 0), ev(2, 0, 5), ev(1, 30, 2))
        self.fs.send("event1", ev(1, em.BTN_LEFT, 1), ev(1, 0x14A, 1), ev(1, 0x133, 1))  # click; BTN_TOUCH; gamepad
        self.m.step()
        self.assertIs(self.m.take_activity(), True)  # press/release/repeat/click
        self.assertIs(self.m.take_activity(), False)

    def test_ignore_codes_for_assist_hotkeys(self):
        m = self.fs.monitor(self.clock, ignore_codes=[67])
        m.step()
        self.fs.send("event0", ev(1, 67, 1), ev(1, 67, 0))
        m.step()
        self.assertFalse(m.take_activity())
        self.fs.send("event0", ev(1, 68, 1))
        m.step()
        self.assertTrue(m.take_activity())

    def test_partial_trailing_bytes_are_dropped_not_buffered(self):
        self.m.step()
        raw = ev(1, 30, 1)
        self.fs.send("event0", raw[:10])
        self.m.step()
        self.assertFalse(self.m.take_activity())
        self.fs.send("event0", raw[10:])  # no carry-over: the tail alone is not a whole event
        self.m.step()
        self.assertFalse(self.m.take_activity())

    def test_only_boolean_and_timestamp_retained(self):
        marker = 0x1D3  # distinctive key code
        self.clock.t = 5.0
        self.m.step()
        raw = ev(1, marker, 1) + ev(1, marker, 0) + ev(1, marker, 2)
        self.fs.send("event0", raw)
        self.clock.t = 7.5
        self.m.step()
        self.assertEqual(self.m.last_activity, 7.5)
        self.assertIs(self.m.take_activity(), True)
        self.assertEqual(self.m.last_activity, 7.5)  # timestamp survives, activity flag cleared
        allowed = {"clock", "sysfs", "devdir", "_open", "_read", "_close", "rescan_s", "stall_s", "ignore",
                   "include_virtual", "devices", "sel", "_lock", "_active", "last_activity", "heartbeat", "last_scan",
                   "error", "thread", "_stop"}
        self.assertEqual(set(vars(self.m)), allowed)  # no counter-like attribute exists
        self.assertIsInstance(self.m._active, bool)
        self.assertEqual(self.m.ignore, set())  # configuration only, never observations

        def walk(o, depth=0):
            yield o
            if depth < 4 and isinstance(o, (list, tuple, set, frozenset)):
                for x in o:
                    yield from walk(x, depth + 1)
            elif depth < 4 and isinstance(o, dict):
                for k, v in o.items():
                    yield from walk(k, depth + 1)
                    yield from walk(v, depth + 1)
            elif depth < 4 and hasattr(o, "__dict__") and type(o).__module__.startswith("aion2_assist"):
                yield from walk(vars(o), depth + 1)
        for name, val in vars(self.m).items():
            if name in ("clock", "_open", "_read", "_close", "_lock", "sel", "thread", "_stop"):
                continue
            for x in walk(val):
                self.assertNotEqual(x, marker, name)
                self.assertNotIsInstance(x, (bytes, bytearray), name)  # no raw event content
        for dev in self.m.devices.values():
            self.assertEqual(set(vars(dev)), {"info", "fd"})

    def test_hotplug_add_and_remove(self):
        self.m.step()
        self.fs.add("event5", KB_KEYS)
        self.m.step()
        self.assertNotIn("/dev/input/event5", self.m.devices)  # not rescanned yet
        self.clock.t = 2.0
        self.m.step()
        self.assertIn("/dev/input/event5", self.m.devices)
        self.fs.remove("event5")
        self.clock.t = 4.0
        self.m.step()
        self.assertNotIn("/dev/input/event5", self.m.devices)

    def test_unplug_between_scans_via_read_error(self):
        def reader(fd, n):
            raise OSError(errno.ENODEV, "gone")
        m = ManualInputMonitor(self.clock, sysfs=self.fs.sys, opener=self.fs.opener, closer=lambda fd: None, reader=reader)
        m.step()
        self.fs.send("event0", ev(1, 30, 1))
        self.fs.send("event1", ev(1, em.BTN_LEFT, 1))
        m.step()
        self.assertEqual(m.devices, {})
        self.assertEqual(m.status().reason, "no_readable_keyboard_device")


class Status(unittest.TestCase):
    def test_states(self):
        fs = FakeSystem()
        clock = FakeClock()
        m = fs.monitor(clock)
        self.assertEqual(m.status().reason, "not_started")
        m.step()
        self.assertEqual(m.status().reason, "no_readable_keyboard_device")  # nothing present
        fs.add("event1", MOUSE_KEYS)
        clock.t = 2.0
        m.step()
        self.assertEqual(m.status().reason, "no_readable_keyboard_device")  # mouse alone is not enough
        fs.add("event0", KB_KEYS)
        clock.t = 4.0
        m.step()
        self.assertTrue(m.status().live)
        clock.t = 5.5  # loop stopped stepping: stalled
        self.assertEqual(m.status().reason, "monitor_stalled")

    def test_unopenable_device_is_not_live(self):
        fs = FakeSystem()
        fs.add("event0", KB_KEYS)
        m = ManualInputMonitor(FakeClock(), sysfs=fs.sys, opener=mock.Mock(side_effect=PermissionError))
        m.step()
        self.assertEqual(m.status().reason, "no_readable_keyboard_device")

    def test_thread_death_reported(self):
        fs = FakeSystem()
        fs.add("event0", KB_KEYS)
        def reader(fd, n):
            raise RuntimeError("bug")
        m = ManualInputMonitor(time.monotonic, sysfs=fs.sys, opener=fs.opener, closer=lambda fd: None, reader=reader)
        m.start(poll_s=0.01)
        fs.send("event0", ev(1, 30, 1))
        m.thread.join(2)
        self.assertEqual(m.status().reason, "monitor_thread_dead")
        m.stop()

    def test_real_thread_sees_events_and_stops(self):
        fs = FakeSystem()
        fs.add("event0", KB_KEYS)
        m = fs.monitor(time.monotonic)
        m.start(poll_s=0.01)
        fs.send("event0", ev(1, 30, 1))
        for _ in range(200):
            if m.status().live and m._active:
                break
            time.sleep(0.01)
        self.assertTrue(m.status().live)
        self.assertTrue(m.take_activity())
        m.stop()


class ReadOnlyGuarantee(unittest.TestCase):
    def test_open_flags_are_read_only_nonblocking(self):
        fs = FakeSystem()
        fs.add("event0", KB_KEYS)
        fs.add("event1", MOUSE_KEYS)
        m = fs.monitor()
        m.step()
        self.assertEqual(len(fs.opens), 2)
        for _, flags in fs.opens:
            self.assertEqual(flags & os.O_ACCMODE, os.O_RDONLY)
            self.assertTrue(flags & os.O_NONBLOCK)
        self.assertEqual(em.OPEN_FLAGS & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC), 0)

    def test_no_ioctl_no_write_at_runtime(self):
        fs = FakeSystem()
        fs.add("event0", KB_KEYS)
        m = fs.monitor()
        m.step()
        fs.send("event0", ev(1, 30, 1))
        boom = mock.Mock(side_effect=AssertionError("forbidden"))
        with mock.patch("os.write", boom), mock.patch("fcntl.ioctl", boom), mock.patch("os.open", boom):
            m.step()
            m.take_activity()
            m.status()
        boom.assert_not_called()

    def test_source_has_no_grab_write_or_virtual_device_code(self):
        src = open(em.__file__).read()
        for token in ("ioctl", "fcntl", "EVIOCGRAB", "uinput", "O_WRONLY", "O_RDWR", "os.write", "'w'", '"w"', "O_CREAT"):
            self.assertNotIn(token, src, token)


class WithController(unittest.TestCase):
    def rig(self, fs):
        self.clock, self.sink, self.src = FakeClock(), MockSink(), MockFrameSource()
        self.hot = MockOwnerInput()
        self.hot.hold = True
        self.mon = fs.monitor(self.clock)
        self.owner = MonitoredOwnerInput(self.hot, self.mon)
        self.logs = []
        self.ctl = Controller(cfg(DMG_ONLY, manual_pause_ms=1000, watchdog_ms=10 ** 6), PROFILE, self.src, MockFocus(),
                              self.sink, self.owner, self.clock, self.logs.append)

    def tick(self, t, toggle=False):
        self.clock.t = t
        self.mon.step()
        if toggle:
            self.hot.press("toggle")
        self.src.frame = make_frame(t)
        return self.ctl.tick()

    def test_enable_refused_until_monitor_live(self):
        fs = FakeSystem()
        self.rig(fs)
        self.assertEqual(self.tick(0.0, toggle=True)["reason"], "enable_refused:no_manual_input_signal")
        fs.add("event0", KB_KEYS)
        self.clock.t = 2.0
        rec = self.tick(2.0, toggle=True)
        self.assertEqual((rec["event"], self.sink.keys), ("decision", ["4"]))

    def test_owner_keypress_pauses_within_one_tick_then_resumes(self):
        fs = FakeSystem()
        fs.add("event0", KB_KEYS)
        self.rig(fs)
        self.tick(0.0)  # first scan
        self.tick(0.3, toggle=True)
        n = len(self.sink.keys)
        fs.send("event0", ev(1, 30, 1), ev(1, 30, 0))
        self.assertEqual(self.tick(0.6)["reason"], "manual_pause")  # the very next tick
        self.assertEqual(len(self.sink.keys), n)
        self.assertEqual(self.tick(1.5)["reason"], "manual_pause")
        self.assertEqual(self.tick(1.6)["event"], "decision")

    def test_monitor_death_disables_and_releases(self):
        fs = FakeSystem()
        fs.add("event0", KB_KEYS)
        self.rig(fs)
        self.tick(0.0)
        self.tick(0.3, toggle=True)
        self.assertTrue(self.ctl.enabled)
        self.sink.down.add("held")
        self.mon.error = RuntimeError("monitor thread died")
        rec = self.tick(0.6)
        self.assertEqual(rec["reason"], "signal_lost:no_manual_input_signal")
        self.assertFalse(self.ctl.enabled)
        self.assertEqual(self.sink.down, set())
        self.assertEqual(self.tick(0.9)["reason"], "disabled")

    def test_monitor_stall_disables(self):
        fs = FakeSystem()
        fs.add("event0", KB_KEYS)
        self.rig(fs)
        self.tick(0.0)
        self.tick(0.3, toggle=True)
        self.clock.t = 3.0  # no mon.step(): stalled
        self.src.frame = make_frame(3.0)
        self.assertEqual(self.ctl.tick()["reason"], "signal_lost:no_manual_input_signal")

    def test_self_check_sends_nothing(self):
        fs = FakeSystem()
        fs.add("event0", KB_KEYS)
        self.rig(fs)
        self.mon.step()
        r = startup_self_check(self.mon, self.sink, lambda s: self.mon.step(), 1.0)
        self.assertEqual((r.ok, r.activity_seen, r.sent_by_assist, r.live_verified), (True, False, 0, False))
        self.sink.events.append((0.0, "4"))
        self.sink.events.append((0.1, "4"))
        fs.send("event0", ev(1, 30, 1))
        r = startup_self_check(self.mon, self.sink, lambda s: (self.sink.events.append((0.2, "x")), self.mon.step()), 1.0)
        self.assertFalse(r.ok)
        self.assertEqual(r.sent_by_assist, 1)

    def test_self_check_reports_owner_input_without_blaming_assist(self):
        fs = FakeSystem()
        fs.add("event0", KB_KEYS)
        self.rig(fs)
        self.mon.step()
        def sleep(s):
            fs.send("event0", ev(1, 30, 1))
            self.mon.step()
        r = startup_self_check(self.mon, self.sink, sleep)
        self.assertEqual((r.ok, r.activity_seen, r.sent_by_assist), (True, True, 0))
        self.assertIn("not attributable", r.note)


if __name__ == "__main__":
    unittest.main()
