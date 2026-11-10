# llm-observe

> Self-hosted, minimal LLM tracing: one-line SDK wrapper, SQLite storage and a local web UI for prompts, latency, cost and errors.

**Status:** 🚧 Early stage — design and roadmap published, implementation in progress. Stars, issues and ideas welcome.

## Why

Heavy SaaS observability tools are overkill for small teams that just want to see what their LLM calls did.

## Planned features

- [ ] One-line wrapper for popular LLM SDKs
- [ ] Captures prompts, responses, latency, tokens, cost, errors
- [ ] SQLite by default, no external services
- [ ] Local web UI to search and inspect traces
- [ ] Export to OpenTelemetry

## Intended usage

```
client = observe(llm_client)
llm-observe ui
```

_The interface above is the target design and may change._

## Roadmap

1. Core implementation of the first feature set
2. Examples and tests
3. CI and first tagged release
4. Docs and integrations

## Contributing

Contributions are welcome. Open an issue to discuss an idea or pick an unchecked item from the feature list.

## License

[MIT](LICENSE)
