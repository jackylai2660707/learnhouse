from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

import src.services.ai.images as image_service
from src.services.ai.base import ImageGenerationProviderError
from src.services.ai.schemas.images import GenerateImageRequest


@pytest.mark.asyncio
async def test_image_generation_failure_refunds_credit_and_returns_safe_fallback(
    monkeypatch,
    db,
    regular_user,
    mock_request,
    org,
    activity,
):
    check_access = AsyncMock()
    reserve_credit = AsyncMock()
    refund_credit = Mock()
    monkeypatch.setattr(image_service, "check_resource_access", check_access)
    monkeypatch.setattr(image_service, "enforce_ai_rate_limit", Mock())
    monkeypatch.setattr(image_service, "reserve_ai_credit", reserve_credit)
    monkeypatch.setattr(
        image_service,
        "generate_openai_compatible_image",
        Mock(
            side_effect=ImageGenerationProviderError(
                "ai_image_timeout",
                retryable=True,
            )
        ),
    )
    monkeypatch.setattr(
        "src.security.features_utils.usage.refund_ai_credit",
        refund_credit,
    )

    with pytest.raises(HTTPException) as exc_info:
        await image_service.generate_activity_image_block(
            mock_request,
            GenerateImageRequest(
                org_id=org.id,
                activity_uuid=activity.activity_uuid,
                prompt="合成的澳門校園科學插圖",
            ),
            regular_user,
            db,
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == {
        "code": "ai_image_timeout",
        "message": "AI 生圖暫時不可用，你可以稍後重試或先手動上傳圖片。",
        "retryable": True,
    }
    reserve_credit.assert_awaited_once()
    refund_credit.assert_called_once_with(org.id, 3)
