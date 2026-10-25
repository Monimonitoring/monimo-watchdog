"""Watchdog 이 바깥으로 내보내는 신호 둘 — 사람에게 가는 Slack, 기계에게 가는 Healthchecks.io 핑."""

import json
import urllib.request

SLACK_TIMEOUT_SEC = 5
PING_TIMEOUT_SEC = 5


def post_slack(webhook: str, text: str, timeout: float = SLACK_TIMEOUT_SEC) -> None:
    """관제 스택(notifier)을 거치지 않고 직접 쏜다. 스택이 죽었을 때도 알려야 하므로 (FN-56)"""
    request = urllib.request.Request(
        webhook,
        method="POST",
        data=json.dumps({"text": text}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response.read()


def ping(ping_url: str, timeout: float = PING_TIMEOUT_SEC) -> None:
    """Dead Man's Switch. 이 핑이 끊기면 Healthchecks.io 가 대신 알린다 (FN-53)"""
    with urllib.request.urlopen(ping_url, timeout=timeout) as response:
        response.read()
