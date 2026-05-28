"""Tests for vivify.probes.rule_engine — composite signal rule engine."""
import pytest

from vivify.probes.rule_engine import Rule, RuleCondition, RuleEngine, RuleEvaluation


# ────────────────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────────────────

SAMPLE_PROBE_RESULTS = {
    "site_health": {
        "response_time_ms": 2500,
        "status_code": 200,
        "uptime_percent": 99.5,
    },
    "error_log_patterns": {
        "error_count": 15,
        "warning_count": 42,
        "last_error": "TimeoutError in handler",
    },
    "test_coverage": {
        "coverage_percent": 78.3,
        "uncovered_files": 5,
    },
}


def _make_rule_config(
    name="test_rule",
    match_mode="all",
    conditions=None,
    action="create_issue",
    severity="medium",
    message="",
):
    """Helper: build a single rule config dict."""
    return {
        "name": name,
        "when": {match_mode: conditions or []},
        "action": action,
        "severity": severity,
        "message": message,
    }


# ────────────────────────────────────────────────────────────────────────────────
# Condition parsing tests
# ────────────────────────────────────────────────────────────────────────────────


class TestConditionParsing:
    def test_greater_than(self):
        engine = RuleEngine([_make_rule_config(conditions=[
            {"probe": "site_health", "field": "response_time_ms", "condition": "> 2000"},
        ])])
        evs = engine.evaluate(SAMPLE_PROBE_RESULTS)
        assert evs[0].triggered is True

    def test_greater_than_not_met(self):
        engine = RuleEngine([_make_rule_config(conditions=[
            {"probe": "site_health", "field": "response_time_ms", "condition": "> 5000"},
        ])])
        evs = engine.evaluate(SAMPLE_PROBE_RESULTS)
        assert evs[0].triggered is False

    def test_less_than(self):
        engine = RuleEngine([_make_rule_config(conditions=[
            {"probe": "test_coverage", "field": "coverage_percent", "condition": "< 80"},
        ])])
        evs = engine.evaluate(SAMPLE_PROBE_RESULTS)
        assert evs[0].triggered is True

    def test_equal(self):
        engine = RuleEngine([_make_rule_config(conditions=[
            {"probe": "site_health", "field": "status_code", "condition": "== 200"},
        ])])
        evs = engine.evaluate(SAMPLE_PROBE_RESULTS)
        assert evs[0].triggered is True

    def test_not_equal(self):
        engine = RuleEngine([_make_rule_config(conditions=[
            {"probe": "site_health", "field": "status_code", "condition": "!= 500"},
        ])])
        evs = engine.evaluate(SAMPLE_PROBE_RESULTS)
        assert evs[0].triggered is True

    def test_greater_equal(self):
        engine = RuleEngine([_make_rule_config(conditions=[
            {"probe": "site_health", "field": "response_time_ms", "condition": ">= 2500"},
        ])])
        evs = engine.evaluate(SAMPLE_PROBE_RESULTS)
        assert evs[0].triggered is True

    def test_less_equal(self):
        engine = RuleEngine([_make_rule_config(conditions=[
            {"probe": "site_health", "field": "uptime_percent", "condition": "<= 99.5"},
        ])])
        evs = engine.evaluate(SAMPLE_PROBE_RESULTS)
        assert evs[0].triggered is True

    def test_contains(self):
        engine = RuleEngine([_make_rule_config(conditions=[
            {"probe": "error_log_patterns", "field": "last_error", "condition": "contains Timeout"},
        ])])
        evs = engine.evaluate(SAMPLE_PROBE_RESULTS)
        assert evs[0].triggered is True

    def test_contains_not_found(self):
        engine = RuleEngine([_make_rule_config(conditions=[
            {"probe": "error_log_patterns", "field": "last_error", "condition": "contains SegFault"},
        ])])
        evs = engine.evaluate(SAMPLE_PROBE_RESULTS)
        assert evs[0].triggered is False

    def test_matches_regex(self):
        engine = RuleEngine([_make_rule_config(conditions=[
            {"probe": "error_log_patterns", "field": "last_error", "condition": "matches Timeout.*handler"},
        ])])
        evs = engine.evaluate(SAMPLE_PROBE_RESULTS)
        assert evs[0].triggered is True

    def test_matches_regex_no_match(self):
        engine = RuleEngine([_make_rule_config(conditions=[
            {"probe": "error_log_patterns", "field": "last_error", "condition": "matches ^Fatal"},
        ])])
        evs = engine.evaluate(SAMPLE_PROBE_RESULTS)
        assert evs[0].triggered is False

    def test_matches_invalid_regex(self):
        engine = RuleEngine([_make_rule_config(conditions=[
            {"probe": "error_log_patterns", "field": "last_error", "condition": "matches [invalid"},
        ])])
        evs = engine.evaluate(SAMPLE_PROBE_RESULTS)
        assert evs[0].triggered is False


# ────────────────────────────────────────────────────────────────────────────────
# Match mode tests (all / any)
# ────────────────────────────────────────────────────────────────────────────────


class TestMatchMode:
    def test_all_mode_all_match(self):
        engine = RuleEngine([_make_rule_config(
            match_mode="all",
            conditions=[
                {"probe": "site_health", "field": "response_time_ms", "condition": "> 2000"},
                {"probe": "error_log_patterns", "field": "error_count", "condition": "> 10"},
            ],
        )])
        evs = engine.evaluate(SAMPLE_PROBE_RESULTS)
        assert evs[0].triggered is True
        assert len(evs[0].matched_conditions) == 2

    def test_all_mode_partial_match(self):
        engine = RuleEngine([_make_rule_config(
            match_mode="all",
            conditions=[
                {"probe": "site_health", "field": "response_time_ms", "condition": "> 2000"},
                {"probe": "error_log_patterns", "field": "error_count", "condition": "> 100"},
            ],
        )])
        evs = engine.evaluate(SAMPLE_PROBE_RESULTS)
        assert evs[0].triggered is False
        assert len(evs[0].matched_conditions) == 1

    def test_any_mode_one_match(self):
        engine = RuleEngine([_make_rule_config(
            match_mode="any",
            conditions=[
                {"probe": "site_health", "field": "response_time_ms", "condition": "> 9000"},
                {"probe": "error_log_patterns", "field": "error_count", "condition": "> 10"},
            ],
        )])
        evs = engine.evaluate(SAMPLE_PROBE_RESULTS)
        assert evs[0].triggered is True

    def test_any_mode_none_match(self):
        engine = RuleEngine([_make_rule_config(
            match_mode="any",
            conditions=[
                {"probe": "site_health", "field": "response_time_ms", "condition": "> 9000"},
                {"probe": "error_log_patterns", "field": "error_count", "condition": "> 100"},
            ],
        )])
        evs = engine.evaluate(SAMPLE_PROBE_RESULTS)
        assert evs[0].triggered is False


# ────────────────────────────────────────────────────────────────────────────────
# Edge cases / tolerance
# ────────────────────────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_rules(self):
        engine = RuleEngine([])
        evs = engine.evaluate(SAMPLE_PROBE_RESULTS)
        assert evs == []

    def test_missing_probe_in_results(self):
        engine = RuleEngine([_make_rule_config(conditions=[
            {"probe": "nonexistent_probe", "field": "x", "condition": "> 0"},
        ])])
        evs = engine.evaluate(SAMPLE_PROBE_RESULTS)
        assert evs[0].triggered is False

    def test_missing_field_in_probe_data(self):
        engine = RuleEngine([_make_rule_config(conditions=[
            {"probe": "site_health", "field": "nonexistent_field", "condition": "> 0"},
        ])])
        evs = engine.evaluate(SAMPLE_PROBE_RESULTS)
        assert evs[0].triggered is False

    def test_empty_probe_results(self):
        engine = RuleEngine([_make_rule_config(conditions=[
            {"probe": "site_health", "field": "response_time_ms", "condition": "> 0"},
        ])])
        evs = engine.evaluate({})
        assert evs[0].triggered is False

    def test_type_mismatch_graceful(self):
        """Condition target can't be cast to value type — should not crash."""
        engine = RuleEngine([_make_rule_config(conditions=[
            {"probe": "site_health", "field": "response_time_ms", "condition": "> not_a_number"},
        ])])
        evs = engine.evaluate(SAMPLE_PROBE_RESULTS)
        assert evs[0].triggered is False

    def test_invalid_expression_returns_false(self):
        """Unrecognized expression format should return False, not crash."""
        engine = RuleEngine([_make_rule_config(conditions=[
            {"probe": "site_health", "field": "response_time_ms", "condition": "UNKNOWN 42"},
        ])])
        evs = engine.evaluate(SAMPLE_PROBE_RESULTS)
        assert evs[0].triggered is False

    def test_multiple_rules_mixed(self):
        """Multiple rules: one triggered, one not."""
        configs = [
            _make_rule_config(name="rule_a", conditions=[
                {"probe": "site_health", "field": "response_time_ms", "condition": "> 2000"},
            ]),
            _make_rule_config(name="rule_b", conditions=[
                {"probe": "site_health", "field": "response_time_ms", "condition": "> 9000"},
            ]),
        ]
        engine = RuleEngine(configs)
        evs = engine.evaluate(SAMPLE_PROBE_RESULTS)
        assert evs[0].triggered is True
        assert evs[1].triggered is False

    def test_rule_metadata(self):
        """Verify parsed rule retains action/severity/message."""
        cfg = _make_rule_config(
            name="perf_degrade",
            action="create_feature",
            severity="high",
            message="Performance issue detected",
            conditions=[
                {"probe": "site_health", "field": "response_time_ms", "condition": "> 2000"},
            ],
        )
        engine = RuleEngine([cfg])
        assert engine.rules[0].name == "perf_degrade"
        assert engine.rules[0].action == "create_feature"
        assert engine.rules[0].severity == "high"
        assert engine.rules[0].message == "Performance issue detected"

    def test_boolean_value_handling(self):
        """Boolean field compared with == true."""
        probe_results = {"ci_status": {"passing": True, "has_failures": False}}
        engine = RuleEngine([_make_rule_config(conditions=[
            {"probe": "ci_status", "field": "passing", "condition": "== true"},
        ])])
        evs = engine.evaluate(probe_results)
        assert evs[0].triggered is True

    def test_boolean_false_check(self):
        probe_results = {"ci_status": {"passing": True, "has_failures": False}}
        engine = RuleEngine([_make_rule_config(conditions=[
            {"probe": "ci_status", "field": "has_failures", "condition": "== true"},
        ])])
        evs = engine.evaluate(probe_results)
        assert evs[0].triggered is False

    def test_string_equality(self):
        probe_results = {"deploy": {"status": "failed"}}
        engine = RuleEngine([_make_rule_config(conditions=[
            {"probe": "deploy", "field": "status", "condition": "== failed"},
        ])])
        evs = engine.evaluate(probe_results)
        assert evs[0].triggered is True
