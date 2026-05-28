"""Knowledge graph data models for vivify."""

from dataclasses import dataclass, field
from typing import List, Dict, Optional
from enum import Enum


class NodeType(str, Enum):
    MODULE = "module"
    FILE = "file"
    CLASS = "class"
    FUNCTION = "function"
    ENDPOINT = "endpoint"
    CONFIG = "config"


class EdgeType(str, Enum):
    IMPORTS = "imports"
    CONTAINS = "contains"
    CALLS = "calls"
    INHERITS = "inherits"
    IMPLEMENTS = "implements"
    CONFIGURES = "configures"
    DEPENDS_ON = "depends_on"
    TESTS = "tests"


class Complexity(str, Enum):
    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"


@dataclass
class GraphNode:
    """A node in the knowledge graph."""

    id: str  # 格式: "type:path" 如 "module:vivify/kernel"
    type: NodeType
    name: str
    path: str  # 相对于项目根的路径
    summary: str = ""  # 一句话摘要
    tags: List[str] = field(default_factory=list)
    complexity: Complexity = Complexity.SIMPLE
    # 模块特有字段
    responsibility: str = ""  # 模块职责
    exports: List[str] = field(default_factory=list)  # 导出的公共 API
    dependencies: List[str] = field(default_factory=list)  # 依赖的其他模块
    layer: str = ""  # 架构层级 (api/core/data/util/config)
    # 文件/函数特有字段
    line_count: int = 0
    functions: List[str] = field(default_factory=list)  # 函数名列表
    classes: List[str] = field(default_factory=list)  # 类名列表

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type.value,
            "name": self.name,
            "path": self.path,
            "summary": self.summary,
            "tags": self.tags,
            "complexity": self.complexity.value,
            "responsibility": self.responsibility,
            "exports": self.exports,
            "dependencies": self.dependencies,
            "layer": self.layer,
            "line_count": self.line_count,
            "functions": self.functions,
            "classes": self.classes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GraphNode":
        return cls(
            id=data["id"],
            type=NodeType(data["type"]),
            name=data["name"],
            path=data["path"],
            summary=data.get("summary", ""),
            tags=data.get("tags", []),
            complexity=Complexity(data.get("complexity", "simple")),
            responsibility=data.get("responsibility", ""),
            exports=data.get("exports", []),
            dependencies=data.get("dependencies", []),
            layer=data.get("layer", ""),
            line_count=data.get("line_count", 0),
            functions=data.get("functions", []),
            classes=data.get("classes", []),
        )


@dataclass
class GraphEdge:
    """An edge connecting two nodes in the knowledge graph."""

    source: str  # 源节点 id
    target: str  # 目标节点 id
    type: EdgeType
    weight: float = 1.0  # 0.0-1.0 关系强度

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "target": self.target,
            "type": self.type.value,
            "weight": self.weight,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GraphEdge":
        return cls(
            source=data["source"],
            target=data["target"],
            type=EdgeType(data["type"]),
            weight=data.get("weight", 1.0),
        )


@dataclass
class ArchitectureLayer:
    """Represents an architecture layer grouping."""

    name: str  # 如 "API Layer", "Core Layer"
    description: str
    node_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "node_ids": self.node_ids,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ArchitectureLayer":
        return cls(
            name=data["name"],
            description=data["description"],
            node_ids=data.get("node_ids", []),
        )


@dataclass
class CodeConvention:
    """A coding convention or standard."""

    category: str  # naming, formatting, patterns, imports
    rule: str  # 规则描述
    example: str = ""  # 代码示例
    language: str = ""  # 适用语言

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "rule": self.rule,
            "example": self.example,
            "language": self.language,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CodeConvention":
        return cls(
            category=data["category"],
            rule=data["rule"],
            example=data.get("example", ""),
            language=data.get("language", ""),
        )


@dataclass
class GraphMetadata:
    """Metadata about the knowledge graph."""

    project_name: str = ""
    description: str = ""
    languages: List[str] = field(default_factory=list)
    frameworks: List[str] = field(default_factory=list)
    git_commit_hash: str = ""
    generated_at: str = ""
    version: str = "1.0.0"
    file_fingerprints: Dict[str, str] = field(default_factory=dict)  # path -> hash

    def to_dict(self) -> dict:
        return {
            "project_name": self.project_name,
            "description": self.description,
            "languages": self.languages,
            "frameworks": self.frameworks,
            "git_commit_hash": self.git_commit_hash,
            "generated_at": self.generated_at,
            "version": self.version,
            "file_fingerprints": self.file_fingerprints,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GraphMetadata":
        return cls(
            project_name=data.get("project_name", ""),
            description=data.get("description", ""),
            languages=data.get("languages", []),
            frameworks=data.get("frameworks", []),
            git_commit_hash=data.get("git_commit_hash", ""),
            generated_at=data.get("generated_at", ""),
            version=data.get("version", "1.0.0"),
            file_fingerprints=data.get("file_fingerprints", {}),
        )


@dataclass
class KnowledgeGraph:
    """The complete knowledge graph for a project."""

    metadata: GraphMetadata = field(default_factory=GraphMetadata)
    nodes: List[GraphNode] = field(default_factory=list)
    edges: List[GraphEdge] = field(default_factory=list)
    layers: List[ArchitectureLayer] = field(default_factory=list)
    conventions: List[CodeConvention] = field(default_factory=list)

    def get_node(self, node_id: str) -> Optional[GraphNode]:
        """Get a node by its ID."""
        for node in self.nodes:
            if node.id == node_id:
                return node
        return None

    def get_module_nodes(self) -> List[GraphNode]:
        """Get all module-type nodes."""
        return [n for n in self.nodes if n.type == NodeType.MODULE]

    def get_edges_for(self, node_id: str) -> List[GraphEdge]:
        """Get all edges connected to a node (as source or target)."""
        return [e for e in self.edges if e.source == node_id or e.target == node_id]

    def get_dependencies(self, module_id: str) -> List[str]:
        """Get all module IDs that the given module depends on."""
        return [
            e.target
            for e in self.edges
            if e.source == module_id and e.type == EdgeType.DEPENDS_ON
        ]

    def to_dict(self) -> dict:
        """Serialize the entire graph to a dictionary."""
        return {
            "metadata": self.metadata.to_dict(),
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "layers": [layer.to_dict() for layer in self.layers],
            "conventions": [c.to_dict() for c in self.conventions],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "KnowledgeGraph":
        """Deserialize a knowledge graph from a dictionary."""
        metadata = GraphMetadata.from_dict(data.get("metadata", {}))
        nodes = [GraphNode.from_dict(n) for n in data.get("nodes", [])]
        edges = [GraphEdge.from_dict(e) for e in data.get("edges", [])]
        layers = [ArchitectureLayer.from_dict(l) for l in data.get("layers", [])]
        conventions = [CodeConvention.from_dict(c) for c in data.get("conventions", [])]
        return cls(
            metadata=metadata,
            nodes=nodes,
            edges=edges,
            layers=layers,
            conventions=conventions,
        )
