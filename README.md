# llm-observe

Self-hosted, minimal LLM tracing: one-line SDK wrapper, SQLite storage and a local web UI for prompts, latency, cost and errors.

> **Project status: planning. There is no working code in this repository yet.**
> This README describes what the project will do. Nothing below can be installed or run today.
> Features are ticked off in the checklist as they are built.

## What it is

A Python library with a local web UI.

**The problem it solves:** Heavy SaaS observability tools are overkill for small teams that just want to see what their LLM calls did.

## Who it is for

Small teams who want to see what their LLM calls did without adopting a hosted observability product.

## How it will work

1. You wrap your model client with one line.
2. Every call's prompt, response, latency, token count, cost and error is saved to a local SQLite file.
3. You open the local web page to search and inspect the calls.

## Planned features

Unticked items are not built yet.

- [ ] One-line wrapper for popular LLM SDKs
- [ ] Captures prompts, responses, latency, tokens, cost, errors
- [ ] SQLite by default, no external services
- [ ] Local web UI to search and inspect traces
- [ ] Export to OpenTelemetry

## Planned usage

This is the intended interface, shown so you can judge the design. **It does not work yet**, and names may change before the first release.

```python
client = observe(llm_client)  # in your code
# then, in a terminal:  llm-observe ui
```

## Contributing

Feedback on the design is the most useful contribution right now. Open an issue to suggest a feature, question an approach or describe your use case. Code contributions are welcome once the first version lands.

## License

[MIT](LICENSE)
