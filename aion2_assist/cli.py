"""Offline calibration/replay and explicitly gated live combat/workflow execution."""
import argparse
import dataclasses
import json
import os
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


def _print_run(sink, recs, as_json):
    for r in recs:
        if as_json:
            print(json.dumps(r, sort_keys=True))
            continue
        extra = f" action={r['action']} key={r['key']}" if r.get("action") else ""
        extra += f" {r['detail']}" if r.get("detail") else ""
        print(f"t={r['t']:<5} {r['event']:<8} reason={r['reason']}{extra}")
    print("--- summary (MOCK adapters: nothing was sent to any real input device)")
    from collections import Counter
    sup = Counter(r["reason"] for r in recs if r["event"] == "suppress")
    print(f"ticks={len(recs)} keys_emitted={len(sink.events)} suppressions={dict(sorted(sup.items()))}")
    print("key sequence:", [(t, k) for t, k in sink.events])
    return 0


def _demo_inputs():
    from .replay import DEMO_DIR
    from .synth import synthetic_profile
    return load_config(os.path.join(DEMO_DIR, "config.json")), synthetic_profile(), DEMO_DIR


def cmd_replay(a):
    from .replay import load_timeline, replay, run_frames, frames_dir_steps
    if a.demo:
        cfg, prof, d = _demo_inputs()
        src = os.path.join(d, "timeline.json")
        print("replay: bundled synthetic demo (synthetic profile + demo config; no owner inputs)")
    else:
        if not (a.source and a.config and a.profile):
            print("replay needs <timeline.json|frames dir> --config --profile (or --demo)", file=sys.stderr)
            return 2
        cfg, prof, src = load_config(a.config), load_profile(a.profile), a.source
    if os.path.isdir(src):
        _, steps = frames_dir_steps(src, load_frame)
        cfg = dataclasses.replace(cfg, watchdog_ms=max(cfg.watchdog_ms, a.dt * 2000))
        sink, recs = run_frames(cfg, prof, steps, a.dt)
    else:
        steps, dt, hold = load_timeline(src)
        sink, recs = replay(cfg, prof, steps, dt, hold)
    return _print_run(sink, recs, a.json)


def cmd_dry_run(a):
    from .replay import frames_dir_steps, run_frames
    import tempfile
    from .png import write_png
    from .synth import make_frame
    if a.demo:
        cfg, prof, _ = _demo_inputs()
        tmp = tempfile.TemporaryDirectory()
        for i, kw in enumerate((dict(), dict(hp=0.3), dict(state="dialog"), dict(anchor=False), dict(target="none"))):
            write_png(os.path.join(tmp.name, f"frame{i:02d}.png"), make_frame(0.0, **kw))
        directory = tmp.name
        print("dry-run: demo screenshots (synthetic PNGs: normal, hp 0.30, dialog, HUD anchor missing, no target)")
    else:
        if not (a.frames and a.config and a.profile):
            print("dry-run needs --frames DIR --config --profile (or --demo)", file=sys.stderr)
            return 2
        cfg, prof, directory = load_config(a.config), load_profile(a.profile), a.frames
    names, steps = frames_dir_steps(directory, load_frame)
    dt = 1.1 if a.demo else a.dt  # demo: spaced past the 1.0 s GCD so each frame can act
    cfg = dataclasses.replace(cfg, watchdog_ms=max(cfg.watchdog_ms, dt * 2000))  # fake clock: no real stalls
    sink, recs = run_frames(cfg, prof, steps, dt)
    print(f"fake clock dt={dt}s per frame; frames toggle the (mock) owner on at the first frame, hold key held")
    for n, r in zip(names, recs):
        print(f"[{n}]", end=" ")
        print(f"t={r['t']:<5} {r['event']:<8} reason={r['reason']}"
              + (f" action={r['action']} key={r['key']}" if r.get("action") else ""))
    print("--- summary (MOCK adapters: nothing was sent to any real input device)")
    print(f"frames={len(recs)} keys_emitted={len(sink.events)} key sequence: {[(t, k) for t, k in sink.events]}")
    return 0


def cmd_run(a):
    from .orchestrator import live_main
    return live_main(a)


def cmd_approve(a):
    """Owner action: write the profile hash into the gates file (mode 0600). Never run by the assist itself."""
    from .gates import DEFAULT_GATES_FILE
    if not a.i_accept_live_risk:
        print("approve refused: pass --i-accept-live-risk", file=sys.stderr)
        return 2
    prof = load_profile(a.profile)
    hashes = [prof.hash]
    if getattr(a, "workflow", None):
        from .workflow import load_workflow
        hashes.append(load_workflow(a.workflow, prof).hash)
    path = os.path.expanduser(a.gates_file)
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    import shutil
    import stat
    import tempfile
    import uuid
    if os.path.lexists(path):
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o022:
            print("approve refused: existing approval must be a private owner-controlled regular file", file=sys.stderr)
            return 2
        backup = path + ".bak-" + uuid.uuid4().hex
        shutil.copy2(path, backup)
        print(f"previous approval backed up to {backup}")
    fd, temporary = tempfile.mkstemp(prefix=".approval-", dir=parent)
    try:
        with os.fdopen(fd, "w") as f:
            f.write("# owner approval for live trial of profile and optional workflow\n" + "\n".join(hashes) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f"approved profile {prof.hash[:12]}... in {path}")
    return 0


def cmd_binds(a):
    from .binds import make_template, validate_binds
    if a.action == "template":
        prof = load_profile(a.profile)
        with open(a.file, "w") as f:
            json.dump(make_template(prof), f, indent=1)
        print(f"template written to {a.file}: fill in key/role per slot (blank keys are refused)")
        return 0
    with open(a.file) as f:
        problems = validate_binds(json.load(f), load_profile(a.profile) if a.profile else None)
    for p in problems:
        print(f"bind problem: {p}", file=sys.stderr)
    if not problems:
        print("binds ok")
    return 2 if problems else 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="aion2_assist", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    from .workflow_cli import add_parser
    add_parser(sub)
    from .capture import add_parser as add_capture_parser
    add_capture_parser(sub)
    from .ingest import add_parser as add_ingest_parser
    add_ingest_parser(sub)
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
    from .gates import DEFAULT_GATES_FILE
    doctor = sub.add_parser("doctor", help="read-only local readiness report; no device/bus access")
    doctor.add_argument("--config")
    doctor.add_argument("--profile")
    doctor.add_argument("--workflow")
    doctor.add_argument("--gates-file", default=DEFAULT_GATES_FILE)
    from .doctor import main as doctor_main
    doctor.set_defaults(fn=doctor_main)
    run = sub.add_parser("run", help="LIVE session (default-refusing: lists every missing gate and exits 2)",
                         description="Starts the live assist only if ALL gates pass: --i-accept-live-risk, valid config "
                                     "(no UNSET keys), profile whose hash is pinned in the config, explicit window_match, "
                                     "ignore_codes, gates file containing the profile hash, and the startup self-check. "
                                     "The controller then stays OFF until the toggle hotkey.")
    run.add_argument("--config")
    run.add_argument("--profile")
    run.add_argument("--i-accept-live-risk", action="store_true",
                     help="acknowledge the NC Integrated Account Ban risk")
    run.add_argument("--gates-file", default=DEFAULT_GATES_FILE, help="owner approval file (default: %(default)s)")
    run.add_argument("--tick-ms", type=float, default=50.0)
    run.add_argument("--self-check-s", type=float, default=1.0)
    run.add_argument("--workflow", help="calibrated workflow; its hash also needs explicit live approval")
    run.add_argument("--workflow-journal", help="durable progress journal (required with --workflow)")
    run.set_defaults(fn=cmd_run)
    ap_ = sub.add_parser("approve", help="OWNER ONLY: write the profile hash into the gates file")
    ap_.add_argument("--profile", required=True)
    ap_.add_argument("--workflow", help="also approve this specific calibrated workflow hash")
    ap_.add_argument("--i-accept-live-risk", action="store_true")
    ap_.add_argument("--gates-file", default=DEFAULT_GATES_FILE)
    ap_.set_defaults(fn=cmd_approve)
    b = sub.add_parser("binds", help="write a blank binds template / validate a filled-in file")
    b.add_argument("action", choices=("template", "validate"))
    b.add_argument("file")
    b.add_argument("--profile", help="required for template; optional for validate (checks slot ids)")
    b.set_defaults(fn=cmd_binds)
    r = sub.add_parser("replay", help="run the full controller over a timeline JSON or PNG frames dir (mock sink)")
    r.add_argument("source", nargs="?")
    r.add_argument("--config")
    r.add_argument("--profile")
    r.add_argument("--dt", type=float, default=0.1, help="fake-clock seconds per frame (frames dir)")
    r.add_argument("--demo", action="store_true", help="bundled synthetic timeline, zero owner inputs")
    r.add_argument("--json", action="store_true", help="print structured log records")
    r.set_defaults(fn=cmd_replay)
    d = sub.add_parser("dry-run", help="controller over a folder of owner screenshots, fake clock, mock sink")
    d.add_argument("--frames")
    d.add_argument("--config")
    d.add_argument("--profile")
    d.add_argument("--dt", type=float, default=0.1)
    d.add_argument("--demo", action="store_true")
    d.set_defaults(fn=cmd_dry_run)
    a = ap.parse_args(argv)
    if a.cmd == "binds" and a.action == "template" and not a.profile:
        ap.error("binds template needs --profile")
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
