"""
WSDOT Ferries Bulletin Scraper
Scrapes and parses real-time rider alerts and travel bulletins from Bulletin.aspx.
Accurately detects active schedule mode (3-boat vs 2-boat) and specific cancelled sailings
(e.g., vessel crew shortages or mechanical issues operating on a 3-boat timetable),
preventing historical weekend notices from mischaracterizing weekday service.
"""

import re
import urllib.request
from datetime import datetime
from typing import Dict, List, Any, Optional

from src.config import WSDOT_BULLETIN_URL, WSDOT_ADDS_CANCELS_URL, WSDOT_SCHEDULE_DETAIL_URL

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

KNOWN_VESSELS = ["sealth", "kittitas", "kitsap", "cathlamet", "issaquah", "chelan"]


def fetch_route_cancellations(timeout: int = 10) -> Dict[str, Any]:
    """
    Scrapes WSDOT addcancelbysimpleroute.aspx?routeid=14 to extract
    official published daily additions and cancellations for Fauntleroy / Vashon.
    """
    req = urllib.request.Request(WSDOT_ADDS_CANCELS_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            html = response.read().decode("utf-8", errors="ignore")
            
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        
        # 1. Parse Legend
        legend = {}
        for tr in soup.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) >= 2:
                num = tds[0].get_text(strip=True)
                name = tds[1].get_text(strip=True)
                if num.isdigit() and name:
                    legend[num] = name

        # 2. Parse Adds and Cancels by Date
        cancels_by_date: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}

        for row in soup.find_all("tr", class_="addcancelcontent"):
            date_el = row.find("span", id=lambda x: x and "lblTripDate" in x)
            if not date_el:
                continue
            date_str = date_el.get_text(strip=True)  # e.g. 'Mon 09/14'
            
            def parse_time_blocks(span_el):
                times = []
                if not span_el:
                    return times
                for div in span_el.find_all("div", style=lambda s: s and "white-space: nowrap" in s):
                    time_div = div.find(["div", "b"])
                    raw_time = time_div.get_text(strip=True) if time_div else ""
                    is_pm = False
                    classes = time_div.get("class", []) if time_div else []
                    if "pm" in classes or div.find("b") is not None:
                        is_pm = True
                    vessel_span = div.find("span", style=lambda s: s and "font-size" in s)
                    v_num = vessel_span.get_text(strip=True) if vessel_span else ""
                    v_name = legend.get(v_num, f"Vessel {v_num}" if v_num else "")
                    
                    m = re.match(r"(\d+):(\d+)", raw_time)
                    if m:
                        h = int(m.group(1))
                        minute = int(m.group(2))
                        if is_pm and h < 12:
                            h += 12
                        elif not is_pm and h == 12:
                            h = 0
                        hhmm = f"{h:02d}:{minute:02d}"
                        times.append({
                            "time_hhmm": hhmm,
                            "time_display": raw_time + (" PM" if is_pm else " AM"),
                            "vessel_num": v_num,
                            "vessel_name": v_name,
                        })
                return times

            westbound_span = row.find("span", id=lambda x: x and "WestboundCancelledTimeAdj" in x)
            eastbound_span = row.find("span", id=lambda x: x and "EastboundCancelledTimeAdj" in x)
            
            cancels_by_date[date_str] = {
                "fauntleroy_cancelled": parse_time_blocks(westbound_span),
                "vashon_cancelled": parse_time_blocks(eastbound_span),
            }

        return {
            "legend": legend,
            "cancellations_by_date": cancels_by_date,
        }
    except Exception as e:
        print(f"[WARNING] Failed scraping route cancellations: {e}")
        return {
            "legend": {},
            "cancellations_by_date": {},
        }


def _match_date_cancels(cancels_by_date: Dict[str, Any], dt: datetime) -> Dict[str, Any]:
    """Matches cancellations by date string (e.g. 'Mon 09/14' or '09/14')."""
    date_key_pattern = dt.strftime("%m/%d")  # e.g. '09/14'
    for k, v in cancels_by_date.items():
        if date_key_pattern in k:
            return v
    return {"fauntleroy_cancelled": [], "vashon_cancelled": []}


def parse_bulletin_datetime(raw_date: str) -> Optional[datetime]:
    """Parses WSDOT bulletin timestamp into a datetime object for chronological sorting."""
    if not raw_date:
        return None
    cleaned = re.sub(r"^\[Last Updated:\s*", "", raw_date).rstrip("]")
    for fmt in (
        "%A, %B %d, %Y %I:%M %p",
        "%B %d, %Y %I:%M %p",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ):
        try:
            return datetime.strptime(cleaned, fmt)
        except Exception:
            pass
    return None


def _extract_cancelled_vessels(text: str) -> List[str]:
    """Extracts names of vessels reported as cancelled or out of service, excluding active vessels."""
    text_lower = text.lower()
    sentences = re.split(r"[\.\n;\!]", text_lower)
    
    cancelled = set()
    operating = set()

    for s in sentences:
        s_clean = s.strip()
        if not s_clean:
            continue
        for v in KNOWN_VESSELS:
            if v in s_clean:
                is_op = any(
                    kw in s_clean 
                    for kw in [
                        f"operate with {v}", f"operating with {v}", f"service with {v}",
                        f"with {v}", f"{v} is running", f"{v} will run"
                    ]
                ) or (("operate" in s_clean or "operating" in s_clean or "schedule with" in s_clean) and "with" in s_clean and v in s_clean)
                
                if is_op:
                    operating.add(v.capitalize())
                elif any(kw in s_clean for kw in ["out of service", "cancelled", "shortage of crew", "lack of crew", "tied up"]):
                    cancelled.add(v.capitalize())

    for v in KNOWN_VESSELS:
        if re.search(rf"\b{v}\b\s+cancelled", text_lower) or re.search(rf"\b{v}\b\s+is out of service", text_lower) or re.search(rf"\b{v}\b\s+out of service", text_lower):
            cancelled.add(v.capitalize())

    return [v.capitalize() for v in KNOWN_VESSELS if v.capitalize() in cancelled and v.capitalize() not in operating]



def scrape_bulletins(timeout: int = 10) -> Dict[str, Any]:
    """
    Scrapes WSDOT Bulletin.aspx and extracts alerts relevant to Fauntleroy/Vashon/Southworth.
    Determines:
    1. Base schedule timetable mode ("3-boat" vs "2-boat")
    2. Service status ("normal", "cancelled_sailings", "reduced_2boat")
    3. Any cancelled vessels due to crew shortages or mechanical issues
    4. Monday lookahead configuration for weekend commuters
    """
    req = urllib.request.Request(WSDOT_BULLETIN_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            html = response.read().decode("utf-8", errors="ignore")
            
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        
        bulletins: List[Dict[str, Any]] = []
        
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
            clean_date = re.sub(r"^\[Last Updated:\s*", "", date_text).rstrip("]")
            parsed_dt = parse_bulletin_datetime(clean_date)
            
            bulletin_obj = {
                "id": idx,
                "date": clean_date,
                "parsed_datetime": parsed_dt.isoformat() if parsed_dt else None,
                "title": title_text,
                "content": content_text,
                "is_triangle_route": False,
            }
            
            # Check relevance to Fauntleroy / Vashon / Southworth / Triangle
            search_corpus = f"{title_text} {content_text}".lower()
            keywords = [
                "fauntleroy", "faunt", "vashon", "southworth", "triangle",
                "f-v-s", "f/v/s", "fvs", "route 14", "route #14",
                "sealth", "kitsap", "kittitas", "cathlamet", "issaquah", "chelan",
                "point defiance", "pt defiance", "pt. defiance", "tahlequah", "pd/tah", "pd-tah",
                "all routes", "system-wide", "systemwide"
            ]
            if any(k in search_corpus for k in keywords):
                bulletin_obj["is_triangle_route"] = True
                
            bulletins.append(bulletin_obj)
            idx += 1
            
        triangle_bulletins = [b for b in bulletins if b["is_triangle_route"]]
        
        # Sort triangle bulletins chronologically descending (newest first)
        triangle_bulletins.sort(
            key=lambda x: parse_bulletin_datetime(x.get("date", "")) or datetime.min,
            reverse=True
        )
        
        detected_mode = "3-boat"
        service_status = "normal"
        cancelled_vessels: List[str] = []
        restored_vessels: List[str] = []
        cancelled_boat_numbers: List[str] = []
        disruption_reason = ""
        mode_reason = "Operating on normal three-boat schedule."
        
        # Determine current status from newest relevant bulletins
        for b in triangle_bulletins:
            text = f"{b['title']} {b['content']}".lower()
            
            # Check for vessel restoration / return to 3-boat schedule
            is_restoration = any(kw in text for kw in [
                "back in service", "returned to service", "return to service",
                "filled the previously open position", "route back to three-boat schedule",
                "route back to 3-boat schedule", "back on the three-boat schedule",
                "back on the 3-boat schedule"
            ])
            if is_restoration:
                for v in KNOWN_VESSELS:
                    if f"{v} back in service" in text or f"{v} will start" in text or f"{v} returned" in text or f"{v} starts" in text:
                        restored_vessels.append(v.capitalize())
                if "#3" in text and ("back in service" in text or "filled" in text or "start on" in text):
                    restored_vessels.append("Sealth")
                if not restored_vessels and "sealth" in text:
                    restored_vessels.append("Sealth")
                if any(kw in text for kw in ["return to three-boat", "route back to three-boat", "back on the three-boat"]):
                    detected_mode = "3-boat"
                    service_status = "normal"
                    mode_reason = f"Notice: {b['title']}"

            # Check for crew shortage / cancelled sailings
            is_crew_shortage = "shortage of crew" in text or "lack of crew" in text or "crew shortage" in text
            is_cancelled = "cancelled sailings" in text or "cancels all the #" in text or "out of service until a relief" in text
            
            if is_crew_shortage or is_cancelled:
                bulletin_cancelled = _extract_cancelled_vessels(f"{b['title']} {b['content']}")
                # If these vessels were already restored by a newer bulletin, don't re-cancel them
                unrestored_vessels = [v for v in bulletin_cancelled if v not in restored_vessels]
                
                if unrestored_vessels:
                    # Crucial check: Does route continue on 3-boat schedule for remaining boats?
                    if "continue to operate on 3-boat schedule" in text or "continue to operate on three-boat schedule" in text or "operate on 3-boat schedule" in text or "operate on three-boat schedule" in text:
                        detected_mode = "3-boat"
                    else:
                        if "operating on two-boat schedule" not in text and "operating on 2-boat schedule" not in text:
                            detected_mode = "3-boat"
                        else:
                            detected_mode = "2-boat"
                    
                    service_status = "cancelled_sailings"
                    disruption_reason = "Shortage of crew (relief pending)" if is_crew_shortage else "Cancelled sailings"
                    for v in unrestored_vessels:
                        if v not in cancelled_vessels:
                            cancelled_vessels.append(v)
                    if "#3" in text and "Sealth" in unrestored_vessels:
                        cancelled_boat_numbers.append("#3")
                    mode_reason = f"Alert: {b['title']}"
                    break
                
            # Check for official 2-boat schedule announcement
            is_fvs_route = any(kw in text for kw in ["fauntleroy", "faunt", "vashon", "southworth", "triangle", "f-v-s", "f/v/s", "route 14"])
            two_boat_phrases = [
                "two-boat schedule", "2-boat schedule", "two boat schedule", "2 boat schedule",
                "reduced to two boats", "reduced from three boats to two boats",
                "reduced to 2 boats", "reduced from 3 boats to 2 boats",
                "operating on two-boat", "operating on 2-boat",
                "operating on two boat", "operating on 2 boat",
                "route is on a two-boat", "route is on a 2-boat",
                "two-boat contingency", "2-boat contingency"
            ]
            if is_fvs_route and any(kw in text for kw in two_boat_phrases):
                if not is_restoration:
                    detected_mode = "2-boat"
                    service_status = "reduced_2boat"
                    disruption_reason = "Two-boat contingency schedule"
                    mode_reason = f"Alert: {b['title']}"
                    break
                
            # Check for return to 3-boat schedule announcement
            if "return to three-boat schedule" in text or "return to 3-boat schedule" in text or "three-boat schedule" in text:
                detected_mode = "3-boat"
                service_status = "normal"
                mode_reason = f"Notice: {b['title']}"

        # Detect Monday's schedule mode & cancellation status specifically (essential for weekend lookahead)
        from datetime import timezone, timedelta
        pacific_now = datetime.now(timezone(timedelta(hours=-7)))
        weekday = pacific_now.weekday()
        is_weekend = (weekday in (5, 6) or (weekday == 4 and pacific_now.hour >= 19))

        if is_weekend:
            # On weekends, default Monday to normal 3-boat unless an alert explicitly applies to Monday or is an ongoing reduction
            monday_mode = "3-boat"
            monday_service_status = "normal"
            monday_cancelled_vessels: List[str] = []
            monday_reason = "Operating on normal Monday three-boat schedule."
        else:
            monday_mode = detected_mode
            monday_service_status = service_status
            monday_cancelled_vessels = list(cancelled_vessels)
            monday_reason = mode_reason
        
        for b in triangle_bulletins:
            text = f"{b['title']} {b['content']}".lower()
            has_monday = "monday" in text or "mon 10/" in text or "mon 09/" in text or "mon 11/" in text
            is_ongoing_2boat = any(kw in text for kw in [
                "two-boat schedule until further notice", "2-boat schedule until further notice",
                "two boat schedule until further notice", "2 boat schedule until further notice",
                "two-boat schedule beginning", "2-boat schedule beginning",
                "two boat schedule beginning", "2 boat schedule beginning",
                "reduced from three boats to two boats", "reduced from 3 boats to 2 boats",
                "for approximately one month"
            ]) and any(kw in text for kw in ["two-boat", "2-boat", "two boat", "2 boat", "two boats", "2 boats"])
            
            is_3boat_restoration = any(kw in text for kw in [
                "return to three-boat", "return to 3-boat", "return to 3 boat",
                "back to three-boat", "back to 3-boat", "route back to three", "back on the three"
            ])

            has_2boat_kw = any(kw in text for kw in [
                "two-boat", "2-boat", "two boat", "2 boat",
                "reduced to two boats", "reduced from three boats to two boats",
                "reduced to 2 boats", "reduced from 3 boats to 2 boats"
            ])

            if has_monday:
                if is_3boat_restoration:
                    monday_mode = "3-boat"
                    monday_service_status = "normal"
                    monday_reason = f"Bulletin indicates Monday returns to 3-boat schedule: {b['title']}"
                    break
                elif has_2boat_kw:
                    monday_mode = "2-boat"
                    monday_service_status = "reduced_2boat"
                    monday_reason = f"Bulletin indicates Monday runs 2-boat service: {b['title']}"
                    break
                elif any(kw in text for kw in ["three-boat", "3-boat", "three boat", "3 boat"]):
                    monday_mode = "3-boat"
                    monday_service_status = "normal"
                    monday_reason = f"Bulletin indicates Monday operates 3-boat schedule: {b['title']}"
                    break
                # Check for Monday cancellations specifically
                if ("shortage of crew" in text or "cancelled sailings" in text) and monday_mode == "3-boat":
                    monday_service_status = "cancelled_sailings"
                    monday_cancelled_vessels = _extract_cancelled_vessels(f"{b['title']} {b['content']}")
                    break
            elif is_ongoing_2boat and not is_3boat_restoration:
                monday_mode = "2-boat"
                monday_service_status = "reduced_2boat"
                monday_reason = f"Bulletin indicates ongoing 2-boat service: {b['title']}"
                break

        # Fetch official route additions & cancellations table from WSDOT
        cancellations_data = fetch_route_cancellations(timeout=timeout)
        cancels_by_date = cancellations_data.get("cancellations_by_date", {})
        
        from datetime import timezone, timedelta
        pacific_now = datetime.now(timezone(timedelta(hours=-7)))
        current_hhmm = f"{pacific_now.hour:02d}:{pacific_now.minute:02d}"
        today_cancels = _match_date_cancels(cancels_by_date, pacific_now)
        exact_cancelled_f_to_v = [t["time_hhmm"] for t in today_cancels.get("fauntleroy_cancelled", [])]
        exact_cancelled_v_to_f = [t["time_hhmm"] for t in today_cancels.get("vashon_cancelled", [])]
        
        all_today_cancels = today_cancels.get("fauntleroy_cancelled", []) + today_cancels.get("vashon_cancelled", [])
        for t in all_today_cancels:
            v_name = t.get("vessel_name")
            trip_time = t.get("time_hhmm", "")
            if not v_name or v_name not in [v.capitalize() for v in KNOWN_VESSELS]:
                continue
            # Only add to cancelled_vessels if the cancellation is upcoming today
            if trip_time > current_hhmm:
                if v_name not in restored_vessels and v_name not in cancelled_vessels:
                    cancelled_vessels.append(v_name)
                    service_status = "cancelled_sailings"

        # If a vessel had cancellations earlier today, but has NO upcoming cancellations
        # and is not in cancelled_vessels, it has completed its morning cancellations
        past_vessels = {
            t.get("vessel_name") for t in all_today_cancels
            if t.get("vessel_name") in [v.capitalize() for v in KNOWN_VESSELS] and t.get("time_hhmm", "") <= current_hhmm
        }
        for v_name in past_vessels:
            has_upcoming = any(
                t.get("vessel_name") == v_name and t.get("time_hhmm", "") > current_hhmm
                for t in all_today_cancels
            )
            if not has_upcoming and v_name not in cancelled_vessels and v_name not in restored_vessels:
                restored_vessels.append(v_name)

        if not cancelled_vessels and service_status == "cancelled_sailings":
            service_status = "normal"

        # Match Monday cancellations (for weekend lookahead)
        weekday = pacific_now.weekday()
        is_weekend = (weekday in (5, 6) or (weekday == 4 and pacific_now.hour >= 19))
        if weekday == 5:
            monday_dt = pacific_now + timedelta(days=2)
        elif weekday == 6:
            monday_dt = pacific_now + timedelta(days=1)
        elif weekday == 4 and pacific_now.hour >= 19:
            monday_dt = pacific_now + timedelta(days=3)
        else:
            monday_dt = pacific_now

        monday_cancels = _match_date_cancels(cancels_by_date, monday_dt)
        monday_exact_f_to_v = [t["time_hhmm"] for t in monday_cancels.get("fauntleroy_cancelled", [])]
        monday_exact_v_to_f = [t["time_hhmm"] for t in monday_cancels.get("vashon_cancelled", [])]
        monday_all_cancels = monday_cancels.get("fauntleroy_cancelled", []) + monday_cancels.get("vashon_cancelled", [])

        if is_weekend:
            # On the weekend, all Monday cancellations are in the future
            for t in monday_all_cancels:
                v_name = t.get("vessel_name")
                if v_name and v_name not in restored_vessels and v_name not in monday_cancelled_vessels and v_name in [v.capitalize() for v in KNOWN_VESSELS]:
                    monday_cancelled_vessels.append(v_name)
            if monday_cancelled_vessels:
                monday_service_status = "cancelled_sailings"
            elif restored_vessels:
                monday_service_status = "normal"
        else:
            monday_cancelled_vessels = list(cancelled_vessels)
            monday_service_status = service_status
                
        return {
            "schedule_mode": detected_mode,
            "service_status": service_status,
            "cancelled_vessels": cancelled_vessels,
            "restored_vessels": list(dict.fromkeys(restored_vessels)),
            "cancelled_boat_numbers": cancelled_boat_numbers,
            "exact_cancelled_f_to_v": exact_cancelled_f_to_v,
            "exact_cancelled_v_to_f": exact_cancelled_v_to_f,
            "cancellations_by_date": cancels_by_date,
            "disruption_reason": disruption_reason,
            "mode_reason": mode_reason,
            "monday_schedule_mode": monday_mode,
            "monday_service_status": monday_service_status,
            "monday_cancelled_vessels": monday_cancelled_vessels,
            "monday_restored_vessels": list(dict.fromkeys(restored_vessels)),
            "monday_exact_cancelled_f_to_v": monday_exact_f_to_v,
            "monday_exact_cancelled_v_to_f": monday_exact_v_to_f,
            "monday_mode_reason": monday_reason,
            "triangle_bulletins": triangle_bulletins,
            "all_bulletins_count": len(bulletins),
            "bulletin_url": WSDOT_BULLETIN_URL,
            "adds_cancels_url": WSDOT_ADDS_CANCELS_URL,
            "schedule_detail_url": WSDOT_SCHEDULE_DETAIL_URL,
        }
    except Exception as e:
        print(f"[ERROR] Failed scraping bulletins: {e}")
        return {
            "schedule_mode": "3-boat",
            "service_status": "normal",
            "cancelled_vessels": [],
            "cancelled_boat_numbers": [],
            "disruption_reason": "",
            "mode_reason": f"Scraper fallback: {e}",
            "monday_schedule_mode": "3-boat",
            "monday_service_status": "normal",
            "monday_cancelled_vessels": [],
            "monday_mode_reason": "Default normal schedule.",
            "triangle_bulletins": [],
            "all_bulletins_count": 0,
        }
