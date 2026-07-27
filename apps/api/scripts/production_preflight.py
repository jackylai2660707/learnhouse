#!/usr/bin/env python3
"""Fail-closed production configuration validation for LearnHouse."""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlsplit

from dotenv import dotenv_values


TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}
PRODUCTION_ENVIRONMENTS = {"prod", "production"}
PLACEHOLDER_MARKERS = (
    "change-me",
    "change_this",
    "changeme",
    "example",
    "placeholder",
    "replace-me",
    "replace_this",
)
RESERVED_PUBLIC_TLDS = {"example", "invalid", "localhost", "test"}


@dataclass(frozen=True)
class PreflightResult:
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def _value(env: Mapping[str, str], name: str) -> str:
    return env.get(name, "").strip()


def _is_true(value: str) -> bool:
    return value.strip().lower() in TRUE_VALUES


def _is_false(value: str) -> bool:
    return value.strip().lower() in FALSE_VALUES


def _looks_like_placeholder(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", value.lower())
    return (
        "<" in value
        or ">" in value
        or any(
            marker.replace("-", "").replace("_", "") in normalized
            for marker in PLACEHOLDER_MARKERS
        )
    )


def _require(
    env: Mapping[str, str],
    name: str,
    errors: list[str],
    *,
    guidance: str,
) -> str:
    value = _value(env, name)
    if not value:
        errors.append(f"{name} is required. {guidance}")
    return value


def _validate_secret(
    env: Mapping[str, str],
    name: str,
    errors: list[str],
    *,
    minimum_length: int = 32,
) -> None:
    secret = _value(env, name)
    if not secret:
        errors.append(f"{name} is required and must be randomly generated.")
        return

    has_low_variety = len(set(secret)) < 8
    if (
        len(secret) < minimum_length
        or _looks_like_placeholder(secret)
        or has_low_variety
    ):
        errors.append(
            f"{name} is weak. Use a randomly generated value of at least "
            f"{minimum_length} characters."
        )


def _validate_url(
    name: str,
    value: str,
    errors: list[str],
    *,
    schemes: set[str],
    require_hostname: bool = True,
) -> None:
    if not value:
        return
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        errors.append(f"{name} is not a valid URL.")
        return

    if parsed.scheme.lower() not in schemes:
        allowed = ", ".join(sorted(schemes))
        errors.append(f"{name} must use one of these schemes: {allowed}.")
    if require_hostname and not parsed.hostname:
        errors.append(f"{name} must include a hostname.")
    if "<" in value or ">" in value:
        errors.append(f"{name} still contains a placeholder value.")
    if port is not None and not 1 <= port <= 65535:
        errors.append(f"{name} contains an invalid port.")


def _validate_public_endpoints(env: Mapping[str, str], errors: list[str]) -> None:
    domain = _require(
        env,
        "LEARNHOUSE_DOMAIN",
        errors,
        guidance="Set the public school platform hostname without a URL scheme.",
    )
    if domain:
        try:
            parsed_domain = urlsplit(f"//{domain}")
        except ValueError:
            parsed_domain = None
        if (
            parsed_domain is None
            or not parsed_domain.hostname
            or parsed_domain.path not in ("", "/")
            or parsed_domain.username
            or parsed_domain.password
        ):
            errors.append(
                "LEARNHOUSE_DOMAIN must be a hostname, optionally with a port, "
                "and must not contain credentials or a path."
            )
        elif parsed_domain.hostname.lower() in {"localhost", "127.0.0.1", "::1"}:
            errors.append(
                "LEARNHOUSE_DOMAIN must not use a localhost address in production."
            )
        elif parsed_domain.hostname.rsplit(".", 1)[-1].lower() in RESERVED_PUBLIC_TLDS:
            errors.append("LEARNHOUSE_DOMAIN must use a real production hostname.")

    ssl = _require(
        env,
        "LEARNHOUSE_SSL",
        errors,
        guidance="Set it to True when HTTPS terminates at the reverse proxy.",
    )
    if ssl and not _is_true(ssl):
        errors.append("LEARNHOUSE_SSL must be True in production.")

    public_https = _require(
        env,
        "NEXT_PUBLIC_LEARNHOUSE_HTTPS",
        errors,
        guidance="Set it to True for browser-facing production URLs.",
    )
    if public_https and not _is_true(public_https):
        errors.append("NEXT_PUBLIC_LEARNHOUSE_HTTPS must be True in production.")

    endpoint_schemes = {
        "NEXTAUTH_URL": {"https"},
        "NEXT_PUBLIC_LEARNHOUSE_API_URL": {"https"},
        "NEXT_PUBLIC_LEARNHOUSE_BACKEND_URL": {"https"},
        "NEXT_PUBLIC_COLLAB_URL": {"wss"},
    }
    for name, schemes in endpoint_schemes.items():
        endpoint = _require(
            env,
            name,
            errors,
            guidance="Configure the public HTTPS/WSS endpoint used by the web client.",
        )
        _validate_url(name, endpoint, errors, schemes=schemes)

    nextauth_url = _value(env, "NEXTAUTH_URL")
    if domain and nextauth_url:
        try:
            domain_host = urlsplit(f"//{domain}").hostname
            nextauth_host = urlsplit(nextauth_url).hostname
        except ValueError:
            domain_host = None
            nextauth_host = None
        if (
            domain_host
            and nextauth_host
            and domain_host.lower() != nextauth_host.lower()
        ):
            errors.append("NEXTAUTH_URL hostname must match LEARNHOUSE_DOMAIN.")


def _validate_data_services(env: Mapping[str, str], errors: list[str]) -> None:
    database_url = _require(
        env,
        "LEARNHOUSE_SQL_CONNECTION_STRING",
        errors,
        guidance="Use a PostgreSQL connection string for the production database.",
    )
    _validate_url(
        "LEARNHOUSE_SQL_CONNECTION_STRING",
        database_url,
        errors,
        schemes={"postgres", "postgresql", "postgresql+asyncpg", "postgresql+psycopg2"},
    )

    redis_url = _require(
        env,
        "LEARNHOUSE_REDIS_CONNECTION_STRING",
        errors,
        guidance="Configure the API Redis database.",
    )
    _validate_url(
        "LEARNHOUSE_REDIS_CONNECTION_STRING",
        redis_url,
        errors,
        schemes={"redis", "rediss"},
    )

    collab_redis_url = _require(
        env,
        "LEARNHOUSE_REDIS_URL",
        errors,
        guidance="Configure Redis for the collaboration service.",
    )
    _validate_url(
        "LEARNHOUSE_REDIS_URL",
        collab_redis_url,
        errors,
        schemes={"redis", "rediss"},
    )


def _validate_ai(env: Mapping[str, str], errors: list[str]) -> None:
    if not _is_true(_value(env, "LEARNHOUSE_IS_AI_ENABLED")):
        return

    provider = _require(
        env,
        "LEARNHOUSE_AI_PROVIDER",
        errors,
        guidance="Use gemini or openai_compatible.",
    ).lower()
    if provider not in {"gemini", "openai_compatible"}:
        errors.append("LEARNHOUSE_AI_PROVIDER must be gemini or openai_compatible.")
        return

    if provider == "gemini":
        api_key = _require(
            env,
            "LEARNHOUSE_GEMINI_API_KEY",
            errors,
            guidance="Set the Gemini API key or disable AI.",
        )
        if api_key and _looks_like_placeholder(api_key):
            errors.append(
                "LEARNHOUSE_GEMINI_API_KEY still contains a placeholder value."
            )
        return

    base_url = _require(
        env,
        "LEARNHOUSE_OPENAI_BASE_URL",
        errors,
        guidance="Set the OpenAI-compatible /v1 endpoint.",
    )
    _validate_url("LEARNHOUSE_OPENAI_BASE_URL", base_url, errors, schemes={"https"})
    _require(
        env,
        "LEARNHOUSE_OPENAI_API_KEY",
        errors,
        guidance="Set the provider API key or disable AI.",
    )
    api_key = _value(env, "LEARNHOUSE_OPENAI_API_KEY")
    if api_key and _looks_like_placeholder(api_key):
        errors.append("LEARNHOUSE_OPENAI_API_KEY still contains a placeholder value.")
    for name in (
        "LEARNHOUSE_OPENAI_MODEL",
        "LEARNHOUSE_OPENAI_IMAGE_MODEL",
        "LEARNHOUSE_OPENAI_EMBEDDING_MODEL",
    ):
        model = _require(
            env,
            name,
            errors,
            guidance="Set an explicit production model identifier.",
        )
        if model and _looks_like_placeholder(model):
            errors.append(f"{name} still contains a placeholder value.")


def _validate_bootstrap(
    env: Mapping[str, str], errors: list[str], warnings: list[str]
) -> None:
    bootstrap_enabled = _is_true(_value(env, "LEARNHOUSE_BOOTSTRAP_ADMIN"))
    bootstrap_password = _value(env, "LEARNHOUSE_INITIAL_ADMIN_PASSWORD")

    if bootstrap_password and not bootstrap_enabled:
        errors.append(
            "LEARNHOUSE_INITIAL_ADMIN_PASSWORD is still configured. Remove it after "
            "installation, or explicitly set LEARNHOUSE_BOOTSTRAP_ADMIN=True only for "
            "the first controlled bootstrap."
        )
    if bootstrap_enabled:
        _require(
            env,
            "LEARNHOUSE_INITIAL_ADMIN_EMAIL",
            errors,
            guidance="Set the one-time administrator email.",
        )
        _validate_secret(
            env,
            "LEARNHOUSE_INITIAL_ADMIN_PASSWORD",
            errors,
            minimum_length=12,
        )
        warnings.append(
            "Administrator bootstrap is enabled. Disable it and remove the initial "
            "password immediately after installation."
        )
    elif _value(env, "LEARNHOUSE_INITIAL_ADMIN_EMAIL"):
        warnings.append(
            "LEARNHOUSE_INITIAL_ADMIN_EMAIL is no longer needed after installation."
        )


def validate_production_environment(env: Mapping[str, str]) -> PreflightResult:
    errors: list[str] = []
    warnings: list[str] = []

    environment = _require(
        env,
        "LEARNHOUSE_ENV",
        errors,
        guidance="Set it to production.",
    ).lower()
    if environment and environment not in PRODUCTION_ENVIRONMENTS:
        errors.append("LEARNHOUSE_ENV must be production for this startup path.")

    development_mode = _require(
        env,
        "LEARNHOUSE_DEVELOPMENT_MODE",
        errors,
        guidance="Set it to False in production.",
    )
    if development_mode and not _is_false(development_mode):
        errors.append("LEARNHOUSE_DEVELOPMENT_MODE must be False in production.")

    _validate_public_endpoints(env, errors)
    _validate_data_services(env, errors)
    _validate_secret(env, "LEARNHOUSE_AUTH_JWT_SECRET_KEY", errors)
    _validate_secret(env, "NEXTAUTH_SECRET", errors)
    _validate_secret(env, "COLLAB_INTERNAL_KEY", errors)
    _validate_bootstrap(env, errors, warnings)
    _validate_ai(env, errors)

    if not _value(env, "LEARNHOUSE_SENTRY_DSN"):
        warnings.append(
            "LEARNHOUSE_SENTRY_DSN is not configured; error monitoring is disabled."
        )
    if not _value(env, "LEARNHOUSE_SYSTEM_EMAIL_ADDRESS"):
        warnings.append(
            "System email is not configured; outbound notifications may be unavailable."
        )
    if _value(env, "LEARNHOUSE_CONTENT_DELIVERY_TYPE").lower() in {"", "filesystem"}:
        warnings.append(
            "Filesystem content storage requires a tested persistent-volume backup."
        )

    return PreflightResult(tuple(errors), tuple(warnings))


def format_result(result: PreflightResult) -> str:
    lines: list[str] = []
    if result.errors:
        lines.append("Production preflight failed:")
        lines.extend(
            f"  {index}. {message}" for index, message in enumerate(result.errors, 1)
        )
    else:
        lines.append("Production preflight passed.")
    if result.warnings:
        lines.append("Warnings:")
        lines.extend(f"  - {message}" for message in result.warnings)
    return "\n".join(lines)


def _load_environment(env_file: str | None) -> Mapping[str, str]:
    if not env_file:
        return os.environ
    file_values = {key: value or "" for key, value in dotenv_values(env_file).items()}
    file_values.update(os.environ)
    return file_values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        help="Optional dotenv file for an operator-run validation.",
    )
    args = parser.parse_args()
    result = validate_production_environment(_load_environment(args.env_file))
    print(format_result(result))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
