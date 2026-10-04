"""
AWS Lambda Handler for Fauntleroy-Vashon School Ferry Tracker.
Executes scheduled pipeline on EventBridge trigger (every 15 minutes):
WSDOT Ingestion -> Gemini Flash / Heuristic Analysis -> S3 Static Deployment.

Features:
1. Fast-Path HTTP: Serves on-demand AIS boat telemetry for browser maps via Lambda Function URL (<150ms).
2. Scheduled Pipeline: Executes directly every time EventBridge cron triggers it (every 15 minutes).
3. AI Throttling: Reuses cached Gemini advisory across invocations unless bulletins or schedules change.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Tuple
from zoneinfo import ZoneInfo

# Ensure project root is in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import boto3
from src.config import S3_BUCKET_NAME, VESSELS_API_URL
from src.collectors.wsdot_api import get_merged_ferry_telemetry, fetch_enriched_triangle_vessels
from src.collectors.bulletin_scraper import scrape_bulletins
from src.analyzer.gemini_analyzer import analyze_commute_with_gemini
from src.generator.site_generator import generate_static_site

LOCAL_TMP_DIR = "/tmp/vashon_ferry_dist"
PACIFIC_TZ = ZoneInfo("America/Los_Angeles")


def is_http_request(event: Dict[str, Any]) -> bool:
    """Checks whether the event originates from an HTTP request (Lambda Function URL or API Gateway)."""
    if not isinstance(event, dict):
        return False
    if "requestContext" in event and ("http" in event["requestContext"] or "httpMethod" in event.get("requestContext", {})):
        return True
    if "rawPath" in event or "httpMethod" in event:
        return True
    return False


def handle_http_telemetry_request(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handles fast on-demand HTTP requests from Lambda Function URL for real-time AIS map polling.
    Bypasses AI analysis, scraping, and S3 uploads to return live boat telemetry in <150ms.
    """
    http_ctx = event.get("requestContext", {}).get("http", {})
    method = http_ctx.get("method") or event.get("httpMethod", "GET")
    method = (method or "GET").upper()

    # AWS Lambda Function URL automatically injects Access-Control-* headers
    # based on its infrastructure CORS configuration. Application code must not
    # return duplicate Access-Control-* headers to prevent multiple-value CORS browser errors.
    response_headers = {
        "Content-Type": "application/json",
        "Cache-Control": "max-age=5, public"
    }

    if method == "OPTIONS":
        return {
            "statusCode": 204,
            "headers": response_headers,
            "body": ""
        }

    raw_path = event.get("rawPath") or event.get("path", "")
    print(f"[HTTP-API] Received {method} request for '{raw_path}'")
    start_time = time.time()

    try:
        vessels = fetch_enriched_triangle_vessels()
        now_pacific = datetime.now(PACIFIC_TZ)
        payload = {
            "timestamp": now_pacific.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "timestamp_epoch": time.time(),
            "vessels_telemetry": vessels,
            "vessels": vessels,
            "count": len(vessels),
            "latency_ms": round((time.time() - start_time) * 1000, 1)
        }
        return {
            "statusCode": 200,
            "headers": response_headers,
            "body": json.dumps(payload)
        }
    except Exception as e:
        print(f"[ERROR] Failed serving HTTP vessel telemetry: {e}")
        return {
            "statusCode": 500,
            "headers": response_headers,
            "body": json.dumps({"error": f"Failed fetching vessel telemetry: {str(e)}"})
        }


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main AWS Lambda entrypoint invoked by EventBridge cron, Lambda Function URL, or manual test.
    Executes directly every time EventBridge triggers it.
    """
    # 0. Fast-path: On-demand HTTP AIS polling for the JavaScript map
    if is_http_request(event):
        return handle_http_telemetry_request(event)

    print("[INFO] Lambda scheduled pipeline execution started.")
    now_pacific = datetime.now(PACIFIC_TZ)
    print(f"[SCHEDULE] Execution Time: {now_pacific.strftime('%A %I:%M %p %Z')}")
    bucket_name = os.getenv("S3_BUCKET_NAME", S3_BUCKET_NAME)
    s3 = boto3.client("s3")

    # 1. Try to fetch previous AI state from S3 to avoid cold-start LLM invocations
    cached_ai_state = None
    if bucket_name and bucket_name != "vashon-ferry-commute":
        try:
            s3_obj = s3.get_object(Bucket=bucket_name, Key="data.json")
            prev_data = json.loads(s3_obj["Body"].read().decode("utf-8"))
            if "state_fingerprint" in prev_data:
                cached_ai_state = {
                    "fingerprint": prev_data.get("state_fingerprint"),
                    "executive_briefing": prev_data.get("executive_briefing"),
                    "ai_insights": prev_data.get("ai_insights", []),
                    "ai_parent_action_items": prev_data.get("ai_parent_action_items", []),
                    "ai_model": prev_data.get("ai_model"),
                    "timestamp_epoch": prev_data.get("timestamp_epoch", 0),
                }
        except Exception:
            pass

    # 2. Ingest telemetry & bulletins
    print("[INFO] Ingesting WSDOT telemetry and bulletins...")
    telemetry = get_merged_ferry_telemetry()
    bulletins = scrape_bulletins()
    
    # 3. Run Gemini Flash analysis (with state fingerprinting & caching)
    force_ai = event.get("force_ai", False)
    analysis = analyze_commute_with_gemini(
        telemetry=telemetry,
        bulletins_data=bulletins,
        cached_ai_state=cached_ai_state,
        force_ai_refresh=force_ai
    )
    analysis["timestamp_epoch"] = time.time()
    
    # 4. Generate static site files
    print(f"[INFO] Rendering static site into {LOCAL_TMP_DIR}...")
    files = generate_static_site(analysis, LOCAL_TMP_DIR)
    
    # 6. Upload to S3 if configured
    uploaded = False
    s3_details = {}
    if bucket_name and bucket_name != "vashon-ferry-commute":
        try:
            print(f"[INFO] Uploading static assets to S3 bucket: {bucket_name}...")
            
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

            # Upload favicon.svg
            if "favicon_svg" in files and Path(files["favicon_svg"]).exists():
                with open(files["favicon_svg"], "rb") as f:
                    s3.put_object(
                        Bucket=bucket_name,
                        Key="favicon.svg",
                        Body=f.read(),
                        ContentType="image/svg+xml",
                        CacheControl="max-age=86400, public",
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
        print("[INFO] S3_BUCKET_NAME not configured for upload; skipping remote S3 upload.")
        
    return {
        "statusCode": 200,
        "body": json.dumps({
            "message": "Ferry commute update completed successfully.",
            "schedule_mode": analysis.get("schedule_mode"),
            "service_status": analysis.get("service_status"),
            "cancelled_vessels": analysis.get("cancelled_vessels"),
            "ai_provider": analysis.get("ai_provider"),
            "ai_cached": analysis.get("ai_cached", False),
            "s3_uploaded": uploaded,
            "s3_details": s3_details,
            "timestamp": analysis.get("timestamp"),
        })
    }


if __name__ == "__main__":
    # Allows testing lambda handler locally
    res = lambda_handler({"force": True}, None)
    print(json.dumps(res, indent=2))
