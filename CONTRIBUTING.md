# Contributing to Copy-Right

Thanks for helping out! Read [docs/PLAN.md](docs/PLAN.md) first. It is the source of truth for
the architecture, capability interfaces, storyboard schema, and milestone order.

## Ground rules

- **Business code never calls a provider SDK.** Everything goes through the adapters in
  `app/capabilities/base.py`.
- **No model IDs or endpoints in code.** They live only in `app/providers/registry.yaml`.
  Before writing an adapter, check the provider's current official docs.
- **Every capability needs a working mock**, so the app runs with no API keys.
- **Never log or return full secrets.** Use `app.core.secrets.mask_secret`.
- `ContentRejected` (provider moderation) stays its own error type.

## Adding a provider

1. Create `app/providers/<provider_id>/adapter.py` with an `Adapter` subclass.
2. Implement `submit`, `poll`, `fetch_result`, and a `test_connection` that never triggers
   billable generation.
3. Fill in the provider's `adapter`, `models`, and `credentials` in `registry.yaml`.
4. Add tests that mock the HTTP layer. CI must never call a real provider.

## Development

```bash
uv sync
uv run pytest
uv run ruff check app tests && uv run ruff format --check app tests
cd web && bun install && bun run build
```

Use [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, ...).
