"""
Deterministic Heuristic Analyzer for Fauntleroy-Vashon Commute.
Evaluates scheduled sailings, live boat positions, delays, and bus catchability.
Detects Saturday and Sunday lookups to automatically target Monday morning sailings.
"""

from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional

from src.config import VISD_COMMUTE_RULES, STANDARD_CROSSING_MINUTES


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


def evaluate_commute(
    telemetry: Dict[str, Any],
    bulletins_data: Dict[str, Any],
    simulated_time: Optional[datetime] = None
) -> Dict[str, Any]:
    """
    Evaluates commute status using deterministic rules and real-time feeds.
    When invoked on Saturday or Sunday, automatically shows Monday morning as target sailings.
    """
    now = simulated_time or get_current_pacific_time()
    weekday = now.weekday()  # 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
    is_weekend = (weekday in (5, 6))
    
    # Target day logic
    if is_weekend:
        target_day_name = "Monday"
        is_target_friday = False
        target_weekday = 0
        # Use Monday's schedule mode if announced in bulletins
        mode = bulletins_data.get("monday_schedule_mode", bulletins_data.get("schedule_mode", "3-boat"))
        mode_reason = bulletins_data.get("monday_mode_reason", bulletins_data.get("mode_reason", ""))
        commute_title = "Monday Morning Commute (Next School Day)"
    else:
        target_day_name = now.strftime("%A")
        is_target_friday = (weekday == 4)
        target_weekday = weekday
        mode = bulletins_data.get("schedule_mode", "3-boat")
        mode_reason = bulletins_data.get("mode_reason", "")
        commute_title = f"{target_day_name} School Commute"

    vessels = telemetry.get("vessels", [])
    schedule = telemetry.get("schedule", {})
    
    # Map vessels by name for lookup
    vessel_map = {v.get("name", "").lower(): v for v in vessels}
    
    # Determine VISD target sailings for the target school day
    target_sailings = _determine_target_sailings(mode, is_target_friday, target_weekday)
    
    # Evaluate AM commute (Fauntleroy -> Vashon)
    f_to_v_scheduled = schedule.get("fauntleroy_to_vashon", [])
    am_eval = _evaluate_direction_commute(
        direction="Fauntleroy -> Vashon Island",
        scheduled_list=f_to_v_scheduled,
        targets=target_sailings["am"],
        vessel_map=vessel_map,
        is_am=True,
        is_weekend=is_weekend
    )
    
    # Evaluate PM commute (Vashon -> Fauntleroy)
    v_to_f_scheduled = schedule.get("vashon_to_fauntleroy", [])
    pm_eval = _evaluate_direction_commute(
        direction="Vashon Island -> Fauntleroy",
        scheduled_list=v_to_f_scheduled,
        targets=target_sailings["pm"],
        vessel_map=vessel_map,
        is_am=False,
        is_weekend=is_weekend
    )
    
    # Generate Plain-English Briefing
    briefing = _generate_heuristic_briefing(
        mode=mode,
        mode_reason=mode_reason,
        is_weekend=is_weekend,
        is_friday=is_target_friday,
        now=now,
        target_day_name=target_day_name,
        am_eval=am_eval,
        pm_eval=pm_eval,
        bulletins_data=bulletins_data
    )
    
    return {
        "timestamp": now.isoformat(),
        "timestamp_epoch": now.timestamp(),
        "current_time_display": now.strftime("%A, %b %d at %I:%M %p"),
        "is_weekend": is_weekend,
        "current_weekday_name": now.strftime("%A"),
        "target_commute_day": target_day_name,
        "target_commute_title": commute_title,
        "schedule_mode": mode,
        "is_friday_pdd": is_target_friday,
        "bulletin_mode_reason": mode_reason,
        "executive_briefing": briefing,
        "am_commute": am_eval,
        "pm_commute": pm_eval,
        "vessels_telemetry": vessels,
        "active_bulletins": bulletins_data.get("triangle_bulletins", []),
    }


def _determine_target_sailings(mode: str, is_friday: bool, weekday: int) -> Dict[str, Dict[str, str]]:
    """Resolves target sailing times based on mode, Friday PDD, and weekday."""
    targets = {"am": {}, "pm": {}}
    
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
            
        targets["pm"]["vhs_mcm"] = "15:25"
        targets["pm"]["ces"] = "16:40"
        targets["pm"]["sports_bus"] = "16:40"
        targets["pm"]["sports_bus_late"] = "17:40"
    else:
        # 3-Boat Normal
        if is_friday:
            targets["am"]["vhs_mcm"] = "08:25"
            targets["am"]["ces"] = "09:30"
            targets["am"]["description"] = "Friday Late Start (PDD) 3-Boat Schedule"
        else:
            targets["am"]["vhs_mcm"] = "07:05"
            targets["am"]["ces"] = "08:05"
            targets["am"]["description"] = "Regular Mon-Thu 3-Boat Schedule"
            
        targets["pm"]["vhs_mcm"] = "15:25"
        targets["pm"]["ces"] = "16:40"
        targets["pm"]["sports_bus"] = "16:40"
        targets["pm"]["sports_bus_late"] = "17:40"

    return targets


def _evaluate_direction_commute(
    direction: str,
    scheduled_list: List[Dict[str, Any]],
    targets: Dict[str, str],
    vessel_map: Dict[str, Any],
    is_am: bool,
    is_weekend: bool = False
) -> Dict[str, Any]:
    """Evaluates target school sailings against live schedule and vessel telemetry."""
    schedule_by_time = {s.get("time_hhmm"): s for s in scheduled_list if s.get("time_hhmm")}
    
    evaluated_targets = {}
    for key, target_hhmm in targets.items():
        if key == "description":
            continue
            
        sched_entry = schedule_by_time.get(target_hhmm)
        vessel_name = sched_entry.get("vessel_name") if sched_entry else "Scheduled Vessel"
        
        delay_minutes = 0
        status = "Scheduled On Time"
        catchability = "SAFE"
        
        # On weekends, real-time telemetry applies to active weekend boats, so target Monday sailings are "Scheduled"
        if not is_weekend:
            live_vessel = vessel_map.get(vessel_name.lower()) if vessel_name else None
            if live_vessel:
                if live_vessel.get("depart_delayed"):
                    delay_minutes = 10
                    status = "Delayed (Flagged by WSDOT)"
                elif live_vessel.get("at_dock") and live_vessel.get("speed_knots", 0) == 0:
                    status = "At Dock (Loading/Boarding)"
                elif live_vessel.get("speed_knots", 0) > 2:
                    status = f"Underway ({live_vessel.get('speed_knots')} kts)"
        else:
            status = "Scheduled for Monday"
                
        # Estimate departure & arrival
        sched_mins = time_str_to_minutes(target_hhmm)
        est_dep_mins = sched_mins + delay_minutes
        est_arr_mins = est_dep_mins + STANDARD_CROSSING_MINUTES
        
        est_dep_hhmm = minutes_to_time_str(est_dep_mins)
        est_arr_hhmm = minutes_to_time_str(est_arr_mins)
        
        # Bus connection analysis
        if is_am:
            if key == "vhs_mcm":
                cutoff_mins = time_str_to_minutes("07:35") if target_hhmm in ["07:05", "07:20", "06:45"] else time_str_to_minutes("08:55")
                catchability = "SAFE" if est_arr_mins <= cutoff_mins else ("TIGHT" if est_arr_mins <= cutoff_mins + 5 else "AT_RISK")
                prefix = "Monday Morning: " if is_weekend else ""
                bus_note = f"{prefix}Arrives Vashon ~{minutes_to_12hr(est_arr_hhmm)}. School bus meets dock at {minutes_to_12hr(minutes_to_time_str(cutoff_mins))}."
            elif key == "ces":
                cutoff_mins = time_str_to_minutes("08:35") if target_hhmm in ["08:05", "08:15"] else time_str_to_minutes("10:00")
                catchability = "SAFE" if est_arr_mins <= cutoff_mins else "TIGHT"
                prefix = "Monday Morning: " if is_weekend else ""
                bus_note = f"{prefix}Arrives Vashon ~{minutes_to_12hr(est_arr_hhmm)}. Chaperone accompanied. Bus waiting at {minutes_to_12hr(minutes_to_time_str(cutoff_mins))}."
            else:
                bus_note = "Standard commute sailing."
        else:
            if "sports" in key:
                bus_note = "Monday PM: After-school sports bus drops students at north-end dock."
            else:
                bus_note = "Monday PM: Regular afternoon dismissal bus from school to dock."

        evaluated_targets[key] = {
            "scheduled_time": target_hhmm,
            "scheduled_time_display": minutes_to_12hr(target_hhmm),
            "estimated_departure": est_dep_hhmm,
            "estimated_departure_display": minutes_to_12hr(est_dep_hhmm),
            "estimated_arrival": est_arr_hhmm,
            "estimated_arrival_display": minutes_to_12hr(est_arr_hhmm),
            "delay_minutes": delay_minutes,
            "vessel_name": vessel_name,
            "status": status,
            "catchability": catchability,
            "bus_note": bus_note,
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
    bulletins_data: Dict[str, Any]
) -> str:
    """Synthesizes human-friendly executive briefing paragraph."""
    lines = []
    
    if is_weekend:
        lines.append(f"🏖️ **Weekend Preview (Showing Monday Morning Commute)**: School is not in session today ({now.strftime('%A')}). Below are the target sailings you need for **Monday morning**.")
        
        if mode == "3-boat":
            lines.append("✅ **Monday Schedule Status**: Route will operate on the **Three-Boat Schedule (Normal)**. " + mode_reason)
        else:
            lines.append("⚠️ **Monday Schedule Status**: Route is scheduled for **Two-Boat Reduced Service**. " + mode_reason)

        vhs_target = am_eval["evaluated_targets"].get("vhs_mcm", {}).get("scheduled_time_display", "7:05 AM")
        ces_target = am_eval["evaluated_targets"].get("ces", {}).get("scheduled_time_display", "8:05 AM")
        lines.append(f"🎒 **Monday Commute Targets**: High & Middle School (VHS/McMurray) target is **{vhs_target}**; Elementary (CES) target is **{ces_target}** from Fauntleroy.")
        lines.append("🏅 **Monday Sports & Activities**: Students participating in Monday sports or clubs will take the **4:40 PM** return ferry.")
    else:
        if mode == "2-boat":
            lines.append("⚠️ **Route Operating on Two-Boat Schedule**: Washington State Ferries has reduced service on the Fauntleroy/Vashon/Southworth route.")
        else:
            lines.append("✅ **Route Operating on Normal Three-Boat Schedule**: Full three-vessel service is active.")
            
        if is_friday:
            lines.append("🎉 **Friday Late Start (PDD)**: Students start school later today. Commuters should take the **8:25 AM** (VHS/McMurray) or **9:30 AM** (CES) ferry from Fauntleroy.")
        else:
            vhs_target = am_eval["evaluated_targets"].get("vhs_mcm", {}).get("scheduled_time_display", "7:05 AM")
            ces_target = am_eval["evaluated_targets"].get("ces", {}).get("scheduled_time_display", "8:05 AM")
            lines.append(f"📅 **Regular Weekday Commute ({target_day_name})**: High/Middle school target is **{vhs_target}**; Elementary target is **{ces_target}**.")
            
        lines.append("🏅 **Sports & After-School Activities**: Students participating in sports or extracurriculars take the **4:40 PM** ferry (or 5:40 PM for late practice) returning to Fauntleroy.")

        vhs_target_obj = am_eval["evaluated_targets"].get("vhs_mcm", {})
        if vhs_target_obj.get("delay_minutes", 0) > 0:
            lines.append(f"⏱️ **Delay Alert**: Morning sailing {vhs_target_obj.get('scheduled_time_display')} is experiencing ~{vhs_target_obj.get('delay_minutes')} mins delay ({vhs_target_obj.get('status')}).")
        else:
            lines.append("🚢 **Vessel Status**: Morning commute sailings are tracking on time with safe bus connections.")

    return "\n\n".join(lines)
