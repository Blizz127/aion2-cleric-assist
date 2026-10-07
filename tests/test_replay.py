import unittest

from aion2_assist.replay import replay
from tests.helpers import PROFILE, cfg


def hp_at(i):  # tick index -> HP fraction (dt = 0.1 s)
    if i < 20:
        return 1.0
    if i == 20:
        return 0.30       # sudden hit at t=2.0
    return 0.5 if i <= 34 else 0.95  # healed to 0.5, HoT brings it up from t=3.5


def cooldown_at(i):  # what the game would show: slot greyed from press+0.1 for its cooldown
    cd = set()
    if i >= 1:
        cd.add("s3")                                   # shield pressed t=0.0, 15 s
    if 11 <= i <= 30 or 41 <= i <= 60 or 62 <= i <= 81:
        cd.add("s4")                                   # dmg pressed t=1.0, 4.0, 6.1; 2 s each
    if 21 <= i <= 70:
        cd.add("s1")                                   # heal pressed t=2.0, 5 s
    if i >= 31:
        cd.add("s2")                                   # hot pressed t=3.0, 6 s (display held to end)
    return tuple(sorted(cd))


class ReplayFight(unittest.TestCase):
    def test_scripted_fight_exact_key_sequence(self):
        timeline = [dict(hp=hp_at(i), mp=0.8, target="enemy", cooldown=cooldown_at(i)) for i in range(75)]
        timeline[0]["ctl"] = ("toggle",)
        sink, recs = replay(cfg(), PROFILE, timeline, dt=0.1)
        self.assertEqual(sink.events, [
            (0.0, "3"),   # shield upkeep at full HP (nothing else due; GCD blocks the rest)
            (1.0, "4"),   # GCD over, safe, enemy targeted -> damage
            (2.0, "1"),   # HP 0.30 < 0.35 the instant the GCD ends -> emergency heal
            (3.0, "2"),   # GCD over, HP 0.5 < 0.9 -> HoT upkeep
            (4.0, "4"),   # HP 0.95 safe -> damage
            (6.1, "4"),   # tracked cooldown ends 6.0 but screen still greyed -> wait one tick (cross-check)
        ])
        by_t = {r["t"]: r for r in recs}
        self.assertTrue(by_t[2.0]["reason"].startswith("emergency_heal"))
        self.assertEqual(by_t[6.0]["reason"], "nothing_available")
        self.assertEqual(by_t[5.0]["reason"], "nothing_available")
        self.assertEqual(sink.down, set())

    def test_same_timeline_is_deterministic(self):
        timeline = [dict(hp=hp_at(i), cooldown=cooldown_at(i)) for i in range(75)]
        timeline[0]["ctl"] = ("toggle",)
        a, _ = replay(cfg(), PROFILE, timeline)
        b, _ = replay(cfg(), PROFILE, timeline)
        self.assertEqual(a.events, b.events)

    def test_dialog_midfight_stops_output_then_resumes(self):
        timeline = [dict(hp=1.0, target="enemy") for i in range(30)]
        timeline[0]["ctl"] = ("toggle",)
        for i in range(10, 20):
            timeline[i]["state"] = "dialog"
        sink, recs = replay(cfg({"s4": dict(key="4", role="dmg", cooldown=0, gcd=0)}, desync_n=10 ** 6), PROFILE, timeline)
        self.assertTrue(all(r["reason"] == "unsafe_state:dialog" for r in recs[10:20]))
        self.assertFalse(any(1.0 <= t < 2.0 for t, _ in sink.events))
        self.assertTrue(any(t >= 2.0 for t, _ in sink.events))


if __name__ == "__main__":
    unittest.main()
