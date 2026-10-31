from datetime import datetime, timedelta, timezone

from handler import Config, run
from state import MemoryStateStore

CONFIG = Config(
    shop_order_url="http://shop/api/orders",
    api_server_url="http://api",
    internal_token="token",
    slack_webhook="http://slack",
    ping_url="http://ping",
)

STALE = {"last_signal_at": "2026-10-01T00:00:00Z", "age_sec": 900, "threshold_sec": 180, "fresh": False}
FRESH = {"last_signal_at": "2026-10-01T00:00:00Z", "age_sec": 15, "threshold_sec": 180, "fresh": True}

# 자기 점검 응답 (backend #226 · #267). 다섯 다 ok
ALL_OK = {
    "postgres": {"ok": True, "ms": 3, "error": None},
    "detector": {"ok": True, "age_sec": 11.2, "max_sec": 180, "error": None},
    "collector": {"ok": True, "age_sec": 8.0, "max_sec": 180, "error": None},
    "notifier": {"ok": True, "age_sec": 2.5, "max_sec": 180, "error": None},
    "outbox": {"ok": True, "oldest_pending_sec": None, "failed_recent": 0, "max_sec": 180, "error": None},
}

T0 = datetime(2026, 10, 29, 12, 33, 0, tzinfo=timezone.utc)


def minutes(n: int) -> datetime:
    return T0 + timedelta(minutes=n)


class Spy:
    """부른 횟수만 센다. 네트워크를 타지 않는다"""

    def __init__(self, result=None, error=None):
        self.calls = []
        self._result = result
        self._error = error

    def __call__(self, *args):
        self.calls.append(args)
        if self._error:
            raise self._error
        return self._result


class BrokenStore:
    """저장소가 죽은 상황"""

    def load(self):
        raise ConnectionError("dynamodb down")

    def save(self, state):
        raise ConnectionError("dynamodb down")


def cycle(store, slack, *, freshness=FRESH, self_check=ALL_OK, order=Spy("tp"), at=T0, ping=None):
    """한 주기. 기본은 전부 정상"""
    fetch_freshness = freshness if isinstance(freshness, Spy) else Spy(freshness)
    fetch_self_check = self_check if isinstance(self_check, Spy) else Spy(self_check)
    return run(
        CONFIG,
        send_order=order,
        fetch_freshness=fetch_freshness,
        fetch_self_check=fetch_self_check,
        post_slack=slack,
        ping=ping or Spy(),
        store=store,
        now=at,
    )


def texts(slack: Spy) -> list:
    return [call[1] for call in slack.calls]


# ── 문제 · 복구를 한 번씩


def test_카나리가_끊기면_PROBLEM_한_줄_심각도와_뜻과_감지_시각이_있다():
    store, slack = MemoryStateStore(), Spy()

    result = cycle(store, slack, freshness=STALE)

    assert result == {"result": "STALE", "self_check": "OK", "sent": 1}
    assert texts(slack) == [
        "[PROBLEM][CRITICAL] 카나리 끊김 — 마지막 신호 2026-10-01T00:00:00Z, 900초 전 (기준 180초)"
        " · 수집 길(shop → Collector → Kafka → Ingester → ClickHouse) 어딘가 멈춤 (감지 2026-10-29T12:33:00Z)"
    ]


def test_같은_문제가_이어지면_다시_보내지_않는다():
    store, slack = MemoryStateStore(), Spy()
    cycle(store, slack, freshness=STALE, at=minutes(0))

    result = cycle(store, slack, freshness=STALE, at=minutes(1))

    assert result["sent"] == 0
    assert len(slack.calls) == 1


def test_풀리면_RECOVERED_한_줄에_걸린_시간이_있다():
    store, slack = MemoryStateStore(), Spy()
    cycle(store, slack, freshness=STALE, at=minutes(0))

    result = cycle(store, slack, freshness=FRESH, at=minutes(8))

    assert result == {"result": "FRESH", "self_check": "OK", "sent": 1}
    assert texts(slack)[1] == "[RECOVERED] 카나리 끊김 — 복구 (감지 2026-10-29T12:33:00Z, 복구 2026-10-29T12:41:00Z, 8분)"
    # 복구 뒤에는 기억에서 지워져 다음 주기는 조용하다
    assert cycle(store, slack, at=minutes(9))["sent"] == 0


def test_전부_정상이면_처음부터_조용하다():
    store, slack = MemoryStateStore(), Spy()

    result = cycle(store, slack)

    assert result == {"result": "FRESH", "self_check": "OK", "sent": 0}
    assert slack.calls == []
    assert store.state["active"] == {}


# ── 카나리 쪽 실패


def test_카나리가_한_건도_없어도_STALE_이고_문구에_None_이_없다():
    slack = Spy()
    none = {"last_signal_at": None, "age_sec": None, "threshold_sec": 180, "fresh": False}

    result = cycle(MemoryStateStore(), slack, freshness=none)

    assert result["result"] == "STALE"
    assert "None" not in texts(slack)[0]
    assert "마지막 신호 없음 (기준 180초)" in texts(slack)[0]


def test_주문이_실패하면_조회를_건너뛰고_PROBLEM_이_가고_핑은_나간다():
    slack, heartbeat, freshness = Spy(), Spy(), Spy(STALE)

    result = cycle(MemoryStateStore(), slack, order=Spy(error=ConnectionError()), freshness=freshness, ping=heartbeat)

    # 조회했으면 직전 주기 카나리가 잡혀 "정상" 으로 오판했을 자리다
    assert result["result"] == "ORDER_FAILED"
    assert freshness.calls == []
    assert "[PROBLEM][CRITICAL] 카나리 주문 실패 — 주문 호출 실패 (ConnectionError) · 쇼핑몰 주문 API 가 안 받음" in texts(slack)[0]
    assert heartbeat.calls == [(CONFIG.ping_url,)]


def test_신선도_조회가_터지면_카나리_상태는_모르는_것으로_두고_복구로_치지_않는다():
    store, slack = MemoryStateStore(), Spy()
    cycle(store, slack, freshness=STALE, at=minutes(0))

    result = cycle(store, slack, freshness=Spy(error=TimeoutError()), at=minutes(1))

    assert result["result"] == "FRESHNESS_UNREACHABLE"
    assert texts(slack)[1] == (
        "[PROBLEM][CRITICAL] API server 신선도 조회 실패 — 조회 실패 (TimeoutError)"
        " · 파이프라인이 아니라 API server 쪽일 수 있음 (감지 2026-10-29T12:34:00Z)"
    )
    assert "canary" in store.state["active"]  # 끊김은 아직 열려 있다


# ── 자기 점검 쪽


def test_PG_만_멈추면_그_한_줄만_간다():
    slack = Spy()
    pg_down = dict(ALL_OK, postgres={"ok": False, "ms": 5003, "error": "timeout"})

    result = cycle(MemoryStateStore(), slack, self_check=pg_down)

    assert result == {"result": "FRESH", "self_check": "PROBLEM", "sent": 1}
    assert texts(slack) == [
        "[PROBLEM][CRITICAL] PostgreSQL 응답 없음 — 5003ms, timeout · 경보 판정 · 설정 · 발송 전부 멈춤 (감지 2026-10-29T12:33:00Z)"
    ]


def test_여러_항목이_한_번에_멈추면_한_메시지에_줄마다_심각도가_붙는다():
    slack = Spy()
    all_bad = {
        "postgres": {"ok": False, "ms": 5001, "error": "timeout"},
        "detector": {"ok": False, "age_sec": 251.4, "max_sec": 180, "error": None},
        "collector": {"ok": False, "age_sec": None, "max_sec": 180, "error": "unreachable"},
    }

    cycle(MemoryStateStore(), slack, self_check=all_bad)

    assert len(slack.calls) == 1
    lines = texts(slack)[0].split("\n")
    assert lines[0].startswith("[PROBLEM][CRITICAL] PostgreSQL 응답 없음 — 5001ms, timeout")
    assert lines[1].startswith("[PROBLEM][CRITICAL] Detector 평가 멈춤 — 251초째 (기준 180초) · 경보가 안 만들어짐")
    assert lines[2].startswith("[PROBLEM][WARNING] Collector 비율 갱신 멈춤 — 게이지 못 읽음 (unreachable) · 샘플링이 낡은 값으로 돔")
    assert "None" not in texts(slack)[0].replace("(unreachable)", "")


def test_Notifier_큐_읽기가_멈추면_경보가_안_나간다는_뜻이_붙는다():
    slack = Spy()
    stuck = dict(ALL_OK, notifier={"ok": False, "age_sec": 412.0, "max_sec": 180, "error": None})

    cycle(MemoryStateStore(), slack, self_check=stuck)

    assert texts(slack) == [
        "[PROBLEM][CRITICAL] Notifier 큐 읽기 멈춤 — 412초째 (기준 180초) · 경보가 Slack 으로 안 나감 (감지 2026-10-29T12:33:00Z)"
    ]


def test_발송_큐가_쌓이면_적체_한_줄_실패만_있으면_실패_수만_못_보면_error():
    slack = Spy()
    backlog = dict(ALL_OK, outbox={"ok": False, "oldest_pending_sec": 301, "failed_recent": 2, "max_sec": 180, "error": None})
    failed_only = dict(ALL_OK, outbox={"ok": False, "oldest_pending_sec": None, "failed_recent": 1, "max_sec": 180, "error": None})
    unreadable = dict(ALL_OK, outbox={"ok": False, "oldest_pending_sec": None, "failed_recent": 0, "max_sec": 180, "error": "timeout"})

    cycle(MemoryStateStore(), slack, self_check=backlog)
    cycle(MemoryStateStore(), slack, self_check=failed_only)
    cycle(MemoryStateStore(), slack, self_check=unreadable)

    assert "[PROBLEM][CRITICAL] 발송 적체 — 미발송 가장 오래된 것 301초째 (기준 180초), 최근 실패 2건 · 경보가 Slack 으로 안 나감" in texts(slack)[0]
    assert "발송 적체 — 최근 실패 1건 (재시도 한도 도달)" in texts(slack)[1]
    assert "발송 적체 — 발송 큐를 못 봄 (timeout)" in texts(slack)[2]
    assert all("None" not in t for t in texts(slack))


def test_응답에_notifier_outbox_가_없으면_옛_API_server_로_보고_건너뛴다():
    slack = Spy()
    old = {k: v for k, v in ALL_OK.items() if k in ("postgres", "detector", "collector")}

    result = cycle(MemoryStateStore(), slack, self_check=old)

    assert result == {"result": "FRESH", "self_check": "OK", "sent": 0}
    assert slack.calls == []


def test_자기_점검_조회가_터지면_그것만_알리고_항목_상태는_그대로_둔다():
    store, slack = MemoryStateStore(), Spy()
    pg_down = dict(ALL_OK, postgres={"ok": False, "ms": 5003, "error": "timeout"})
    cycle(store, slack, self_check=pg_down, at=minutes(0))

    result = cycle(store, slack, self_check=Spy(error=TimeoutError()), at=minutes(1))

    assert result["self_check"] == "UNREACHABLE"
    assert texts(slack)[1].startswith("[PROBLEM][CRITICAL] API server 자기 점검 조회 실패 — 조회 실패 (TimeoutError) · API server 가 죽었거나")
    assert "postgres" in store.state["active"]  # PG 문제는 복구로 안 친다 : 이번 주기엔 알 수 없었다

    # 조회가 돌아오고 PG 도 멀쩡하면 둘 다 복구
    result = cycle(store, slack, at=minutes(5))
    assert result["sent"] == 2
    assert "[RECOVERED] API server 자기 점검 조회 실패 — 복구 (감지 2026-10-29T12:34:00Z, 복구 2026-10-29T12:38:00Z, 4분)" in texts(slack)[2]
    assert "[RECOVERED] PostgreSQL 응답 없음 — 복구 (감지 2026-10-29T12:33:00Z, 복구 2026-10-29T12:38:00Z, 5분)" in texts(slack)[2]


# ── 기억이 없을 때


def test_저장소가_없으면_매_주기_현재_문제를_다시_알린다():
    slack = Spy()

    cycle(None, slack, freshness=STALE, at=minutes(0))
    cycle(None, slack, freshness=STALE, at=minutes(1))

    assert len(slack.calls) == 2


def test_저장소를_못_읽거나_못_써도_현재_문제는_보내고_주기는_끝난다():
    slack, heartbeat = Spy(), Spy()

    result = cycle(BrokenStore(), slack, freshness=STALE, ping=heartbeat)

    assert result["sent"] == 1
    assert "카나리 끊김" in texts(slack)[0]
    assert heartbeat.calls == [(CONFIG.ping_url,)]


def test_기억에_걸린_시간은_초_분_시간으로_읽기_좋게():
    from report import duration_text

    assert duration_text("2026-10-29T12:00:00Z", "2026-10-29T12:00:45Z") == "45초"
    assert duration_text("2026-10-29T12:00:00Z", "2026-10-29T12:08:00Z") == "8분"
    assert duration_text("2026-10-29T12:00:00Z", "2026-10-29T12:08:30Z") == "8분 30초"
    assert duration_text("2026-10-29T12:00:00Z", "2026-10-29T13:05:00Z") == "1시간 5분"
