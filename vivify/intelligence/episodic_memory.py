"""L2 Episodic Memory - recent fix context injection for improved hit rate.

Unlike Skill Capsules (distilled strategy templates), Episodic Memory provides
raw recent fix records with concrete context (files, error info, diff) to help
the agent recognise similar situations and avoid re-discovering known solutions.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Episode:
    """A single episodic memory entry — a past fix event."""

    action_id: str
    probe_id: str
    issue_category: str
    issue_summary: str
    fix_summary: str  # action_log.result_summary
    files_changed: List[str]  # modified files
    timestamp: datetime
    success: bool

    @property
    def age_hours(self) -> float:
        """Hours since this episode occurred."""
        now = datetime.now(timezone.utc)
        ts = self.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (now - ts).total_seconds() / 3600

    @property
    def recency_weight(self) -> float:
        """Recency-based weight (1.0 for today, decaying over 7 days)."""
        hours = self.age_hours
        if hours <= 24:
            return 1.0
        return max(0.1, 1.0 - (hours / (7 * 24)))


class EpisodicMemory:
    """Manages recent fix episodes for context injection.

    Retrieves successful fix episodes from the last N days,
    finds similar ones by probe_id and keyword matching,
    and formats them for prompt injection.
    """

    def __init__(self, storage, window_days: int = 7, max_episodes: int = 3):
        """
        Args:
            storage: StorageProvider instance
            window_days: How many days back to search
            max_episodes: Maximum episodes to inject into prompt
        """
        self._storage = storage
        self._window_days = window_days
        self._max_episodes = max_episodes

    def recall_similar(self, probe_id: str, issue_text: str) -> List[Episode]:
        """Find similar past episodes for the given issue.

        Matching strategy:
        1. Same probe_id (exact match, highest relevance)
        2. Similar issue keywords (token overlap)
        3. Sort by recency_weight × relevance_score
        4. Return top N
        """
        try:
            recent_actions = self._storage.get_recent_successful_actions(
                days=self._window_days
            )
        except Exception as e:
            logger.debug("episodic recall failed to query storage: %s", e)
            return []

        episodes: list[tuple[Episode, float]] = []
        for action in recent_actions:
            episode = self._action_to_episode(action)
            if episode is None:
                continue

            relevance = self._calculate_relevance(episode, probe_id, issue_text)
            if relevance > 0.3:  # Minimum threshold
                episodes.append((episode, relevance))

        # Sort by combined score (recency × relevance)
        episodes.sort(key=lambda x: x[0].recency_weight * x[1], reverse=True)

        return [ep for ep, _ in episodes[: self._max_episodes]]

    def format_for_prompt(self, episodes: List[Episode]) -> str:
        """Format episodes into a prompt-injectable string.

        Returns empty string when no episodes are available, ensuring
        zero impact on the prompt when there's nothing to inject.
        """
        if not episodes:
            return ""

        lines = ["## Recent Similar Fixes (Episodic Memory)", ""]
        for i, ep in enumerate(episodes, 1):
            age = self._format_age(ep.age_hours)
            lines.append(f"### Episode {i} ({age}, probe: {ep.probe_id})")
            lines.append(f"**Issue**: {ep.issue_summary[:200]}")
            lines.append(f"**Fix**: {ep.fix_summary[:300]}")
            if ep.files_changed:
                files_str = ", ".join(ep.files_changed[:5])
                lines.append(f"**Files**: {files_str}")
            lines.append("")

        return "\n".join(lines)

    def _calculate_relevance(
        self, episode: Episode, probe_id: str, issue_text: str
    ) -> float:
        """Calculate relevance score between episode and current issue."""
        score = 0.0

        # Same probe = high relevance
        if episode.probe_id == probe_id:
            score += 0.6

        # Keyword overlap
        issue_tokens = set(issue_text.lower().split())
        episode_tokens = set(episode.issue_summary.lower().split())
        if issue_tokens and episode_tokens:
            overlap = len(issue_tokens & episode_tokens)
            score += min(0.4, overlap * 0.05)

        return min(1.0, score)

    def _action_to_episode(self, action: dict) -> Optional[Episode]:
        """Convert an action_log dict to an Episode."""
        try:
            # Parse timestamp
            ts = action.get("timestamp")
            if ts is None:
                ts = action.get("created_at")
            if isinstance(ts, str):
                # ISO format parsing
                s = ts.rstrip("Z")
                for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
                    try:
                        ts = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
                        break
                    except ValueError:
                        continue
                else:
                    ts = datetime.now(timezone.utc)
            elif not isinstance(ts, datetime):
                ts = datetime.now(timezone.utc)

            # Parse files_changed from details
            files_changed = action.get("files_changed", [])
            if not files_changed:
                details = action.get("details", {})
                if isinstance(details, dict):
                    files_changed = details.get("files_changed", [])
                    if not isinstance(files_changed, list):
                        files_changed = []

            return Episode(
                action_id=str(action.get("id", "")),
                probe_id=action.get("source_probe", action.get("probe_id", "unknown")),
                issue_category=action.get("category", "general"),
                issue_summary=action.get("title", action.get("issue_summary", "")),
                fix_summary=action.get("result_summary", ""),
                files_changed=files_changed,
                timestamp=ts,
                success=True,  # We only query successful actions
            )
        except (KeyError, TypeError, ValueError) as e:
            logger.debug("_action_to_episode conversion failed: %s", e)
            return None

    @staticmethod
    def _format_age(hours: float) -> str:
        if hours < 1:
            return "just now"
        elif hours < 24:
            return f"{int(hours)}h ago"
        else:
            return f"{int(hours / 24)}d ago"


__all__ = ["Episode", "EpisodicMemory"]
