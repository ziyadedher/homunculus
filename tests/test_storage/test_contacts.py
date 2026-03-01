import pytest

from homunculus.storage import store
from homunculus.types import ContactId


async def test_create_and_get_contact(db):
    contact_id = await store.create_contact(
        db, ContactId("alice1"), name="Alice", phone="+11111111111"
    )
    assert contact_id == "alice1"

    contact = await store.get_contact(db, contact_id)
    assert contact is not None
    assert contact.name == "Alice"
    assert contact.phone == "+11111111111"
    assert contact.email is None


async def test_get_contact_not_found(db):
    contact = await store.get_contact(db, ContactId("nonexistent"))
    assert contact is None


async def test_get_contact_by_phone(db):
    await store.create_contact(db, ContactId("bob1"), name="Bob", phone="+12222222222")
    contact = await store.get_contact_by_phone(db, "+12222222222")
    assert contact is not None
    assert contact.name == "Bob"


async def test_get_contact_by_phone_not_found(db):
    contact = await store.get_contact_by_phone(db, "+19999999999")
    assert contact is None


async def test_get_contact_by_email(db):
    await store.create_contact(db, ContactId("carol1"), name="Carol", email="carol@example.com")
    contact = await store.get_contact_by_email(db, "carol@example.com")
    assert contact is not None
    assert contact.name == "Carol"


async def test_get_contact_by_email_not_found(db):
    contact = await store.get_contact_by_email(db, "nobody@example.com")
    assert contact is None


async def test_list_contacts(db):
    await store.create_contact(db, ContactId("alice2"), name="Alice", phone="+11111111111")
    await store.create_contact(db, ContactId("bob2"), name="Bob", phone="+12222222222")
    contacts = await store.list_contacts(db)
    assert len(contacts) == 2
    names = [c.name for c in contacts]
    assert "Alice" in names
    assert "Bob" in names


async def test_list_contacts_empty(db):
    contacts = await store.list_contacts(db)
    assert contacts == []


async def test_update_contact(db):
    contact_id = await store.create_contact(
        db, ContactId("alice3"), name="Alice", phone="+11111111111"
    )
    updated = await store.update_contact(
        db, contact_id, {"name": "Alice Smith", "email": "alice@test.com"}
    )
    assert updated is True

    contact = await store.get_contact(db, contact_id)
    assert contact is not None
    assert contact.name == "Alice Smith"
    assert contact.email == "alice@test.com"


async def test_update_contact_clear_field(db):
    contact_id = await store.create_contact(
        db, ContactId("alice4"), name="Alice", phone="+11111111111", notes="test"
    )
    updated = await store.update_contact(db, contact_id, {"notes": None})
    assert updated is True

    contact = await store.get_contact(db, contact_id)
    assert contact is not None
    assert contact.notes is None


async def test_update_contact_invalid_field(db):
    contact_id = await store.create_contact(db, ContactId("alice5"), name="Alice")
    updated = await store.update_contact(db, contact_id, {"invalid_field": "value"})
    assert updated is False


async def test_update_contact_not_found(db):
    updated = await store.update_contact(db, ContactId("nonexistent"), {"name": "Bob"})
    assert updated is False


async def test_delete_contact(db):
    contact_id = await store.create_contact(db, ContactId("alice6"), name="Alice")
    deleted = await store.delete_contact(db, contact_id)
    assert deleted is True

    contact = await store.get_contact(db, contact_id)
    assert contact is None


async def test_delete_contact_not_found(db):
    deleted = await store.delete_contact(db, ContactId("nonexistent"))
    assert deleted is False


async def test_unique_phone_constraint(db):
    await store.create_contact(db, ContactId("alice7"), name="Alice", phone="+11111111111")
    with pytest.raises(Exception, match="UNIQUE"):
        await store.create_contact(db, ContactId("bob7"), name="Bob", phone="+11111111111")


async def test_unique_email_constraint(db):
    await store.create_contact(db, ContactId("alice8"), name="Alice", email="alice@test.com")
    with pytest.raises(Exception, match="UNIQUE"):
        await store.create_contact(db, ContactId("bob8"), name="Bob", email="alice@test.com")


async def test_unique_contact_id_constraint(db):
    await store.create_contact(db, ContactId("duped"), name="Alice")
    with pytest.raises(Exception, match="UNIQUE"):
        await store.create_contact(db, ContactId("duped"), name="Bob")


async def test_contact_all_fields(db):
    contact_id = await store.create_contact(
        db,
        ContactId("alice9"),
        name="Alice",
        phone="+11111111111",
        email="alice@test.com",
        timezone="America/New_York",
        notes="VIP contact",
    )
    contact = await store.get_contact(db, contact_id)
    assert contact is not None
    assert contact.name == "Alice"
    assert contact.phone == "+11111111111"
    assert contact.email == "alice@test.com"
    assert contact.timezone == "America/New_York"
    assert contact.notes == "VIP contact"
    assert contact.contact_id == "alice9"
