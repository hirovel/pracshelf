import argparse
import json
import sys
from pathlib import Path

import httpx

from .analysis import ORIGINS, VERDICTS, Analysis
from .collector import FEEDS, Collector
from .pipeline import Pipeline
from .storage import Store


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Run the local PracShelf workbench")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Start the local app with Codex setup guidance")
    serve.add_argument("--port", type=int, default=8766)
    serve.add_argument("--no-browser", action="store_true")
    backup = commands.add_parser("backup", help="Back up the private library without credentials")
    backup.add_argument("destination", type=Path)
    restore = commands.add_parser("restore", help="Validate and restore into a new directory")
    restore.add_argument("backup", type=Path)
    restore.add_argument("--into", type=Path, required=True)
    for name in ("post", "search", "feed", "article", "fetch", "extract"):
        command = commands.add_parser(name)
        command.add_argument("input")
        command.add_argument(
            "--limit", type=int, choices=range(1, 101), default=5, metavar="1..100"
        )
        command.add_argument(
            "--max-pages", type=int, choices=range(1, 11), default=1, metavar="1..10"
        )
    for name in ("sources", "list", "runs", "export"):
        commands.add_parser(name)
    prepare = commands.add_parser("prepare", help="Export version-bound source and model input")
    prepare.add_argument("version_id", type=int)
    accept = commands.add_parser("import-analysis", help="Import and validate an external result")
    accept.add_argument("analysis_id")
    accept.add_argument("response_file", type=Path)
    accept.add_argument("--origin", required=True, choices=sorted(ORIGINS))
    accept.add_argument("--model", help="External model/tool label, explicitly self-reported")
    inspect = commands.add_parser("inspect-analysis", help="Show each stage and its output files")
    inspect.add_argument("analysis_id")
    inspect.add_argument("--attempt")
    render = commands.add_parser("render-analysis", help="Rebuild an article-first reading view")
    render.add_argument("analysis_id")
    render.add_argument("attempt_id")
    review = commands.add_parser("review-point", help="Record YOUR semantic judgment of a point")
    review.add_argument("analysis_id")
    review.add_argument("attempt_id")
    review.add_argument("point", type=int)
    review.add_argument("--verdict", choices=sorted(VERDICTS), required=True)
    review.add_argument("--note", required=True)
    capture = commands.add_parser(
        "pipeline-capture", help="Run the three capture stages and stop for review"
    )
    capture.add_argument("url")
    args = parser.parse_args()
    if args.command in {"backup", "restore"}:
        import sqlite3

        from .library_backup import create_backup, restore_backup

        try:
            output = (create_backup(args.data_dir, args.destination) if args.command == "backup"
                      else restore_backup(args.backup, args.into))
            print(json.dumps(output, ensure_ascii=False, indent=2))
        except (ValueError, OSError, sqlite3.Error) as error:
            print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
            raise SystemExit(1) from error
        return
    if args.command == "serve":
        import uvicorn

        from .web import create_app

        if not 1 <= args.port <= 65535:
            parser.error("--port must be between 1 and 65535")
        url = f"http://127.0.0.1:{args.port}/setup"
        print(f"本地工作台：{url}\n资料目录：{args.data_dir.resolve()}", flush=True)
        if not args.no_browser:
            import threading
            import webbrowser

            opener = threading.Timer(1.5, webbrowser.open, args=(url,))
            opener.daemon = True
            opener.start()
        uvicorn.run(create_app(args.data_dir.resolve()), host="127.0.0.1", port=args.port)
        return
    if args.command == "sources":
        print(json.dumps(FEEDS, ensure_ascii=False, indent=2))
        return
    store = Store(args.data_dir.resolve())
    try:
        if args.command == "list":
            output = [
                {"version_id": row["version_id"]}
                | {k: row["document"][k] for k in ("kind", "title", "url")}
                for row in store.documents()
            ]
        elif args.command == "runs":
            output = store.runs()
        elif args.command == "export":
            output = {"reading_index": str(store.export())}
        elif args.command == "prepare":
            output = Analysis(store).prepare(args.version_id)
        elif args.command == "import-analysis":
            output = Analysis(store).accept(
                args.analysis_id,
                args.response_file.read_bytes(),
                origin=args.origin,
                model=args.model,
            )
        elif args.command == "inspect-analysis":
            output = Analysis(store).inspect(args.analysis_id, args.attempt)
        elif args.command == "render-analysis":
            output = Analysis(store).render(args.analysis_id, args.attempt_id)
        elif args.command == "review-point":
            output = Analysis(store).review(
                args.analysis_id, args.attempt_id, args.point, args.verdict, args.note
            )
        else:
            with httpx.Client(
                timeout=15,
                follow_redirects=True,
                max_redirects=5,
                headers={"User-Agent": "PracShelf/0.1 (personal reader)"},
            ) as client:
                if args.command == "pipeline-capture":
                    flow = Pipeline(store, client)
                    output = flow.create(args.url)
                    for stage in ("fetch", "extract", "validate"):
                        output = flow.run(output["id"], stage)
                        if any(s["status"] == "failed" for s in output["stages"]):
                            output["status"] = "failed"
                            break
                else:
                    output = Collector(store, client).collect(
                        args.command, args.input, limit=args.limit, max_pages=args.max_pages
                    )
        print(json.dumps(output, ensure_ascii=False, indent=2))
        if isinstance(output, dict) and output.get("status") in {
            "partial",
            "failed",
            "invalid_output",
            "invalid_evidence",
        }:
            raise SystemExit(1)
    except (ValueError, OSError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False, indent=2))
        raise SystemExit(1) from error
    finally:
        store.close()


if __name__ == "__main__":
    main()
