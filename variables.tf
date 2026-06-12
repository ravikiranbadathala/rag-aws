variable "aws_region" {
  default = "us-east-1"
}

variable "env" {
  default = "dev"
}

variable "openai_api_key" {
  description = "OpenAI API Key"
  type        = string
  sensitive   = true
}