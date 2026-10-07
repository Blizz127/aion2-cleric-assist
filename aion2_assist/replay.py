"""Replay simulator: scripted/recorded frames drive the real Controller with a fake clock and MOCK adapters
(MockSink only: no key is ever sent anywhere). Offline only."""
import dataclasses
import json
import os

from .adapters import MockFocus, MockFrameSource, MockOwnerInput, MockSink
from .safety import Controller
from .synth import make_frame

DEMO_DIR = os.path.join(os.path.dirname(__file__), "demo")


class FakeClock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def run_frames(cfg, profile, steps, dt=0.1, hold=True, log=None):
    """steps: iterable of (frame_factory(ts) -> Frame, ctl_names, hold_or_None, age_s).
    Returns (sink, records). The frame timestamp is the fake clock minus age (default fresh)."""
    clock, sink, owner, src = FakeClock(), MockSink(), MockOwnerInput(), MockFrameSource()
    ctl = Controller(cfg, profile, src, MockFocus(), sink, owner, clock, log)
    recs = []
    for i, (factory, ctl_names, h, age) in enumerate(steps):
        clock.t = round(i * dt, 6)
        for name in ctl_names:
            owner.press(name)
        owner.hold = hold if h is None else h
        src.frame = factory(round(clock.t - age, 6))
        recs.append(ctl.tick())
    return sink, recs


def replay(cfg, profile, timeline, dt=0.1, hold=True, log=None):
    """timeline: list of dicts of make_frame kwargs (+ optional 'ctl', 'hold', 'age', 'n' repeat count)."""
    steps = []
    for step in timeline:
        step = dict(step)
        n, ctl_names, h, age = step.pop("n", 1), step.pop("ctl", ()), step.pop("hold", None), step.pop("age", 0.0)
        for k in range(n):
            steps.append((lambda ts, kw=dict(step): make_frame(ts, **kw), ctl_names if k == 0 else (), h, age))
    return run_frames(cfg, profile, steps, dt, hold, log)


def load_timeline(path):
    with open(path) as f:
        doc = json.load(f)
    return doc["steps"], doc.get("dt", 0.1), doc.get("hold", True)


def frames_dir_steps(directory, load):
    """PNG files in name order; the first tick toggles the assist on (mock owner)."""
    names = sorted(n for n in os.listdir(directory) if n.lower().endswith(".png"))
    if not names:
        raise FileNotFoundError(f"no .png frames in {directory}")
    steps = []
    for i, n in enumerate(names):
        fr = load(os.path.join(directory, n))
        steps.append((lambda ts, fr=fr: dataclasses.replace(fr, ts=ts), ("toggle",) if i == 0 else (), None, 0.0))
    return names, steps
