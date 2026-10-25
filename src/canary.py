"""카나리 한 번 — 쇼핑몰 주문 API 에 traceparent 를 붙여 쏘고, API server 에 신선도와 자기 점검을 되묻는다."""

import json
import secrets
import urllib.parse
import urllib.request

# traceparent 끝자리 flags. 01 = sampled. 00 으로 보내면 에이전트가 스팬을 기록하지 않아 카나리가 사라진다
_SAMPLED = "01"

# Ingester 가 이 값을 보고 스팬 attributes 에 monimo.canary 키를 남긴다 (ADR #41)
TRACESTATE = "monimon=canary"

# 카나리가 처음 닿는 서비스. 판정 대상을 하나로 고정해야 age_sec 이 흔들리지 않는다
SERVICE_NAME = "shop-order"

# 재고 표에 있는 상품이어야 한다. 없는 상품은 재고 서비스 404 → 주문 502 라 매 분 ORDER_FAILED 가 간다 (k6 와 같은 P-100)
ORDER_BODY = {"productId": "P-100", "quantity": 1, "amount": 1000}

ORDER_TIMEOUT_SEC = 10
FRESHNESS_TIMEOUT_SEC = 10
# API server 가 PG 두드림을 5초에 끊어 답하므로(backend #226) 10초면 넉넉하다
SELF_CHECK_TIMEOUT_SEC = 10


def new_traceparent() -> str:
    """W3C Trace Context 헤더. `00-<trace-id 32>-<span-id 16>-01`"""
    return f"00-{_nonzero_hex(16)}-{_nonzero_hex(8)}-{_SAMPLED}"


def _nonzero_hex(size: int) -> str:
    # 전부 0 인 id 는 W3C 상 무효라 받는 쪽이 헤더를 통째로 버린다
    while True:
        value = secrets.token_hex(size)
        if value.strip("0"):
            return value


def send_order(shop_order_url: str, timeout: float = ORDER_TIMEOUT_SEC) -> str:
    """가짜 주문 1건을 쏘고 붙인 traceparent 를 돌려준다. 실패하면 예외를 그대로 올린다."""
    traceparent = new_traceparent()
    headers = {
        "Content-Type": "application/json",
        "traceparent": traceparent,
        # tracestate 는 traceparent 와 짝으로만 전파된다. 혼자 보내면 버려진다
        "tracestate": TRACESTATE,
    }
    _read(
        urllib.request.Request(
            shop_order_url,
            method="POST",
            data=json.dumps(ORDER_BODY).encode(),
            headers=headers,
        ),
        timeout,
    )
    return traceparent


def fetch_freshness(
    api_server_url: str,
    internal_token: str,
    service_name: str = SERVICE_NAME,
    timeout: float = FRESHNESS_TIMEOUT_SEC,
) -> dict:
    """가장 최근 카나리가 몇 초 전인지 묻는다. 판정(`fresh`)은 API server 가 한다."""
    query = urllib.parse.urlencode({"service_name": service_name})
    url = f"{api_server_url.rstrip('/')}/api/v1/internal/canary/freshness?{query}"
    raw = _read(
        urllib.request.Request(url, method="GET", headers={"X-Internal-Token": internal_token}),
        timeout,
    )
    # 성공 응답은 명세 봉투 { data: ... } 다 (API 명세 §0)
    return json.loads(raw)["data"]


def fetch_self_check(
    api_server_url: str,
    internal_token: str,
    timeout: float = SELF_CHECK_TIMEOUT_SEC,
) -> dict:
    """PG · Detector · Collector 가 멈췄는지 API server 에 묻는다 (backend #226). 항목마다 `ok` 는 API server 가 낸다."""
    url = f"{api_server_url.rstrip('/')}/api/v1/internal/self-check"
    raw = _read(
        urllib.request.Request(url, method="GET", headers={"X-Internal-Token": internal_token}),
        timeout,
    )
    return json.loads(raw)["data"]


def _read(request: urllib.request.Request, timeout: float) -> bytes:
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()
