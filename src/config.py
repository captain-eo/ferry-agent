"""
Configuration and constants for the Fauntleroy-to-Vashon School Commute Ferry Tracker.
Contains WSDOT endpoints, terminal IDs, and Vashon Island School District (VISD)
commute rules (including Friday PDD late start and sports/after-school buses).
"""

import os
from typing import Dict, Any

# WSDOT Endpoints
WSDOT_VESSEL_LOCATIONS_URL = "https://www.wsdot.wa.gov/Ferries/API/Vessels/rest/vessellocations"
WSDOT_VESSEL_WATCH_URL = "https://wsdot.com/ferries/vesselwatch/Vessels.ashx"
WSDOT_SCHEDULE_API_URL = "https://www.wsdot.wa.gov/Ferries/API/Schedule/rest/schedule/{date}/14"
WSDOT_BULLETIN_URL = "https://wsdot.com/ferries/schedule/Bulletin.aspx"
WSDOT_ADDS_CANCELS_URL = "https://wsdot.com/Ferries/Schedule/addcancelbysimpleroute.aspx?routeid=14"
WSDOT_SCHEDULE_DETAIL_URL = "https://wsdot.com/Ferries/Schedule/scheduledetailbyroute.aspx?route=f-v-s"

# Terminals
TERMINAL_FAUNTLEROY_ID = 9
TERMINAL_VASHON_ID = 22
TERMINAL_SOUTHWORTH_ID = 20

ROUTE_ID_FVS = 14  # Fauntleroy / Vashon
OP_ROUTE_FVS = "f-v-s"

# Crossing time estimate (minutes) between Fauntleroy & Vashon
STANDARD_CROSSING_MINUTES = 20

# Gemini Settings
DEFAULT_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Commute Schedule Configuration (Pacific Time / America/Los_Angeles)
COMMUTE_SCHEDULE = {
    # Monday - Friday Morning Commute: 6:00 AM - 9:15 AM
    "am_start": (6, 0),
    "am_end": (9, 15),
    # Monday - Friday Afternoon & Sports Return: 2:30 PM - 6:00 PM
    "pm_start": (14, 30),
    "pm_end": (18, 0),
    "offpeak_interval_minutes": 15,
}

# AWS Settings
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "vashon-ferry-commute")
VESSELS_API_URL = ""

# VISD Commuter Schedule Rules
VISD_COMMUTE_RULES: Dict[str, Any] = {
    "schools": {
        "vhs_mcm": {
            "name": "Vashon High & McMurray Middle",
            "grades": "6-12",
            "normal_start": "08:00 AM",
            "pdd_start": "09:15 AM",  # Friday late start
            "bus_wait_dock_am": "07:35 AM",
        },
        "ces": {
            "name": "Chautauqua Elementary",
            "grades": "K-5",
            "normal_start": "08:50 AM",
            "pdd_start": "10:15 AM",  # Friday late start
            "chaperone": True,
            "bus_wait_dock_am": "08:35 AM",
        }
    },
    "sailings": {
        "3_boat": {
            "name": "Three-Boat Schedule (Normal)",
            "am_fauntleroy_to_vashon": {
                # Regular Monday-Thursday
                "vhs_mcm_regular": "07:05",
                "ces_regular": "08:05",
                # Friday Late Start (PDD)
                "vhs_mcm_friday_pdd": "08:25",
                "ces_friday_pdd": "09:30",
            },
            "pm_vashon_to_fauntleroy": {
                # Regular dismissal
                "vhs_mcm_regular": "15:25",
                "ces_regular": "16:40",
                # Sports / Activity Late Bus
                "vhs_mcm_sports_bus_1": "16:40",  # Connects with late activity bus (shares boat with CES)
                "vhs_mcm_sports_bus_2": "17:45",  # Later sports practice / varsity games
                # Early dismissal days
                "vhs_mcm_early_dismissal": "11:20",
                "ces_early_dismissal": "12:20",
            }
        },
        "2_boat": {
            "name": "Two-Boat Schedule (Reduced Service)",
            "am_fauntleroy_to_vashon": {
                # Regular Monday-Wednesday
                "vhs_mcm_mon_wed": "07:20",
                # Regular Thursday (due to maintenance rotation)
                "vhs_mcm_thu": "06:45",
                "ces_regular": "08:15",
                # Friday Late Start (PDD)
                "vhs_mcm_friday_pdd": "08:15",
                "ces_friday_pdd": "09:30",
            },
            "pm_vashon_to_fauntleroy": {
                "vhs_mcm_regular": "15:25",
                "ces_regular": "16:40",
                "vhs_mcm_sports_bus_1": "16:40",
                "vhs_mcm_sports_bus_2": "17:45",
                "vhs_mcm_early_dismissal": "11:20",
                "ces_early_dismissal": "12:20",
            }
        }
    }
}
