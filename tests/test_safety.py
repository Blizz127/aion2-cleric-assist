import dataclasses
import io
import json
import math
import random
import unittest

from aion2_assist.adapters import MockFocus, MockFrameSource, MockOwnerInput, MockSink
from aion2_assist.perception import Perceiver
from aion2_assist.profile import ProfileMismatch
from aion2_assist.replay import FakeClock
from aion2_assist.safety import Controller, json_logger
from aion2_assist.frame import Reading
from aion2_assist.synth import make_frame
from tests.helpers import DMG_ONLY, PROFILE, Rig, cfg


class Gate(unittest.TestCase):
    def assertSuppressed(self, rec, reason, rig):
        self.assertEqual((rec["event"], rec["reason"]), ("suppress", reason))
        self.assertEqual(rig.keys, [])

    def test_default_off(self):
        r = Rig(DMG_ONLY)
        self.assertFalse(r.ctl.enabled)
        for i in range(5):
            self.assertSuppressed(r.tick(i * 0.1), "disabled", r)

    def test_enable_needs_toggle_then_emits(self):
        r = Rig(DMG_ONLY)
        rec = r.enable()
        self.assertEqual((rec["event"], rec["action"], r.keys), ("decision", "s4", ["4"]))

    def test_hold_release_stops_within_one_tick(self):
        r = Rig(DMG_ONLY)
        r.enable(0.0)
        r.tick(0.3)
        self.assertEqual(len(r.keys), 2)
        r.owner.hold = False
        self.assertSuppressed_after(r, r.tick(0.6), "hold_released")
        r.owner.hold = True
        r.tick(0.9)
        self.assertEqual(len(r.keys), 3)

    def assertSuppressed_after(self, r, rec, reason):
        self.assertEqual(rec["reason"], reason)
        self.assertEqual(len(r.keys), 2)

    def test_toggle_mode_ignores_hold(self):
        r = Rig(DMG_ONLY, mode="toggle")
        r.owner.hold = False
        r.enable()
        self.assertEqual(r.keys, ["4"])

    def test_emergency_stop_disables_immediately(self):
        r = Rig(DMG_ONLY)
        r.enable(0.0)
        rec = r.tick(0.3, ctl=("emergency",))
        self.assertEqual(rec["reason"], "emergency_stop")
        self.assertFalse(r.ctl.enabled)
        self.assertEqual(len(r.keys), 1)
        self.assertEqual(r.tick(0.6)["reason"], "disabled")  # stays off until explicit toggle
        self.assertEqual(r.sink.down, set())

    def test_emergency_beats_simultaneous_toggle(self):
        r = Rig(DMG_ONLY)
        r.tick(0.0, ctl=("toggle", "emergency"))
        self.assertFalse(r.ctl.enabled)
        self.assertEqual(r.keys, [])

    def test_manual_input_pauses_then_resumes(self):
        r = Rig(DMG_ONLY, manual_pause_ms=1000, watchdog_ms=10 ** 6)
        r.enable(0.0)
        rec = r.tick(1.0, ctl=("manual",))
        self.assertEqual(rec["reason"], "manual_pause")
        self.assertEqual(r.tick(1.9)["reason"], "manual_pause")
        self.assertEqual(len(r.keys), 1)
        self.assertEqual(r.tick(2.0)["event"], "decision")
        self.assertEqual(len(r.keys), 2)

    def test_stale_frame(self):
        r = Rig(DMG_ONLY)
        self.assertSuppressed(r.enable(age=0.3), "stale_frame", r)
        self.assertEqual(r.tick(0.1, age=0.2)["event"], "decision")

    def test_future_frame(self):
        r = Rig(DMG_ONLY)
        self.assertSuppressed(r.enable(age=-0.5), "future_frame", r)

    def test_capture_error(self):
        r = Rig(DMG_ONLY)
        r.src.error = RuntimeError("pipewire gone")
        self.assertSuppressed(r.enable(frame=False), "capture_error", r)

    def test_focus_not_game_and_focus_error(self):
        r = Rig(DMG_ONLY)
        r.focus.title = "Firefox"
        self.assertSuppressed(r.enable(), "focus_not_game", r)
        r.focus.active_window = lambda: (_ for _ in ()).throw(RuntimeError("dbus"))
        self.assertSuppressed(r.tick(0.1), "focus_error", r)

    def test_profile_size_mismatch(self):
        r = Rig(DMG_ONLY)
        self.assertSuppressed(r.enable(size=(640, 360)), "profile_mismatch", r)

    def test_anchor_missing(self):
        r = Rig(DMG_ONLY)
        self.assertSuppressed(r.enable(anchor=False), "anchor_missing", r)

    def test_low_confidence_required_readings(self):
        for kw, name in ((dict(mp=0.3, mp_stray=True), "mp"), (dict(hp=0.3, hp_stray=True), "hp")):
            r = Rig(DMG_ONLY)
            self.assertSuppressed(r.enable(**kw), f"low_confidence:{name}", r)

    def test_dialog_loading_death(self):
        for st in ("dialog", "loading", "death"):
            r = Rig(DMG_ONLY)
            self.assertSuppressed(r.enable(state=st), f"unsafe_state:{st}", r)

    def test_invalid_resource_values(self):
        for bad in (math.nan, math.inf, -1.0, 2.0):
            r = Rig(DMG_ONLY)
            real = r.ctl.perceiver.read
            r.ctl.perceiver.read = lambda f, real=real, bad=bad: dataclasses.replace(real(f), hp=Reading(bad, 1.0, f.ts))
            self.assertSuppressed(r.enable(), "invalid_reading:hp", r)

    def test_watchdog_stall(self):
        r = Rig(DMG_ONLY, watchdog_ms=500)
        r.enable(0.0)
        r.tick(0.3)
        self.assertEqual(r.tick(0.9)["reason"], "watchdog_stall")
        self.assertEqual(len(r.keys), 2)
        self.assertEqual(r.tick(1.0)["event"], "decision")
        self.assertEqual(len(r.keys), 3)

    def test_desync_latches_until_reenabled(self):
        slot = {"s4": dict(key="4", role="dmg", cooldown=1.0)}
        r = Rig(slot, desync_n=2, response_ms=500, watchdog_ms=10 ** 6)
        # frames never show a cooldown after the press: the game is ignoring us
        times = [0.0, 0.6, 1.0, 1.6, 2.0, 2.6]
        recs = [r.enable(times[0])] + [r.tick(t) for t in times[1:]]
        self.assertEqual(r.keys, ["4", "4"])
        self.assertEqual(recs[3]["reason"], "desync_latched")
        self.assertEqual(recs[5]["reason"], "desync_latched")
        r.tick(3.0, ctl=("toggle",))  # off
        rec = r.tick(3.1, ctl=("toggle",))  # explicit re-enable clears the latch
        self.assertEqual(rec["event"], "decision")

    def test_responding_game_does_not_desync(self):
        slot = {"s4": dict(key="4", role="dmg", cooldown=1.0)}
        r = Rig(slot, desync_n=2, response_ms=500)
        r.enable(0.0)
        for i in range(1, 60):
            t = i * 0.1
            r.tick(t, cooldown=("s4",) if (t % 1.0) < 0.95 and t > 0.05 and round(t, 6) % 1.0 != 0 else ())
        self.assertFalse(r.ctl.desync)
        self.assertGreaterEqual(len(r.keys), 3)

    def test_enable_refused_without_focus_or_manual_signal(self):
        r = Rig(DMG_ONLY)
        r.focus.available = False
        self.assertEqual(r.enable()["reason"], "enable_refused:no_reliable_focus_signal")
        self.assertFalse(r.ctl.enabled)
        r2 = Rig(DMG_ONLY)
        r2.owner.provides_manual_input = False
        self.assertEqual(r2.enable()["reason"], "enable_refused:no_manual_input_signal")
        self.assertEqual((r.keys, r2.keys), ([], []))

    def test_profile_hash_pin_and_placeholder_keys_refused(self):
        args = (PROFILE, MockFrameSource(), MockFocus(), MockSink(), MockOwnerInput(), FakeClock())
        with self.assertRaises(ProfileMismatch):
            Controller(cfg(DMG_ONLY, profile_hash="0" * 64), *args)
        Controller(cfg(DMG_ONLY, profile_hash=PROFILE.hash), *args)
        from aion2_assist.config import ConfigError
        with self.assertRaises(ConfigError):
            Controller(cfg({"s4": dict(key="UNSET_4", role="dmg")}), *args)
        with self.assertRaises(ProfileMismatch):  # enabled slot without a calibrated region
            Controller(cfg({"zz": dict(key="z", role="dmg")}), *args)

    def test_every_tick_logs_structured_line(self):
        buf = io.StringIO()
        r = Rig(DMG_ONLY)
        r.ctl.log = json_logger(buf)
        r.tick(0.0)
        r.enable(0.1)
        r.tick(0.2, anchor=False)
        lines = [json.loads(x) for x in buf.getvalue().splitlines()]
        self.assertEqual([l["reason"] for l in lines], ["disabled", "damage", "anchor_missing"])
        self.assertTrue(all({"t", "event", "reason", "enabled"} <= set(l) for l in lines))


class FailClosedOnException(unittest.TestCase):
    def test_decider_exception_disables_and_releases(self):
        r = Rig(DMG_ONLY)
        r.enable(0.0)
        r.sink.down.add("held")  # a key somehow still down
        r.ctl.decider.decide = lambda *a: 1 / 0
        rec = r.tick(0.5)
        self.assertEqual(rec["reason"], "exception_fail_closed")
        self.assertFalse(r.ctl.enabled)
        self.assertEqual(r.sink.down, set())
        self.assertEqual(r.tick(0.8)["reason"], "disabled")
        self.assertEqual(len(r.keys), 1)

    def test_key_up_guaranteed_when_hold_fails(self):
        r = Rig(DMG_ONLY)
        r.sink.hold_error = RuntimeError("boom between down and up")
        rec = r.enable()
        self.assertEqual(rec["reason"], "exception_fail_closed")
        self.assertEqual(r.sink.raw, [("down", "4"), ("up", "4")])
        self.assertEqual(r.sink.down, set())
        self.assertFalse(r.ctl.enabled)

    def test_keyboard_interrupt_releases_and_propagates(self):
        r = Rig(DMG_ONLY)
        r.sink.hold_error = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            r.enable()
        self.assertEqual(r.sink.down, set())
        self.assertFalse(r.ctl.enabled)

    def test_toggle_off_and_shutdown_release_keys(self):
        r = Rig(DMG_ONLY)
        r.enable()
        r.sink.down.add("x")
        r.tick(0.1, ctl=("toggle",))
        self.assertEqual(r.sink.down, set())
        r.sink.down.add("y")
        r.ctl.shutdown()
        self.assertEqual(r.sink.down, set())


class RateCap(unittest.TestCase):
    def test_rate_cap_property_random_sequences(self):
        for seed, cap in ((1234, 4.0), (99, 2.5), (7, 10.0)):
            rng = random.Random(seed)
            slot = {"s4": dict(key="4", role="dmg", cooldown=0, gcd=0)}
            r = Rig(slot, max_keys_per_sec=cap, desync_n=10 ** 9, manual_pause_ms=300, watchdog_ms=10 ** 6)
            frames = [make_frame(0.0, hp=hp, target=tg) for hp in (1.0, 0.9, 0.5, 0.2) for tg in ("enemy", "none")]
            r.enable(0.0)
            t = 0.0
            for _ in range(600):
                t += rng.uniform(0.001, 0.2)
                f = rng.choice(frames)
                r.src.frame = dataclasses.replace(f, ts=t)
                r.owner.hold = rng.random() < 0.9
                ctl = [n for n, p in (("manual", 0.01), ("toggle", 0.002), ("emergency", 0.001)) if rng.random() < p]
                r.tick(t, frame=False, ctl=ctl)
                if rng.random() < 0.01:
                    r.owner.press("toggle")  # re-enable sometimes
            ts = [e[0] for e in r.sink.events]
            self.assertGreater(len(ts), 15, "property would be vacuous")
            spacing = 1.0 / cap
            self.assertTrue(all(b - a >= spacing - 1e-12 for a, b in zip(ts, ts[1:])), seed)
            k = max(1, math.ceil(cap))
            self.assertTrue(all(ts[i + k] - ts[i] >= 1.0 - 1e-9 for i in range(len(ts) - k)), seed)

    def test_rate_cap_logged(self):
        slot = {"s4": dict(key="4", role="dmg")}
        r = Rig(slot, max_keys_per_sec=2)
        r.enable(0.0)
        rec = r.tick(0.2)
        self.assertEqual((rec["reason"], len(r.keys)), ("rate_cap", 1))
        self.assertEqual(r.tick(0.5)["event"], "decision")


if __name__ == "__main__":
    unittest.main()
