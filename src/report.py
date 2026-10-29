"""판정 결과를 항목(finding)으로 모으고 Slack 문구를 만든다. 항목마다 심각도와 "그래서 무엇이 안 되는가" 를 붙인다 (#26)."""

from datetime import datetime, timezone

CRITICAL = "CRITICAL"
WARNING = "WARNING"

# key → (이름, 심각도, 뜻). 순서가 Slack 줄 순서다. 수집 길 → API server → 경보 길 순
ITEMS = {
    "order": ("카나리 주문 실패", CRITICAL, "쇼핑몰 주문 API 가 안 받음 · 신선도 조회는 건너뜀"),
    "canary": ("카나리 끊김", CRITICAL, "수집 길(shop → Collector → Kafka → Ingester → ClickHouse) 어딘가 멈춤"),
    "freshness": ("API server 신선도 조회 실패", CRITICAL, "파이프라인이 아니라 API server 쪽일 수 있음"),
    "self_check": ("API server 자기 점검 조회 실패", CRITICAL, "API server 가 죽었거나 내부 문이 막힘"),
    "postgres": ("PostgreSQL 응답 없음", CRITICAL, "경보 판정 · 설정 · 발송 전부 멈춤"),
    "detector": ("Detector 평가 멈춤", CRITICAL, "경보가 안 만들어짐"),
    "collector": ("Collector 비율 갱신 멈춤", WARNING, "샘플링이 낡은 값으로 돔"),
    "notifier": ("Notifier 큐 읽기 멈춤", CRITICAL, "경보가 Slack 으로 안 나감"),
    "outbox": ("발송 적체", CRITICAL, "경보가 Slack 으로 안 나감"),
}

# 자기 점검 응답에서 "마지막 성공 뒤 몇 초" 게이지로 오는 항목
AGE_ITEMS = ("detector", "collector", "notifier")
# 자기 점검 조회가 터지면 상태를 알 수 없는 항목
SELF_CHECK_ITEMS = ("postgres", *AGE_ITEMS, "outbox")


def stale_detail(freshness: dict) -> str:
    """카나리가 한 건도 없으면 last_signal_at · age_sec 이 null 로 온다 (backend #160). 그때는 시각을 적지 않는다"""
    threshold = freshness.get("threshold_sec")
    if freshness.get("age_sec") is None:
        return f"마지막 신호 없음 (기준 {threshold}초)"
    return f"마지막 신호 {freshness['last_signal_at']}, {freshness['age_sec']}초 전 (기준 {threshold}초)"


def failure_detail(what: str, error: BaseException) -> str:
    return f"{what} ({type(error).__name__})"


def self_check_problems(data: dict) -> dict:
    """ok=false 인 항목마다 key → 상세. 응답에 없는 항목은 건너뛴다 (옛 API server 에서 "못 읽음" 으로 울리지 않게)"""
    problems = {}
    pg = data.get("postgres")
    if pg is not None and not pg.get("ok"):
        problems["postgres"] = f"{pg.get('ms')}ms, {pg.get('error')}"
    for key in AGE_ITEMS:
        if key not in data:
            continue
        item = data.get(key) or {}
        if item.get("ok"):
            continue
        if item.get("age_sec") is None:
            problems[key] = f"게이지 못 읽음 ({item.get('error')})"
        else:
            problems[key] = f"{round(item['age_sec'])}초째 (기준 {item.get('max_sec')}초)"
    outbox = _outbox_detail(data)
    if outbox:
        problems["outbox"] = outbox
    return problems


def _outbox_detail(data: dict):
    """발송 큐 적체 (backend #267). Notifier 게이지가 작아도 웹훅이 틀리거나 서킷이 열리면 여기만 커진다"""
    if "outbox" not in data:
        return None
    item = data.get("outbox") or {}
    if item.get("ok"):
        return None
    if item.get("error"):
        return f"발송 큐를 못 봄 ({item.get('error')})"
    failed = f"최근 실패 {item.get('failed_recent')}건"
    oldest = item.get("oldest_pending_sec")
    # 웹훅이 틀리면 줄이 재시도 한도에 닿아 FAILED 로 끝나므로 미발송이 없어도 온다. 그때는 실패 수만 적는다
    if oldest is None:
        return f"{failed} (재시도 한도 도달)"
    return f"미발송 가장 오래된 것 {round(oldest)}초째 (기준 {item.get('max_sec')}초), {failed}"


def problem_line(key: str, detail: str, since: str) -> str:
    name, severity, meaning = ITEMS[key]
    return f"[PROBLEM][{severity}] {name} — {detail} · {meaning} (감지 {since})"


def recovered_line(key: str, since: str, now: str) -> str:
    name, _, _ = ITEMS[key]
    return f"[RECOVERED] {name} — 복구 (감지 {since}, 복구 {now}, {duration_text(since, now)})"


def duration_text(since: str, now: str) -> str:
    seconds = int((parse(now) - parse(since)).total_seconds())
    if seconds < 60:
        return f"{seconds}초"
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}분" if rest == 0 else f"{minutes}분 {rest}초"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}시간" if minutes == 0 else f"{hours}시간 {minutes}분"


def stamp(moment: datetime) -> str:
    """UTC · 초 단위. monimo-alert 의 발화 시각과 같은 꼴 (2026-10-16T08:22:00Z)"""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
