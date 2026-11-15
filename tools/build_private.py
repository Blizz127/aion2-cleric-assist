#!/usr/bin/env python3
"""Build and smoke an isolated private source artifact. Does not publish or run live."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from aion2_assist import __version__

SOURCE_DIRS = ("aion2_assist", "kwin_script", "tests", "tools")
SOURCE_FILES = ("README.md", "HANDOFF.md", "deploy-alienware.sh", "requirements.txt", "config.example.json",
                "regions.example.json", "config.alienware-draft.json", "regions.alienware-2560x1600.draft.json")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_deployment_inventory(stage, env):
    argv = ["sh", "./deploy-alienware.sh", "--check"]
    result = subprocess.run(argv, cwd=stage, env=env, capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise RuntimeError(f"deployment inventory check: exit {result.returncode}\n{result.stdout}\n{result.stderr}")
    declared = result.stdout.splitlines()
    expected = set(SOURCE_DIRS + SOURCE_FILES)
    if len(declared) != len(expected) or set(declared) != expected:
        raise RuntimeError("deployment inventory differs from packaged source entries")
    missing = [name for name in declared if not (stage / name).exists()]
    if missing:
        raise RuntimeError(f"deployment inventory references missing packaged files: {missing}")
    return dict(argv=argv, exit_code=result.returncode, files=declared)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    name = f"aion2-assist-{__version__}-{stamp}"
    with tempfile.TemporaryDirectory(prefix="aion2-build-") as temporary:
        stage = Path(temporary) / name
        stage.mkdir()
        for item in SOURCE_DIRS:
            shutil.copytree(ROOT / item, stage / item, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.bak*"))
        for item in SOURCE_FILES:
            shutil.copy2(ROOT / item, stage / item)
        # Compile without generating caches or touching source files.
        for path in stage.rglob("*.py"):
            compile(path.read_text(), str(path.relative_to(stage)), "exec")
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        env.pop("PYTHONPATH", None)
        checks = [check_deployment_inventory(stage, env)]  # --check never contacts the target
        for argv, expected in ((["--help"], 0), (["replay", "--demo", "--json"], 0),
                               (["workflow", "demo"], 0), (["ingest", "demo"], 0),
                               (["doctor"], 0), (["capture", "--help"], 0),
                               (["capture", "--every", "0"], 2),
                               (["check-config", "--config", "config.alienware-draft.json"], 0),
                               (["run", "--gates-file", str(stage / "NONEXISTENT-APPROVAL")], 2)):
            result = subprocess.run([sys.executable, "-m", "aion2_assist", *argv], cwd=stage, env=env,
                                    capture_output=True, text=True, timeout=30)
            if result.returncode != expected:
                raise RuntimeError(f"smoke {argv}: exit {result.returncode}\n{result.stdout}\n{result.stderr}")
            check = dict(argv=argv, exit_code=result.returncode)
            if argv == ["workflow", "demo"]:
                summary = json.loads(result.stdout.splitlines()[-1])
                if not summary["complete"] or summary["confirmed"] != 7 or summary["live_verified"]:
                    raise RuntimeError("workflow smoke did not prove seven synthetic stages")
                check["summary"] = summary
            if argv == ["ingest", "demo"]:
                summary = json.loads(result.stdout)
                if (not summary["valid"] or not summary["synthetic"] or summary["live_verified"] or
                        summary["owner_evidence_satisfied"] or summary["input_sent"] or summary["calibrate_argv"]):
                    raise RuntimeError("ingest smoke failed or crossed the synthetic evidence boundary")
                check["summary"] = summary
            checks.append(check)
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        files = {str(p.relative_to(stage)): sha(p) for p in sorted(stage.rglob("*")) if p.is_file()}
        manifest = dict(version=__version__, build=name, source_head=head, source_files=files,
                        smoke_checks=checks, live_verified=False, published=False)
        (stage / "BUILD.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        artifact = out / f"{name}.tar.gz"
        with tarfile.open(artifact, "x:gz") as tar:
            tar.add(stage, arcname=name)
        checksum = sha(artifact)
        (out / f"{name}.tar.gz.sha256").write_text(f"{checksum}  {artifact.name}\n")
        receipt = dict(version=__version__, artifact=str(artifact), sha256=checksum, smoke_checks=len(checks),
                       live_verified=False, published=False)
        (out / f"{name}.receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
