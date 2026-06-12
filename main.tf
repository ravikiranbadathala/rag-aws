terraform {
  backend "s3" {
    bucket = "southwest-tfstate-ravi-2026"
    key    = "rag-aws/terraform.tfstate"
    region = "us-east-1"
  }
}

provider "aws" {
  region = var.aws_region
}

# ECR repository — stores Docker image
resource "aws_ecr_repository" "rag_repo" {
  name         = "southwest-rag"
  force_delete = true
}

# S3 bucket — stores documents and FAISS index
resource "aws_s3_bucket" "rag_docs" {
  bucket = "southwest-rag-docs-${var.env}-2"
}

# IAM role for Lambda
resource "aws_iam_role" "lambda_role" {
  name = "rag-lambda-role-2"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_s3" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess"
}

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchLogsFullAccess"
}

# Allow Lambda to push custom CloudWatch metrics (AIOps)
resource "aws_iam_role_policy_attachment" "lambda_cloudwatch_metrics" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchFullAccess"
}

# Lambda function — deployed as Docker container image
resource "aws_lambda_function" "rag_handler" {
  function_name = "southwest-rag"
  role          = aws_iam_role.lambda_role.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.rag_repo.repository_url}:latest"
  timeout       = 60
  memory_size   = 1024

  environment {
    variables = {
      S3_BUCKET      = aws_s3_bucket.rag_docs.bucket
      OPENAI_API_KEY = var.openai_api_key
    }
  }
}

# API Gateway
resource "aws_apigatewayv2_api" "rag_api" {
  name          = "rag-api"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "lambda_integration" {
  api_id           = aws_apigatewayv2_api.rag_api.id
  integration_type = "AWS_PROXY"
  integration_uri  = aws_lambda_function.rag_handler.invoke_arn
}

resource "aws_apigatewayv2_route" "query_route" {
  api_id    = aws_apigatewayv2_api.rag_api.id
  route_key = "POST /query"
  target    = "integrations/${aws_apigatewayv2_integration.lambda_integration.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.rag_api.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.rag_handler.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.rag_api.execution_arn}/*/*"
}

# ───────────────────────────────────────────────
# AIOps — CloudWatch Dashboard
# ───────────────────────────────────────────────
resource "aws_cloudwatch_dashboard" "rag_dashboard" {
  dashboard_name = "southwest-rag-dashboard"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["SouthwestRAG", "RequestLatency", "Mode", "naive"],
            ["SouthwestRAG", "RequestLatency", "Mode", "multi_query"],
            ["SouthwestRAG", "RequestLatency", "Mode", "rerank"],
            ["SouthwestRAG", "RequestLatency", "Mode", "hyde"]
          ]
          period = 60
          stat   = "Average"
          region = "us-east-1"
          title  = "RAG Latency by Mode (ms)"
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["SouthwestRAG", "RequestSuccess"],
            ["SouthwestRAG", "RequestError"]
          ]
          period = 60
          stat   = "Sum"
          region = "us-east-1"
          title  = "Success vs Error Count"
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["SouthwestRAG", "EstimatedCostUSD", "Mode", "naive"],
            ["SouthwestRAG", "EstimatedCostUSD", "Mode", "multi_query"],
            ["SouthwestRAG", "EstimatedCostUSD", "Mode", "rerank"],
            ["SouthwestRAG", "EstimatedCostUSD", "Mode", "hyde"]
          ]
          period = 60
          stat   = "Sum"
          region = "us-east-1"
          title  = "Estimated Cost by Mode (USD)"
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 6
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["SouthwestRAG", "LLMCallsPerRequest", "Mode", "naive"],
            ["SouthwestRAG", "LLMCallsPerRequest", "Mode", "multi_query"],
            ["SouthwestRAG", "LLMCallsPerRequest", "Mode", "rerank"],
            ["SouthwestRAG", "LLMCallsPerRequest", "Mode", "hyde"]
          ]
          period = 60
          stat   = "Average"
          region = "us-east-1"
          title  = "Avg LLM Calls per Request by Mode"
        }
      }
    ]
  })
}

# ───────────────────────────────────────────────
# AIOps — Alarms
# ───────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "high_error_rate" {
  alarm_name          = "southwest-rag-high-error-rate"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "RequestError"
  namespace           = "SouthwestRAG"
  period              = 300
  statistic           = "Sum"
  threshold           = 3
  alarm_description   = "Triggers if RAG errors exceed 3 in 5 minutes"
  treat_missing_data  = "notBreaching"
}

resource "aws_cloudwatch_metric_alarm" "high_latency" {
  alarm_name          = "southwest-rag-high-latency"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "RequestLatency"
  namespace           = "SouthwestRAG"
  period              = 300
  statistic           = "Average"
  threshold           = 10000
  alarm_description   = "Triggers if average latency exceeds 10 seconds"
  treat_missing_data  = "notBreaching"
}

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  alarm_name          = "southwest-rag-lambda-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 0

  dimensions = {
    FunctionName = aws_lambda_function.rag_handler.function_name
  }

  alarm_description  = "Triggers on any Lambda function error"
  treat_missing_data = "notBreaching"
}

# ───────────────────────────────────────────────
# Outputs
# ───────────────────────────────────────────────

output "api_url" {
  value = aws_apigatewayv2_stage.default.invoke_url
}

output "ecr_repo_url" {
  value = aws_ecr_repository.rag_repo.repository_url
}

output "dashboard_url" {
  value = "https://console.aws.amazon.com/cloudwatch/home?region=us-east-1#dashboards:name=${aws_cloudwatch_dashboard.rag_dashboard.dashboard_name}"
}