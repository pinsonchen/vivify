"""Goal management — parse, decompose, dedupe."""
from vivify.goals.decomposer import AgentGoalDecomposer, GoalDecomposerConfig
from vivify.goals.differ import is_duplicate, title_similarity
from vivify.goals.parser import GoalsDoc, parse_goal_list, parse_goals

__all__ = [
    "AgentGoalDecomposer",
    "GoalDecomposerConfig",
    "GoalsDoc",
    "is_duplicate",
    "parse_goal_list",
    "parse_goals",
    "title_similarity",
]
