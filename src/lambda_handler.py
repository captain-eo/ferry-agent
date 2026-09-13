"""
AWS Lambda Handler for Fauntleroy-Vashon School Ferry Tracker.
Executes periodic pipeline: WSDOT Ingestion -> Gemini Flash Analysis -> S3 Static Deployment.

Features:
1. Commute-Hours Governor: Runs frequent updates (e.g. every 4m) during school commute windows
   (Mon-Fri 6:00-9:15 AM & 2:30-6:00 PM PT), and throttles to once-an-hour during off-peak times.
2. AI Invocation Throttling: Reuses cached Gemini advisory across invocations unless bulletins,
   schedules, or route operating modes change.
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
from src.config import S3_BUCKET_NAME, COMMUTE_SCHEDULE
from src.collectors.wsdot_api import get_merged_ferry_telemetry
from src.collectors.bulletin_scraper import scrape_bulletins
from src.analyzer.gemini_analyzer import analyze_commute_with_gemini
from src.generator.site_generator import generate_static_site

LOCAL_TMP_DIR = "/tmp/vashon_ferry_dist"
PACIFIC_TZ = ZoneInfo("America/Los_Angeles")


def is_school_commute_window(now_dt: datetime) -> Tuple[bool, str]:
    """
    Checks if current Pacific time is in the morning or afternoon school commute window.
    Morning: Monday-Friday 6:00 AM - 9:15 AM
    Afternoon / Sports: Monday-Friday 2:30 PM - 6:00 PM
    """
    # 0 = Monday, 4 = Friday, 5 = Saturday, 6 = Sunday
    if now_dt.weekday() > 4:
        return False, "Weekend (Off-Peak)"
        
    current_minutes = now_dt.hour * 60 + now_dt.minute
    am_start = COMMUTE_SCHEDULE["am_start"][0] * 60 + COMMUTE_SCHEDULE["am_start"][1] # 06:00
    am_end = COMMUTE_SCHEDULE["am_end"][0] * 60 + COMMUTE_SCHEDULE["am_end"][1]       # 09:15
    pm_start = COMMUTE_SCHEDULE["pm_start"][0] * 60 + COMMUTE_SCHEDULE["pm_start"][1] # 14:30
    pm_end = COMMUTE_SCHEDULE["pm_end"][0] * 60 + COMMUTE_SCHEDULE["pm_end"][1]       # 18:00

    if am_start <= current_minutes <= am_end:
        return True, "Morning Commute (Peak)"
    if pm_start <= current_minutes <= pm_end:
        return True, "Afternoon / Sports Return (Peak)"
        
    return False, "Midday / Evening (Off-Peak)"


def check_offpeak_should_run(s3_client, bucket_name: str, now_dt: datetime) -> Tuple[bool, float]:
    """
    For off-peak times, checks whether we should run the scheduled refresh.
    Default interval is 15 minutes.
    Returns (should_run, minutes_since_last_update).
    """
    if not bucket_name or bucket_name == "vashon-ferry-commute":
        return True, 999.0

    target_interval = COMMUTE_SCHEDULE.get("offpeak_interval_minutes", 15)
    # Allow a 2-minute buffer so e.g. a 4-minute cron fires smoothly around ~13-16 minutes
    threshold_minutes = max(1.0, float(target_interval - 2))

    try:
        head_res = s3_client.head_object(Bucket=bucket_name, Key="data.json")
        last_modified = head_res.get("LastModified")
        if last_modified:
            now_utc = datetime.now(timezone.utc)
            elapsed_minutes = (now_utc - last_modified).total_seconds() / 60.0
            if elapsed_minutes < threshold_minutes:
                return False, elapsed_minutes
            return True, elapsed_minutes
    except Exception:
        # If file doesn't exist yet, proceed with run
        return True, 999.0

    return True, 999.0


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main AWS Lambda entrypoint invoked by EventBridge cron or manual test.
    """
    print("[INFO] Lambda execution started.")
    now_pacific = datetime.now(PACIFIC_TZ)
    bucket_name = os.getenv("S3_BUCKET_NAME", S3_BUCKET_NAME)
    force_run = event.get("force", False) or os.getenv("FORCE_RUN", "false").lower() == "true"
    
    # 1. Evaluate Commute Hours Governor
    is_commute, commute_label = is_school_commute_window(now_pacific)
    print(f"[SCHEDULE] Time: {now_pacific.strftime('%A %I:%M %p %Z')} | Status: {commute_label} (In-Commute: {is_commute})")
    
    s3 = boto3.client("s3")
    
    if not is_commute and not force_run:
        should_run, elapsed_mins = check_offpeak_should_run(s3, bucket_name, now_pacific)
        target_interval = COMMUTE_SCHEDULE.get("offpeak_interval_minutes", 15)
        if not should_run:
            msg = f"Off-peak window; last update was {elapsed_mins:.1f} minutes ago. Throttled to ~{target_interval}m refresh."
            print(f"[INFO] {msg}")
            return {
                "statusCode": 200,
                "body": json.dumps({
                    "status": "skipped_offpeak_throttle",
                    "message": msg,
                    "schedule_label": commute_label,
                    "target_interval_minutes": target_interval,
                    "next_scheduled_run": f"in ~{max(1, round(target_interval - elapsed_mins))} minutes"
                })
            }
        else:
            print(f"[INFO] Off-peak {target_interval}m tick triggered (elapsed: {elapsed_mins:.1f} mins).")

    # 2. Try to fetch previous AI state from S3 to avoid cold-start LLM invocations
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

    # 3. Ingest telemetry & bulletins
    print("[INFO] Ingesting WSDOT telemetry and bulletins...")
    telemetry = get_merged_ferry_telemetry()
    bulletins = scrape_bulletins()
    
    # 4. Run Gemini Flash analysis (with state fingerprinting & caching)
    force_ai = event.get("force_ai", False)
    analysis = analyze_commute_with_gemini(
        telemetry=telemetry,
        bulletins_data=bulletins,
        cached_ai_state=cached_ai_state,
        force_ai_refresh=force_ai
    )
    analysis["commute_window_label"] = commute_label
    analysis["is_commute_window"] = is_commute
    analysis["timestamp_epoch"] = time.time()
    
    # 5. Generate static site files
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
            "commute_window": commute_label,
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
