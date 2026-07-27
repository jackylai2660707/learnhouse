from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.core.events.database import get_db_session
from src.routers.ai import courseplanning
from src.security.auth import get_current_user
from src.services.ai import pdf_build_jobs


@pytest.fixture
def pdf_jobs_app(db, admin_user):
    app = FastAPI()
    app.include_router(courseplanning.router, prefix="/api/v1/ai")
    app.dependency_overrides[get_db_session] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: admin_user
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
async def pdf_jobs_client(pdf_jobs_app):
    async with AsyncClient(
        transport=ASGITransport(app=pdf_jobs_app), base_url="http://test"
    ) as client:
        yield client


@pytest.fixture(autouse=True)
def _pdf_job_router_dependencies(monkeypatch):
    monkeypatch.setattr(courseplanning, "check_limits_with_usage", AsyncMock())
    monkeypatch.setattr(
        courseplanning, "get_org_ai_model", AsyncMock(return_value="test-model")
    )
    monkeypatch.setattr(
        pdf_build_jobs,
        "_stage_files",
        AsyncMock(
            return_value=[
                {
                    "storage_key": "content/_internal/pdf-builds/job/source_0.pdf",
                    "original_name": "teacher-private.pdf",
                    "safe_filename": "source_0.pdf",
                    "sha256": "c" * 64,
                    "size": 16,
                }
            ]
        ),
    )
    monkeypatch.setattr(pdf_build_jobs, "reserve_ai_credit_once", AsyncMock(return_value=8))
    monkeypatch.setattr(pdf_build_jobs, "get_ai_credit_period_token", lambda org_id: "0")


@pytest.mark.asyncio
async def test_pdf_build_routes_are_scoped_idempotent_and_private(
    pdf_jobs_client, org, other_org
):
    org_id = org.id
    other_org_id = other_org.id
    form = {
        "org_id": str(org_id),
        "course_name": "澳門科學",
        "instructions": "private-instruction-never-return",
        "language": "zh-Hant",
        "auto_index": "true",
    }
    files = {"files": ("teacher-private.pdf", b"%PDF-1.4\nlesson", "application/pdf")}
    with patch("src.services.security.rate_limiting.enforce_ai_rate_limit"):
        created = await pdf_jobs_client.post(
            "/api/v1/ai/courseplanning/pdf-builds",
            data=form,
            files=files,
            headers={"Idempotency-Key": "router-stable-key"},
        )
        replay = await pdf_jobs_client.post(
            "/api/v1/ai/courseplanning/pdf-builds",
            data=form,
            files=files,
            headers={"Idempotency-Key": "router-stable-key"},
        )
        conflict = await pdf_jobs_client.post(
            "/api/v1/ai/courseplanning/pdf-builds",
            data={**form, "course_name": "不同課程"},
            files=files,
            headers={"Idempotency-Key": "router-stable-key"},
        )

    assert created.status_code == 202
    body = created.json()
    assert created.headers["location"].endswith(
        f"/{body['job_uuid']}?org_id={org_id}"
    )
    assert created.headers["idempotent-replay"] == "false"
    assert replay.status_code == 202
    assert replay.headers["idempotent-replay"] == "true"
    assert replay.json()["job_uuid"] == body["job_uuid"]
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "pdf_build_idempotency_conflict"

    serialized = created.text
    assert "private-instruction-never-return" not in serialized
    assert "teacher-private.pdf" not in serialized
    for private_field in ("options", "plan_checkpoint", "lease_owner", "idempotency_key"):
        assert private_field not in body

    own = await pdf_jobs_client.get(
        f"/api/v1/ai/courseplanning/pdf-builds/{body['job_uuid']}",
        params={"org_id": org_id},
    )
    wrong_org = await pdf_jobs_client.get(
        f"/api/v1/ai/courseplanning/pdf-builds/{body['job_uuid']}",
        params={"org_id": other_org_id},
    )
    active = await pdf_jobs_client.get(
        "/api/v1/ai/courseplanning/pdf-builds",
        params={"org_id": org_id, "active": "true", "mine": "true", "limit": 1},
    )
    assert own.status_code == 200
    assert wrong_org.status_code == 404
    assert active.status_code == 200
    assert [item["job_uuid"] for item in active.json()] == [body["job_uuid"]]


@pytest.mark.asyncio
async def test_pdf_build_create_requires_idempotency_key(pdf_jobs_client, org):
    with patch("src.services.security.rate_limiting.enforce_ai_rate_limit"):
        response = await pdf_jobs_client.post(
            "/api/v1/ai/courseplanning/pdf-builds",
            data={"org_id": str(org.id)},
            files={"files": ("teacher.pdf", b"%PDF-1.4\nlesson", "application/pdf")},
        )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "pdf_build_idempotency_key_required"


@pytest.mark.asyncio
async def test_legacy_synchronous_pdf_build_route_returns_gone(
    pdf_jobs_client, org
):
    response = await pdf_jobs_client.post(
        "/api/v1/ai/courseplanning/pdf-build",
        data={"org_id": str(org.id)},
        files={"files": ("teacher.pdf", b"%PDF-1.4\nlesson", "application/pdf")},
    )

    assert response.status_code == 410
    assert response.json()["detail"] == {
        "code": "pdf_build_legacy_endpoint_retired",
        "message": "同步 PDF 建課已停用，請改用新的背景建課功能。",
        "retryable": False,
        "replacement": "/api/v1/ai/courseplanning/pdf-builds",
    }


def test_pdf_build_openapi_exposes_durable_routes_and_marks_legacy_gone(
    pdf_jobs_app,
):
    paths = pdf_jobs_app.openapi()["paths"]
    collection = paths["/api/v1/ai/courseplanning/pdf-builds"]
    detail = paths["/api/v1/ai/courseplanning/pdf-builds/{job_uuid}"]
    legacy = paths["/api/v1/ai/courseplanning/pdf-build"]["post"]

    assert {"get", "post"}.issubset(collection)
    assert "get" in detail
    assert legacy["deprecated"] is True
    assert "410" in legacy["responses"]
    assert "200" not in legacy["responses"]


@pytest.mark.asyncio
async def test_pdf_build_create_and_pre_course_status_enforce_course_rbac(
    pdf_jobs_client, pdf_jobs_app, org, regular_user
):
    with patch("src.services.security.rate_limiting.enforce_ai_rate_limit"):
        created = await pdf_jobs_client.post(
            "/api/v1/ai/courseplanning/pdf-builds",
            data={"org_id": str(org.id)},
            files={"files": ("teacher.pdf", b"%PDF-1.4\nlesson", "application/pdf")},
            headers={"Idempotency-Key": "rbac-admin-key"},
        )
    assert created.status_code == 202
    job_uuid = created.json()["job_uuid"]

    pdf_jobs_app.dependency_overrides[get_current_user] = lambda: regular_user
    denied_read = await pdf_jobs_client.get(
        f"/api/v1/ai/courseplanning/pdf-builds/{job_uuid}",
        params={"org_id": org.id},
    )
    stage_files = pdf_build_jobs._stage_files
    stage_files.reset_mock()
    with patch("src.services.security.rate_limiting.enforce_ai_rate_limit"):
        denied_create = await pdf_jobs_client.post(
            "/api/v1/ai/courseplanning/pdf-builds",
            data={"org_id": str(org.id)},
            files={"files": ("teacher.pdf", b"%PDF-1.4\nlesson", "application/pdf")},
            headers={"Idempotency-Key": "rbac-student-key"},
        )

    assert denied_read.status_code == 403
    assert denied_create.status_code == 403
    stage_files.assert_not_awaited()
