"""自动 Revert 机制 — 验证失败时撤回已合并的代码"""

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class RevertResult:
    success: bool
    revert_pr_url: Optional[str] = None
    error: Optional[str] = None


class AutoReverter:
    """自动创建 revert PR"""

    def __init__(self, repo_path: str, base_branch: str = "main", env: dict = None):
        self.repo_path = Path(repo_path)
        self.base_branch = base_branch
        self.env = env  # 包含 GH_TOKEN 等

    def revert_commit(self, commit_hash: str, feature_title: str, feature_id: int) -> RevertResult:
        """为指定 commit 创建 revert PR

        流程：
        1. 创建 revert 分支
        2. git revert <commit_hash>
        3. push 分支
        4. 创建 PR
        5. 自动合并
        """
        branch_name = f"vivify/revert-{feature_id}-{commit_hash[:7]}"

        try:
            # 1. 确保在最新的 base branch
            self._run(["git", "fetch", "origin", self.base_branch])
            self._run(["git", "checkout", self.base_branch])
            self._run(["git", "pull", "origin", self.base_branch])

            # 2. 创建 revert 分支
            self._run(["git", "checkout", "-b", branch_name])

            # 3. git revert（--no-edit 自动生成 commit message）
            self._run(["git", "revert", "--no-edit", commit_hash])

            # 4. push
            self._run(["git", "push", "origin", branch_name])

            # 5. 创建 PR
            pr_title = f"revert: rollback feature #{feature_id} - {feature_title}"
            pr_body = (
                f"Auto-revert by vivify: Feature #{feature_id} verification "
                f"failed after max retries.\n\nReverts commit {commit_hash}"
            )

            result = self._run([
                "gh", "pr", "create",
                "--base", self.base_branch,
                "--head", branch_name,
                "--title", pr_title,
                "--body", pr_body,
            ])
            pr_url = result.stdout.strip()

            # 6. 自动合并
            if pr_url:
                self._run(
                    ["gh", "pr", "merge", pr_url, "--merge", "--auto", "--delete-branch"],
                    check=False,
                )

            return RevertResult(success=True, revert_pr_url=pr_url)

        except subprocess.CalledProcessError as e:
            logger.error(f"Revert failed for feature #{feature_id}: {e.stderr}")
            return RevertResult(success=False, error=str(e.stderr))
        except Exception as e:
            logger.error(f"Revert unexpected error: {e}")
            return RevertResult(success=False, error=str(e))
        finally:
            # 清理：回到 base branch
            try:
                self._run(["git", "checkout", self.base_branch], check=False)
                self._run(["git", "branch", "-D", branch_name], check=False)
            except Exception:
                pass

    def _run(self, cmd: list, check: bool = True) -> subprocess.CompletedProcess:
        """执行命令"""
        env = os.environ.copy()
        if self.env:
            env.update(self.env)
        return subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=str(self.repo_path), env=env,
            check=check, timeout=60,
        )
