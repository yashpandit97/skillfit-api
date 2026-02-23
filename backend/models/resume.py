from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from backend.db.session import Base


class ResumeVersion(Base):
    __tablename__ = "resume_versions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    job_submission_id = Column(Integer, ForeignKey("job_submissions.id"), nullable=True, index=True)
    content_json = Column(Text, nullable=True)  # Structured resume data
    file_path = Column(String(1024), nullable=True)  # Stored .docx path if saved
    version = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="resume_versions")
    job_submission = relationship("JobSubmission", back_populates="resume_versions")
