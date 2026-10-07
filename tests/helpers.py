from dataclasses import replace

from aion2_assist.adapters import MockFocus, MockFrameSource, MockOwnerInput, MockSink
from aion2_assist.config import Config
from aion2_assist.perception import Perceiver
from aion2_assist.replay import FakeClock
from aion2_assist.safety import Controller
from aion2_assist.synth import make_frame, synthetic_profile

PROFILE = synthetic_profile()
PERCEIVER = Perceiver(PROFILE)

SLOTS = {
    "s1": dict(key="1", role="heal", cooldown=5, gcd=1.0, mp_cost=100, priority=10),
    "s2": dict(key="2", role="hot", cooldown=6, gcd=1.0, mp_cost=50, priority=20, duration=12),
    "s3": dict(key="3", role="shield", cooldown=15, gcd=1.0, mp_cost=50, priority=30, duration=8),
    "s4": dict(key="4", role="dmg", cooldown=2, gcd=1.0, mp_cost=40, priority=40),
    "s5": dict(key="5", role="dmg", cooldown=2, gcd=1.0, mp_cost=40, priority=1, enabled=False),  # locked
}
DMG_ONLY = {"s4": dict(key="4", role="dmg", cooldown=0, gcd=0, mp_cost=0)}


def cfg(slots=None, **over):
    return Config.from_dict({"slots": slots if slots is not None else SLOTS, **over})


def obs(ts=0.0, **kw):
    return PERCEIVER.read(make_frame(ts, **kw))


class Rig:
    """Controller wired to mocks and a fake clock. Starts DISABLED, hold key held."""

    def __init__(self, slots=None, profile=PROFILE, **over):
        self.clock, self.sink, self.owner = FakeClock(), MockSink(), MockOwnerInput()
        self.src, self.focus, self.logs = MockFrameSource(), MockFocus(), []
        self.ctl = Controller(cfg(slots, **over), profile, self.src, self.focus, self.sink, self.owner,
                              self.clock, self.logs.append)
        self.owner.hold = True

    def tick(self, t, age=0.0, frame=True, ctl=(), **kw):
        self.clock.t = t
        for c in ctl:
            self.owner.press(c)
        if frame:
            self.src.frame = make_frame(t - age, **kw)
        return self.ctl.tick()

    def enable(self, t=0.0, **kw):
        return self.tick(t, ctl=("toggle",), **kw)

    @property
    def keys(self):
        return self.sink.keys
