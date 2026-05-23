"""vivify — self-growing intelligent extension for any GitHub repo.

Top-level package. Sub-packages:
    kernel      — main loop + dispatch + escalation
    interfaces  — ABCs (Probe / Fixer / StorageProvider / CodingAgent / GoalDecomposer)
    models      — dataclasses (Issue / FeatureRequest / Goal / KPI / Snapshot)
    probes      — registry + 12 built-in YAML probes
    fixers      — registry + 7 built-in fixers
    storage     — SQLite default + abstract base
    agents      — Qoder CLI agent + prompt builders
    goals       — GOALS.md parser + decomposer
    pr_mode     — worktree + PR creator + self-growth guard
    verifier    — before/after diff + KPI snapshots
    reporter    — logging + GitHub issue mirror
    cli         — argparse entrypoints
    config      — pydantic schema + loader
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
