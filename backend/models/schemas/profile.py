from pydantic import BaseModel
from typing import Optional


class ProfileUpdate(BaseModel):
    skills: Optional[list[str]] = None
    experience_summary: Optional[str] = None


class ProfileResponse(BaseModel):
    skills: list[str] = []
    experience_summary: Optional[str] = None
    baseline_resume_json: Optional[dict] = None

    class Config:
        from_attributes = True
