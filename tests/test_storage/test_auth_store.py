import aiosqlite

from homunculus.storage import store


async def test_create_and_get_auth_session(db: aiosqlite.Connection):
    await store.create_auth_session(db, "sess_1", "identity", "state_abc", "2099-01-01 00:00:00")

    session = await store.get_auth_session(db, "sess_1")
    assert session is not None
    assert session["flow_type"] == "identity"
    assert session["state"] == "state_abc"
    assert session["email"] is None
    assert session["credentials_json"] is None


async def test_get_auth_session_by_state(db: aiosqlite.Connection):
    await store.create_auth_session(db, "sess_2", "calendar", "state_xyz", "2099-01-01 00:00:00")

    session = await store.get_auth_session_by_state(db, "state_xyz")
    assert session is not None
    assert session["session_id"] == "sess_2"


async def test_get_auth_session_not_found(db: aiosqlite.Connection):
    assert await store.get_auth_session(db, "nonexistent") is None
    assert await store.get_auth_session_by_state(db, "nonexistent") is None


async def test_complete_identity_session(db: aiosqlite.Connection):
    await store.create_auth_session(db, "sess_3", "identity", "state_id", "2099-01-01 00:00:00")
    await store.complete_identity_session(
        db, "sess_3", "user@test.com", '{"token": "access", "refresh_token": "refresh"}'
    )

    session = await store.get_auth_session(db, "sess_3")
    assert session is not None
    assert session["email"] == "user@test.com"
    assert session["credentials_json"] == '{"token": "access", "refresh_token": "refresh"}'


async def test_complete_service_session(db: aiosqlite.Connection):
    await store.create_auth_session(db, "sess_4", "calendar", "state_cal", "2099-01-01 00:00:00")
    await store.complete_service_session(db, "sess_4", '{"token": "cal_creds"}')

    session = await store.get_auth_session(db, "sess_4")
    assert session is not None
    assert session["credentials_json"] == '{"token": "cal_creds"}'


async def test_create_session_with_email_flow_type(db: aiosqlite.Connection):
    """flow_type is no longer constrained by CHECK — email is valid."""
    await store.create_auth_session(db, "sess_em", "email", "state_em", "2099-01-01 00:00:00")

    session = await store.get_auth_session(db, "sess_em")
    assert session is not None
    assert session["flow_type"] == "email"


async def test_cleanup_expired_sessions(db: aiosqlite.Connection):
    # Create an expired session
    await store.create_auth_session(db, "expired", "identity", "state_exp", "2000-01-01 00:00:00")
    # Create a valid session
    await store.create_auth_session(db, "valid", "identity", "state_val", "2099-01-01 00:00:00")

    count = await store.cleanup_expired_sessions(db)
    assert count == 1

    assert await store.get_auth_session(db, "expired") is None
    assert await store.get_auth_session(db, "valid") is not None


async def test_save_and_get_google_credentials(db: aiosqlite.Connection):
    await store.save_google_credentials(
        db, "user@test.com", "calendar", '{"token": "abc"}', "calendar"
    )

    row = await store.get_google_credentials(db, "user@test.com", "calendar")
    assert row is not None
    assert row["credentials_json"] == '{"token": "abc"}'
    assert row["scopes"] == "calendar"


async def test_save_google_credentials_upsert(db: aiosqlite.Connection):
    await store.save_google_credentials(db, "user@test.com", "calendar", '{"v": 1}', "scope1")
    await store.save_google_credentials(db, "user@test.com", "calendar", '{"v": 2}', "scope2")

    row = await store.get_google_credentials(db, "user@test.com", "calendar")
    assert row is not None
    assert row["credentials_json"] == '{"v": 2}'
    assert row["scopes"] == "scope2"


async def test_save_credentials_different_services(db: aiosqlite.Connection):
    """Same email, different services — both stored independently."""
    await store.save_google_credentials(db, "user@test.com", "calendar", '{"cal": 1}', "cal_scope")
    await store.save_google_credentials(db, "user@test.com", "email", '{"em": 1}', "email_scope")

    cal = await store.get_google_credentials(db, "user@test.com", "calendar")
    assert cal is not None
    assert cal["credentials_json"] == '{"cal": 1}'

    em = await store.get_google_credentials(db, "user@test.com", "email")
    assert em is not None
    assert em["credentials_json"] == '{"em": 1}'


async def test_get_google_credentials_not_found(db: aiosqlite.Connection):
    assert await store.get_google_credentials(db, "nobody@test.com", "calendar") is None
