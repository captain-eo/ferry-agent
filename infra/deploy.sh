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
python3 -m pip install beautifulsoup4 soupsieve -t "$BUILD_DIR" --quiet

echo "   Copying src/ application code and HTML template..."
cp -r "$ROOT_DIR/src" "$BUILD_DIR/"
find "$BUILD_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

echo "   Creating $PACKAGE_ZIP..."
rm -f "$PACKAGE_ZIP"
(cd "$BUILD_DIR" && zip -r -q "$PACKAGE_ZIP" .)
ZIP_SIZE=$(du -h "$PACKAGE_ZIP" | cut -f1)
echo "   ✅ Bundle created successfully ($ZIP_SIZE) -> $PACKAGE_ZIP"

if [ "$1" == "--package-only" ]; then
    echo "Done! (--package-only flag detected)"
    exit 0
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
    echo "⚠️  GEMINI_API_KEY environment variable is not set."
    echo "   Lambda will operate using the built-in deterministic heuristic fallback."
    echo "   You can add your key anytime in AWS Lambda -> Configuration -> Environment variables."
fi

# Step 3: Test CloudFormation / Lambda permissions
echo "🔍 Checking AWS permissions..."
CAN_DEPLOY_CFN=true
if ! aws cloudformation describe-stacks --region "$REGION" >/dev/null 2>&1; then
    CAN_DEPLOY_CFN=false
fi

if [ "$CAN_DEPLOY_CFN" = true ]; then
    echo "🚀 Step 3: Deploying CloudFormation infrastructure stack ($STACK_NAME)..."
    aws cloudformation deploy \
        --template-file "$SCRIPT_DIR/template.yaml" \
        --stack-name "$STACK_NAME" \
        --region "$REGION" \
        --capabilities CAPABILITY_IAM \
        --parameter-overrides "GeminiApiKey=${GEMINI_API_KEY:-}"

    echo "⚡ Step 4: Updating Lambda function code with application package..."
    aws lambda update-function-code \
        --function-name "vashon-ferry-tracker" \
        --zip-file "fileb://$PACKAGE_ZIP" \
        --region "$REGION" > /dev/null

    echo "⏳ Waiting for function code update to complete..."
    aws lambda wait function-updated --function-name "vashon-ferry-tracker" --region "$REGION"

    echo "🔄 Step 5: Triggering initial run to populate static site..."
    aws lambda invoke \
        --function-name "vashon-ferry-tracker" \
        --region "$REGION" \
        --log-type Tail \
        /tmp/vashon_lambda_test_output.json > /dev/null 2>&1 || true

    echo "=========================================================="
    echo "🎉 DEPLOYMENT COMPLETE!"
    echo "=========================================================="
    aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --region "$REGION" \
        --query "Stacks[0].Outputs" \
        --output table
else
    echo "⚠️  The current AWS credentials do not have CloudFormation/Lambda deploy permissions."
    echo "   (Error: AccessDenied on cloudformation/lambda APIs)."
    echo ""
    echo "👉 You have two simple options to complete deployment:"
    echo ""
    echo "Option A (AWS Console Upload):"
    echo "  1. In CloudFormation Console, create stack using infra/template.yaml."
    echo "  2. In Lambda Console -> 'vashon-ferry-tracker' -> 'Upload from' -> select '$PACKAGE_ZIP'."
    echo ""
    echo "Option B (Attach IAM Permissions to user):"
    echo "  Attach the following managed policies in IAM:"
    echo "  - AWSCloudFormationFullAccess"
    echo "  - AWSLambda_FullAccess"
    echo "  - AmazonEventBridgeFullAccess"
    echo "  - IAMFullAccess (or permissions to create Lambda execution role)"
    echo "  - AmazonS3FullAccess"
    echo "  Then re-run: ./infra/deploy.sh"
fi
