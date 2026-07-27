from types import SimpleNamespace

import pytest

import src.services.courses.activities.assignments as assignments_service
from config.config import Judge0Config
from src.db.courses.assignments import AssignmentTask, AssignmentTaskTypeEnum
from src.services.judge0 import Judge0ServiceError


def _task(*, grading_mode="equal_weight"):
    return SimpleNamespace(
        assignment_task_uuid="assignmenttask_synthetic",
        max_grade_value=100,
        contents={
            "language_id": 71,
            "grading_mode": grading_mode,
            "test_cases": [
                {"stdin": "1", "expectedStdout": "one", "weight": 1},
                {"stdin": "2", "expectedStdout": "two", "weight": 3},
            ],
        },
    )


def _submission():
    return SimpleNamespace(
        task_submission={
            "language_id": 71,
            "source_code": "print(input())",
        }
    )


def _web_task(*, grading_mode="equal_weight"):
    return SimpleNamespace(
        assignment_type=assignments_service.AssignmentTaskTypeEnum.CODE,
        max_grade_value=100,
        contents={
            "mode": "web_preview",
            "grading_mode": grading_mode,
            "web_checks": [
                {"target": "html", "match": "regex", "pattern": "^<h1>.+</h1>$", "weight": 1},
                {"target": "css", "match": "contains", "pattern": "color: red", "weight": 3},
            ],
        },
    )


def _web_submission():
    return SimpleNamespace(task_submission={
        "mode": "web_preview",
        "html_code": "<header>標題</header>\n<h1>第一行</h1>\n<footer>頁尾</footer>",
        "css_code": "body { color: blue; }",
        "js_code": "",
        "results": [{"passed": True, "expected_stdout": "forged"}],
    })


def _config():
    return SimpleNamespace(
        judge0_config=Judge0Config(
            api_url="https://judge.example.test",
            client_id=None,
            client_secret=None,
        )
    )


@pytest.mark.asyncio
async def test_code_grading_uses_judge0_results(monkeypatch):
    monkeypatch.setattr(assignments_service, "get_learnhouse_config", _config)
    responses = iter(
        [
            {"stdout": "one\n", "status": {"id": 3}},
            {"stdout": "wrong\n", "status": {"id": 3}},
        ]
    )

    async def submit(*args, **kwargs):
        return next(responses)

    monkeypatch.setattr(assignments_service, "submit_judge0", submit)

    grade = await assignments_service._grade_code_task_async(_task(), _submission())

    assert grade == 50


@pytest.mark.asyncio
async def test_student_serialization_does_not_remove_server_owned_hidden_grading_tests(
    monkeypatch,
):
    task = AssignmentTask(
        id=1,
        assignment_task_uuid="assignmenttask_hidden_grading_regression",
        creation_date="2026-07-26",
        update_date="2026-07-26",
        assignment_id=1,
        org_id=1,
        course_id=1,
        chapter_id=1,
        activity_id=1,
        title="Hidden grading regression",
        description="",
        hint="",
        assignment_type=AssignmentTaskTypeEnum.CODE,
        max_grade_value=100,
        contents={
            "language_id": 71,
            "solution_code": "SOLUTION_SENTINEL",
            "test_cases": [
                {
                    "id": "visible",
                    "stdin": "visible",
                    "expectedStdout": "visible",
                    "hidden": False,
                },
                {
                    "id": "hidden",
                    "stdin": "SECRET_INPUT",
                    "expectedStdout": "SECRET_OUTPUT",
                    "hidden": True,
                },
            ],
        },
    )
    student_read = assignments_service._student_assignment_task_read(task)
    assert [case["id"] for case in student_read.contents["test_cases"]] == ["visible"]
    assert "solution_code" not in student_read.contents

    seen_inputs = []

    async def submit(*args, **kwargs):
        seen_inputs.append(kwargs["stdin"])
        stdout = "SECRET_OUTPUT" if kwargs["stdin"] == "SECRET_INPUT" else "visible"
        return {"stdout": stdout, "status": {"id": 3}}

    monkeypatch.setattr(assignments_service, "get_learnhouse_config", _config)
    monkeypatch.setattr(assignments_service, "submit_judge0", submit)

    grade = await assignments_service._grade_code_task_async(task, _submission())

    assert grade == 100
    assert seen_inputs == ["visible", "SECRET_INPUT"]
    assert len(task.contents["test_cases"]) == 2


@pytest.mark.asyncio
async def test_code_grading_preserves_existing_grade_when_judge0_fails(monkeypatch):
    monkeypatch.setattr(assignments_service, "get_learnhouse_config", _config)
    calls = 0

    async def submit(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise Judge0ServiceError(
                code="judge0_timeout",
                http_status=503,
                retryable=True,
            )
        return {"stdout": "one\n", "status": {"id": 3}}

    monkeypatch.setattr(assignments_service, "submit_judge0", submit)

    grade = await assignments_service._grade_code_task_async(_task(), _submission())

    assert grade is None


@pytest.mark.asyncio
async def test_code_grading_preserves_existing_grade_when_judge0_unconfigured(monkeypatch):
    monkeypatch.setattr(
        assignments_service,
        "get_learnhouse_config",
        lambda: SimpleNamespace(judge0_config=None),
    )

    grade = await assignments_service._grade_code_task_async(_task(), _submission())

    assert grade is None


@pytest.mark.parametrize(
    ("grading_mode", "expected"),
    [("equal_weight", 50), ("custom_weights", 25), ("binary", 0)],
)
def test_web_preview_grading_modes_and_multiline_regex_parity(grading_mode, expected):
    grade = assignments_service._grade_web_preview_code_task(
        _web_task(grading_mode=grading_mode),
        _web_submission(),
    )

    assert grade == expected


def test_code_submission_sanitizer_discards_client_results_and_expected_values():
    payload = assignments_service._sanitize_code_submission_payload(
        _web_task(),
        {
            "mode": "web_preview",
            "html_code": "<main>學生作品</main>",
            "css_code": "main {}",
            "js_code": "",
            "results": [{"passed": True, "expected_stdout": "HIDDEN_SENTINEL"}],
            "expected_stdout": "HIDDEN_SENTINEL",
        },
    )

    assert payload == {
        "mode": "web_preview",
        "html_code": "<main>學生作品</main>",
        "css_code": "main {}",
        "js_code": "",
    }
