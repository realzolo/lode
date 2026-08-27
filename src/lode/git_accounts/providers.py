"""Bounded provider adapters for GitHub, GitLab, and Gitee repository catalogues."""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from lode.evidence_connectors.transport import BoundedHTTPTransport
from lode.evidence_connectors.types import ProviderExecutionError
from lode.infrastructure.provider_http import validate_provider_endpoint

_MAX_PAGES = 20
_REQUEST_TIMEOUT_SECONDS = 15
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class GitProviderError(RuntimeError):
    """A provider failure that is suitable to present as a stable API error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class GitProviderProfile:
    external_id: str
    login: str
    account_url: str


@dataclass(frozen=True, slots=True)
class GitProviderRepository:
    external_id: str
    name: str
    full_name: str
    clone_url: str
    web_url: str
    default_branch: str
    visibility: str
    archived: bool
    pushed_at: datetime | None


def default_provider_urls(kind: str) -> tuple[str, str]:
    values = {
        "github": ("https://github.com", "https://api.github.com"),
        "gitlab": ("https://gitlab.com", "https://gitlab.com/api/v4"),
        "gitee": ("https://gitee.com", "https://gitee.com/api/v5"),
    }
    try:
        return values[kind]
    except KeyError as exc:
        raise ValueError("unsupported Git provider") from exc


def validate_git_provider_url(value: str) -> str:
    return validate_provider_endpoint(value)


async def authenticate_access_token(
    *, kind: str, api_url: str, token: str
) -> GitProviderProfile:
    response = await _request(kind, api_url, "GET", _profile_path(kind), token=token)
    return _profile(kind, _json_object(response))


async def list_repositories(
    *, kind: str, api_url: str, token: str, auth_mode: str
) -> tuple[GitProviderRepository, ...]:
    if kind == "github":
        path = "/installation/repositories" if auth_mode == "github_app" else "/user/repos"
        query = {"per_page": "100"}
        if auth_mode != "github_app":
            query["affiliation"] = "owner,collaborator,organization_member"
    elif kind == "gitlab":
        path = "/projects"
        query = {"membership": "true", "simple": "true", "per_page": "100"}
    elif kind == "gitee":
        path = "/user/repos"
        query = {"per_page": "100", "sort": "updated"}
    else:  # pragma: no cover - schema and instance constraints prevent this
        raise ValueError("unsupported Git provider")

    results: list[GitProviderRepository] = []
    for page in range(1, _MAX_PAGES + 1):
        response = await _request(
            kind,
            api_url,
            "GET",
            path,
            token=token,
            query={**query, "page": str(page)},
        )
        payload = _json_value(response)
        if kind == "github" and auth_mode == "github_app":
            if not isinstance(payload, dict) or not isinstance(payload.get("repositories"), list):
                raise GitProviderError("invalid_response", "GitHub repository list is invalid")
            values = payload["repositories"]
        else:
            if not isinstance(payload, list):
                raise GitProviderError("invalid_response", "Git repository list is invalid")
            values = payload
        results.extend(_repository(kind, value) for value in values if isinstance(value, dict))
        if len(values) < 100:
            break
    else:
        raise GitProviderError("catalog_limit_exceeded", "Git account repository catalogue is too large")
    return tuple(results)


async def github_app_installation_token(
    *, api_url: str, app_id: str, private_key_pem: str, installation_id: str
) -> tuple[GitProviderProfile, str, datetime]:
    jwt = _github_app_jwt(app_id, private_key_pem)
    headers = {"accept": "application/vnd.github+json", "authorization": f"Bearer {jwt}"}
    installation = _json_object(
        await _request_raw(api_url, "GET", f"/app/installations/{quote(installation_id, safe='')}", headers=headers)
    )
    account = installation.get("account")
    if not isinstance(account, dict):
        raise GitProviderError("invalid_response", "GitHub App installation account is invalid")
    profile = _profile("github", account)
    token_body = _json_object(
        await _request_raw(
            api_url,
            "POST",
            f"/app/installations/{quote(installation_id, safe='')}/access_tokens",
            headers=headers,
        )
    )
    token = token_body.get("token")
    expires_at = _parse_time(token_body.get("expires_at"))
    if not isinstance(token, str) or not token or expires_at is None:
        raise GitProviderError("invalid_response", "GitHub App installation token is invalid")
    return profile, token, expires_at


def oauth_authorization_url(
    *, kind: str, base_url: str, client_id: str, redirect_uri: str, state: str
) -> str:
    origin = validate_git_provider_url(base_url)
    if kind == "gitlab":
        path = "/oauth/authorize"
        scope = "read_api read_repository"
    elif kind == "gitee":
        path = "/oauth/authorize"
        scope = "projects user_info"
    else:
        raise ValueError("OAuth is supported only for GitLab and Gitee")
    return f"{origin}{path}?{urlencode({'client_id': client_id, 'redirect_uri': redirect_uri, 'response_type': 'code', 'scope': scope, 'state': state})}"


async def exchange_oauth_code(
    *, kind: str, base_url: str, client_id: str, client_secret: str, redirect_uri: str, code: str
) -> str:
    origin = validate_git_provider_url(base_url)
    if kind not in {"gitlab", "gitee"}:
        raise ValueError("OAuth is supported only for GitLab and Gitee")
    endpoint = f"{origin}/oauth/token"
    try:
        async with httpx.AsyncClient(
            timeout=_REQUEST_TIMEOUT_SECONDS,
            follow_redirects=False,
            verify=True,
            trust_env=False,
        ) as client:
            response = await client.post(
                endpoint,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                },
            )
    except httpx.TimeoutException as exc:
        raise GitProviderError("provider_timeout", "Git provider timed out") from exc
    except httpx.HTTPError as exc:
        raise GitProviderError("provider_unavailable", "Git provider request failed") from exc
    if response.status_code in {401, 403}:
        raise GitProviderError("authentication_failed", "Git provider rejected the authorization code")
    if not 200 <= response.status_code < 300:
        raise GitProviderError("oauth_exchange_failed", "Git provider could not exchange the authorization code")
    if len(response.content) > _MAX_RESPONSE_BYTES:
        raise GitProviderError("invalid_response", "Git provider OAuth response is too large")
    try:
        payload = response.json()
    except ValueError as exc:
        raise GitProviderError("invalid_response", "Git provider OAuth response is invalid") from exc
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token:
        raise GitProviderError("oauth_exchange_failed", "Git provider did not return an access token")
    return token


def _profile_path(kind: str) -> str:
    return "/user"


async def _request(
    kind: str,
    api_url: str,
    method: str,
    path: str,
    *,
    token: str,
    query: Mapping[str, str] | None = None,
) -> bytes:
    return await _request_raw(
        api_url,
        method,
        path,
        headers=_headers(kind, token),
        query=query,
    )


async def _request_raw(
    api_url: str,
    method: str,
    path: str,
    *,
    headers: Mapping[str, str],
    query: Mapping[str, str] | None = None,
) -> bytes:
    validated = validate_git_provider_url(api_url)
    parsed = urlsplit(validated)
    origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    prefix = parsed.path.rstrip("/")
    try:
        transport = BoundedHTTPTransport(
            base_url=origin,
            headers=headers,
            max_response_bytes=_MAX_RESPONSE_BYTES,
            max_timeout_ms=int(_REQUEST_TIMEOUT_SECONDS * 1_000),
        )
        response = await transport.request(
            method,
            f"{prefix}{path}",
            query=query,
            timeout_ms=int(_REQUEST_TIMEOUT_SECONDS * 1_000),
        )
    except ProviderExecutionError as exc:
        raise GitProviderError(exc.code, "Git provider request failed") from exc
    if response.status_code in {401, 403}:
        raise GitProviderError("authentication_failed", "Git provider rejected the account credential")
    if response.status_code == 429:
        raise GitProviderError("rate_limited", "Git provider rate limit was reached")
    if response.status_code >= 500:
        raise GitProviderError("provider_unavailable", "Git provider is unavailable")
    if not 200 <= response.status_code < 300:
        raise GitProviderError("invalid_response", "Git provider rejected the request")
    return response.body


def _headers(kind: str, token: str) -> dict[str, str]:
    headers = {"accept": "application/json", "authorization": f"Bearer {token}"}
    if kind == "github":
        headers["accept"] = "application/vnd.github+json"
    elif kind == "gitlab":
        headers = {"accept": "application/json", "private-token": token}
    return headers


def _json_value(payload: bytes) -> object:
    try:
        return json.loads(payload)
    except (TypeError, json.JSONDecodeError) as exc:
        raise GitProviderError("invalid_response", "Git provider response is not JSON") from exc


def _json_object(payload: bytes) -> dict[str, Any]:
    value = _json_value(payload)
    if not isinstance(value, dict):
        raise GitProviderError("invalid_response", "Git provider response is invalid")
    return value


def _profile(kind: str, payload: Mapping[str, Any]) -> GitProviderProfile:
    external_id = payload.get("id")
    login = payload.get("login") if kind != "gitlab" else payload.get("username")
    account_url = payload.get("html_url") if kind != "gitlab" else payload.get("web_url")
    if external_id is None or not isinstance(login, str) or not login or not isinstance(account_url, str):
        raise GitProviderError("invalid_response", "Git provider account response is invalid")
    return GitProviderProfile(str(external_id), login, account_url)


def _repository(kind: str, payload: Mapping[str, Any]) -> GitProviderRepository:
    if kind == "github":
        external_id = payload.get("id")
        full_name = payload.get("full_name")
        clone_url = payload.get("clone_url")
        web_url = payload.get("html_url")
        visibility = payload.get("visibility") or ("private" if payload.get("private") else "public")
        archived = bool(payload.get("archived", False))
        pushed_at = _parse_time(payload.get("pushed_at"))
    elif kind == "gitlab":
        external_id = payload.get("id")
        full_name = payload.get("path_with_namespace")
        clone_url = payload.get("http_url_to_repo")
        web_url = payload.get("web_url")
        visibility = payload.get("visibility") or "private"
        archived = bool(payload.get("archived", False))
        pushed_at = _parse_time(payload.get("last_activity_at"))
    else:
        external_id = payload.get("id")
        full_name = payload.get("full_name") or payload.get("path")
        clone_url = payload.get("clone_url")
        web_url = payload.get("html_url")
        visibility = "private" if payload.get("private", True) else "public"
        archived = bool(payload.get("archived", False))
        pushed_at = _parse_time(payload.get("updated_at"))
    name = payload.get("name")
    branch = payload.get("default_branch") or "main"
    if (
        external_id is None
        or not isinstance(name, str)
        or not name
        or not isinstance(full_name, str)
        or not full_name
        or not isinstance(clone_url, str)
        or not clone_url
        or not isinstance(web_url, str)
        or not web_url
        or visibility not in {"public", "private", "internal"}
    ):
        raise GitProviderError("invalid_response", "Git repository response is invalid")
    return GitProviderRepository(
        external_id=str(external_id),
        name=name,
        full_name=full_name,
        clone_url=validate_git_provider_url(clone_url),
        web_url=validate_git_provider_url(web_url),
        default_branch=branch,
        visibility=visibility,
        archived=archived,
        pushed_at=pushed_at,
    )


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC)


def _github_app_jwt(app_id: str, private_key_pem: str) -> str:
    try:
        key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    except (TypeError, ValueError) as exc:
        raise GitProviderError("github_app_key_invalid", "GitHub App private key is invalid") from exc
    now = int(time.time())
    header = _base64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = _base64url(
        json.dumps({"iat": now - 60, "exp": now + 540, "iss": app_id}, separators=(",", ":")).encode()
    )
    signed = f"{header}.{claims}".encode()
    try:
        signature = key.sign(signed, padding.PKCS1v15(), hashes.SHA256())
    except AttributeError as exc:
        raise GitProviderError("github_app_key_invalid", "GitHub App key must be RSA") from exc
    return f"{header}.{claims}.{_base64url(signature)}"


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()
