#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=========================================================="
echo "🚢 Fauntleroy-Vashon School Ferry Commute Tracker Deployment"
echo "=========================================================="

STACK_NAME="${STACK_NAME:-vashon-ferry-commute}"
REGION="${AWS_REGION:-$(aws configure get region 2>/dev/null || echo "us-west-2")}"
PACKAGE_ZIP="$SCRIPT_DIR/lambda_package.zip"

# Step 1: Package Lambda Zip
echo "📦 Step 1: Packaging Lambda deployment bundle..."
BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT

echo "   Installing pure-Python dependencies (beautifulsoup4, soupsieve)..."
if ! python3 -m pip install beautifulsoup4 soupsieve -t "$BUILD_DIR" --quiet 2>/dev/null; then
    echo "   (Pip network install unavailable; copying from local Python environment...)"
    python3 -c "import shutil, bs4, soupsieve; from pathlib import Path; shutil.copytree(Path(bs4.__file__).parent, Path('$BUILD_DIR/bs4'), dirs_exist_ok=True); shutil.copytree(Path(soupsieve.__file__).parent, Path('$BUILD_DIR/soupsieve'), dirs_exist_ok=True)"
fi

echo "   Copying src/ application code and HTML template..."
cp -r "$ROOT_DIR/src" "$BUILD_DIR/"
find "$BUILD_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

echo "   Creating $PACKAGE_ZIP..."
rm -f "$PACKAGE_ZIP"
(cd "$BUILD_DIR" && zip -r -q "$PACKAGE_ZIP" .)
ZIP_SIZE=$(du -h "$PACKAGE_ZIP" | cut -f1)
echo "   ✅ Bundle created successfully ($ZIP_SIZE) -> $PACKAGE_ZIP"

FUNCTION_NAME="${FUNCTION_NAME:-vashon-ferry-tracker}"
DEPLOY_MODE="code"

if [ "$1" == "--package-only" ]; then
    echo "Done! (--package-only flag detected)"
    exit 0
elif [ "$1" == "--full" ] || [ "$1" == "--infra" ]; then
    DEPLOY_MODE="full"
elif [ "$1" == "--code-only" ] || [ "$1" == "--zip-only" ] || [ "$1" == "--update-code" ]; then
    DEPLOY_MODE="code"
elif [ -n "$1" ]; then
    echo "Usage:"
    echo "  ./infra/deploy.sh               # Fast code deployment: packages & replaces Lambda zip (no IAM needed)"
    echo "  ./infra/deploy.sh --code-only   # Explicit code-only deployment (same as default)"
    echo "  ./infra/deploy.sh --package-only # Only build infra/lambda_package.zip locally"
    echo "  ./infra/deploy.sh --full        # Full CloudFormation stack deployment (requires IAM permissions)"
    exit 1
fi

# Step 2: Check AWS CLI
if ! command -v aws &> /dev/null; then
    echo "❌ AWS CLI could not be found. Please install and configure aws-cli."
    exit 1
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query "Account" --output text 2>/dev/null || echo "")
if [ -z "$ACCOUNT_ID" ]; then
    echo "❌ Unable to authenticate with AWS. Run 'aws configure' or check credentials."
    exit 1
fi
echo "🔑 Authenticated as AWS Account: $ACCOUNT_ID (Region: $REGION)"

# Check Gemini API Key
if [ -z "$GEMINI_API_KEY" ]; then
    echo "ℹ️  GEMINI_API_KEY environment variable is not set."
    echo "   Operating with built-in deterministic heuristic engine."
fi

# Step 3: Deploy
if [ "$DEPLOY_MODE" == "code" ]; then
    echo "⚡ Deploying code update directly to Lambda ($FUNCTION_NAME)..."
    echo "   (No IAM or CloudFormation permissions needed)"
    
    if ! aws lambda get-function --function-name "$FUNCTION_NAME" --region "$REGION" >/dev/null 2>&1; then
        echo "❌ Lambda function '$FUNCTION_NAME' not found in $REGION."
        echo "   If this is initial setup, run './infra/deploy.sh --full' to create the stack,"
        echo "   or upload '$PACKAGE_ZIP' manually in the AWS Lambda console."
        exit 1
    fi

    echo "⏱️  Ensuring EventBridge cron schedule is rate(15 minutes)..."
    aws events put-rule \
        --name "vashon-ferry-tracker-cron" \
        --schedule-expression "rate(15 minutes)" \
        --region "$REGION" >/dev/null 2>&1 || true

    aws lambda update-function-code \
        --function-name "$FUNCTION_NAME" \
        --zip-file "fileb://$PACKAGE_ZIP" \
        --region "$REGION" > /dev/null

    echo "⏳ Waiting for Lambda code update to complete..."
    aws lambda wait function-updated --function-name "$FUNCTION_NAME" --region "$REGION"

    echo "🔄 Triggering run to refresh static site..."
    aws lambda invoke \
        --function-name "$FUNCTION_NAME" \
        --region "$REGION" \
        --log-type Tail \
        /tmp/vashon_lambda_test_output.json > /dev/null 2>&1 || true

    echo "=========================================================="
    echo "🎉 CODE DEPLOYMENT COMPLETE!"
    echo "   Lambda function '$FUNCTION_NAME' updated with new zip ($ZIP_SIZE)."
    echo "=========================================================="
    exit 0

elif [ "$DEPLOY_MODE" == "full" ]; then
    echo "🚀 Deploying full CloudFormation infrastructure stack ($STACK_NAME)..."
    echo "   (Requires IAM permissions to create/update execution role)"
    aws cloudformation deploy \
        --template-file "$SCRIPT_DIR/template.yaml" \
        --stack-name "$STACK_NAME" \
        --region "$REGION" \
        --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
        --parameter-overrides "GeminiApiKey=${GEMINI_API_KEY:-}"

    echo "⚡ Updating Lambda function code with application package..."
    aws lambda update-function-code \
        --function-name "$FUNCTION_NAME" \
        --zip-file "fileb://$PACKAGE_ZIP" \
        --region "$REGION" > /dev/null

    echo "⏳ Waiting for function code update to complete..."
    aws lambda wait function-updated --function-name "$FUNCTION_NAME" --region "$REGION"

    echo "🔄 Triggering initial run to populate static site..."
    aws lambda invoke \
        --function-name "$FUNCTION_NAME" \
        --region "$REGION" \
        --log-type Tail \
        /tmp/vashon_lambda_test_output.json > /dev/null 2>&1 || true

    echo "=========================================================="
    echo "🎉 FULL DEPLOYMENT COMPLETE!"
    echo "=========================================================="
    aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --region "$REGION" \
        --query "Stacks[0].Outputs" \
        --output table
fi
