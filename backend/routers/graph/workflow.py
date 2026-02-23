"""
LangGraph workflow: resume intelligence pipeline with deterministic branching and retry.
Do not mix graph logic with route definitions; routes invoke this workflow.
"""
import logging
from typing import Literal

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from backend.routers.graph.state import ResumeWorkflowState
from backend.routers.graph.nodes import (
    input_normalization_node,
    jd_expansion_node,
    skill_extraction_node,
    questionnaire_generation_node,
    user_answer_evaluation_node,
    skill_gap_analysis_node,
    resume_generation_node,
    docx_rendering_node,
)

logger = logging.getLogger(__name__)

MAX_RETRIES = 2


def _route_after_input(state: ResumeWorkflowState) -> Literal["jd_expansion", "skill_extraction"]:
    if state.get("error"):
        return "skill_extraction"
    if state.get("needs_jd_expansion"):
        return "jd_expansion"
    return "skill_extraction"


def _route_after_skill_extraction(state: ResumeWorkflowState) -> Literal["skill_extraction", "questionnaire_generation"]:
    if state.get("extraction_validation_error") and (state.get("retry_count") or 0) < MAX_RETRIES:
        return "skill_extraction"
    return "questionnaire_generation"


def _route_after_questionnaire(state: ResumeWorkflowState) -> Literal["questionnaire_generation", "user_answer_evaluation", "end"]:
    if state.get("questionnaire_validation_error") and (state.get("retry_count") or 0) < MAX_RETRIES:
        return "questionnaire_generation"
    if not state.get("user_answers"):
        return "end"
    return "user_answer_evaluation"


def _route_after_evaluation(state: ResumeWorkflowState) -> Literal["user_answer_evaluation", "skill_gap_analysis"]:
    if state.get("evaluation_validation_error") and (state.get("retry_count") or 0) < MAX_RETRIES:
        return "user_answer_evaluation"
    return "skill_gap_analysis"


def _route_after_gap(state: ResumeWorkflowState) -> Literal["skill_gap_analysis", "resume_generation"]:
    if state.get("gap_validation_error") and (state.get("retry_count") or 0) < MAX_RETRIES:
        return "skill_gap_analysis"
    return "resume_generation"


def _route_after_resume(state: ResumeWorkflowState) -> Literal["resume_generation", "docx_rendering"]:
    if state.get("resume_validation_error") and (state.get("retry_count") or 0) < MAX_RETRIES:
        return "resume_generation"
    return "docx_rendering"


def build_resume_workflow():
    graph = StateGraph(ResumeWorkflowState)

    graph.add_node("input_normalization", input_normalization_node)
    graph.add_node("jd_expansion", jd_expansion_node)
    graph.add_node("skill_extraction", skill_extraction_node)
    graph.add_node("questionnaire_generation", questionnaire_generation_node)
    graph.add_node("user_answer_evaluation", user_answer_evaluation_node)
    graph.add_node("skill_gap_analysis", skill_gap_analysis_node)
    graph.add_node("resume_generation", resume_generation_node)
    graph.add_node("docx_rendering", docx_rendering_node)

    graph.set_entry_point("input_normalization")

    graph.add_conditional_edges(
        "input_normalization",
        _route_after_input,
        {"jd_expansion": "jd_expansion", "skill_extraction": "skill_extraction"},
    )
    graph.add_edge("jd_expansion", "skill_extraction")
    graph.add_conditional_edges(
        "skill_extraction",
        _route_after_skill_extraction,
        {"skill_extraction": "skill_extraction", "questionnaire_generation": "questionnaire_generation"},
    )
    graph.add_conditional_edges(
        "questionnaire_generation",
        _route_after_questionnaire,
        {
            "questionnaire_generation": "questionnaire_generation",
            "user_answer_evaluation": "user_answer_evaluation",
            "end": END,
        },
    )
    graph.add_conditional_edges(
        "user_answer_evaluation",
        _route_after_evaluation,
        {"user_answer_evaluation": "user_answer_evaluation", "skill_gap_analysis": "skill_gap_analysis"},
    )
    graph.add_conditional_edges(
        "skill_gap_analysis",
        _route_after_gap,
        {"skill_gap_analysis": "skill_gap_analysis", "resume_generation": "resume_generation"},
    )
    graph.add_conditional_edges(
        "resume_generation",
        _route_after_resume,
        {"resume_generation": "resume_generation", "docx_rendering": "docx_rendering"},
    )
    graph.add_edge("docx_rendering", END)

    memory = MemorySaver()
    return graph.compile(checkpointer=memory)
