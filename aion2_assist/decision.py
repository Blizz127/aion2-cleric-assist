"""Deterministic decision logic. No clock, no randomness: all time comes in as `now`."""
from collections import deque
from dataclasses import dataclass
from typing import Optional

from .config import Config, SlotCfg, UPKEEP_ROLES
from .perception import Observation, valid_fraction


@dataclass(frozen=True)
class Decision:
    slot: Optional[str]  # None = NONE
    reason: str


NONE = None
SELF_ROLES = ("heal", "hot", "shield")


class Decider:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.ready_at = {}       # slot -> earliest time config says it is off cooldown
        self.active_until = {}   # slot -> end of hot/shield/buff effect (our own bookkeeping)
        self.gcd_until = 0.0
        self.busy_until = 0.0    # cast lock
        self.hist = deque(maxlen=cfg.hp_samples)  # (ts, hp)
        self._slope = 0.0

    # ---- bookkeeping -------------------------------------------------
    def record_press(self, slot_id: str, now: float):
        s = self.cfg.slots[slot_id]
        self.ready_at[slot_id] = now + s.cooldown
        self.busy_until = max(self.busy_until, now + s.cast)
        if s.gcd > 0:
            self.gcd_until = max(self.gcd_until, now + s.gcd)
        if s.role in UPKEEP_ROLES:
            self.active_until[slot_id] = now + s.cast + s.duration

    def _observe_hp(self, ts: float, hp: float):
        if self.hist and ts <= self.hist[-1][0]:
            return  # same/older frame: no new sample
        if self.hist:
            t0, h0 = self.hist[-1]
            inst = (hp - h0) / (ts - t0)
            a = self.cfg.ewma_alpha
            self._slope = inst if len(self.hist) == 1 else a * inst + (1 - a) * self._slope
        self.hist.append((ts, hp))

    def predicted_hp(self) -> float:
        """Current HP plus EWMA slope * horizon (only extrapolates downward trends)."""
        hp = self.hist[-1][1]
        return hp + min(0.0, self._slope) * self.cfg.predict_horizon_s

    # ---- availability ------------------------------------------------
    def _unavailable(self, s: SlotCfg, obs: Observation, now: float) -> Optional[str]:
        c = self.cfg
        if now < self.busy_until:
            return "casting"
        if s.gcd > 0 and now < self.gcd_until:
            return "gcd"
        if now < self.ready_at.get(s.id, 0.0):
            return "cooldown_tracked"
        if obs.mp.value * c.mp_max < s.mp_cost:
            return "mp"
        if s.role in SELF_ROLES:  # self-only heals: refuse when the target is unclear or an ally
            t = obs.target
            if t.confidence < c.min_confidence or t.value not in ("enemy", "none"):
                return "heal_target_unclear"
        r = obs.slots.get(s.id)
        if r is None or r.confidence < c.min_confidence:
            return "slot_uncertain"
        if r.value != "ready":
            return "observed_cooldown"  # cross-check: config says ready, screen says no
        return None

    def _first(self, role_set, obs, now, extra=None):
        for s in sorted((s for s in self.cfg.enabled_slots() if s.role in role_set), key=lambda s: (s.priority, s.id)):
            if extra and not extra(s):
                continue
            if self._unavailable(s, obs, now) is None:
                return s
        return None

    # ---- decide ------------------------------------------------------
    def decide(self, obs: Observation, now: float) -> Decision:
        c = self.cfg
        age_ms = (now - obs.ts) * 1000
        if age_ms > c.max_stale_ms:
            return Decision(NONE, "stale_observation")
        if age_ms < -c.future_tolerance_ms:
            return Decision(NONE, "future_timestamp")
        for name in ("hp", "mp"):
            if not valid_fraction(getattr(obs, name).value):
                return Decision(NONE, f"invalid_reading:{name}")
        if not obs.anchor.value:
            return Decision(NONE, "anchor_missing")
        for name in ("hp", "mp", "state", "anchor"):
            if getattr(obs, name).confidence < c.min_confidence:
                return Decision(NONE, f"low_confidence:{name}")
        if obs.state.value is not None:
            return Decision(NONE, f"unknown_state:{obs.state.value}")
        hp = obs.hp.value
        self._observe_hp(obs.ts, hp)
        pred = self.predicted_hp()

        low, early = hp < c.emergency_hp, pred < c.emergency_hp
        if low or early:
            s = self._first(("heal",), obs, now)
            if s:
                why = f"hp={hp:.2f}<{c.emergency_hp}" if low else f"predicted_hp={pred:.2f}<{c.emergency_hp}"
                return Decision(s.id, f"emergency_heal:{why}")
        s = self._first(("hot", "shield", "buff"), obs, now, lambda s: self._upkeep_due(s, hp, now))
        if s:
            return Decision(s.id, f"upkeep:{s.role}")
        if hp < c.safe_hp or pred < c.emergency_hp:
            return Decision(NONE, "unsafe_for_damage:hp")
        if not (obs.target.confidence >= c.min_confidence and obs.target.value == "enemy"):
            return Decision(NONE, "no_target")
        heal_costs = [x.mp_cost for x in c.enabled_slots() if x.role == "heal"]
        reserve = min(heal_costs) if heal_costs else 0.0
        mp_abs = obs.mp.value * c.mp_max
        s = self._first(("dmg",), obs, now, lambda s: mp_abs - s.mp_cost >= reserve)
        if s:
            return Decision(s.id, "damage")
        return Decision(NONE, "nothing_available")

    def _upkeep_due(self, s: SlotCfg, hp: float, now: float) -> bool:
        if now < self.active_until.get(s.id, 0.0):
            return False
        return hp < self.cfg.hot_hp if s.role == "hot" else True
