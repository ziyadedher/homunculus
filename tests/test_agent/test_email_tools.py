import json
from unittest.mock import MagicMock, patch

from homunculus.agent.tools.email import make_email_tools
from homunculus.services.email.models import EmailDetail, EmailSummary


def test_make_email_tools_returns_two_tools():
    creds = MagicMock()
    tools = make_email_tools(creds)
    assert len(tools) == 2
    names = {t.name for t in tools}
    assert names == {"search_emails", "read_email"}


def test_email_tools_require_approval():
    creds = MagicMock()
    tools = make_email_tools(creds)
    for tool in tools:
        assert tool.requires_approval is True


async def test_search_emails_tool():
    creds = MagicMock()
    tools = make_email_tools(creds)
    search_tool = next(t for t in tools if t.name == "search_emails")

    mock_results = [
        EmailSummary(
            id="msg1",
            thread_id="thread1",
            subject="Test Subject",
            sender="alice@test.com",
            date="Mon, 1 Jan 2024 12:00:00 +0000",
            snippet="Hello world",
        ),
    ]

    with patch("homunculus.agent.tools.email.gmail.search_messages", return_value=mock_results):
        result = await search_tool.handler(query="test")

    parsed = json.loads(result)
    assert len(parsed) == 1
    assert parsed[0]["id"] == "msg1"
    assert parsed[0]["subject"] == "Test Subject"
    assert parsed[0]["sender"] == "alice@test.com"


async def test_read_email_tool():
    creds = MagicMock()
    tools = make_email_tools(creds)
    read_tool = next(t for t in tools if t.name == "read_email")

    mock_detail = EmailDetail(
        id="msg1",
        thread_id="thread1",
        subject="Test Subject",
        sender="alice@test.com",
        to=["bob@test.com"],
        date="Mon, 1 Jan 2024 12:00:00 +0000",
        body_text="Hello, this is a test email.",
    )

    with patch("homunculus.agent.tools.email.gmail.get_message", return_value=mock_detail):
        result = await read_tool.handler(message_id="msg1")

    parsed = json.loads(result)
    assert parsed["id"] == "msg1"
    assert parsed["subject"] == "Test Subject"
    assert parsed["to"] == ["bob@test.com"]
    assert parsed["body_text"] == "Hello, this is a test email."
