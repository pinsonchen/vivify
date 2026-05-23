"""Webhook deployer — notifies external service to trigger deployment."""
import json
import time
import urllib.request
import urllib.error

from .base import Deployer, DeployResult


class WebhookDeployer(Deployer):
    """通过 Webhook 通知外部服务触发部署

    适用于已有 CI/CD pipeline 的场景，
    vivify 只需通知触发即可。
    """

    def deploy(self) -> DeployResult:
        webhook_url = self.config.get("webhook_url", "")
        webhook_secret = self.config.get("webhook_secret", "")
        timeout = self.config.get("webhook_timeout_seconds", 30)

        if not webhook_url:
            return DeployResult(
                success=False, method="webhook",
                error="webhook_url not configured"
            )

        start = time.time()

        try:
            payload = json.dumps({
                "event": "deploy",
                "repo": str(self.repo_root),
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }).encode()

            headers = {
                "Content-Type": "application/json",
            }
            if webhook_secret:
                headers["X-Vivify-Secret"] = webhook_secret

            req = urllib.request.Request(
                webhook_url, data=payload, headers=headers, method="POST"
            )
            resp = urllib.request.urlopen(req, timeout=timeout)

            duration = time.time() - start

            if 200 <= resp.status < 300:
                return DeployResult(
                    success=True, method="webhook",
                    duration_seconds=duration,
                    message=f"Webhook triggered: {resp.status}"
                )
            else:
                return DeployResult(
                    success=False, method="webhook",
                    duration_seconds=duration,
                    error=f"Webhook returned status {resp.status}"
                )
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            return DeployResult(
                success=False, method="webhook",
                duration_seconds=time.time() - start,
                error=str(e)
            )
