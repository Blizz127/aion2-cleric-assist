"""Offline workflow calibration, validation, feedback demo, and screenshot replay."""
import dataclasses
import json
import math
import sys
from pathlib import Path

from .config import load_config
from .profile import ProfileMismatch, load_profile
from .workflow import Journal, WorkflowError, WorkflowRunner, calibrate_workflow, load_workflow


def main(args):
    try:
        if args.action == "demo":
            from .workflow_demo import run_demo
            sink, records, status = run_demo()
        else:
            profile = load_profile(args.profile)
            if args.action == "calibrate":
                with open(args.spec) as f:
                    recipe = calibrate_workflow(json.load(f), profile, Path(args.spec).resolve().parent)
                # Never overwrite a calibration/config; new files keep rollback straightforward.
                with open(args.out, "x") as f:
                    json.dump(recipe.data, f, sort_keys=True, indent=1)
                print(json.dumps({"workflow_hash": recipe.hash, "steps": len(recipe.steps), "file": args.out}))
                return 0
            recipe = load_workflow(args.recipe, profile)
            if args.action == "validate":
                print(json.dumps({"valid": True, "workflow_hash": recipe.hash, "steps": len(recipe.steps),
                                  "budget": recipe.budget, "live_verified": False}))
                return 0
            from .png import load_frame
            from .replay import frames_dir_steps, run_frames
            if not math.isfinite(args.dt) or not 0 < args.dt <= 60:
                raise WorkflowError("dt must be finite in (0, 60]")
            config = load_config(args.config)
            config = dataclasses.replace(config, watchdog_ms=max(config.watchdog_ms, args.dt * 2000))
            _, steps = frames_dir_steps(args.frames, load_frame)
            runner = WorkflowRunner(recipe, Journal(recipe))
            sink, records = run_frames(config, profile, steps, dt=args.dt, workflow=runner)
            status = runner.status()
        for record in records:
            print(json.dumps(record, sort_keys=True))
        print(json.dumps({"event": "workflow_summary", "evidence": "synthetic" if args.action == "demo" else "screenshots",
                          "adapters": "mock", "live_verified": False, "keys": sink.events, **status}, sort_keys=True))
        return 0 if status["complete"] else 4
    except (OSError, ValueError, KeyError, TypeError, ProfileMismatch) as e:
        print(f"workflow refused: {e}", file=sys.stderr)
        return 2


def add_parser(sub):
    parser = sub.add_parser("workflow", help="visual quest/navigation/combat/vendor workflows")
    modes = parser.add_subparsers(dest="action", required=True)
    demo = modes.add_parser("demo", help="synthetic seven-stage feedback world; mock inputs only")
    demo.set_defaults(fn=main)
    cal = modes.add_parser("calibrate", help="compile positive/negative full-frame screenshots into a recipe")
    cal.add_argument("--spec", required=True)
    cal.add_argument("--profile", required=True)
    cal.add_argument("--out", required=True)
    cal.set_defaults(fn=main)
    for action in ("validate", "replay"):
        p = modes.add_parser(action)
        p.add_argument("--recipe", required=True)
        p.add_argument("--profile", required=True)
        if action == "replay":
            p.add_argument("--config", required=True)
            p.add_argument("--frames", required=True)
            p.add_argument("--dt", type=float, default=0.1)
        p.set_defaults(fn=main)
