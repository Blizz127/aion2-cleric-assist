import dataclasses
import math
import unittest

from aion2_assist.decision import Decider
from aion2_assist.frame import Reading
from tests.helpers import DMG_ONLY, cfg, obs


def decide(d, t, now=None, **kw):
    return d.decide(obs(t, **kw), t if now is None else now)


class Emergency(unittest.TestCase):
    def test_heal_below_threshold(self):
        r = decide(Decider(cfg()), 0.0, hp=0.30)
        self.assertEqual((r.slot, r.reason.split(":")[0]), ("s1", "emergency_heal"))

    def test_no_heal_at_threshold_with_steady_hp(self):
        d = Decider(cfg())
        for i in range(5):
            r = decide(d, i * 0.1, hp=0.5)
        self.assertNotEqual(r.slot, "s1")

    def test_prediction_triggers_early_heal(self):
        d = Decider(cfg())
        decide(d, 0.0, hp=1.0)
        r = decide(d, 0.1, hp=0.8)  # hp well above 0.35 but falling 2.0/s: predicted < X
        self.assertEqual(r.slot, "s1")
        self.assertIn("predicted_hp", r.reason)

    def test_rising_hp_never_predicts(self):
        d = Decider(cfg())
        decide(d, 0.0, hp=0.4)
        self.assertNotIn("emergency", decide(d, 0.1, hp=0.6).reason)

    def test_heal_priority_and_mp_gating(self):
        slots = {"s1": dict(key="a", role="heal", mp_cost=900, priority=1), "s2": dict(key="b", role="heal", mp_cost=10, priority=2)}
        d = Decider(cfg(slots))
        # mp 0.5*1605=802 < 900: falls back to the cheaper heal
        self.assertEqual(decide(d, 0.0, hp=0.2, mp=0.5).slot, "s2")
        self.assertEqual(decide(Decider(cfg(slots)), 0.0, hp=0.2, mp=1.0).slot, "s1")
        self.assertIsNone(decide(Decider(cfg(slots)), 0.0, hp=0.2, mp=0.001).slot)

    def test_dmg_keeps_mp_reserve_for_heal(self):
        slots = {"s1": dict(key="1", role="heal", mp_cost=100), "s4": dict(key="4", role="dmg", mp_cost=40)}
        # mp bar has 1% steps (16 MP): 8% = 128 MP leaves 88 < heal cost 100; 9% = 144 leaves 104
        self.assertIsNone(decide(Decider(cfg(slots)), 0.0, mp=0.08).slot)
        self.assertEqual(decide(Decider(cfg(slots)), 0.0, mp=0.09).slot, "s4")


class Upkeep(unittest.TestCase):
    def test_order_heal_then_hot_then_shield_then_damage(self):
        d = Decider(cfg(emergency_hp=0.3))
        seq = []
        for i in range(4):
            t = i * 2.0  # far apart: gcd never limits
            r = decide(d, t, hp=0.8)
            if r.slot:
                d.record_press(r.slot, t)
            seq.append(r.slot)
        self.assertEqual(seq[:2], ["s2", "s3"])  # hot (hp<0.9) then shield; both on cooldown after

    def test_upkeep_not_repeated_while_active(self):
        d = Decider(cfg())
        r = decide(d, 0.0, hp=0.8)
        d.record_press(r.slot, 0.0)
        self.assertNotEqual(decide(d, 5.0, hp=0.8).slot, r.slot)


class DamageSafety(unittest.TestCase):
    def check_none(self, why, **kw):
        r = decide(Decider(cfg(DMG_ONLY)), 0.0, **kw)
        self.assertIsNone(r.slot, why)
        return r

    def test_damage_when_safe(self):
        self.assertEqual(decide(Decider(cfg(DMG_ONLY)), 0.0).slot, "s4")

    def test_no_damage_when_unsafe(self):
        self.check_none("hp below Y", hp=0.5)
        self.check_none("no target", target="none")
        self.check_none("friendly target", target="friendly")
        self.check_none("dialog", state="dialog")
        self.check_none("loading", state="loading")
        self.check_none("death", state="death")
        self.check_none("anchor missing", anchor=False)
        self.check_none("uncertain mp", mp=0.3, mp_stray=True)
        self.check_none("uncertain hp", hp=0.3, hp_stray=True)

    def test_stale_observation(self):
        d = Decider(cfg(DMG_ONLY))
        self.assertIsNone(d.decide(obs(0.0), 0.3).slot)
        self.assertEqual(d.decide(obs(0.0), 0.2).slot, "s4")

    def test_future_timestamp(self):
        d = Decider(cfg(DMG_ONLY))
        r = d.decide(obs(5.0), 1.0)
        self.assertEqual((r.slot, r.reason), (None, "future_timestamp"))

    def test_nan_inf_out_of_range_resources(self):
        for bad in (math.nan, math.inf, -math.inf, -0.1, 1.5, None, "0.5"):
            for field in ("hp", "mp"):
                o = obs(0.0)
                o = dataclasses.replace(o, **{field: Reading(bad, 1.0, 0.0)})
                r = Decider(cfg()).decide(o, 0.0)
                self.assertEqual((r.slot, r.reason), (None, f"invalid_reading:{field}"), (bad, field))

    def test_target_not_a_dmg_gate_for_self_heals(self):
        self.assertEqual(decide(Decider(cfg()), 0.0, hp=0.2, target="none").slot, "s1")


class HealTargetAndSlots(unittest.TestCase):
    def test_refuse_self_heal_when_ally_targeted(self):
        r = decide(Decider(cfg()), 0.0, hp=0.2, target="friendly")
        self.assertIsNone(r.slot)

    def test_refuse_when_target_unclear(self):
        o = obs(0.0, hp=0.2)
        o = dataclasses.replace(o, target=Reading("none", 0.3, 0.0))
        self.assertIsNone(Decider(cfg()).decide(o, 0.0).slot)

    def test_locked_slot_never_used_even_with_best_priority(self):
        for t in range(6):
            r = decide(Decider(cfg()), t, hp=1.0, cooldown=())
            self.assertNotEqual(r.slot, "s5")

    def test_unlearned_slot_observed_uncertain_is_skipped(self):
        o = obs(0.0, hp=0.2)
        o.slots["s1"] = Reading("ready", 0.2, 0.0)
        self.assertIsNone(Decider(cfg({"s1": dict(key="1", role="heal")})).decide(o, 0.0).slot)

    def test_observed_cooldown_overrides_config_ready(self):
        r = decide(Decider(cfg()), 0.0, hp=0.2, cooldown=("s1",))
        self.assertNotEqual(r.slot, "s1")

    def test_tracked_cooldown_overrides_observed_ready(self):
        d = Decider(cfg({"s1": dict(key="1", role="heal", cooldown=5)}))
        d.record_press("s1", 0.0)  # screen lags and still shows ready
        self.assertIsNone(decide(d, 1.0, hp=0.2).slot)
        self.assertEqual(decide(d, 5.0, hp=0.2).slot, "s1")

    def test_stale_ready_cooldown_transitions(self):
        d = Decider(cfg({"s1": dict(key="1", role="heal", cooldown=2)}))
        self.assertEqual(decide(d, 0.0, hp=0.2).slot, "s1")
        d.record_press("s1", 0.0)
        # frame from BEFORE the press (shows ready) arriving late is rejected as stale
        self.assertEqual(d.decide(obs(0.0, hp=0.2), 0.4).reason, "stale_observation")
        # fresh frame still showing cooldown after the tracked cooldown ended: wait, do not press
        self.assertEqual(d.decide(obs(2.1, hp=0.2, cooldown=("s1",)), 2.1).slot, None)
        # screen flips back to ready: press again
        self.assertEqual(d.decide(obs(2.2, hp=0.2), 2.2).slot, "s1")


if __name__ == "__main__":
    unittest.main()
