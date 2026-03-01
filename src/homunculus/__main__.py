import asyncio
import contextlib
import sys
from pathlib import Path

from aiohttp import web
from cyclopts import App
from dotenv import load_dotenv

from homunculus.app import create_app
from homunculus.calendar.google import get_credentials
from homunculus.cli import (
    run_audit_log,
    run_chat,
    run_contacts_add,
    run_contacts_edit,
    run_contacts_list,
    run_contacts_rm,
    run_conversation_detail,
    run_conversations_list,
    run_dashboard,
)
from homunculus.utils.config import Config, load_config
from homunculus.utils.logging import configure_logging, get_logger
from homunculus.utils.tracing import configure_tracing

app = App(
    name="homunculus",
    help="Personal AI scheduling agent.",
)
log = get_logger()

DEFAULT_CONFIG = Path("config/config.toml")


def _load_and_configure(config_path: Path) -> Config:
    try:
        config = load_config(config_path)
    except FileNotFoundError:
        sys.stderr.write(f"Config file not found: {config_path}\n")
        sys.exit(1)
    except KeyError as e:
        sys.stderr.write(f"Missing required config key: {e}\n")
        sys.exit(1)

    configure_logging(level=config.logging.level, fmt=config.logging.format)
    configure_tracing(config.tracing)
    return config


@app.command
def chat(
    conversation_id: str,
    *,
    config_path: Path = DEFAULT_CONFIG,
    server: str | None = None,
) -> None:
    """Chat via the server API (e.g. cli:alice, telegram:123456789).

    Messages are sent to the server's /api/message endpoint. Requires
    Google Calendar config for OAuth authentication.
    """
    config = _load_and_configure(config_path)
    if config.google_calendar is None:
        sys.stderr.write("Error: [google_calendar] config section required for CLI auth.\n")
        sys.exit(1)
    server_url = server or f"http://localhost:{config.server.port}"
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run_chat(config, conversation_id_str=conversation_id, server_url=server_url))


@app.command
def auth(*, config_path: Path = DEFAULT_CONFIG) -> None:
    """Authenticate with Google Calendar (opens browser for OAuth)."""
    config = _load_and_configure(config_path)
    if config.google_calendar is None:
        sys.stderr.write("Error: [google_calendar] section required in config.\n")
        sys.exit(1)
    get_credentials(
        credentials_path=config.google_calendar.credentials_path,
        token_path=config.google_calendar.token_path,
    )
    sys.stdout.write(
        f"Authenticated successfully. Token saved to {config.google_calendar.token_path}\n"
    )


@app.default
@app.command
def serve(*, config_path: Path = DEFAULT_CONFIG) -> None:
    """Start the HTTP webhook server (requires Telegram + Google Calendar config)."""
    config = _load_and_configure(config_path)

    if config.telegram is None:
        sys.stderr.write("Error: [telegram] config or TELEGRAM_BOT_TOKEN env var required.\n")
        sys.exit(1)
    if config.google_calendar is None:
        sys.stderr.write("Error: [google_calendar] config section required for serve mode.\n")
        sys.exit(1)

    async def _run() -> None:
        application = await create_app(config)
        runner = web.AppRunner(application)
        await runner.setup()
        site = web.TCPSite(runner, config.server.host, config.server.port)
        await site.start()
        log.info("server_started", host=config.server.host, port=config.server.port)
        await asyncio.Event().wait()

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run())


# --- Admin sub-app ---

admin_app = App(name="admin", help="Admin commands.")
app.command(admin_app)

contacts_app = App(name="contacts", help="Manage contacts.")
admin_app.command(contacts_app)


@admin_app.command
def dashboard(*, config_path: Path = DEFAULT_CONFIG) -> None:
    """Owner approval dashboard: approve/deny pending requests."""
    config = _load_and_configure(config_path)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run_dashboard(config))


@contacts_app.default
@contacts_app.command(name="list")
def contacts_list(*, config_path: Path = DEFAULT_CONFIG) -> None:
    """List all contacts."""
    config = _load_and_configure(config_path)
    asyncio.run(run_contacts_list(config))


@contacts_app.command(name="add")
def contacts_add(
    contact_id: str,
    name: str,
    *,
    phone: str | None = None,
    email: str | None = None,
    timezone: str | None = None,
    notes: str | None = None,
    telegram_chat_id: str | None = None,
    config_path: Path = DEFAULT_CONFIG,
) -> None:
    """Add a new contact."""
    config = _load_and_configure(config_path)
    asyncio.run(
        run_contacts_add(
            config,
            contact_id,
            name,
            phone=phone,
            email=email,
            timezone=timezone,
            notes=notes,
            telegram_chat_id=telegram_chat_id,
        )
    )


@contacts_app.command(name="edit")
def contacts_edit(contact_id: str, *, config_path: Path = DEFAULT_CONFIG) -> None:
    """Edit an existing contact."""
    config = _load_and_configure(config_path)
    asyncio.run(run_contacts_edit(config, contact_id))


@contacts_app.command(name="rm")
def contacts_rm(contact_id: str, *, config_path: Path = DEFAULT_CONFIG) -> None:
    """Delete a contact."""
    config = _load_and_configure(config_path)
    asyncio.run(run_contacts_rm(config, contact_id))


@admin_app.command(name="log")
def audit_log(*, config_path: Path = DEFAULT_CONFIG, conversation: str | None = None) -> None:
    """View audit log entries."""
    config = _load_and_configure(config_path)
    asyncio.run(run_audit_log(config, conversation_id=conversation))


conversations_app = App(name="conversations", help="Manage conversations.")
admin_app.command(conversations_app)


@conversations_app.default
@conversations_app.command(name="list")
def conversations_list(*, config_path: Path = DEFAULT_CONFIG) -> None:
    """List active conversations."""
    config = _load_and_configure(config_path)
    asyncio.run(run_conversations_list(config))


@conversations_app.command(name="view")
def conversations_view(conversation_id: str, *, config_path: Path = DEFAULT_CONFIG) -> None:
    """View message history for a conversation."""
    config = _load_and_configure(config_path)
    asyncio.run(run_conversation_detail(config, conversation_id))


def main() -> None:
    load_dotenv(override=True)
    app()


if __name__ == "__main__":
    main()
