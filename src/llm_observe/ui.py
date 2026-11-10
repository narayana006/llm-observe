"""A small read-only web page over the trace database, standard library only."""

from __future__ import annotations

import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .store import TraceStore, preview

PAGE_SIZE = 100

STYLE = """
body { font: 14px/1.45 system-ui, sans-serif; margin: 2rem auto; max-width: 1100px; padding: 0 1rem; color: #1c1c1c; }
a { color: #0b57d0; text-decoration: none; } a:hover { text-decoration: underline; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: .35rem .6rem; border-bottom: 1px solid #e4e4e4; vertical-align: top; }
th { font-weight: 600; background: #f6f6f6; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.err { color: #b3261e; } .ok { color: #1e7d32; } .muted { color: #6b6b6b; }
form { margin: 1rem 0; display: flex; gap: .6rem; align-items: center; }
input[type=text] { flex: 1; padding: .4rem .6rem; font: inherit; }
button { padding: .4rem .9rem; font: inherit; }
pre { background: #f6f6f6; padding: .8rem; overflow-x: auto; white-space: pre-wrap; word-break: break-word; }
dl { display: grid; grid-template-columns: max-content 1fr; gap: .2rem 1rem; }
dt { font-weight: 600; } dd { margin: 0; }
"""


def _e(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>{_e(title)}</title><style>{STYLE}</style></head><body>{body}</body></html>"
    )


def _num(value: object, fmt: str = "{}") -> str:
    return "" if value is None else fmt.format(value)


def render_list(traces: list[dict], query: str, errors_only: bool) -> str:
    rows = []
    for t in traces:
        status = "<span class='err'>error</span>" if t["error"] else "<span class='ok'>ok</span>"
        rows.append(
            "<tr>"
            f"<td class='num'><a href='/trace/{t['id']}'>{t['id']}</a></td>"
            f"<td>{_e(t['ts'][:19].replace('T', ' '))}</td>"
            f"<td>{_e(t['name'])}</td><td>{_e(t['model'])}</td>"
            f"<td class='num'>{_num(t['latency_ms'], '{:.0f}')}</td>"
            f"<td class='num'>{_num(t['input_tokens'])}</td>"
            f"<td class='num'>{_num(t['output_tokens'])}</td>"
            f"<td class='num'>{_num(t['cost_usd'], '{:.6f}')}</td>"
            f"<td>{status}</td>"
            f"<td><a href='/trace/{t['id']}'>{_e(preview(t['prompt'], 90))}</a></td>"
            "</tr>"
        )
    checked = " checked" if errors_only else ""
    table = (
        "<table><tr><th class='num'>ID</th><th>Time (UTC)</th><th>Name</th><th>Model</th>"
        "<th class='num'>ms</th><th class='num'>In</th><th class='num'>Out</th>"
        "<th class='num'>Cost USD</th><th>Status</th><th>Prompt</th></tr>"
        + "".join(rows)
        + "</table>"
        if rows
        else "<p class='muted'>No traces match.</p>"
    )
    body = (
        "<h1>LLM traces</h1>"
        "<form method='get' action='/'>"
        f"<input type='text' name='q' value='{_e(query)}' placeholder='Search prompts, responses and errors'>"
        f"<label><input type='checkbox' name='errors' value='1'{checked}> errors only</label>"
        "<button type='submit'>Search</button></form>"
        f"<p class='muted'>Showing {len(traces)} trace(s), newest first (at most {PAGE_SIZE}).</p>"
        + table
    )
    return _page("LLM traces", body)


def render_trace(t: dict, back: str = "/") -> str:
    status = f"<span class='err'>{_e(t['error'])}</span>" if t["error"] else "<span class='ok'>ok</span>"
    facts = [
        ("Time (UTC)", _e(t["ts"])),
        ("Name", _e(t["name"])),
        ("Model", _e(t["model"])),
        ("Latency", _e(_num(t["latency_ms"], "{:.1f} ms"))),
        ("Tokens", f"{_e(_num(t['input_tokens']))} in / {_e(_num(t['output_tokens']))} out"),
        ("Cost", _e(_num(t["cost_usd"], "${:.6f}"))),
        ("Status", status),
    ]
    body = (
        f"<p><a href='{_e(back)}'>&larr; all traces</a></p><h1>Trace {t['id']}</h1><dl>"
        + "".join(f"<dt>{label}</dt><dd>{value}</dd>" for label, value in facts)
        + f"</dl><h2>Prompt</h2><pre>{_e(t['prompt'])}</pre>"
        + f"<h2>Response</h2><pre>{_e(t['response'])}</pre>"
    )
    return _page(f"Trace {t['id']}", body)


def make_server(db_path: str | None, host: str = "127.0.0.1", port: int = 8321) -> ThreadingHTTPServer:
    """Build the server (not yet serving). ``port=0`` picks a free port."""
    store = TraceStore(db_path)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, page: str) -> None:
            data = page.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 (name required by http.server)
            url = urlparse(self.path)
            params = parse_qs(url.query)
            if url.path == "/":
                query = params.get("q", [""])[0].strip()
                errors_only = params.get("errors", [""])[0] == "1"
                traces = store.list(query=query or None, errors_only=errors_only, limit=PAGE_SIZE)
                self._send(200, render_list(traces, query, errors_only))
                return
            if url.path.startswith("/trace/"):
                tail = url.path[len("/trace/") :]
                trace = store.get(int(tail)) if tail.isdigit() else None
                if trace is not None:
                    self._send(200, render_trace(trace))
                    return
            self._send(404, _page("Not found", "<h1>Not found</h1><p><a href='/'>All traces</a></p>"))

        def log_message(self, format: str, *args: object) -> None:
            pass  # keep the terminal quiet

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def serve(db_path: str | None, host: str = "127.0.0.1", port: int = 8321) -> None:
    server = make_server(db_path, host, port)
    address = f"http://{host}:{server.server_address[1]}/"
    print(f"llm-observe UI on {address} (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()


