"""Offline capture intake. Synthetic fixtures never establish owner or live evidence."""
import hashlib
import json
from pathlib import Path
import tempfile

from .capture import png_size
from .png import load_frame, write_png
from .profile import REQUIRED_REGIONS, ProfileMismatch, build_profile

REQUIRED_SHOTS = ("normal", "cooldown", "no_target")
EVIDENCE_COUNTS = {"hp_levels": 3, "mp_partial": 1, "q_stack": 2, "e_stack": 2}
MAX_PIXELS = 16_777_216
MAX_PNG_BYTES = 128 * 1024 * 1024
MAX_FILES = 128
INPUT_ERRORS = (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError,
                RecursionError, ProfileMismatch)
REVIEW = [
    "Confirm capture provenance and each assigned game-state label visually.",
    "Measure target geometry on a real enemy-targeted full frame.",
    "Confirm all configured slots are ready in normal and simultaneously on cooldown in cooldown.",
    "Check HP at full, about half and low, partly spent MP, and bar fill directions.",
    "Review Q/E frames in press order through stack exhaustion; two files are only minimum coverage.",
    "Read fresh frames with the resulting profile; owner approval and first live trial remain pending.",
]


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate JSON key: {key}")
        obj[key] = value
    return obj


def _read_json(path):
    if not path.is_file():
        raise ValueError("JSON input must be an existing regular file")
    with path.open("rb") as stream:
        data = stream.read(1_048_577)
    if len(data) > 1_048_576:
        raise ValueError("JSON input exceeds 1 MiB")
    value = json.loads(data, object_pairs_hook=_unique_object)
    if not isinstance(value, dict):
        raise ValueError("JSON input must be an object")
    return value, hashlib.sha256(data).hexdigest()


def _spec_check(spec):
    size = spec.get("frame_size")
    if (not isinstance(size, list) or len(size) != 2 or
            any(type(n) is not int or n <= 0 for n in size) or size[0] * size[1] > MAX_PIXELS):
        raise ValueError(f"frame_size must contain two positive integers, at most {MAX_PIXELS} pixels")
    regions = spec.get("regions")
    if not isinstance(regions, dict):
        raise ValueError("regions must be an object")
    missing = [key for key in REQUIRED_REGIONS if key not in regions]
    if missing:
        raise ValueError(f"missing regions: {', '.join(missing)}; measure on real full frames")
    for group in ("slots", "states"):
        if not isinstance(regions.get(group, {}), dict):
            raise ValueError(f"regions.{group} must be an object")
    if not regions["slots"]:
        raise ValueError("regions.slots must contain at least one calibrated slot")
    states = regions.get("states", {})
    if set(states) & set((*REQUIRED_SHOTS, "friendly_target")):
        raise ValueError("state names must not collide with calibration shot roles")
    rects = {k: regions[k] for k in REQUIRED_REGIONS if k != "slots"}
    rects.update({f"{group}.{k}": v for group in ("slots", "states")
                  for k, v in regions.get(group, {}).items()})
    for label, r in rects.items():
        if (not isinstance(r, list) or len(r) != 4 or any(type(n) is not int for n in r) or
                min(r[:2]) < 0 or min(r[2:]) < 1 or r[0] + r[2] > size[0] or r[1] + r[3] > size[1]):
            raise ValueError(f"invalid full-frame rectangle: {label}")
    return tuple(size), set(states)


def _image_path(base, name):
    if not isinstance(name, str) or not name or Path(name).is_absolute():
        raise ValueError("image paths must be nonempty relative paths inside the manifest directory")
    path = (base / name).resolve()
    if not path.is_relative_to(base) or path.suffix.lower() != ".png":
        raise ValueError("image path must stay inside the manifest directory and end in .png")
    return path


def validate(regions_path, manifest_path):
    """Read-only report; valid means technical intake success, never verified game evidence."""
    report = dict(event="ingest_report", version=1, source=None, synthetic=False, valid=False,
                  calibration_buildable=False, evidence_coverage_complete=False,
                  owner_evidence_satisfied=False, live_verified=False, input_sent=False,
                  missing_shots=[], missing_evidence=[], errors=[], warnings=[], files=[],
                  manual_review_required=list(REVIEW), calibrate_argv=None)
    errors = report["errors"]
    regions_path, manifest_path = Path(regions_path).resolve(), Path(manifest_path).resolve()
    base = manifest_path.parent
    spec, manifest, size, states = None, None, None, set()
    try:
        spec, report["regions_sha256"] = _read_json(regions_path)
        size, states = _spec_check(spec)
    except INPUT_ERRORS as exc:
        errors.append(f"regions: {exc}")
    try:
        manifest, report["manifest_sha256"] = _read_json(manifest_path)
        if type(manifest.get("version")) is not int or manifest["version"] != 1:
            raise ValueError("manifest version must be 1")
        if manifest.get("source") not in ("synthetic", "owner_capture"):
            raise ValueError("source must be synthetic or owner_capture (a declaration, not verification)")
        report["source"] = manifest["source"]
        if set(manifest) - {"version", "source", "shots", "evidence", "note"}:
            raise ValueError("unknown manifest fields")
        if not isinstance(manifest.get("shots"), dict) or not isinstance(manifest.get("evidence"), dict):
            raise ValueError("shots and evidence must be objects")
    except INPUT_ERRORS as exc:
        errors.append(f"manifest: {exc}")
        return report

    shots, evidence = manifest["shots"], manifest["evidence"]
    report["missing_shots"] = [name for name in REQUIRED_SHOTS if name not in shots]
    allowed = set((*REQUIRED_SHOTS, "friendly_target")) | states
    if size and set(shots) - allowed:
        errors.append(f"unknown shot roles: {sorted(set(shots) - allowed)}")
    if set(evidence) - set(EVIDENCE_COUNTS):
        errors.append(f"unknown evidence labels: {sorted(set(evidence) - set(EVIDENCE_COUNTS))}")
    for name in sorted(states - set(shots)):
        report["warnings"].append(f"state {name!r} has no shot and will be omitted from calibration")

    # Canonical paths prevent aliases from counting as separate sequence files.
    shot_paths, evidence_paths = {}, {}
    def resolve(label, name):
        try:
            return _image_path(base, name)
        except INPUT_ERRORS as exc:
            errors.append(f"{label}: {exc}")
            return None

    for role, name in shots.items():
        shot_paths[role] = resolve(f"shots.{role}", name)
    for role, minimum in EVIDENCE_COUNTS.items():
        names = evidence.get(role, [])
        if not isinstance(names, list):
            errors.append(f"evidence.{role} must be an ordered list of relative PNG paths")
            names = []
        paths = [resolve(f"evidence.{role}", name) for name in names]
        evidence_paths[role] = paths
        distinct = set(paths) - {None}
        if len(distinct) < minimum:
            report["missing_evidence"].append(f"{role}: need {minimum} distinct files, got {len(distinct)}")
        if len(distinct) != len(paths):
            errors.append(f"evidence.{role} contains duplicate or invalid paths")
    paths = (set(shot_paths.values()) | {p for group in evidence_paths.values() for p in group}) - {None}
    marked = (base / "SYNTHETIC.json").exists() or any(p.name.startswith("synthetic-") for p in paths)
    report["synthetic"] = manifest["source"] == "synthetic" or marked
    if manifest["source"] == "owner_capture" and marked:
        errors.append("known synthetic bundle cannot be relabeled as owner_capture")
    if len(paths) > MAX_FILES:
        errors.append(f"select at most {MAX_FILES} PNG files per manifest")
        return report

    good, frames = set(), {}
    for path in sorted(paths):
        entry = dict(path=str(path.relative_to(base)), decoded=False)
        report["files"].append(entry)
        try:
            before = path.stat()
            if not path.is_file() or before.st_size > MAX_PNG_BYTES:
                raise ValueError("PNG must be a regular file of at most 128 MiB")
            actual_size = png_size(path)
            if actual_size is None:
                raise ValueError("incomplete or corrupt PNG chunk structure/checksum")
            entry["frame_size"] = list(actual_size)
            if size is None:
                raise ValueError("pixel decoding requires a valid regions spec first")
            if actual_size != size:
                raise ValueError(f"full-frame size {actual_size} differs from regions {size}; crops are refused")
            frame = load_frame(path, force_stdlib=True)
            with path.open("rb") as stream:
                entry["sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
            after = path.stat()
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                    after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                raise ValueError("file changed during validation; retry after capture finishes")
            entry["decoded"] = True
            good.add(path)
            if path in shot_paths.values():
                frames[path] = frame
            del frame  # evidence-only pixels are not retained
        except INPUT_ERRORS as exc:
            entry["error"] = str(exc)
            errors.append(f"{entry['path']}: {exc}")
    report["evidence_coverage_complete"] = not report["missing_evidence"] and all(
        paths and all(p in good for p in paths) for paths in evidence_paths.values())
    if size and not report["missing_shots"] and all(p in frames for p in shot_paths.values()):
        try:
            profile = build_profile(spec, {role: frames[p] for role, p in shot_paths.items()})
            report["calibration_buildable"] = True
            report["profile_hash"] = profile.hash
        except INPUT_ERRORS as exc:
            errors.append(f"calibration: {exc}")
    report["valid"] = (not errors and report["calibration_buildable"] and report["evidence_coverage_complete"])
    if report["valid"] and not report["synthetic"]:
        argv = ["python3", "-m", "aion2_assist", "calibrate", "--regions", str(regions_path)]
        for role, path in shot_paths.items():
            argv += ["--shot", f"{role}={path}"]
        report["calibrate_argv"] = argv + ["--out", "<new-profile.json>"]
    return report


def fixtures(out):
    """Create a new, clearly synthetic bundle. Never use the owner's draft geometry."""
    from .synth import REGIONS, make_frame
    out = Path(out).resolve()
    root = Path(__file__).resolve().parents[1]
    if any(out.is_relative_to(root / name) for name in ("aion2_assist", "tests", "tools", "kwin_script")):
        raise ValueError("fixture output must stay outside packaged source directories")
    out.mkdir(parents=True, exist_ok=False)
    def write_json(name, data):
        (out / name).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    write_json("SYNTHETIC.json", {"source": "synthetic", "owner_evidence_satisfied": False,
                                   "live_verified": False, "note": "Not real Aion 2 UI or owner captures."})
    write_json("regions.json", REGIONS)
    definitions = {
        "normal": {}, "cooldown": {"cooldown": tuple(REGIONS["regions"]["slots"])},
        "no_target": {"target": "none"}, "friendly_target": {"target": "friendly"},
        "hp_half": {"hp": .5, "mp": .6}, "hp_low": {"hp": .2, "mp": .4},
        "q_after_1": {}, "q_after_2": {"cooldown": ("s3",)},
        "e_after_1": {}, "e_after_2": {"cooldown": ("s4",)},
        **{state: {"state": state} for state in REGIONS["regions"]["states"]},
    }
    names = {}
    for name, kwargs in definitions.items():
        names[name] = f"synthetic-{name}.png"
        write_png(out / names[name], make_frame(0, **kwargs))
    manifest = dict(version=1, source="synthetic",
                    note="Offline demo only. s3/s4 are fictional Q/E examples, not game binds or stack evidence.",
                    shots={name: names[name] for name in (*REQUIRED_SHOTS, "friendly_target",
                                                        *REGIONS["regions"]["states"])},
                    evidence={"hp_levels": [names[n] for n in ("normal", "hp_half", "hp_low")],
                              "mp_partial": [names["hp_half"]],
                              "q_stack": [names[n] for n in ("q_after_1", "q_after_2")],
                              "e_stack": [names[n] for n in ("e_after_1", "e_after_2")]})
    write_json("manifest.json", manifest)
    return dict(event="ingest_fixtures", source="synthetic", directory=str(out), files=len(names),
                owner_evidence_satisfied=False, live_verified=False, input_sent=False)


def _run(args):
    try:
        if args.ingest_command == "fixtures":
            result = fixtures(args.out)
        elif args.ingest_command == "demo":
            with tempfile.TemporaryDirectory(prefix="aion2-synthetic-ingest-") as folder:
                out = Path(folder) / "fixtures"
                fixtures(out)
                result = validate(out / "regions.json", out / "manifest.json")
        else:
            result = validate(args.regions, args.manifest)
        print(json.dumps(result, sort_keys=True))
        return 0 if result.get("valid", args.ingest_command == "fixtures") else 2
    except INPUT_ERRORS as exc:
        print(json.dumps(dict(event="ingest_error", error=str(exc), owner_evidence_satisfied=False,
                              live_verified=False, input_sent=False), sort_keys=True))
        return 2


def add_parser(sub):
    parser = sub.add_parser("ingest", help="offline capture validation and synthetic fixtures; never sends input")
    commands = parser.add_subparsers(dest="ingest_command", required=True)
    fixture = commands.add_parser("fixtures", help="create a new clearly marked synthetic bundle")
    fixture.add_argument("--out", required=True, type=Path)
    check = commands.add_parser("validate", help="check labeled captures without writing a profile or approval")
    check.add_argument("--regions", required=True, type=Path)
    check.add_argument("--manifest", required=True, type=Path)
    commands.add_parser("demo", help="generate and validate a synthetic bundle in a temporary directory")
    parser.set_defaults(fn=_run)
