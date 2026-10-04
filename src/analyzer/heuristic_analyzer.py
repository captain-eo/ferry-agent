"""
Deterministic Heuristic Analyzer for Fauntleroy-Vashon Commute.
Evaluates scheduled sailings, live boat positions, delays, and bus catchability.
Detects Saturday and Sunday lookups to automatically target Monday morning sailings.
Accurately distinguishes between official 2-boat schedule changes vs. 3-boat schedules
with uncrewed/cancelled vessels (e.g. crew shortages on the #3 boat).
"""

import math
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional

from src.config import VISD_COMMUTE_RULES, STANDARD_CROSSING_MINUTES, VESSELS_API_URL
from src.collectors.wsdot_api import parse_time_hh_mm

FAUNTLEROY_LAT = 47.5235
FAUNTLEROY_LON = -122.3965


def calculate_distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculates approximate distance in nautical miles between two coordinates."""
    d_lat = (lat2 - lat1) * 60.0
    d_lon = (lon2 - lon1) * 60.0 * math.cos(math.radians((lat1 + lat2) / 2.0))
    return math.hypot(d_lat, d_lon)

STANDARD_3_BOAT_F_TO_V = [
    ("05:05", "Kittitas"),
    ("05:50", "Kitsap"),
    ("06:10", "Sealth"),
    ("07:05", "Sealth"),
    ("08:05", "Kittitas"),
    ("08:25", "Kitsap"),
    ("09:30", "Kitsap"),
    ("09:50", "Kittitas"),
    ("10:10", "Sealth"),
]

STANDARD_3_BOAT_V_TO_F = [
    ("15:25", "Sealth"),
    ("16:40", "Kittitas"),
    ("17:05", "Sealth"),
    ("17:45", "Kitsap"),
    ("18:45", "Sealth"),
]


def get_current_pacific_time() -> datetime:
    """Returns current Pacific Time."""
    return datetime.now(timezone(timedelta(hours=-7)))


def time_str_to_minutes(hhmm: str) -> int:
    """Converts 'HH:MM' (24-hour) to minutes from midnight."""
    parts = hhmm.split(":")
    return int(parts[0]) * 60 + int(parts[1])


def minutes_to_time_str(mins: int) -> str:
    """Converts minutes from midnight to 'HH:MM' string."""
    mins = mins % (24 * 60)
    h = mins // 60
    m = mins % 60
    return f"{h:02d}:{m:02d}"


def minutes_to_12hr(hhmm: str) -> str:
    """Converts 'HH:MM' to 'H:MM AM/PM'."""
    mins = time_str_to_minutes(hhmm)
    h = (mins // 60) % 24
    m = mins % 60
    ampm = "AM" if h < 12 else "PM"
    h_12 = h % 12
    if h_12 == 0:
        h_12 = 12
    return f"{h_12}:{m:02d} {ampm}"


def is_vessel_on_schedule(
    vessel_name: str,
    est_dep_mins: int,
    scheduled_sailings: List[Dict[str, Any]]
) -> bool:
    """
    Checks if a vessel is operating a scheduled sailing, or if an official scheduled
    sailing departs Fauntleroy around the given departure time.
    """
    if not vessel_name or not scheduled_sailings:
        return False
    v_clean = vessel_name.replace("M/V", "").strip().lower()
    for s in scheduled_sailings:
        s_time = s.get("time_hhmm")
        if not s_time:
            continue
        try:
            s_mins = time_str_to_minutes(s_time)
        except Exception:
            continue
        s_vessel = (s.get("vessel_name") or "").replace("M/V", "").strip().lower()
        
        # If this vessel is assigned to a sailing within 45 mins of this departure
        if s_vessel and s_vessel == v_clean and abs(s_mins - est_dep_mins) <= 45:
            return True
            
        # If any official sailing is scheduled within 10 mins of this departure
        if abs(s_mins - est_dep_mins) <= 10:
            return True
            
    return False


def detect_ghost_ferry(
    vessels: List[Dict[str, Any]],
    now: datetime,
    target_weekday: int,
    is_weekend: bool = False,
    scheduled_sailings: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    Detects the unscheduled morning walk-on school boat ("Ghost Ferry") at Fauntleroy.
    Between 7:00 AM and 8:05 AM, an unscheduled positioning/turnaround boat (frequently M/V Kitsap)
    often docks at Fauntleroy (~7:25-7:35 AM). While not taking cars, it accepts walk-on school students.
    
    Only detects vessels that:
    1. Arrive at Fauntleroy between approximately 7:15 AM and 7:45 AM.
    2. Are NOT already on the official timetable schedule.
    
    On Thursdays (target_weekday == 3), this run typically carries hazardous cargo (fuel/propane tankers)
    under USCG regulations, strictly prohibiting walk-on passengers.
    """
    is_thursday = (target_weekday == 3)
    current_mins = now.hour * 60 + now.minute
    # Morning commute active detection window: strictly 07:00 AM (420 min) to 08:05 AM (485 min) on weekdays
    in_active_window = (420 <= current_mins <= 485) and not is_weekend

    # Ensure scheduled sailings list includes standard 3-boat timetable if empty or partial
    sched_list: List[Dict[str, Any]] = [dict(s) for s in scheduled_sailings] if scheduled_sailings else []
    if not sched_list:
        sched_list = [{"time_hhmm": t, "vessel_name": v} for t, v in STANDARD_3_BOAT_F_TO_V]
    else:
        existing_times = {s.get("time_hhmm") for s in sched_list if s.get("time_hhmm")}
        for t, v in STANDARD_3_BOAT_F_TO_V:
            if t not in existing_times:
                sched_list.append({"time_hhmm": t, "vessel_name": v})
        for s in sched_list:
            if not s.get("vessel_name"):
                t = s.get("time_hhmm")
                for std_t, std_v in STANDARD_3_BOAT_F_TO_V:
                    if std_t == t:
                        s["vessel_name"] = std_v
                        break

    detected_vessel = None
    docking_status = "Monitored Live (7:00 AM – 8:05 AM)"
    est_dep_hhmm = "07:35"
    is_detected = False

    if in_active_window:
        for v in vessels:
            if v.get("in_service") is False:
                continue

            arr_id = v.get("arriving_terminal_id")
            arr_name = (v.get("arriving_terminal") or "").lower()
            dep_id = v.get("departing_terminal_id")
            dep_name = (v.get("departing_terminal") or "").lower()
            at_dock = v.get("at_dock", False)
            speed = v.get("speed_knots", 0.0)
            lat = v.get("latitude")
            lon = v.get("longitude")

            # Check if vessel is at Fauntleroy dock
            is_at_fauntleroy = at_dock and (dep_id == 9 or "fauntleroy" in dep_name)

            # Check if vessel is inbound to Fauntleroy
            is_inbound = (arr_id == 9 or "fauntleroy" in arr_name) and speed > 0.5

            # Check if vessel is within 2.2 nautical miles of Fauntleroy dock and moving
            is_near = False
            if lat and lon:
                dist_nm = calculate_distance_nm(lat, lon, FAUNTLEROY_LAT, FAUNTLEROY_LON)
                if dist_nm <= 2.2 and speed > 1.0:
                    is_near = True

            # Check if vessel is returning from Fauntleroy heading to Vashon/Southworth
            is_returning = (dep_id == 9 or "fauntleroy" in dep_name) and (arr_id == 22 or "vashon" in arr_name) and speed > 0.5

            if not (is_at_fauntleroy or is_inbound or is_near or is_returning):
                continue

            # Calculate estimated arrival time at Fauntleroy and estimated departure time
            if is_at_fauntleroy:
                arr_fauntleroy_mins = current_mins
                dep_mins = current_mins + 5
                in_arr_window = (435 <= current_mins <= 470)
            elif is_returning:
                arr_fauntleroy_mins = current_mins - 10
                dep_mins = max(current_mins - 5, 420)
                in_arr_window = (435 <= arr_fauntleroy_mins <= 465) and (440 <= current_mins <= 475)
            else:
                eta_hhmm = v.get("eta_hhmm")
                if not eta_hhmm and v.get("eta"):
                    eta_hhmm = parse_time_hh_mm(v.get("eta"))

                if not eta_hhmm and lat and lon:
                    dist_nm = calculate_distance_nm(lat, lon, FAUNTLEROY_LAT, FAUNTLEROY_LON)
                    eta_mins_offset = int((dist_nm / max(speed, 6.0)) * 60)
                    arr_fauntleroy_mins = current_mins + eta_mins_offset
                elif eta_hhmm:
                    arr_fauntleroy_mins = time_str_to_minutes(eta_hhmm)
                else:
                    arr_fauntleroy_mins = current_mins + 10

                dep_mins = arr_fauntleroy_mins + 5
                in_arr_window = (435 <= arr_fauntleroy_mins <= 465)

            # Must arrive at Fauntleroy between ~7:15 AM (435) and ~7:45 AM (465)
            if not in_arr_window:
                continue

            # Exclude vessels already on the official timetable schedule
            v_raw_name = v.get("name") or ""
            if is_vessel_on_schedule(v_raw_name, dep_mins, sched_list):
                continue

            detected_vessel = v
            is_detected = True
            est_dep_hhmm = minutes_to_time_str(dep_mins)

            if is_at_fauntleroy:
                docking_status = "At Fauntleroy Dock Now"
            elif is_returning:
                docking_status = "Departed Fauntleroy • En Route to Vashon"
            else:
                eta_display = minutes_to_12hr(minutes_to_time_str(arr_fauntleroy_mins))
                docking_status = f"Approaching Fauntleroy (ETA ~{eta_display})"
            break

    v_raw_name = detected_vessel.get("name") if detected_vessel else "Kitsap"
    vessel_name = f"M/V {v_raw_name}" if not v_raw_name.startswith("M/V") else v_raw_name
    if not detected_vessel:
        vessel_name += " (Typical)"
    
    # Calculate arrival at Vashon dock (~20 min crossing)
    dep_mins = time_str_to_minutes(est_dep_hhmm)
    arr_mins = dep_mins + STANDARD_CROSSING_MINUTES
    est_arr_hhmm = minutes_to_time_str(arr_mins)

    # Catchability & Bus note
    if is_thursday:
        catchability = "HAZMAT"
        bus_note = (
            "⚠️ Thursday Hazmat Advisory: Washington State Ferries conducts scheduled hazardous materials transport "
            "(fuel/propane tankers) on Thursday mornings. Coast Guard regulations strictly prohibit walk-on passengers "
            "during hazmat runs. Students should plan for the 8:05 AM boat or verify with dock staff before boarding."
        )
    elif is_detected:
        catchability = "NOT RECOMMENDED"
        bus_note = (
            f"⚠️ Unscheduled Run (Not Recommended): {vessel_name} detected navigating to/operating at Fauntleroy dock (~{minutes_to_12hr(est_dep_hhmm)}). "
            f"This positioning boat does not carry vehicles and is NOT an official VISD school commute sailing. "
            f"While walk-on students sometimes use it to reach Vashon ~{minutes_to_12hr(est_arr_hhmm)}, taking it is not recommended by the school district."
        )
    else:
        catchability = "NOT RECOMMENDED"
        bus_note = (
            "⚠️ Unscheduled Run (Not Recommended): Monitored live between 7:00 AM – 8:05 AM. Only displayed when detected."
        )

    return {
        "scheduled_time": "07:30",
        "scheduled_time_display": "~7:30 AM" if is_detected else "~7:30 AM (Est.)",
        "estimated_departure": est_dep_hhmm,
        "estimated_departure_display": minutes_to_12hr(est_dep_hhmm),
        "estimated_arrival": est_arr_hhmm,
        "estimated_arrival_display": minutes_to_12hr(est_arr_hhmm),
        "delay_minutes": 0,
        "vessel_name": vessel_name,
        "status": docking_status,
        "catchability": catchability,
        "bus_note": bus_note,
        "is_cancelled": False,
        "is_ghost_ferry": True,
        "is_detected": is_detected,
        "is_thursday_hazmat": is_thursday,
        "next_sailing": None,
    }


def evaluate_commute(
    telemetry: Dict[str, Any],
    bulletins_data: Dict[str, Any],
    simulated_time: Optional[datetime] = None
) -> Dict[str, Any]:
    """
    Evaluates commute status using deterministic rules and real-time feeds.
    When invoked on Saturday or Sunday, automatically shows Monday morning as target sailings.
    Handles uncrewed/cancelled vessels without erroneously switching to 2-boat contingency times.
    """
    now = simulated_time or get_current_pacific_time()
    weekday = now.weekday()  # 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
    hour = now.hour
    is_after_7pm = (hour >= 19)
    is_weekend = (weekday in (5, 6))
    is_next_day = False
    
    if weekday == 5:  # Saturday -> +2 days to Monday
        target_dt = now + timedelta(days=2)
        is_weekend = True
    elif weekday == 6:  # Sunday -> +1 day to Monday
        target_dt = now + timedelta(days=1)
        is_weekend = True
    elif weekday == 4 and is_after_7pm:  # Friday after 7pm -> +3 days to Monday
        target_dt = now + timedelta(days=3)
        is_weekend = True
        is_next_day = True
    elif is_after_7pm:  # Mon-Thu after 7pm -> +1 day to tomorrow
        target_dt = now + timedelta(days=1)
        is_next_day = True
    else:
        target_dt = now

    target_weekday = target_dt.weekday()
    target_day_name = target_dt.strftime("%A")
    is_target_friday = (target_weekday == 4)

    # Target day logic
    if is_weekend:
        target_day_name = "Monday"
        is_target_friday = False
        target_weekday = 0
        mode = bulletins_data.get("monday_schedule_mode", "3-boat")
        service_status = bulletins_data.get("monday_service_status", "normal")
        cancelled_vessels = list(bulletins_data.get("monday_cancelled_vessels", []))
        restored_vessels = list(bulletins_data.get("monday_restored_vessels", []))
        mode_reason = bulletins_data.get("monday_mode_reason", "Operating on normal Monday three-boat schedule.")
        commute_title = "Monday School Commute (Next School Day)"
    elif is_next_day:
        target_day_lower = target_day_name.lower()
        # Default tomorrow's mode to 3-boat unless an active bulletin explicitly applies to tomorrow
        # (e.g. mentions tomorrow or announces an ongoing reduction until further notice without day restrictions)
        tomorrow_mode = "3-boat"
        tomorrow_service_status = "normal"
        tomorrow_reason = f"Operating on normal {target_day_name} three-boat schedule."
        
        for b in bulletins_data.get("triangle_bulletins", []):
            b_text = f"{b.get('title', '')} {b.get('content', '')}".lower()
            has_tomorrow = target_day_lower in b_text or target_day_lower[:3] in b_text
            is_ufn = "until further notice" in b_text and not any(
                kw in b_text for kw in ["today", "tonight", "this morning", "this afternoon"]
            )
            if has_tomorrow or is_ufn:
                if "three-boat" in b_text or "3-boat" in b_text or "return to three-boat" in b_text:
                    tomorrow_mode = "3-boat"
                    tomorrow_service_status = "normal"
                    tomorrow_reason = f"Notice: {b.get('title', '')}"
                    break
                elif "two-boat" in b_text or "2-boat" in b_text:
                    tomorrow_mode = "2-boat"
                    tomorrow_service_status = "reduced_2boat"
                    tomorrow_reason = f"Alert: {b.get('title', '')}"
                    break
        
        mode = tomorrow_mode
        service_status = tomorrow_service_status
        restored_vessels = list(bulletins_data.get("restored_vessels", []))
        mode_reason = tomorrow_reason
        commute_title = f"{target_day_name} School Commute (Tomorrow)"
        
        # Check tomorrow's published cancellations from WSDOT adds & cancels table
        target_date_pattern = target_dt.strftime("%m/%d")
        tomorrow_cancels = {"fauntleroy_cancelled": [], "vashon_cancelled": []}
        for k, v in bulletins_data.get("cancellations_by_date", {}).items():
            if target_date_pattern in k:
                tomorrow_cancels = v
                break
        
        cancelled_vessels = [
            t["vessel_name"] for t in (tomorrow_cancels.get("fauntleroy_cancelled", []) + tomorrow_cancels.get("vashon_cancelled", []))
            if t.get("vessel_name")
        ]
        if cancelled_vessels:
            service_status = "cancelled_sailings"
        else:
            # Check if active bulletins explicitly apply to tomorrow or say "until further notice"
            tomorrow_bulletin_cancels = []
            for b in bulletins_data.get("triangle_bulletins", []):
                b_text = f"{b.get('title', '')} {b.get('content', '')}".lower()
                if "until further notice" in b_text or target_day_name.lower() in b_text:
                    if "shortage of crew" in b_text or "cancelled sailings" in b_text:
                        for cv in bulletins_data.get("cancelled_vessels", []):
                            if cv not in tomorrow_bulletin_cancels:
                                tomorrow_bulletin_cancels.append(cv)
            if tomorrow_bulletin_cancels:
                cancelled_vessels = tomorrow_bulletin_cancels
                service_status = "cancelled_sailings"
            else:
                if mode != "2-boat":
                    service_status = "normal"
    else:
        target_day_name = now.strftime("%A")
        is_target_friday = (weekday == 4)
        target_weekday = weekday
        mode = bulletins_data.get("schedule_mode", "3-boat")
        service_status = bulletins_data.get("service_status", "normal")
        cancelled_vessels = list(bulletins_data.get("cancelled_vessels", []))
        restored_vessels = list(bulletins_data.get("restored_vessels", []))
        mode_reason = bulletins_data.get("mode_reason", "")
        commute_title = f"{target_day_name} School Commute"

    # Restored vessels override active vessel cancellation status
    cancelled_vessels = [v for v in cancelled_vessels if v not in restored_vessels]
    if not cancelled_vessels and service_status == "cancelled_sailings" and restored_vessels:
        service_status = "normal"

    disruption_reason = bulletins_data.get("disruption_reason", "")
    has_cancelled = bool(cancelled_vessels)

    vessels = telemetry.get("vessels", [])
    schedule = telemetry.get("schedule", {})
    
    # Map vessels by name for lookup
    vessel_map = {v.get("name", "").lower(): v for v in vessels}

    # Reconcile mode with official WSDOT timetable for the target date if available
    v_to_f_scheduled = schedule.get("vashon_to_fauntleroy", [])
    f_to_v_scheduled = schedule.get("fauntleroy_to_vashon", [])
    if f_to_v_scheduled:
        sched_times = {s.get("time_hhmm") for s in f_to_v_scheduled if s.get("time_hhmm")}
        
        # 3-Boat timetable markers:
        # Normal 3-boat schedule has 07:05 AM, 08:05 AM, and 06:10 AM; Friday has 08:25 AM.
        # Weekday 3-boat schedule has 28+ departures.
        has_3boat_marker = (
            ("07:05" in sched_times and "08:05" in sched_times)
            or ("07:05" in sched_times and "06:10" in sched_times)
            or (is_target_friday and "08:25" in sched_times)
            or ("07:05" in sched_times)
            or ("08:05" in sched_times and "08:15" not in sched_times)
            or ("06:10" in sched_times)
            or (len(f_to_v_scheduled) >= 28)
        )
        
        # 2-Boat timetable markers:
        # 2-boat timetable has 07:40 AM (never on 3-boat), 06:45 AM (without 07:05 AM),
        # 08:15 AM (without 08:05 AM), and exactly 24 total departures (<= 26 when full day >= 20).
        has_2boat_marker = (
            ("07:40" in sched_times)
            or ("06:45" in sched_times and "07:05" not in sched_times and ("08:15" in sched_times or "05:45" in sched_times))
            or ("08:15" in sched_times and "08:05" not in sched_times and not is_target_friday)
            or ("07:20" in sched_times)
            or (len(f_to_v_scheduled) >= 20 and len(f_to_v_scheduled) <= 26 and "07:05" not in sched_times and "08:05" not in sched_times)
        )

        # Check if an active bulletin specifically declared 2-boat service for the target commute day
        has_explicit_2boat_bulletin = False
        target_name_lower = target_day_name.lower()
        for b in bulletins_data.get("triangle_bulletins", []):
            b_text = f"{b.get('title', '')} {b.get('content', '')}".lower()
            if (target_name_lower in b_text or (is_weekend and "monday" in b_text)) and (
                "two-boat" in b_text or "2-boat" in b_text or "two boat" in b_text or "2 boat" in b_text
                or "reduced to two boats" in b_text or "reduced from three boats to two boats" in b_text
            ):
                has_explicit_2boat_bulletin = True
                break

        if has_2boat_marker and not has_3boat_marker:
            mode = "2-boat"
            if service_status == "normal":
                service_status = "reduced_2boat"
            mode_reason = f"Route 14 timetable confirms published two-boat schedule for {target_day_name}."
        elif has_3boat_marker and not has_2boat_marker and not has_explicit_2boat_bulletin:
            mode = "3-boat"
            if service_status == "reduced_2boat":
                service_status = "normal"
            mode_reason = f"Route 14 timetable confirms three-boat schedule for {target_day_name}."

    # Determine VISD target sailings for the target school day
    target_sailings = _determine_target_sailings(
        mode=mode,
        is_friday=is_target_friday,
        weekday=target_weekday,
        v_to_f_scheduled=v_to_f_scheduled
    )
    
    if is_weekend:
        exact_cancelled_f_to_v = bulletins_data.get("monday_exact_cancelled_f_to_v", [])
        exact_cancelled_v_to_f = bulletins_data.get("monday_exact_cancelled_v_to_f", [])
    elif is_next_day:
        target_date_pattern = target_dt.strftime("%m/%d")
        tomorrow_cancels = {"fauntleroy_cancelled": [], "vashon_cancelled": []}
        for k, v in bulletins_data.get("cancellations_by_date", {}).items():
            if target_date_pattern in k:
                tomorrow_cancels = v
                break
        exact_cancelled_f_to_v = [t["time_hhmm"] for t in tomorrow_cancels.get("fauntleroy_cancelled", [])]
        exact_cancelled_v_to_f = [t["time_hhmm"] for t in tomorrow_cancels.get("vashon_cancelled", [])]
    else:
        exact_cancelled_f_to_v = bulletins_data.get("exact_cancelled_f_to_v", [])
        exact_cancelled_v_to_f = bulletins_data.get("exact_cancelled_v_to_f", [])
    
    # Reconcile cancelled_vessels against live AIS telemetry:
    # If a vessel is reporting in_service=True and underway or at dock on the route,
    # and has no upcoming cancellations in exact_cancelled, it has returned to schedule!
    if not is_weekend and not is_next_day and cancelled_vessels:
        current_hhmm = f"{now.hour:02d}:{now.minute:02d}"
        active_cancelled = []
        for v_name in cancelled_vessels:
            live = vessel_map.get(v_name.lower())
            has_upcoming_cancels = False
            for t in exact_cancelled_f_to_v + exact_cancelled_v_to_f:
                if t > current_hhmm:
                    has_upcoming_cancels = True
                    break
            
            if live and live.get("in_service") is True and (live.get("speed_knots", 0) > 0.5 or live.get("at_dock")) and not has_upcoming_cancels:
                if v_name not in restored_vessels:
                    restored_vessels.append(v_name)
            else:
                active_cancelled.append(v_name)
        cancelled_vessels = active_cancelled
        if not cancelled_vessels and service_status == "cancelled_sailings":
            service_status = "normal"
        has_cancelled = bool(cancelled_vessels)
    
    # Evaluate AM commute (Fauntleroy -> Vashon)
    f_to_v_scheduled = schedule.get("fauntleroy_to_vashon", [])
    am_eval = _evaluate_direction_commute(
        direction="Fauntleroy -> Vashon Island",
        scheduled_list=f_to_v_scheduled,
        targets=target_sailings["am"],
        vessel_map=vessel_map,
        is_am=True,
        is_weekend=is_weekend,
        is_next_day=is_next_day,
        target_day_name=target_day_name,
        cancelled_vessels=cancelled_vessels,
        exact_cancelled=exact_cancelled_f_to_v,
        restored_vessels=restored_vessels,
        service_status=service_status,
        disruption_reason=disruption_reason
    )

    # Detect Ghost Ferry (unscheduled walk-on school boat ~7:15-7:45 AM Fauntleroy arrival)
    sched_for_gf = list(f_to_v_scheduled) if f_to_v_scheduled else []
    for tgt_k in ["vhs_mcm", "ces"]:
        if tgt_k in am_eval.get("evaluated_targets", {}):
            tgt = am_eval["evaluated_targets"][tgt_k]
            sched_for_gf.append({
                "time_hhmm": tgt.get("scheduled_time"),
                "vessel_name": tgt.get("vessel_name")
            })
    ghost_ferry_eval = detect_ghost_ferry(
        vessels=vessels,
        now=now,
        target_weekday=target_weekday,
        is_weekend=is_weekend,
        scheduled_sailings=sched_for_gf
    )

    # Order AM targets chronologically: VHS (7:05) -> Ghost Ferry (~7:30) -> CES (8:05)
    ordered_am_targets = {}
    if "vhs_mcm" in am_eval["evaluated_targets"]:
        ordered_am_targets["vhs_mcm"] = am_eval["evaluated_targets"]["vhs_mcm"]
    if mode != "2-boat":
        ordered_am_targets["ghost_ferry"] = ghost_ferry_eval
    for k, v in am_eval["evaluated_targets"].items():
        if k != "vhs_mcm":
            ordered_am_targets[k] = v
    am_eval["evaluated_targets"] = ordered_am_targets
    
    # Evaluate PM commute (Vashon -> Fauntleroy)
    v_to_f_scheduled = schedule.get("vashon_to_fauntleroy", [])
    pm_eval = _evaluate_direction_commute(
        direction="Vashon Island -> Fauntleroy",
        scheduled_list=v_to_f_scheduled,
        targets=target_sailings["pm"],
        vessel_map=vessel_map,
        is_am=False,
        is_weekend=is_weekend,
        is_next_day=is_next_day,
        target_day_name=target_day_name,
        cancelled_vessels=cancelled_vessels,
        exact_cancelled=exact_cancelled_v_to_f,
        restored_vessels=restored_vessels,
        service_status=service_status,
        disruption_reason=disruption_reason
    )
    
    # Ensure PM targets are sorted chronologically by scheduled departure time (e.g. 15:25 -> 16:40 -> 17:45)
    pm_eval["evaluated_targets"] = dict(
        sorted(pm_eval["evaluated_targets"].items(), key=lambda item: item[1].get("scheduled_time", ""))
    )

    # Plain-English Executive Briefing
    briefing = _generate_heuristic_briefing(
        mode=mode,
        mode_reason=mode_reason,
        is_weekend=is_weekend,
        is_friday=is_target_friday,
        now=now,
        target_day_name=target_day_name,
        am_eval=am_eval,
        pm_eval=pm_eval,
        bulletins_data=bulletins_data,
        service_status=service_status,
        cancelled_vessels=cancelled_vessels,
        restored_vessels=restored_vessels,
        disruption_reason=disruption_reason,
        is_next_day=is_next_day
    )

    if has_cancelled:
        vessel_str = ", ".join(cancelled_vessels) if cancelled_vessels else "Sealth"
        mode_display = f"3-Boat ({vessel_str} Cancelled)"
    elif mode == "2-boat":
        mode_display = "2-Boat Contingency Schedule"
    else:
        mode_display = "3-Boat Schedule (Normal)"
    
    try:
        formatted_time = now.strftime("%b %d, %-I:%M %p")
    except Exception:
        formatted_time = now.strftime("%b %d, %I:%M %p").replace(" 0", " ")

    return {
        "timestamp": now.isoformat(),
        "timestamp_epoch": now.timestamp(),
        "current_time_display": formatted_time,
        "is_weekend": is_weekend,
        "is_next_day": is_next_day,
        "current_weekday_name": now.strftime("%A"),
        "target_commute_day": target_day_name,
        "target_commute_title": commute_title,
        "schedule_mode": mode,
        "service_status": service_status,
        "has_cancelled_sailings": has_cancelled,
        "cancelled_vessels": cancelled_vessels,
        "restored_vessels": restored_vessels,
        "mode_display": mode_display,
        "is_friday_pdd": is_target_friday,
        "bulletin_mode_reason": mode_reason,
        "disruption_reason": disruption_reason,
        "executive_briefing": briefing,
        "am_commute": am_eval,
        "pm_commute": pm_eval,
        "vessels_telemetry": vessels,
        "vessels_api_url": VESSELS_API_URL,
        "active_bulletins": bulletins_data.get("triangle_bulletins", []),
        "bulletin_url": bulletins_data.get("bulletin_url", "https://wsdot.com/ferries/schedule/Bulletin.aspx"),
        "adds_cancels_url": bulletins_data.get("adds_cancels_url", "https://wsdot.com/Ferries/Schedule/addcancelbysimpleroute.aspx?routeid=14"),
        "schedule_detail_url": bulletins_data.get("schedule_detail_url", "https://wsdot.com/Ferries/Schedule/scheduledetailbyroute.aspx?route=f-v-s"),
    }


def _determine_target_sailings(
    mode: str,
    is_friday: bool,
    weekday: int,
    v_to_f_scheduled: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Dict[str, str]]:
    """Resolves target sailing times based on mode, Friday PDD, weekday, and published timetable."""
    targets = {"am": {}, "pm": {}}
    v_times = {s.get("time_hhmm") for s in (v_to_f_scheduled or []) if s.get("time_hhmm")}
    
    if mode == "2-boat":
        # 2-Boat Contingency
        if is_friday:
            targets["am"]["vhs_mcm"] = "08:15"
            targets["am"]["ces"] = "09:30"
            targets["am"]["description"] = "Friday Late Start (PDD) 2-Boat Schedule"
        elif weekday == 3:  # Thursday
            targets["am"]["vhs_mcm"] = "06:45"
            targets["am"]["ces"] = "08:15"
            targets["am"]["description"] = "Thursday 2-Boat Schedule (Early VHS/McM)"
        else:  # Monday - Wednesday
            targets["am"]["vhs_mcm"] = "07:20"
            targets["am"]["ces"] = "08:15"
            targets["am"]["description"] = "Regular Mon-Wed 2-Boat Schedule"
            
        # 2-boat PM sailings from Vashon to Fauntleroy are 15:20, 16:45, 17:45
        targets["pm"]["vhs_mcm"] = "15:20" if (not v_times or "15:20" in v_times) else ("15:25" if "15:25" in v_times else "15:20")
        targets["pm"]["ces_sports_bus"] = "16:45" if (not v_times or "16:45" in v_times) else ("16:40" if "16:40" in v_times else "16:45")
        targets["pm"]["sports_bus_late"] = "17:45"
    else:
        # 3-Boat Normal / 3-Boat with Cancelled Vessel
        if is_friday:
            targets["am"]["vhs_mcm"] = "08:25"
            targets["am"]["ces"] = "09:30"
            targets["am"]["description"] = "Friday Late Start (PDD) 3-Boat Schedule"
        else:
            targets["am"]["vhs_mcm"] = "07:05"
            targets["am"]["ces"] = "08:05"
            targets["am"]["description"] = "Regular Mon-Thu 3-Boat Schedule"
            
        targets["pm"]["vhs_mcm"] = "15:25" if (not v_times or "15:25" in v_times) else ("15:20" if "15:20" in v_times else "15:25")
        targets["pm"]["ces_sports_bus"] = "16:40" if (not v_times or "16:40" in v_times) else ("16:45" if "16:45" in v_times else "16:40")
        targets["pm"]["sports_bus_late"] = "17:45"

    return targets


def _find_next_operating_sailing(
    scheduled_list: List[Dict[str, Any]],
    after_hhmm: str,
    cancelled_vessels_lower: List[str],
    exact_cancelled: Optional[List[str]] = None,
    is_am: bool = True
) -> Optional[Dict[str, Any]]:
    """Finds the next sailing departing after after_hhmm that is not on a cancelled vessel or cancelled sailing."""
    after_mins = time_str_to_minutes(after_hhmm)
    exact_cancelled_set = set(exact_cancelled or [])
    
    # 1. Search active scheduled list first
    for s in scheduled_list:
        dep_hhmm = s.get("time_hhmm")
        if not dep_hhmm:
            continue
        if dep_hhmm in exact_cancelled_set:
            continue
        v_name = s.get("vessel_name") or ""
        if v_name.lower() in cancelled_vessels_lower:
            continue
        dep_mins = time_str_to_minutes(dep_hhmm)
        if dep_mins > after_mins:
            arr_mins = dep_mins + STANDARD_CROSSING_MINUTES
            arr_hhmm = minutes_to_time_str(arr_mins)
            return {
                "time": minutes_to_12hr(dep_hhmm),
                "time_hhmm": dep_hhmm,
                "vessel": v_name,
                "arr_time": minutes_to_12hr(arr_hhmm),
                "arr_hhmm": arr_hhmm,
            }
            
    # 2. Fallback to standard 3-boat timetable if scheduled_list was pruned by WSDOT
    fallback_timetable = STANDARD_3_BOAT_F_TO_V if is_am else STANDARD_3_BOAT_V_TO_F
    for dep_hhmm, v_name in fallback_timetable:
        if dep_hhmm in exact_cancelled_set:
            continue
        if v_name.lower() in cancelled_vessels_lower:
            continue
        dep_mins = time_str_to_minutes(dep_hhmm)
        if dep_mins > after_mins:
            arr_mins = dep_mins + STANDARD_CROSSING_MINUTES
            arr_hhmm = minutes_to_time_str(arr_mins)
            return {
                "time": minutes_to_12hr(dep_hhmm),
                "time_hhmm": dep_hhmm,
                "vessel": v_name,
                "arr_time": minutes_to_12hr(arr_hhmm),
                "arr_hhmm": arr_hhmm,
            }
            
    return None


def _evaluate_direction_commute(
    direction: str,
    scheduled_list: List[Dict[str, Any]],
    targets: Dict[str, str],
    vessel_map: Dict[str, Any],
    is_am: bool,
    is_weekend: bool = False,
    is_next_day: bool = False,
    target_day_name: str = "",
    cancelled_vessels: Optional[List[str]] = None,
    exact_cancelled: Optional[List[str]] = None,
    restored_vessels: Optional[List[str]] = None,
    service_status: str = "normal",
    disruption_reason: str = ""
) -> Dict[str, Any]:
    """Evaluates target school sailings against live schedule, vessel telemetry, and cancellations."""
    schedule_by_time = {s.get("time_hhmm"): s for s in scheduled_list if s.get("time_hhmm")}
    cancelled_lower = [v.lower() for v in (cancelled_vessels or [])]
    restored_lower = [v.lower() for v in (restored_vessels or [])]
    exact_cancelled_set = set(exact_cancelled or [])
    
    evaluated_targets = {}
    for key, target_hhmm in targets.items():
        if key == "description":
            continue
            
        sched_entry = schedule_by_time.get(target_hhmm)
        
        # Determine vessel name
        if sched_entry and sched_entry.get("vessel_name"):
            vessel_name = sched_entry.get("vessel_name")
        else:
            # Fallback based on standard 3-boat & 2-boat assignments
            if target_hhmm == "07:20":
                vessel_name = "Cathlamet / Kitsap"
            elif target_hhmm in ["07:05", "15:25", "17:05"]:
                vessel_name = "Sealth"
            elif target_hhmm in ["08:05", "16:40", "15:20", "16:45"]:
                vessel_name = "Kittitas"
            elif target_hhmm in ["08:25", "17:45"]:
                vessel_name = "Kitsap"
            elif target_hhmm == "06:45":
                vessel_name = "Kittitas"
            elif target_hhmm == "08:15":
                vessel_name = "Cathlamet"
            else:
                vessel_name = "Scheduled Vessel"

        delay_minutes = 0
        if target_hhmm == "07:20":
            status = f"Unscheduled Run (VISD Designated for {target_day_name})" if (is_weekend or is_next_day) else "Unscheduled Run (VISD Designated)"
        else:
            status = f"Scheduled for {target_day_name}" if (is_weekend or is_next_day) else "Scheduled On Time"
        catchability = "SAFE"
        is_cancelled = False
        next_sailing = None

        live_vessel = vessel_map.get(vessel_name.lower()) if vessel_name else None

        # Check for vessel cancellation / lack of crew
        if target_hhmm in exact_cancelled_set:
            is_cancelled = True
        elif vessel_name.lower() in cancelled_lower and vessel_name.lower() not in restored_lower:
            # If live telemetry verifies vessel is active in water, it is operating
            if live_vessel and live_vessel.get("in_service") is True and (live_vessel.get("speed_knots", 0) > 0 or live_vessel.get("at_dock")):
                is_cancelled = False
            else:
                is_cancelled = True
        elif sched_entry is None and target_hhmm != "07:20" and (service_status == "cancelled_sailings" or cancelled_vessels):
            # In 3-boat mode, if target sailing is omitted from WSDOT API and on an unrestored cancelled boat
            if vessel_name.lower() in cancelled_lower and vessel_name.lower() not in restored_lower:
                is_cancelled = True

        if live_vessel and not is_weekend and not is_next_day and not is_cancelled:
            if live_vessel.get("in_service") is False:
                # If explicitly marked out of service and matches cancellation alert
                if vessel_name.lower() in cancelled_lower and vessel_name.lower() not in restored_lower:
                    is_cancelled = True

        if is_cancelled:
            reason_label = disruption_reason or "Crew Shortage"
            status = f"Out of Service ({reason_label})"
            catchability = "MISSED"
            next_sailing = _find_next_operating_sailing(
                scheduled_list, target_hhmm, cancelled_lower, exact_cancelled=exact_cancelled, is_am=is_am
            )

            if is_am:
                if key == "vhs_mcm":
                    if next_sailing:
                        bus_note = (
                            f"⚠️ No 7:05 AM boat ({reason_label}). "
                            f"Next available departure is {next_sailing['time']} ({next_sailing['vessel']}), "
                            f"arriving Vashon ~{next_sailing['arr_time']} (misses 7:35 AM bus)."
                        )
                    else:
                        bus_note = f"⚠️ No 7:05 AM boat ({reason_label}). Normal 7:35 AM bus connection will be missed."
                elif key == "ces":
                    if next_sailing:
                        bus_note = f"⚠️ No 8:05 AM boat ({reason_label}). Next available boat is {next_sailing['time']} ({next_sailing['vessel']})."
                    else:
                        bus_note = f"⚠️ No 8:05 AM boat ({reason_label})."
                else:
                    bus_note = f"⚠️ No {minutes_to_12hr(target_hhmm)} boat ({reason_label})."
            else:
                if key == "vhs_mcm":
                    if next_sailing:
                        bus_note = (
                            f"⚠️ No 3:25 PM dismissal boat ({reason_label}). "
                            f"Next departure to Fauntleroy: {next_sailing['time']} ({next_sailing['vessel']}), "
                            f"arriving Fauntleroy ~{next_sailing['arr_time']}."
                        )
                    else:
                        bus_note = f"⚠️ No 3:25 PM dismissal boat ({reason_label})."
                else:
                    if next_sailing:
                        bus_note = f"⚠️ No {minutes_to_12hr(target_hhmm)} boat ({reason_label}). Next departure: {next_sailing['time']} ({next_sailing['vessel']})."
                    else:
                        bus_note = f"⚠️ No {minutes_to_12hr(target_hhmm)} boat ({reason_label})."
        else:
            # Not cancelled: compute live delays and connection catchability
            if not is_weekend and not is_next_day and live_vessel:
                if live_vessel.get("depart_delayed"):
                    delay_minutes = 10
                    status = "Delayed (Flagged by WSDOT)"
                elif live_vessel.get("at_dock") and live_vessel.get("speed_knots", 0) == 0:
                    status = "At Dock (Loading/Boarding)"
                elif live_vessel.get("speed_knots", 0) > 2:
                    status = f"Underway ({live_vessel.get('speed_knots')} kts)"

            sched_mins = time_str_to_minutes(target_hhmm)
            est_dep_mins = sched_mins + delay_minutes
            est_arr_mins = est_dep_mins + STANDARD_CROSSING_MINUTES
            
            est_dep_hhmm = minutes_to_time_str(est_dep_mins)
            est_arr_hhmm = minutes_to_time_str(est_arr_mins)

            if is_am:
                prefix = "Monday Morning: " if is_weekend else (f"{target_day_name} Morning: " if is_next_day else "")
                if key == "vhs_mcm":
                    if target_hhmm == "07:20":
                        cutoff_mins = time_str_to_minutes("07:45")
                        catchability = "SAFE" if est_arr_mins <= cutoff_mins else ("TIGHT" if est_arr_mins <= cutoff_mins + 5 else "AT_RISK")
                        bus_note = (
                            f"{prefix}Arrives Vashon ~{minutes_to_12hr(est_arr_hhmm)}. "
                            f"VISD school bus meets dock connection (~{minutes_to_12hr(minutes_to_time_str(cutoff_mins))}). "
                            f"(Unscheduled hazmat run; walk-ons permitted Mon–Wed)."
                        )
                    elif target_hhmm == "06:45":
                        cutoff_mins = time_str_to_minutes("07:15")
                        catchability = "SAFE" if est_arr_mins <= cutoff_mins else "TIGHT"
                        bus_note = (
                            f"{prefix}Arrives Vashon ~{minutes_to_12hr(est_arr_hhmm)}. "
                            f"Meets early Thursday 2-boat school bus at dock. "
                            f"(Required on Thursdays: 7:20 AM boat is USCG closed-hazmat with strictly no walk-ons)."
                        )
                    elif target_hhmm == "08:15":
                        cutoff_mins = time_str_to_minutes("08:40")
                        catchability = "SAFE" if est_arr_mins <= cutoff_mins else "TIGHT"
                        bus_note = f"{prefix}Arrives Vashon ~{minutes_to_12hr(est_arr_hhmm)}. Friday Late Start (PDD) bus meets dock at {minutes_to_12hr(minutes_to_time_str(cutoff_mins))}."
                    else:
                        cutoff_mins = time_str_to_minutes("07:35") if target_hhmm == "07:05" else time_str_to_minutes("08:55")
                        catchability = "SAFE" if est_arr_mins <= cutoff_mins else ("TIGHT" if est_arr_mins <= cutoff_mins + 5 else "AT_RISK")
                        bus_note = f"{prefix}Arrives Vashon ~{minutes_to_12hr(est_arr_hhmm)}. School bus meets dock at {minutes_to_12hr(minutes_to_time_str(cutoff_mins))}."
                elif key == "ces":
                    if target_hhmm == "08:15":
                        cutoff_mins = time_str_to_minutes("08:40")
                        catchability = "SAFE" if est_arr_mins <= cutoff_mins else "TIGHT"
                        bus_note = f"{prefix}Arrives Vashon ~{minutes_to_12hr(est_arr_hhmm)}. Chaperone accompanied. CES bus meets dock at {minutes_to_12hr(minutes_to_time_str(cutoff_mins))}."
                    elif target_hhmm == "09:30":
                        cutoff_mins = time_str_to_minutes("10:00")
                        catchability = "SAFE" if est_arr_mins <= cutoff_mins else "TIGHT"
                        bus_note = f"{prefix}Arrives Vashon ~{minutes_to_12hr(est_arr_hhmm)}. Friday Late Start (PDD) chaperone bus waiting at dock."
                    else:
                        cutoff_mins = time_str_to_minutes("08:35") if target_hhmm == "08:05" else time_str_to_minutes("10:00")
                        catchability = "SAFE" if est_arr_mins <= cutoff_mins else "TIGHT"
                        bus_note = f"{prefix}Arrives Vashon ~{minutes_to_12hr(est_arr_hhmm)}. Chaperone accompanied. Bus waiting at {minutes_to_12hr(minutes_to_time_str(cutoff_mins))}."
                else:
                    bus_note = f"{prefix}Standard commute sailing."
            else:
                if key == "ces_sports_bus":
                    bus_note = f"CES dismissal bus (chaperoned) and after-school sports bus connect with {minutes_to_12hr(target_hhmm)} departure (arrives Fauntleroy ~{minutes_to_12hr(est_arr_hhmm)})."
                elif key == "sports_bus_late":
                    bus_note = f"After-school late activities & practice bus drops students at dock for {minutes_to_12hr(target_hhmm)} departure (arrives Fauntleroy ~{minutes_to_12hr(est_arr_hhmm)})."
                elif key == "vhs_mcm":
                    bus_note = f"Regular afternoon dismissal bus from school to dock connects with {minutes_to_12hr(target_hhmm)} departure (arrives Fauntleroy ~{minutes_to_12hr(est_arr_hhmm)})."
                else:
                    bus_note = "Regular afternoon dismissal bus from school to dock."

        sched_mins = time_str_to_minutes(target_hhmm)
        est_dep_mins = sched_mins + delay_minutes
        est_arr_mins = est_dep_mins + STANDARD_CROSSING_MINUTES
        est_dep_hhmm = minutes_to_time_str(est_dep_mins)
        est_arr_hhmm = minutes_to_time_str(est_arr_mins)

        sched_display = "7:20 AM (Unscheduled)" if target_hhmm == "07:20" else minutes_to_12hr(target_hhmm)

        evaluated_targets[key] = {
            "scheduled_time": target_hhmm,
            "scheduled_time_display": sched_display,
            "estimated_departure": est_dep_hhmm,
            "estimated_departure_display": minutes_to_12hr(est_dep_hhmm),
            "estimated_arrival": est_arr_hhmm,
            "estimated_arrival_display": minutes_to_12hr(est_arr_hhmm),
            "delay_minutes": delay_minutes,
            "vessel_name": vessel_name,
            "status": status,
            "catchability": catchability,
            "bus_note": bus_note,
            "is_cancelled": is_cancelled,
            "next_sailing": next_sailing,
        }
        
    return {
        "direction": direction,
        "description": targets.get("description", ""),
        "evaluated_targets": evaluated_targets,
        "all_scheduled_count": len(scheduled_list),
        "scheduled_sailings": scheduled_list,
    }


def _generate_heuristic_briefing(
    mode: str,
    mode_reason: str,
    is_weekend: bool,
    is_friday: bool,
    now: datetime,
    target_day_name: str,
    am_eval: Dict[str, Any],
    pm_eval: Dict[str, Any],
    bulletins_data: Dict[str, Any],
    service_status: str = "normal",
    cancelled_vessels: Optional[List[str]] = None,
    restored_vessels: Optional[List[str]] = None,
    disruption_reason: str = "",
    is_next_day: bool = False
) -> str:
    """Synthesizes a concise, plain-English executive briefing accurately reflecting operating status."""
    vessel_names = ", ".join(cancelled_vessels) if cancelled_vessels else "Sealth"
    reason = disruption_reason or "lack of crew"

    if is_weekend:
        if cancelled_vessels:
            return (
                f"🏖️ **Weekend Preview (Showing Monday Morning Commute)**: School is not in session today ({now.strftime('%A')}). "
                f"Route operates on the 3-boat timetable for Monday. Note: M/V {vessel_names} has cancelled sailings due to {reason.lower()} "
                f"unless relief is secured; remaining vessels follow normal 3-boat schedule."
            )
        mode_desc = "normal three-boat schedule" if mode == "3-boat" else "two-boat reduced schedule"
        return f"🏖️ **Weekend Preview (Showing Monday Morning Commute)**: School is not in session today ({now.strftime('%A')}). Route operates on the {mode_desc} on Monday with safe school bus connections."

    if is_next_day:
        parts = []
        if cancelled_vessels:
            parts.append(
                f"🌅 **Next Day Preview (Showing {target_day_name} Commute)**: Today's school commute is complete. "
                f"Service for tomorrow ({target_day_name}) has cancelled sailings announced for M/V {vessel_names} due to {reason.lower()}."
            )
        elif mode == "2-boat":
            parts.append(
                f"🌅 **Next Day Preview (Showing {target_day_name} Commute)**: Today's school commute is complete. "
                f"Route is scheduled for the published two-boat contingency timetable tomorrow ({target_day_name})."
            )
        else:
            parts.append(
                f"🌅 **Next Day Preview (Showing {target_day_name} Commute)**: Today's school commute is complete. "
                f"Route operates on the normal three-boat schedule tomorrow ({target_day_name}) with safe school bus connections."
            )
        
        if is_friday:
            parts.append("🎉 **Friday Late Start (PDD)**: Target departures shift later for delayed school start.")
        elif target_day_name == "Thursday":
            parts.append("⚠️ **Thursday Hazmat Note**: Morning unlisted boats typically transport hazardous cargo and cannot board walk-on passengers.")
        return " ".join(parts)

    parts = []
    ghost_target = am_eval["evaluated_targets"].get("ghost_ferry", {})

    if cancelled_vessels:
        msg = (
            f"⚠️ **Crew Shortage Alert (3-Boat Schedule with Cancelled Sailings)**: "
            f"The M/V {vessel_names} is out of service due to {reason.lower()}, cancelling all #3 sailings. "
            f"The route continues on the **3-boat schedule** with Kittitas and Kitsap (NOT an official 2-boat schedule change). "
            f"The **7:05 AM Fauntleroy departure is CANCELLED**; next available boat is the **8:05 AM (Kittitas)**. "
            f"The 8:05 AM CES elementary sailing is running on time."
        )
        if ghost_target.get("is_detected") and not ghost_target.get("is_thursday_hazmat"):
            msg += (
                f" ⚠️ **Unscheduled Ferry Detected (Not Recommended)**: {ghost_target.get('vessel_name')} detected at/operating Fauntleroy "
                f"(~{ghost_target.get('estimated_departure_display')})—this run is not an official VISD school sailing and is not recommended."
            )
        parts.append(msg)
        return " ".join(parts)

    if restored_vessels:
        restored_str = ", ".join(restored_vessels)
        return (
            f"✅ **Three-Boat Schedule Restored**: Route has returned to the full three-boat schedule. "
            f"The M/V {restored_str} is in service following earlier morning crew shortage cancellations. "
            f"Afternoon and evening commute sailings (3:25 PM VHS dismissal, 4:40 PM CES & Sports Bus, and 5:45 PM Late Sports Bus) are operating on schedule."
        )

    if mode == "2-boat":
        parts.append("⚠️ **Two-Boat Schedule Active**: Washington State Ferries has reduced service to the published two-boat contingency timetable.")
    else:
        parts.append("✅ **Normal Three-Boat Schedule Active**: All three vessels are running.")

    if ghost_target.get("is_detected"):
        if ghost_target.get("is_thursday_hazmat"):
            parts.append(
                f"⚠️ **Thursday Hazmat Advisory**: Unscheduled vessel ({ghost_target.get('vessel_name')}) detected at Fauntleroy "
                f"(~{ghost_target.get('estimated_departure_display')}), but Thursday morning runs typically carry hazardous cargo (fuel/propane). "
                f"Walk-on passengers are prohibited under Coast Guard regulations."
            )
        else:
            parts.append(
                f"⚠️ **Unscheduled Ferry Detected (Not Recommended)**: {ghost_target.get('vessel_name')} is at/navigating Fauntleroy dock "
                f"(~{ghost_target.get('estimated_departure_display')}). This unscheduled run does not take vehicles and is not an official VISD school commute sailing."
            )
    elif target_day_name == "Thursday" and not is_weekend:
        parts.append("⚠️ **Thursday Hazmat Note**: Morning unlisted boats typically transport hazardous cargo and cannot board walk-on passengers.")

    if is_friday:
        parts.append("🎉 **Friday Late Start (PDD)**: Target departures shift later for delayed school start.")
    else:
        vhs_target_obj = am_eval["evaluated_targets"].get("vhs_mcm", {})
        delay = vhs_target_obj.get("delay_minutes", 0)
        if delay > 0:
            parts.append(f"⏱️ **Delay Alert**: Morning sailing is delayed ~{delay} min ({vhs_target_obj.get('status', 'delayed')}).")
        else:
            parts.append("Morning commute sailings are tracking on time with safe bus connections at Vashon dock.")

    return " ".join(parts)
