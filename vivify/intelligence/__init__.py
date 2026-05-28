"""项目智能分析模块。

负责扫描项目、识别场景、推荐配置并通过交互式问答完成 ``vivify init``。
"""

from __future__ import annotations

from .ai_analyzer import AIAnalyzer, AIAnalysisResult
from .classifier import Classifier, ProjectProfile, ScenarioType
from .configurator import ConfigQuestion, Configurator, SCENARIO_FIXERS, SCENARIO_PROBES
from .interviewer import Interviewer
from .scanner import ProjectSignals, Scanner
from .wiki_generator import (
    DEFAULT_WIKI_DIR,
    WikiContext,
    generate_wiki,
    load_wiki_context_if_available,
    parse_wiki_metadata,
)

__all__ = [
    "AIAnalyzer",
    "AIAnalysisResult",
    "Scanner",
    "ProjectSignals",
    "Classifier",
    "ProjectProfile",
    "ScenarioType",
    "Configurator",
    "ConfigQuestion",
    "SCENARIO_PROBES",
    "SCENARIO_FIXERS",
    "Interviewer",
    "WikiContext",
    "DEFAULT_WIKI_DIR",
    "generate_wiki",
    "parse_wiki_metadata",
    "load_wiki_context_if_available",
]
