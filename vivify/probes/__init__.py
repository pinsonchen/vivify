"""Probes package: detection layer for vivify.

Public surface:

* :class:`Probe` / :class:`ProbeContext` — re-exported from ``interfaces``
* :class:`YamlProbe` — declarative probe driven by a YAML spec
* :class:`ProbeRegistry` + :func:`build_default_registry`
* :func:`run_probes`, :func:`aggregate_issues`, :class:`ProbeRunReport`
"""
from vivify.interfaces.probe import Probe, ProbeContext
from vivify.probes.base import YamlProbe
from vivify.probes.registry import ProbeRegistry, build_default_registry
from vivify.probes.runner import ProbeRunReport, aggregate_issues, run_probes

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
