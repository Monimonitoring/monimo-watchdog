# AGENTS.md — 작업을 시작하기 전에 읽는 파일

> **사람이든 AI 도구든, 이 레포에서 무언가를 고치기 전에 이 파일을 먼저 읽는다.**
> 여기에는 레포 구조, 깨면 안 되는 규칙, 지금까지 한 일, 지금 막혀 있는 것이 들어 있다.
>
> 작업을 끝내면 **§5(지금까지 한 일)와 §6(막혀 있는 것)을 갱신해서 같은 PR 에 넣는다.**

## 0. 정본이 어디인가

| 무엇 | 정본 |
|---|---|
| 설계 결정(ADR) — Watchdog 은 `#01` `#13` `#18` `#41` | `monimo-backend` 레포 `docs/design/01-decisions.md` |
| 한 주기 · 로컬 실행 · 환경변수 · 배포 | 이 레포 `README.md` |
| 카나리 조회 문 명세 (`GET /api/v1/internal/canary/freshness`) | `monimo-backend` 레포 `docs/design/web-v2/api-spec.md` (사본) · 노션 「API 명세」(정본) |
| 카나리 표식이 저장되는 자리 | `monimo-backend` `AGENTS.md` §6 — CH `spans.attributes['monimo.canary']` |

## 1. 레포 한눈에

**Watchdog.** 우리 관제 스택 *바깥*(AWS Lambda)에서 1분마다 쇼핑몰에 카나리 주문 1건을 넣고, 그 신호가 에이전트 → Collector → Kafka → Ingester → ClickHouse → API server 를 실제로 끝까지 지나갔는지 묻는다. 스택이 통째로 죽어도 알림이 가야 하므로 스택 안에 두지 않는다 (ADR `#01` `#13`).

Python 3.13 · 표준 라이브러리만 · Terraform · AWS Lambda · EventBridge Scheduler. 배포는 `src/` 를 zip 으로 묶어 올리는 것이 전부다.

| 폴더 | 하는 일 |
|---|---|
| `src/handler.py` | 한 주기를 조립한다. 환경변수를 `Config` 로 읽고 아래 둘을 차례로 부른다 |
| `src/canary.py` | `traceparent` 생성 · `tracestate: monimon=canary` · 쇼핑몰 주문 호출 · 신선도 조회 |
| `src/notify.py` | Slack 웹훅 · Healthchecks.io 핑 |
| `tests/` | pytest 4건. 네트워크를 타지 않는다 |
| `infra/` | Terraform — Lambda · EventBridge Scheduler · IAM · 로그 그룹. 기본값 없음 |
| `.github/workflows/` | `ci`(pytest + `terraform fmt` · `validate`) · `deploy`(main 의 `src/` 변경 → zip 교체) · 라벨 · Slack |

## 2. 깨면 안 되는 규칙

- **표준 라이브러리만 쓴다.** `requirements-dev.txt` 는 테스트용이다. 의존성을 넣으면 zip 배포가 깨지고 Lambda 레이어가 필요해진다.
- **판정하지 않는다.** `fresh` 는 API server 가 낸다. 기준값(`MONIMO_CANARY_THRESHOLD_SEC`)은 API server 쪽 설정이다. Watchdog 에 판정 로직을 넣으면 기준을 조일 때마다 Lambda 를 다시 배포해야 한다.
- **기다리지 않는다.** 조회에서 보이는 것은 직전 주기(60초 전)의 카나리다. 내 것이 도착하기를 기다리면 Lambda 가 30~60초를 붙잡아 상주 서버와 다를 게 없어진다.
- **`tracestate` 는 `traceparent` 와 짝으로만 보낸다.** 혼자 보내면 버려진다. 값 `monimon=canary` 는 Collector 샘플링 예외(backend `#46`)와 적재 표식(backend `#58`, ADR `#41`)이 그대로 읽는 약속이다. 바꾸면 카나리가 1% 샘플링에 걸려 사라진다.
- **테스트는 네트워크를 타지 않는다.** CI 가 AWS 에도 쇼핑몰에도 닿지 않는다.
- **인프라는 Terraform 으로만 바꾼다.** 일정 · IAM · 환경변수를 콘솔에서 고치면 다음 `apply` 가 되돌린다. `deploy.yml` 은 코드(zip)만 교체한다.
- **비밀값은 레포에 올리지 않는다.** `.env.example` · `terraform.tfvars.example` 에 이름만 둔다. AWS 는 영구 키가 아니라 GitHub Actions OIDC 로 역할을 빌린다. 레포는 퍼블릭이다.
- **쇼핑몰 주문 API 모양(`SHOP_ORDER_URL` · 본문)은 `monimo-shop` 과의 약속이다.** 바뀌면 shop 담당(승조)이 알려 주기로 했다 — 받으면 `src/canary.py` 의 `ORDER_BODY` 를 같은 PR 에서 맞춘다.

## 3. 작업 흐름

1. GitHub 이슈를 만든다. 제목은 커밋 형식과 같게 (`feat: ...`).
2. 브랜치를 판다. `feat/<이슈번호>-<설명>` · `fix/<이슈번호>-<설명>` · `chore/<설명>`. **`origin/develop` 에서 새로 판다.**
3. 커밋 메시지는 `<타입>: <요약> (#이슈)`. 타입은 feat · fix · docs · chore · refactor · test · ci. **모듈이 하나라 범위를 붙이지 않는다.**
4. PR 의 base 는 `develop`. `main` · `develop` 직접 push 는 막혀 있다. `main` 은 배포 단위로 `develop` 에서 한 번에 올린다.

CI 는 **test**(pytest) 와 **terraform**(`fmt -check` · `init -backend=false` · `validate`) 두 개를 돈다. state 에 닿지 않으므로 PR 에서 돌려도 안전하다. `main` 에 `src/` 가 바뀌어 올라가면 `deploy.yml` 이 zip 을 다시 올린다 — 단, 레포 Variable `LAMBDA_FUNCTION_NAME` 이 비어 있으면 통째로 건너뛴다.

## 4. 담당

| 폴더 | 담당 |
|---|---|
| 전체 | 재범 `@jaebeom79` |
| `infra/` · `.github/` | 재범 · 승조 `@SeungJo-02` (배포 · CI 는 같이 본다) |

## 5. 지금까지 한 일

- 레포 초기 구성 — README · LICENSE · PR 템플릿 · CODEOWNERS, PR 라벨 자동화 + 머지 Slack 알림 (`#1`), `.env.example` (`#2`), CODEOWNERS 담당 확정 (`#3`)
- **`#4`** 카나리 한 주기 — `traceparent` 주입 · `tracestate: monimon=canary` · 쇼핑몰 주문 1건 · `GET /api/v1/internal/canary/freshness` 조회 · `fresh` 가 false 면 Slack · 끝나면 Healthchecks.io 핑. 기다리지 않고 판정하지 않는 구조로
- **`#5`** Terraform — Lambda · EventBridge Scheduler(1분, **꺼진 채로 생성** `schedule_enabled = false`) · IAM · 로그 그룹. GitHub Actions OIDC 로 역할을 빌려 `deploy.yml` 이 zip 을 교체
- **`#8`** 라벨러 3개를 한 워크플로로 합침 — 따로 돌면 `type` 라벨이 지워지는 문제
- **`#9`** develop 브랜치 전략 — 워크플로 트리거 `[main, develop]`, 기여 규칙
- **`#12`** `AGENTS.md` · `CLAUDE.md`, README 「AI 와 일한 방법」 절(승조가 맡은 부분만), `docs/prompts/`. 하네스 정본은 backend `docs/seungjo/harness.md` 한 곳
- **`#13`** 카나리 주문을 게이트웨이(8090) · 재고 있는 상품 `P-100` 으로, 리전 서울 권장 (`#14`). 카나리가 한 건도 없을 때 문구의 `None초 전` 제거 (`#15` · `#16`)
- **`#20`** 한 주기에 **자기 점검** 단계 — 카나리 뒤에 `GET /api/v1/internal/self-check`(backend `#226`)를 불러 PG 두드림 · Detector · Collector 낡음 게이지(ADR `#54`) 중 `ok=false` 인 항목마다 Slack 한 줄(한 메시지). 카나리 결과와 따로 본다 : backend `#208` ⑥ 실측에서 PG 를 멈추자 Detector · Notifier 가 PG 에 막혀 경보 0건이었고 probe · 카나리도 PG 를 안 봐서, 밖(Lambda)에 있는 Watchdog 이 그 눈이 됐다. 조회가 터지면 `UNREACHABLE` 로 따로 알린다
- 2026-10-26 문서 · 주석의 우리말 조어를 현업 이름으로 바꿨다 (backend `#240` · ADR `#73`). 코드 안 문자열(화면 문구 · Slack 경보 · 테스트 이름)은 backend `#241`

## 6. 지금 막혀 있는 것

| 무엇 | 누가 풀어야 하나 | 안 풀면 |
|---|---|---|
| 카나리 조회 문 `GET /api/v1/internal/canary/freshness` 가 API server 에 없다 | 조회 파트 (Nova) | **일정을 켤 수 없다.** 켜면 1분마다 오탐 Slack 이 하루 1,440번. 적재는 표식을 `attributes['monimo.canary']` 로 남기고 있으니(backend `#58`) 조회 문은 `mapContains(attributes, 'monimo.canary')` 기준으로 만들면 된다. 명세의 `service_name=canary-probe` 기준은 0건이 나온다(backend `AGENTS.md` §6) |
| 배포 변수가 비어 있다 — 레포 Secret `AWS_DEPLOY_ROLE_ARN`, Variable `AWS_REGION` · `LAMBDA_FUNCTION_NAME`, `terraform.tfvars`, backend 버킷 | 배포 (승조) + Watchdog (재범) | `terraform plan` 이 돌지 않고 `deploy.yml` 이 건너뛴다. AWS 계정이 정해져야 한다 |
| 명세는 "서비스 6개가 `/readyz` 를 연다" 고 적는데 Watchdog 은 Lambda 라 상주 문이 없다 | 팀 결정 (backend `02-open-questions.md`) | 화면 S10 「우리 서비스 6개」카드에서 Watchdog 줄을 어떻게 채울지 정해야 한다. Healthchecks.io 핑 상태를 대신 보여 주는 것이 한 방법 |
| 카나리 주문이 쇼핑몰 MySQL 에 실제 줄로 남는다 | 쇼핑몰 (승조) · Watchdog (재범) | 하루 1,440건. 지금은 로컬이라 문제 없지만 배포 뒤에는 `productId = canary` 를 주기적으로 지우거나 쇼핑몰이 카나리 주문을 저장하지 않게 해야 한다 |

## 7. 참고

- 설계 문서: https://github.com/2026-techeer-project-team-b/monimo-backend/tree/HEAD/docs/design
- Collector → ClickHouse 가 이어져 있는지 로컬에서 보려면 `monimo-backend` 의 `scripts/check-pipeline.sh`
