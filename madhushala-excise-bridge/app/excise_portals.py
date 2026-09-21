"""Config-driven Excise portal registry.

Adding another state should normally require only a registry/config change, not a
Chrome extension release. State-specific CSS selectors may be supplied in the
registry when the generic login selectors are not sufficient.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


DEFAULT_REGISTRY_PATH = Path(__file__).with_name("excise_portals.json")

DEFAULT_LOGIN_PROFILE: dict[str, Any] = {
    "usernameSelectors": [
        'input[name*="UserName" i]',
        'input[id*="UserName" i]',
        'input[name*="User" i]',
        'input[id*="User" i]',
        'input[name*="Login" i]',
        'input[id*="Login" i]',
        'input[name*="userid" i]',
        'input[id*="userid" i]',
        'input[autocomplete="username"]',
        'input[type="text"]',
    ],
    "passwordSelectors": [
        'input[type="password"]',
        'input[name*="Password" i]',
        'input[id*="Password" i]',
        'input[name*="pwd" i]',
        'input[id*="pwd" i]',
        'input[autocomplete="current-password"]',
    ],
    "captchaSelectors": [
        'input[name*="captcha" i]',
        'input[id*="captcha" i]',
        'input[name*="capcha" i]',
        'input[id*="capcha" i]',
        'input[name*="verification" i]',
        'input[id*="verification" i]',
    ],
    "loginSelectors": [
        'button[type="submit"]',
        'input[type="submit"]',
        'button[id*="login" i]',
        'input[id*="login" i]',
        'button[name*="login" i]',
        'input[name*="login" i]',
    ],
    "loginText": ["login", "log in", "sign in", "submit"],
    "autoSubmit": True,
}


def _normalise_state(value: str | None) -> str:
    return " ".join(str(value or "").strip().upper().replace("_", " ").split())


def _validate_https_url(value: str, *, field: str) -> str:
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"{field} must be an absolute HTTPS URL")
    return parsed.geturl()


def _registry_path() -> Path:
    configured = str(os.getenv("EXCISE_PORTAL_REGISTRY_PATH", "")).strip()
    return Path(configured) if configured else DEFAULT_REGISTRY_PATH


def load_excise_portal_registry(path: Path | None = None) -> dict[str, Any]:
    source = path or _registry_path()
    data = json.loads(source.read_text(encoding="utf-8"))
    states = data.get("states")
    if not isinstance(states, dict):
        raise RuntimeError("Excise portal registry must contain a 'states' object")
    return states


def resolve_excise_portal(state: str | None, *, registry: dict[str, Any] | None = None) -> dict[str, Any] | None:
    raw_state = _normalise_state(state)
    states = registry if registry is not None else load_excise_portal_registry()

    canonical = ""
    config: dict[str, Any] | None = None
    for name, candidate in states.items():
        if not isinstance(candidate, dict):
            continue
        canonical_name = _normalise_state(name)
        aliases = {_normalise_state(alias) for alias in candidate.get("aliases", [])}
        aliases.add(canonical_name)
        if raw_state in aliases:
            canonical = canonical_name
            config = candidate
            break

    if config is None:
        return None

    login_url = _validate_https_url(str(config.get("loginUrl") or ""), field="loginUrl")
    parsed = urlparse(login_url)
    default_origin = f"{parsed.scheme}://{parsed.netloc}"

    allowed_origins: list[str] = []
    for origin in config.get("allowedOrigins") or [default_origin]:
        validated = _validate_https_url(str(origin), field="allowedOrigins")
        parsed_origin = urlparse(validated)
        allowed_origins.append(f"{parsed_origin.scheme}://{parsed_origin.netloc}")

    profile = dict(DEFAULT_LOGIN_PROFILE)
    for key in (
        "usernameSelectors",
        "passwordSelectors",
        "captchaSelectors",
        "loginSelectors",
        "loginText",
    ):
        configured_value = config.get(key)
        if isinstance(configured_value, list) and configured_value:
            profile[key] = [str(item) for item in configured_value if str(item).strip()]
    profile["autoSubmit"] = bool(config.get("autoSubmit", DEFAULT_LOGIN_PROFILE["autoSubmit"]))
    profile["allowedOrigins"] = sorted(set(allowed_origins))

    return {
        "state": canonical,
        "loginUrl": login_url,
        "loginProfile": profile,
    }
