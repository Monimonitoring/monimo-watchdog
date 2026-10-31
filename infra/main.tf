# src/ 내용물이 zip 루트에 오도록 묶는다. handler.py 가 `import canary` 로 옆을 부르기 때문
data "archive_file" "source" {
  type        = "zip"
  source_dir  = "${path.module}/../src"
  output_path = "${path.module}/build/watchdog.zip"
  excludes    = ["__pycache__"]
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${var.name_prefix}-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# 지난 주기 결과를 기억하는 표 (#26). 항목 하나(pk = "watchdog")에 JSON 한 칸. 1분에 읽기 1 · 쓰기 1 이라
# 온디맨드로 두어도 월 센트 단위다. 표가 없어지면 Watchdog 은 기억 없이 돌아 매 주기 현재 문제를 다시 알린다
resource "aws_dynamodb_table" "state" {
  name         = "${var.name_prefix}-state"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"

  attribute {
    name = "pk"
    type = "S"
  }
}

# Lambda 가 닿는 AWS 자원은 이 표 하나뿐이다. 로그 쓰기 말고는 다른 권한이 없다
data "aws_iam_policy_document" "lambda_state" {
  statement {
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem"]
    resources = [aws_dynamodb_table.state.arn]
  }
}

resource "aws_iam_role_policy" "lambda_state" {
  name   = "${var.name_prefix}-state"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda_state.json
}

# Lambda 가 알아서 만들게 두면 보관 기간이 무기한이 된다
resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${var.name_prefix}"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "watchdog" {
  function_name = var.name_prefix
  role          = aws_iam_role.lambda.arn
  handler       = "handler.lambda_handler"
  runtime       = "python3.13"
  timeout       = var.lambda_timeout_sec
  memory_size   = 128

  filename         = data.archive_file.source.output_path
  source_code_hash = data.archive_file.source.output_base64sha256

  environment {
    variables = {
      SHOP_ORDER_URL        = var.shop_order_url
      API_SERVER_URL        = var.api_server_url
      MONIMO_INTERNAL_TOKEN = var.monimo_internal_token
      SLACK_WEBHOOK         = var.slack_webhook
      PING_URL              = var.ping_url
      STATE_TABLE           = aws_dynamodb_table.state.name
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.lambda_logs,
    aws_iam_role_policy.lambda_state,
    aws_cloudwatch_log_group.lambda,
  ]
}

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${var.name_prefix}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

data "aws_iam_policy_document" "scheduler_invoke" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.watchdog.arn]
  }
}

resource "aws_iam_role_policy" "scheduler_invoke" {
  name   = "${var.name_prefix}-invoke"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler_invoke.json
}

resource "aws_scheduler_schedule" "cycle" {
  name                = var.name_prefix
  schedule_expression = var.schedule_expression
  state               = var.schedule_enabled ? "ENABLED" : "DISABLED"

  # 흔들림 창을 더 얹지 않는다. 그래도 Scheduler 자체 정밀도가 60초라 주기 간격은 ±59초 흔들린다
  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.watchdog.arn
    role_arn = aws_iam_role.scheduler.arn

    retry_policy {
      # 다음 주기가 60초 뒤에 다시 쏜다. 재시도하면 같은 분에 카나리가 두 번 들어간다
      maximum_retry_attempts = 0
    }
  }
}
