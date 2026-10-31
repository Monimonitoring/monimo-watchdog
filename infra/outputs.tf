output "function_name" {
  description = "deploy.yml 의 LAMBDA_FUNCTION_NAME 에 넣을 값"
  value       = aws_lambda_function.watchdog.function_name
}

output "schedule_state" {
  description = "DISABLED 면 Watchdog 이 깨어나지 않는다"
  value       = aws_scheduler_schedule.cycle.state
}

output "state_table" {
  description = "지난 주기 결과를 기억하는 DynamoDB 표 (#26). 비우면 다음 주기가 현재 문제를 전부 새로 알린다"
  value       = aws_dynamodb_table.state.name
}
