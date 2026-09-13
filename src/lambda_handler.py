"""
AWS Lambda Handler for Fauntleroy-Vashon School Ferry Tracker.
Executes periodic pipeline: WSDOT Ingestion -> Gemini Flash Analysis -> S3 Static Deployment.
"""

import json
import os
import sys
from pathlib import Path
from typing import Dict, Any

# Ensure project root is in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import boto3
from src.config import S3_BUCKET_NAME
from src.collectors.wsdot_api import get_merged_ferry_telemetry
from src.collectors.bulletin_scraper import scrape_bulletins
from src.analyzer.gemini_analyzer import analyze_commute_with_gemini
from src.generator.site_generator import generate_static_site

# In AWS Lambda, /tmp is writable
LOCAL_TMP_DIR = "/tmp/vashon_ferry_dist"


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main AWS Lambda entrypoint invoked by EventBridge cron or manual test.
    """
    print("[INFO] Lambda execution started.")
    bucket_name = os.getenv("S3_BUCKET_NAME", S3_BUCKET_NAME)
    
    # 1. Ingest telemetry & bulletins
    print("[INFO] Ingesting WSDOT telemetry and bulletins...")
    telemetry = get_merged_ferry_telemetry()
    bulletins = scrape_bulletins()
    
    # 2. Run Gemini Flash analysis
    print("[INFO] Running AI investigation...")
    analysis = analyze_commute_with_gemini(telemetry, bulletins)
    
    # 3. Generate static site
    print(f"[INFO] Rendering static site into {LOCAL_TMP_DIR}...")
    files = generate_static_site(analysis, LOCAL_TMP_DIR)
    
    # 4. Upload to S3 if configured
    uploaded = False
    s3_details = {}
    if bucket_name and bucket_name != "vashon-ferry-commute":
        try:
            print(f"[INFO] Uploading static assets to S3 bucket: {bucket_name}...")
            s3 = boto3.client("s3")
            
            # Upload index.html
            with open(files["index_html"], "rb") as f:
                s3.put_object(
                    Bucket=bucket_name,
                    Key="index.html",
                    Body=f.read(),
                    ContentType="text/html",
                    CacheControl="max-age=60, public",
                )
                
            # Upload data.json
            with open(files["data_json"], "rb") as f:
                s3.put_object(
                    Bucket=bucket_name,
                    Key="data.json",
                    Body=f.read(),
                    ContentType="application/json",
                    CacheControl="no-cache, no-store, must-revalidate",
                )
                
            uploaded = True
            s3_details = {
                "bucket": bucket_name,
                "url": f"http://{bucket_name}.s3-website-{os.getenv('AWS_REGION', 'us-west-2')}.amazonaws.com"
            }
            print(f"[SUCCESS] Uploaded to S3 successfully: {s3_details['url']}")
        except Exception as e:
            print(f"[ERROR] S3 upload failed: {e}")
            s3_details = {"error": str(e)}
    else:
        print("[INFO] S3_BUCKET_NAME not set or is default; skipping remote S3 upload.")
        
    return {
        "statusCode": 200,
        "body": json.dumps({
            "message": "Ferry commute update completed successfully.",
            "schedule_mode": analysis.get("schedule_mode"),
            "is_friday_pdd": analysis.get("is_friday_pdd"),
            "ai_provider": analysis.get("ai_provider"),
            "s3_uploaded": uploaded,
            "s3_details": s3_details,
            "timestamp": analysis.get("timestamp"),
        })
    }


if __name__ == "__main__":
    # Allows testing lambda handler locally
    res = lambda_handler({}, None)
    print(json.dumps(res, indent=2))
