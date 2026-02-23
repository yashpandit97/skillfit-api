from pydantic import BaseModel, Field
from typing import Optional


class DeficiencyAnnotation(BaseModel):
    """Inline comment for weak/missing content in resume."""
    placeholder: str  # e.g. "[Needs deeper experience in X]"
    severity: str = "medium"  # low, medium, high


class ResumeBullet(BaseModel):
    text: str
    is_deficient: bool = False
    deficiency_comment: Optional[str] = None  # Shown in red / as comment


class ResumeSection(BaseModel):
    heading: str  # Summary, Skills, Experience, Projects, Education, Certifications
    bullets: list[ResumeBullet] = Field(default_factory=list)
    subsections: list["ResumeSection"] = Field(default_factory=list)


class ResumeStructured(BaseModel):
    """ATS-friendly resume structure. No tables, standard headings."""
    summary: Optional[str] = None
    summary_deficiency: Optional[str] = None
    sections: list[ResumeSection] = Field(default_factory=list)
    max_pages: int = 2


ResumeSection.model_rebuild()
