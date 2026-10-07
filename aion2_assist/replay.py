"""Replay simulator: a scripted timeline of synthetic frames drives the real Controller with a
fake clock and mock adapters. Offline only."""
from .adapters import MockFocus, MockFrameSource, MockOwnerInput, MockSink
from .safety import Controller
from .synth import make_frame


class FakeClock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def replay(cfg, profile, timeline, dt=0.1, hold=True, log=None):
    """timeline: list of dicts of make_frame kwargs (hp, mp, target, cooldown, state...) plus optional
    'ctl' keys ('toggle'|'emergency'|'manual'). One entry per tick of `dt` seconds.
    Returns (sink, records)."""
    clock, sink, owner = FakeClock(), MockSink(), MockOwnerInput()
    src = MockFrameSource()
    ctl = Controller(cfg, profile, src, MockFocus(), sink, owner, clock, log)
    owner.hold = hold
    recs = []
    for i, step in enumerate(timeline):
        step = dict(step)
        clock.t = round(i * dt, 6)
        for name in step.pop("ctl", ()):
            owner.press(name)
        owner.hold = step.pop("hold", hold)
        src.frame = make_frame(clock.t - step.pop("age", 0.0), **step)
        recs.append(ctl.tick())
    return sink, recs
