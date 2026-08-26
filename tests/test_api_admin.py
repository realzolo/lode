"""Integration tests for admin user/AI-model management and invites.

Mirrors ``test_api_auth.py``: an async ``httpx`` client drives the ASGI app with
no lifespan (schema already current). Admin and regular users are created in
the same loop the requests run in. Everything created is torn down afterwards so
repeated runs stay idempotent.
"""

from __future__ import annotations

import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from lode.api.main import app
from lode.crypto import decrypt_secret
from lode.db.models.git import GitCredential, GitRepo
from lode.db.models.platform_setting import PlatformSetting
from lode.db.models.user import Invite, User
from lode.db.session import AsyncSessionLocal
from lode.security import hash_password

ADMIN_EMAIL = f"admin-{uuid.uuid4().hex}@lode.local"
ADMIN_PASSWORD = "admin-pass-1"
USER_EMAIL = f"user-{uuid.uuid4().hex}@lode.local"
USER_PASSWORD = "user-pass-1"


async def _make_user(email: str, password: str, role: str) -> int:
    async with AsyncSessionLocal() as session:
        u = User(email=email, name=role, role=role, status="active")
        u.password_hash = hash_password(password)
        session.add(u)
        await session.commit()
        await session.refresh(u)
        return u.id


@pytest_asyncio.fixture
async def admin() -> tuple[str, str, int]:
    uid = await _make_user(ADMIN_EMAIL, ADMIN_PASSWORD, "admin")
    yield ADMIN_EMAIL, ADMIN_PASSWORD, uid
    async with AsyncSessionLocal() as session:
        v = (await session.execute(select(User).where(User.id == uid))).scalars().first()
        if v is not None:
            await session.delete(v)
            await session.commit()


@pytest_asyncio.fixture
async def user() -> tuple[str, str, int]:
    uid = await _make_user(USER_EMAIL, USER_PASSWORD, "user")
    yield USER_EMAIL, USER_PASSWORD, uid
    async with AsyncSessionLocal() as session:
        v = (await session.execute(select(User).where(User.id == uid))).scalars().first()
        if v is not None:
            await session.delete(v)
            await session.commit()


async def _login(email: str, password: str) -> str:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/auth/login", json={"email": email, "password": password})
        assert resp.status_code == 200, resp.text
        return resp.json()["token"]


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# --- AI model config (admin) --------------------------------------------

async def test_non_admin_cannot_create_ai_model(user):
    _email, _pw, _uid = user
    token = await _login(USER_EMAIL, USER_PASSWORD)
    async with _client() as client:
        resp = await client.post(
            "/settings/ai-models",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "provider": "openai",
                "base_url": "https://api.openai.com/v1",
                "api_key": "test-openai-key",
                "model": "gpt-4o-mini",
                "is_default": True,
            },
        )
        assert resp.status_code == 403
        resp = await client.put(
            "/settings/ai-output-language",
            headers={"Authorization": f"Bearer {token}"},
            json={"language": "zh"},
        )
        assert resp.status_code == 403


async def test_admin_updates_ai_output_language(admin):
    _email, _pw, _uid = admin
    token = await _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncSessionLocal() as session:
        previous = await session.get(PlatformSetting, "ai_output_language")
        previous_value = previous.value if previous is not None else None

    try:
        async with _client() as client:
            current = await client.get("/settings", headers=headers)
            assert current.status_code == 200, current.text
            assert current.json()["ai_output_language"] in {"en", "zh"}
            assert current.json()["supported_ai_output_languages"] == ["en", "zh"]

            updated = await client.put(
                "/settings/ai-output-language",
                headers=headers,
                json={"language": "zh"},
            )
            assert updated.status_code == 200, updated.text
            assert updated.json() == {"language": "zh"}

            invalid = await client.put(
                "/settings/ai-output-language",
                headers=headers,
                json={"language": "ja"},
            )
            assert invalid.status_code == 422

        async with AsyncSessionLocal() as session:
            stored = await session.get(PlatformSetting, "ai_output_language")
            assert stored is not None
            assert stored.value == "zh"
    finally:
        async with AsyncSessionLocal() as session:
            stored = await session.get(PlatformSetting, "ai_output_language")
            if previous_value is None:
                if stored is not None:
                    await session.delete(stored)
            elif stored is None:
                session.add(PlatformSetting(key="ai_output_language", value=previous_value))
            else:
                stored.value = previous_value
            await session.commit()


async def test_admin_crud_ai_model(admin):
    _email, _pw, _uid = admin
    token = await _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    async with _client() as client:
        # create
        resp = await client.post(
            "/settings/ai-models",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "provider": "openai",
                "base_url": "https://api.openai.com/v1",
                "api_key": "test-openai-key",
                "model": "gpt-4o-mini",
                "is_default": True,
            },
        )
        assert resp.status_code == 201, resp.text
        created = resp.json()
        assert created["is_default"] is True
        assert created["has_key"] is True
        mid = created["id"]

        # update (metadata only, key omitted -> preserved)
        resp = await client.put(
            f"/settings/ai-models/{mid}",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "provider": "openai",
                "base_url": "https://api.openai.com/v1",
                "api_key": "",
                "model": "gpt-4o",
                "is_default": True,
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["model"] == "gpt-4o"
        assert resp.json()["has_key"] is True

        # delete
        resp = await client.delete(
            f"/settings/ai-models/{mid}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 204


# --- Git credentials (admin) ---------------------------------------------

async def test_non_admin_cannot_manage_git(user):
    _email, _pw, _uid = user
    token = await _login(USER_EMAIL, USER_PASSWORD)
    async with _client() as client:
        resp = await client.post(
            "/settings/git-credentials",
            headers={"Authorization": f"Bearer {token}"},
            json={"auth_type": "ssh", "username": "x", "secret": "test-secret", "readonly": True, "note": ""},
        )
        assert resp.status_code == 403
        resp = await client.post(
            "/settings/git-repos",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "r", "repo_url": "https://github.com/o/r", "default_branch": "main", "repo_type": "github", "credential_id": None},
        )
        assert resp.status_code == 403


async def test_admin_crud_git_credential(admin):
    _email, _pw, _uid = admin
    token = await _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    async with _client() as client:
        headers = {"Authorization": f"Bearer {token}"}

        # Create stores only encrypted ciphertext.
        resp = await client.post(
            "/settings/git-credentials",
            headers=headers,
            json={"auth_type": "ssh", "username": "deploy", "secret": "deploy-key", "readonly": True, "note": "ci key"},
        )
        assert resp.status_code == 201, resp.text
        cid = resp.json()["id"]
        assert resp.json()["has_secret"] is True

        # the env ref is kept as-is in the DB row.
        async with AsyncSessionLocal() as session:
            row = await session.get(GitCredential, cid)
            assert decrypt_secret(row.secret_ciphertext) == "deploy-key"

        # create with a literal secret -> encrypted at rest (not stored as plaintext).
        resp = await client.post(
            "/settings/git-credentials",
            headers=headers,
            json={"auth_type": "https", "username": "robot", "secret": "ghp_literalabc123", "readonly": False, "note": ""},
        )
        assert resp.status_code == 201, resp.text
        cid2 = resp.json()["id"]
        async with AsyncSessionLocal() as session:
            row = await session.get(GitCredential, cid2)
            assert row.secret_ciphertext != "ghp_literalabc123"
            # and it decrypts back to the original literal.
            assert decrypt_secret(row.secret_ciphertext) == "ghp_literalabc123"

        # update without re-supplying the secret -> existing secret preserved.
        resp = await client.put(
            f"/settings/git-credentials/{cid2}",
            headers=headers,
            json={"auth_type": "https", "username": "robot", "secret": "", "readonly": True, "note": "rotated meta"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["readonly"] is True
        assert resp.json()["note"] == "rotated meta"
        async with AsyncSessionLocal() as session:
            row = await session.get(GitCredential, cid2)
            assert decrypt_secret(row.secret_ciphertext) == "ghp_literalabc123"

        # update supplying a new env ref -> stored verbatim.
        resp = await client.put(
            f"/settings/git-credentials/{cid2}",
            headers=headers,
            json={"auth_type": "https", "username": "robot", "secret": "rotated-secret", "readonly": True, "note": "rotated meta"},
        )
        assert resp.status_code == 200, resp.text
        async with AsyncSessionLocal() as session:
            row = await session.get(GitCredential, cid2)
            assert decrypt_secret(row.secret_ciphertext) == "rotated-secret"

        # 404 on missing credential.
        resp = await client.put(
            "/settings/git-credentials/999999",
            headers=headers,
            json={"auth_type": "ssh", "username": "x", "secret": "", "readonly": True, "note": ""},
        )
        assert resp.status_code == 404

        # delete both.
        for c in (cid, cid2):
            resp = await client.delete(f"/settings/git-credentials/{c}", headers=headers)
            assert resp.status_code == 204


# --- Git repository registry (admin) -------------------------------------

async def test_admin_crud_git_repo(admin):
    _email, _pw, _uid = admin
    token = await _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    async with _client() as client:
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.post(
            "/settings/git-repos",
            headers=headers,
            json={"name": "core", "repo_url": "https://github.com/o/core", "default_branch": "main", "repo_type": "github", "credential_id": None},
        )
        assert resp.status_code == 201, resp.text
        rid = resp.json()["id"]
        assert resp.json()["repo_type"] == "github"
        assert resp.json()["default_branch"] == "main"

        # duplicate repo_url -> 409.
        resp = await client.post(
            "/settings/git-repos",
            headers=headers,
            json={"name": "core-dup", "repo_url": "https://github.com/o/core", "default_branch": "main", "repo_type": "github", "credential_id": None},
        )
        assert resp.status_code == 409, resp.text

        # update repo_type + name.
        resp = await client.put(
            f"/settings/git-repos/{rid}",
            headers=headers,
            json={"name": "core-renamed", "repo_url": "https://github.com/o/core", "default_branch": "release", "repo_type": "gitlab", "credential_id": None},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["repo_type"] == "gitlab"
        assert resp.json()["name"] == "core-renamed"
        assert resp.json()["default_branch"] == "release"

        # 404 on missing repo.
        resp = await client.put(
            "/settings/git-repos/999999",
            headers=headers,
            json={"name": "x", "repo_url": "https://github.com/o/x", "default_branch": "main", "repo_type": "other", "credential_id": None},
        )
        assert resp.status_code == 404

        resp = await client.delete(f"/settings/git-repos/{rid}", headers=headers)
        assert resp.status_code == 204


async def test_delete_git_credential_clears_repo_fk(admin):
    """Deleting a credential must SET NULL the FK on any bound repository."""
    _email, _pw, _uid = admin
    token = await _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    async with _client() as client:
        headers = {"Authorization": f"Bearer {token}"}
        cred = (await client.post(
            "/settings/git-credentials",
            headers=headers,
            json={"auth_type": "ssh", "username": "deploy", "secret": "deploy-key", "readonly": True, "note": ""},
        )).json()
        repo = (await client.post(
            "/settings/git-repos",
            headers=headers,
            json={"name": "svc", "repo_url": "https://github.com/o/svc", "default_branch": "main", "repo_type": "github", "credential_id": cred["id"]},
        )).json()
        assert repo["credential_id"] == cred["id"]

        # delete the credential; the repo's credential_id should drop to NULL.
        resp = await client.delete(f"/settings/git-credentials/{cred['id']}", headers=headers)
        assert resp.status_code == 204

        async with AsyncSessionLocal() as session:
            row = await session.get(GitRepo, repo["id"])
            assert row.credential_id is None

        # cleanup
        await client.delete(f"/settings/git-repos/{repo['id']}", headers=headers)


# --- User management (admin) --------------------------------------------

async def test_admin_user_crud(admin):
    _email, _pw, _uid = admin
    token = await _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    async with _client() as client:
        email = f"created-{uuid.uuid4().hex}@lode.local"
        resp = await client.post(
            "/users",
            headers={"Authorization": f"Bearer {token}"},
            json={"email": email, "name": "New", "role": "user", "password": "new-pass-1"},
        )
        assert resp.status_code == 201, resp.text
        new_id = resp.json()["id"]

        # the new user can log in
        login = await client.post("/auth/login", json={"email": email, "password": "new-pass-1"})
        assert login.status_code == 200

        # admin resets password
        resp = await client.post(
            f"/users/{new_id}/reset-password",
            headers={"Authorization": f"Bearer {token}"},
            json={"password": "reset-pass-1"},
        )
        assert resp.status_code == 200

        # cleanup
        await client.delete(f"/users/{new_id}", headers={"Authorization": f"Bearer {token}"})


async def test_admin_cannot_delete_self(admin):
    _email, _pw, uid = admin
    token = await _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    async with _client() as client:
        resp = await client.delete(f"/users/{uid}", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 409


# --- Self-service password change ---------------------------------------

async def test_change_own_password(user):
    _email, _pw, _uid = user
    token = await _login(USER_EMAIL, USER_PASSWORD)
    async with _client() as client:
        resp = await client.post(
            "/auth/change-password",
            headers={"Authorization": f"Bearer {token}"},
            json={"current_password": USER_PASSWORD, "new_password": "changed-pass-1"},
        )
        assert resp.status_code == 200, resp.text
        # new password works
        login = await client.post(
            "/auth/login", json={"email": USER_EMAIL, "password": "changed-pass-1"}
        )
        assert login.status_code == 200


# --- Invites -------------------------------------------------------------

async def test_deleting_default_promotes_newest_global_model(admin):
    """Deleting the global default promotes a remaining model so analysis keeps working."""
    _email, _pw, _uid = admin
    token = await _login(ADMIN_EMAIL, ADMIN_PASSWORD)

    async with _client() as client:
        headers = {"Authorization": f"Bearer {token}"}

        first = (await client.post("/settings/ai-models", headers=headers, json={
            "provider": "openai", "base_url": "https://api.openai.com/v1",
            "api_key": "openai-key", "model": "gpt-4o", "is_default": True,
        })).json()
        assert first["is_default"] is True
        second = (await client.post("/settings/ai-models", headers=headers, json={
            "provider": "anthropic", "base_url": "https://api.anthropic.com",
            "api_key": "anthropic-key", "model": "claude", "is_default": False,
        })).json()

        resp = await client.delete(f"/settings/ai-models/{first['id']}", headers=headers)
        assert resp.status_code == 204

        rows = {m["id"]: m for m in (await client.get("/settings/ai-models", headers=headers)).json()}
        assert rows[second["id"]]["is_default"] is True

        await client.delete(f"/settings/ai-models/{second['id']}", headers=headers)


async def test_invite_create_and_accept(admin):
    _email, _pw, _uid = admin
    token = await _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    email = f"invited-{uuid.uuid4().hex}@lode.local"
    async with _client() as client:
        resp = await client.post(
            "/invites",
            headers={"Authorization": f"Bearer {token}"},
            json={"email": email},
        )
        assert resp.status_code == 201, resp.text
        token_str = resp.json()["token"]

        # accept (open endpoint, no auth)
        resp = await client.post(
            "/invites/accept",
            json={"token": token_str, "password": "invitee-pass-1", "name": "Invitee"},
        )
        assert resp.status_code == 200, resp.text

        # the accepted account can now log in
        login = await client.post("/auth/login", json={"email": email, "password": "invitee-pass-1"})
        assert login.status_code == 200

        # cleanup the accepted user
        async with AsyncSessionLocal() as session:
            v = (await session.execute(select(User).where(User.email == email))).scalars().first()
            if v is not None:
                await session.delete(v)
                await session.commit()
            inv = (await session.execute(select(Invite).where(Invite.token == token_str))).scalars().first()
            if inv is not None:
                await session.delete(inv)
                await session.commit()
