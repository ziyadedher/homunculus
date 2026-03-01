from datetime import UTC, datetime

from homunculus.agent.prompt import build_system_prompt
from homunculus.utils.config import OwnerConfig


def test_system_prompt_contains_owner_name():
    owner = OwnerConfig(
        name="Ziyad",
        email="ziyad@test.com",
        timezone="America/Los_Angeles",
        telegram_chat_id="999000",
    )
    prompt = build_system_prompt(owner)
    assert "Ziyad" in prompt


def test_system_prompt_contains_timezone():
    owner = OwnerConfig(
        name="Ziyad",
        email="ziyad@test.com",
        timezone="America/Los_Angeles",
        telegram_chat_id="999000",
    )
    prompt = build_system_prompt(owner)
    assert "America/Los_Angeles" in prompt


def test_system_prompt_contains_current_time():
    owner = OwnerConfig(
        name="Ziyad",
        email="ziyad@test.com",
        timezone="America/Los_Angeles",
        telegram_chat_id="999000",
    )
    now = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
    prompt = build_system_prompt(owner, now=now)
    assert "2025-06-15" in prompt


def test_system_prompt_privacy_rules():
    owner = OwnerConfig(
        name="Ziyad",
        email="ziyad@test.com",
        timezone="America/Los_Angeles",
        telegram_chat_id="999000",
    )
    prompt = build_system_prompt(owner)
    assert "NEVER share event titles" in prompt


def test_system_prompt_with_contact():
    owner = OwnerConfig(
        name="Ziyad",
        email="ziyad@test.com",
        timezone="America/Los_Angeles",
        telegram_chat_id="999000",
    )
    contact = {
        "name": "Alice",
        "timezone": "America/New_York",
        "notes": "VIP contact",
    }
    prompt = build_system_prompt(owner, contact=contact)
    assert "Alice" in prompt
    assert "America/New_York" in prompt
    assert "VIP contact" in prompt


def test_system_prompt_with_contact_no_optional_fields():
    owner = OwnerConfig(
        name="Ziyad",
        email="ziyad@test.com",
        timezone="America/Los_Angeles",
        telegram_chat_id="999000",
    )
    contact = {"name": "Bob", "timezone": None, "notes": None}
    prompt = build_system_prompt(owner, contact=contact)
    assert "Bob" in prompt
    assert "Contact Information" in prompt


def test_system_prompt_without_contact():
    owner = OwnerConfig(
        name="Ziyad",
        email="ziyad@test.com",
        timezone="America/Los_Angeles",
        telegram_chat_id="999000",
    )
    prompt = build_system_prompt(owner, contact=None)
    assert "Contact Information" not in prompt


def test_system_prompt_approval_instructions():
    owner = OwnerConfig(
        name="Ziyad",
        email="ziyad@test.com",
        timezone="America/Los_Angeles",
        telegram_chat_id="999000",
    )
    prompt = build_system_prompt(owner)
    assert "requiring approval" in prompt or "approval" in prompt.lower()
