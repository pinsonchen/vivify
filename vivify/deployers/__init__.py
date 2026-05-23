"""Deployer module — automated deployment after PR merge."""
from pathlib import Path
from typing import Optional

from .base import Deployer, DeployResult
from .ssh import SSHDeployer
from .command import CommandDeployer
from .webhook import WebhookDeployer

__all__ = [
    "Deployer",
    "DeployResult",
    "SSHDeployer",
    "CommandDeployer",
    "WebhookDeployer",
    "get_deployer",
]

_DEPLOYER_MAP = {
    "ssh": SSHDeployer,
    "rsync": SSHDeployer,
    "command": CommandDeployer,
    "webhook": WebhookDeployer,
}


def get_deployer(
    repo_root: Path, deploy_method: str, deploy_config: dict
) -> Optional[Deployer]:
    """根据配置获取对应的部署器实例

    Args:
        repo_root: 项目根目录
        deploy_method: 部署方式 (ssh, rsync, command, webhook, manual)
        deploy_config: 部署配置字典

    Returns:
        Deployer 实例，如果 method 为 manual 或未知则返回 None
    """
    if deploy_method in ("manual", "github-pages", "vercel", "netlify"):
        # manual: 不执行自动部署
        # github-pages/vercel/netlify: 由平台 CI 自动处理，无需 vivify 执行
        return None

    deployer_cls = _DEPLOYER_MAP.get(deploy_method)
    if deployer_cls is None:
        return None

    return deployer_cls(repo_root, deploy_config)
