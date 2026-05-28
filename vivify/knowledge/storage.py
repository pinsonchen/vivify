"""File system storage for knowledge graph data."""

import json
from pathlib import Path
from typing import List, Optional

from vivify.knowledge.models import (
    CodeConvention,
    GraphMetadata,
    KnowledgeGraph,
)


class KnowledgeStorage:
    """Manages reading/writing knowledge graph files.

    Storage layout:
        .vivify/knowledge/
        ├── graph.json            # 完整图
        ├── modules/              # 按模块分片存储的详细信息
        │   ├── kernel.json
        │   └── ...
        ├── conventions.json
        └── meta.json
    """

    def __init__(self, project_root: Path):
        self.root = project_root
        self.knowledge_dir = project_root / ".vivify" / "knowledge"

    def ensure_dir(self) -> None:
        """Create the knowledge directory structure if it doesn't exist."""
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)
        (self.knowledge_dir / "modules").mkdir(exist_ok=True)

    def save_graph(self, graph: KnowledgeGraph) -> None:
        """Save the complete knowledge graph to graph.json."""
        self.ensure_dir()
        graph_path = self.knowledge_dir / "graph.json"
        graph_path.write_text(
            json.dumps(graph.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def load_graph(self) -> Optional[KnowledgeGraph]:
        """Load the complete knowledge graph from graph.json."""
        graph_path = self.knowledge_dir / "graph.json"
        if not graph_path.exists():
            return None
        try:
            data = json.loads(graph_path.read_text(encoding="utf-8"))
            return KnowledgeGraph.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def save_module_detail(self, module_name: str, detail: dict) -> None:
        """Save detailed information for a specific module."""
        self.ensure_dir()
        module_path = self.knowledge_dir / "modules" / f"{module_name}.json"
        module_path.write_text(
            json.dumps(detail, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def load_module_detail(self, module_name: str) -> Optional[dict]:
        """Load detailed information for a specific module."""
        module_path = self.knowledge_dir / "modules" / f"{module_name}.json"
        if not module_path.exists():
            return None
        try:
            return json.loads(module_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def save_meta(self, meta: GraphMetadata) -> None:
        """Save graph metadata to meta.json."""
        self.ensure_dir()
        meta_path = self.knowledge_dir / "meta.json"
        meta_path.write_text(
            json.dumps(meta.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def load_meta(self) -> Optional[GraphMetadata]:
        """Load graph metadata from meta.json."""
        meta_path = self.knowledge_dir / "meta.json"
        if not meta_path.exists():
            return None
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            return GraphMetadata.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def save_conventions(self, conventions: List[CodeConvention]) -> None:
        """Save coding conventions to conventions.json."""
        self.ensure_dir()
        conv_path = self.knowledge_dir / "conventions.json"
        conv_path.write_text(
            json.dumps(
                [c.to_dict() for c in conventions],
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def load_conventions(self) -> List[CodeConvention]:
        """Load coding conventions from conventions.json."""
        conv_path = self.knowledge_dir / "conventions.json"
        if not conv_path.exists():
            return []
        try:
            data = json.loads(conv_path.read_text(encoding="utf-8"))
            return [CodeConvention.from_dict(c) for c in data]
        except (json.JSONDecodeError, KeyError):
            return []

    def get_last_commit_hash(self) -> str:
        """Get the git commit hash from the last graph generation."""
        meta = self.load_meta()
        if meta is None:
            return ""
        return meta.git_commit_hash

    def needs_update(self, current_hash: str) -> bool:
        """Check if the knowledge graph needs updating based on commit hash."""
        if not current_hash:
            return True
        last_hash = self.get_last_commit_hash()
        if not last_hash:
            return True
        return last_hash != current_hash
