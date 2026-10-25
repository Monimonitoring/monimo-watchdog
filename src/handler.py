"""한 주기를 조립한다. 바깥과는 canary · notify 를 통해서만 말한다."""

import logging
import os
from typing import NamedTuple

import canary
import notify

log = logging.getLogger(__name__)

logging.basicConfig(level=logging.INFO)
# Lambda 런타임이 루트 핸들러를 미리 달아 두어 위 줄이 아무 일도 하지 않는다. 평문 로그의
# 파이썬 런타임 기본 레벨은 WARN 이라, 직접 올리지 않으면 age_sec 이 CloudWatch 에 남지 않는다
logging.getLogger().setLevel(logging.INFO)


class Config(NamedTuple):
    shop_order_url: str
    api_server_url: str
    internal_token: str
    slack_webhook: str
    ping_url: str

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            shop_order_url=os.environ["SHOP_ORDER_URL"],
            api_server_url=os.environ["API_SERVER_URL"],
            internal_token=os.environ["MONIMO_INTERNAL_TOKEN"],
            slack_webhook=os.environ["SLACK_WEBHOOK"],
            ping_url=os.environ["PING_URL"],
        )


def lambda_handler(event, context):
    return run(Config.from_env())


def run(
    config: Config,
    send_order=canary.send_order,
    fetch_freshness=canary.fetch_freshness,
    fetch_self_check=canary.fetch_self_check,
    post_slack=notify.post_slack,
    ping=notify.ping,
) -> dict:
    """결과를 돌려주되 던지지 않는다. 한 주기가 실패해도 다음 주기는 그대로 돈다."""
    try:
        result = _canary(config, send_order, fetch_freshness, post_slack)
        # 카나리가 어떻게 끝났든 자기 점검은 따로 본다. 둘은 서로 다른 것을 잡는다 (파이프라인 vs PG · Detector · Collector)
        result["self_check"] = _self_check(config, fetch_self_check, post_slack)
        return result
    finally:
        # 판정이 어떻게 끝났든 보낸다. 안 보내면 Healthchecks 가 Watchdog 이 죽은 것으로 본다
        _quietly("Healthchecks 핑", ping, config.ping_url)


def _canary(config, send_order, fetch_freshness, post_slack) -> dict:
    try:
        traceparent = send_order(config.shop_order_url)
    except Exception:
        log.exception("카나리 주문 호출 실패")
        # 카나리를 못 넣었으니 조회하면 직전 주기 것이 잡혀 "정상"으로 오판한다
        _quietly("Slack", post_slack, config.slack_webhook, _ORDER_FAILED)
        return {"result": "ORDER_FAILED"}

    log.info("카나리 주문 전송 traceparent=%s", traceparent)

    try:
        freshness = fetch_freshness(config.api_server_url, config.internal_token)
    except Exception:
        log.exception("카나리 신선도 조회 실패")
        _quietly("Slack", post_slack, config.slack_webhook, _FRESHNESS_UNREACHABLE)
        return {"result": "FRESHNESS_UNREACHABLE"}

    if freshness.get("fresh"):
        log.info("카나리 정상 age_sec=%s", freshness.get("age_sec"))
        return {"result": "FRESH"}

    _quietly("Slack", post_slack, config.slack_webhook, _stale_text(freshness))
    return {"result": "STALE"}


def _self_check(config, fetch_self_check, post_slack) -> str:
    """PG · Detector · Collector 가 멈췄는지 (backend #226). Detector 가 PG 에 막히면 경보 체계 전체가 조용해지므로 밖에서 본다"""
    try:
        data = fetch_self_check(config.api_server_url, config.internal_token)
    except Exception:
        log.exception("자기 점검 조회 실패")
        _quietly("Slack", post_slack, config.slack_webhook, _SELF_CHECK_UNREACHABLE)
        return "UNREACHABLE"

    lines = _problem_lines(data)
    if not lines:
        log.info("자기 점검 정상 %s", _summary(data))
        return "OK"

    _quietly("Slack", post_slack, config.slack_webhook, _SELF_CHECK_PROBLEM + "\n" + "\n".join(lines))
    return "PROBLEM"


def _quietly(what: str, call, *args) -> None:
    """실패를 로그로만 남긴다. 알림 · 핑이 터져도 나머지 주기는 끝까지 간다"""
    try:
        call(*args)
    except Exception:
        log.exception("%s 실패", what)


_ORDER_FAILED = ":rotating_light: 파수꾼 — 쇼핑몰 주문 API 에 카나리를 넣지 못했습니다. 신선도 조회는 건너뛰었습니다"
_FRESHNESS_UNREACHABLE = ":rotating_light: 파수꾼 — API 서버 신선도 조회에 실패했습니다. 파이프라인이 아니라 API 서버 쪽일 수 있습니다"
_SELF_CHECK_UNREACHABLE = ":rotating_light: 파수꾼 — API 서버 자기 점검 조회에 실패했습니다. API 서버가 죽었거나 내부 문이 막혔습니다"
_SELF_CHECK_PROBLEM = ":rotating_light: 파수꾼 — 자기 점검에서 멈춘 것이 있습니다"


def _stale_text(freshness: dict) -> str:
    """카나리가 한 건도 없으면 last_signal_at · age_sec 이 null 로 온다 (backend #160). 그때는 시각을 적지 않는다"""
    threshold = freshness.get("threshold_sec")
    if freshness.get("age_sec") is None:
        return f":rotating_light: 파수꾼 — 카나리가 끊겼습니다 (마지막 신호 없음 / 기준 {threshold}초)"
    return (
        ":rotating_light: 파수꾼 — 카나리가 끊겼습니다 "
        f"(마지막 신호 {freshness['last_signal_at']}, {freshness['age_sec']}초 전 / 기준 {threshold}초)"
    )


# 항목 이름 · 무엇이 멈춘 것인지. Detector 가 멈추면 경보가 안 나고, Collector 비율 갱신이 멈추면 샘플링이 낡은 값으로 돈다
_AGE_ITEMS = (("detector", "탐지 평가"), ("collector", "수집기 비율 갱신"))


def _problem_lines(data: dict) -> list:
    """ok=false 인 항목마다 한 줄. 값이 없으면(못 읽음) 그 사실을 적는다"""
    lines = []
    pg = data.get("postgres") or {}
    if not pg.get("ok"):
        lines.append(f"• PostgreSQL 응답 없음 ({pg.get('ms')}ms, {pg.get('error')})")
    for key, name in _AGE_ITEMS:
        item = data.get(key) or {}
        if item.get("ok"):
            continue
        if item.get("age_sec") is None:
            lines.append(f"• {name} 게이지를 못 읽음 ({item.get('error')})")
        else:
            lines.append(f"• {name} 멈춤 — {round(item['age_sec'])}초째 (기준 {item.get('max_sec')}초)")
    return lines


def _summary(data: dict) -> str:
    pg = data.get("postgres") or {}
    ages = " ".join(f"{key}_age={(data.get(key) or {}).get('age_sec')}" for key, _ in _AGE_ITEMS)
    return f"pg_ms={pg.get('ms')} {ages}"
