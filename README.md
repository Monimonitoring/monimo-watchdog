# monimo-watchdog

Watchdog: 우리 스택 바깥에서 카나리 데이터로 파이프라인이 살아 있는지 확인하는 Lambda

- 기술: Python · Terraform · AWS Lambda · EventBridge Scheduler
- 상태: 카나리 한 주기 + 배포 정의 완료. **일정은 꺼 둔 상태**

## 폴더 구성

| 폴더 | 하는 일 |
|---|---|
| `src/handler.py` | 한 주기를 조립한다. 바깥과는 아래 둘을 통해서만 말한다 |
| `src/canary.py` | `traceparent` 생성 · 쇼핑몰 주문 호출 · 신선도 조회 |
| `src/notify.py` | Slack 웹훅 · Healthchecks.io 핑 |
| `tests/` | pytest. 네트워크를 타지 않는다 |
| `infra/` | Terraform: Lambda · EventBridge Scheduler · IAM · 로그 그룹 |

표준 라이브러리만 쓴다. `src/` 내용물을 zip 으로 묶어 올리는 것이 배포의 전부다.

## 한 주기

1. `traceparent`(`00-<32hex>-<16hex>-01`) + `tracestate: monimon=canary` 를 붙여 쇼핑몰 주문 API 에 가짜 주문 1건
2. 바로 `GET /api/v1/internal/canary/freshness?service_name=shop-order` (`X-Internal-Token`)
3. `fresh` 가 false 면 전용 Slack 웹훅
4. `GET /api/v1/internal/self-check` (`X-Internal-Token`, backend #226 · #267) — PG 두드림 · Detector · Collector · Notifier 낡음 게이지 · 발송 큐 적체. `ok` 가 false 인 항목마다 Slack 한 줄 (한 메시지)
5. 무엇으로 끝났든 Healthchecks.io 핑

**자기 점검은 카나리와 따로 본다.** 카나리는 "신호가 끝까지 흐르나", 자기 점검은 "PG · Detector · Collector · Notifier 가 멈췄나, 경보가 Slack 으로 나가나" 다. backend #208 ⑥ 실측에서 PG 를 멈추자 Detector · Notifier 가 PG 에 막혀 경보가 0건이었고, probe · 카나리도 PG 를 안 봐서 밖에서 보는 눈이 Watchdog 뿐이었다. Notifier 는 둘로 본다 (backend #267) : 게이지는 "큐 읽기가 멈췄나"(죽음 · PG 막힘), 발송 큐 적체는 "읽는데 안 나가나"(웹훅 틀림 · 5xx · 서킷 OPEN) — 게이지만으로는 뒤의 것을 못 잡는다. 응답에 없는 항목은 건너뛴다. 결과의 `self_check` 는 `OK` · `PROBLEM` · `UNREACHABLE`.

**기다리지 않는다.** 2번에서 보이는 것은 방금 쏜 카나리가 아니라 직전 주기(60초 전)에 쏜 것이다. 내 것이 도착할 때까지 기다리면 Lambda 가 30~60초를 붙잡고 있어야 해서 1분 주기에 상주 서버와 다를 게 없어진다.

**판정하지 않는다.** `fresh` 는 API server 가 낸다. 기준값(`MONIMO_CANARY_THRESHOLD_SEC`, 180초)을 조일 때 Watchdog 을 다시 배포하지 않으려고.

## 로컬 실행

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest                                  # 네트워크 없이

cp .env.example .env                    # 값을 채운 뒤
set -a && . ./.env && set +a
PYTHONPATH=src python -c 'from handler import Config, run; print(run(Config.from_env()))'
```

## 환경변수

실제 값은 레포에 올리지 않는다. `.env.example` 에 이름만 적는다.

| 이름 | 설명 |
|---|---|
| `SHOP_ORDER_URL` | 카나리가 호출할 쇼핑몰 주문 API. 게이트웨이(8090)를 거친다 (shop `#26`). 상품은 재고 표에 있는 `P-100` |
| `API_SERVER_URL` | 카나리 신선도를 조회할 API server |
| `PING_URL` | Healthchecks.io 핑 주소 (Dead Man's Switch) |
| `MONIMO_INTERNAL_TOKEN` | 내부 문 출입증. API server 의 같은 이름 값과 맞춘다 |
| `SLACK_WEBHOOK` | Watchdog 전용 슬랙 웹훅 (관제 스택과 별도 채널) |

운영(Lambda)에서는 `infra/` 의 Terraform 이 같은 이름으로 넣는다.

## 배포

스택 안에 두면 스택이 죽을 때 감시자도 같이 죽는다. 그래서 AWS Lambda 다. 영구 액세스 키를 레포에 두지 않으려고 GitHub Actions **OIDC** 로 역할을 빌린다.

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars      # 값을 채운다
terraform init -backend-config=backend.hcl        # 버킷 · 키 · 리전
terraform plan
```

**일정은 꺼진 채로 만들어진다**(`schedule_enabled = false`). 조회 문 `GET /api/v1/internal/canary/freshness` 는 들어왔다(backend `#160`). AWS 계정 · 결제가 준비되면(10/17) `terraform apply` 뒤 `true` 로 바꾸고, 주문 201 과 `age_sec` 로그를 며칠 모아 기준 180초를 확인한다.

`main` 에 `src/` 가 바뀌어 올라가면 `deploy.yml` 이 zip 을 다시 올린다. 인프라 자체(일정 · IAM · 환경변수)는 Terraform 으로만 바꾼다.

### 배포 파트와 맞출 것

Terraform 에 기본값을 두지 않았다. 아래가 정해져야 `terraform plan` 이 돈다.

| 어디에 | 무엇 |
|---|---|
| `terraform init -backend-config` | state 를 둘 S3 버킷 · 키 · 리전 |
| `terraform.tfvars` | `aws_region`(서울 `ap-northeast-2`, 스택과 같은 리전 · backend ADR `#62`) · `shop_order_url` · `api_server_url` |
| `terraform.tfvars` 또는 `TF_VAR_*` | `monimo_internal_token`(API server 와 같은 값) · `slack_webhook` · `ping_url` |
| 레포 Secret `AWS_DEPLOY_ROLE_ARN` | GitHub OIDC 를 신뢰하는 배포 역할 |
| 레포 Variable `AWS_REGION` · `LAMBDA_FUNCTION_NAME` | `terraform output` 으로 나온다. **이 둘이 비어 있으면 `deploy.yml` 이 통째로 건너뛴다** |

## 관련 문서

- [설계 문서 (결정 기록 원본)](https://github.com/2026-techeer-project-team-b/monimo-backend/tree/main/docs/design): monimo-backend 레포의 `docs/design/`
- [레포별 파일 구성](https://app.notion.com/p/3e1d7d6851ff80a8a110e8aea0b5783b)
- [깃허브 레포지토리 규칙](https://app.notion.com/p/3dcd7d6851ff8000b795f1cc609124e6)

## 기여 규칙

- 브랜치 전략: 기능 브랜치 → `develop`(기본 브랜치, 작업을 모으는 곳) → 배포 단위로 `develop` → `main`
- `main` · `develop` 직접 push 금지, PR로만 머지. PR 의 base 는 기본값(`develop`) 그대로 두면 된다
- 브랜치: `feat/<이슈번호>-<설명>` · `fix/<이슈번호>-<설명>` · `chore/<설명>`
- 커밋: `<타입>: <요약> (#이슈)` 한 줄 (타입: feat · fix · docs · chore · refactor · test · ci). 이 레포는 모듈이 하나라 범위를 붙이지 않는다

## AI 와 일한 방법 (승조가 맡은 부분)

Watchdog 담당은 재범(`@jaebeom79`)이다. 승조(`@SeungJo-02`)가 맡은 초기 구성 · PR 라벨 · CODEOWNERS · develop 전략 · `AGENTS.md`(`#1` `#2` `#3` `#9` `#12`)는 AI(Claude Code)와 함께 만들었다. 절차 · 역할 분담 · 토큰 기준값 · AI 가 틀린 것과 잡은 방법은 `monimo-backend` 의 [`docs/seungjo/harness.md`](https://github.com/2026-techeer-project-team-b/monimo-backend/blob/HEAD/docs/seungjo/harness.md)(정본), 이 레포에서 무엇을 어떻게 물었는지는 [`docs/prompts/`](docs/prompts/README.md) 에 있다. 재범의 작업 방식은 여기서 말하지 않는다.
