# 계정 · 리전 · 주소 · 비밀값에는 기본값을 두지 않는다. 잘못된 곳에 조용히 올라가지 않도록

variable "aws_region" {
  description = "Watchdog 을 올릴 리전. 스택과 같은 서울(ap-northeast-2) 권장 — 계정 · 결제 문제는 리전이 달라도 같이 겪는다"
  type        = string
}

variable "name_prefix" {
  description = "Lambda · 역할 · 일정 이름의 앞머리"
  type        = string
  default     = "monimo-watchdog"
}

variable "schedule_enabled" {
  description = "일정을 켤지. 조회 문이 들어오기 전에 켜면 1분마다 오탐 Slack 이 하루 1,440번 간다"
  type        = bool
  default     = false
}

variable "schedule_expression" {
  # rate 의 단위는 값이 1 이어도 복수형이다 — 문서상 유효 입력이 minutes | hours | days 뿐이다
  description = "EventBridge Scheduler 주기"
  type        = string
  default     = "rate(1 minutes)"
}

variable "lambda_timeout_sec" {
  # HTTP 타임아웃 합(10+10+5+5=30)보다 커야 한다. urllib 의 timeout 은 총 시간이 아니라 작업 하나당이라
  # 합이 상한을 보장하지 않는다. 여기서 잘리면 finally 의 핑을 못 보내 Healthchecks 가 죽음으로 오진한다
  description = "한 주기는 2~3초면 끝난다. 최악의 경우에도 핑까지 마치도록 여유를 둔 값"
  type        = number
  default     = 45
}

variable "log_retention_days" {
  description = "CloudWatch 로그 보관. 0 이면 무기한이라 명시한다"
  type        = number
  default     = 14
}

variable "shop_order_url" {
  description = "카나리가 traceparent 를 붙여 호출할 쇼핑몰 주문 API"
  type        = string
}

variable "api_server_url" {
  description = "카나리 신선도를 조회할 API server"
  type        = string
}

variable "monimo_internal_token" {
  description = "내부 문 출입증. API server 의 같은 이름 값과 맞춘다"
  type        = string
  sensitive   = true
}

variable "slack_webhook" {
  description = "Watchdog 전용 슬랙 웹훅. 관제 스택(notifier)과 다른 채널"
  type        = string
  sensitive   = true
}

variable "ping_url" {
  description = "Healthchecks.io 핑 주소 (Dead Man's Switch)"
  type        = string
  sensitive   = true
}
