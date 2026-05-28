"""Probe 复合信号规则引擎

在 .vivify.yml 中声明规则：
rules:
  - name: "performance_degradation"
    when:
      all:
        - probe: site_health
          field: response_time_ms
          condition: "> 2000"
        - probe: error_log_patterns
          field: error_count
          condition: "> 10"
    action: create_issue
    severity: high
    message: "性能退化：响应时间超过 2s 且错误数超过 10"
"""
from __future__ import annotations

import logging
import operator
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RuleCondition:
    """单个条件"""

    probe: str  # probe 名称
    field: str  # 输出字段名
    condition: str  # 条件表达式，如 "> 2000", "== true", "contains error"


@dataclass
class Rule:
    """复合规则"""

    name: str
    conditions: list[RuleCondition]
    match_mode: str = "all"  # "all" (AND) 或 "any" (OR)
    action: str = "create_issue"  # create_issue / create_feature / escalate
    severity: str = "medium"  # low / medium / high / critical
    message: str = ""


@dataclass
class RuleEvaluation:
    """规则评估结果"""

    rule: Rule
    triggered: bool
    matched_conditions: list[str] = field(default_factory=list)


class RuleEngine:
    """规则引擎 — 评估 Probe 输出的复合条件"""

    def __init__(self, rules_config: list[dict]):
        self.rules = self._parse_rules(rules_config)

    def _parse_rules(self, configs: list[dict]) -> list[Rule]:
        """解析 YAML 规则配置"""
        rules = []
        for cfg in configs:
            when = cfg.get("when", {})
            match_mode = "all" if "all" in when else "any"
            conditions_raw = when.get(match_mode, [])

            conditions = []
            for c in conditions_raw:
                conditions.append(
                    RuleCondition(
                        probe=c.get("probe", ""),
                        field=c.get("field", ""),
                        condition=c.get("condition", ""),
                    )
                )

            rules.append(
                Rule(
                    name=cfg.get("name", "unnamed"),
                    conditions=conditions,
                    match_mode=match_mode,
                    action=cfg.get("action", "create_issue"),
                    severity=cfg.get("severity", "medium"),
                    message=cfg.get("message", ""),
                )
            )
        return rules

    def evaluate(self, probe_results: dict[str, dict]) -> list[RuleEvaluation]:
        """评估所有规则，返回触发的规则列表

        Args:
            probe_results: {probe_name: {field: value, ...}, ...}
        """
        evaluations = []
        for rule in self.rules:
            eval_result = self._evaluate_rule(rule, probe_results)
            evaluations.append(eval_result)
        return evaluations

    def _evaluate_rule(self, rule: Rule, probe_results: dict) -> RuleEvaluation:
        """评估单个规则"""
        matched = []
        for cond in rule.conditions:
            if self._check_condition(cond, probe_results):
                matched.append(f"{cond.probe}.{cond.field} {cond.condition}")

        if rule.match_mode == "all":
            triggered = len(matched) == len(rule.conditions)
        else:  # any
            triggered = len(matched) > 0

        return RuleEvaluation(rule=rule, triggered=triggered, matched_conditions=matched)

    def _check_condition(self, cond: RuleCondition, probe_results: dict) -> bool:
        """检查单个条件"""
        probe_data = probe_results.get(cond.probe)
        if not probe_data:
            return False

        value = probe_data.get(cond.field)
        if value is None:
            return False

        return self._eval_expression(value, cond.condition)

    def _eval_expression(self, value: Any, expr: str) -> bool:
        """安全表达式求值（不用 eval）"""
        expr = expr.strip()

        # 比较运算符
        ops = {
            ">=": operator.ge,
            "<=": operator.le,
            "!=": operator.ne,
            ">": operator.gt,
            "<": operator.lt,
            "==": operator.eq,
        }

        for op_str, op_func in ops.items():
            if expr.startswith(op_str):
                target = expr[len(op_str) :].strip()
                try:
                    if isinstance(value, bool):
                        target_val = target.lower() in ("true", "1", "yes")
                    else:
                        target_val = type(value)(target)
                    return op_func(value, target_val)
                except (ValueError, TypeError):
                    return False

        # contains 操作
        if expr.startswith("contains "):
            substring = expr[9:].strip().strip('"').strip("'")
            return substring in str(value)

        # matches 正则
        if expr.startswith("matches "):
            pattern = expr[8:].strip().strip('"').strip("'")
            try:
                return bool(re.search(pattern, str(value)))
            except re.error:
                return False

        return False


__all__ = ["RuleEngine", "RuleEvaluation", "Rule", "RuleCondition"]
