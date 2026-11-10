import asyncio
import functools
import warnings

import pytest

from llm_observe import ObserveWarning, TraceStore, observe

PRICES = {"small-model": {"input": 1.0, "output": 2.0}}


@pytest.fixture
def db(tmp_path):
    return tmp_path / "traces.db"


def traces(db):
    store = TraceStore(db)
    try:
        return store.list(limit=None)[::-1]  # oldest first
    finally:
        store.close()


def my_model(prompt, **options):
    return {
        "text": prompt.upper(),
        "model": "small-model",
        "usage": {"input_tokens": 1_000_000, "output_tokens": 500_000},
    }


class Chat:
    def create(self, model, messages):
        return {"text": "hi", "usage": {"prompt_tokens": 7, "completion_tokens": 3}}

    async def acreate(self, model, messages):
        return {"text": "hi async"}


class Client:
    api_base = "http://localhost"

    def __init__(self):
        self.chat = Chat()
        self.timeout = 30

    def complete(self, prompt):
        if prompt == "boom":
            raise ConnectionError("connection reset")
        return f"echo: {prompt}"


def test_function_wrapper_records_prompt_response_tokens_cost_and_latency(db):
    wrapped = observe(my_model, db_path=db, prices=PRICES)
    assert wrapped("hello")["text"] == "HELLO"
    (t,) = traces(db)
    assert t["name"] == "my_model"
    assert t["prompt"] == "hello"
    assert '"text": "HELLO"' in t["response"]
    assert t["model"] == "small-model"  # taken from the response
    assert (t["input_tokens"], t["output_tokens"]) == (1_000_000, 500_000)
    assert t["cost_usd"] == pytest.approx(2.0)
    assert t["latency_ms"] >= 0
    assert t["error"] is None
    assert t["ts"].endswith("+00:00")


def test_bare_decorator_and_decorator_with_options(db, monkeypatch):
    monkeypatch.setenv("LLM_OBSERVE_DB", str(db))

    @observe
    def plain(prompt):
        return "a"

    @observe(name="labelled", model="small-model")
    def configured(prompt):
        return "b"

    assert plain("x") == "a" and configured("y") == "b"
    assert plain.__name__ == "plain"
    first, second = traces(db)
    assert first["name"].endswith("plain")
    assert (second["name"], second["model"]) == ("labelled", "small-model")


def test_decorating_does_not_create_the_database_until_a_call_happens(db):
    observe(my_model, db_path=db)
    assert not db.exists()


def test_exceptions_are_recorded_and_re_raised(db):
    client = observe(Client(), db_path=db)
    with pytest.raises(ConnectionError, match="connection reset"):
        client.complete("boom")
    (t,) = traces(db)
    assert t["error"] == "ConnectionError: connection reset"
    assert t["response"] is None
    assert t["prompt"] == "boom"


def test_client_proxy_records_nested_methods_and_passes_attributes_through(db):
    client = observe(Client(), db_path=db, prices=PRICES)
    assert client.timeout == 30
    assert client.api_base == "http://localhost"
    client.timeout = 5
    assert client.timeout == 5
    assert client.complete("ping") == "echo: ping"
    client.chat.create(model="small-model", messages=[{"role": "user", "content": "hello there"}])
    first, second = traces(db)
    assert first["name"] == "Client.complete"
    assert first["input_tokens"] is None and first["cost_usd"] is None
    assert second["name"] == "Client.chat.create"
    assert second["model"] == "small-model"  # taken from the model= keyword
    assert (second["input_tokens"], second["output_tokens"]) == (7, 3)
    assert second["cost_usd"] == pytest.approx((7 * 1.0 + 3 * 2.0) / 1_000_000)
    assert '"content": "hello there"' in second["prompt"]


def test_client_name_option(db):
    client = observe(Client(), name="support_bot", db_path=db)
    client.complete("x")
    assert traces(db)[0]["name"] == "support_bot.complete"


def test_callable_objects_and_partials(db):
    class CallableModel:
        def __call__(self, prompt):
            return "called " + prompt

    assert observe(CallableModel(), db_path=db)("a") == "called a"
    assert observe(functools.partial(my_model, temperature=0), name="partial", db_path=db)("b")
    first, second = traces(db)
    assert first["name"] == "CallableModel"
    assert second["name"] == "partial"


def test_async_functions_and_async_client_methods(db):
    @observe(db_path=db, name="async_model")
    async def async_model(prompt):
        await asyncio.sleep(0)
        if prompt == "bad":
            raise ValueError("nope")
        return "async " + prompt

    async def scenario():
        assert await async_model("x") == "async x"
        with pytest.raises(ValueError):
            await async_model("bad")
        client = observe(Client(), db_path=db)
        assert (await client.chat.acreate(model="m", messages=[]))["text"] == "hi async"

    asyncio.run(scenario())
    first, second, third = traces(db)
    assert first["response"] == "async x"
    assert second["error"] == "ValueError: nope"
    assert third["name"] == "Client.chat.acreate"


def test_custom_usage_and_response_text_extractors(db):
    wrapped = observe(
        lambda prompt: ("the answer", 11, 22),
        name="tuple_model",
        model="small-model",
        db_path=db,
        usage=lambda result: {"input_tokens": result[1], "output_tokens": result[2]},
        response_text=lambda result: result[0],
    )
    wrapped("q")
    (t,) = traces(db)
    assert t["response"] == "the answer"
    assert (t["input_tokens"], t["output_tokens"]) == (11, 22)


def test_redact_and_truncate(db):
    wrapped = observe(
        lambda prompt: "reply to " + prompt,
        name="m",
        db_path=db,
        redact=lambda text: text.replace("secret-123", "[redacted]"),
        max_chars=30,
    )
    wrapped("my key is secret-123 " + "x" * 100)
    (t,) = traces(db)
    assert "secret-123" not in t["prompt"] and "secret-123" not in t["response"]
    assert t["prompt"].startswith("my key is [redacted]")
    assert "[truncated" in t["prompt"]


def test_objects_that_are_not_json_serialisable_are_still_recorded(db):
    class Response:
        def __init__(self):
            self.text = "from object"
            self.usage = type("U", (), {"input_tokens": 4, "output_tokens": 5})()

    observe(lambda prompt, handle: Response(), name="m", db_path=db)("q", handle=object())
    (t,) = traces(db)
    assert "from object" in t["response"]
    assert (t["input_tokens"], t["output_tokens"]) == (4, 5)
    assert "<object object" in t["prompt"]


def test_a_recording_failure_warns_but_never_breaks_the_call(db):
    def broken_usage(result):
        raise RuntimeError("extractor bug")

    wrapped = observe(lambda prompt: "fine", name="m", db_path=db, usage=broken_usage)
    with pytest.warns(ObserveWarning, match="extractor bug"):
        assert wrapped("q") == "fine"


def test_no_warnings_in_normal_use(db):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        observe(my_model, db_path=db)("hello")


def test_database_directory_is_created_on_first_call(tmp_path):
    nested = tmp_path / "traces" / "deep" / "llm.db"
    observe(my_model, db_path=nested)("hello")
    assert nested.exists()
