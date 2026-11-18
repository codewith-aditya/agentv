# Contributing to AgentV

Thanks for taking the time to contribute.

## Development setup

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Docker must be available for the sandbox and MCP tests.

## Before you open a PR

```bash
.venv/bin/python scripts/selftest.py          # full suite (Docker + real LLM)
.venv/bin/python scripts/selftest.py --skip-docker --skip-llm   # fast, no network
```

- Keep the single-message streaming behaviour intact (no delete + resend).
- New tools go in `agentv/tools/<module>.py`, get registered in
  `agentv/tools/__init__.py`, and must have a `min_role` and English description.
- Never commit `.env`, `data/`, sessions, tokens or API keys.
- Add a selftest check for any new tool or DB path.

## Commit style

`type(scope): short description` — e.g. `feat(tools): add calendar tool`.
