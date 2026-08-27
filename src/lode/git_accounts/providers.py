"""Registered, token-only Git account adapters and repository catalogues."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from lode.evidence_connectors.transport import BoundedHTTPTransport
from lode.evidence_connectors.types import ProviderExecutionError
from lode.infrastructure.provider_http import validate_provider_endpoint

_MAX_PAGES = 20
_REQUEST_TIMEOUT_MS = 15_000
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class GitProviderError(RuntimeError):
    """A provider failure that maps to a stable control-plane error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class GitAdapter:
    id: str
    display_name: str
    official_api_url: str
    custom_endpoint_allowed: bool


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


_ADAPTERS = MappingProxyType(
    {
        "github": GitAdapter("github", "GitHub", "https://api.github.com", True),
        "gitlab": GitAdapter("gitlab", "GitLab", "https://gitlab.com/api/v4", True),
        "gitee": GitAdapter("gitee", "Gitee", "https://gitee.com/api/v5", False),
    }
)


def registered_adapters() -> tuple[GitAdapter, ...]:
    return tuple(_ADAPTERS.values())


def require_adapter(adapter_id: str) -> GitAdapter:
    try:
        return _ADAPTERS[adapter_id]
    except KeyError as exc:
        raise ValueError("unsupported Git adapter") from exc


def resolve_api_url(adapter_id: str, api_url: str | None) -> str:
    adapter = require_adapter(adapter_id)
    if api_url is None or not api_url.strip():
        return adapter.official_api_url
    if not adapter.custom_endpoint_allowed:
        raise ValueError(f"{adapter.display_name} does not support a custom API endpoint")
    return validate_git_provider_url(api_url)


def endpoint_identity_hash(adapter_id: str, api_url: str) -> str:
    value = f"{require_adapter(adapter_id).id}\n{validate_git_provider_url(api_url)}"
    return hashlib.sha256(value.encode()).hexdigest()


def validate_git_provider_url(value: str) -> str:
    return validate_provider_endpoint(value)


async def authenticate_access_token(*, adapter_id: str, api_url: str, token: str) -> GitProviderProfile:
    require_adapter(adapter_id)
    response = await _request(adapter_id, api_url, "GET", "/user", token=token)
    return _profile(adapter_id, _json_object(response))


async def list_repositories(*, adapter_id: str, api_url: str, token: str) -> tuple[GitProviderRepository, ...]:
    require_adapter(adapter_id)
    if adapter_id == "github":
        path, query = "/user/repos", {"per_page": "100", "affiliation": "owner,collaborator,organization_member"}
    elif adapter_id == "gitlab":
        path, query = "/projects", {"membership": "true", "simple": "true", "per_page": "100"}
    else:
        path, query = "/user/repos", {"per_page": "100", "sort": "updated"}

    results: list[GitProviderRepository] = []
    for page in range(1, _MAX_PAGES + 1):
        payload = _json_value(
            await _request(adapter_id, api_url, "GET", path, token=token, query={**query, "page": str(page)})
        )
        if not isinstance(payload, list):
            raise GitProviderError("invalid_response", "Git repository list is invalid")
        results.extend(_repository(adapter_id, value) for value in payload if isinstance(value, dict))
        if len(payload) < 100:
            return tuple(results)
    raise GitProviderError("catalog_limit_exceeded", "Git account repository catalogue is too large")


async def _request(
    adapter_id: str,
    api_url: str,
    method: str,
    path: str,
    *,
    token: str,
    query: Mapping[str, str] | None = None,
) -> bytes:
    validated = validate_git_provider_url(api_url)
    parsed = urlsplit(validated)
    origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    prefix = parsed.path.rstrip("/")
    try:
        transport = BoundedHTTPTransport(
            base_url=origin,
            headers=_headers(adapter_id, token),
            max_response_bytes=_MAX_RESPONSE_BYTES,
            max_timeout_ms=_REQUEST_TIMEOUT_MS,
        )
        response = await transport.request(method, f"{prefix}{path}", query=query, timeout_ms=_REQUEST_TIMEOUT_MS)
    except ProviderExecutionError as exc:
        raise GitProviderError(exc.code, "Git provider request failed") from exc
    if response.status_code in {401, 403}:
        raise GitProviderError("authentication_failed", "Git provider rejected the access token")
    if response.status_code == 429:
        raise GitProviderError("rate_limited", "Git provider rate limit was reached")
    if response.status_code >= 500:
        raise GitProviderError("provider_unavailable", "Git provider is unavailable")
    if not 200 <= response.status_code < 300:
        raise GitProviderError("invalid_response", "Git provider rejected the request")
    return response.body


def _headers(adapter_id: str, token: str) -> dict[str, str]:
    if adapter_id == "github":
        return {"accept": "application/vnd.github+json", "authorization": f"Bearer {token}"}
    if adapter_id == "gitlab":
        return {"accept": "application/json", "private-token": token}
    return {"accept": "application/json", "authorization": f"Bearer {token}"}


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


def _profile(adapter_id: str, payload: Mapping[str, Any]) -> GitProviderProfile:
    external_id = payload.get("id")
    login = payload.get("username") if adapter_id == "gitlab" else payload.get("login")
    account_url = payload.get("web_url") if adapter_id == "gitlab" else payload.get("html_url")
    if external_id is None or not isinstance(login, str) or not login or not isinstance(account_url, str):
        raise GitProviderError("invalid_response", "Git provider account response is invalid")
    return GitProviderProfile(str(external_id), login, account_url)


def _repository(adapter_id: str, payload: Mapping[str, Any]) -> GitProviderRepository:
    if adapter_id == "github":
        external_id, full_name = payload.get("id"), payload.get("full_name")
        clone_url, web_url = payload.get("clone_url"), payload.get("html_url")
        visibility = payload.get("visibility") or ("private" if payload.get("private") else "public")
        archived, pushed_at = bool(payload.get("archived", False)), _parse_time(payload.get("pushed_at"))
    elif adapter_id == "gitlab":
        external_id, full_name = payload.get("id"), payload.get("path_with_namespace")
        clone_url, web_url = payload.get("http_url_to_repo"), payload.get("web_url")
        visibility = payload.get("visibility") or "private"
        archived, pushed_at = bool(payload.get("archived", False)), _parse_time(payload.get("last_activity_at"))
    else:
        external_id, full_name = payload.get("id"), payload.get("full_name") or payload.get("path")
        clone_url, web_url = payload.get("clone_url"), payload.get("html_url")
        visibility = "private" if payload.get("private", True) else "public"
        archived, pushed_at = bool(payload.get("archived", False)), _parse_time(payload.get("updated_at"))
    name, branch = payload.get("name"), payload.get("default_branch") or "main"
    if not all((external_id is not None, isinstance(name, str) and name, isinstance(full_name, str) and full_name, isinstance(clone_url, str) and clone_url, isinstance(web_url, str) and web_url, visibility in {"public", "private", "internal"})):
        raise GitProviderError("invalid_response", "Git repository response is invalid")
    return GitProviderRepository(str(external_id), name, full_name, validate_git_provider_url(clone_url), validate_git_provider_url(web_url), branch, visibility, archived, pushed_at)


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None
