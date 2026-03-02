# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv run homunculus chat <id>         # CLI → server API (needs [owner] + [anthropic] + [google])
uv run homunculus chat <id> --server http://host:port  # Explicit server URL
uv run homunculus serve             # HTTP server (needs [telegram] + [google] + [google.calendar])
uv run pytest tests/ -v             # Run all tests (200 tests)
uv run pytest tests/test_agent/ -v  # Run a test directory
uv run pytest tests/test_agent/test_tools.py::test_name -v  # Run a single test
uv run ruff check src/ tests/       # Lint
uv run ruff format src/ tests/      # Format
```

## Architecture

Self-hosted AI agent for scheduling/coordination via Telegram. Messages flow: inbound (Telegram webhook or CLI API) → `MessageRouter` or API handler → `process_message` agent loop (Claude API with tools, max 10 turns) → outbound response via channel or API response. The CLI is a thin HTTP client that POSTs to the server's `/api/message` endpoint and polls `/api/approvals/{id}` for escalation results.

**Key modules:**
- `agent/loop.py` — Agentic loop: conversation history, Claude API calls, tool execution, approval gating
- `agent/prompt.py` — System prompt builder with owner/contact context, privacy rules, and pending approval awareness
- `agent/tools/` — Tool definitions. Each module exports `make_*_tools(...)` → `list[ToolDef]`. Tools with `requires_approval=True` are system-enforced (agent can't bypass)
- `channels/router.py` — `MessageRouter`: routes inbound messages, handles owner approval responses (text + inline buttons), rejects unknown senders
- `channels/base.py` — `Channel` ABC; `telegram.py` implements it (fully async via httpx)
- `storage/store.py` — All SQLite operations (conversations, contacts, approvals, audit log). Auto-migrates on startup from `migrations/`
- `server/app.py` — FastAPI app factory with async lifespan, wires routes/DB/channel/router/reaper/webhook registration
- `server/dependencies.py` — Typed `AppState` frozen dataclass, FastAPI dependency functions (`get_state`, `get_current_user`, `require_owner`)
- `server/auth.py` — Google OAuth routes (`APIRouter(prefix="/auth")`), credential loading, service tool hot-reload
- `server/handlers.py` — API routes (`/api/message`, `/api/requests/{id}`, `/api/reset`) + Telegram webhook handler with inline button callback support
- `types.py` — Domain types: `NewType` IDs (`ApprovalId`, `ChannelId`, `ContactId`, `ConversationId`, `MessageId`), `Message` dataclass, `StrEnum` for statuses

**Config:** Split into `config/config.server.toml` (server/admin, not in git) and `config/config.client.toml` (CLI client, tracked). Server config requires `[owner]` (name, email, timezone, telegram_chat_id) + `[anthropic]`; `[telegram]`, `[google]` are optional (`None` by default). Google config uses nested sections: `[google]` (credentials_path, token_path), `[google.calendar]` (calendar_id), `[google.maps]` (env var activated). Secrets from env vars (`ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`). Client config has `[client]` with `server_url` and `credentials_path`.

## Deployment

Runs on a GCE e2-micro (Debian 12) at `homunculus.ziyadedher.com`. Caddy handles automatic HTTPS via Let's Encrypt and reverse-proxies to the app on port 8080.

```bash
docker compose up -d              # Production (homunculus + caddy)
```

- `config/Caddyfile` — Caddy reverse proxy config
- `GET /health` — Health check endpoint (returns `{"status": "ok"}`)
- `GET /docs` — Auto-generated OpenAPI docs (FastAPI)
- `POST /api/message` — CLI API endpoint (Google OAuth Bearer token auth, owner-only AuthZ)
- `GET /api/approvals/{id}` — Poll approval status and response text (owner-only)
- Telegram webhook: `https://homunculus.ziyadedher.com/webhook/telegram` (auto-registered on startup, supports inline button callbacks)
- Secrets via env vars on the VM, config via `config/config.server.toml` (not in git)
- Use `gcloud compute ssh homunculus --zone=us-west1-b --command="..."` to debug the VM (check logs, restart containers, etc.)

## Git Conventions

- **Small, self-contained commits.** Each commit should do one thing — a single bug fix, a single refactor, a single feature. If a change can be split into independent steps (e.g. "move files" then "add new feature"), make separate commits. Don't bundle unrelated changes.
- Semantic commit messages: `feat:`, `fix:`, `refactor:`, `docs:`, `chore:`, `test:`, `ci:`, etc.

## Code Style

- Python 3.14, async-first, functional style (functions + frozen dataclasses over class hierarchies)
- `PLC0415`: all imports at top-level, no inline imports
- `TID251`: `from __future__ import annotations` is banned (not needed on 3.14)
- `ANN401`: no `typing.Any` in function signatures
- `B008` suppressed in `server/` for FastAPI `Depends()` patterns
- structlog: use `BoundLogger` (sync) — call `log.info()` without `await`
- Line length: 100
- Pydantic request/response models live in the same file as their router, not in a separate `models.py`
