"""CLI (offline tools only). There is deliberately no `run` command: live use is not wired up."""
import argparse
import json
import sys

from .config import ConfigError, load_config
from .perception import Perceiver
from .png import load_frame
from .profile import CalibrationError, build_profile, load_profile, save_profile


def cmd_calibrate(a):
    with open(a.regions) as f:
        spec = json.load(f)
    shots = {}
    for item in a.shot:
        name, _, path = item.partition("=")
        shots[name] = load_frame(path)
    try:
        prof = build_profile(spec, shots)
    except CalibrationError as e:
        print(f"calibration refused: {e}", file=sys.stderr)
        return 2
    save_profile(prof, a.out)
    print(f"profile {a.out} hash={prof.hash} frame={prof.frame_size[0]}x{prof.frame_size[1]}")
    return 0


def cmd_read(a):
    """Print every reading for a screenshot (to verify calibration against a fresh owner shot)."""
    prof = load_profile(a.profile)
    obs = Perceiver(prof).read(load_frame(a.screenshot))
    print(json.dumps({k: (v._asdict() if hasattr(v, "_asdict") else {s: r._asdict() for s, r in v.items()})
                      for k, v in vars(obs).items() if k != "ts"}, indent=1, default=str))
    return 0


def cmd_check_config(a):
    try:
        cfg = load_config(a.config)
        cfg.validate_for_run()
    except (ConfigError, OSError) as e:
        print(f"config not runnable: {e}", file=sys.stderr)
        return 2
    print(f"config ok: {len(cfg.enabled_slots())} enabled slots, mode={cfg.mode}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="aion2_assist", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("calibrate", help="build a profile from full-frame owner screenshots")
    c.add_argument("--regions", required=True)
    c.add_argument("--shot", action="append", required=True, metavar="NAME=PNG",
                   help="normal, cooldown, no_target (required); friendly_target, dialog, loading, death (optional)")
    c.add_argument("--out", required=True)
    c.set_defaults(fn=cmd_calibrate)
    r = sub.add_parser("read", help="show readings for a screenshot")
    r.add_argument("--profile", required=True)
    r.add_argument("screenshot")
    r.set_defaults(fn=cmd_read)
    k = sub.add_parser("check-config", help="validate a config (rejects placeholder keys)")
    k.add_argument("--config", required=True)
    k.set_defaults(fn=cmd_check_config)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
