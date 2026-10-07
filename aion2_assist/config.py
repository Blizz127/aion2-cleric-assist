"""User-editable config (JSON, or TOML by extension). No skills/binds are invented here."""
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

ROLES = ("heal", "hot", "shield", "dmg", "buff")
UPKEEP_ROLES = ("hot", "shield", "buff")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class SlotCfg:
    id: str
    key: str
    role: str
    cooldown: float = 0.0   # seconds
    cast: float = 0.0       # seconds (blocks all other presses)
    gcd: float = 0.0        # seconds; >0 means the slot triggers/obeys the global cooldown
    mp_cost: float = 0.0
    priority: int = 100     # lower number is tried first
    duration: float = 0.0   # hot/shield/buff effect length used for upkeep timing
    enabled: bool = True    # set false for level-locked slots

    @property
    def expect_cooldown(self) -> bool:
        return self.cooldown > 0.0


@dataclass(frozen=True)
class Config:
    window_match: str = "AION2"
    hp_max: float = 318.0
    mp_max: float = 1605.0
    slots: Dict[str, SlotCfg] = field(default_factory=dict)
    # decision
    emergency_hp: float = 0.35      # X (fraction)
    safe_hp: float = 0.60           # Y (fraction)
    hot_hp: float = 0.90            # cast HoT only below this
    predict_horizon_s: float = 1.0  # H
    ewma_alpha: float = 0.5
    hp_samples: int = 6
    min_confidence: float = 0.7
    # safety
    mode: str = "hold"              # "hold" (default) or "toggle"
    max_stale_ms: float = 250.0
    manual_pause_ms: float = 1500.0
    max_keys_per_sec: float = 4.0
    watchdog_ms: float = 500.0
    desync_n: int = 3
    response_ms: float = 1500.0     # extra wait (after cast) for the slot to show cooldown
    profile_hash: Optional[str] = None  # pin: refuse to run with any other profile

    @staticmethod
    def from_dict(d: dict) -> "Config":
        d = dict(d)
        raw = d.pop("slots", {})
        slots = {}
        for sid, s in raw.items():
            s = dict(s)
            try:
                slot = SlotCfg(id=sid, **s)
            except TypeError as e:
                raise ConfigError(f"slot {sid}: {e}")
            if slot.role not in ROLES:
                raise ConfigError(f"slot {sid}: role must be one of {ROLES}")
            if min(slot.cooldown, slot.cast, slot.gcd, slot.mp_cost, slot.duration) < 0:
                raise ConfigError(f"slot {sid}: negative timing/cost")
            slots[sid] = slot
        try:
            cfg = Config(slots=slots, **d)
        except TypeError as e:
            raise ConfigError(str(e))
        cfg.validate()
        return cfg

    def validate(self):
        if self.mode not in ("hold", "toggle"):
            raise ConfigError("mode must be 'hold' or 'toggle'")
        if not 0 < self.emergency_hp < self.safe_hp <= 1:
            raise ConfigError("need 0 < emergency_hp < safe_hp <= 1")
        if self.max_keys_per_sec <= 0 or self.max_stale_ms <= 0 or self.desync_n < 1:
            raise ConfigError("max_keys_per_sec/max_stale_ms must be > 0, desync_n >= 1")
        if not 0 < self.min_confidence <= 1:
            raise ConfigError("min_confidence in (0,1]")
        keys = [s.key for s in self.enabled_slots()]
        if len(keys) != len(set(keys)):
            raise ConfigError("duplicate keys among enabled slots")

    def enabled_slots(self) -> List[SlotCfg]:
        return [s for s in self.slots.values() if s.enabled]

    def validate_for_run(self):
        """Extra guard: refuse placeholder binds (the real bar/binds are not known yet)."""
        bad = [s.id for s in self.enabled_slots() if not s.key or s.key.upper().startswith("UNSET")]
        if bad:
            raise ConfigError(f"placeholder/empty keys for enabled slots: {bad}")
        if not self.enabled_slots():
            raise ConfigError("no enabled slots")

    @property
    def min_spacing_s(self) -> float:
        return 1.0 / self.max_keys_per_sec


def load_config(path: str) -> Config:
    if path.endswith(".toml"):
        import tomllib
        with open(path, "rb") as f:
            return Config.from_dict(tomllib.load(f))
    with open(path) as f:
        return Config.from_dict(json.load(f))
