"""
WSDOT Ferries Bulletin Scraper
Scrapes and parses real-time rider alerts and travel bulletins from Bulletin.aspx.
Detects active schedule mode (3-boat vs 2-boat) and specific route delays,
including explicit tracking of Monday's schedule mode for weekend lookups.
"""

import re
import urllib.request
from typing import Dict, List, Any
from bs4 import BeautifulSoup

from src.config import WSDOT_BULLETIN_URL

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def scrape_bulletins(timeout: int = 10) -> Dict[str, Any]:
    """
    Scrapes WSDOT Bulletin.aspx and extracts alerts relevant to Fauntleroy/Vashon/Southworth.
    Determines whether route is operating in 2-boat or 3-boat mode today,
    and also checks if upcoming Monday has a specific schedule announced.
    """
    req = urllib.request.Request(WSDOT_BULLETIN_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            html = response.read().decode("utf-8", errors="ignore")
            
        soup = BeautifulSoup(html, "html.parser")
        
        bulletins: List[Dict[str, str]] = []
        
        # Look for repeater spans
        idx = 0
        while True:
            date_span = soup.find("span", id=f"cphPageTemplate_rprBulletins_lblBulletinDate_{idx}")
            title_span = soup.find("span", id=f"cphPageTemplate_rprBulletins_lblBulletinTitle_{idx}")
            content_span = soup.find("span", id=f"cphPageTemplate_rprBulletins_lblContent_{idx}")
            
            if not date_span and not title_span and not content_span:
                break
                
            date_text = date_span.get_text(" ", strip=True) if date_span else ""
            title_text = title_span.get_text(" ", strip=True) if title_span else ""
            content_text = content_span.get_text(" ", strip=True) if content_span else ""
            
            # Clean up boilerplate
            date_text = re.sub(r"^\[Last Updated:\s*", "", date_text).rstrip("]")
            
            bulletin_obj = {
                "id": idx,
                "date": date_text,
                "title": title_text,
                "content": content_text,
                "is_triangle_route": False,
            }
            
            # Check relevance to Fauntleroy / Vashon / Southworth / Triangle
            search_corpus = f"{title_text} {content_text}".lower()
            keywords = ["fauntleroy", "faunt", "vashon", "southworth", "triangle", "f-v-s", "f/v/s"]
            if any(k in search_corpus for k in keywords):
                bulletin_obj["is_triangle_route"] = True
                
            bulletins.append(bulletin_obj)
            idx += 1
            
        # Detect 2-boat vs 3-boat operating mode for today
        detected_mode = "3-boat"  # default to normal
        mode_reason = "No active reduction notices found; operating normal three-boat schedule."
        
        triangle_bulletins = [b for b in bulletins if b["is_triangle_route"]]
        
        for b in triangle_bulletins:
            text = f"{b['title']} {b['content']}".lower()
            if "two-boat schedule" in text or "2-boat schedule" in text or "two-boat service" in text:
                detected_mode = "2-boat"
                mode_reason = f"Alert: {b['title']}"
                break
            elif "three-boat schedule" in text or "3-boat schedule" in text:
                detected_mode = "3-boat"
                mode_reason = f"Notice: {b['title']}"

        # Detect Monday's schedule mode specifically (essential for weekend lookahead)
        monday_mode = detected_mode
        monday_reason = mode_reason
        for b in triangle_bulletins:
            text = f"{b['title']} {b['content']}".lower()
            if "monday" in text:
                if "three-boat" in text or "3-boat" in text:
                    monday_mode = "3-boat"
                    monday_reason = f"Bulletin indicates Monday returns to 3-boat service: {b['title']}"
                    break
                elif "two-boat" in text or "2-boat" in text:
                    monday_mode = "2-boat"
                    monday_reason = f"Bulletin indicates Monday runs 2-boat service: {b['title']}"
                    break
                
        return {
            "schedule_mode": detected_mode,
            "mode_reason": mode_reason,
            "monday_schedule_mode": monday_mode,
            "monday_mode_reason": monday_reason,
            "triangle_bulletins": triangle_bulletins,
            "all_bulletins_count": len(bulletins),
        }
    except Exception as e:
        print(f"[ERROR] Failed scraping bulletins: {e}")
        return {
            "schedule_mode": "3-boat",
            "mode_reason": f"Scraper fallback: {e}",
            "monday_schedule_mode": "3-boat",
            "monday_mode_reason": "Default normal schedule.",
            "triangle_bulletins": [],
            "all_bulletins_count": 0,
        }
