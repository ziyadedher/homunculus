import json

from google.oauth2.credentials import Credentials

from homunculus.agent.tools.registry import ToolDef
from homunculus.services.email import google as gmail


def make_email_tools(creds: Credentials) -> list[ToolDef]:
    async def search_emails(query: str, max_results: int = 10) -> str:
        results = await gmail.search_messages(creds, query=query, max_results=max_results)
        return json.dumps(
            [
                {
                    "id": e.id,
                    "thread_id": e.thread_id,
                    "subject": e.subject,
                    "sender": e.sender,
                    "date": e.date,
                    "snippet": e.snippet,
                }
                for e in results
            ]
        )

    async def read_email(message_id: str) -> str:
        detail = await gmail.get_message(creds, message_id=message_id)
        return json.dumps(
            {
                "id": detail.id,
                "thread_id": detail.thread_id,
                "subject": detail.subject,
                "sender": detail.sender,
                "to": detail.to,
                "date": detail.date,
                "body_text": detail.body_text,
            }
        )

    return [
        ToolDef(
            name="search_emails",
            description="Search the owner's Gmail inbox. Returns a list of matching email summaries.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Gmail search query (same syntax as Gmail search bar)",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results to return (default 10)",
                    },
                },
                "required": ["query"],
            },
            handler=search_emails,
            requires_approval=True,
        ),
        ToolDef(
            name="read_email",
            description="Read the full content of a specific email by its message ID.",
            input_schema={
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "string",
                        "description": "The Gmail message ID to read",
                    },
                },
                "required": ["message_id"],
            },
            handler=read_email,
            requires_approval=True,
        ),
    ]
