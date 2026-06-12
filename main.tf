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

output "api_url" {
  value = aws_apigatewayv2_stage.default.invoke_url
}

output "ecr_repo_url" {
  value = aws_ecr_repository.rag_repo.repository_url
}