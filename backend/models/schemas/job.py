from pydantic import BaseModel, Field
from typing import Optional


class JobInputRequest(BaseModel):
    job_title: Optional[str] = None
    job_description: Optional[str] = None

    def has_full_jd(self) -> bool:
        return bool(self.job_description and len(self.job_description.strip()) > 50)


class JobInputResponse(BaseModel):
    job_submission_id: int
    job_title: Optional[str] = None
    normalized_description: Optional[str] = None
    extracted_skills: Optional[dict] = None
    questionnaire: Optional[list] = None
    message: str = "ok"


class QuestionnaireItem(BaseModel):
    id: str
    concept: str
    category: str  # fundamentals, tools, advanced_concepts, real_world, metrics_impact
    description: Optional[str] = None
    stage: Optional[int] = None  # 1-based; present when using variable questions per stage


class QuestionnaireResponse(BaseModel):
    questions: list[QuestionnaireItem]
    current_stage: int = 1  # 1-5
    total_stages: int = 5
    ready: bool = True  # False when submission exists but questionnaire not yet generated


class UserAnswersPayload(BaseModel):
    answers: dict[str, str]  # question_id -> "yes" | "no" only


class StageAnswersRequest(BaseModel):
    stage: int  # 1-5
    answers: dict[str, str]  # question_id -> "yes" | "no" for this stage only


class StageAnswersResponse(BaseModel):
    next_stage_questions: list[QuestionnaireItem] = Field(default_factory=list)
    current_stage: int = 1
    done: bool = False
    message: str = ""


class SkillAreaScore(BaseModel):
    area: str
    score: int  # 0-100
    missing_concepts: list[str] = Field(default_factory=list)
    strong_areas: list[str] = Field(default_factory=list)
    recommendation: Optional[str] = None


class EvaluationResultResponse(BaseModel):
    scores: list[SkillAreaScore]
    overall_score: Optional[int] = None
    summary: Optional[str] = None
    concepts_to_prepare: Optional[list[str]] = None
