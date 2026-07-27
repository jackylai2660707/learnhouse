import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from src.db.courses.assignments import (
    Assignment,
    AssignmentTask,
    AssignmentTaskTypeEnum,
    AssignmentUserSubmission,
    AssignmentUserSubmissionStatus,
    GradingTypeEnum,
)
from src.services.ai.assignments import (
    _assignment_ai_configuration_error,
    _assignment_generation_model,
    _build_assignment_generation_prompt,
    _extract_json_object,
    _has_specific_generation_context,
    _normalize_generated_task_drafts,
    _refund_reserved_ai_credit,
    generate_assignment_tasks,
    normalize_generated_task,
)
from src.services.ai.schemas.assignments import GenerateAssignmentTasksRequest
from src.services.ai.schemas.assignments import SIMPLE_AI_MAX_TASK_COUNT
from src.services.courses.activities.assignments import SIMPLE_PILOT_MAX_ASSIGNMENT_TASKS


def test_extract_json_object_accepts_markdown_fenced_json():
    payload = {"tasks": [{"title": "T"}]}

    assert _extract_json_object(f"```json\n{json.dumps(payload)}\n```") == payload


def test_ai_generation_count_limit_matches_simple_assignment_limit():
    assert SIMPLE_AI_MAX_TASK_COUNT == SIMPLE_PILOT_MAX_ASSIGNMENT_TASKS


def test_normalize_generated_quiz_task_adds_required_uuids():
    task = normalize_generated_task(
        {
            "assignment_type": "QUIZ",
            "title": "Variables",
            "contents": {
                "questions": [
                    {
                        "questionText": "Which one creates a Python variable?",
                        "options": [
                            {"text": "name = 'Ada'", "assigned_right_answer": True},
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


def test_normalize_generated_task_accepts_common_simple_type_aliases():
    quiz = normalize_generated_task(
        {
            "assignment_type": "multiple-choice",
            "title": "Simple quiz",
            "contents": {
                "questions": [
                    {
                        "questionText": "澳門位於哪個國家？",
                        "options": [
                            {"text": "中國", "assigned_right_answer": True},
                            {"text": "日本", "assigned_right_answer": False},
                        ],
                    }
                ]
            },
        }
    )
    form = normalize_generated_task(
        {
            "assignment_type": "fill in the blank",
            "title": "Simple fill blank",
            "contents": {
                "questions": [
                    {
                        "questionText": "澳門的官方語言之一是____。",
                        "blanks": [{"correctAnswer": "中文"}],
                    }
                ]
            },
        }
    )
    short_answer = normalize_generated_task(
        {
            "assignment_type": "short-answer",
            "title": "Simple short answer",
            "contents": {
                "prompt": "澳門位於哪個國家？",
                "correct_answers": ["中國"],
            },
        }
    )

    assert quiz.assignment_type == AssignmentTaskTypeEnum.QUIZ
    assert form.assignment_type == AssignmentTaskTypeEnum.FORM
    assert short_answer.assignment_type == AssignmentTaskTypeEnum.SHORT_ANSWER


def test_normalize_generated_task_accepts_traditional_chinese_simple_type_aliases():
    quiz = normalize_generated_task(
        {
            "assignment_type": "選擇題",
            "title": "簡單選擇題",
            "contents": {
                "questions": [
                    {
                        "questionText": "澳門位於哪個國家？",
                        "options": [
                            {"text": "中國", "assigned_right_answer": True},
                            {"text": "日本", "assigned_right_answer": False},
                        ],
                    }
                ]
            },
        }
    )
    form = normalize_generated_task(
        {
            "assignment_type": "填空題",
            "title": "簡單填空題",
            "contents": {
                "questions": [
                    {
                        "questionText": "澳門的官方語言之一是____。",
                        "blanks": [{"correctAnswer": "中文"}],
                    }
                ]
            },
        }
    )
    short_answer = normalize_generated_task(
        {
            "assignment_type": "問答題",
            "title": "簡單問答題",
            "contents": {
                "prompt": "澳門位於哪個國家？",
                "correct_answers": ["中國"],
            },
        }
    )

    assert quiz.assignment_type == AssignmentTaskTypeEnum.QUIZ
    assert form.assignment_type == AssignmentTaskTypeEnum.FORM
    assert short_answer.assignment_type == AssignmentTaskTypeEnum.SHORT_ANSWER


def test_normalize_generated_task_converts_common_simplified_chinese_to_traditional():
    task = normalize_generated_task(
        {
            "assignment_type": "选择题",
            "title": "澳门地理练习",
            "description": "学生选择正确答案。",
            "hint": "留意澳门位于哪个国家。",
            "contents": {
                "questions": [
                    {
                        "questionText": "澳门位于哪个国家？",
                        "options": [
                            {"text": "中国", "assigned_right_answer": True},
                            {"text": "日本", "assigned_right_answer": False},
                        ],
                    }
                ]
            },
        }
    )

    assert task.assignment_type == AssignmentTaskTypeEnum.QUIZ
    assert task.title == "澳門地理練習"
    assert task.description == "學生選擇正確答案。"
    assert task.hint == "留意澳門位於哪個國家。"
    question = task.contents["questions"][0]
    assert question["questionText"] == "澳門位於哪個國家？"
    assert question["options"][0]["text"] == "中國"


def test_normalize_generated_task_fills_description_from_student_prompt():
    quiz = normalize_generated_task(
        {
            "assignment_type": "QUIZ",
            "title": "簡單選擇題",
            "contents": {
                "questions": [
                    {
                        "questionText": "澳門位於哪個國家？",
                        "options": [
                            {"text": "中國", "assigned_right_answer": True},
                            {"text": "日本", "assigned_right_answer": False},
                        ],
                    }
                ]
            },
        }
    )
    form = normalize_generated_task(
        {
            "assignment_type": "FORM",
            "title": "簡單填空題",
            "contents": {
                "questions": [
                    {
                        "questionText": "澳門的官方語言之一是____。",
                        "blanks": [{"correctAnswer": "中文"}],
                    }
                ]
            },
        }
    )
    short_answer = normalize_generated_task(
        {
            "assignment_type": "SHORT_ANSWER",
            "title": "簡單問答題",
            "contents": {
                "prompt": "澳門位於哪個國家？",
                "correct_answers": ["中國"],
            },
        }
    )

    assert quiz.description == "澳門位於哪個國家？"
    assert form.description == "澳門的官方語言之一是____。"
    assert short_answer.description == "澳門位於哪個國家？"


def test_normalize_generated_task_preserves_teacher_facing_description():
    task = normalize_generated_task(
        {
            "assignment_type": "QUIZ",
            "title": "簡單選擇題",
            "description": "請完成這題選擇題。",
            "contents": {
                "questions": [
                    {
                        "questionText": "澳門位於哪個國家？",
                        "options": [
                            {"text": "中國", "assigned_right_answer": True},
                            {"text": "日本", "assigned_right_answer": False},
                        ],
                    }
                ]
            },
        }
    )

    assert task.description == "請完成這題選擇題。"


def test_normalize_generated_task_fills_missing_title_from_student_prompt():
    quiz = normalize_generated_task(
        {
            "assignment_type": "QUIZ",
            "contents": {
                "questions": [
                    {
                        "questionText": "澳門位於哪個國家？",
                        "options": [
                            {"text": "中國", "assigned_right_answer": True},
                            {"text": "日本", "assigned_right_answer": False},
                        ],
                    }
                ]
            },
        }
    )
    form = normalize_generated_task(
        {
            "assignment_type": "FORM",
            "contents": {
                "questions": [
                    {
                        "questionText": "澳門的官方語言之一是____。",
                        "blanks": [{"correctAnswer": "中文"}],
                    }
                ]
            },
        }
    )
    short_answer = normalize_generated_task(
        {
            "assignment_type": "SHORT_ANSWER",
            "contents": {
                "prompt": "澳門位於哪個國家？",
                "correct_answers": ["中國"],
            },
        }
    )

    assert quiz.title == "澳門位於哪個國家？"
    assert form.title == "澳門的官方語言之一是____。"
    assert short_answer.title == "澳門位於哪個國家？"


def test_normalize_generated_task_replaces_generic_title_with_student_prompt():
    task = normalize_generated_task(
        {
            "assignment_type": "QUIZ",
            "title": "AI 自動生成題目",
            "contents": {
                "questions": [
                    {
                        "questionText": "水由液體變成氣體的過程叫甚麼？",
                        "options": [
                            {"text": "蒸發", "assigned_right_answer": True},
                            {"text": "凝結", "assigned_right_answer": False},
                        ],
                    }
                ]
            },
        }
    )

    assert task.title == "水由液體變成氣體的過程叫甚麼？"


def test_normalize_generated_task_preserves_specific_title():
    task = normalize_generated_task(
        {
            "assignment_type": "QUIZ",
            "title": "澳門地理小測",
            "contents": {
                "questions": [
                    {
                        "questionText": "澳門位於哪個國家？",
                        "options": [
                            {"text": "中國", "assigned_right_answer": True},
                            {"text": "日本", "assigned_right_answer": False},
                        ],
                    }
                ]
            },
        }
    )

    assert task.title == "澳門地理小測"


def test_normalize_generated_quiz_rejects_missing_answer_key():
    with pytest.raises(ValueError) as exc_info:
        normalize_generated_task(
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

    assert "correct option" in str(exc_info.value)


def test_normalize_generated_quiz_rejects_multiple_correct_options_for_simple_workflow():
    with pytest.raises(ValueError) as exc_info:
        normalize_generated_task(
            {
                "assignment_type": "QUIZ",
                "title": "Simple single-answer quiz",
                "contents": {
                    "questions": [
                        {
                            "questionText": "Which option is a fruit?",
                            "options": [
                                {"text": "Apple", "assigned_right_answer": True},
                                {"text": "Banana", "assigned_right_answer": True},
                                {"text": "Desk", "assigned_right_answer": False},
                            ],
                        }
                    ]
                },
            }
        )

    assert "exactly one correct option" in str(exc_info.value)


def test_normalize_generated_quiz_treats_string_false_as_false():
    task = normalize_generated_task(
        {
            "assignment_type": "QUIZ",
            "title": "Simple boolean flags",
            "contents": {
                "questions": [
                    {
                        "questionText": "澳門位於哪個國家？",
                        "options": [
                            {"text": "中國", "assigned_right_answer": "true"},
                            {"text": "日本", "assigned_right_answer": "false"},
                        ],
                    }
                ]
            },
        }
    )

    options = task.contents["questions"][0]["options"]
    assert [option["assigned_right_answer"] for option in options] == [True, False]


def test_normalize_generated_quiz_accepts_common_answer_key_fields():
    task = normalize_generated_task(
        {
            "assignment_type": "QUIZ",
            "title": "Question-level answer key",
            "contents": {
                "questions": [
                    {
                        "questionText": "澳門位於哪個國家？",
                        "correctAnswer": "中國",
                        "options": [
                            {"text": "中國", "is_correct": "false"},
                            {"text": "日本", "is_correct": "false"},
                        ],
                    }
                ]
            },
        }
    )

    options = task.contents["questions"][0]["options"]
    assert [option["assigned_right_answer"] for option in options] == [True, False]


def test_normalize_generated_quiz_keeps_four_options_and_preserves_late_answer():
    task = normalize_generated_task(
        {
            "assignment_type": "QUIZ",
            "title": "Too many options",
            "contents": {
                "questions": [
                    {
                        "questionText": "澳門位於哪個國家？",
                        "options": [
                            {"text": "日本", "assigned_right_answer": False},
                            {"text": "韓國", "assigned_right_answer": False},
                            {"text": "新加坡", "assigned_right_answer": False},
                            {"text": "泰國", "assigned_right_answer": False},
                            {"text": "中國", "assigned_right_answer": True},
                        ],
                    }
                ]
            },
        }
    )

    options = task.contents["questions"][0]["options"]
    assert len(options) == 4
    assert options[-1]["text"] == "中國"
    assert sum(1 for option in options if option["assigned_right_answer"]) == 1


def test_normalize_generated_quiz_keeps_one_question_for_simple_workflow():
    task = normalize_generated_task(
        {
            "assignment_type": "QUIZ",
            "title": "Simple Macau geography",
            "contents": {
                "questions": [
                    {
                        "questionText": "澳門位於哪個國家？",
                        "options": [
                            {"text": "中國", "assigned_right_answer": True},
                            {"text": "日本", "assigned_right_answer": False},
                        ],
                    },
                    {
                        "questionText": "澳門由幾個主要區域組成？",
                        "options": [
                            {"text": "三個", "assigned_right_answer": True},
                            {"text": "十個", "assigned_right_answer": False},
                        ],
                    },
                ]
            },
        }
    )

    assert len(task.contents["questions"]) == 1
    assert task.contents["questions"][0]["questionText"] == "澳門位於哪個國家？"


def test_normalize_generated_form_keeps_one_blank_for_simple_workflow():
    task = normalize_generated_task(
        {
            "assignment_type": "FORM",
            "title": "Simple fill-in",
            "contents": {
                "questions": [
                    {
                        "questionText": "澳門特別行政區的簡稱是____。",
                        "blanks": [
                            {"placeholder": "填寫簡稱", "correctAnswer": "澳門"},
                            {"placeholder": "填寫國家", "correctAnswer": "中國"},
                        ],
                    },
                    {
                        "questionText": "澳門使用的官方語文包括中文和____。",
                        "blanks": [
                            {"placeholder": "填寫語文", "correctAnswer": "葡文"},
                        ],
                    },
                ]
            },
        }
    )

    assert len(task.contents["questions"]) == 1
    assert len(task.contents["questions"][0]["blanks"]) == 1
    assert task.contents["questions"][0]["blanks"][0]["correctAnswer"] == "澳門"


def test_normalize_generated_form_accepts_question_level_answer_key():
    task = normalize_generated_task(
        {
            "assignment_type": "FORM",
            "title": "Question-level fill-in",
            "contents": {
                "questions": [
                    {
                        "questionText": "澳門的官方語言之一是____。",
                        "correctAnswer": "中文",
                    }
                ]
            },
        }
    )

    blank = task.contents["questions"][0]["blanks"][0]
    assert blank["correctAnswer"] == "中文"
    assert blank["placeholder"] == "填寫答案"


def test_normalize_generated_form_rejects_sentence_answer_key_for_simple_workflow():
    with pytest.raises(ValueError) as exc_info:
        normalize_generated_task(
            {
                "assignment_type": "FORM",
                "title": "Sentence fill-in",
                "contents": {
                    "questions": [
                        {
                            "questionText": "水循環的主要過程是____。",
                            "blanks": [
                                {
                                    "correctAnswer": "水蒸發到空氣中，冷卻後凝結成雲，再以雨或雪回到地面。"
                                }
                            ],
                        }
                    ]
                },
            }
        )

    assert "FORM task needs at least one valid blank" in str(exc_info.value)


def test_normalize_generated_code_task_is_rejected_for_simple_ai_workflow():
    with pytest.raises(ValueError):
        normalize_generated_task(
            {
                "assignment_type": "CODE",
                "title": "Echo",
                "contents": {
                    "language_id": 71,
                    "starter_code": "name = input()\n",
                    "solution_code": "print(input())\n",
                    "test_cases": [
                        {"stdin": "Ada\n", "expectedStdout": "Ada\n"},
                    ],
                },
            }
        )


def test_normalize_generated_short_answer_avoids_regex_matching_for_simple_workflow():
    task = normalize_generated_task(
        {
            "assignment_type": "SHORT_ANSWER",
            "title": "Vocabulary",
            "contents": {
                "prompt": "「天氣」的英文是甚麼？",
                "correct_answers": ["weather"],
                "match_mode": "regex",
            },
        }
    )

    assert task.contents["match_mode"] == "case_insensitive"


def test_normalize_generated_short_answer_avoids_contains_matching_for_simple_workflow():
    task = normalize_generated_task(
        {
            "assignment_type": "SHORT_ANSWER",
            "title": "Macau location",
            "contents": {
                "prompt": "澳門位於哪個國家？",
                "correct_answers": ["中國"],
                "match_mode": "contains",
            },
        }
    )

    assert task.contents["match_mode"] == "case_insensitive"


def test_normalize_generated_short_answer_accepts_common_answer_key_fields():
    task = normalize_generated_task(
        {
            "assignment_type": "SHORT_ANSWER",
            "title": "Common answer key field",
            "contents": {
                "prompt": "澳門位於哪個國家？",
                "correctAnswer": "中國",
            },
        }
    )

    assert task.contents["correct_answers"] == ["中國"]


def test_normalize_generated_short_answer_keeps_only_three_unique_answer_keys():
    task = normalize_generated_task(
        {
            "assignment_type": "SHORT_ANSWER",
            "title": "Synonyms",
            "contents": {
                "prompt": "水由液體變成氣體的過程叫甚麼？",
                "correct_answers": ["蒸發", "蒸發", "汽化", "evaporation", "水蒸發"],
            },
        }
    )

    assert task.contents["correct_answers"] == ["蒸發", "汽化", "evaporation"]


def test_normalize_generated_short_answer_rejects_subjective_prompt_for_simple_workflow():
    with pytest.raises(ValueError) as exc_info:
        normalize_generated_task(
            {
                "assignment_type": "SHORT_ANSWER",
                "title": "Subjective answer",
                "contents": {
                    "prompt": "請解釋水循環的過程。",
                    "correct_answers": ["水蒸發後凝結，再以降水回到地面。"],
                },
            }
        )

    assert "simple factual Q&A" in str(exc_info.value)


def test_normalize_generated_short_answer_rejects_open_ended_prompt_for_simple_workflow():
    with pytest.raises(ValueError) as exc_info:
        normalize_generated_task(
            {
                "assignment_type": "SHORT_ANSWER",
                "title": "Open ended answer",
                "contents": {
                    "prompt": "請比較蒸發和凝結的不同。",
                    "correct_answers": ["蒸發是液體變氣體，凝結是氣體變液體。"],
                },
            }
        )

    assert "simple factual Q&A" in str(exc_info.value)


def test_normalize_generated_short_answer_rejects_list_prompt_for_simple_workflow():
    with pytest.raises(ValueError) as exc_info:
        normalize_generated_task(
            {
                "assignment_type": "SHORT_ANSWER",
                "title": "List answer",
                "contents": {
                    "prompt": "請列出水循環的三個過程。",
                    "correct_answers": ["蒸發、凝結、降雨"],
                },
            }
        )

    assert "simple factual Q&A" in str(exc_info.value)


def test_normalize_generated_short_answer_rejects_reason_prompt_for_simple_workflow():
    with pytest.raises(ValueError) as exc_info:
        normalize_generated_task(
            {
                "assignment_type": "SHORT_ANSWER",
                "title": "Reason answer",
                "contents": {
                    "prompt": "澳門旅遊業發展的主要原因是甚麼？",
                    "correct_answers": ["世界文化遺產"],
                },
            }
        )

    assert "simple factual Q&A" in str(exc_info.value)


def test_normalize_generated_short_answer_allows_factual_comparison_topic_word():
    task = normalize_generated_task(
        {
            "assignment_type": "SHORT_ANSWER",
            "title": "分數比較符號",
            "contents": {
                "prompt": "分數比較常用的符號之一是甚麼？",
                "correct_answers": ["大於號"],
                "match_mode": "case_insensitive",
            },
        }
    )

    assert task.contents["prompt"] == "分數比較常用的符號之一是甚麼？"
    assert task.contents["correct_answers"] == ["大於號"]


def test_normalize_generated_short_answer_rejects_long_answer_key_for_simple_workflow():
    with pytest.raises(ValueError) as exc_info:
        normalize_generated_task(
            {
                "assignment_type": "SHORT_ANSWER",
                "title": "Long answer",
                "contents": {
                    "prompt": "水循環包括哪三個主要步驟？",
                    "correct_answers": ["水蒸發到空氣中，冷卻後凝結成雲，再以雨或雪回到地面。"],
                },
            }
        )

    assert "accepted answers must stay short" in str(exc_info.value)


def test_normalize_generated_task_drafts_uses_later_valid_items_after_bad_ai_output():
    drafts, warnings = _normalize_generated_task_drafts(
        [
            {
                "assignment_type": "QUIZ",
                "title": "Broken quiz",
                "contents": {
                    "questions": [
                        {
                            "questionText": "Which option is correct?",
                            "options": [
                                {"text": "A"},
                                {"text": "B"},
                            ],
                        }
                    ]
                },
            },
            {
                "assignment_type": "QUIZ",
                "title": "Good quiz",
                "contents": {
                    "questions": [
                        {
                            "questionText": "Which one is a fruit?",
                            "options": [
                                {"text": "Apple", "assigned_right_answer": True},
                                {"text": "Desk", "assigned_right_answer": False},
                            ],
                        }
                    ]
                },
            },
            {
                "assignment_type": "SHORT_ANSWER",
                "title": "Good short answer",
                "contents": {
                    "prompt": "Write the colour of grass.",
                    "correct_answers": ["green"],
                },
            },
        ],
        ["QUIZ", "SHORT_ANSWER"],
        2,
    )

    assert [draft.title for draft in drafts] == ["Good quiz", "Good short answer"]
    assert warnings == ["已略過 1 題格式不完整、無法自動批改的題目。"]


def test_normalize_generated_task_drafts_keeps_only_usable_items_when_ai_returns_too_few():
    drafts, warnings = _normalize_generated_task_drafts(
        [
            {
                "assignment_type": "SHORT_ANSWER",
                "title": "Only usable item",
                "contents": {
                    "prompt": "Write the colour of grass.",
                    "correct_answers": ["green"],
                },
            }
        ],
        ["QUIZ", "FORM", "SHORT_ANSWER"],
        3,
        GenerateAssignmentTasksRequest(
            assignment_uuid="assignment_test",
            subject="常識",
            unit="澳門地理",
        ),
    )

    assert len(drafts) == 1
    assert drafts[0].title == "Only usable item"
    assert warnings == [
        "AI 本次少了 2 題可自動批改的題目，"
        "系統只建立已通過檢查的 1 題。"
        "請補充課題後再生成，或用題庫/手動新增。"
    ]


def test_normalize_generated_task_drafts_returns_no_drafts_when_all_ai_items_are_unusable():
    drafts, warnings = _normalize_generated_task_drafts(
        [
            {
                "assignment_type": "CODE",
                "title": "Too complex",
                "contents": {},
            },
            {
                "assignment_type": "SHORT_ANSWER",
                "title": "Subjective",
                "contents": {
                    "prompt": "請分析澳門旅遊業的影響。",
                    "correct_answers": ["旅遊業能帶動經濟，但也會帶來人流壓力。"],
                },
            },
        ],
        ["FORM"],
        2,
        GenerateAssignmentTasksRequest(
            assignment_uuid="assignment_test",
            assignment_title="澳門旅遊基礎練習",
            unit="旅遊業",
        ),
    )

    assert drafts == []
    assert warnings == [
        "已略過 2 題格式不完整、無法自動批改的題目。",
        "AI 本次少了 2 題可自動批改的題目，"
        "系統只建立已通過檢查的 0 題。"
        "請補充課題後再生成，或用題庫/手動新增。",
    ]


def test_normalize_rejects_human_only_task_types():
    with pytest.raises(ValueError):
        normalize_generated_task(
            {
                "assignment_type": "FILE_SUBMISSION",
                "title": "Upload essay",
                "contents": {},
            }
        )


def test_assignment_generation_prompt_includes_school_context_and_traditional_chinese():
    prompt = _build_assignment_generation_prompt(
        GenerateAssignmentTasksRequest(
            assignment_uuid="assignment_test",
            assignment_title="分數比較課後練習",
            assignment_description="學生需要比較同分母和異分母分數。",
            prompt="為學生生成分數比較練習",
            subject="數學",
            education_stage="primary",
            grade_level="小四",
            unit="分數",
            learning_objectives=["能比較分數大小", "能解釋基本步驟"],
            count=3,
            question_types=["QUIZ", "FORM"],
        )
    )

    assert "use Traditional Chinese for Macau schools" in prompt
    assert "If the teacher brief uses Simplified Chinese" in prompt
    assert "Assignment title: 分數比較課後練習" in prompt
    assert "Assignment description: 學生需要比較同分母和異分母分數。" in prompt
    assert "Subject: 數學" in prompt
    assert "Education stage: primary" in prompt
    assert "Grade level: 小四" in prompt
    assert "Course unit: 分數" in prompt
    assert "- 能比較分數大小" in prompt
    assert "Keep all Chinese in Traditional Chinese" in prompt
    assert "Allowed assignment_type values: QUIZ" in prompt
    assert "Each task must contain exactly one learner question" in prompt
    assert "exactly one correct option" in prompt
    assert "without AI judgement, teacher rubric, or subjective interpretation" in prompt
    assert "For FORM, use exactly one blank per task" in prompt
    assert "one QUIZ, one FORM, and one SHORT_ANSWER" in prompt
    assert "Do not use regex" in prompt
    assert "answer keys must be short values, not full sentences" in prompt
    assert "avoid question wording such as why, how, explain" in prompt
    assert "Do not create judgement-heavy rubrics" in prompt
    assert "澳門地理選擇題" in prompt
    assert "澳門特別行政區位於哪個國家？" in prompt
    assert "Short teacher-facing title" not in prompt
    assert "Question text" not in prompt
    assert "Do not output Simplified Chinese" in prompt
    assert "- NUMBER_ANSWER:" not in prompt
    assert "- CODE:" not in prompt


def test_assignment_generation_request_filters_complex_question_types_to_simple():
    request = GenerateAssignmentTasksRequest(
        assignment_uuid="assignment_test",
        prompt="為學生生成分數比較練習",
        question_types=["quiz", "NUMBER_ANSWER", "FORM", "CODE"],
    )

    assert request.question_types == ["QUIZ", "FORM"]

    prompt = _build_assignment_generation_prompt(request)

    assert "Allowed assignment_type values: QUIZ, FORM" in prompt
    assert "- NUMBER_ANSWER:" not in prompt
    assert "- CODE:" not in prompt


def test_assignment_generation_request_accepts_traditional_chinese_question_type_aliases():
    request = GenerateAssignmentTasksRequest(
        assignment_uuid="assignment_test",
        prompt="為學生生成簡單練習",
        question_types=["選擇題", "填空題", "簡答題"],
    )

    assert request.question_types == ["QUIZ", "FORM", "SHORT_ANSWER"]

    prompt = _build_assignment_generation_prompt(request)

    assert "Allowed assignment_type values: QUIZ, FORM, SHORT_ANSWER" in prompt
    assert "- CODE:" not in prompt


def test_assignment_generation_request_accepts_simplified_chinese_question_type_aliases():
    request = GenerateAssignmentTasksRequest(
        assignment_uuid="assignment_test",
        prompt="为学生生成简单练习",
        question_types=["选择题", "填空题", "简答题"],
    )

    assert request.question_types == ["QUIZ", "FORM", "SHORT_ANSWER"]


def test_assignment_generation_request_accepts_comma_separated_question_types():
    request = GenerateAssignmentTasksRequest(
        assignment_uuid="assignment_test",
        prompt="為學生生成簡單練習",
        question_types="quiz, fill in the blank, short-answer",
    )

    assert request.question_types == ["QUIZ", "FORM", "SHORT_ANSWER"]


def test_assignment_generation_request_accepts_object_question_type_values():
    request = GenerateAssignmentTasksRequest(
        assignment_uuid="assignment_test",
        prompt="為學生生成簡單練習",
        question_types=[
            {"value": "quiz"},
            {"assignment_type": "FORM"},
            {"id": "short-answer"},
        ],
    )

    assert request.question_types == ["QUIZ", "FORM", "SHORT_ANSWER"]


def test_assignment_generation_request_defaults_to_simple_when_only_complex_types_requested():
    request = GenerateAssignmentTasksRequest(
        assignment_uuid="assignment_test",
        prompt="請生成程式題、數字題和檔案提交",
        question_types=["CODE", "NUMBER_ANSWER", "FILE_SUBMISSION"],
    )

    assert request.question_types == ["QUIZ", "FORM", "SHORT_ANSWER"]

    prompt = _build_assignment_generation_prompt(request)

    assert "Allowed assignment_type values: QUIZ, FORM, SHORT_ANSWER" in prompt
    assert "convert it into a simple auto-gradable QUIZ, FORM, or SHORT_ANSWER task" in prompt
    assert "- NUMBER_ANSWER:" not in prompt
    assert "- CODE:" not in prompt


def test_assignment_generation_skips_ai_tasks_outside_requested_simple_types():
    drafts, warnings = _normalize_generated_task_drafts(
        [
            {
                "assignment_type": "FORM",
                "title": "Not requested fill-in",
                "contents": {
                    "questions": [
                        {
                            "questionText": "澳門特別行政區的簡稱是____。",
                            "blanks": [{"correctAnswer": "澳門"}],
                        }
                    ]
                },
            },
            {
                "assignment_type": "QUIZ",
                "title": "Requested quiz",
                "contents": {
                    "questions": [
                        {
                            "questionText": "澳門位於哪個國家？",
                            "options": [
                                {"text": "中國", "assigned_right_answer": True},
                                {"text": "日本", "assigned_right_answer": False},
                            ],
                        }
                    ]
                },
            },
        ],
        ["QUIZ"],
        1,
    )

    assert [draft.assignment_type for draft in drafts] == [AssignmentTaskTypeEnum.QUIZ]
    assert [draft.title for draft in drafts] == ["Requested quiz"]
    assert warnings == ["已略過 1 題格式不完整、無法自動批改的題目。"]


def test_assignment_generation_request_disables_images_for_simple_school_homework():
    request = GenerateAssignmentTasksRequest(
        assignment_uuid="assignment_test",
        prompt="為學生生成有圖片的題目",
        include_images=True,
    )

    assert request.include_images is False

    prompt = _build_assignment_generation_prompt(request)

    assert "Use images: no" in prompt
    assert "image_prompt" not in prompt


def test_assignment_generation_request_forces_advanced_difficulty_to_beginner():
    request = GenerateAssignmentTasksRequest(
        assignment_uuid="assignment_test",
        prompt="為學生生成很難的挑戰題",
        difficulty="advanced",
    )

    assert request.difficulty == "beginner"


def test_assignment_generation_request_forces_intermediate_difficulty_to_beginner():
    request = GenerateAssignmentTasksRequest(
        assignment_uuid="assignment_test",
        prompt="為學生生成進階題",
        difficulty="intermediate",
    )

    assert request.difficulty == "beginner"


def test_assignment_generation_accepts_short_teacher_topic():
    request = GenerateAssignmentTasksRequest(
        assignment_uuid="assignment_test",
        prompt="分數",
        subject="數學",
        grade_level="小四",
    )

    assert request.prompt == "分數"


def test_assignment_generation_accepts_empty_prompt_for_one_click_simple_homework():
    request = GenerateAssignmentTasksRequest(
        assignment_uuid="assignment_test",
        subject="常識",
        grade_level="小三",
        unit="天氣",
        count=3,
    )

    prompt = _build_assignment_generation_prompt(request)

    assert request.prompt == ""
    assert "生成一份簡單可自動批改的基礎作業" in prompt
    assert "Allowed assignment_type values: QUIZ, FORM, SHORT_ANSWER" in prompt


def test_assignment_generation_context_requires_specific_lesson_topic():
    assert not _has_specific_generation_context(
        GenerateAssignmentTasksRequest(
            assignment_uuid="assignment_test",
            assignment_title="新作業",
            prompt="根據本課內容，生成 3 題簡單作業。",
            subject="數學",
            grade_level="小四",
        )
    )
    assert _has_specific_generation_context(
        GenerateAssignmentTasksRequest(
            assignment_uuid="assignment_test",
            prompt="為學生生成分數比較練習",
            subject="數學",
            grade_level="小四",
        )
    )
    assert _has_specific_generation_context(
        GenerateAssignmentTasksRequest(
            assignment_uuid="assignment_test",
            unit="澳門世界文化遺產",
            subject="常識",
        )
    )


def test_assignment_generation_clamps_count_to_simple_pilot_limit():
    request = GenerateAssignmentTasksRequest(
        assignment_uuid="assignment_test",
        prompt="請一次生成很多題",
        count=10,
    )

    assert request.count == 3


def test_assignment_generation_defaults_to_three_task_quick_homework():
    request = GenerateAssignmentTasksRequest(
        assignment_uuid="assignment_test",
        prompt="為學生生成基礎練習",
    )

    assert request.count == 3

    prompt = _build_assignment_generation_prompt(request)

    assert "Number of tasks: 3" in prompt


def test_assignment_generation_prompt_converts_complex_requests_to_simple_tasks():
    prompt = _build_assignment_generation_prompt(
        GenerateAssignmentTasksRequest(
            assignment_uuid="assignment_test",
            prompt="請生成挑戰題、程式題、附圖題和長篇作文",
            subject="中文",
            grade_level="中一",
        )
    )

    assert "Difficulty: beginner only" in prompt
    assert "Keep the homework simple enough" in prompt
    assert "Do not include file upload, images, drawings, essays" in prompt
    assert "image-based" in prompt
    assert "convert it into a simple auto-gradable QUIZ, FORM, or SHORT_ANSWER task" in prompt


def test_assignment_generation_prompt_allows_short_numeric_answers_inside_simple_types():
    prompt = _build_assignment_generation_prompt(
        GenerateAssignmentTasksRequest(
            assignment_uuid="assignment_test",
            prompt="請生成小四數學加法填空",
            subject="數學",
            grade_level="小四",
            question_types=["FORM", "SHORT_ANSWER"],
        )
    )

    assert "dedicated numeric-answer task types" in prompt
    assert "Numeric answers are allowed when they fit a simple FORM fill-in-the-blank or SHORT_ANSWER question" in prompt
    assert "For math or science, use FORM or SHORT_ANSWER when the answer is a short number" in prompt
    assert "- NUMBER_ANSWER:" not in prompt


def test_assignment_generation_prompt_keeps_short_answers_auto_gradable():
    prompt = _build_assignment_generation_prompt(
        GenerateAssignmentTasksRequest(
            assignment_uuid="assignment_test",
            prompt="請生成問答題",
            subject="常識",
            grade_level="小五",
            question_types=["SHORT_ANSWER"],
        )
    )

    assert "Allowed assignment_type values: SHORT_ANSWER" in prompt
    assert "generate simple Q&A only" in prompt
    assert "at most 3 accepted answers" in prompt
    assert "Do not ask for opinions, explanations, summaries, paragraphs" in prompt
    assert "set match_mode to case_insensitive" in prompt
    assert "Do not use contains" in prompt
    assert "- QUIZ:" not in prompt
    assert "- FORM:" not in prompt


def test_assignment_generation_defaults_to_simple_school_question_types():
    request = GenerateAssignmentTasksRequest(
        assignment_uuid="assignment_test",
        prompt="為學生生成課文理解練習",
    )

    assert request.difficulty == "beginner"
    assert request.question_types == ["QUIZ", "FORM", "SHORT_ANSWER"]

    prompt = _build_assignment_generation_prompt(request)

    assert "Allowed assignment_type values: QUIZ, FORM, SHORT_ANSWER" in prompt
    assert "- QUIZ:" in prompt
    assert "- FORM:" in prompt
    assert "- SHORT_ANSWER:" in prompt
    assert "- NUMBER_ANSWER:" not in prompt
    assert "- CODE:" not in prompt


def test_assignment_ai_configuration_accepts_complete_openai_compatible_config(monkeypatch):
    monkeypatch.setattr(
        "src.services.ai.assignments.get_learnhouse_config",
        lambda: SimpleNamespace(
            ai_config=SimpleNamespace(
                provider="openai_compatible",
                openai_base_url="https://api.example.test/v1",
                openai_api_key="secret",
                openai_model="gpt-test",
            )
        ),
    )

    assert _assignment_ai_configuration_error() is None


def test_assignment_generation_model_uses_openai_compatible_configured_model(monkeypatch):
    monkeypatch.setattr(
        "src.services.ai.assignments.get_learnhouse_config",
        lambda: SimpleNamespace(
            ai_config=SimpleNamespace(
                provider="openai_compatible",
                openai_model="gpt-5.4-mini",
            )
        ),
    )

    assert _assignment_generation_model() == "gpt-5.4-mini"


def test_assignment_generation_model_uses_gemini_default_for_gemini_provider(monkeypatch):
    monkeypatch.setattr(
        "src.services.ai.assignments.get_learnhouse_config",
        lambda: SimpleNamespace(
            ai_config=SimpleNamespace(
                provider="gemini",
                gemini_api_key="secret",
            )
        ),
    )

    assert _assignment_generation_model() == "gemini-2.0-flash"


def test_assignment_ai_configuration_reports_missing_openai_compatible_fields(monkeypatch):
    monkeypatch.setattr(
        "src.services.ai.assignments.get_learnhouse_config",
        lambda: SimpleNamespace(
            ai_config=SimpleNamespace(
                provider="openai_compatible",
                openai_base_url="https://api.example.test/v1",
                openai_api_key="",
                openai_model=None,
            )
        ),
    )

    message = _assignment_ai_configuration_error()

    assert message is not None
    assert "LEARNHOUSE_OPENAI_API_KEY" in message
    assert "LEARNHOUSE_OPENAI_MODEL" in message


def test_assignment_ai_configuration_degrades_when_config_reader_fails(monkeypatch):
    def failing_config():
        raise RuntimeError("broken config")

    monkeypatch.setattr("src.services.ai.assignments.get_learnhouse_config", failing_config)

    message = _assignment_ai_configuration_error()

    assert message == "AI 出題配置讀取失敗：broken config"
    assert _assignment_generation_model() == ""


def test_refund_reserved_ai_credit_does_not_mask_original_error(monkeypatch):
    def failing_refund(org_id, amount):
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr("src.services.ai.assignments.refund_ai_credit", failing_refund)

    _refund_reserved_ai_credit(1, 1)


async def test_generate_assignment_tasks_rejects_locked_assignment_before_ai_credit(
    db,
    org,
    course,
    chapter,
    activity,
    admin_user,
    regular_user,
    mock_request,
    monkeypatch,
):
    assignment = Assignment(
        id=401,
        title="Submitted assignment",
        description="Students already submitted this assignment",
        due_date="2030-01-01",
        published=True,
        grading_type=GradingTypeEnum.NUMERIC,
        auto_grading=True,
        org_id=org.id,
        course_id=course.id,
        chapter_id=chapter.id,
        activity_id=activity.id,
        assignment_uuid="assignment_ai_locked",
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    submission = AssignmentUserSubmission(
        id=402,
        user_id=regular_user.id,
        assignment_id=assignment.id,
        grade=80,
        submission_status=AssignmentUserSubmissionStatus.SUBMITTED,
        attempt_number=1,
        assignmentusersubmission_uuid="assignmentusersubmission_ai_locked",
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(assignment)
    db.add(submission)
    await db.commit()

    reserve_mock = AsyncMock()
    model_mock = AsyncMock()
    monkeypatch.setattr("src.services.ai.assignments.check_resource_access", AsyncMock())
    monkeypatch.setattr("src.services.ai.assignments.reserve_ai_credit", reserve_mock)
    monkeypatch.setattr(
        "src.services.ai.assignments.get_gemini_client",
        lambda: SimpleNamespace(
            models=SimpleNamespace(generate_content=model_mock),
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        await generate_assignment_tasks(
            mock_request,
            GenerateAssignmentTasksRequest(
                assignment_uuid=assignment.assignment_uuid,
                prompt="生成 3 題簡單作業",
            ),
            admin_user,
            db,
        )

    assert exc_info.value.status_code == 400
    assert "已有學生提交這份作業" in exc_info.value.detail
    reserve_mock.assert_not_called()
    model_mock.assert_not_called()


async def test_generate_assignment_tasks_rejects_published_assignment_before_ai_credit(
    db,
    org,
    course,
    chapter,
    activity,
    admin_user,
    mock_request,
    monkeypatch,
):
    assignment = Assignment(
        id=451,
        title="Published assignment",
        description="Students may already be viewing this assignment",
        due_date="2030-01-01",
        published=True,
        grading_type=GradingTypeEnum.NUMERIC,
        auto_grading=True,
        org_id=org.id,
        course_id=course.id,
        chapter_id=chapter.id,
        activity_id=activity.id,
        assignment_uuid="assignment_ai_published_guard",
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(assignment)
    await db.commit()

    reserve_mock = AsyncMock()
    model_mock = AsyncMock()
    monkeypatch.setattr("src.services.ai.assignments.check_resource_access", AsyncMock())
    monkeypatch.setattr("src.services.ai.assignments.reserve_ai_credit", reserve_mock)
    monkeypatch.setattr(
        "src.services.ai.assignments.get_gemini_client",
        lambda: SimpleNamespace(
            models=SimpleNamespace(generate_content=model_mock),
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        await generate_assignment_tasks(
            mock_request,
            GenerateAssignmentTasksRequest(
                assignment_uuid=assignment.assignment_uuid,
                prompt="生成 3 題簡單作業",
            ),
            admin_user,
            db,
        )

    assert exc_info.value.status_code == 400
    assert "已發布作業不能再用 AI 新增題目" in exc_info.value.detail
    reserve_mock.assert_not_called()
    model_mock.assert_not_called()


async def test_generate_assignment_tasks_rejects_missing_lesson_context_before_ai_credit(
    db,
    org,
    course,
    chapter,
    activity,
    admin_user,
    mock_request,
    monkeypatch,
):
    assignment = Assignment(
        id=471,
        title="新作業",
        description="",
        due_date="2030-01-01",
        published=False,
        grading_type=GradingTypeEnum.NUMERIC,
        auto_grading=True,
        org_id=org.id,
        course_id=course.id,
        chapter_id=chapter.id,
        activity_id=activity.id,
        assignment_uuid="assignment_ai_missing_context_guard",
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(assignment)
    await db.commit()

    reserve_mock = AsyncMock()
    model_mock = AsyncMock()
    monkeypatch.setattr("src.services.ai.assignments.check_resource_access", AsyncMock())
    monkeypatch.setattr("src.services.ai.assignments.reserve_ai_credit", reserve_mock)
    monkeypatch.setattr(
        "src.services.ai.assignments.get_gemini_client",
        lambda: SimpleNamespace(
            models=SimpleNamespace(generate_content=model_mock),
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        await generate_assignment_tasks(
            mock_request,
            GenerateAssignmentTasksRequest(
                assignment_uuid=assignment.assignment_uuid,
                prompt="根據本課內容，生成 3 題簡單作業。",
                subject="數學",
                grade_level="小四",
            ),
            admin_user,
            db,
        )

    assert exc_info.value.status_code == 400
    assert "請先輸入課題、單元、作業說明或學習目標" in exc_info.value.detail
    reserve_mock.assert_not_called()
    model_mock.assert_not_called()


async def test_generate_assignment_tasks_uses_specific_course_name_as_context(
    db,
    org,
    course,
    chapter,
    activity,
    admin_user,
    mock_request,
    monkeypatch,
):
    course.name = "小四數學：分數比較"
    db.add(course)

    assignment = Assignment(
        id=472,
        title="新作業",
        description="",
        due_date="2030-01-01",
        published=False,
        grading_type=GradingTypeEnum.NUMERIC,
        auto_grading=True,
        org_id=org.id,
        course_id=course.id,
        chapter_id=chapter.id,
        activity_id=activity.id,
        assignment_uuid="assignment_ai_course_name_context",
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(assignment)
    await db.commit()

    model_mock = Mock(
        return_value=SimpleNamespace(
            text=json.dumps(
                {
                    "tasks": [
                        {
                            "assignment_type": "QUIZ",
                            "title": "分數比較選擇題",
                            "contents": {
                                "questions": [
                                    {
                                        "questionText": "哪一個分數比較大？",
                                        "options": [
                                            {"text": "1/2", "assigned_right_answer": True},
                                            {"text": "1/3", "assigned_right_answer": False},
                                        ],
                                    }
                                ]
                            },
                        }
                    ]
                }
            )
        )
    )
    monkeypatch.setattr("src.services.ai.assignments.check_resource_access", AsyncMock())
    monkeypatch.setattr("src.services.courses.activities.assignments.check_resource_access", AsyncMock())
    monkeypatch.setattr("src.services.ai.assignments.enforce_ai_rate_limit", Mock())
    monkeypatch.setattr("src.services.ai.assignments.reserve_ai_credit", AsyncMock())
    monkeypatch.setattr(
        "src.services.ai.assignments.get_gemini_client",
        lambda: SimpleNamespace(
            models=SimpleNamespace(generate_content=model_mock),
        ),
    )
    monkeypatch.setattr(
        "src.services.ai.assignments.get_learnhouse_config",
        lambda: SimpleNamespace(
            ai_config=SimpleNamespace(
                provider="openai_compatible",
                openai_base_url="https://api.example.test/v1",
                openai_api_key="secret",
                openai_model="gpt-test",
            )
        ),
    )

    response = await generate_assignment_tasks(
        mock_request,
        GenerateAssignmentTasksRequest(
            assignment_uuid=assignment.assignment_uuid,
            prompt="根據本課內容，生成 3 題簡單作業。",
            subject="數學",
            grade_level="小四",
            count=1,
        ),
        admin_user,
        db,
    )

    prompt_text = model_mock.call_args.kwargs["contents"][0]["parts"][0]["text"]
    assert len(response.tasks) == 1
    assert "Course unit: 小四數學：分數比較" in prompt_text


async def test_generate_assignment_tasks_rejects_unconfigured_ai_without_creating_tasks(
    db,
    org,
    course,
    chapter,
    activity,
    admin_user,
    mock_request,
    monkeypatch,
):
    assignment = Assignment(
        id=501,
        title="Fallback assignment",
        description="AI endpoint is not ready",
        due_date="2030-01-01",
        published=False,
        grading_type=GradingTypeEnum.NUMERIC,
        auto_grading=True,
        org_id=org.id,
        course_id=course.id,
        chapter_id=chapter.id,
        activity_id=activity.id,
        assignment_uuid="assignment_ai_unconfigured_no_tasks",
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(assignment)
    await db.commit()

    reserve_mock = AsyncMock()
    create_bank_mock = AsyncMock()
    monkeypatch.setattr("src.services.ai.assignments.check_resource_access", AsyncMock())
    monkeypatch.setattr("src.services.courses.activities.assignments.check_resource_access", AsyncMock())
    monkeypatch.setattr("src.services.ai.assignments.reserve_ai_credit", reserve_mock)
    monkeypatch.setattr("src.services.ai.assignments.create_question_bank_item", create_bank_mock)
    monkeypatch.setattr(
        "src.services.ai.assignments.get_learnhouse_config",
        lambda: SimpleNamespace(
            ai_config=SimpleNamespace(
                provider="openai_compatible",
                openai_base_url="",
                openai_api_key="",
                openai_model="",
            )
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        await generate_assignment_tasks(
            mock_request,
            GenerateAssignmentTasksRequest(
                assignment_uuid=assignment.assignment_uuid,
                assignment_title="澳門地理基礎練習",
                count=2,
                save_to_question_bank=True,
            ),
            admin_user,
            db,
        )

    assert exc_info.value.status_code == 503
    assert "AI 出題尚未配置完整" in exc_info.value.detail
    assert "題庫或手動建立" in exc_info.value.detail
    reserve_mock.assert_not_called()
    create_bank_mock.assert_not_called()


async def test_generate_assignment_tasks_rejects_malformed_json_without_creating_fallback_tasks(
    db,
    org,
    course,
    chapter,
    activity,
    admin_user,
    mock_request,
    monkeypatch,
):
    assignment = Assignment(
        id=551,
        title="澳門地理基礎練習",
        description="讓學生認識澳門位置",
        due_date="2030-01-01",
        published=False,
        grading_type=GradingTypeEnum.NUMERIC,
        auto_grading=True,
        org_id=org.id,
        course_id=course.id,
        chapter_id=chapter.id,
        activity_id=activity.id,
        assignment_uuid="assignment_ai_malformed_json_fallback",
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(assignment)
    await db.commit()

    reserve_mock = AsyncMock()
    refund_mock = Mock()
    create_bank_mock = AsyncMock()
    model_mock = Mock(return_value=SimpleNamespace(text="Here are some questions, but not JSON."))
    monkeypatch.setattr("src.services.ai.assignments.check_resource_access", AsyncMock())
    monkeypatch.setattr("src.services.courses.activities.assignments.check_resource_access", AsyncMock())
    monkeypatch.setattr("src.services.ai.assignments.enforce_ai_rate_limit", Mock())
    monkeypatch.setattr("src.services.ai.assignments.reserve_ai_credit", reserve_mock)
    monkeypatch.setattr("src.services.ai.assignments.refund_ai_credit", refund_mock)
    monkeypatch.setattr("src.services.ai.assignments.create_question_bank_item", create_bank_mock)
    monkeypatch.setattr(
        "src.services.ai.assignments.get_gemini_client",
        lambda: SimpleNamespace(
            models=SimpleNamespace(generate_content=model_mock),
        ),
    )
    monkeypatch.setattr(
        "src.services.ai.assignments.get_learnhouse_config",
        lambda: SimpleNamespace(
            ai_config=SimpleNamespace(
                provider="openai_compatible",
                openai_base_url="https://api.example.test/v1",
                openai_api_key="secret",
                openai_model="gpt-test",
            )
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        await generate_assignment_tasks(
            mock_request,
            GenerateAssignmentTasksRequest(
                assignment_uuid=assignment.assignment_uuid,
                count=2,
                save_to_question_bank=True,
            ),
            admin_user,
            db,
        )

    assert exc_info.value.status_code == 502
    assert "AI 沒有生成可自動批改" in exc_info.value.detail
    model_mock.assert_called_once()
    reserve_mock.assert_awaited_once()
    refund_mock.assert_called_once()
    create_bank_mock.assert_not_called()


async def test_generate_assignment_tasks_rejects_empty_ai_tasks_without_creating_fallback_tasks(
    db,
    org,
    course,
    chapter,
    activity,
    admin_user,
    mock_request,
    monkeypatch,
):
    assignment = Assignment(
        id=571,
        title="澳門科學基礎練習",
        description="讓學生認識水的三態",
        due_date="2030-01-01",
        published=False,
        grading_type=GradingTypeEnum.NUMERIC,
        auto_grading=True,
        org_id=org.id,
        course_id=course.id,
        chapter_id=chapter.id,
        activity_id=activity.id,
        assignment_uuid="assignment_ai_empty_tasks_no_fallback",
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(assignment)
    await db.commit()

    reserve_mock = AsyncMock()
    refund_mock = Mock()
    create_task_mock = AsyncMock()
    create_bank_mock = AsyncMock()
    model_mock = Mock(return_value=SimpleNamespace(text=json.dumps({"tasks": []})))
    monkeypatch.setattr("src.services.ai.assignments.check_resource_access", AsyncMock())
    monkeypatch.setattr("src.services.courses.activities.assignments.check_resource_access", AsyncMock())
    monkeypatch.setattr("src.services.ai.assignments.enforce_ai_rate_limit", Mock())
    monkeypatch.setattr("src.services.ai.assignments.reserve_ai_credit", reserve_mock)
    monkeypatch.setattr("src.services.ai.assignments.refund_ai_credit", refund_mock)
    monkeypatch.setattr("src.services.ai.assignments.create_assignment_task", create_task_mock)
    monkeypatch.setattr("src.services.ai.assignments.create_question_bank_item", create_bank_mock)
    monkeypatch.setattr(
        "src.services.ai.assignments.get_gemini_client",
        lambda: SimpleNamespace(
            models=SimpleNamespace(generate_content=model_mock),
        ),
    )
    monkeypatch.setattr(
        "src.services.ai.assignments.get_learnhouse_config",
        lambda: SimpleNamespace(
            ai_config=SimpleNamespace(
                provider="openai_compatible",
                openai_base_url="https://api.example.test/v1",
                openai_api_key="secret",
                openai_model="gpt-test",
            )
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        await generate_assignment_tasks(
            mock_request,
            GenerateAssignmentTasksRequest(
                assignment_uuid=assignment.assignment_uuid,
                count=2,
                save_to_question_bank=True,
            ),
            admin_user,
            db,
        )

    assert exc_info.value.status_code == 502
    assert "AI 沒有生成可自動批改" in exc_info.value.detail
    assert "未建立題目" in exc_info.value.detail
    model_mock.assert_called_once()
    reserve_mock.assert_awaited_once()
    refund_mock.assert_called_once()
    create_task_mock.assert_not_called()
    create_bank_mock.assert_not_called()


async def test_generate_assignment_tasks_only_fills_remaining_simple_pilot_slots(
    db,
    org,
    course,
    chapter,
    activity,
    admin_user,
    mock_request,
    monkeypatch,
):
    assignment = Assignment(
        id=601,
        title="Almost full assignment",
        description="Only one task slot remains",
        due_date="2030-01-01",
        published=False,
        grading_type=GradingTypeEnum.NUMERIC,
        auto_grading=True,
        org_id=org.id,
        course_id=course.id,
        chapter_id=chapter.id,
        activity_id=activity.id,
        assignment_uuid="assignment_ai_remaining_slots",
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(assignment)
    for index in range(2):
        db.add(
            AssignmentTask(
                id=610 + index,
                title=f"Existing Task {index + 1}",
                description="Existing simple task",
                hint="",
                reference_file=None,
                assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
                contents={
                    "prompt": f"What is item {index + 1}?",
                    "correct_answers": [str(index + 1)],
                    "match_mode": "exact",
                },
                max_grade_value=100,
                assignment_id=assignment.id,
                org_id=org.id,
                course_id=course.id,
                chapter_id=chapter.id,
                activity_id=activity.id,
                assignment_task_uuid=f"assignmenttask_ai_existing_{index + 1}",
                creation_date=str(datetime.now()),
                update_date=str(datetime.now()),
            )
        )
    await db.commit()

    monkeypatch.setattr("src.services.ai.assignments.check_resource_access", AsyncMock())
    monkeypatch.setattr("src.services.courses.activities.assignments.check_resource_access", AsyncMock())
    monkeypatch.setattr("src.services.ai.assignments.enforce_ai_rate_limit", Mock())
    monkeypatch.setattr("src.services.ai.assignments.reserve_ai_credit", AsyncMock())
    model_mock = Mock(
        return_value=SimpleNamespace(
            text=json.dumps(
                {
                    "tasks": [
                        {
                            "assignment_type": "SHORT_ANSWER",
                            "title": "澳門地理短問答",
                            "contents": {
                                "prompt": "澳門位於哪個國家？",
                                "correct_answers": ["中國"],
                            },
                        }
                    ]
                }
            )
        )
    )
    monkeypatch.setattr(
        "src.services.ai.assignments.get_gemini_client",
        lambda: SimpleNamespace(
            models=SimpleNamespace(generate_content=model_mock),
        ),
    )
    monkeypatch.setattr(
        "src.services.ai.assignments.get_learnhouse_config",
        lambda: SimpleNamespace(
            ai_config=SimpleNamespace(
                provider="openai_compatible",
                openai_base_url="https://api.example.test/v1",
                openai_api_key="secret",
                openai_model="gpt-test",
            )
        ),
    )

    response = await generate_assignment_tasks(
        mock_request,
        GenerateAssignmentTasksRequest(
            assignment_uuid=assignment.assignment_uuid,
            assignment_title="澳門地理基礎練習",
            count=10,
        ),
        admin_user,
        db,
    )

    assert len(response.tasks) == 1
    assert any("本次只補 1 題" in warning for warning in response.warnings)
    assert not any("備用題" in warning for warning in response.warnings)


async def test_generate_assignment_tasks_enables_simple_learning_settings(
    db,
    org,
    course,
    chapter,
    activity,
    admin_user,
    mock_request,
    monkeypatch,
):
    assignment = Assignment(
        id=701,
        title="澳門地理基礎練習",
        description="讓學生掌握澳門所在位置",
        due_date="2030-01-01",
        published=False,
        grading_type=GradingTypeEnum.NUMERIC,
        auto_grading=False,
        allow_retries=False,
        show_correct_answers=False,
        score_policy="latest",
        teacher_review_required=True,
        teacher_review_status="pending",
        org_id=org.id,
        course_id=course.id,
        chapter_id=chapter.id,
        activity_id=activity.id,
        assignment_uuid="assignment_ai_simple_learning_settings",
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(assignment)
    await db.commit()

    monkeypatch.setattr("src.services.ai.assignments.check_resource_access", AsyncMock())
    monkeypatch.setattr("src.services.courses.activities.assignments.check_resource_access", AsyncMock())
    monkeypatch.setattr("src.services.ai.assignments.enforce_ai_rate_limit", Mock())
    monkeypatch.setattr("src.services.ai.assignments.reserve_ai_credit", AsyncMock())
    model_mock = Mock(
        return_value=SimpleNamespace(
            text=json.dumps(
                {
                    "tasks": [
                        {
                            "assignment_type": "QUIZ",
                            "title": "澳門位置選擇題",
                            "contents": {
                                "questions": [
                                    {
                                        "questionText": "澳門位於哪個國家？",
                                        "options": [
                                            {"text": "中國", "assigned_right_answer": True},
                                            {"text": "日本", "assigned_right_answer": False},
                                        ],
                                    }
                                ]
                            },
                        }
                    ]
                }
            )
        )
    )
    monkeypatch.setattr(
        "src.services.ai.assignments.get_gemini_client",
        lambda: SimpleNamespace(
            models=SimpleNamespace(generate_content=model_mock),
        ),
    )
    monkeypatch.setattr(
        "src.services.ai.assignments.get_learnhouse_config",
        lambda: SimpleNamespace(
            ai_config=SimpleNamespace(
                provider="openai_compatible",
                openai_base_url="https://api.example.test/v1",
                openai_api_key="secret",
                openai_model="gpt-test",
            )
        ),
    )

    response = await generate_assignment_tasks(
        mock_request,
        GenerateAssignmentTasksRequest(
            assignment_uuid=assignment.assignment_uuid,
            count=1,
        ),
        admin_user,
        db,
    )

    await db.refresh(assignment)
    assert len(response.tasks) == 1
    assert assignment.auto_grading is True
    assert assignment.allow_retries is True
    assert assignment.show_correct_answers is True
    assert assignment.score_policy == "highest"
    assert assignment.teacher_review_required is False
    assert assignment.teacher_review_status == "not_required"
    assert any("已自動設定為簡單學習作業" in warning for warning in response.warnings)
