"""Probes package: detection layer for auto-heal.

Public surface:

* :class:`Probe` / :class:`ProbeContext` — re-exported from ``interfaces``
* :class:`YamlProbe` — declarative probe driven by a YAML spec
* :class:`ProbeRegistry` + :func:`build_default_registry`
* :func:`run_probes`, :func:`aggregate_issues`, :class:`ProbeRunReport`
"""
from auto_heal.interfaces.probe import Probe, ProbeContext
from auto_heal.probes.base import YamlProbe
from auto_heal.probes.registry import ProbeRegistry, build_default_registry
from auto_heal.probes.runner import ProbeRunReport, aggregate_issues, run_probes

__all__ = [
    "Probe",
    "ProbeContext",
    "YamlProbe",
    "ProbeRegistry",
    "build_default_registry",
    "ProbeRunReport",
    "aggregate_issues",
    "run_probes",
]
