from pydantic import BaseModel, Field


class ResumeRiskClaim(BaseModel):
    claim: str
    risk: str  # e.g. "No measurable impact", "Concept not demonstrated"


class SkillGapSummary(BaseModel):
    weaknesses: list[str] = Field(default_factory=list)
    improvement_suggestions: list[str] = Field(default_factory=list)
    resume_risk_claims: list[ResumeRiskClaim] = Field(default_factory=list)
    overall_gap_severity: str = "medium"  # low, medium, high
