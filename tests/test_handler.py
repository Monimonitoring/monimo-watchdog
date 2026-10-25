from handler import Config, run

CONFIG = Config(
    shop_order_url="http://shop/api/orders",
    api_server_url="http://api",
    internal_token="token",
    slack_webhook="http://slack",
    ping_url="http://ping",
)

STALE = {"last_signal_at": "2026-10-01T00:00:00Z", "age_sec": 900, "threshold_sec": 180, "fresh": False}
FRESH = {"last_signal_at": "2026-10-01T00:00:00Z", "age_sec": 15, "threshold_sec": 180, "fresh": True}

# 자기 점검 응답 (backend #226). 셋 다 ok
ALL_OK = {
    "postgres": {"ok": True, "ms": 3, "error": None},
    "detector": {"ok": True, "age_sec": 11.2, "max_sec": 180, "error": None},
    "collector": {"ok": True, "age_sec": 8.0, "max_sec": 180, "error": None},
}


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


def test_카나리가_끊기면_slack_을_부른다():
    slack = Spy()

    result = run(CONFIG, send_order=Spy("tp"), fetch_freshness=Spy(STALE), fetch_self_check=Spy(ALL_OK), post_slack=slack, ping=Spy())

    assert result == {"result": "STALE", "self_check": "OK"}
    assert "900초 전" in slack.calls[0][1]


def test_카나리가_한_건도_없어도_STALE_이고_문구에_None_이_없다():
    slack = Spy()
    none = {"last_signal_at": None, "age_sec": None, "threshold_sec": 180, "fresh": False}

    result = run(CONFIG, send_order=Spy("tp"), fetch_freshness=Spy(none), fetch_self_check=Spy(ALL_OK), post_slack=slack, ping=Spy())

    assert result == {"result": "STALE", "self_check": "OK"}
    assert "None" not in slack.calls[0][1]
    assert "마지막 신호 없음" in slack.calls[0][1]


def test_조회가_터져도_핑은_나간다():
    heartbeat = Spy()

    result = run(
        CONFIG,
        send_order=Spy("tp"),
        fetch_freshness=Spy(error=TimeoutError()),
        fetch_self_check=Spy(ALL_OK),
        post_slack=Spy(),
        ping=heartbeat,
    )

    assert result == {"result": "FRESHNESS_UNREACHABLE", "self_check": "OK"}
    assert heartbeat.calls == [(CONFIG.ping_url,)]


def test_주문이_실패하면_조회를_건너뛴다():
    freshness = Spy(STALE)

    result = run(
        CONFIG,
        send_order=Spy(error=ConnectionError()),
        fetch_freshness=freshness,
        fetch_self_check=Spy(ALL_OK),
        post_slack=Spy(),
        ping=Spy(),
    )

    # 조회했으면 직전 주기 카나리가 잡혀 "정상" 으로 오판했을 자리다
    assert result == {"result": "ORDER_FAILED", "self_check": "OK"}
    assert freshness.calls == []


def test_자기_점검이_다_ok_면_slack_을_안_부른다():
    slack = Spy()

    result = run(CONFIG, send_order=Spy("tp"), fetch_freshness=Spy(FRESH), fetch_self_check=Spy(ALL_OK), post_slack=slack, ping=Spy())

    assert result == {"result": "FRESH", "self_check": "OK"}
    assert slack.calls == []


def test_PG_만_멈추면_그_한_줄만_slack_에_간다():
    slack = Spy()
    pg_down = dict(ALL_OK, postgres={"ok": False, "ms": 5003, "error": "timeout"})

    result = run(CONFIG, send_order=Spy("tp"), fetch_freshness=Spy(FRESH), fetch_self_check=Spy(pg_down), post_slack=slack, ping=Spy())

    assert result == {"result": "FRESH", "self_check": "PROBLEM"}
    text = slack.calls[0][1]
    assert "PostgreSQL 응답 없음 (5003ms, timeout)" in text
    assert "Detector" not in text and "Collector" not in text


def test_셋_다_멈추면_세_줄이_한_메시지로_간다():
    slack = Spy()
    all_bad = {
        "postgres": {"ok": False, "ms": 5001, "error": "timeout"},
        "detector": {"ok": False, "age_sec": 251.4, "max_sec": 180, "error": None},
        "collector": {"ok": False, "age_sec": None, "max_sec": 180, "error": "unreachable"},
    }

    run(CONFIG, send_order=Spy("tp"), fetch_freshness=Spy(FRESH), fetch_self_check=Spy(all_bad), post_slack=slack, ping=Spy())

    assert len(slack.calls) == 1
    text = slack.calls[0][1]
    assert "PostgreSQL 응답 없음" in text
    assert "Detector 평가 멈춤 — 251초째 (기준 180초)" in text
    assert "Collector 비율 갱신 게이지를 못 읽음 (unreachable)" in text
    assert "None" not in text.replace("(unreachable)", "")


def test_자기_점검_조회가_터지면_그것만_알리고_카나리_결과는_그대로다():
    slack = Spy()

    result = run(CONFIG, send_order=Spy("tp"), fetch_freshness=Spy(STALE), fetch_self_check=Spy(error=TimeoutError()), post_slack=slack, ping=Spy())

    assert result == {"result": "STALE", "self_check": "UNREACHABLE"}
    assert len(slack.calls) == 2
    assert "자기 점검 조회에 실패" in slack.calls[1][1]
