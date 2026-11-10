import json
import threading
import urllib.error
import urllib.request

import pytest

from llm_observe import TraceStore
from llm_observe.cli import main
from llm_observe.store import preview
from llm_observe.ui import make_server


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "traces.db"
    store = TraceStore(path)
    store.add(
        ts="2025-03-01T10:00:00.000+00:00", name="chat", model="small-model",
        prompt="How do I get a refund?", response="Within 30 days.",
        latency_ms=120.4, input_tokens=10, output_tokens=5, cost_usd=0.00002,
    )
    store.add(
        ts="2025-03-02T10:00:00.000+00:00", name="chat", model="large-model",
        prompt=json.dumps({"messages": [{"role": "user", "content": "50% off_code <b>bold</b>?"}]}),
        latency_ms=950.0, error="TimeoutError: upstream timed out",
    )
    store.add(
        ts="2025-03-03T10:00:00.000+00:00", name="summarize", model="small-model",
        prompt="Summarise the ticket", response="A refund was requested.",
        latency_ms=80.0, input_tokens=30, output_tokens=8, cost_usd=0.00004,
    )
    store.close()
    return str(path)


def run(capsys, *argv):
    code = main(list(argv))
    return code, capsys.readouterr()


def ids(out):
    return [int(line.split()[0]) for line in out.splitlines()[1:]]


def test_list_is_newest_first_with_a_readable_prompt_column(db, capsys):
    code, captured = run(capsys, "list", "--db", db)
    assert code == 0
    assert ids(captured.out) == [3, 2, 1]
    header, newest, failed, _ = captured.out.splitlines()
    assert header.split()[:3] == ["ID", "TIME", "(UTC)"]
    assert "2025-03-03 10:00:00" in newest and "Summarise the ticket" in newest
    assert "error" in failed and "50% off_code" in failed  # message text, not raw JSON


def test_list_filters(db, capsys):
    assert ids(run(capsys, "list", "--db", db, "--errors")[1].out) == [2]
    assert ids(run(capsys, "list", "--db", db, "--name", "chat")[1].out) == [2, 1]
    assert ids(run(capsys, "list", "--db", db, "--model", "small-model")[1].out) == [3, 1]
    assert ids(run(capsys, "list", "--db", db, "--since", "2025-03-02")[1].out) == [3, 2]
    assert ids(run(capsys, "list", "--db", db, "--limit", "1")[1].out) == [3]


def test_search_matches_prompt_response_and_error_case_insensitively(db, capsys):
    assert ids(run(capsys, "search", "REFUND", "--db", db)[1].out) == [3, 1]
    assert ids(run(capsys, "search", "timed out", "--db", db)[1].out) == [2]
    assert "No traces found." in run(capsys, "search", "nothing-like-this", "--db", db)[1].out


def test_search_treats_like_wildcards_literally(db, capsys):
    assert ids(run(capsys, "search", "50% off_code", "--db", db)[1].out) == [2]
    assert "No traces found." in run(capsys, "search", "5%f", "--db", db)[1].out
    assert "No traces found." in run(capsys, "search", "off code", "--db", db)[1].out


def test_show_prints_the_full_trace(db, capsys):
    code, captured = run(capsys, "show", "1", "--db", db)
    assert code == 0
    assert "tokens:   10 in, 5 out" in captured.out
    assert "cost:     $0.000020" in captured.out
    assert captured.out.rstrip().endswith("Response:\nWithin 30 days.")
    code, captured = run(capsys, "show", "2", "--db", db)
    assert "tokens:   - in, - out" in captured.out
    assert captured.out.rstrip().endswith("Error:\nTimeoutError: upstream timed out")


def test_show_json_and_unknown_id(db, capsys):
    code, captured = run(capsys, "show", "3", "--db", db, "--json")
    assert json.loads(captured.out)["name"] == "summarize"
    code, captured = run(capsys, "show", "99", "--db", db)
    assert code == 1 and "no trace with id 99" in captured.err


def test_list_json(db, capsys):
    rows = json.loads(run(capsys, "list", "--db", db, "--json")[1].out)
    assert [r["id"] for r in rows] == [3, 2, 1]


def test_stats(db, capsys):
    lines = run(capsys, "stats", "--db", db)[1].out.splitlines()
    assert lines[1].split() == ["chat", "2", "1", "535", "950", "10", "5", "0.000020"]
    assert lines[2].split()[:3] == ["summarize", "1", "0"]


def test_database_from_environment_and_missing_database(db, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("LLM_OBSERVE_DB", db)
    assert ids(run(capsys, "list")[1].out) == [3, 2, 1]
    missing = tmp_path / "nope.db"
    code, captured = run(capsys, "list", "--db", str(missing))
    assert code == 2 and "no database" in captured.err
    assert not missing.exists()


def test_preview_picks_the_last_message_text():
    prompt = json.dumps(
        {"model": "m", "messages": [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "Hi\nthere"}]}
    )
    assert preview(prompt) == "Hi there"
    assert preview("plain   text") == "plain text"
    assert preview("{not json") == "{not json"
    assert preview("x" * 100, 10) == "xxxxxxx..."
    assert preview(None) == ""


@pytest.fixture
def server(db):
    srv = make_server(db, port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def fetch(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.status, response.read().decode("utf-8")


def test_ui_lists_and_searches_traces(server):
    status, page = fetch(server + "/")
    assert status == 200
    assert "Summarise the ticket" in page and "How do I get a refund?" in page
    assert "href='/trace/3'" in page

    _, page = fetch(server + "/?q=refund")
    assert "href='/trace/1'" in page and "href='/trace/3'" in page and "href='/trace/2'" not in page
    _, page = fetch(server + "/?errors=1")
    assert "href='/trace/2'" in page and "href='/trace/1'" not in page
    _, page = fetch(server + "/?q=zzz")
    assert "No traces match." in page


def test_ui_trace_page_escapes_stored_text(server):
    status, page = fetch(server + "/trace/2")
    assert status == 200
    assert "TimeoutError: upstream timed out" in page
    assert "&lt;b&gt;bold&lt;/b&gt;" in page
    assert "<b>bold</b>" not in page


def test_ui_escapes_the_search_box_value(server):
    _, page = fetch(server + "/?q=%27%3E%3Cscript%3Ealert(1)%3C/script%3E")
    assert "<script>" not in page


@pytest.mark.parametrize("path", ["/trace/999", "/trace/abc", "/other"])
def test_ui_unknown_pages_are_404(server, path):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        fetch(server + path)
    assert excinfo.value.code == 404
