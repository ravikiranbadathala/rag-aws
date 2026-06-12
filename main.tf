provider "aws" {
  region = var.aws_region
}

# S3 bucket — stores documents, FAISS index, and lambda zip
resource "aws_s3_bucket" "rag_docs" {
  bucket = "southwest-rag-docs-${var.env}-2"
}

# Upload lambda.zip to S3 (avoids 70MB direct upload limit)
resource "aws_s3_object" "lambda_zip" {
  bucket = aws_s3_bucket.rag_docs.id
  key    = "lambda.zip"
  source = "lambda.zip"
  etag   = filemd5("lambda.zip")
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

# Lambda function — deployed via S3
resource "aws_lambda_function" "rag_handler" {
  function_name    = "southwest-rag"
  role             = aws_iam_role.lambda_role.arn
  handler          = "lambda_function.handler"
  runtime          = "python3.11"
  timeout          = 60
  memory_size      = 512

  s3_bucket        = aws_s3_bucket.rag_docs.id
  s3_key           = aws_s3_object.lambda_zip.key
  source_code_hash = filebase64sha256("lambda.zip")

  environment {
    variables = {
      S3_BUCKET      = aws_s3_bucket.rag_docs.bucket
      OPENAI_API_KEY = var.openai_api_key
    }
  }

  depends_on = [aws_s3_object.lambda_zip]
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