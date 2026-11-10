# llm-observe

See what your LLM calls did. Wrap a client or a function with `observe()` and every call's prompt, response, latency, token counts, cost and error is saved to a local SQLite file that you can list, search and inspect from the terminal or a local web page.

**Status: early but usable (v0.1.0).** The `observe()` wrapper (functions, client objects, sync and async), SQLite storage, the `list` / `search` / `show` / `stats` commands and a read-only local web page work today. OpenTelemetry export and streaming support are not built.

No dependencies beyond the Python standard library, no account, no network. It does not import or know about any model SDK: it records whatever passes through the object you hand it.

## Install

Python 3.10 or newer.

```bash
pip install git+https://github.com/narayana006/llm-observe
```

## Try it offline

The repository includes a demo with a fake client, so you can see the whole loop without a model:

```bash
git clone https://github.com/narayana006/llm-observe
cd llm-observe
pip install -e .
python examples/demo.py
llm-observe list --db examples/demo.db
```

```text
ID  TIME (UTC)           NAME                  MODEL        MS   IN  OUT  COST USD  STATUS  PROMPT
 4  2026-11-10 05:17:11  summarize             small-model  12   40    7  0.000030  ok      The customer reports that the parcel arrived ...
 3  2026-11-10 05:17:11  my_model.chat.create  small-model  27    -    -         -  error   Please crash now
 2  2026-11-10 05:17:11  my_model.chat.create  small-model  26   96  126  0.000237  ok      What are your opening hours on public holidays?
 1  2026-11-10 05:17:11  my_model.chat.create  small-model  27  108  135  0.000257  ok      How do I request a refund for order A-1042?
```

Times and latencies will be your own. Open the failed call:

```bash
llm-observe show 3 --db examples/demo.db
```

```text
Trace 3
  time:     2026-11-10T05:17:11.091+00:00
  name:     my_model.chat.create
  model:    small-model
  latency:  27.3 ms
  tokens:   - in, - out
  cost:     -
  status:   error

Prompt:
{
  "model": "small-model",
  "messages": [
    {
      "role": "user",
      "content": "Please crash now"
    }
  ]
}

Error:
TimeoutError: upstream timed out after 30s
```

Or browse the same data at http://127.0.0.1:8321/ with `llm-observe ui --db examples/demo.db`.

## Recording calls

### A client object

```python
from llm_observe import observe

client = observe(llm_client)

# use it exactly as before
client.chat.create(model="small-model", messages=[{"role": "user", "content": "Hello"}])
```

`observe()` returns a stand-in for your client. Method calls at any depth are recorded under a name such as `MyClient.chat.create`; attributes that are not methods pass straight through. The return values and exceptions your code sees are unchanged.

### A function

```python
from llm_observe import observe

@observe
def llm_call(prompt):
    ...

@observe(name="summarize", model="small-model")
async def summarize(text):
    ...

wrapped = observe(some_existing_function)   # without decorator syntax
```

### What gets stored

| Field | Source |
| --- | --- |
| prompt | The call's arguments. A single string is stored as it is; keyword arguments are stored as JSON. |
| response | The return value: strings as they are, everything else as JSON (objects by their public attributes). Empty when the call raised. |
| latency | Wall-clock time of the call in milliseconds. |
| tokens | Read from the response: a `usage` key or attribute holding `input_tokens`/`output_tokens` or `prompt_tokens`/`completion_tokens`. Empty if the response has neither. |
| model | The `model=` option of `observe()`, else the call's `model=` keyword, else a `model` field on the response. |
| cost | Tokens times your price table. Empty unless you pass `prices` and the model is in it. |
| error | `ExceptionType: message` when the call raised. The exception is re-raised unchanged. |

### Options

```python
client = observe(
    llm_client,
    name="support_bot",                       # label prefix instead of the class name
    db_path="traces/llm.db",                  # default: $LLM_OBSERVE_DB, else ./llm_observe.db
    model="small-model",                      # fixed model name for every trace
    prices={"small-model": {"input": 0.50, "output": 1.50}},   # USD per 1M tokens, your numbers
    usage=lambda resp: (resp["tokens_in"], resp["tokens_out"]),  # where your responses keep token counts
    response_text=lambda resp: resp["text"],  # store only the text instead of the whole response
    redact=lambda text: text.replace(API_TOKEN, "[token]"),       # applied to prompt, response and error
    max_chars=20_000,                         # truncate longer texts (default 100,000)
)
```

All options are keyword-only and optional (`API_TOKEN` above stands for whatever string you want kept out of the file). No prices are built in.

If recording a trace fails (unwritable file, a bug in your `usage` function), the wrapped call still returns normally and an `ObserveWarning` is emitted.

## Inspecting traces

Every command takes `--db FILE`; without it, `$LLM_OBSERVE_DB` or `./llm_observe.db` is used.

```text
llm-observe list   [--limit N] [--errors] [--name NAME] [--model MODEL] [--since DATE] [--json]
llm-observe search TEXT [same filters as list]
llm-observe show   ID [--json]
llm-observe stats
llm-observe ui     [--host 127.0.0.1] [--port 8321]
```

`search` is a case-insensitive substring match over prompt, response and error text:

```bash
llm-observe search refund --db examples/demo.db
```

```text
ID  TIME (UTC)           NAME                  MODEL        MS   IN  OUT  COST USD  STATUS  PROMPT
 4  2026-11-10 05:17:11  summarize             small-model  12   40    7  0.000030  ok      The customer reports that the parcel arrived ...
 1  2026-11-10 05:17:11  my_model.chat.create  small-model  27  108  135  0.000257  ok      How do I request a refund for order A-1042?
```

`stats` totals calls, errors, latency, tokens and cost per name:

```bash
llm-observe stats --db examples/demo.db
```

```text
NAME                  CALLS  ERRORS  AVG MS  MAX MS  IN TOK  OUT TOK  COST USD
my_model.chat.create      3       1      27      27     204      261  0.000494
summarize                 1       0      12      12      40        7  0.000030
```

For structured prompts the `PROMPT` column shows the last text found under a key such as `content` or `prompt`, which is usually the latest user message; `show` prints everything.

### Web page

`llm-observe ui` serves a read-only page: a table of the latest 100 traces with a search box and an errors-only filter, and a detail page per trace. It is plain HTML from Python's built-in HTTP server, bound to `127.0.0.1` by default, with no JavaScript and no authentication. Do not bind it to a public address.

The data is a single table, `traces`, in an ordinary SQLite file, so `sqlite3 llm_observe.db` works too.

## Feature checklist

- [x] One-line wrapper for any client object or function (sync and async)
- [x] Captures prompts, responses, latency, tokens, cost, errors
- [x] SQLite by default, no external services
- [x] CLI to list, search, show and summarise traces
- [x] Local web page to search and inspect traces (read-only, basic)
- [x] Redaction hook and length cap for stored text
- [ ] Streaming responses (generators are recorded as their `repr`, not their content)
- [ ] Grouping related calls into one trace with spans
- [ ] Retention / pruning command
- [ ] Export to OpenTelemetry

## Limitations

- **Prompts and responses are stored in plain text** in the SQLite file. Treat that file like your logs: keep it out of version control, and use `redact=` if prompts can contain secrets or personal data.
- Streaming calls are not captured meaningfully. If a method returns a generator or stream object, the trace holds its `repr` and the latency of creating it, not the streamed text or token counts.
- Token counts are only what the response reports; nothing is tokenised locally. Cost uses one input and one output rate per model.
- Each call is a separate row. There is no parent/child relation between calls, no sessions and no sampling.
- The file grows without bound. Delete it or prune rows with SQL when it gets large.
- Writing a trace adds a small synchronous SQLite insert after each call, including for async functions.
- The client proxy forwards attribute access but is not an instance of the original class, so `isinstance(client, MyClient)` is false on the wrapped object.

## Contributing

Issues and pull requests are welcome. To run the tests:

```bash
pip install -e ".[dev]"
pytest
```

Built and maintained by [Narayana Vemburaj](https://github.com/narayana006).

## License

[MIT](LICENSE)
