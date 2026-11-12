"""Synthetic feedback world. This is NOT an Aion route, item list, or binding set."""
from dataclasses import replace

from .adapters import MockFocus, MockFrameSource, MockOwnerInput, MockSink
from .config import Config
from .frame import Frame
from .profile import compute_hash
from .replay import FakeClock
from .safety import Controller
from .synth import make_frame, synthetic_profile
from .workflow import Journal, Workflow, WorkflowRunner

RECT = [220, 110, 24, 16]


def marker_frame(ts, marker, state=None, target="none", hp=1.0):
    frame = make_frame(ts, state=state, target=target, hp=hp)
    rows = [bytearray(r) for r in frame.rows]
    x, y, w, h = RECT
    for yy in range(h):
        for xx in range(w):
            # Binary patterns are far apart even for neighboring marker ids.
            color = (240, 240, 240) if marker & (1 << (xx % 5)) else (5, 5, 5)
            rows[y + yy][3 * (x + xx):3 * (x + xx + 1)] = bytes(color)
    return Frame(frame.width, frame.height, tuple(map(bytes, rows)), ts)


def demo_recipe():
    profile = synthetic_profile()
    steps, checkpoints = [], {}
    for i, (name, kind, key, state) in enumerate((
        ("accept_quest", "quest", "q", "dialog"), ("reach_encounter", "navigate", "w", None),
        ("win_encounter", "combat", None, None), ("collect_loot", "loot", "f", None),
        ("turn_in", "quest", "q", "dialog"), ("restock", "buy", "b", "dialog"),
        ("sell_junk", "sell", "s", "dialog"),
    )):
        step = dict(id=name, kind=kind, before=f"{name}_before", after=f"{name}_after", timeout_s=10)
        if key:
            step["key"] = key
        if kind in {"buy", "sell"}:
            step["trade"] = dict(item="synthetic_potion" if kind == "buy" else "synthetic_junk",
                                 quantity=1, total=5 if kind == "buy" else 2, currency="in_game", protected=False)
        for j, side in enumerate(("before", "after")):
            marker = i * 2 + j + 1
            checkpoints[step[side]] = dict(rect=RECT, state=state, threshold=1 if kind in {"buy", "sell"} else 0.99, absent_score=0,
                                          template=list(marker_frame(0, marker).flat(tuple(RECT))))
        steps.append(step)
    data = dict(version=1, profile_hash=profile.hash, checkpoints=checkpoints, steps=steps, budget=5,
                description="SYNTHETIC ONLY: not real game calibration, binds, routes, or prices")
    data["hash"] = compute_hash(data)
    return profile, Workflow(data, profile)


def run_demo():
    profile, recipe = demo_recipe()
    runner = WorkflowRunner(recipe, Journal(recipe))
    cfg = Config.from_dict(dict(slots={
        "s1": dict(key="1", role="heal", cooldown=2, gcd=0.5),
        "s4": dict(key="4", role="dmg", cooldown=1, gcd=0.5),
    }))
    clock, source, sink, owner = FakeClock(), MockFrameSource(), MockSink(), MockOwnerInput()
    owner.hold = True
    owner.press("toggle")
    ctl = Controller(cfg, profile, source, MockFocus(), sink, owner, clock, workflow=runner)
    records, seen_stage, event_start, healed, succeeded = [], -1, 0, False, False
    for tick in range(200):
        if runner.complete or runner.blocked:
            break
        if seen_stage != runner.index:
            seen_stage, event_start, healed, succeeded = runner.index, len(sink.events), False, False
        s = runner.step
        emitted = sink.events[event_start:]
        if s["kind"] == "combat":
            healed = any(k == "1" for _, k in emitted)
            succeeded = any(k == "4" for _, k in emitted)
        else:
            succeeded = any(k == s.get("key") for _, k in emitted)
        clock.t = round(tick * 0.3, 6)
        marker = runner.index * 2 + (2 if succeeded else 1)
        state = recipe.checkpoints[s["after"] if succeeded else s["before"]]["state"]
        source.frame = marker_frame(clock.t, marker, state=state,
                                    target="enemy" if s["kind"] == "combat" and not succeeded else "none",
                                    hp=0.3 if s["kind"] == "combat" and not healed else 1.0)
        # Observed cooldown acknowledgements come from the simulated screen.
        if emitted and s["kind"] == "combat":
            from .synth import REGIONS
            cd = make_frame(clock.t, cooldown=tuple(cfg.slots))
            rows = list(source.frame.rows)
            for rect in (REGIONS["regions"]["slots"][sid] for sid in cfg.slots):
                x, y, w, h = rect
                for yy in range(y, y + h):
                    row = bytearray(rows[yy])
                    row[x*3:(x+w)*3] = cd.rows[yy][x*3:(x+w)*3]
                    rows[yy] = bytes(row)
            # Heal cooldown must not suppress the first damage action.
            if not succeeded:
                x, y, w, h = REGIONS["regions"]["slots"]["s4"]
                ready = make_frame(clock.t)
                for yy in range(y, y+h):
                    row = bytearray(rows[yy])
                    row[x*3:(x+w)*3] = ready.rows[yy][x*3:(x+w)*3]
                    rows[yy] = bytes(row)
            source.frame = replace(source.frame, rows=tuple(rows))
        records.append(ctl.tick())
    ctl.shutdown()
    return sink, records, runner.status()
