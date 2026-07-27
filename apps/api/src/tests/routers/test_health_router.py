"""Router tests for API liveness/readiness and legacy health compatibility."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.core.events.database import get_db_session
from src.routers.health import router
from src.services.health.health import ReadinessReport


@pytest.fixture
def app(db):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/health")
    app.dependency_overrides[get_db_session] = lambda: db
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as test_client:
        yield test_client


def _report(status: str) -> ReadinessReport:
    return ReadinessReport(
        status=status,
        timestamp="2026-07-12T00:00:00+00:00",
        checks={},
        integrations={},
    )


class TestHealthRouter:
    async def test_legacy_health_endpoint_returns_true(self, client):
        response = await client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json() is True

    async def test_liveness_is_structured(self, client):
        response = await client.get("/api/v1/health/live")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"
        assert response.json()["service"] == "learnhouse-api"

    async def test_readiness_returns_200_for_ready_or_degraded(self, client):
        for status in ("ready", "degraded"):
            with patch(
                "src.routers.health.check_readiness",
                new=AsyncMock(return_value=_report(status)),
            ):
                response = await client.get("/api/v1/health/ready")
            assert response.status_code == 200
            assert response.json()["status"] == status

    async def test_readiness_returns_structured_503(self, client):
        with patch(
            "src.routers.health.check_readiness",
            new=AsyncMock(return_value=_report("not_ready")),
        ):
            response = await client.get("/api/v1/health/ready")

        assert response.status_code == 503
        assert response.json()["status"] == "not_ready"
        assert "detail" not in response.json()
