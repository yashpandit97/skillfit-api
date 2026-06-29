"""
Skill gap dashboard: list gaps and improvement suggestions with study URLs.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from backend.utils.auth import get_current_user
from backend.models.user import User
from backend.db import get_db_dependency
from backend.models.job import JobSubmission
from backend.models.skill_gap import SkillGapRecord
from backend.utils.study_urls import sanitize_study_urls

router = APIRouter(prefix="/gap", tags=["skill_gap"])


class StudyUrls(BaseModel):
    """Study materials: article and documentation URLs only."""
    websites: list[str] = Field(default_factory=list)
    youtube: list[str] = Field(default_factory=list)


class WeaknessItem(BaseModel):
    """One weakness with optional study links."""
    text: str
    study_urls: StudyUrls = Field(default_factory=lambda: StudyUrls())


class ImprovementItem(BaseModel):
    """One improvement suggestion with optional study links."""
    text: str
    study_urls: StudyUrls = Field(default_factory=lambda: StudyUrls())


def _normalize_gap_items(raw_list: list) -> list[dict]:
    """Convert stored gap data (strings or objects) to list of {text, study_urls}."""
    out = []
    for x in raw_list or []:
        if isinstance(x, str):
            out.append({"text": x, "study_urls": {"websites": [], "youtube": []}})
        elif isinstance(x, dict):
            su = x.get("study_urls") or {}
            if not isinstance(su, dict):
                su = {}
            out.append({
                "text": x.get("text", ""),
                "study_urls": sanitize_study_urls(su),
            })
        else:
            out.append({"text": "", "study_urls": {"websites": [], "youtube": []}})
    return out


class SkillGapDashboardItem(BaseModel):
    job_submission_id: int
    weaknesses: list[WeaknessItem]
    improvement_suggestions: list[ImprovementItem]
    resume_risk_claims: list[dict]
    overall_gap_severity: str
    scores_by_area: list[dict] | None


class SkillGapDashboardResponse(BaseModel):
    items: list[SkillGapDashboardItem]


@router.get("/{job_submission_id}/skill-graph")
def get_skill_graph(
    job_submission_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db_dependency)],
):
    """Return nodes (concepts/skills with strength: strong, partial, gap) and edges for graph visualization."""
    submission = db.query(JobSubmission).filter(
        JobSubmission.id == job_submission_id,
        JobSubmission.user_id == current_user.id,
    ).first()
    if not submission:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    extracted = submission.extracted_skills or {}
    answers = submission.user_answers or {}
    questionnaire = submission.questionnaire or []
    gap = submission.skill_gap_summary or {}
    missing = set()
    for s in (gap.get("weaknesses") or []):
        if isinstance(s, dict) and s.get("text"):
            missing.add(s.get("text", "").strip())
        elif isinstance(s, str):
            missing.add(s.strip())
    nodes = []
    seen = set()
    for q in questionnaire:
        if not isinstance(q, dict):
            continue
        concept = (q.get("concept") or "").strip()
        if not concept or concept in seen:
            continue
        seen.add(concept)
        ans = (answers.get(q.get("id") or "") or "").strip().lower()
        if ans == "yes":
            strength = "strong"
        elif ans == "a_bit":
            strength = "partial"
        else:
            strength = "gap"
        nodes.append({"id": f"n_{len(nodes)}", "label": concept, "category": q.get("category") or "fundamentals", "strength": strength})
    for skill in (extracted.get("required_skills") or [])[:15]:
        if isinstance(skill, str) and skill.strip() and skill.strip() not in seen:
            seen.add(skill.strip())
            nodes.append({"id": f"n_{len(nodes)}", "label": skill.strip(), "category": "skills", "strength": "gap"})
    edges = []
    for i, n1 in enumerate(nodes):
        for j, n2 in enumerate(nodes):
            if i < j and n1.get("category") == n2.get("category"):
                edges.append({"source": n1["id"], "target": n2["id"], "type": "same_category"})
    return {"nodes": nodes, "edges": edges[:50]}


@router.get("/{job_submission_id}/export")
def export_gap_report(
    job_submission_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db_dependency)],
    format: str = "md",
):
    """Export gap report as markdown or PDF. format=md|pdf."""
    submission = db.query(JobSubmission).filter(
        JobSubmission.id == job_submission_id,
        JobSubmission.user_id == current_user.id,
    ).first()
    if not submission:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    gap = submission.skill_gap_summary or {}
    weaknesses = _normalize_gap_items(gap.get("weaknesses", []))
    improvements = _normalize_gap_items(gap.get("improvement_suggestions", []))
    risk_claims = gap.get("resume_risk_claims", [])
    md_lines = [
        "# Skill Gap Report",
        f"Job #{job_submission_id}",
        "",
        "## Weaknesses",
    ]
    for w in weaknesses:
        md_lines.append(f"- {w.get('text', '')}")
    md_lines.extend(["", "## Improvement suggestions"])
    for i in improvements:
        md_lines.append(f"- {i.get('text', '')}")
    if risk_claims:
        md_lines.extend(["", "## Resume risk claims"])
        for r in risk_claims:
            if isinstance(r, dict):
                md_lines.append(f"- **{r.get('claim', '')}** — {r.get('risk', '')}")
    md_content = "\n".join(md_lines)
    if format == "pdf":
        from fastapi.responses import Response
        try:
            from reportlab.lib.pagesizes import letter
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
            from reportlab.lib.styles import getSampleStyleSheet
            import io
            buf = io.BytesIO()
            doc = SimpleDocTemplate(buf, pagesize=letter)
            styles = getSampleStyleSheet()
            story = []
            for line in md_content.split("\n"):
                s = (line or "").strip()
                if not s:
                    story.append(Spacer(1, 6))
                    continue
                safe = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                if safe.startswith("## "):
                    story.append(Paragraph(f"<b>{safe[3:]}</b>", styles["Heading2"]))
                elif safe.startswith("# "):
                    story.append(Paragraph(f"<b>{safe[2:]}</b>", styles["Heading1"]))
                elif safe.startswith("- "):
                    story.append(Paragraph(f"• {safe[2:]}", styles["Normal"]))
                else:
                    story.append(Paragraph(safe, styles["Normal"]))
                story.append(Spacer(1, 4))
            doc.build(story)
            buf.seek(0)
            return Response(content=buf.read(), media_type="application/pdf", headers={"Content-Disposition": "attachment; filename=gap-report.pdf"})
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(md_content, media_type="text/markdown", headers={"Content-Disposition": "attachment; filename=gap-report.md"})


@router.get("/{job_submission_id}", response_model=SkillGapDashboardItem)
def get_gap_for_job(
    job_submission_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db_dependency)],
):
    """Get skill gap summary for a job submission."""
    submission = db.query(JobSubmission).filter(
        JobSubmission.id == job_submission_id,
        JobSubmission.user_id == current_user.id,
    ).first()
    if not submission:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    gap = submission.skill_gap_summary or {}
    weaknesses = _normalize_gap_items(gap.get("weaknesses", []))
    improvement_suggestions = _normalize_gap_items(gap.get("improvement_suggestions", []))
    return SkillGapDashboardItem(
        job_submission_id=submission.id,
        weaknesses=[WeaknessItem(**w) for w in weaknesses],
        improvement_suggestions=[ImprovementItem(**i) for i in improvement_suggestions],
        resume_risk_claims=gap.get("resume_risk_claims", []),
        overall_gap_severity=gap.get("overall_gap_severity", "medium"),
        scores_by_area=submission.evaluation_result.get("scores") if submission.evaluation_result else None,
    )


@router.get("/", response_model=SkillGapDashboardResponse)
def list_gaps(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db_dependency)],
):
    """List all skill gap records for the current user."""
    records = db.query(SkillGapRecord).filter(SkillGapRecord.user_id == current_user.id).order_by(SkillGapRecord.created_at.desc()).all()
    items = []
    for r in records:
        gap = r.gap_summary or {}
        weaknesses = _normalize_gap_items(gap.get("weaknesses", []))
        improvement_suggestions = _normalize_gap_items(gap.get("improvement_suggestions", []))
        items.append(
            SkillGapDashboardItem(
                job_submission_id=r.job_submission_id or 0,
                weaknesses=[WeaknessItem(**w) for w in weaknesses],
                improvement_suggestions=[ImprovementItem(**i) for i in improvement_suggestions],
                resume_risk_claims=gap.get("resume_risk_claims", []),
                overall_gap_severity=gap.get("overall_gap_severity", "medium"),
                scores_by_area=r.scores_by_area,
            )
        )
    return SkillGapDashboardResponse(items=items)
