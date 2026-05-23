"""Custom command deployer — runs a user-defined deploy command."""
import subprocess
import time
from pathlib import Path

from .base import Deployer, DeployResult


class CommandDeployer(Deployer):
    """执行用户自定义的部署命令

    适用于任何通过 shell 命令可完成的部署场景。
    命令在项目根目录执行。
    """

    def deploy(self) -> DeployResult:
        command = self.config.get("deploy_command", "")
        timeout = self.config.get("deploy_timeout_seconds", 300)

        if not command:
            return DeployResult(
                success=False, method="command",
                error="deploy_command not configured"
            )

        start = time.time()

        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                timeout=timeout,
                env=self._build_env()
            )

            duration = time.time() - start

            if proc.returncode == 0:
                return DeployResult(
                    success=True, method="command",
                    duration_seconds=duration,
                    message=proc.stdout.strip()[-200:] if proc.stdout else "OK"
                )
            else:
                return DeployResult(
                    success=False, method="command",
                    duration_seconds=duration,
                    error=proc.stderr.strip()[-500:] or f"exit code: {proc.returncode}"
                )
        except subprocess.TimeoutExpired:
            return DeployResult(
                success=False, method="command",
                duration_seconds=time.time() - start,
                error=f"Deploy command timed out after {timeout}s"
            )
        except Exception as e:
            return DeployResult(
                success=False, method="command",
                duration_seconds=time.time() - start,
                error=str(e)
            )

    def _build_env(self) -> dict:
        """构建环境变量（继承当前 env + 加载 ~/.vivify/env）"""
        import os
        env = os.environ.copy()
        env_file = Path.home() / ".vivify" / "env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    env[key.strip()] = value.strip()
        return env
