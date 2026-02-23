"""
Pytest fixtures: mock Ollama service for deterministic tests.
"""
import pytest
from unittest.mock import MagicMock
from backend.services.ollama_llm_service import OllamaLLMService


@pytest.fixture
def mock_llm_service():
    """Replace LLM calls with deterministic JSON responses."""
    service = MagicMock(spec=OllamaLLMService)
    service.invoke.return_value = '{"required_skills": ["Python"], "required_tools": ["Git"], "concepts": ["OOP"], "responsibilities": ["Develop"], "experience_level": "mid", "ats_keywords": ["Python", "API"]}'
    service.invoke_structured.return_value = MagicMock(
        model_dump=lambda: {
            "required_skills": ["Python", "FastAPI"],
            "required_tools": ["Git", "Docker"],
            "concepts": ["REST", "OOP"],
            "responsibilities": ["Design APIs", "Write tests"],
            "experience_level": "mid",
            "ats_keywords": ["Python", "API", "SQL"],
        }
    )
    service.invoke_json_dict.return_value = {
        "summary": "Experienced developer.",
        "summary_deficiency": None,
        "sections": [
            {"heading": "Skills", "bullets": [{"text": "Python", "is_deficient": False, "deficiency_comment": None}]},
            {"heading": "Experience", "bullets": [{"text": "Led projects", "is_deficient": True, "deficiency_comment": "[No measurable impact provided]"}]},
        ],
        "max_pages": 2,
    }
    return service
