"""JSON CLI for the experimental task runtime."""

import argparse
import json
import signal
import sys

from .core import MAX_MANIFEST_BYTES, Runtime, RuntimeError, build_plan, validate_manifest, validate_prefix


def _manifest(path):
    with open(path, "rb") as source:
        data = source.read(MAX_MANIFEST_BYTES + 1)
    if len(data) > MAX_MANIFEST_BYTES:
        raise RuntimeError("invalidManifest", "manifest exceeds 64 KiB")

    def distinct_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise RuntimeError("invalidManifest", "duplicate JSON key: " + key)
            result[key] = value
        return result

    return validate_manifest(json.loads(data, object_pairs_hook=distinct_keys))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Agentrooms experimental task workspace runtime")
    parser.add_argument("--state-dir", help="explicit absolute state/workspace directory")
    parser.add_argument("--engine-prefix-json", help="JSON argv prefix, e.g. [\"nerdctl\"]; saved per run")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "plan", "run"):
        command = commands.add_parser(name)
        command.add_argument("manifest")
    for name in ("inspect", "status", "stop", "recover", "outputs"):
        command = commands.add_parser(name)
        command.add_argument("id")
    commands.add_parser("list")
    args = parser.parse_args(argv)
    runtime = None
    try:
        prefix = validate_prefix(json.loads(args.engine_prefix_json)) if args.engine_prefix_json else None
        if args.command == "validate":
            manifest = _manifest(args.manifest)
            result = {"valid": True, "schemaVersion": 1, "id": manifest["id"]}
        else:
            if not args.state_dir:
                raise RuntimeError("invalidStateDirectory", "--state-dir is required")
            if args.command == "plan":
                result = build_plan(_manifest(args.manifest), args.state_dir, prefix)
            else:
                runtime = Runtime(args.state_dir, prefix)
                if args.command == "run":
                    result = runtime.run(_manifest(args.manifest))
                elif args.command == "list":
                    result = {"runs": runtime.list(), "observation": "saved; inspect an id for current engine state"}
                else:
                    result = getattr(runtime, args.command)(args.id)
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
        if args.command in ("run", "recover", "stop", "inspect") and result.get("state") in ("failed", "unknown", "timed_out"):
            return 1
        return 0
    except (RuntimeError, OSError, ValueError) as exc:
        print(json.dumps({"error": getattr(exc, "code", "invalidInput"), "message": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"error": "interrupted", "message": "controller interrupted; inspect the recorded run to verify current engine state"}), file=sys.stderr)
        return 130
    finally:
        if runtime is not None:
            runtime.close()


def cli():
    """Install signal handling for both the console script and module command."""
    def terminate(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    return main()


if __name__ == "__main__":
    sys.exit(cli())
