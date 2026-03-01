import json

import aiosqlite

from homunculus.agent.tools.registry import ToolDef
from homunculus.storage import store


def make_contact_tools(db: aiosqlite.Connection) -> list[ToolDef]:
    async def lookup_contact(query: str) -> str:
        contacts = await store.list_contacts(db)
        query_lower = query.lower()
        matches = [
            {
                "contact_id": str(c["contact_id"]),
                "name": str(c["name"]),
                "phone": str(c["phone"]) if c["phone"] else None,
                "email": str(c["email"]) if c["email"] else None,
                "timezone": str(c["timezone"]) if c["timezone"] else None,
            }
            for c in contacts
            if query_lower in str(c["name"]).lower()
        ]
        return json.dumps({"matches": matches, "count": len(matches)})

    return [
        ToolDef(
            name="lookup_contact",
            description="Look up a contact by name (substring match). Returns matching contacts.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Name or partial name to search for",
                    },
                },
                "required": ["query"],
            },
            handler=lookup_contact,
        ),
    ]
