import json

import pytest

from src.db.courses.assignments import AssignmentTaskTypeEnum
from src.services.ai.assignments import _extract_json_object, normalize_generated_task


def test_extract_json_object_accepts_markdown_fenced_json():
    payload = {"tasks": [{"title": "T"}]}

    assert _extract_json_object(f"```json\n{json.dumps(payload)}\n```") == payload


def test_normalize_generated_quiz_task_adds_required_uuids_and_correct_answer():
    task = normalize_generated_task(
        {
            "assignment_type": "QUIZ",
            "title": "Variables",
            "contents": {
                "questions": [
                    {
                        "questionText": "Which one creates a Python variable?",
                        "options": [
                            {"text": "name = 'Ada'"},
                            {"text": "variable name Ada"},
                        ],
                    }
                ]
            },
        }
    )

    assert task.assignment_type == AssignmentTaskTypeEnum.QUIZ
    question = task.contents["questions"][0]
    assert question["questionUUID"].startswith("question_")
    assert question["options"][0]["optionUUID"].startswith("option_")
    assert any(option["assigned_right_answer"] for option in question["options"])


def test_normalize_generated_code_task_keeps_judge0_compatible_schema():
    task = normalize_generated_task(
        {
            "assignment_type": "CODE",
            "title": "Echo",
            "contents": {
                "language_id": 71,
                "starter_code": "name = input()\n",
                "solution_code": "print(input())\n",
                "test_cases": [
                    {"stdin": "Ada\n", "expectedStdout": "Ada\n"},
                    {"stdin": "Linus\n", "expected_stdout": "Linus\n", "hidden": True},
                ],
            },
        }
    )

    assert task.contents["language_id"] == 71
    assert task.contents["grading_mode"] == "equal_weight"
    assert task.contents["test_cases"][1]["expectedStdout"] == "Linus"
    assert task.contents["allow_student_run"] is True


def test_normalize_rejects_human_only_task_types():
    with pytest.raises(ValueError):
        normalize_generated_task(
            {
                "assignment_type": "FILE_SUBMISSION",
                "title": "Upload essay",
                "contents": {},
            }
        )

