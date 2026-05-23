"""Goal management — parse, decompose, dedupe."""
from auto_heal.goals.decomposer import AgentGoalDecomposer, GoalDecomposerConfig
from auto_heal.goals.differ import is_duplicate, title_similarity
from auto_heal.goals.parser import GoalsDoc, parse_goal_list, parse_goals

__all__ = [
    "AgentGoalDecomposer",
    "GoalDecomposerConfig",
    "GoalsDoc",
    "is_duplicate",
    "parse_goal_list",
    "parse_goals",
    "title_similarity",
]
