"""``observe()``: record calls made through a function or a client object."""

from __future__ import annotations

import functools
import inspect
import json
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .store import TraceStore

DEFAULT_MAX_CHARS = 100_000
TOKENS_PER_UNIT = 1_000_000  # prices are USD per million tokens

_INPUT_NAMES = ("input_tokens", "prompt_tokens")
_OUTPUT_NAMES = ("output_tokens", "completion_tokens")
_PLAIN_TYPES = (str, bytes, int, float, bool, type(None), list, tuple, dict, set, frozenset)


class ObserveWarning(UserWarning):
    """A trace could not be recorded. The observed call itself is unaffected."""


def _get(obj: Any, name: str) -> Any:
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def _first(obj: Any, names: tuple[str, ...]) -> Any:
    for name in names:
        value = _get(obj, name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
    return None


def default_usage(response: Any) -> tuple[int | None, int | None]:
    """Find token counts on a response: a ``usage`` key or attribute holding
    ``input_tokens``/``output_tokens`` (or ``prompt_tokens``/``completion_tokens``),
    or those fields on the response itself. Missing numbers come back as None."""
    if response is None or isinstance(response, (str, bytes, int, float)):
        return None, None
    holder = _get(response, "usage")
    if holder is None:
        holder = response
    return _first(holder, _INPUT_NAMES), _first(holder, _OUTPUT_NAMES)


def _as_pair(found: Any) -> tuple[int | None, int | None]:
    """Normalise what a usage function returned: a pair, or a mapping/object
    with the usual field names."""
    if isinstance(found, (tuple, list)) and len(found) == 2:
        return found[0], found[1]
    if found is None:
        return None, None
    return _first(found, _INPUT_NAMES), _first(found, _OUTPUT_NAMES)


def to_text(value: Any) -> str:
    """Strings are stored as they are; everything else as JSON, falling back
    to ``repr`` for objects JSON cannot represent."""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=_jsonable, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return repr(value)


def _jsonable(obj: Any) -> Any:
    if hasattr(obj, "__dict__") and vars(obj):
        return {k: v for k, v in vars(obj).items() if not k.startswith("_")}
    return repr(obj)


def prompt_text(args: tuple, kwargs: dict) -> str:
    """A lone string argument is stored verbatim, keyword-only calls as a JSON
    object of the keywords, anything else as ``{"args": [...], "kwargs": {...}}``."""
    if len(args) == 1 and not kwargs and isinstance(args[0], str):
        return args[0]
    if not args:
        return to_text(kwargs)
    payload: dict[str, Any] = {"args": list(args)}
    if kwargs:
        payload["kwargs"] = kwargs
    return to_text(payload)


class Recorder:
    """Holds the settings of one ``observe()`` call and writes traces."""

    def __init__(
        self,
        *,
        db_path: str | Path | None = None,
        model: str | None = None,
        prices: Mapping[str, Mapping[str, float]] | None = None,
        usage: Callable[[Any], Any] | None = None,
        response_text: Callable[[Any], str] | None = None,
        redact: Callable[[str], str] | None = None,
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> None:
        self.db_path = db_path
        self.model = model
        self.prices = dict(prices or {})
        self.usage = usage or default_usage
        self.response_text = response_text or to_text
        self.redact = redact
        self.max_chars = max_chars
        self._store: TraceStore | None = None

    @property
    def store(self) -> TraceStore:
        # Opened on first use so that decorating a function at import time
        # does not create a database file.
        if self._store is None:
            self._store = TraceStore(self.db_path)
        return self._store

    def _clean(self, text: str) -> str:
        if self.redact is not None:
            text = self.redact(text)
        if len(text) > self.max_chars:
            dropped = len(text) - self.max_chars
            text = f"{text[: self.max_chars]}... [truncated {dropped} characters]"
        return text

    def _cost(self, model: str | None, tokens_in: int | None, tokens_out: int | None) -> float | None:
        price = self.prices.get(model) if model else None
        if price is None or (tokens_in is None and tokens_out is None):
            return None
        return (
            (tokens_in or 0) * float(price.get("input", 0))
            + (tokens_out or 0) * float(price.get("output", 0))
        ) / TOKENS_PER_UNIT

    def record(
        self,
        name: str,
        started_at: datetime,
        seconds: float,
        args: tuple,
        kwargs: dict,
        result: Any = None,
        error: BaseException | None = None,
    ) -> None:
        """Write one trace. Never raises: tracing must not break the call."""
        try:
            model = self.model or kwargs.get("model")
            tokens_in = tokens_out = None
            response = None
            if error is None:
                tokens_in, tokens_out = _as_pair(self.usage(result))
                model = model or _get(result, "model")
                response = self._clean(self.response_text(result))
            model = str(model) if model is not None else None
            self.store.add(
                ts=started_at.isoformat(timespec="milliseconds"),
                name=name,
                model=model,
                prompt=self._clean(prompt_text(args, kwargs)),
                response=response,
                latency_ms=round(seconds * 1000, 2),
                input_tokens=tokens_in,
                output_tokens=tokens_out,
                cost_usd=self._cost(model, tokens_in, tokens_out),
                error=None if error is None else self._clean(f"{type(error).__name__}: {error}"),
            )
        except Exception as exc:
            warnings.warn(f"llm-observe: could not record trace for {name}: {exc!r}", ObserveWarning)

    def wrap(self, fn: Callable[..., Any], name: str) -> Callable[..., Any]:
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def observed_async(*args: Any, **kwargs: Any) -> Any:
                started_at, start = datetime.now(timezone.utc), time.perf_counter()
                try:
                    result = await fn(*args, **kwargs)
                except Exception as exc:
                    self.record(name, started_at, time.perf_counter() - start, args, kwargs, error=exc)
                    raise
                self.record(name, started_at, time.perf_counter() - start, args, kwargs, result)
                return result

            return observed_async

        @functools.wraps(fn)
        def observed(*args: Any, **kwargs: Any) -> Any:
            started_at, start = datetime.now(timezone.utc), time.perf_counter()
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:
                self.record(name, started_at, time.perf_counter() - start, args, kwargs, error=exc)
                raise
            self.record(name, started_at, time.perf_counter() - start, args, kwargs, result)
            return result

        return observed


class ObservedClient:
    """Stands in for a client object. Method calls, at any depth
    (``client.chat.create(...)``), are recorded; everything else passes
    through to the real object."""

    __slots__ = ("_target", "_recorder", "_name")

    def __init__(self, target: Any, recorder: Recorder, name: str) -> None:
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_recorder", recorder)
        object.__setattr__(self, "_name", name)

    def __getattr__(self, attr: str) -> Any:
        value = getattr(self._target, attr)
        if attr.startswith("_"):
            return value
        name = f"{self._name}.{attr}"
        if inspect.isroutine(value):
            return self._recorder.wrap(value, name)
        if isinstance(value, _PLAIN_TYPES) or inspect.isclass(value) or inspect.ismodule(value):
            return value
        return ObservedClient(value, self._recorder, name)

    def __setattr__(self, attr: str, value: Any) -> None:
        setattr(self._target, attr, value)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._recorder.wrap(self._target, self._name)(*args, **kwargs)

    def __repr__(self) -> str:
        return f"<observed {self._target!r}>"


def observe(
    target: Any = None,
    *,
    name: str | None = None,
    db_path: str | Path | None = None,
    model: str | None = None,
    prices: Mapping[str, Mapping[str, float]] | None = None,
    usage: Callable[[Any], Any] | None = None,
    response_text: Callable[[Any], str] | None = None,
    redact: Callable[[str], str] | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> Any:
    """Record every call made through ``target`` to a local SQLite file.

    ``target`` is a function (sync or async) or a client object. A function
    comes back wrapped; a client comes back as a proxy whose method calls are
    recorded. Also works as a decorator, bare (``@observe``) or with options
    (``@observe(name="summarize")``).

    Options:
      name           label stored with each trace (default: the function's
                     qualified name or the client's class name)
      db_path        SQLite file (default: ``$LLM_OBSERVE_DB`` or ``llm_observe.db``)
      model          model name to store; otherwise the ``model=`` keyword of
                     the call or a ``model`` field on the response is used
      prices         ``{model: {"input": usd, "output": usd}}`` per million
                     tokens; without a matching entry the cost is left empty
      usage          function ``response -> (input_tokens, output_tokens)``
                     replacing the default lookup
      response_text  function ``response -> str`` choosing what is stored as
                     the response
      redact         function ``str -> str`` applied to prompt, response and
                     error text before storing
      max_chars      longer texts are truncated (default 100,000)
    """
    recorder = Recorder(
        db_path=db_path,
        model=model,
        prices=prices,
        usage=usage,
        response_text=response_text,
        redact=redact,
        max_chars=max_chars,
    )

    def apply(obj: Any) -> Any:
        if inspect.isroutine(obj) or isinstance(obj, functools.partial):
            fallback = getattr(obj, "__qualname__", None) or getattr(obj, "__name__", "call")
            return recorder.wrap(obj, name or fallback)
        return ObservedClient(obj, recorder, name or type(obj).__name__)

    return apply if target is None else apply(target)
