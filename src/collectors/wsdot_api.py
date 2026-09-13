"""
WSDOT Ferries API Collector
Fetches real-time vessel locations, VesselWatch telemetry, and daily timetable.
Handles weekend lookahead to ensure Saturday/Sunday lookups fetch Monday's school schedule.
"""

import json
import re
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional

from src.config import (
    WSDOT_VESSEL_LOCATIONS_URL,
    WSDOT_VESSEL_WATCH_URL,
    WSDOT_SCHEDULE_API_URL,
    TERMINAL_FAUNTLEROY_ID,
    TERMINAL_VASHON_ID,
    OP_ROUTE_FVS,
)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def parse_dot_net_date(raw: Optional[str]) -> Optional[str]:
    """Parses WSDOT /Date(1789311600000-0700)/ into ISO 8601 string."""
    if not raw or not isinstance(raw, str) or not raw.startswith("/Date("):
        return None
    try:
        match = re.search(r"/Date\((\d+)([+-]\d{4})?\)/", raw)
        if not match:
            return None
        ms = int(match.group(1))
        offset_str = match.group(2)
        
        # Default Pacific offset (-07:00 / -08:00)
        tz = timezone(timedelta(hours=-7))
        if offset_str:
            hours = int(offset_str[:3])
            mins = int(offset_str[0] + offset_str[3:])
            tz = timezone(timedelta(hours=hours, minutes=mins))
        
        dt = datetime.fromtimestamp(ms / 1000.0, tz=tz)
        return dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    except Exception:
        return None


def parse_time_hh_mm(iso_or_raw: Optional[str]) -> Optional[str]:
    """Returns HH:MM format from ISO string or /Date(...)/."""
    if not iso_or_raw:
        return None
    if iso_or_raw.startswith("/Date("):
        iso_str = parse_dot_net_date(iso_or_raw)
        if not iso_str:
            return None
        return iso_str[11:16]
    if "T" in iso_or_raw:
        return iso_or_raw.split("T")[1][:5]
    return iso_or_raw[:5]


def get_target_school_commute_date(dt: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Returns target commute date.
    If today is Saturday (weekday 5) or Sunday (weekday 6), shifts target to Monday.
    """
    now = dt or datetime.now(timezone(timedelta(hours=-7)))
    weekday = now.weekday()
    is_weekend = (weekday in (5, 6))
    
    if weekday == 5:  # Saturday -> +2 days to Monday
        target_dt = now + timedelta(days=2)
    elif weekday == 6:  # Sunday -> +1 day to Monday
        target_dt = now + timedelta(days=1)
    else:
        target_dt = now

    return {
        "current_datetime": now,
        "is_weekend": is_weekend,
        "current_weekday_name": now.strftime("%A"),
        "target_date_str": target_dt.strftime("%Y-%m-%d"),
        "target_weekday_name": target_dt.strftime("%A"),
        "target_display_name": target_dt.strftime("%A, %b %d"),
    }


def fetch_vessel_locations(timeout: int = 10) -> List[Dict[str, Any]]:
    """Fetches real-time vessel positions and telemetry from WSDOT REST API."""
    req = urllib.request.Request(WSDOT_VESSEL_LOCATIONS_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
            
            triangle_vessels = []
            for v in data:
                routes = [r.lower() for r in v.get("OpRouteAbbrev") or []]
                dep_term = (v.get("DepartingTerminalName") or "").lower()
                arr_term = (v.get("ArrivingTerminalName") or "").lower()
                
                is_triangle = (
                    OP_ROUTE_FVS in routes
                    or any(t in dep_term for t in ["fauntleroy", "vashon", "southworth"])
                    or any(t in arr_term for t in ["fauntleroy", "vashon", "southworth"])
                )
                
                if is_triangle:
                    v_clean = {
                        "vessel_id": v.get("VesselID"),
                        "name": v.get("VesselName"),
                        "mmsi": v.get("Mmsi"),
                        "in_service": v.get("InService", True),
                        "at_dock": v.get("AtDock", False),
                        "departing_terminal_id": v.get("DepartingTerminalID"),
                        "departing_terminal": v.get("DepartingTerminalName"),
                        "arriving_terminal_id": v.get("ArrivingTerminalID"),
                        "arriving_terminal": v.get("ArrivingTerminalName"),
                        "latitude": v.get("Latitude"),
                        "longitude": v.get("Longitude"),
                        "speed_knots": v.get("Speed", 0.0),
                        "heading": v.get("Heading", 0),
                        "scheduled_departure": parse_dot_net_date(v.get("ScheduledDeparture")),
                        "scheduled_departure_hhmm": parse_time_hh_mm(v.get("ScheduledDeparture")),
                        "left_dock": parse_dot_net_date(v.get("LeftDock")),
                        "left_dock_hhmm": parse_time_hh_mm(v.get("LeftDock")),
                        "eta": parse_dot_net_date(v.get("Eta")),
                        "eta_hhmm": parse_time_hh_mm(v.get("Eta")),
                        "eta_basis": v.get("EtaBasis"),
                        "timestamp": parse_dot_net_date(v.get("TimeStamp")),
                    }
                    triangle_vessels.append(v_clean)
            return triangle_vessels
    except Exception as e:
        print(f"[ERROR] Failed fetching vessel locations: {e}")
        return []


def fetch_vessel_watch(timeout: int = 10) -> Dict[int, Dict[str, Any]]:
    """
    Fetches real-time VesselWatch telemetry (delay flags, nextdep) from Vessels.ashx.
    """
    req = urllib.request.Request(WSDOT_VESSEL_WATCH_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
            watch_map = {}
            for v in data.get("vessellist", []):
                mmsi = v.get("mmsi")
                if mmsi:
                    watch_map[mmsi] = {
                        "depart_delayed": v.get("departDelayed") == "Y",
                        "next_dep": v.get("nextdep"),
                        "next_dep_ampm": v.get("nextdepAMPM"),
                        "head_text": v.get("headtxt"),
                        "eta_raw": v.get("eta"),
                        "left_dock_raw": v.get("leftdock"),
                        "shutoff_msg": (v.get("vesselwatch", {}).get("shutoff", {}) or {}).get("shutmsg"),
                    }
            return watch_map
    except Exception as e:
        print(f"[WARNING] Failed fetching VesselWatch: {e}")
        return {}


def fetch_today_schedule(date_str: Optional[str] = None, timeout: int = 15) -> Dict[str, Any]:
    """
    Fetches scheduled sailings for Route 14 (Fauntleroy / Vashon).
    date_str format: YYYY-MM-DD.
    """
    if not date_str:
        target_info = get_target_school_commute_date()
        date_str = target_info["target_date_str"]
        
    url = WSDOT_SCHEDULE_API_URL.format(date=date_str)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
            
            fauntleroy_to_vashon = []
            vashon_to_fauntleroy = []
            
            for tc in data.get("TerminalCombos", []):
                dep_id = tc.get("DepartingTerminalID")
                arr_id = tc.get("ArrivingTerminalID")
                
                times = tc.get("Times", [])
                parsed_times = []
                for t in times:
                    dep_iso = parse_dot_net_date(t.get("DepartingTime"))
                    arr_iso = parse_dot_net_date(t.get("ArrivingTime"))
                    parsed_times.append({
                        "scheduled_departure": dep_iso,
                        "time_hhmm": parse_time_hh_mm(dep_iso),
                        "vessel_id": t.get("VesselID"),
                        "vessel_name": t.get("VesselName"),
                        "scheduled_arrival": arr_iso,
                        "arrival_hhmm": parse_time_hh_mm(arr_iso),
                        "loading_rule": t.get("LoadingRule"),
                    })
                
                if dep_id == TERMINAL_FAUNTLEROY_ID and arr_id == TERMINAL_VASHON_ID:
                    fauntleroy_to_vashon = parsed_times
                elif dep_id == TERMINAL_VASHON_ID and arr_id == TERMINAL_FAUNTLEROY_ID:
                    vashon_to_fauntleroy = parsed_times
                    
            return {
                "date": date_str,
                "schedule_name": data.get("ScheduleName"),
                "schedule_pdf_url": data.get("SchedulePDFUrl"),
                "fauntleroy_to_vashon": fauntleroy_to_vashon,
                "vashon_to_fauntleroy": vashon_to_fauntleroy,
            }
    except Exception as e:
        print(f"[ERROR] Failed fetching schedule for {date_str}: {e}")
        return {
            "date": date_str,
            "fauntleroy_to_vashon": [],
            "vashon_to_fauntleroy": [],
        }


def get_merged_ferry_telemetry(date_str: Optional[str] = None, simulated_time: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Combines vessel locations, VesselWatch, and schedule.
    Automatically handles Saturday/Sunday lookups by defaulting schedule to Monday.
    """
    target_info = get_target_school_commute_date(simulated_time)
    sched_date = date_str or target_info["target_date_str"]
    
    vessels = fetch_vessel_locations()
    watch = fetch_vessel_watch()
    schedule = fetch_today_schedule(sched_date)
    
    # Enrich vessels with watch telemetry
    for v in vessels:
        mmsi = v.get("mmsi")
        if mmsi in watch:
            v.update(watch[mmsi])
            
    pacific_now = simulated_time or datetime.now(timezone(timedelta(hours=-7)))
    
    return {
        "timestamp": pacific_now.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "is_weekend": target_info["is_weekend"],
        "target_commute_date": target_info["target_date_str"],
        "target_commute_day_name": target_info["target_weekday_name"],
        "target_display_name": target_info["target_display_name"],
        "vessels": vessels,
        "schedule": schedule,
    }
