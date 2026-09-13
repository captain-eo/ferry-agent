#!/usr/bin/env bash
set -e

echo "=========================================================="
echo "🚢 Deploying Fauntleroy-Vashon School Ferry Tracker to AWS"
echo "=========================================================="

# Check AWS CLI
if ! command -v aws &> /dev/null; then
    echo "❌ AWS CLI could not be found. Please install and configure aws-cli."
    exit 1
fi

STACK_NAME="vashon-ferry-commute"
REGION="${AWS_REGION:-us-west-2}"

# Prompt or check for GEMINI_API_KEY
if [ -z "$GEMINI_API_KEY" ]; then
    echo "⚠️ GEMINI_API_KEY environment variable not set."
    echo "The Lambda will use the built-in deterministic heuristic engine."
    echo "You can provide your key anytime in the AWS Lambda console."
fi

# Check if AWS SAM CLI is available
if command -v sam &> /dev/null; then
    echo "📦 Building and deploying with AWS SAM..."
    sam build -t infra/template.yaml
    sam deploy \
        --stack-name "$STACK_NAME" \
        --region "$REGION" \
        --capabilities CAPABILITY_IAM \
        --parameter-overrides "GeminiApiKey=$GEMINI_API_KEY" \
        --resolve-s3
else
    echo "ℹ️ SAM CLI not detected. Using AWS CloudFormation package & deploy..."
    TMP_BUCKET="${DEPLOY_S3_BUCKET:-vashon-deploy-artifacts-$REGION}"
    
    # Create deploy package
    echo "📦 Packaging dependencies into lambda_package.zip..."
    rm -rf /tmp/lambda_build && mkdir -p /tmp/lambda_build
    pip install -r requirements.txt -t /tmp/lambda_build --quiet
    cp -r src /tmp/lambda_build/
    (cd /tmp/lambda_build && zip -r -q "$OLDPWD/infra/lambda_package.zip" .)

    echo "🚀 Deploying CloudFormation Stack..."
    aws cloudformation deploy \
        --template-file infra/template.yaml \
        --stack-name "$STACK_NAME" \
        --region "$REGION" \
        --capabilities CAPABILITY_IAM \
        --parameter-overrides "GeminiApiKey=$GEMINI_API_KEY"
fi

echo "🎉 Deployment finished!"
aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --query "Stacks[0].Outputs" \
    --output table
