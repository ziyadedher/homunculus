import json

from homunculus.agent.tools.contacts import make_contact_tools
from homunculus.storage import store
from homunculus.types import ContactId


async def test_lookup_contact_found(db):
    await store.create_contact(db, ContactId("alice"), name="Alice Smith", phone="+11111111111")
    await store.create_contact(db, ContactId("bob"), name="Bob Jones", phone="+12222222222")

    tools = make_contact_tools(db)
    assert len(tools) == 1
    assert tools[0].name == "lookup_contact"

    result = await tools[0].handler(query="Alice")
    parsed = json.loads(result)
    assert parsed["count"] == 1
    assert parsed["matches"][0]["name"] == "Alice Smith"


async def test_lookup_contact_not_found(db):
    await store.create_contact(db, ContactId("alice"), name="Alice", phone="+11111111111")

    tools = make_contact_tools(db)
    result = await tools[0].handler(query="Charlie")
    parsed = json.loads(result)
    assert parsed["count"] == 0
    assert parsed["matches"] == []


async def test_lookup_contact_case_insensitive(db):
    await store.create_contact(db, ContactId("alice"), name="Alice Smith", phone="+11111111111")

    tools = make_contact_tools(db)
    result = await tools[0].handler(query="alice")
    parsed = json.loads(result)
    assert parsed["count"] == 1


async def test_lookup_contact_partial_match(db):
    await store.create_contact(db, ContactId("alice1"), name="Alice Smith", phone="+11111111111")
    await store.create_contact(db, ContactId("alice2"), name="Alice Jones", phone="+12222222222")

    tools = make_contact_tools(db)
    result = await tools[0].handler(query="Alice")
    parsed = json.loads(result)
    assert parsed["count"] == 2
