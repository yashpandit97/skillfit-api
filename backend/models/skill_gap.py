from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship

from backend.db.session import Base


class SkillGapRecord(Base):
    __tablename__ = "skill_gap_records"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    job_submission_id = Column(Integer, nullable=True, index=True)
    gap_summary = Column(JSON, nullable=True)  # Aggregated weaknesses, suggestions
    scores_by_area = Column(JSON, nullable=True)
    resume_risk_claims = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="skill_gap_records")
