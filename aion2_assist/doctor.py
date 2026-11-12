"""Read-only local readiness report. Never opens a bus, input device, or game."""
import dataclasses
import importlib.util
import json
import os
import shutil

from . import __version__
from .gates import evaluate_static


def main(args):
    gates, _, profile = evaluate_static(False, args.config, args.profile, args.gates_file)
    dependencies = {name: bool(shutil.which(name)) for name in ("gst-launch-1.0", "kpackagetool6")}
    dependencies["qdbus"] = bool(shutil.which("qdbus") or shutil.which("qdbus6"))
    dependencies["jeepney"] = importlib.util.find_spec("jeepney") is not None
    workflow = {"configured": False, "live_verified": False}
    if args.workflow:
        from .workflow import load_workflow
        try:
            if profile is None:
                raise ValueError("a valid profile is required")
            recipe = load_workflow(args.workflow, profile)
            workflow.update(configured=True, hash=recipe.hash, steps=len(recipe.steps), budget=recipe.budget)
        except (OSError, ValueError, KeyError, TypeError) as e:
            workflow["error"] = str(e)
    print(json.dumps({"version": __version__, "live_verified": False, "read_only": True,
                      "session_type": os.environ.get("XDG_SESSION_TYPE", "unknown"),
                      "dependencies": dependencies, "static_gates": [dataclasses.asdict(g) for g in gates],
                      "workflow": workflow,
                      "unverified": ["target capture latency", "KWin focus", "manual input exclusion",
                                     "portal consent", "actual game binds and action outcomes"]}, indent=2))
    return 0
