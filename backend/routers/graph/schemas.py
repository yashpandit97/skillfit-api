"""
Pydantic schemas for LLM structured outputs in graph nodes. Enforce JSON shape.
"""
from pydantic import BaseModel, Field, field_validator
from typing import Optional, Any


# --- Skill extraction ---
class ExtractedSkills(BaseModel):
    required_skills: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    experience_level: str = "mid"
    ats_keywords: list[str] = Field(default_factory=list)


# --- Questionnaire: concept-awareness yes/no (no free text) ---
VALID_CATEGORIES = {"fundamentals", "tools", "advanced_concepts", "real_world", "metrics_impact"}


def _normalize_category(v: str) -> str:
    if not v or not isinstance(v, str):
        return "fundamentals"
    normalized = v.strip().lower().replace(" ", "_").replace("-", "_")
    if normalized in VALID_CATEGORIES:
        return normalized
    for valid in VALID_CATEGORIES:
        if valid in normalized or normalized in valid:
            return valid
    return "fundamentals"


class QuestionnaireItemSchema(BaseModel):
    id: str
    concept: str  # Short name of skill/concept
    category: str  # fundamentals, tools, advanced_concepts, real_world, metrics_impact
    description: Optional[str] = None  # One-line clarification if needed

    @field_validator("id", mode="before")
    @classmethod
    def coerce_id(cls, v: Any) -> str:
        if v is None:
            return "q0"
        return str(v).strip()

    @field_validator("concept", mode="before")
    @classmethod
    def coerce_concept(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v).strip() or "Concept"

    @field_validator("category", mode="before")
    @classmethod
    def normalize_category(cls, v: str) -> str:
        if not v or not isinstance(v, str):
            return "fundamentals"
        normalized = v.strip().lower().replace(" ", "_").replace("-", "_")
        if normalized in VALID_CATEGORIES:
            return normalized
        for valid in VALID_CATEGORIES:
            if valid in normalized or normalized in valid:
                return valid
        return "fundamentals"


class QuestionnaireSchema(BaseModel):
    questions: list[QuestionnaireItemSchema]


# --- Evaluation (yes/no answers → scores and missing concepts) ---
class SkillAreaScoreSchema(BaseModel):
    area: str
    score: int  # 0-100
    missing_concepts: list[str] = Field(default_factory=list)
    strong_areas: list[str] = Field(default_factory=list)
    recommendation: Optional[str] = None


class EvaluationSchema(BaseModel):
    scores: list[SkillAreaScoreSchema]
    overall_score: Optional[int] = None
    summary: Optional[str] = None
    concepts_to_prepare: list[str] = Field(default_factory=list)


# --- Skill gap ---
class ResumeRiskClaimSchema(BaseModel):
    claim: str
    risk: str


class SkillGapSchema(BaseModel):
    weaknesses: list[str] = Field(default_factory=list)
    improvement_suggestions: list[str] = Field(default_factory=list)
    resume_risk_claims: list[ResumeRiskClaimSchema] = Field(default_factory=list)
    overall_gap_severity: str = "medium"
