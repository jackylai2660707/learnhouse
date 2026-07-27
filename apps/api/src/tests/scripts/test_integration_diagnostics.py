from argparse import Namespace

import pytest

from scripts import integration_diagnostics


def _args(**overrides):
    values = {
        "all": False,
        "text": False,
        "embedding": False,
        "image": False,
        "judge0": False,
        "email": False,
        "allow_costly_image": False,
        "report": None,
    }
    values.update(overrides)
    return Namespace(**values)


@pytest.mark.asyncio
async def test_run_diagnostics_reports_ready_for_selected_capabilities(monkeypatch):
    async def ready():
        return {"status": "ready", "code": "ready"}

    monkeypatch.setattr(integration_diagnostics, "diagnose_text", ready)
    monkeypatch.setattr(integration_diagnostics, "diagnose_judge0", ready)

    payload, exit_code = await integration_diagnostics.run_diagnostics(
        _args(text=True, judge0=True)
    )

    assert payload["overall"] == "ready"
    assert set(payload["results"]) == {"ai_text", "judge0"}
    assert exit_code == 0


@pytest.mark.asyncio
async def test_run_diagnostics_marks_skipped_image_and_email_degraded(monkeypatch):
    async def degraded_email():
        return {"status": "degraded", "code": "email_sender_invalid"}

    monkeypatch.setattr(integration_diagnostics, "diagnose_email", degraded_email)

    payload, exit_code = await integration_diagnostics.run_diagnostics(
        _args(image=True, email=True)
    )

    assert payload["overall"] == "degraded"
    assert payload["results"]["ai_image"]["code"] == "ai_image_explicit_approval_required"
    assert payload["results"]["email"]["code"] == "email_sender_invalid"
    assert exit_code == 1


def test_report_writer_uses_private_permissions(tmp_path):
    report = tmp_path / "diagnostics" / "report.json"

    integration_diagnostics._write_report(report, {"overall": "ready"})

    assert report.stat().st_mode & 0o777 == 0o600
    assert '"overall": "ready"' in report.read_text()
