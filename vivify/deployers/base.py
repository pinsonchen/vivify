"""Base deployer interface."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import time


@dataclass
class DeployResult:
    """部署执行结果"""
    success: bool
    method: str
    duration_seconds: float = 0.0
    message: str = ""
    error: str = ""
    deploy_url: str = ""
    verified: Optional[bool] = None  # None=未验证, True=验证通过, False=验证失败


class Deployer(ABC):
    """部署器抽象基类"""

    def __init__(self, repo_root: Path, config: dict):
        self.repo_root = repo_root
        self.config = config

    @abstractmethod
    def deploy(self) -> DeployResult:
        """执行部署操作，返回部署结果"""
        ...

    def verify(self, deploy_url: str, timeout: int = 30) -> bool:
        """部署后健康检查：检测 URL 是否可达且返回 200"""
        import urllib.request
        import urllib.error

        # 等待部署生效
        wait_seconds = self.config.get("post_deploy_wait_seconds", 30)
        time.sleep(wait_seconds)

        try:
            req = urllib.request.Request(deploy_url, method="HEAD")
            resp = urllib.request.urlopen(req, timeout=timeout)
            return resp.status == 200
        except (urllib.error.URLError, urllib.error.HTTPError, OSError):
            return False

    @property
    def method_name(self) -> str:
        return self.__class__.__name__.replace("Deployer", "").lower()
