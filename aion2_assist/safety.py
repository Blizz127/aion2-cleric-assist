"""Safety controller: default OFF, fail closed, deterministic rate cap, structured logging."""
import json
import sys

from .adapters import FocusProbe, FrameSource, InputSink, OwnerInput
from .config import Config
from .decision import Decider
from .perception import Perceiver
from .profile import Profile, ProfileMismatch


def json_logger(stream=None):
    stream = stream or sys.stderr
    return lambda rec: stream.write(json.dumps(rec, sort_keys=True) + "\n")


class Controller:
    def __init__(self, cfg: Config, profile: Profile, capture: FrameSource, focus: FocusProbe,
                 sink: InputSink, owner: OwnerInput, clock, log=None):
        cfg.validate_for_run()
        if cfg.profile_hash and cfg.profile_hash != profile.hash:
            raise ProfileMismatch("config pins a different profile hash; refusing to run")
        unknown = [s.id for s in cfg.enabled_slots() if s.id not in profile["regions"]["slots"]]
        if unknown:
            raise ProfileMismatch(f"profile has no region for enabled slots {unknown}")
        self.cfg, self.profile, self.capture, self.focus = cfg, profile, capture, focus
        self.sink, self.owner, self.clock = sink, owner, clock
        self.log = log or (lambda rec: None)
        self.perceiver, self.decider = Perceiver(profile), Decider(cfg)
        self.enabled = False  # default OFF
        self.pause_until = 0.0
        self.last_tick = None
        self.last_emit = None
        self.pending = {}     # slot -> (press_t, deadline)
        self.misses = 0
        self.desync = False

    def _out(self, now, event, reason, **extra):
        rec = {"t": round(now, 4), "event": event, "reason": reason, "enabled": self.enabled, **extra}
        self.log(rec)
        return rec

    def _sup(self, now, reason, **extra):
        return self._out(now, "suppress", reason, **extra)

    def tick(self) -> dict:
        """One loop iteration. Emits at most one key; returns the structured log record."""
        cfg, now = self.cfg, self.clock()
        st = self.owner.poll()
        stalled = self.last_tick is not None and (now - self.last_tick) * 1000 > cfg.watchdog_ms
        self.last_tick = now
        if st.emergency:
            self.enabled = False
            return self._sup(now, "emergency_stop")
        if st.toggle:
            self.enabled = not self.enabled
            if self.enabled:  # explicit re-enable clears the desync latch
                self.desync, self.misses = False, 0
                self.pending.clear()
        if st.manual:
            self.pause_until = now + cfg.manual_pause_ms / 1000.0
        if not self.enabled:
            return self._sup(now, "disabled")
        if cfg.mode == "hold" and not st.hold:
            return self._sup(now, "hold_released")
        if stalled:
            return self._sup(now, "watchdog_stall")
        if now < self.pause_until:
            return self._sup(now, "manual_pause")
        if self.desync:
            return self._sup(now, "desync_latched")
        try:
            win = self.focus.active_window()
        except Exception as e:
            return self._sup(now, "focus_error", error=type(e).__name__)
        if cfg.window_match not in (win or ""):
            return self._sup(now, "focus_not_game", window=win)
        try:
            frame = self.capture.grab()
        except Exception as e:
            return self._sup(now, "capture_error", error=type(e).__name__)
        if (now - frame.ts) * 1000 > cfg.max_stale_ms:
            return self._sup(now, "stale_frame", age_ms=round((now - frame.ts) * 1000, 1))
        try:
            obs = self.perceiver.read(frame)
        except ProfileMismatch as e:
            return self._sup(now, "profile_mismatch", detail=str(e))
        except Exception as e:
            return self._sup(now, "perception_error", error=type(e).__name__)
        for name in ("state", "anchor", "hp", "mp"):
            if getattr(obs, name).confidence < cfg.min_confidence:
                return self._sup(now, f"low_confidence:{name}")
        if obs.state.value is not None:
            return self._sup(now, f"unsafe_state:{obs.state.value}")
        if not obs.anchor.value:
            return self._sup(now, "anchor_missing")
        self._check_responses(obs)
        if self.desync:
            return self._sup(now, "desync_latched", misses=self.misses)
        d = self.decider.decide(obs, now)
        if d.slot is None:
            return self._out(now, "decision", d.reason, action=None)
        if self.last_emit is not None and now - self.last_emit < cfg.min_spacing_s:
            return self._sup(now, "rate_cap", wanted=d.slot)
        slot = cfg.slots[d.slot]
        self.sink.press(slot.key, now)
        self.last_emit = now
        self.decider.record_press(d.slot, now)
        if slot.expect_cooldown:
            self.pending[d.slot] = (now, now + slot.cast + cfg.response_ms / 1000.0)
        return self._out(now, "decision", d.reason, action=d.slot, key=slot.key)

    def _check_responses(self, obs):
        """Each press on a cooldown slot must show 'cooldown' on screen soon; N misses => desync."""
        for sid, (t0, deadline) in list(self.pending.items()):
            r = obs.slots.get(sid)
            if obs.ts <= t0 or r is None or r.confidence < self.cfg.min_confidence:
                continue
            if r.value == "cooldown":
                self.misses = 0
                del self.pending[sid]
            elif obs.ts >= deadline:
                self.misses += 1
                del self.pending[sid]
                if self.misses >= self.cfg.desync_n:
                    self.desync = True
