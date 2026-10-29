"""한 주기를 조립한다. 바깥과는 canary · notify · state 를 통해서만 말한다."""

import logging
import os
from datetime import datetime, timezone
from typing import NamedTuple, Optional

import canary
import notify
import report
import state

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
    return run(Config.from_env(), store=state.from_env())


def run(
    config: Config,
    send_order=canary.send_order,
    fetch_freshness=canary.fetch_freshness,
    fetch_self_check=canary.fetch_self_check,
    post_slack=notify.post_slack,
    ping=notify.ping,
    store=None,
    now: Optional[datetime] = None,
) -> dict:
    """결과를 돌려주되 던지지 않는다. 한 주기가 실패해도 다음 주기는 그대로 돈다.
    Slack 은 지난 주기와 달라진 항목만 : 문제가 생기면 [PROBLEM] 한 번, 풀리면 [RECOVERED] 한 번 (#26)"""
    moment = report.stamp(now or datetime.now(timezone.utc))
    try:
        result, problems, unknown = _observe(config, send_order, fetch_freshness, fetch_self_check)
        lines = _reconcile(store, problems, unknown, moment)
        if lines:
            _quietly("Slack", post_slack, config.slack_webhook, "\n".join(lines))
        result["sent"] = len(lines)
        return result
    finally:
        # 판정이 어떻게 끝났든 보낸다. 안 보내면 Healthchecks 가 Watchdog 이 죽은 것으로 본다
        _quietly("Healthchecks 핑", ping, config.ping_url)


def _observe(config, send_order, fetch_freshness, fetch_self_check):
    """이번 주기에 본 것. problems = 문제인 항목(key → 상세), unknown = 이번 주기에 알 수 없었던 항목(지난 상태를 그대로 둔다)"""
    problems, unknown = {}, set()
    result = {"result": _canary(config, send_order, fetch_freshness, problems, unknown)}
    # 카나리가 어떻게 끝났든 자기 점검은 따로 본다. 둘은 서로 다른 것을 잡는다 (파이프라인 vs PG · Detector · Collector · Notifier)
    result["self_check"] = _self_check(config, fetch_self_check, problems, unknown)
    return result, problems, unknown


def _canary(config, send_order, fetch_freshness, problems, unknown) -> str:
    try:
        traceparent = send_order(config.shop_order_url)
    except Exception as e:
        log.exception("카나리 주문 호출 실패")
        # 카나리를 못 넣었으니 조회하면 직전 주기 것이 잡혀 "정상"으로 오판한다
        problems["order"] = report.failure_detail("주문 호출 실패", e)
        unknown.update({"canary", "freshness"})
        return "ORDER_FAILED"

    log.info("카나리 주문 전송 traceparent=%s", traceparent)

    try:
        freshness = fetch_freshness(config.api_server_url, config.internal_token)
    except Exception as e:
        log.exception("카나리 신선도 조회 실패")
        problems["freshness"] = report.failure_detail("조회 실패", e)
        unknown.add("canary")
        return "FRESHNESS_UNREACHABLE"

    if freshness.get("fresh"):
        log.info("카나리 정상 age_sec=%s", freshness.get("age_sec"))
        return "FRESH"

    problems["canary"] = report.stale_detail(freshness)
    return "STALE"


def _self_check(config, fetch_self_check, problems, unknown) -> str:
    """PG · Detector · Collector · Notifier 가 멈췄는지, 발송 큐가 쌓이는지 (backend #226 · #267).
    Detector 가 PG 에 막히면 경보 체계 전체가 조용해지고, Notifier 가 죽으면 그 사실을 알릴 것도 Notifier 라 밖에서 본다"""
    try:
        data = fetch_self_check(config.api_server_url, config.internal_token)
    except Exception as e:
        log.exception("자기 점검 조회 실패")
        problems["self_check"] = report.failure_detail("조회 실패", e)
        unknown.update(report.SELF_CHECK_ITEMS)
        return "UNREACHABLE"

    found = report.self_check_problems(data)
    problems.update(found)
    log.info("자기 점검 %s %s", "문제 " + ",".join(found) if found else "정상", _summary(data))
    return "PROBLEM" if found else "OK"


def _reconcile(store, problems: dict, unknown: set, now: str) -> list:
    """지난 주기와 비교해 바뀐 항목의 줄만 만든다. 기억이 없으면(첫 주기 · 저장소 못 읽음 · 저장소 없음) 현재 문제를 전부 새로 알린다 —
    조용해지는 쪽으로 실패하지 않게"""
    previous = _load(store)
    was_active = (previous or {}).get("active") or {}
    active, opened, closed = {}, [], []
    for key in report.ITEMS:
        was = was_active.get(key)
        if key in unknown:
            if was:
                active[key] = was
            continue
        if key in problems:
            if was:
                active[key] = dict(was, detail=problems[key])
            else:
                active[key] = {"since": now, "detail": problems[key]}
                opened.append(report.problem_line(key, problems[key], now))
        elif was:
            closed.append(report.recovered_line(key, was["since"], now))
    _save(store, {"active": active, "updated_at": now})
    return opened + closed


def _load(store):
    if store is None:
        return None
    try:
        return store.load()
    except Exception:
        log.exception("지난 상태를 못 읽음 — 기억 없이 간다 (현재 문제를 다시 알린다)")
        return None


def _save(store, snapshot: dict) -> None:
    if store is None:
        return
    _quietly("상태 저장", store.save, snapshot)


def _quietly(what: str, call, *args) -> None:
    """실패를 로그로만 남긴다. 알림 · 핑 · 저장이 터져도 나머지 주기는 끝까지 간다"""
    try:
        call(*args)
    except Exception:
        log.exception("%s 실패", what)


def _summary(data: dict) -> str:
    pg = data.get("postgres") or {}
    ages = " ".join(f"{key}_age={(data.get(key) or {}).get('age_sec')}" for key in report.AGE_ITEMS if key in data)
    outbox = data.get("outbox") or {}
    return f"pg_ms={pg.get('ms')} {ages} outbox_oldest={outbox.get('oldest_pending_sec')} outbox_failed={outbox.get('failed_recent')}"
