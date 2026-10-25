output "function_name" {
  description = "deploy.yml 의 LAMBDA_FUNCTION_NAME 에 넣을 값"
  value       = aws_lambda_function.watchdog.function_name
}

output "schedule_state" {
  description = "DISABLED 면 Watchdog 이 깨어나지 않는다"
  value       = aws_scheduler_schedule.cycle.state
}
