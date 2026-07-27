from pathlib import Path

from dotenv import dotenv_values

from scripts.production_preflight import format_result, validate_production_environment


REPO_ROOT = Path(__file__).resolve().parents[5]


def _valid_environment() -> dict[str, str]:
    return {
        "LEARNHOUSE_ENV": "production",
        "LEARNHOUSE_DEVELOPMENT_MODE": "False",
        "LEARNHOUSE_DOMAIN": "school.example.edu",
        "LEARNHOUSE_SSL": "True",
        "NEXT_PUBLIC_LEARNHOUSE_HTTPS": "True",
        "NEXTAUTH_URL": "https://school.example.edu",
        "NEXT_PUBLIC_LEARNHOUSE_API_URL": "https://school.example.edu/api/v1/",
        "NEXT_PUBLIC_LEARNHOUSE_BACKEND_URL": "https://school.example.edu/",
        "NEXT_PUBLIC_COLLAB_URL": "wss://school.example.edu/collab",
        "LEARNHOUSE_SQL_CONNECTION_STRING": (
            "postgresql://learnhouse:database-password@db:5432/learnhouse"
        ),
        "LEARNHOUSE_REDIS_CONNECTION_STRING": "redis://redis:6379/learnhouse",
        "LEARNHOUSE_REDIS_URL": "redis://redis:6379",
        "LEARNHOUSE_AUTH_JWT_SECRET_KEY": "jwt-8wK6pQ2zN9mR4sT7vX1cB5dF0hJ3lY",
        "NEXTAUTH_SECRET": "nextauth-7zP3mK9vQ1xN5rT8cD2fG6hJ0lW4",
        "COLLAB_INTERNAL_KEY": "collab-6yN2pR8vK4mT1xQ7cF3hJ9lD5sW0",
        "LEARNHOUSE_IS_AI_ENABLED": "True",
        "LEARNHOUSE_AI_PROVIDER": "openai_compatible",
        "LEARNHOUSE_OPENAI_BASE_URL": "https://ai.example.edu/v1",
        "LEARNHOUSE_OPENAI_API_KEY": "provider-api-key",
        "LEARNHOUSE_OPENAI_MODEL": "school-text-model",
        "LEARNHOUSE_OPENAI_IMAGE_MODEL": "school-image-model",
        "LEARNHOUSE_OPENAI_EMBEDDING_MODEL": "school-embedding-model",
        "LEARNHOUSE_SENTRY_DSN": "https://public@sentry.example.edu/1",
        "LEARNHOUSE_SYSTEM_EMAIL_ADDRESS": "learnhouse@example.edu",
        "LEARNHOUSE_CONTENT_DELIVERY_TYPE": "s3api",
    }


def test_valid_production_environment_passes():
    result = validate_production_environment(_valid_environment())

    assert result.ok
    assert result.errors == ()


def test_weak_secret_blocks_startup_without_echoing_secret():
    env = _valid_environment()
    weak_secret = "change-me-now"
    env["NEXTAUTH_SECRET"] = weak_secret

    result = validate_production_environment(env)
    output = format_result(result)

    assert not result.ok
    assert "NEXTAUTH_SECRET" in output
    assert weak_secret not in output


def test_enabled_openai_provider_requires_all_model_capabilities():
    env = _valid_environment()
    env.pop("LEARNHOUSE_OPENAI_BASE_URL")
    env.pop("LEARNHOUSE_OPENAI_IMAGE_MODEL")
    env.pop("LEARNHOUSE_OPENAI_EMBEDDING_MODEL")

    result = validate_production_environment(env)
    output = format_result(result)

    assert not result.ok
    assert "LEARNHOUSE_OPENAI_BASE_URL" in output
    assert "LEARNHOUSE_OPENAI_IMAGE_MODEL" in output
    assert "LEARNHOUSE_OPENAI_EMBEDDING_MODEL" in output


def test_initial_admin_password_is_blocked_after_bootstrap():
    env = _valid_environment()
    env["LEARNHOUSE_INITIAL_ADMIN_EMAIL"] = "admin@example.edu"
    env["LEARNHOUSE_INITIAL_ADMIN_PASSWORD"] = "TemporaryAdminPassword-4839"

    result = validate_production_environment(env)

    assert not result.ok
    assert any(
        "LEARNHOUSE_INITIAL_ADMIN_PASSWORD" in message for message in result.errors
    )


def test_explicit_one_time_bootstrap_accepts_a_strong_password_with_warning():
    env = _valid_environment()
    env["LEARNHOUSE_BOOTSTRAP_ADMIN"] = "True"
    env["LEARNHOUSE_INITIAL_ADMIN_EMAIL"] = "admin@example.edu"
    env["LEARNHOUSE_INITIAL_ADMIN_PASSWORD"] = "TemporaryAdminPassword-4839"

    result = validate_production_environment(env)

    assert result.ok
    assert any("bootstrap" in warning.lower() for warning in result.warnings)


def test_public_endpoints_must_use_encrypted_production_schemes():
    env = _valid_environment()
    env["NEXTAUTH_URL"] = "http://school.example.edu"
    env["NEXT_PUBLIC_COLLAB_URL"] = "ws://school.example.edu/collab"

    result = validate_production_environment(env)
    output = format_result(result)

    assert not result.ok
    assert "NEXTAUTH_URL" in output
    assert "NEXT_PUBLIC_COLLAB_URL" in output


def test_production_example_cannot_pass_until_placeholders_are_replaced():
    values = {
        key: value or ""
        for key, value in dotenv_values(REPO_ROOT / ".env.production.example").items()
    }

    result = validate_production_environment(values)
    output = format_result(result)

    assert not result.ok
    assert "LEARNHOUSE_AUTH_JWT_SECRET_KEY" in output
    assert "LEARNHOUSE_OPENAI_API_KEY" in output
    assert "LEARNHOUSE_OPENAI_MODEL" in output
