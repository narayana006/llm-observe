"""``llm-observe``: list, search and show traces, or open the local web page."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .store import TraceStore, default_db_path, preview


def _opt(value: object, fmt: str = "{}") -> str:
    return "-" if value is None else fmt.format(value)


def format_list(traces: list[dict]) -> str:
    headers = ["ID", "TIME (UTC)", "NAME", "MODEL", "MS", "IN", "OUT", "COST USD", "STATUS", "PROMPT"]
    right = {0, 4, 5, 6, 7}
    rows = [
        [
            str(t["id"]),
            t["ts"][:19].replace("T", " "),
            t["name"],
            _opt(t["model"]),
            f"{t['latency_ms']:.0f}",
            _opt(t["input_tokens"]),
            _opt(t["output_tokens"]),
            _opt(t["cost_usd"], "{:.6f}"),
            "error" if t["error"] else "ok",
            preview(t["prompt"], 48),
        ]
        for t in traces
    ]
    widths = [max(len(row[i]) for row in [headers, *rows]) for i in range(len(headers))]

    def line(cells: list[str]) -> str:
        parts = [c.rjust(w) if i in right else c.ljust(w) for i, (c, w) in enumerate(zip(cells, widths))]
        return "  ".join(parts).rstrip()

    return "\n".join(line(row) for row in [headers, *rows])


def format_trace(t: dict) -> str:
    lines = [
        f"Trace {t['id']}",
        f"  time:     {t['ts']}",
        f"  name:     {t['name']}",
        f"  model:    {_opt(t['model'])}",
        f"  latency:  {t['latency_ms']:.1f} ms",
        f"  tokens:   {_opt(t['input_tokens'])} in, {_opt(t['output_tokens'])} out",
        f"  cost:     {_opt(t['cost_usd'], '${:.6f}')}",
        f"  status:   {'error' if t['error'] else 'ok'}",
        "",
        "Prompt:",
        t["prompt"],
        "",
    ]
    if t["error"]:
        lines += ["Error:", t["error"]]
    else:
        lines += ["Response:", t["response"] or ""]
    return "\n".join(lines)


def format_stats(rows: list[dict]) -> str:
    headers = ["NAME", "CALLS", "ERRORS", "AVG MS", "MAX MS", "IN TOK", "OUT TOK", "COST USD"]
    body = [
        [
            r["name"],
            str(r["calls"]),
            str(r["errors"]),
            f"{r['avg_ms']:.0f}",
            f"{r['max_ms']:.0f}",
            f"{r['input_tokens']:,}",
            f"{r['output_tokens']:,}",
            f"{r['cost_usd']:.6f}",
        ]
        for r in rows
    ]
    widths = [max(len(row[i]) for row in [headers, *body]) for i in range(len(headers))]

    def line(cells: list[str]) -> str:
        parts = [cells[0].ljust(widths[0])] + [c.rjust(w) for c, w in zip(cells[1:], widths[1:])]
        return "  ".join(parts).rstrip()

    return "\n".join(line(row) for row in [headers, *body])


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--db", metavar="FILE", help="SQLite file (default: $LLM_OBSERVE_DB or llm_observe.db)"
    )
    filters = argparse.ArgumentParser(add_help=False)
    filters.add_argument("--limit", type=int, default=20, help="maximum rows (default: 20)")
    filters.add_argument("--errors", action="store_true", help="only calls that raised")
    filters.add_argument("--name", help="only traces with this name")
    filters.add_argument("--model", help="only traces for this model")
    filters.add_argument("--since", metavar="DATE", help="only traces at or after this UTC date/time (ISO 8601)")
    filters.add_argument("--json", action="store_true", help="print JSON instead of a table")

    parser = argparse.ArgumentParser(
        prog="llm-observe", description="Inspect LLM calls recorded by observe()."
    )
    parser.add_argument("--version", action="version", version=f"llm-observe {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", parents=[common, filters], help="list recent traces, newest first")
    search = sub.add_parser(
        "search", parents=[common, filters], help="find traces whose prompt, response or error contains TEXT"
    )
    search.add_argument("text", metavar="TEXT")
    show = sub.add_parser("show", parents=[common], help="print one trace in full")
    show.add_argument("id", type=int)
    show.add_argument("--json", action="store_true", help="print JSON")
    sub.add_parser("stats", parents=[common], help="calls, errors, latency, tokens and cost per name")
    ui = sub.add_parser("ui", parents=[common], help="serve a local web page for browsing traces")
    ui.add_argument("--host", default="127.0.0.1", help="address to bind (default: 127.0.0.1)")
    ui.add_argument("--port", type=int, default=8321, help="port (default: 8321)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db = args.db or default_db_path()
    if not Path(db).exists():
        print(f"llm-observe: error: no database at {db}", file=sys.stderr)
        return 2

    if args.command == "ui":
        from .ui import serve

        serve(db, args.host, args.port)
        return 0

    store = TraceStore(db)
    try:
        if args.command in ("list", "search"):
            traces = store.list(
                query=getattr(args, "text", None),
                name=args.name,
                model=args.model,
                errors_only=args.errors,
                since=args.since,
                limit=args.limit,
            )
            if args.json:
                print(json.dumps(traces, indent=2))
            elif traces:
                print(format_list(traces))
            else:
                print("No traces found.")
        elif args.command == "show":
            trace = store.get(args.id)
            if trace is None:
                print(f"llm-observe: error: no trace with id {args.id}", file=sys.stderr)
                return 1
            print(json.dumps(trace, indent=2) if args.json else format_trace(trace))
        elif args.command == "stats":
            rows = store.stats()
            print(format_stats(rows) if rows else "No traces found.")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
