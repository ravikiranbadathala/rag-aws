provider "aws" {
  region = var.aws_region
}

resource "aws_s3_bucket" "rag_docs" {
  bucket = "southwest-rag-docs-${var.env}"
}

resource "aws_iam_role" "lambda_role" {
  name = "rag-lambda-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_bedrock" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonBedrockFullAccess"
}

resource "aws_lambda_function" "rag_handler" {
  filename         = "lambda.zip"
  function_name    = "southwest-rag"
  role             = aws_iam_role.lambda_role.arn
  handler          = "lambda_function.handler"
  runtime          = "python3.11"
  timeout          = 60
  memory_size      = 512

  environment {
    variables = {
      OPENSEARCH_ENDPOINT = aws_opensearchserverless_collection.rag_vs.collection_endpoint
      S3_BUCKET           = aws_s3_bucket.rag_docs.bucket
    }
  }
}

resource "aws_apigatewayv2_api" "rag_api" {
  name          = "rag-api"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "lambda_integration" {
  api_id             = aws_apigatewayv2_api.rag_api.id
  integration_type   = "AWS_PROXY"
  integration_uri    = aws_lambda_function.rag_handler.invoke_arn
}

resource "aws_apigatewayv2_route" "query_route" {
  api_id    = aws_apigatewayv2_api.rag_api.id
  route_key = "POST /query"
  target    = "integrations/${aws_apigatewayv2_integration.lambda_integration.id}"
}

resource "aws_opensearchserverless_collection" "rag_vs" {
  name = "southwest-vectors"
  type = "VECTORSEARCH"
}

output "api_url" {
  value = aws_apigatewayv2_api.rag_api.api_endpoint
}
