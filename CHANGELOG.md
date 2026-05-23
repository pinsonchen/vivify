# Changelog

## 0.1.0 (alpha)

Initial extraction from `pinsonchen/channels-monitor::auto_heal` (private).

- Project-agnostic kernel: detect → direct-fix → agent-fix → verify → escalate
- SQLite `StorageProvider` (default) + abstract interface for remote storage
- Qoder CLI coding agent with global slot manager (env-tagged subprocesses)
- PR mode: worktree → push branch → `gh pr create` → optional auto-merge
- Self-growth path whitelist (kernel changes require human review)
- 12 built-in YAML probes + 7 built-in fixers
- `GOALS.md` parser + Goal-to-Feature decomposer
- KPI-degradation auto-feature creator
- `auto-heal init` interactive scaffolder
