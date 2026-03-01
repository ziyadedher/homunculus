# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv run homunculus chat <id>         # CLI → server API (needs [owner] + [anthropic] + [google_calendar])
uv run homunculus chat <id> --server http://host:port  # Explicit server URL
uv run homunculus serve             # HTTP server (needs [telegram] + [google_calendar])
uv run pytest tests/ -v             # Run all tests (151 tests)
uv run pytest tests/test_agent/ -v  # Run a test directory
uv run pytest tests/test_agent/test_tools.py::test_name -v  # Run a single test
uv run ruff check src/ tests/       # Lint
uv run ruff format src/ tests/      # Format
```

## Architecture

Self-hosted AI agent for scheduling/coordination via Telegram. Messages flow: inbound (Telegram webhook or CLI API) → `MessageRouter` or API handler → `process_message` agent loop (Claude API with tools, max 10 turns) → outbound response via channel or API response. The CLI is a thin HTTP client that POSTs to the server's `/api/message` endpoint and polls `/api/approvals/{id}` for escalation results.

**Key modules:**
- `agent/loop.py` — Agentic loop: conversation history, Claude API calls, tool execution, approval gating
- `agent/prompt.py` — System prompt builder with owner/contact context and privacy rules
- `agent/tools/` — Tool definitions. Each module exports `make_*_tools(...)` → `list[ToolDef]`. Tools with `requires_approval=True` are system-enforced (agent can't bypass)
- `channels/router.py` — `MessageRouter`: routes inbound messages, handles owner approval responses, rejects unknown senders
- `channels/base.py` — `Channel` ABC; `telegram.py` implements it (fully async via aiohttp)
- `storage/store.py` — All SQLite operations (conversations, contacts, approvals, audit log). Auto-migrates on startup from `migrations/`
- `app.py` — aiohttp app factory, wires everything together. Includes `POST /api/message` and `GET /api/approvals/{id}` endpoints (Google OAuth auth)
- `types.py` — Domain types: `NewType` IDs (`ApprovalId`, `ChannelId`, `ContactId`, `ConversationId`, `MessageId`), `Message` dataclass, `StrEnum` for statuses

**Config:** `config/config.toml` (TOML via `tomllib`). Only `[owner]` (name, email, timezone, telegram_chat_id) + `[anthropic]` required; `[telegram]`, `[google_calendar]`, `[google_maps]` are optional (`None` by default). Secrets from env vars (`ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`). CLI chat requires `[google_calendar]` for OAuth auth.

## Deployment

Runs on a GCE e2-micro (Debian 12) at `homunculus.ziyadedher.com`. Caddy handles automatic HTTPS via Let's Encrypt and reverse-proxies to the app on port 8080.

```bash
docker compose up -d              # Production (homunculus + caddy)
```

- `config/Caddyfile` — Caddy reverse proxy config
- `GET /health` — Health check endpoint (returns `{"status": "ok"}`)
- `POST /api/message` — CLI API endpoint (Google OAuth Bearer token auth, owner email must match config)
- `GET /api/approvals/{id}` — Poll approval status and response text
- Telegram webhook: `https://homunculus.ziyadedher.com/webhook/telegram` (auto-registered on startup)
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
