"""End-to-end test script for the Homunculus API.

Usage:
    uv run python scripts/test_e2e.py "What's on my calendar today?"
    uv run python scripts/test_e2e.py --reset
    uv run python scripts/test_e2e.py --reset "Hello"
    uv run python scripts/test_e2e.py --conversation-id test-1 "Hello"
"""

import asyncio
import sys

from cyclopts import App

from homunculus.client import HomunculusClient

app = App(name="test_e2e", help="Homunculus E2E test script.")

DEFAULT_SERVER = "https://homunculus.ziyadedher.com"

_write = sys.stdout.write


@app.default
def main(
    message: str | None = None,
    *,
    server: str = DEFAULT_SERVER,
    reset: bool = False,
    hard_reset: bool = False,
    conversation_id: str | None = None,
    timeout: float = 120.0,
) -> None:
    """Send a message and/or reset conversation data."""
    if not reset and not hard_reset and not message:
        sys.stderr.write("Error: provide a message, --reset, or --hard-reset\n")
        sys.exit(1)

    asyncio.run(_run(server, message, reset, hard_reset, conversation_id, timeout))


async def _run(
    server: str,
    message: str | None,
    reset: bool,
    hard_reset: bool,
    conversation_id: str | None,
    timeout: float,
) -> None:
    async with HomunculusClient(server) as client:
        # Health check
        health = await client.health()
        _write(f"Health: {health['status']}\n")

        # Whoami
        who = await client.whoami()
        _write(f"Authenticated as: {who.email} (owner={who.is_owner})\n")

        # Reset
        if hard_reset:
            reset_resp = await client.reset(hard=True)
            _write(f"Hard reset: {reset_resp.status}\n")
        elif reset:
            reset_resp = await client.reset()
            _write(f"Reset: {reset_resp.status}\n")

        # Send message
        if message:
            _write(f"\nSending: {message}\n")
            result = await client.send_and_poll(
                message,
                override_client_id=conversation_id,
                timeout=timeout,
            )
            _write(f"Response: {result.response_text}\n")
            if result.request_id:
                _write(f"Request ID: {result.request_id}\n")
                if result.request_message:
                    _write(f"Request message: {result.request_message}\n")


if __name__ == "__main__":
    app()
