"""
Unit and Integration Tests for Ferry Agent.
Tests API parsing, Friday PDD late start logic, sports bus logic,
weekend (Saturday/Sunday) lookahead to Monday morning, and static site generator.
"""

import json
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
                    {"time_hhmm": "17:45", "vessel_name": "Kitsap"},
                ]
            }
        }
        dummy_bulletins = {"schedule_mode": "3-boat", "triangle_bulletins": []}

        eval_res = evaluate_commute(dummy_telemetry, dummy_bulletins, simulated_time=regular_dt)
        pm_targets = eval_res["pm_commute"]["evaluated_targets"]
        
        self.assertIn("ces_sports_bus", pm_targets)
        self.assertEqual(pm_targets["ces_sports_bus"]["scheduled_time"], "16:40")
        self.assertEqual(pm_targets["ces_sports_bus"]["estimated_arrival"], "17:00")
        self.assertIn("sports_bus_late", pm_targets)
        self.assertEqual(pm_targets["sports_bus_late"]["scheduled_time"], "17:45")

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


    def test_scheduled_lambda_handler_execution(self):
        """Verifies that scheduled Lambda invocation executes the pipeline directly."""
        from unittest.mock import patch, MagicMock
        from src.lambda_handler import lambda_handler

        dummy_analysis = {
            "schedule_mode": "3-boat",
            "service_status": "normal",
            "cancelled_vessels": [],
            "timestamp": "2026-10-04T12:00:00",
        }
        with patch("src.lambda_handler.get_merged_ferry_telemetry", return_value={"vessels": [], "schedule": {}}), \
             patch("src.lambda_handler.scrape_bulletins", return_value={"schedule_mode": "3-boat"}), \
             patch("src.lambda_handler.analyze_commute_with_gemini", return_value=dummy_analysis), \
             patch("src.lambda_handler.generate_static_site", return_value={"index_html": "/tmp/test.html", "data_json": "/tmp/test.json"}):
            
            resp = lambda_handler({}, None)
            self.assertEqual(resp["statusCode"], 200)
            body = json.loads(resp["body"])
            self.assertEqual(body["schedule_mode"], "3-boat")
            self.assertIn("Ferry commute update completed successfully", body["message"])

    def test_ai_state_fingerprinting_and_cache(self):
        from src.analyzer.gemini_analyzer import compute_schedule_state_fingerprint

        heuristic_base = {
            "schedule_mode": "2-boat",
            "bulletin_mode_reason": "Vessel mechanical issue",
            "target_commute_day": "Monday",
            "is_friday_pdd": False,
            "am_commute": {"evaluated_targets": {"vhs": {"status": "ON_TIME", "delay_minutes": 0}}}
        }
        bulletins_base = {
            "triangle_bulletins": [{"bulletin_id": "101", "title": "Fauntleroy / Vashon", "content": "Operating 2-boat"}]
        }

        fp1 = compute_schedule_state_fingerprint(heuristic_base, bulletins_base)
        fp2 = compute_schedule_state_fingerprint(heuristic_base, bulletins_base)
        self.assertEqual(fp1, fp2, "Identical inputs must yield identical fingerprints")

        # Change schedule mode -> must change fingerprint
        heuristic_modified = dict(heuristic_base)
        heuristic_modified["schedule_mode"] = "3-boat"
        fp3 = compute_schedule_state_fingerprint(heuristic_modified, bulletins_base)
        self.assertNotEqual(fp1, fp3, "Changing schedule mode must alter fingerprint")

        # Change bulletin text -> must change fingerprint
        bulletins_modified = {
            "triangle_bulletins": [{"bulletin_id": "102", "title": "Fauntleroy / Vashon", "content": "Restored 3-boat"}]
        }
        fp4 = compute_schedule_state_fingerprint(heuristic_base, bulletins_modified)
        self.assertNotEqual(fp1, fp4, "Changing bulletins must alter fingerprint")

    def test_vessel_telemetry_freshness_in_template(self):
        """Verifies template does not falsely claim 'real-time' and includes freshness status."""
        import tempfile
        import json

        dummy_analysis = {
            "timestamp": "2026-09-13T16:49:56-07:00",
            "timestamp_epoch": 1789343396.0,
            "current_time_display": "Sunday, Sep 13 at 04:49 PM",
            "is_weekend": True,
            "target_commute_title": "Monday Commute",
            "schedule_mode": "3-boat",
            "is_friday_pdd": False,
            "executive_briefing": "Test briefing",
            "am_commute": {"evaluated_targets": {}, "scheduled_sailings": []},
            "pm_commute": {"evaluated_targets": {}, "scheduled_sailings": []},
            "vessels_telemetry": [
                {
                    "vessel_id": 19,
                    "name": "Kittitas",
                    "latitude": 47.51,
                    "longitude": -122.46,
                    "speed_knots": 0,
                    "heading": 209,
                    "at_dock": True,
                    "timestamp": "2026-09-13T16:49:43-0700"
                }
            ],
            "active_bulletins": []
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            files = generate_static_site(dummy_analysis, tmp_dir)
            with open(files["index_html"], "r", encoding="utf-8") as f:
                content = f.read()

            self.assertNotIn("tracking in real-time", content)
            self.assertIn("vessel-telemetry-status", content)
            self.assertIn("radar-ais-badge", content)
            self.assertIn("updateVesselTelemetryStatus", content)
            self.assertIn("pollVesselTelemetry", content)

    def test_lambda_http_function_url_vessel_handler(self):
        """Verifies Lambda Function URL HTTP routing returns CORS headers and vessel telemetry."""
        from unittest.mock import patch
        from src.lambda_handler import lambda_handler, is_http_request

        # Test HTTP detection
        http_event = {
            "version": "2.0",
            "routeKey": "$default",
            "rawPath": "/vessels",
            "requestContext": {
                "http": {
                    "method": "GET",
                    "path": "/vessels"
                }
            }
        }
        self.assertTrue(is_http_request(http_event))

        # Test OPTIONS preflight
        options_event = {
            "requestContext": {"http": {"method": "OPTIONS"}}
        }
        options_resp = lambda_handler(options_event, None)
        self.assertEqual(options_resp["statusCode"], 204)
        # Verify application code does not return duplicate Access-Control headers
        self.assertNotIn("Access-Control-Allow-Origin", options_resp["headers"])

        # Mock fetch_enriched_triangle_vessels for GET
        dummy_vessels = [{"vessel_id": 19, "name": "Kittitas", "speed_knots": 0}]
        with patch("src.lambda_handler.fetch_enriched_triangle_vessels", return_value=dummy_vessels):
            get_resp = lambda_handler(http_event, None)
            self.assertEqual(get_resp["statusCode"], 200)
            self.assertNotIn("Access-Control-Allow-Origin", get_resp["headers"])
            self.assertIn("Cache-Control", get_resp["headers"])
            
            body = json.loads(get_resp["body"])
            self.assertEqual(body["count"], 1)
            self.assertEqual(body["vessels_telemetry"][0]["name"], "Kittitas")

    def test_crew_shortage_cancelled_sailings_logic(self):
        """Verifies that crew shortage keeps 3-boat timetable but marks target vessel sailings cancelled."""
        from src.collectors.bulletin_scraper import _extract_cancelled_vessels
        
        sample_bulletin = (
            "Due to a shortage of crew, the M/V Sealth is out of service until a relief can be found. "
            "This cancels all the #3 sailings. The route will continue to operate on 3-boat schedule with Kittitas and Kitsap."
        )
        cancelled = _extract_cancelled_vessels(sample_bulletin)
        self.assertEqual(cancelled, ["Sealth"])
        self.assertNotIn("Kittitas", cancelled)
        self.assertNotIn("Kitsap", cancelled)

        # Evaluate commute with Sealth cancelled on Monday
        monday_dt = datetime(2026, 9, 14, 6, 45, tzinfo=timezone(timedelta(hours=-7)))
        dummy_telemetry = {
            "vessels": [
                {"name": "Kittitas", "speed_knots": 16.0, "in_service": True, "at_dock": False},
                {"name": "Kitsap", "speed_knots": 5.0, "in_service": True, "at_dock": False},
                {"name": "Sealth", "speed_knots": 0.0, "in_service": False, "at_dock": True},
            ],
            "schedule": {
                "fauntleroy_to_vashon": [
                    {"time_hhmm": "05:05", "vessel_name": "Kittitas"},
                    {"time_hhmm": "05:50", "vessel_name": "Kitsap"},
                    {"time_hhmm": "08:05", "vessel_name": "Kittitas"},
                    {"time_hhmm": "08:25", "vessel_name": "Kitsap"},
                ],
                "vashon_to_fauntleroy": [
                    {"time_hhmm": "16:40", "vessel_name": "Kittitas"},
                    {"time_hhmm": "17:45", "vessel_name": "Kitsap"},
                ]
            }
        }
        bulletins_data = {
            "schedule_mode": "3-boat",
            "service_status": "cancelled_sailings",
            "cancelled_vessels": ["Sealth"],
            "disruption_reason": "Shortage of crew (relief pending)",
            "mode_reason": "Alert: Faunt/Va/SW - Sealth cancelled sailings due to shortage of crew",
            "triangle_bulletins": []
        }

        eval_res = evaluate_commute(dummy_telemetry, bulletins_data, simulated_time=monday_dt)
        self.assertEqual(eval_res["schedule_mode"], "3-boat")
        self.assertTrue(eval_res["has_cancelled_sailings"])
        self.assertIn("Sealth Cancelled", eval_res["mode_display"])

        # VHS target (07:05 AM) must be CANCELLED and point to 08:05 AM Kittitas
        vhs_target = eval_res["am_commute"]["evaluated_targets"]["vhs_mcm"]
        self.assertEqual(vhs_target["scheduled_time"], "07:05")
        self.assertTrue(vhs_target["is_cancelled"])
        self.assertEqual(vhs_target["catchability"], "MISSED")
        self.assertIn("Out of Service", vhs_target["status"])
        self.assertIsNotNone(vhs_target["next_sailing"])
        self.assertEqual(vhs_target["next_sailing"]["time_hhmm"], "08:05")
        self.assertEqual(vhs_target["next_sailing"]["vessel"], "Kittitas")

        # CES target (08:05 AM) must be SAFE on Kittitas
        ces_target = eval_res["am_commute"]["evaluated_targets"]["ces"]
        self.assertEqual(ces_target["scheduled_time"], "08:05")
        self.assertFalse(ces_target["is_cancelled"])
        self.assertEqual(ces_target["catchability"], "SAFE")
        self.assertEqual(ces_target["vessel_name"], "Kittitas")

        # Executive briefing must explain 3-boat timetable with cancelled #3 boat
        self.assertIn("3-Boat Schedule with Cancelled Sailings", eval_res["executive_briefing"])
        self.assertIn("NOT an official 2-boat schedule change", eval_res["executive_briefing"])
        self.assertIn("7:05 AM Fauntleroy departure is CANCELLED", eval_res["executive_briefing"])

    def test_route_cancellations_table_parser(self):
        """Verifies parsing of WSDOT adds and cancels HTML table."""
        from unittest.mock import patch
        from src.collectors.bulletin_scraper import fetch_route_cancellations

        sample_html = """
        <table>
            <tr><td>3</td><td>Sealth</td></tr>
        </table>
        <table>
            <tr class="addcancelcontent">
                <td><span id="lblTripDate_0">Mon 09/14</span></td>
                <td>
                    <span id="lblWestboundCancelledTimeAdj_0">
                        <div style="white-space: nowrap;">
                            <div class="am">7:05</div>
                            <span style="font-size: 0.7em;">3</span>
                        </div>
                    </span>
                </td>
                <td>
                    <span id="lblEastboundCancelledTimeAdj_0">
                        <div style="white-space: nowrap;">
                            <div class="am">8:15</div>
                            <span style="font-size: 0.7em;">3</span>
                        </div>
                    </span>
                </td>
            </tr>
        </table>
        """

        class MockResponse:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self):
                return sample_html.encode("utf-8")

        with patch("urllib.request.urlopen", return_value=MockResponse()):
            res = fetch_route_cancellations()
            self.assertEqual(res["legend"].get("3"), "Sealth")
            cancels = res["cancellations_by_date"].get("Mon 09/14", {})
            self.assertEqual(len(cancels["fauntleroy_cancelled"]), 1)
            self.assertEqual(cancels["fauntleroy_cancelled"][0]["time_hhmm"], "07:05")
            self.assertEqual(cancels["fauntleroy_cancelled"][0]["vessel_name"], "Sealth")
            self.assertEqual(len(cancels["vashon_cancelled"]), 1)
            self.assertEqual(cancels["vashon_cancelled"][0]["time_hhmm"], "08:15")
            self.assertEqual(cancels["vashon_cancelled"][0]["vessel_name"], "Sealth")

    def test_pm_return_commute_operating_and_restoration(self):
        """Verifies that PM return sailings (15:25, 16:40, 17:45) operate safely after boat is restored."""
        monday_pm_dt = datetime(2026, 9, 14, 14, 30, tzinfo=timezone(timedelta(hours=-7)))
        dummy_telemetry = {
            "vessels": [
                {"name": "Sealth", "in_service": True, "speed_knots": 16.5, "at_dock": False},
                {"name": "Kittitas", "in_service": True, "speed_knots": 0, "at_dock": True},
                {"name": "Kitsap", "in_service": True, "speed_knots": 14.0, "at_dock": False},
            ],
            "schedule": {
                "fauntleroy_to_vashon": [],
                "vashon_to_fauntleroy": [
                    {"time_hhmm": "15:25", "vessel_name": "Sealth"},
                    {"time_hhmm": "16:40", "vessel_name": "Kittitas"},
                    {"time_hhmm": "17:05", "vessel_name": "Sealth"},
                    {"time_hhmm": "17:45", "vessel_name": "Kitsap"},
                ]
            }
        }
        # Morning cancellations existed earlier for 05:40 and 06:35 Vashon, 07:05 Fauntleroy,
        # but Sealth was restored by 8:15 AM
        bulletins_data = {
            "schedule_mode": "3-boat",
            "service_status": "normal",
            "cancelled_vessels": [],
            "restored_vessels": ["Sealth"],
            "exact_cancelled_f_to_v": ["06:10", "07:05"],
            "exact_cancelled_v_to_f": ["05:40", "06:35"],
            "mode_reason": "Notice: Route back to three-boat schedule",
            "triangle_bulletins": []
        }

        eval_res = evaluate_commute(dummy_telemetry, bulletins_data, simulated_time=monday_pm_dt)
        self.assertEqual(eval_res["schedule_mode"], "3-boat")
        self.assertEqual(eval_res["mode_display"], "3-Boat Schedule (Normal)")
        self.assertFalse(eval_res["has_cancelled_sailings"])

        pm_targets = eval_res["pm_commute"]["evaluated_targets"]
        # 15:25 VHS dismissal (Sealth) must NOT be cancelled
        self.assertFalse(pm_targets["vhs_mcm"]["is_cancelled"])
        self.assertEqual(pm_targets["vhs_mcm"]["catchability"], "SAFE")
        self.assertEqual(pm_targets["vhs_mcm"]["vessel_name"], "Sealth")

        # 16:40 CES dismissal & Sports Bus (Kittitas) must NOT be cancelled
        self.assertIn("ces_sports_bus", pm_targets)
        self.assertFalse(pm_targets["ces_sports_bus"]["is_cancelled"])
        self.assertEqual(pm_targets["ces_sports_bus"]["catchability"], "SAFE")
        self.assertEqual(pm_targets["ces_sports_bus"]["vessel_name"], "Kittitas")

        # 17:45 Sports Bus Late (Kitsap) must NOT be cancelled
        self.assertFalse(pm_targets["sports_bus_late"]["is_cancelled"])
        self.assertEqual(pm_targets["sports_bus_late"]["scheduled_time"], "17:45")
        self.assertEqual(pm_targets["sports_bus_late"]["vessel_name"], "Kitsap")

        # Executive briefing explains 3-boat restored status
        self.assertIn("Three-Boat Schedule Restored", eval_res["executive_briefing"])
        self.assertIn("Sealth", eval_res["executive_briefing"])

    def test_ghost_ferry_detection_inbound_fauntleroy(self):
        """Verifies detection of unscheduled morning walk-on boat ('Ghost Ferry') at Fauntleroy."""
        mon_725 = datetime(2026, 9, 14, 7, 25, tzinfo=timezone(timedelta(hours=-7)))
        vessels = [
            {"name": "Kitsap", "in_service": True, "speed_knots": 14.5, "arriving_terminal_id": 9, "eta_hhmm": "07:30", "latitude": 47.521, "longitude": -122.412},
            {"name": "Kittitas", "in_service": True, "speed_knots": 0, "at_dock": True, "departing_terminal_id": 20},
        ]
        from src.analyzer.heuristic_analyzer import detect_ghost_ferry
        gf = detect_ghost_ferry(vessels, mon_725, target_weekday=0)

        self.assertTrue(gf["is_ghost_ferry"])
        self.assertTrue(gf["is_detected"])
        self.assertFalse(gf["is_thursday_hazmat"])
        self.assertEqual(gf["vessel_name"], "M/V Kitsap")
        self.assertIn("Approaching Fauntleroy", gf["status"])
        self.assertEqual(gf["estimated_departure"], "07:35")
        self.assertEqual(gf["estimated_arrival"], "07:55")
        self.assertEqual(gf["catchability"], "NOT RECOMMENDED")
        self.assertIn("Not Recommended", gf["bus_note"])
        self.assertIn("Kitsap", gf["bus_note"])

    def test_ghost_ferry_thursday_hazmat_disclaimer(self):
        """Verifies that Thursday mornings flag the Ghost Ferry with Hazmat USCG warnings."""
        thu_725 = datetime(2026, 9, 17, 7, 25, tzinfo=timezone(timedelta(hours=-7)))
        vessels = [
            {"name": "Kitsap", "in_service": True, "speed_knots": 14.5, "arriving_terminal_id": 9, "eta_hhmm": "07:30", "latitude": 47.521, "longitude": -122.412},
        ]
        from src.analyzer.heuristic_analyzer import detect_ghost_ferry
        gf = detect_ghost_ferry(vessels, thu_725, target_weekday=3)

        self.assertTrue(gf["is_ghost_ferry"])
        self.assertTrue(gf["is_detected"])
        self.assertTrue(gf["is_thursday_hazmat"])
        self.assertEqual(gf["catchability"], "HAZMAT")
        self.assertIn("Thursday Hazmat Advisory", gf["bus_note"])
        self.assertIn("strictly prohibit walk-on passengers", gf["bus_note"])

    def test_ghost_ferry_lookahead_outside_window(self):
        """Verifies that outside morning hours, Ghost Ferry displays live monitor placeholder."""
        mon_afternoon = datetime(2026, 9, 14, 15, 0, tzinfo=timezone(timedelta(hours=-7)))
        vessels = [
            {"name": "Kitsap", "in_service": True, "speed_knots": 16.0, "arriving_terminal_id": 22},
        ]
        from src.analyzer.heuristic_analyzer import detect_ghost_ferry
        gf = detect_ghost_ferry(vessels, mon_afternoon, target_weekday=0)

        self.assertTrue(gf["is_ghost_ferry"])
        self.assertFalse(gf["is_detected"])
        self.assertIn("Monitored Live", gf["status"])
        self.assertEqual(gf["catchability"], "NOT RECOMMENDED")

    def test_ghost_ferry_arrival_window_restrictions(self):
        """Verifies Ghost Ferry is ONLY detected if Fauntleroy arrival is between ~7:15 AM and ~7:45 AM."""
        from src.analyzer.heuristic_analyzer import detect_ghost_ferry

        # 1. Monitored strictly within 7:00 AM - 8:05 AM window
        # At 6:50 AM, should NOT detect even if boat is at dock
        mon_650 = datetime(2026, 9, 14, 6, 50, tzinfo=timezone(timedelta(hours=-7)))
        vessels_at_dock = [
            {"name": "Kitsap", "in_service": True, "at_dock": True, "departing_terminal_id": 9}
        ]
        gf = detect_ghost_ferry(vessels_at_dock, mon_650, target_weekday=0)
        self.assertFalse(gf["is_detected"])

        # At 8:15 AM, should NOT detect
        mon_815 = datetime(2026, 9, 14, 8, 15, tzinfo=timezone(timedelta(hours=-7)))
        gf = detect_ghost_ferry(vessels_at_dock, mon_815, target_weekday=0)
        self.assertFalse(gf["is_detected"])

        # 2. Within 7:00 - 8:05 AM, vessel arrival at Fauntleroy must be between 7:15 and 7:45 AM
        mon_725 = datetime(2026, 9, 14, 7, 25, tzinfo=timezone(timedelta(hours=-7)))
        
        # Boat arriving too early (ETA 7:10 AM < 7:15 AM)
        vessel_early = [
            {"name": "Kitsap", "in_service": True, "speed_knots": 14.0, "arriving_terminal_id": 9, "eta_hhmm": "07:10"}
        ]
        gf = detect_ghost_ferry(vessel_early, mon_725, target_weekday=0)
        self.assertFalse(gf["is_detected"])

        # Boat arriving too late (ETA 7:50 AM > 7:45 AM)
        vessel_late = [
            {"name": "Kitsap", "in_service": True, "speed_knots": 14.0, "arriving_terminal_id": 9, "eta_hhmm": "07:50"}
        ]
        gf = detect_ghost_ferry(vessel_late, mon_725, target_weekday=0)
        self.assertFalse(gf["is_detected"])

        # Boat arriving in window (ETA 7:30 AM between 7:15 and 7:45 AM)
        vessel_in_window = [
            {"name": "Kitsap", "in_service": True, "speed_knots": 14.0, "arriving_terminal_id": 9, "eta_hhmm": "07:30"}
        ]
        gf = detect_ghost_ferry(vessel_in_window, mon_725, target_weekday=0)
        self.assertTrue(gf["is_detected"])
        self.assertEqual(gf["vessel_name"], "M/V Kitsap")

    def test_ghost_ferry_excludes_scheduled_boats(self):
        """Verifies Ghost Ferry logic excludes boats that are already on the official schedule."""
        from src.analyzer.heuristic_analyzer import detect_ghost_ferry

        mon_725 = datetime(2026, 9, 14, 7, 25, tzinfo=timezone(timedelta(hours=-7)))
        scheduled = [
            {"time_hhmm": "07:05", "vessel_name": "Sealth"},
            {"time_hhmm": "08:05", "vessel_name": "Kittitas"},
        ]

        # Scheduled boat Sealth arriving slightly late at 7:18 AM (scheduled for 7:05 AM)
        sealth = [
            {"name": "Sealth", "in_service": True, "speed_knots": 12.0, "arriving_terminal_id": 9, "eta_hhmm": "07:18"}
        ]
        gf_sealth = detect_ghost_ferry(sealth, mon_725, target_weekday=0, scheduled_sailings=scheduled)
        self.assertFalse(gf_sealth["is_detected"])

        # Scheduled boat Kittitas arriving at 7:42 AM (scheduled for 8:05 AM)
        kittitas = [
            {"name": "Kittitas", "in_service": True, "speed_knots": 12.0, "arriving_terminal_id": 9, "eta_hhmm": "07:42"}
        ]
        gf_kittitas = detect_ghost_ferry(kittitas, mon_725, target_weekday=0, scheduled_sailings=scheduled)
        self.assertFalse(gf_kittitas["is_detected"])

        # Unscheduled boat Kitsap arriving at 7:30 AM
        kitsap = [
            {"name": "Kitsap", "in_service": True, "speed_knots": 14.0, "arriving_terminal_id": 9, "eta_hhmm": "07:30"}
        ]
        gf_kitsap = detect_ghost_ferry(kitsap, mon_725, target_weekday=0, scheduled_sailings=scheduled)
        self.assertTrue(gf_kitsap["is_detected"])
        self.assertEqual(gf_kitsap["vessel_name"], "M/V Kitsap")


    def test_travel_bulletins_link_in_generated_site(self):
        """Verifies that generated index.html includes the direct link to WSDOT Travel Bulletins."""
        import tempfile
        from src.generator.site_generator import generate_static_site

        dummy_analysis = {
            "timestamp": "2026-09-14T14:15:00-07:00",
            "timestamp_epoch": 1789344900.0,
            "current_time_display": "Monday, Sep 14 at 02:15 PM",
            "is_weekend": False,
            "target_commute_title": "Monday Commute",
            "schedule_mode": "3-boat",
            "is_friday_pdd": False,
            "executive_briefing": "Test briefing",
            "am_commute": {"evaluated_targets": {}, "scheduled_sailings": []},
            "pm_commute": {"evaluated_targets": {}, "scheduled_sailings": []},
            "vessels_telemetry": [],
            "active_bulletins": []
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            files = generate_static_site(dummy_analysis, tmp_dir)
            with open(files["index_html"], "r", encoding="utf-8") as f:
                content = f.read()

            self.assertIn("https://wsdot.com/ferries/schedule/bulletin.aspx", content)
            self.assertIn("Travel Bulletins", content)
            self.assertIn("getInitialDirection", content)

    def test_ais_telemetry_reconciles_back_to_schedule(self):
        """Verifies that live AIS telemetry clears cancelled status if boat is in service with no upcoming cancels."""
        mon_afternoon = datetime(2026, 9, 14, 14, 0, tzinfo=timezone(timedelta(hours=-7)))
        dummy_telemetry = {
            "vessels": [
                {"name": "Sealth", "in_service": True, "speed_knots": 0.0, "at_dock": True},
                {"name": "Kittitas", "in_service": True, "speed_knots": 15.0, "at_dock": False},
                {"name": "Kitsap", "in_service": True, "speed_knots": 14.0, "at_dock": False},
            ],
            "schedule": {
                "fauntleroy_to_vashon": [],
                "vashon_to_fauntleroy": [
                    {"time_hhmm": "15:25", "vessel_name": "Sealth"},
                    {"time_hhmm": "16:40", "vessel_name": "Kittitas"},
                    {"time_hhmm": "17:45", "vessel_name": "Kitsap"},
                ]
            }
        }
        # Simulate scraper having Sealth in cancelled_vessels because of morning 07:05 cancel
        bulletins_data = {
            "schedule_mode": "3-boat",
            "service_status": "cancelled_sailings",
            "cancelled_vessels": ["Sealth"],
            "restored_vessels": [],
            "exact_cancelled_f_to_v": ["06:10", "07:05"],
            "exact_cancelled_v_to_f": ["05:40", "06:35"],
            "triangle_bulletins": []
        }

        eval_res = evaluate_commute(dummy_telemetry, bulletins_data, simulated_time=mon_afternoon)
        # Sealth should be restored because live telemetry proves it is at dock / in service
        self.assertEqual(eval_res["cancelled_vessels"], [])
        self.assertIn("Sealth", eval_res["restored_vessels"])
        self.assertEqual(eval_res["schedule_mode"], "3-boat")
        self.assertEqual(eval_res["mode_display"], "3-Boat Schedule (Normal)")
        self.assertFalse(eval_res["has_cancelled_sailings"])
        
        # 15:25 PM VHS dismissal on Sealth must be ON TIME
        pm_targets = eval_res["pm_commute"]["evaluated_targets"]
        self.assertFalse(pm_targets["vhs_mcm"]["is_cancelled"])
        self.assertEqual(pm_targets["vhs_mcm"]["vessel_name"], "Sealth")

    def test_after_7pm_rollover_mon_to_tue(self):
        """Mon at 7:30 PM (19:30) must shift target to Tuesday school commute."""
        mon_evening = datetime(2026, 9, 14, 19, 30, tzinfo=timezone(timedelta(hours=-7)))
        
        info = get_target_school_commute_date(mon_evening)
        self.assertTrue(info["is_next_day"])
        self.assertTrue(info["is_after_7pm"])
        self.assertFalse(info["is_weekend"])
        self.assertEqual(info["target_weekday_name"], "Tuesday")
        self.assertEqual(info["target_date_str"], "2026-09-15")

        dummy_telemetry = {
            "vessels": [],
            "schedule": {
                "fauntleroy_to_vashon": [
                    {"time_hhmm": "07:05", "vessel_name": "Sealth"},
                    {"time_hhmm": "08:05", "vessel_name": "Kittitas"}
                ],
                "vashon_to_fauntleroy": []
            }
        }
        dummy_bulletins = {"schedule_mode": "3-boat", "triangle_bulletins": []}

        eval_res = evaluate_commute(dummy_telemetry, dummy_bulletins, simulated_time=mon_evening)
        self.assertTrue(eval_res["is_next_day"])
        self.assertFalse(eval_res["is_weekend"])
        self.assertEqual(eval_res["target_commute_day"], "Tuesday")
        self.assertEqual(eval_res["target_commute_title"], "Tuesday School Commute (Tomorrow)")
        self.assertIn("Next Day Preview (Showing Tuesday Commute)", eval_res["executive_briefing"])

    def test_after_7pm_rollover_thu_to_fri_late_start(self):
        """Thu at 8:00 PM (20:00) must shift to Friday Late Start (PDD)."""
        thu_evening = datetime(2026, 9, 17, 20, 0, tzinfo=timezone(timedelta(hours=-7)))
        self.assertEqual(thu_evening.weekday(), 3)  # Thursday

        info = get_target_school_commute_date(thu_evening)
        self.assertTrue(info["is_next_day"])
        self.assertEqual(info["target_weekday_name"], "Friday")
        self.assertEqual(info["target_date_str"], "2026-09-18")

        dummy_telemetry = {
            "vessels": [],
            "schedule": {"fauntleroy_to_vashon": [], "vashon_to_fauntleroy": []}
        }
        dummy_bulletins = {"schedule_mode": "3-boat", "triangle_bulletins": []}

        eval_res = evaluate_commute(dummy_telemetry, dummy_bulletins, simulated_time=thu_evening)
        self.assertTrue(eval_res["is_next_day"])
        self.assertTrue(eval_res["is_friday_pdd"])
        self.assertEqual(eval_res["target_commute_day"], "Friday")
        self.assertEqual(eval_res["target_commute_title"], "Friday School Commute (Tomorrow)")
        
        # Friday Late Start targets
        am_targets = eval_res["am_commute"]["evaluated_targets"]
        self.assertEqual(am_targets["vhs_mcm"]["scheduled_time"], "08:25")
        self.assertEqual(am_targets["ces"]["scheduled_time"], "09:30")
        self.assertIn("Friday Late Start", eval_res["executive_briefing"])

    def test_after_7pm_rollover_fri_to_mon(self):
        """Fri at 7:15 PM (19:15) must shift to Monday school commute."""
        fri_evening = datetime(2026, 9, 18, 19, 15, tzinfo=timezone(timedelta(hours=-7)))
        self.assertEqual(fri_evening.weekday(), 4)  # Friday

        info = get_target_school_commute_date(fri_evening)
        self.assertTrue(info["is_next_day"])
        self.assertTrue(info["is_weekend"])
        self.assertEqual(info["target_weekday_name"], "Monday")
        self.assertEqual(info["target_date_str"], "2026-09-21")

        dummy_telemetry = {
            "vessels": [],
            "schedule": {"fauntleroy_to_vashon": [], "vashon_to_fauntleroy": []}
        }
        dummy_bulletins = {"schedule_mode": "3-boat", "triangle_bulletins": []}

        eval_res = evaluate_commute(dummy_telemetry, dummy_bulletins, simulated_time=fri_evening)
        self.assertTrue(eval_res["is_weekend"])
        self.assertTrue(eval_res["is_next_day"])
        self.assertEqual(eval_res["target_commute_day"], "Monday")
        self.assertEqual(eval_res["target_commute_title"], "Monday School Commute (Next School Day)")
        self.assertIn("Weekend Preview", eval_res["executive_briefing"])

    def test_before_7pm_keeps_same_day(self):
        """Mon at 6:50 PM (18:50) must stay on Monday."""
        mon_late_afternoon = datetime(2026, 9, 14, 18, 50, tzinfo=timezone(timedelta(hours=-7)))
        info = get_target_school_commute_date(mon_late_afternoon)
        self.assertFalse(info["is_next_day"])
        self.assertFalse(info["is_weekend"])
        self.assertEqual(info["target_weekday_name"], "Monday")
        self.assertEqual(info["target_date_str"], "2026-09-14")

    def test_sunday_night_preserves_monday_3_boat_and_705_am_timetable(self):
        """
        On Sunday night, an active Sunday 2-boat bulletin must NOT cause Monday to predict 2-boat.
        Monday must predict 3-boat with early boat at 7:05 AM aligning with Route 14 timetable.
        """
        sunday_night = datetime(2026, 9, 20, 20, 30, tzinfo=timezone(timedelta(hours=-7)))
        
        # Route 14 timetable for Monday
        monday_schedule = {
            "fauntleroy_to_vashon": [
                {"time_hhmm": "05:05", "vessel_name": "Kittitas"},
                {"time_hhmm": "05:50", "vessel_name": "Kitsap"},
                {"time_hhmm": "06:10", "vessel_name": "Sealth"},
                {"time_hhmm": "07:05", "vessel_name": "Sealth"},
                {"time_hhmm": "08:05", "vessel_name": "Kittitas"},
                {"time_hhmm": "08:25", "vessel_name": "Kitsap"},
            ],
            "vashon_to_fauntleroy": [
                {"time_hhmm": "15:25", "vessel_name": "Sealth"},
                {"time_hhmm": "16:40", "vessel_name": "Kittitas"},
                {"time_hhmm": "17:45", "vessel_name": "Kitsap"},
            ]
        }
        dummy_telemetry = {"vessels": [], "schedule": monday_schedule}
        
        # Sunday bulletin announces 2-boat for Sunday today
        bulletins_data = {
            "schedule_mode": "2-boat",
            "service_status": "reduced_2boat",
            "mode_reason": "Alert: Fauntleroy / Vashon / Southworth - Operating on two-boat schedule today",
            "monday_schedule_mode": "3-boat",
            "monday_service_status": "normal",
            "monday_mode_reason": "Operating on normal Monday three-boat schedule.",
            "triangle_bulletins": [
                {
                    "title": "Fauntleroy / Vashon / Southworth - Operating on two-boat schedule today",
                    "content": "Route is on a two-boat schedule today Sunday, Sept 20 due to crew shortage."
                }
            ]
        }

        eval_res = evaluate_commute(dummy_telemetry, bulletins_data, simulated_time=sunday_night)
        
        self.assertTrue(eval_res["is_weekend"])
        self.assertEqual(eval_res["target_commute_day"], "Monday")
        self.assertEqual(eval_res["schedule_mode"], "3-boat")
        self.assertEqual(eval_res["mode_display"], "3-Boat Schedule (Normal)")
        
        am_targets = eval_res["am_commute"]["evaluated_targets"]
        self.assertEqual(am_targets["vhs_mcm"]["scheduled_time"], "07:05")
        self.assertEqual(am_targets["vhs_mcm"]["vessel_name"], "Sealth")
        self.assertEqual(am_targets["ces"]["scheduled_time"], "08:05")
        self.assertEqual(am_targets["ces"]["vessel_name"], "Kittitas")
        self.assertIn("normal three-boat schedule", eval_res["executive_briefing"])

    def test_timetable_reconciliation_overrides_stray_2_boat_mode(self):
        """
        If bulletins_data mistakenly passed 2-boat on a weekend, but the Route 14 timetable
        contains the 3-boat departure (07:05 AM) with no explicit Monday 2-boat alert,
        reconciliation must correct mode to 3-boat and select 7:05 AM.
        """
        sunday_night = datetime(2026, 9, 27, 21, 0, tzinfo=timezone(timedelta(hours=-7)))
        monday_schedule = {
            "fauntleroy_to_vashon": [
                {"time_hhmm": "05:05", "vessel_name": "Kittitas"},
                {"time_hhmm": "06:10", "vessel_name": "Sealth"},
                {"time_hhmm": "07:05", "vessel_name": "Sealth"},
                {"time_hhmm": "08:05", "vessel_name": "Kittitas"},
            ],
            "vashon_to_fauntleroy": []
        }
        dummy_telemetry = {"vessels": [], "schedule": monday_schedule}
        
        # Suppose monday_schedule_mode was mistakenly set to 2-boat
        bulletins_data = {
            "schedule_mode": "2-boat",
            "monday_schedule_mode": "2-boat",
            "triangle_bulletins": [
                {
                    "title": "Fauntleroy / Vashon / Southworth - Two-boat schedule Sunday",
                    "content": "Operating 2-boat Sunday due to maintenance."
                }
            ]
        }

        eval_res = evaluate_commute(dummy_telemetry, bulletins_data, simulated_time=sunday_night)
        
        # Must reconcile to 3-boat based on Route 14 timetable
        self.assertEqual(eval_res["schedule_mode"], "3-boat")
        self.assertEqual(eval_res["am_commute"]["evaluated_targets"]["vhs_mcm"]["scheduled_time"], "07:05")

    def test_sunday_night_honors_explicit_monday_2_boat_bulletin(self):
        """
        If a bulletin explicitly announces Monday will run on a 2-boat schedule,
        it must predict 2-boat with 07:20 AM target.
        """
        sunday_night = datetime(2026, 9, 20, 20, 0, tzinfo=timezone(timedelta(hours=-7)))
        dummy_telemetry = {
            "vessels": [],
            "schedule": {
                "fauntleroy_to_vashon": [],
                "vashon_to_fauntleroy": []
            }
        }
        bulletins_data = {
            "schedule_mode": "2-boat",
            "monday_schedule_mode": "2-boat",
            "monday_mode_reason": "Alert: Monday 2-boat service due to vessel drydock",
            "triangle_bulletins": [
                {
                    "title": "Fauntleroy / Vashon / Southworth - Two-boat schedule Monday",
                    "content": "Due to emergency vessel repairs, the route will operate on a two-boat schedule on Monday, Sep 21."
                }
            ]
        }

        eval_res = evaluate_commute(dummy_telemetry, bulletins_data, simulated_time=sunday_night)
        self.assertEqual(eval_res["schedule_mode"], "2-boat")
        self.assertEqual(eval_res["am_commute"]["evaluated_targets"]["vhs_mcm"]["scheduled_time"], "07:20")

    def test_scrape_bulletins_weekend_bulletin_does_not_pollute_monday(self):
        """
        Verifies scrape_bulletins parser does not let a Sunday 2-boat alert bleed into monday_schedule_mode.
        """
        from unittest.mock import patch
        from src.collectors.bulletin_scraper import scrape_bulletins

        sample_bulletin_html = """
        <html>
        <body>
            <span id="cphPageTemplate_rprBulletins_lblBulletinDate_0">[Last Updated: Sunday, September 20, 2026 8:00 AM]</span>
            <span id="cphPageTemplate_rprBulletins_lblBulletinTitle_0">Fauntleroy / Vashon / Southworth - Operating on two-boat schedule today</span>
            <span id="cphPageTemplate_rprBulletins_lblContent_0">The route is operating on a two-boat schedule today, Sunday, September 20 due to lack of crew.</span>
        </body>
        </html>
        """

        class MockResponse:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self):
                return sample_bulletin_html.encode("utf-8")

        # Mock Sunday evening time
        sunday_dt = datetime(2026, 9, 20, 20, 0, tzinfo=timezone(timedelta(hours=-7)))
        with patch("urllib.request.urlopen", return_value=MockResponse()), \
             patch("src.collectors.bulletin_scraper.datetime") as mock_dt:
            mock_dt.now.return_value = sunday_dt
            mock_dt.strptime = datetime.strptime
            mock_dt.min = datetime.min
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
            
            res = scrape_bulletins()
            self.assertEqual(res["schedule_mode"], "2-boat")
            self.assertEqual(res["monday_schedule_mode"], "3-boat")
            self.assertEqual(res["monday_service_status"], "normal")

    def test_pm_commute_combines_5pm_ferries_into_single_card(self):
        """
        Verifies that PM commute targets have exactly one target for the 16:40 departure
        (5:00 PM Fauntleroy arrival) combining CES dismissal and after-school sports bus.
        """
        weekday_dt = datetime(2026, 9, 14, 15, 0, tzinfo=timezone(timedelta(hours=-7)))
        dummy_telemetry = {
            "vessels": [],
            "schedule": {
                "fauntleroy_to_vashon": [],
                "vashon_to_fauntleroy": [
                    {"time_hhmm": "15:25", "vessel_name": "Sealth"},
                    {"time_hhmm": "16:40", "vessel_name": "Kittitas"},
                    {"time_hhmm": "17:45", "vessel_name": "Kitsap"},
                ]
            }
        }
        dummy_bulletins = {"schedule_mode": "3-boat", "triangle_bulletins": []}

        eval_res = evaluate_commute(dummy_telemetry, dummy_bulletins, simulated_time=weekday_dt)
        pm_targets = eval_res["pm_commute"]["evaluated_targets"]

        # Exactly 3 targets: 15:25, 16:40, 17:45
        self.assertEqual(len(pm_targets), 3)
        self.assertIn("vhs_mcm", pm_targets)
        self.assertIn("ces_sports_bus", pm_targets)
        self.assertIn("sports_bus_late", pm_targets)

        # The 16:40 target has estimated Fauntleroy arrival of 17:00 (5:00 PM)
        target_5pm = pm_targets["ces_sports_bus"]
        self.assertEqual(target_5pm["scheduled_time"], "16:40")
        self.assertEqual(target_5pm["estimated_departure"], "16:40")
        self.assertEqual(target_5pm["estimated_arrival"], "17:00")
        self.assertEqual(target_5pm["estimated_arrival_display"], "5:00 PM")
        self.assertIn("CES dismissal bus", target_5pm["bus_note"])
        self.assertIn("sports bus", target_5pm["bus_note"])

    def test_october_5_two_boat_schedule_reconciliation(self):
        """
        Verifies that Monday October 5, 2026 timetable accurately detects 2-boat mode,
        selects 07:20 AM (VHS) and 08:15 AM (CES) for AM, 15:20, 16:45, 17:45 for PM,
        and suppresses the ghost ferry card.
        """
        mon_oct5_dt = datetime(2026, 10, 5, 6, 30, tzinfo=timezone(timedelta(hours=-7)))
        oct5_f_to_v = [
            {"time_hhmm": t, "vessel_name": "Kittitas"}
            for t in [
                "05:05", "05:45", "06:45", "07:40", "08:15", "09:10", "09:30", "10:25",
                "11:30", "13:15", "13:35", "14:10", "15:00", "15:50", "16:35", "17:15",
                "18:15", "18:35", "19:35", "19:55", "21:15", "22:35", "23:50", "01:05"
            ]
        ]
        oct5_v_to_f = [
            {"time_hhmm": t, "vessel_name": "Cathlamet"}
            for t in [
                "04:05", "04:30", "05:20", "06:15", "06:55", "07:15", "07:50", "08:40",
                "09:00", "09:55", "10:20", "12:40", "13:40", "15:20", "16:45", "17:45",
                "18:40", "20:15", "22:05", "23:00", "00:15"
            ]
        ]
        telemetry = {
            "vessels": [],
            "schedule": {
                "fauntleroy_to_vashon": oct5_f_to_v,
                "vashon_to_fauntleroy": oct5_v_to_f
            }
        }
        bulletins_data = {
            "schedule_mode": "3-boat",  # Even if default bulletin says 3-boat, timetable must reconcile to 2-boat
            "service_status": "normal",
            "triangle_bulletins": []
        }

        eval_res = evaluate_commute(telemetry, bulletins_data, simulated_time=mon_oct5_dt)
        self.assertEqual(eval_res["schedule_mode"], "2-boat")
        self.assertEqual(eval_res["service_status"], "reduced_2boat")
        self.assertEqual(eval_res["mode_display"], "2-Boat Contingency Schedule")

        am_targets = eval_res["am_commute"]["evaluated_targets"]
        # Ghost ferry must NOT be in evaluated_targets for 2-boat mode
        self.assertNotIn("ghost_ferry", am_targets)
        # VHS/McM target must be 07:20 AM unscheduled run
        self.assertIn("vhs_mcm", am_targets)
        self.assertEqual(am_targets["vhs_mcm"]["scheduled_time"], "07:20")
        self.assertEqual(am_targets["vhs_mcm"]["scheduled_time_display"], "7:20 AM (Unscheduled)")
        self.assertEqual(am_targets["vhs_mcm"]["catchability"], "SAFE")
        self.assertIn("Unscheduled Run", am_targets["vhs_mcm"]["status"])

        # CES target must be 08:15 AM
        self.assertIn("ces", am_targets)
        self.assertEqual(am_targets["ces"]["scheduled_time"], "08:15")
        self.assertEqual(am_targets["ces"]["catchability"], "SAFE")

        # PM targets must match published 2-boat timetable: 15:20, 16:45, 17:45
        pm_targets = eval_res["pm_commute"]["evaluated_targets"]
        self.assertEqual(pm_targets["vhs_mcm"]["scheduled_time"], "15:20")
        self.assertEqual(pm_targets["ces_sports_bus"]["scheduled_time"], "16:45")
        self.assertEqual(pm_targets["sports_bus_late"]["scheduled_time"], "17:45")

    def test_two_boat_announcement_bulletin_scraper(self):
        """
        Verifies that bulletin scraper identifies official announcement:
        'Beginning Monday, October 5, the triangle route will be reduced from three boats to two boats'
        and sets monday_schedule_mode to '2-boat'.
        """
        from unittest.mock import patch
        from src.collectors.bulletin_scraper import scrape_bulletins

        bulletin_html = """
        <html>
        <body>
            <span id="cphPageTemplate_rprBulletins_lblBulletinDate_0">[Last Updated: Sunday, October 4, 2026 10:00 AM]</span>
            <span id="cphPageTemplate_rprBulletins_lblBulletinTitle_0">Two-boat schedule beginning on Monday for approximately one month</span>
            <span id="cphPageTemplate_rprBulletins_lblContent_0">Beginning Monday, October 5, the triangle route will be reduced from three boats to two boats for approximately one month due to scheduled service on the Kittitas ferry. This is expected to last through November 8.</span>
        </body>
        </html>
        """
        class MockResp:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass
            def read(self):
                return bulletin_html.encode("utf-8")

        sunday_dt = datetime(2026, 10, 4, 18, 0, tzinfo=timezone(timedelta(hours=-7)))
        with patch("urllib.request.urlopen", return_value=MockResp()), \
             patch("src.collectors.bulletin_scraper.datetime") as mock_dt:
            mock_dt.now.return_value = sunday_dt
            mock_dt.strptime = datetime.strptime
            mock_dt.min = datetime.min
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)

            res = scrape_bulletins()
            self.assertEqual(res["schedule_mode"], "2-boat")
            self.assertEqual(res["monday_schedule_mode"], "2-boat")
            self.assertEqual(res["monday_service_status"], "reduced_2boat")
            self.assertIn("Two-boat schedule", res["monday_mode_reason"])


if __name__ == "__main__":
    unittest.main()
