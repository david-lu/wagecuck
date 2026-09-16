import argparse
import asyncio
import json
import os
from pathlib import Path

from pydantic import ValidationError

from .agent import OllamaMappingAgent
from .demo import generate_profile
from .models import Profile, RunOptions
from .runner import ApplicationRunner


def main():
    parser = argparse.ArgumentParser(prog="wagecuck")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo-profile", help="Generate a synthetic profile and resume PDF")
    demo.add_argument("--output", type=Path, default=Path("profiles/demo"))
    server = commands.add_parser("demo-server", help="Serve local test application forms")
    server.add_argument("--port", type=int, default=8765)
    run = commands.add_parser("run", help="Process one job application URL")
    run.add_argument("url")
    run.add_argument("--profile", type=Path, required=True)
    run.add_argument("--mode", choices=("inspect", "fill", "submit"), default="fill")
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
    run.add_argument("--artifacts", type=Path, default=Path("runs"))
    run.add_argument("--database", type=Path, default=Path("runs/applications.sqlite3"))
    run.add_argument("--storage-state", type=Path)
    run.add_argument(
        "--sensitive-artifacts",
        action="store_true",
        help="Save screenshot and trace, which contain applicant data",
    )
    run.add_argument("--agent-model", default=os.environ.get("WAGECUCK_AGENT_MODEL"))
    run.add_argument(
        "--agent-fill",
        action="store_true",
        help="Draft unresolved free-text answers from supplied profile facts (requires --agent-model)",
    )
    run.add_argument(
        "--agent-endpoint",
        default=os.environ.get("WAGECUCK_AGENT_ENDPOINT", "http://localhost:11434"),
    )
    args = parser.parse_args()
    if args.command == "demo-server":
        from .fixtures import serve

        serve(args.port)
        return 0
    if args.command == "demo-profile":
        print(json.dumps({"profile": str(generate_profile(args.output))}))
        return 0
    if args.wait_for_user and (args.mode != "fill" or args.headed is False):
        parser.error("--wait-for-user requires fill mode and cannot be combined with --headless")
    if args.agent_fill and not args.agent_model:
        parser.error("--agent-fill requires --agent-model or WAGECUCK_AGENT_MODEL")
    try:
        profile = Profile.load(args.profile)
        options = RunOptions(
            mode=args.mode,
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
        print(
            json.dumps(
                {
                    "success": False,
                    "status": "failed",
                    "code": "PROFILE_INVALID",
                    "message": "Invalid profile, profile path, or run options.",
                }
            )
        )
        return 1
    agent = OllamaMappingAgent(args.agent_model, args.agent_endpoint) if args.agent_model else None
    result = asyncio.run(ApplicationRunner(agent).run(args.url, profile, options))
    print(result.model_dump_json(indent=2))
    return (
        0
        if result.status in ("succeeded", "ready", "inspected")
        else 2
        if result.status == "unknown"
        else 1
    )
