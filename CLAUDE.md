# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv run homunculus chat              # CLI REPL (needs [owner] + [anthropic] config)
uv run homunculus serve             # HTTP server (needs [twilio] + [google_calendar])
uv run pytest tests/ -v             # Run all tests (136 tests)
uv run pytest tests/test_agent/ -v  # Run a test directory
uv run pytest tests/test_agent/test_tools.py::test_name -v  # Run a single test
uv run ruff check src/ tests/       # Lint
uv run ruff format src/ tests/      # Format
```

## Architecture

Self-hosted AI agent for scheduling/coordination via SMS. Messages flow: inbound (Twilio webhook or CLI) → `MessageRouter` (authorization, approval handling) → `process_message` agent loop (Claude API with tools, max 10 turns) → outbound response via channel.

**Key modules:**
- `agent/loop.py` — Agentic loop: conversation history, Claude API calls, tool execution, approval gating
- `agent/prompt.py` — System prompt builder with owner/contact context and privacy rules
- `agent/tools/` — Tool definitions. Each module exports `make_*_tools(...)` → `list[ToolDef]`. Tools with `requires_approval=True` are system-enforced (agent can't bypass)
- `channels/router.py` — `MessageRouter`: routes inbound messages, handles owner approval responses, rejects unknown senders
- `channels/base.py` — `Channel` ABC; `twilio_sms.py` implements it (uses `asyncio.to_thread` for blocking Twilio SDK)
- `storage/store.py` — All SQLite operations (conversations, contacts, approvals, audit log). Auto-migrates on startup from `migrations/`
- `app.py` — aiohttp app factory, wires everything together
- `types.py` — Domain types: `NewType` IDs (`ApprovalId`, `ChannelId`, `ContactId`, `ConversationId`, `MessageId`), `Message` dataclass, `StrEnum` for statuses

**Config:** `config/config.toml` (TOML via `tomllib`). Only `[owner]` + `[anthropic]` required; `[twilio]`, `[google_calendar]`, `[google_maps]` are optional (`None` by default). Secrets from env vars (`ANTHROPIC_API_KEY`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`).

## Deployment

Runs on a GCE e2-micro (Debian 12) at `homunculus.ziyadedher.com`. Caddy handles automatic HTTPS via Let's Encrypt and reverse-proxies to the app on port 8080.

```bash
docker compose up -d              # Production (homunculus + caddy)
```

- `config/Caddyfile` — Caddy reverse proxy config
- `GET /health` — Health check endpoint (returns `{"status": "ok"}`)
- Twilio webhook: `https://homunculus.ziyadedher.com/webhook/sms`
- Secrets via env vars on the VM, config via `config/config.toml` (not in git)
- Use `gcloud compute ssh homunculus --zone=us-west1-b --command="..."` to debug the VM (check logs, restart containers, etc.)

## Git Conventions

Use semantic commit messages: `feat:`, `fix:`, `refactor:`, `docs:`, `chore:`, `test:`, `ci:`, etc.

## Code Style

- Python 3.14, async-first, functional style (functions + frozen dataclasses over class hierarchies)
- `PLC0415`: all imports at top-level, no inline imports
- `TID251`: `from __future__ import annotations` is banned (not needed on 3.14)
- `ANN401`: no `typing.Any` in function signatures
- structlog: use `BoundLogger` (sync) — call `log.info()` without `await`
- Line length: 100
