"""
Local Runner & HTTP Preview Server.
Fetches live data, generates index.html and data.json into dist/,
and serves it on http://localhost:8000 for local development & testing.
"""

import argparse
import http.server
import socketserver
import sys
import threading
import time
from pathlib import Path

# Ensure project root in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from src.collectors.wsdot_api import get_merged_ferry_telemetry
from src.collectors.bulletin_scraper import scrape_bulletins
from src.analyzer.gemini_analyzer import analyze_commute_with_gemini
from src.generator.site_generator import generate_static_site

DIST_DIR = BASE_DIR / "dist"


def run_pipeline(api_key: str = None, model: str = None, s3_bucket: str = None):
    """Executes the data collection, AI analysis, and static site generation."""
    print("\n" + "=" * 60)
    print("🚢 Fetching live WSDOT telemetry and bulletins...")
    telemetry = get_merged_ferry_telemetry()
    bulletins = scrape_bulletins()
    
    print(f"📊 Mode detected: {bulletins.get('schedule_mode')}")
    print(f"🚢 Vessels on route: {len(telemetry.get('vessels', []))}")
    
    print("🤖 Running AI analysis (Gemini Flash / Heuristic fallback)...")
    analysis = analyze_commute_with_gemini(
        telemetry=telemetry,
        bulletins_data=bulletins,
        api_key=api_key,
        model_name=model
    )
    
    print(f"⚙️ Generating static 1-page site in {DIST_DIR}...")
    files = generate_static_site(analysis, str(DIST_DIR))
    print(f"✅ Generated: {files['index_html']}")
    print(f"✅ Generated: {files['data_json']}")

    if s3_bucket:
        try:
            import boto3
            print(f"☁️ Uploading to S3 bucket '{s3_bucket}'...")
            s3 = boto3.client("s3")
            with open(files["index_html"], "rb") as f:
                s3.put_object(
                    Bucket=s3_bucket,
                    Key="index.html",
                    Body=f.read(),
                    ContentType="text/html",
                    CacheControl="max-age=60, public",
                )
            with open(files["data_json"], "rb") as f:
                s3.put_object(
                    Bucket=s3_bucket,
                    Key="data.json",
                    Body=f.read(),
                    ContentType="application/json",
                    CacheControl="no-cache, no-store, must-revalidate",
                )
            print(f"✅ S3 upload complete!")
        except Exception as e:
            print(f"❌ Failed to upload to S3: {e}")

    print("=" * 60 + "\n")
    return files


def serve_dist(port: int = 8000):
    """Serves the dist directory over HTTP."""
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(DIST_DIR), **kwargs)

    with socketserver.TCPServer(("", port), Handler) as httpd:
        print(f"🌐 Serving local ferry tracker dashboard at: http://localhost:{port}")
        print("Press Ctrl+C to stop.\n")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server.")


def main():
    parser = argparse.ArgumentParser(description="Fauntleroy-Vashon Ferry Commute Local Runner")
    parser.add_argument("--key", help="Google Gemini API Key", default=None)
    parser.add_argument("--model", help="Gemini model name", default=None)
    parser.add_argument("--s3-bucket", help="Optional S3 bucket to sync index.html and data.json to", default=None)
    parser.add_argument("--port", type=int, default=8000, help="Local HTTP port")
    parser.add_argument("--once", action="store_true", help="Generate files once without serving")
    parser.add_argument("--interval", type=int, default=60, help="Auto-refresh interval in seconds")
    args = parser.parse_args()

    run_pipeline(api_key=args.key, model=args.model, s3_bucket=args.s3_bucket)

    if args.once:
        return

    # Background polling thread if desired
    if args.interval > 0:
        def loop_updater():
            while True:
                time.sleep(args.interval)
                try:
                    run_pipeline(api_key=args.key, model=args.model, s3_bucket=args.s3_bucket)
                except Exception as e:
                    print(f"[ERROR in background refresh]: {e}")
        t = threading.Thread(target=loop_updater, daemon=True)
        t.start()

    serve_dist(port=args.port)


if __name__ == "__main__":
    main()
