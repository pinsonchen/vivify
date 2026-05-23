"""SSH/rsync deployer — syncs repo to remote server."""
import subprocess
import time

from .base import Deployer, DeployResult


class SSHDeployer(Deployer):
    """通过 SSH 将代码部署到远程服务器

    支持两种模式：
    1. rsync 模式：将本地文件同步到远程目录
    2. git pull 模式：SSH 到远程执行 git pull
    """

    def deploy(self) -> DeployResult:
        host = self.config.get("ssh_host", "")
        user = self.config.get("ssh_user", "")
        remote_path = self.config.get("ssh_path", "")
        ssh_key = self.config.get("ssh_key", "")  # 可选
        mode = self.config.get("ssh_mode", "rsync")  # rsync | git_pull

        if not host:
            return DeployResult(
                success=False, method="ssh",
                error="ssh_host not configured"
            )

        start = time.time()

        try:
            if mode == "git_pull":
                result = self._deploy_git_pull(host, user, remote_path, ssh_key)
            else:
                result = self._deploy_rsync(host, user, remote_path, ssh_key)

            result.duration_seconds = time.time() - start
            return result
        except Exception as e:
            return DeployResult(
                success=False, method="ssh",
                duration_seconds=time.time() - start,
                error=str(e)
            )

    def _deploy_rsync(self, host: str, user: str, remote_path: str, ssh_key: str) -> DeployResult:
        """使用 rsync 同步文件"""
        source_dir = self.config.get("source_dir", "")
        if source_dir:
            source = str(self.repo_root / source_dir) + "/"
        else:
            source = str(self.repo_root) + "/"
        dest = f"{user}@{host}:{remote_path}" if user else f"{host}:{remote_path}"

        cmd = ["rsync", "-avz", "--delete"]
        if ssh_key:
            cmd.extend(["-e", f"ssh -i {ssh_key} -o StrictHostKeyChecking=no"])

        # 排除 .vivify 和 .git 目录
        cmd.extend(["--exclude", ".vivify/", "--exclude", ".git/"])
        cmd.extend([source, dest])

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, errors='replace')

        if proc.returncode == 0:
            return DeployResult(
                success=True, method="ssh/rsync",
                message=f"Synced to {host}:{remote_path}"
            )
        else:
            return DeployResult(
                success=False, method="ssh/rsync",
                error=proc.stderr.strip() or f"rsync exit code: {proc.returncode}"
            )

    def _deploy_git_pull(self, host: str, user: str, remote_path: str, ssh_key: str) -> DeployResult:
        """SSH 到远程服务器执行 git pull"""
        target = f"{user}@{host}" if user else host

        ssh_cmd = ["ssh"]
        if ssh_key:
            ssh_cmd.extend(["-i", ssh_key, "-o", "StrictHostKeyChecking=no"])
        ssh_cmd.append(target)
        ssh_cmd.append(f"cd {remote_path} && git pull origin main")

        proc = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=120, errors='replace')

        if proc.returncode == 0:
            return DeployResult(
                success=True, method="ssh/git_pull",
                message=f"git pull on {host}:{remote_path}"
            )
        else:
            return DeployResult(
                success=False, method="ssh/git_pull",
                error=proc.stderr.strip() or f"ssh exit code: {proc.returncode}"
            )
