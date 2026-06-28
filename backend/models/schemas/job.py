from pydantic import BaseModel, Field
from typing import Optional


class JobInputRequest(BaseModel):
    job_title: Optional[str] = None
    job_description: Optional[str] = None
    company_name: Optional[str] = None

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
    progress_pct: int = 0  # 0–100 for progress bar
    ready: bool = True  # False when submission exists but questionnaire not yet generated


VALID_ANSWER_VALUES = {"yes", "no", "a_bit"}


class UserAnswersPayload(BaseModel):
    answers: dict[str, str]  # question_id -> "yes" | "no" | "a_bit"


class StageAnswersRequest(BaseModel):
    stage: int  # 1-5
    answers: dict[str, str]  # question_id -> "yes" | "no" | "a_bit" for this stage only


class StageAnswersResponse(BaseModel):
    next_stage_questions: list[QuestionnaireItem] = Field(default_factory=list)
    current_stage: int = 1
    total_stages: int = 1
    progress_pct: int = 0  # 0–100 for progress bar
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


class JobSubmissionListItem(BaseModel):
    id: int
    job_title: Optional[str] = None
    company_name: Optional[str] = None
    status: str
    workflow_mode: Optional[str] = None
    created_at: str


class FitReportItemResponse(BaseModel):
    topic: str
    detail: str
    study_urls: list[str] = Field(default_factory=list)


class FitReportData(BaseModel):
    overall_fit_score: int
    role_readiness_summary: str
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    preparation_plan: list[FitReportItemResponse] = Field(default_factory=list)
    ats_keywords_matched: list[str] = Field(default_factory=list)
    ats_keywords_missing: list[str] = Field(default_factory=list)


class FitReportResponse(BaseModel):
    job_submission_id: int
    job_title: Optional[str] = None
    company_name: Optional[str] = None
    status: str
    fit_report: FitReportData


class JobListResponse(BaseModel):
    items: list[JobSubmissionListItem]


class JobCompareItem(BaseModel):
    job_submission_id: int
    job_title: Optional[str] = None
    company_name: Optional[str] = None
    extracted_skills: Optional[dict] = None
    skill_gap_summary: Optional[dict] = None
    overall_gap_severity: str = "medium"


class JobCompareResponse(BaseModel):
    job_1: JobCompareItem
    job_2: JobCompareItem
