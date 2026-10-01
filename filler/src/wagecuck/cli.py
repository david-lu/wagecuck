import argparse
import asyncio
import json
from pathlib import Path

from pydantic import ValidationError

from .agent_config import add_agent_arguments, create_agent
from .demo import generate_profile
from .evaluation import write_report
from .models import Profile, RunOptions
from .runner import ApplicationRunner


def profiles_directory() -> Path:
    """Use the shared repository profiles when running from this checkout."""
    shared = Path(__file__).resolve().parents[3] / "profiles"
    return shared if shared.is_dir() else Path.cwd() / "profiles"


def resolve_profile_path(reference: str | Path) -> Path:
    """Resolve a short profile name while preserving explicit filesystem paths."""
    path = Path(reference)
    if path.exists() or path.suffix or len(path.parts) > 1:
        return path
    return profiles_directory() / path / "profile.json"


def emit_result(payload: dict, output: Path | None) -> None:
    """Print a result and optionally publish the same JSON at an explicit path."""
    if output is not None:
        write_report(output, payload)
    print(json.dumps(payload, indent=2))
    if output is not None:
        print(f"Result: {output.resolve()}")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="wagecuck")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo-profile", help="Generate a synthetic profile and resume PDF")
    demo.add_argument("--output", type=Path, default=profiles_directory() / "dummy")
    server = commands.add_parser("demo-server", help="Serve local test application forms")
    server.add_argument("--port", type=int, default=8765)
    run = commands.add_parser("run", help="Process one job application URL")
    run.add_argument("url")
    run.add_argument(
        "--profile",
        default="dummy",
        help=("Profile name or path to a profile JSON file (default: shared profiles/dummy/profile.json)"),
    )
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--mode", choices=("inspect", "fill", "submit"), default="fill")
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Fill a live application with a synthetic profile, then stop before submission",
    )
    run.add_argument(
        "--wait-for-user",
        action="store_true",
        help="Fill the application, open the browser and wait for you to submit (fill mode only)",
    )
    visibility = run.add_mutually_exclusive_group()
    visibility.add_argument(
        "--show-browser",
        "--headed",
        dest="headed",
        action="store_true",
        help="Open a visible Chromium window so you can watch Playwright work",
    )
    visibility.add_argument(
        "--headless",
        dest="headed",
        action="store_false",
        help="Run without a browser window (default)",
    )
    run.set_defaults(headed=None)
    run.add_argument(
        "--slow-mo",
        type=int,
        default=0,
        metavar="MS",
        help="Delay Playwright operations by this many milliseconds for easier viewing (default: 0)",
    )
    run.add_argument("--timeout", type=float, default=120)
    run.add_argument(
        "--output",
        type=Path,
        help="Write the final result JSON to this path in addition to stdout",
    )
    run.add_argument("--artifacts", type=Path, default=Path(".artifacts/application/runs"))
    run.add_argument(
        "--database",
        type=Path,
        default=Path(".artifacts/application/applications.sqlite3"),
    )
    run.add_argument("--storage-state", type=Path)
    run.add_argument(
        "--sensitive-artifacts",
        action="store_true",
        help="Save screenshot and trace, which contain applicant data",
    )
    add_agent_arguments(run)
    args = parser.parse_args(argv)
    if args.command == "demo-server":
        from .fixtures import serve

        serve(args.port)
        return 0
    if args.command == "demo-profile":
        print(json.dumps({"profile": str(generate_profile(args.output))}))
        return 0
    selected_mode = "dry-run" if args.dry_run else args.mode
    if args.wait_for_user and (selected_mode != "fill" or args.headed is False):
        parser.error("--wait-for-user requires fill mode and cannot be combined with --headless")
    try:
        agent = create_agent(args)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        profile = Profile.load(resolve_profile_path(args.profile))
        if args.dry_run and not profile.synthetic:
            parser.error("--dry-run requires a synthetic profile; use --mode fill for real data")
        options = RunOptions(
            mode=selected_mode,
            headless=not args.headed,
            wait_for_user=args.wait_for_user,
            agent_fill=args.agent_fill,
            slow_mo_ms=args.slow_mo,
            timeout_seconds=args.timeout,
            artifacts_dir=args.artifacts,
            database=args.database,
            storage_state=args.storage_state,
            capture_sensitive_artifacts=args.sensitive_artifacts,
        )
    except (OSError, ValidationError, ValueError):
        emit_result(
            {
                "success": False,
                "status": "failed",
                "code": "PROFILE_INVALID",
                "message": "Invalid profile, profile path, or run options.",
            },
            args.output,
        )
        return 1
    result = asyncio.run(ApplicationRunner(agent).run(args.url, profile, options))
    payload = result.model_dump(mode="json")
    if args.dry_run:
        payload.update(
            mode="dry-run",
            execution_mode="dry-run",
            network_disabled_before_filling=False,
            final_submission_enabled=False,
        )
    emit_result(payload, args.output)
    return (
        0
        if result.status in ("succeeded", "ready", "inspected")
        else 2
        if result.status == "unknown"
        else 1
    )
