from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship

from backend.db.session import Base


class JobSubmission(Base):
    __tablename__ = "job_submissions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    company_name = Column(String(255), nullable=True)
    job_title = Column(String(500), nullable=True)  # When only title provided
    job_description_raw = Column(Text, nullable=True)  # Full JD text
    normalized_input = Column(Text, nullable=True)  # After normalization/expansion
    extracted_skills = Column(JSON, nullable=True)  # Structured skill extraction
    questionnaire = Column(JSON, nullable=True)  # Generated questions (50 total: 5 stages × 10)
    user_answers = Column(JSON, nullable=True)  # User responses (merged after each stage)
    current_stage = Column(Integer, default=1, nullable=False)  # 1-5: which stage we're on
    evaluation_result = Column(JSON, nullable=True)  # Scores per area
    skill_gap_summary = Column(JSON, nullable=True)
    fit_report = Column(JSON, nullable=True)  # Structured fit analysis when workflow_mode=fit_report
    workflow_mode = Column(String(50), nullable=True)  # questionnaire | fit_report
    status = Column(String(50), default="draft")  # draft, questionnaire_ready, fit_report_ready, resume_generated
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="job_submissions")
    resume_versions = relationship("ResumeVersion", back_populates="job_submission")
