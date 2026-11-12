"""Safety controller: default OFF, fail closed, deterministic rate cap, structured logging."""
import json
import math
import sys

from .adapters import FocusProbe, FrameSource, InputSink, OwnerInput
from .config import Config
from .decision import Decider
from .perception import Perceiver, valid_fraction
from .profile import Profile, ProfileMismatch


def json_logger(stream=None):
    stream = stream or sys.stderr
    return lambda rec: stream.write(json.dumps(rec, sort_keys=True) + "\n")


class Controller:
    def __init__(self, cfg: Config, profile: Profile, capture: FrameSource, focus: FocusProbe,
                 sink: InputSink, owner: OwnerInput, clock, log=None, workflow=None):
        cfg.validate_for_run()
        if cfg.profile_hash and cfg.profile_hash != profile.hash:
            raise ProfileMismatch("config pins a different profile hash; refusing to run")
        unknown = [s.id for s in cfg.enabled_slots() if s.id not in profile["regions"]["slots"]]
        if unknown:
            raise ProfileMismatch(f"profile has no region for enabled slots {unknown}")
        self.cfg, self.profile, self.capture, self.focus = cfg, profile, capture, focus
        self.sink, self.owner, self.clock = sink, owner, clock
        self.log = log or (lambda rec: None)
        self.workflow = workflow
        self.perceiver, self.decider = Perceiver(profile), Decider(cfg)
        self.enabled = False  # default OFF
        self.pause_until = 0.0
        self.last_tick = None
        self.last_emit = None
        self.pending = {}     # slot -> (press_t, deadline)
        self.misses = 0
        self.desync = False

    def _before_emit(self, frame):
        """Capture/perception can block. Recheck cancellation and safety at dispatch time."""
        now = self.clock()
        if not self.enabled:
            return now, "disabled_before_dispatch"
        st = self.owner.poll()
        if st.emergency or st.toggle:
            self.shutdown()
            return now, "emergency_stop" if st.emergency else "disabled"
        lost = self.refusal()
        if lost:
            self.shutdown()
            return now, f"signal_lost:{lost}"
        if st.manual:
            self.pause_until = now + self.cfg.manual_pause_ms / 1000.0
        if now < self.pause_until:
            return now, "manual_pause"
        if self.cfg.mode == "hold" and not st.hold:
            return now, "hold_released"
        if not math.isfinite(now) or not math.isfinite(frame.ts):
            return now, "invalid_timestamp"
        age = (now - frame.ts) * 1000
        if age > self.cfg.max_stale_ms:
            return now, "stale_frame"
        if age < -self.cfg.future_tolerance_ms:
            return now, "future_frame"
        if self.cfg.window_match not in (self.focus.active_window() or ""):
            return now, "focus_not_game"
        # Focus queries may also block or deliver a watchdog/signal stop.
        st = self.owner.poll()
        now = self.clock()
        if st.emergency or st.toggle:
            self.shutdown()
            return now, "emergency_stop" if st.emergency else "disabled"
        if st.manual:
            self.pause_until = now + self.cfg.manual_pause_ms / 1000.0
        if now < self.pause_until:
            return now, "manual_pause"
        if self.cfg.mode == "hold" and not st.hold:
            return now, "hold_released"
        lost = self.refusal()
        if lost:
            self.shutdown()
            return now, f"signal_lost:{lost}"
        if not self.enabled:
            return now, "disabled_before_dispatch"
        if not math.isfinite(now) or (now - frame.ts) * 1000 > self.cfg.max_stale_ms:
            return now, "stale_frame"
        if self.last_emit is not None and now - self.last_emit < self.cfg.min_spacing_s:
            return now, "rate_cap"
        return now, None

    def refusal(self):
        """Why enabling must be refused (missing safety signal), or None."""
        if not getattr(self.focus, "available", False):
            return "no_reliable_focus_signal"
        if not getattr(self.owner, "provides_manual_input", False):
            return "no_manual_input_signal"
        return None

    def shutdown(self):
        self.enabled = False
        self.sink.release_all()

    def _out(self, now, event, reason, **extra):
        rec = {"t": round(now, 4), "event": event, "reason": reason, "enabled": self.enabled, **extra}
        self.log(rec)
        return rec

    def _sup(self, now, reason, **extra):
        return self._out(now, "suppress", reason, **extra)

    def tick(self) -> dict:
        """One loop iteration. Emits at most one key; returns the structured log record.
        Any exception => disable, release every key (try/finally), log, emit nothing more."""
        try:
            return self._tick()
        except BaseException as e:
            self.enabled = False
            try:
                self.sink.release_all()
            finally:
                rec = self._sup(self.last_tick or 0.0, "exception_fail_closed", error=type(e).__name__)
            if not isinstance(e, Exception):
                raise
            return rec

    def _tick(self) -> dict:
        cfg, now = self.cfg, self.clock()
        st = self.owner.poll()
        stalled = self.last_tick is not None and (now - self.last_tick) * 1000 > cfg.watchdog_ms
        self.last_tick = now
        if st.emergency:
            self.enabled = False
            self.sink.release_all()
            return self._sup(now, "emergency_stop")
        if st.toggle:
            why = self.refusal() if not self.enabled else None
            if why:
                return self._sup(now, f"enable_refused:{why}")
            self.enabled = not self.enabled
            if self.enabled:  # explicit re-enable clears the desync latch
                self.desync, self.misses = False, 0
                self.pending.clear()
            else:
                self.sink.release_all()
        if self.enabled:  # a required safety signal went away (e.g. monitor thread died): disable at once
            lost = self.refusal()
            if lost:
                self.enabled = False
                self.sink.release_all()
                return self._sup(now, f"signal_lost:{lost}")
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
        now = self.clock()
        if not math.isfinite(now) or not math.isfinite(frame.ts):
            return self._sup(now, "invalid_timestamp")
        age_ms = (now - frame.ts) * 1000
        if age_ms > cfg.max_stale_ms:
            return self._sup(now, "stale_frame", age_ms=round(age_ms, 1))
        if age_ms < -cfg.future_tolerance_ms:
            return self._sup(now, "future_frame", age_ms=round(age_ms, 1))
        try:
            obs = self.perceiver.read(frame)
        except ProfileMismatch as e:
            return self._sup(now, "profile_mismatch", detail=str(e))
        except Exception as e:
            return self._sup(now, "perception_error", error=type(e).__name__)
        now = self.clock()
        if not math.isfinite(now) or (now - frame.ts) * 1000 > cfg.max_stale_ms:
            return self._sup(now, "stale_frame")
        for name in ("hp", "mp"):
            if not valid_fraction(getattr(obs, name).value):
                return self._sup(now, f"invalid_reading:{name}")
        if not obs.anchor.value:  # (anchor confidence only describes a *present* anchor)
            return self._sup(now, "anchor_missing")
        for name in ("anchor", "state", "hp", "mp"):
            if not valid_fraction(getattr(obs, name).confidence) or getattr(obs, name).confidence < cfg.min_confidence:
                return self._sup(now, f"low_confidence:{name}")
        if obs.state.value is not None and not (self.workflow and self.workflow.allows_state(obs.state.value)):
            return self._sup(now, f"unsafe_state:{obs.state.value}")
        self._check_responses(obs)
        if self.desync:
            return self._sup(now, "desync_latched", misses=self.misses)
        d = self.decider.decide(obs, now)
        if self.workflow and (self.workflow.complete or self.workflow.blocked or not d.reason.startswith("emergency_heal:")):
            intent = self.workflow.decide(frame, obs, now)
            if intent.key:
                if obs.hp.value < cfg.safe_hp:
                    return self._sup(now, "workflow_unsafe_hp")
                if (not valid_fraction(obs.target.confidence) or obs.target.confidence < cfg.min_confidence
                        or obs.target.value not in ("none", "friendly")):
                    return self._sup(now, "workflow_target_unsafe")
                if now < self.decider.busy_until:
                    return self._sup(now, "casting")
                now, refusal = self._before_emit(frame)
                if refusal:
                    return self._sup(now, refusal)
                self.workflow.dispatch(now)  # durable intent precedes the possibly ambiguous key press
                now, refusal = self._before_emit(frame)  # journal fsync may have blocked
                if refusal:
                    self.workflow._block("dispatch_cancelled:" + refusal)
                    return self._sup(now, refusal)
                self.workflow.sent_at = now
                self.last_emit = now
                self.sink.press(intent.key, now)
                return self._out(now, "decision", intent.reason, action=self.workflow.step["id"], key=intent.key,
                                 workflow=self.workflow.status())
            if not intent.combat:
                return self._out(now, "decision", intent.reason, action=None, workflow=self.workflow.status())
        if d.slot is None:
            return self._out(now, "decision", d.reason, action=None)
        if d.slot in self.pending:
            return self._sup(now, "awaiting_cooldown_response", wanted=d.slot)
        if self.last_emit is not None and now - self.last_emit < cfg.min_spacing_s:
            return self._sup(now, "rate_cap", wanted=d.slot)
        now, refusal = self._before_emit(frame)
        if refusal:
            return self._sup(now, refusal)
        slot = cfg.slots[d.slot]
        self.last_emit = now  # counts even if press() raises: fail closed, never retry
        self.sink.press(slot.key, now)
        self.decider.record_press(d.slot, now)
        if slot.expect_cooldown:
            self.pending[d.slot] = (now, now + slot.cast + cfg.response_ms / 1000.0)
        return self._out(now, "decision", d.reason, action=d.slot, key=slot.key)

    def _check_responses(self, obs):
        """Each press on a cooldown slot must show 'cooldown' on screen soon; N misses => desync."""
        for sid, (t0, deadline) in list(self.pending.items()):
            r = obs.slots.get(sid)
            if obs.ts <= t0:
                continue
            if r is not None and valid_fraction(r.confidence) and r.confidence >= self.cfg.min_confidence and r.value == "cooldown":
                self.misses = 0
                del self.pending[sid]
            elif obs.ts >= deadline:
                self.misses += 1
                del self.pending[sid]
                if self.misses >= self.cfg.desync_n:
                    self.desync = True
