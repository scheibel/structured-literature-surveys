import json
from pathlib import Path
import shutil
import argparse

from .providers import PROVIDERS
from .runner import prepare, collect
from .quality import evaluate


def add_parser(subparsers):
    parser = subparsers.add_parser("snowball", help="Backward/forward citation discovery and reference-quality smoke tests.")
    commands = parser.add_subparsers(dest="snowball_command", required=True)
    prep = commands.add_parser("prepare", help="Validate and snapshot explicit seeds offline (dry run).")
    prep.add_argument("--seeds", help="CSV or BibTeX seed file")
    prep.add_argument("--candidate-id", action="append", default=[], help="Existing library candidate ID; repeatable")
    prep.add_argument("--slug", required=True)
    prep.add_argument("--date")
    prep.add_argument("--parent-run")
    prep.add_argument("--overrides", help="CSV with left/right record IDs, decision, actor, timestamp, reason")
    settings(prep)
    for name in ("collect", "rebuild"):
        command = commands.add_parser(name, help="Collect provider data." if name == "collect" else "Rebuild derived files offline; leave library unchanged.")
        command.add_argument("--run-dir", required=True)
        if name == "collect":
            command.add_argument("--resume", action="store_true")
    smoke = commands.add_parser("smoke", help="Run an isolated bounded live pilot or replay a smoke snapshot.")
    mode = smoke.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--replay", metavar="RUN_DIR")
    smoke.add_argument("--benchmark", default="tests/benchmarks/snowballing")
    smoke.add_argument("--slug", default="smoke")
    smoke.add_argument("--date")
    smoke.add_argument("--baseline")
    settings(smoke)
    smoke.set_defaults(request_budget=30, max_relationships=20, retries=0, timeout=10)
    quality = commands.add_parser("evaluate", help="Re-evaluate saved smoke data after completing reference_review.csv.")
    quality.add_argument("--run-dir", required=True)
    quality.add_argument("--baseline")


def relationship_limit(value):
    if value == "all":
        return None
    try:
        return int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Use a positive integer or 'all'") from None


def settings(parser):
    parser.add_argument("--providers", nargs="+", choices=list(PROVIDERS), default=list(PROVIDERS))
    parser.add_argument("--direction", choices=["backward", "forward", "both"], default="both")
    parser.add_argument("--request-budget", type=int, default=1000, help="Attempt limit per provider across resumes, including retries")
    parser.add_argument("--budget", action="append", default=[], metavar="PROVIDER=N", help="Override one provider's request budget")
    parser.add_argument("--max-relationships", type=relationship_limit, help="Per seed and direction; 'all' means no relationship cap")
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--request-interval", type=float, default=1.0)
    parser.add_argument("--credential-env", action="append", default=[], metavar="PROVIDER=ENV_NAME")


def pairs(values, convert=str):
    result = {}
    for value in values:
        key, sep, val = value.partition("=")
        if not sep or key not in PROVIDERS or not val:
            raise ValueError("Expected PROVIDER=VALUE for a known provider")
        result[key] = convert(val)
    return result


def run(args):
    project = Path.cwd()
    try:
        command = args.snowball_command
        if command == "prepare" or command == "smoke" and args.live:
            kwargs = {k: getattr(args, k) for k in ("providers", "direction", "slug", "request_budget", "max_relationships", "timeout", "retries", "page_size", "request_interval")}
            kwargs.update(run_date=args.date, credential_env=pairs(args.credential_env), budgets=pairs(args.budget, int))
            if command == "prepare":
                path = prepare(project=project, seeds=args.seeds, candidate_ids=args.candidate_id, parent_run=args.parent_run, overrides=args.overrides, **kwargs)
                print(f"run_dir={path}\nstatus=prepared")
                return 0
            path = prepare(project=project, seeds=Path(args.benchmark) / "seeds.csv", smoke=True, benchmark=args.benchmark, **kwargs)
            print(f"run_dir={path}", flush=True)
            collect(path, project=project)
            result = evaluate(path, baseline=args.baseline)
        elif command in ("collect", "rebuild"):
            result = collect(args.run_dir, project=project, resume=getattr(args, "resume", False), offline=command == "rebuild")
        elif command == "evaluate":
            result = evaluate(args.run_dir, baseline=args.baseline)
        else:
            source = Path(args.replay).resolve()
            if not json.loads((source / "run_config.json").read_text()).get("smoke"):
                raise ValueError("Replay source must be an isolated smoke run")
            # Replay into a sibling snapshot; never rewrite its raw inputs or reviews.
            destination = project / "smoke-tests" / (source.name + "-replay")
            if destination.exists():
                raise ValueError("Replay destination exists; use rebuild/evaluate on it")
            shutil.copytree(source, destination)
            collect(destination, project=project, offline=True)
            result = evaluate(destination, baseline=args.baseline)
            print(f"run_dir={destination}")
        print("status=" + result["status"])
        return 0 if result["status"] in ("ok", "pass") else 1 if result["status"] == "fail" else 2
    except (ValueError, OSError) as exc:
        print(f"error={exc}")
        return 1
