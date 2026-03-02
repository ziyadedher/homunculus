import asyncio
import contextlib
import sys
import webbrowser
from pathlib import Path

import uvicorn
from cyclopts import App
from dotenv import load_dotenv

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
from homunculus.client import HomunculusClient
from homunculus.server.app import create_app
from homunculus.utils.config import (
    ClientConfig,
    ServeConfig,
    load_client_config,
    load_serve_config,
)
from homunculus.utils.logging import configure_logging, get_logger
from homunculus.utils.tracing import configure_tracing

app = App(
    name="homunculus",
    help="Personal AI scheduling agent.",
)
log = get_logger()

DEFAULT_SERVER_CONFIG = Path("config/config.server.toml")
DEFAULT_CLIENT_CONFIG = Path("config/config.client.toml")

POLL_INTERVAL_SECONDS = 2


def _load_client(config_path: Path) -> ClientConfig:
    try:
        config = load_client_config(config_path)
    except FileNotFoundError:
        sys.stderr.write(f"Config file not found: {config_path}\n")
        sys.exit(1)
    except KeyError as e:
        sys.stderr.write(f"Missing required config key: {e}\n")
        sys.exit(1)

    configure_logging(level=config.logging.level, fmt=config.logging.format)
    configure_tracing(config.tracing)
    return config


def _make_client(config: ClientConfig) -> HomunculusClient:
    """Create a HomunculusClient from client config."""
    return HomunculusClient(
        server_url=config.server_url,
        credentials_path=config.credentials_path,
    )


def _load_server(config_path: Path) -> ServeConfig:
    try:
        config = load_serve_config(config_path)
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
    config_path: Path = DEFAULT_CLIENT_CONFIG,
    server: str | None = None,
) -> None:
    """Chat via the server API (e.g. cli:alice, telegram:123456789).

    Messages are sent to the server's /api/message endpoint. Requires
    saved Google credentials from 'homunculus auth login'.
    """
    config = _load_client(config_path)
    client = _make_client(config)
    if server is not None:
        client = HomunculusClient(
            server_url=server,
            credentials_path=config.credentials_path,
        )

    async def _run() -> None:
        async with client:
            await run_chat(client, conversation_id_str=conversation_id)

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run())


# --- Auth sub-app ---

auth_app = App(name="auth", help="Authentication commands.")
app.command(auth_app)


@auth_app.default
@auth_app.command(name="login")
def auth_login(
    *,
    config_path: Path = DEFAULT_CLIENT_CONFIG,
    server: str | None = None,
) -> None:
    """Authenticate with the server (opens browser for Google OAuth)."""
    config = _load_client(config_path)
    # Create client without credentials for unauthenticated auth_start/auth_status
    server_url = server or config.server_url
    client = HomunculusClient(server_url=server_url, credentials_path=config.credentials_path)

    async def _run() -> None:
        async with client:
            result = await client.auth_start()
            session_id = result.session_id
            auth_url = result.auth_url

            sys.stdout.write("Opening browser for authentication...\n")
            sys.stdout.write(f"If browser doesn't open, visit: {auth_url}\n")
            webbrowser.open(auth_url)

            sys.stdout.write("Waiting for authentication...\n")
            while True:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                status_data = await client.auth_status(session_id)

                if status_data.status == "complete":
                    credentials_json = status_data.credentials_json
                    email = status_data.email

                    if credentials_json is None or email is None:
                        sys.stderr.write("Auth completed but missing credentials/email.\n")
                        sys.exit(1)

                    # Save credentials
                    creds_path = config.credentials_path.expanduser()
                    creds_path.parent.mkdir(parents=True, exist_ok=True)
                    creds_path.write_text(credentials_json)
                    creds_path.chmod(0o600)

                    sys.stdout.write(f"Authenticated as {email}\n")
                    sys.stdout.write(f"Credentials saved to {creds_path}\n")
                    return

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run())


@auth_app.command(name="whoami")
def auth_whoami(
    *,
    config_path: Path = DEFAULT_CLIENT_CONFIG,
    server: str | None = None,
) -> None:
    """Show the currently authenticated user and granted services."""
    config = _load_client(config_path)
    client = _make_client(config)
    if server is not None:
        client = HomunculusClient(
            server_url=server,
            credentials_path=config.credentials_path,
        )

    async def _run() -> None:
        async with client:
            try:
                data = await client.whoami()
            except Exception:
                sys.stderr.write("Not authenticated. Run 'homunculus auth login' first.\n")
                sys.exit(1)

            sys.stdout.write(f"Email: {data.email}\n")
            sys.stdout.write(f"Owner: {'yes' if data.is_owner else 'no'}\n")
            if data.services:
                sys.stdout.write(f"Services: {', '.join(data.services)}\n")
            else:
                sys.stdout.write("Services: none\n")

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run())


KNOWN_SERVICES = ("calendar", "email")


@auth_app.command(name="grant")
def auth_grant(
    service: str,
    *,
    config_path: Path = DEFAULT_CLIENT_CONFIG,
    server: str | None = None,
) -> None:
    """Grant a Google service access to the server (e.g. 'calendar', 'email')."""
    if service not in KNOWN_SERVICES:
        sys.stderr.write(
            f"Unknown service: {service}\nKnown services: {', '.join(KNOWN_SERVICES)}\n"
        )
        sys.exit(1)

    config = _load_client(config_path)
    client = _make_client(config)
    if server is not None:
        client = HomunculusClient(
            server_url=server,
            credentials_path=config.credentials_path,
        )

    async def _run() -> None:
        async with client:
            try:
                result = await client.service_start(service)
            except Exception:
                sys.stderr.write(
                    f"Failed to start {service} auth. Run 'homunculus auth login' first.\n"
                )
                sys.exit(1)

            session_id = result.session_id
            auth_url = result.auth_url

            sys.stdout.write(f"Opening browser for {service} authorization...\n")
            sys.stdout.write(f"If browser doesn't open, visit: {auth_url}\n")
            webbrowser.open(auth_url)

            sys.stdout.write(f"Waiting for {service} authorization...\n")
            while True:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                status_data = await client.service_status(service, session_id)

                if status_data.status == "complete":
                    sys.stdout.write(f"{service.title()} access granted.\n")
                    return

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run())


@app.default
@app.command
def serve(*, config_path: Path = DEFAULT_SERVER_CONFIG) -> None:
    """Start the HTTP webhook server (requires Telegram config)."""
    config = _load_server(config_path)
    application = create_app(config)
    uvicorn.run(
        application,
        host=config.server.host,
        port=config.server.port,
        log_config=None,
    )


# --- Admin sub-app ---

admin_app = App(name="admin", help="Admin commands.")
app.command(admin_app)

contacts_app = App(name="contacts", help="Manage contacts.")
admin_app.command(contacts_app)


def _get_owner_timezone(config: ClientConfig) -> str:
    """Get owner timezone via whoami endpoint. Falls back to UTC."""
    client = _make_client(config)

    async def _run() -> str:
        async with client:
            try:
                data = await client.whoami()
                return data.owner_timezone or "UTC"
            except Exception:
                return "UTC"

    return asyncio.run(_run())


@admin_app.command
def dashboard(*, config_path: Path = DEFAULT_CLIENT_CONFIG) -> None:
    """Owner approval dashboard: approve/deny pending requests."""
    config = _load_client(config_path)
    tz_name = _get_owner_timezone(config)
    client = _make_client(config)

    async def _run() -> None:
        async with client:
            await run_dashboard(client, tz_name)

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run())


@contacts_app.default
@contacts_app.command(name="list")
def contacts_list(*, config_path: Path = DEFAULT_CLIENT_CONFIG) -> None:
    """List all contacts."""
    config = _load_client(config_path)
    client = _make_client(config)

    async def _run() -> None:
        async with client:
            await run_contacts_list(client)

    asyncio.run(_run())


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
    config_path: Path = DEFAULT_CLIENT_CONFIG,
) -> None:
    """Add a new contact."""
    config = _load_client(config_path)
    client = _make_client(config)

    async def _run() -> None:
        async with client:
            await run_contacts_add(
                client,
                contact_id,
                name,
                phone=phone,
                email=email,
                timezone=timezone,
                notes=notes,
                telegram_chat_id=telegram_chat_id,
            )

    asyncio.run(_run())


@contacts_app.command(name="edit")
def contacts_edit(contact_id: str, *, config_path: Path = DEFAULT_CLIENT_CONFIG) -> None:
    """Edit an existing contact."""
    config = _load_client(config_path)
    client = _make_client(config)

    async def _run() -> None:
        async with client:
            await run_contacts_edit(client, contact_id)

    asyncio.run(_run())


@contacts_app.command(name="rm")
def contacts_rm(contact_id: str, *, config_path: Path = DEFAULT_CLIENT_CONFIG) -> None:
    """Delete a contact."""
    config = _load_client(config_path)
    client = _make_client(config)

    async def _run() -> None:
        async with client:
            await run_contacts_rm(client, contact_id)

    asyncio.run(_run())


@admin_app.command(name="log")
def audit_log(
    *, config_path: Path = DEFAULT_CLIENT_CONFIG, conversation: str | None = None
) -> None:
    """View audit log entries."""
    config = _load_client(config_path)
    client = _make_client(config)

    async def _run() -> None:
        async with client:
            await run_audit_log(client, conversation_id=conversation)

    asyncio.run(_run())


conversations_app = App(name="conversations", help="Manage conversations.")
admin_app.command(conversations_app)


@conversations_app.default
@conversations_app.command(name="list")
def conversations_list(*, config_path: Path = DEFAULT_CLIENT_CONFIG) -> None:
    """List active conversations."""
    config = _load_client(config_path)
    tz_name = _get_owner_timezone(config)
    client = _make_client(config)

    async def _run() -> None:
        async with client:
            await run_conversations_list(client, tz_name)

    asyncio.run(_run())


@conversations_app.command(name="view")
def conversations_view(conversation_id: str, *, config_path: Path = DEFAULT_CLIENT_CONFIG) -> None:
    """View message history for a conversation."""
    config = _load_client(config_path)
    client = _make_client(config)

    async def _run() -> None:
        async with client:
            await run_conversation_detail(client, conversation_id)

    asyncio.run(_run())


def main() -> None:
    load_dotenv(override=True)
    app()


if __name__ == "__main__":
    main()
