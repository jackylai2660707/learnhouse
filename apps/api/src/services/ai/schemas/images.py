from typing import Literal

from pydantic import BaseModel, Field

from src.db.courses.blocks import BlockRead


class GenerateImageRequest(BaseModel):
    org_id: int
    activity_uuid: str
    prompt: str = Field(min_length=3, max_length=4000)
    size: Literal["1024x1024", "1024x1536", "1536x1024"] = "1024x1024"
    quality: Literal["low", "medium", "high", "auto"] | None = None
    background: Literal["transparent", "opaque", "auto"] | None = None
    model: str | None = None


class GenerateImageResponse(BaseModel):
    block: BlockRead
    file_name: str
    file_path: str
    public_url: str
    revised_prompt: str | None = None
