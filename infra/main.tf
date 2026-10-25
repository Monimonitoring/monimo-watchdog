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

# Watchdog 은 AWS 자원을 읽지 않는다. 로그 쓰기 말고는 권한이 없다
resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
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
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.lambda_logs,
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
