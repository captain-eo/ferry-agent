"""
Unit and Integration Tests for Ferry Agent.
Tests API parsing, Friday PDD late start logic, sports bus logic,
weekend (Saturday/Sunday) lookahead to Monday morning, and static site generator.
"""

import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from src.collectors.wsdot_api import parse_dot_net_date, parse_time_hh_mm, get_target_school_commute_date
from src.analyzer.heuristic_analyzer import evaluate_commute
from src.generator.site_generator import generate_static_site


class TestFerryCommutePipeline(unittest.TestCase):

    def test_dot_net_date_parsing(self):
        raw = "/Date(1789311600000-0700)/"
        iso = parse_dot_net_date(raw)
        self.assertIsNotNone(iso)
        self.assertIn("T", iso)
        
        hhmm = parse_time_hh_mm(raw)
        self.assertIsNotNone(hhmm)
        self.assertEqual(len(hhmm), 5)

    def test_friday_pdd_late_start_detection(self):
        # Create simulated Friday (Sept 18, 2026 is a Friday)
        friday_dt = datetime(2026, 9, 18, 6, 30, tzinfo=timezone(timedelta(hours=-7)))
        self.assertEqual(friday_dt.weekday(), 4)  # 4 = Friday

        dummy_telemetry = {
            "vessels": [],
            "schedule": {
                "fauntleroy_to_vashon": [{"time_hhmm": "08:25", "vessel_name": "Kittitas"}],
                "vashon_to_fauntleroy": [{"time_hhmm": "16:40", "vessel_name": "Kitsap"}]
            }
        }
        dummy_bulletins = {"schedule_mode": "3-boat", "triangle_bulletins": []}

        eval_res = evaluate_commute(dummy_telemetry, dummy_bulletins, simulated_time=friday_dt)
        self.assertTrue(eval_res["is_friday_pdd"])
        self.assertFalse(eval_res["is_weekend"])
        
        am_targets = eval_res["am_commute"]["evaluated_targets"]
        self.assertEqual(am_targets["vhs_mcm"]["scheduled_time"], "08:25")
        self.assertEqual(am_targets["ces"]["scheduled_time"], "09:30")
        self.assertIn("Friday Late Start", eval_res["executive_briefing"])

    def test_weekend_lookahead_saturday(self):
        # Saturday: Sept 19, 2026
        saturday_dt = datetime(2026, 9, 19, 11, 0, tzinfo=timezone(timedelta(hours=-7)))
        self.assertEqual(saturday_dt.weekday(), 5) # 5 = Saturday

        info = get_target_school_commute_date(saturday_dt)
        self.assertTrue(info["is_weekend"])
        self.assertEqual(info["target_weekday_name"], "Monday")
        self.assertEqual(info["target_date_str"], "2026-09-21")

        dummy_telemetry = {
            "vessels": [],
            "schedule": {
                "fauntleroy_to_vashon": [
                    {"time_hhmm": "07:05", "vessel_name": "Kittitas"},
                    {"time_hhmm": "08:05", "vessel_name": "Kitsap"}
                ],
                "vashon_to_fauntleroy": []
            }
        }
        dummy_bulletins = {"schedule_mode": "3-boat", "monday_schedule_mode": "3-boat", "triangle_bulletins": []}

        eval_res = evaluate_commute(dummy_telemetry, dummy_bulletins, simulated_time=saturday_dt)
        self.assertTrue(eval_res["is_weekend"])
        self.assertEqual(eval_res["target_commute_day"], "Monday")
        am_targets = eval_res["am_commute"]["evaluated_targets"]
        self.assertEqual(am_targets["vhs_mcm"]["scheduled_time"], "07:05")
        self.assertEqual(am_targets["ces"]["scheduled_time"], "08:05")
        self.assertIn("Weekend Preview (Showing Monday Morning Commute)", eval_res["executive_briefing"])

    def test_weekend_lookahead_sunday(self):
        # Sunday: Sept 20, 2026
        sunday_dt = datetime(2026, 9, 20, 16, 0, tzinfo=timezone(timedelta(hours=-7)))
        self.assertEqual(sunday_dt.weekday(), 6) # 6 = Sunday

        info = get_target_school_commute_date(sunday_dt)
        self.assertTrue(info["is_weekend"])
        self.assertEqual(info["target_weekday_name"], "Monday")
        self.assertEqual(info["target_date_str"], "2026-09-21")

        dummy_telemetry = {
            "vessels": [],
            "schedule": {
                "fauntleroy_to_vashon": [
                    {"time_hhmm": "07:20", "vessel_name": "Kittitas"},
                    {"time_hhmm": "08:15", "vessel_name": "Kitsap"}
                ],
                "vashon_to_fauntleroy": []
            }
        }
        # Suppose weekend is 2-boat and Monday remains 2-boat
        dummy_bulletins = {
            "schedule_mode": "2-boat",
            "monday_schedule_mode": "2-boat",
            "monday_mode_reason": "Maintenance continues through Monday",
            "triangle_bulletins": []
        }

        eval_res = evaluate_commute(dummy_telemetry, dummy_bulletins, simulated_time=sunday_dt)
        self.assertTrue(eval_res["is_weekend"])
        self.assertEqual(eval_res["target_commute_day"], "Monday")
        am_targets = eval_res["am_commute"]["evaluated_targets"]
        self.assertEqual(am_targets["vhs_mcm"]["scheduled_time"], "07:20")
        self.assertEqual(am_targets["ces"]["scheduled_time"], "08:15")

    def test_sports_bus_logic(self):
        regular_dt = datetime(2026, 9, 15, 14, 0, tzinfo=timezone(timedelta(hours=-7))) # Tuesday
        dummy_telemetry = {
            "vessels": [],
            "schedule": {
                "fauntleroy_to_vashon": [],
                "vashon_to_fauntleroy": [
                    {"time_hhmm": "15:25", "vessel_name": "Kittitas"},
                    {"time_hhmm": "16:40", "vessel_name": "Kitsap"},
                    {"time_hhmm": "17:40", "vessel_name": "Sealth"},
                ]
            }
        }
        dummy_bulletins = {"schedule_mode": "3-boat", "triangle_bulletins": []}

        eval_res = evaluate_commute(dummy_telemetry, dummy_bulletins, simulated_time=regular_dt)
        pm_targets = eval_res["pm_commute"]["evaluated_targets"]
        
        self.assertIn("sports_bus", pm_targets)
        self.assertEqual(pm_targets["sports_bus"]["scheduled_time"], "16:40")
        self.assertIn("sports_bus_late", pm_targets)
        self.assertEqual(pm_targets["sports_bus_late"]["scheduled_time"], "17:40")

    def test_site_generation(self):
        dummy_analysis = {
            "timestamp": "2026-09-13T13:00:00-0700",
            "current_time_display": "Sunday, Sep 13 at 01:00 PM",
            "is_weekend": True,
            "target_commute_day": "Monday",
            "schedule_mode": "3-boat",
            "is_friday_pdd": False,
            "executive_briefing": "All systems operational.",
            "am_commute": {"evaluated_targets": {}, "scheduled_sailings": []},
            "pm_commute": {"evaluated_targets": {}, "scheduled_sailings": []},
            "vessels_telemetry": [],
            "active_bulletins": [],
        }
        test_out = "/tmp/test_ferry_site"
        res = generate_static_site(dummy_analysis, test_out)
        self.assertTrue(Path(res["index_html"]).exists())
        self.assertTrue(Path(res["data_json"]).exists())
        
        with open(res["index_html"], "r") as f:
            content = f.read()
            self.assertIn("Vashon School Ferry Tracker", content)
            self.assertIn("All systems operational.", content)


if __name__ == "__main__":
    unittest.main()
