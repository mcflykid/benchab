"""Synthetic inputs only; no network/vendor imports/private payloads."""

import contextlib
import copy
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from benchab.machine import telemetrie as t

FACTICE_1 = "SECRET"
FACTICE_2 = 8
FACTICE_3 = "7"
FACTICE_4 = 7
FACTICE_5 = "bad"
FACTICE_6 = 0


class TelemetryTests(unittest.TestCase):
    def test_statistics_units_and_zero(self):
        result = t.project_statistics(
            {"base": {"print_totaltime": "2hour12min", "print_count": "0", "material_used": "2.5kg"}}
        )
        self.assertEqual(
            (result["total_duration_seconds"], result["print_count"], result["total_filament_kg"]), (7920, 0, 2.5)
        )
        self.assertEqual(t.project_statistics({"base": {"print_totaltime": "3.5"}})["total_duration_seconds"], 210)

    def test_statistics_unknown_units_never_zero(self):
        for value in (4, "4g", "4kgTOKEN", None, True, "NaN", "-2kg"):
            self.assertIsNone(t.project_statistics({"base": {"material_used": value}})["total_filament_kg"])
        self.assertIsNone(t.project_statistics({"base": {"print_totaltime": 60}})["total_duration_seconds"])
        self.assertEqual(t.project_statistics({})["quality"], "not_reported")

    def test_number_bounds(self):
        for bad in (True, -1, float("nan"), float("inf"), 10**1000, "9" * 100, "-1"):
            self.assertIsNone(t._number(bad))
        self.assertEqual(t._number("0"), 0)

    def test_job_minutes_and_layers(self):
        r = t.project_job(
            {
                "name": "cube",
                "progress": "42",
                "print_time": "12.5",
                "remain_time": 0,
                "settings": '{"curr_layer":2,"total_layers":100}',
                "print_status": 6,
            }
        )
        self.assertEqual((r["elapsed_seconds"], r["remaining_seconds"], r["current_layer"]), (750, 0, 2))
        self.assertEqual(r["state"], "preheating")
        self.assertIsNone(t.project_job({"progress": 101})["progress_percent"])
        self.assertEqual(t.project_job({"progress": 1}, source="unknown")["quality"], "not_reported")

    def test_zero_ne_definit_pas_une_identite_imprimante_ou_projet(self):
        """Garde le zéro valide pour les compteurs, mais refuse de l'utiliser comme identifiant."""
        historique = t.project_history({"data": [{"printerID": FACTICE_6}]}, 0)
        notifications = t.project_notifications({"data": [{"aggregate_mode": 0}]}, 0)
        selection = t.select_active_project(
            {"data": [{"id": 0, "printer_id": FACTICE_6, "print_status": 1}]}, 0
        )
        mesure = t.project_telemetry(
            {"id": 0}, printer_id=FACTICE_6, job={"printer_id": FACTICE_6, "name": "non associé"}
        )

        self.assertEqual(historique["items"], [])
        self.assertEqual(historique["quality"], "printer_scope_required")
        self.assertEqual(notifications["items"], [])
        self.assertEqual(notifications["quality"], "printer_scope_required")
        self.assertIsNone(selection)
        self.assertEqual(mesure["detail_scope"], "printer_scope_required")
        self.assertEqual(mesure["job"]["quality"], "not_reported")
        self.assertEqual(t.project_statistics({"base": {"print_count": "0"}})["print_count"], 0)

    def test_no_raw_private_fields(self):
        raw = {
            "access_token": "SECRET",
            "user_id": FACTICE_1,
            "img": "https://private",
            "start_time": 123,
            "name": "cube",
        }
        output = json.dumps(t.project_job(raw))
        self.assertNotIn("SECRET", output)
        self.assertNotIn("https://", output)
        self.assertNotIn("start_time", output)

    def test_duplicate_and_deep_json_rejected(self):
        self.assertEqual(t._json_dict('{"progress":1,"progress":2}'), {})
        self.assertEqual(t._json_dict("[" * 2000 + "]" * 2000), {})
        self.assertIsNone(t.project_job({"device_message": '{"filename":"a","filename":"b"}'})["name"])

    def test_ace_tag_manual_empty(self):
        slots = [
            {
                "index": i,
                "edit_status": e,
                "sku": "sku",
                "type": "PLA",
                "color": [1, 2, 3, 255],
                "consumables_percent": 0,
                "status": 5,
            }
            for i, e in enumerate((0, 1, 2, 7))
        ]
        r = t.project_ace([{"id": 0, "model_id": 99, "slots": slots}])[0]
        self.assertEqual(len(r["slots"]), 4)
        self.assertEqual([s["presence"] for s in r["slots"]], ["present", "present", "absent", "unknown"])
        self.assertEqual(r["slots"][0]["information_source"], "tag_read_reported")
        self.assertEqual(r["slots"][1]["information_source"], "manual_entry_reported")
        self.assertEqual(r["slots"][0]["remaining_percent"], 0)
        self.assertEqual(r["slots"][0]["color"], "#010203")
        self.assertIsNone(r["slots"][2]["sku"])
        self.assertIsNone(r["slots"][3]["material"])
        self.assertTrue(all((s["rfid_uid"] is None for s in r["slots"])))
        self.assertNotIn("ACE 2", json.dumps(r))

    def test_ace_conflicts_and_booleans(self):
        slots = [
            {"index": 0, "edit_status": 0},
            {"index": 0, "edit_status": 2},
            {"index": 0, "edit_status": 1},
            {"index": 1, "edit_status": True},
        ]
        r = t.project_ace([{"id": 0, "slots": slots}])[0]["slots"]
        self.assertEqual(r[0]["information_source"], "conflicting_reports")
        self.assertEqual(r[0]["presence"], "unknown")
        self.assertEqual(r[1]["presence"], "unknown")
        self.assertEqual(t.project_ace([{"id": True}]), [])

    def test_ace_box_sensor_unknown(self):
        r = t.project_ace([{"id": 1}, {"id": 2}, {"id": 3}])
        self.assertEqual(len(r), 2)
        self.assertIsNone(r[0]["temperature_c"])
        self.assertIsNone(r[0]["humidity_percent"])
        self.assertFalse(r[0]["physical_validation"])

    def test_project_records_scoped_not_completed_history(self):
        data = [
            {"printer_id": FACTICE_2, "name": "other"},
            {"printer_id": FACTICE_3, "name": "mine", "print_status": 1},
        ]
        r = t.project_records({"data": data}, 7)
        self.assertEqual([v["name"] for v in r["items"]], ["mine"])
        self.assertEqual(r["quality"], "project_records")
        self.assertIn("not_verified", r["semantic_scope"])
        self.assertEqual(t.project_records({"data": data}, None)["items"], [])

    def test_records_bounded(self):
        r = t.project_records({"data": [{"printer_id": FACTICE_4, "name": "x"}] * 101}, 7)
        self.assertEqual(len(r["items"]), 20)
        self.assertTrue(r["truncated"])
        self.assertNotIn("print_count", r)

    def test_native_history_schema_exact_and_scoped(self):
        raw = {
            "data": [
                {"printerID": FACTICE_4, "gcode_name": "cube", "print_status": 2, "img": "private", "create_time": 123},
                {"printer_id": FACTICE_4, "gcode_name": "wrong schema"},
                {"printerID": FACTICE_2, "gcode_name": "other"},
            ]
        }
        r = t.project_history(raw, 7)
        self.assertEqual(len(r["items"]), 1)
        self.assertEqual(r["items"][0]["state"], "complete")
        self.assertEqual(r["source"], "sdk2.0.0.3:/v2/project/printHistory")
        self.assertNotIn("private", json.dumps(r))
        self.assertNotIn("create_time", json.dumps(r))

    def test_notifications_scopes_no_account_data_or_marking(self):

        def row(printers):
            return {
                "aggregate_mode": 0,
                "task_name": "cube",
                "reason_desc": "done",
                "alarm_code": 5,
                "printer_list": printers,
                "user_id": FACTICE_1,
                "img": "PRIVATE",
                "status": 0,
            }

        data = [
            row([{"printer_id": FACTICE_4}]),
            row([]),
            row([{"printer_id": FACTICE_2}]),
            row([{"printer_id": FACTICE_5}]),
        ]
        r = t.project_notifications({"data": data}, 7)
        self.assertEqual([x["scope"] for x in r["items"]], ["selected_printer", "account_unattributed"])
        self.assertFalse(r["items"][0]["read"])
        self.assertFalse(r["marks_read"])
        self.assertNotIn("SECRET", json.dumps(r))
        self.assertNotIn("PRIVATE", json.dumps(r))
        self.assertEqual(t.project_notifications({"data": [row([])]}, None)["items"], [])
        self.assertEqual(t.project_notifications({"data": [{"aggregate_mode": 1}]}, 7)["items"], [])

    def test_active_project_selection_not_printer_or_last_completed(self):
        self.assertIsNone(t.select_active_project({"data": [{"id": 7, "is_printing": 2}]}, 7))
        done = {"id": 9, "printer_id": FACTICE_4, "print_status": 2}
        active = {"id": 10, "printer_id": FACTICE_4, "print_status": 6}
        self.assertEqual(t.select_active_project({"data": [done, active]}, 7), active)
        self.assertIsNone(t.select_active_project({"data": [active, dict(active, id=11)]}, 7))
        self.assertIsNone(t.select_active_project({"data": [done]}, 7))

    def test_context_scoping_and_no_unsafe_assumptions(self):
        detail = {"id": 7, "is_printing": 2, "machine_data": {"size_x": 250}, "light_type": 2, "homed": True}
        r = t.control_context(detail, {"id": 9, "printer_id": FACTICE_4, "print_status": 6, "pause": 0})
        self.assertEqual((r["print_state"], r["project_id"]), ("preheating", 9))
        self.assertIsNone(r["light_type"])
        self.assertIsNone(r["homed"])
        self.assertIsNone(r["position_xyz"])
        self.assertIsNone(
            t.control_context(detail, {"id": 9, "printer_id": FACTICE_2, "print_status": 1})["project_id"]
        )
        self.assertEqual(t.control_context({"id": 7, "is_printing": 1})["print_state"], "idle")
        self.assertEqual(
            t.control_context(detail, {"printer_id": FACTICE_4, "print_status": 1, "pause": 1})["print_state"], "paused"
        )
        self.assertIsNone(
            t.control_context(detail, {"id": 9, "printer_id": FACTICE_4, "print_status": 2})["project_id"]
        )

    def test_aggregate_scope_and_source_unchanged(self):
        detail = {"base": {"print_count": 1}, "multi_color_box": [{"id": 0}]}
        before = copy.deepcopy(detail)
        r = t.project_telemetry(detail, job={"printer_id": FACTICE_2, "name": "other"}, printer_id=FACTICE_4)
        self.assertEqual(r["job"]["quality"], "not_reported")
        self.assertEqual(detail, before)
        self.assertFalse(r["commands_enabled"])
        self.assertLess(len(json.dumps(r)), 32768)

    def test_maximal_projection_bounded_and_immutable(self):
        row = {
            "aggregate_mode": 0,
            "task_name": "a" * 160,
            "reason_desc": "b" * 160,
            "printer_list": [{"printer_id": FACTICE_4}],
            "status": 1,
        }
        detail = {
            "multi_color_box": [
                {
                    "id": i,
                    "slots": [
                        {
                            "index": n,
                            "edit_status": 0,
                            "type": "P" * 80,
                            "sku": "S" * 100,
                            "color_group": [[1, 2, 3]] * 1000,
                        }
                        for n in range(4)
                    ],
                }
                for i in range(2)
            ]
        }
        notifications = {"data": [row] * 101}
        snapshot = copy.deepcopy((detail, notifications))
        result = t.project_telemetry(
            detail,
            notifications=notifications,
            history={"data": [{"printerID": FACTICE_4, "gcode_name": "x" * 160}] * 101},
            printer_id=FACTICE_4,
        )
        self.assertLess(len(json.dumps(result, allow_nan=False)), 32768)
        self.assertEqual(len(result["notifications"]["items"]), 20)
        self.assertTrue(result["notifications"]["truncated"])
        self.assertEqual((detail, notifications), snapshot)

    def test_model_capabilities_exact_s1_and_ace_generation(self):
        r = t.capabilities_model(20025, 40002)
        self.assertEqual(r["light_type"], 2)
        self.assertEqual(r["box_fan_range_default"], [0, 100])
        self.assertEqual(r["ace_max_drying_temperature_c"], 65)
        self.assertEqual(t.capabilities_model("20025", "40001")["ace_max_drying_temperature_c"], 55)
        self.assertFalse(r["commands_enabled"])
        self.assertIn("homed", r["live_state_required"])
        self.assertTrue(r["live_fan_limit_overrides_model_default"])

    def test_model_capabilities_do_not_guess_other_s1_series_or_unknown_ace(self):
        for model in (None, True, 20029, 20031, "Anycubic Kobra S1", "Kobra S1 Max"):
            r = t.capabilities_model(model, 0)
            self.assertIsNone(r["light_type"])
            self.assertIsNone(r["box_fan_range_default"])
            self.assertIsNone(r["ace_max_drying_temperature_c"])
        self.assertIsNone(t.capabilities_model(20025, True)["ace_model"])

    def test_combined_capabilities_received_range_and_ace_by_box(self):
        detail = {
            "machine_type": "20025",
            "box_fan_level_limit": [0, 3],
            "multi_color_box": [{"id": 0, "model_id": 40002}, {"id": 1, "model_id": 40001}],
        }
        before = copy.deepcopy(detail)
        result = t.capabilities_for_model("Anycubic Kobra S1", detail)
        self.assertEqual(result["box_fan_level_limit"], [0, 3])
        self.assertEqual(result["box_fan_scale"], "discrete")
        self.assertTrue(result["box_fan_limit_source"].startswith("received:"))
        self.assertEqual(result["ace_models_by_box"]["0"]["max_drying_temperature_c"], 65)
        self.assertEqual(result["ace_models_by_box"]["1"]["max_drying_temperature_c"], 55)
        self.assertIsNone(result["homed"])
        self.assertEqual(detail, before)

    def test_combined_capabilities_strict_model_unknowns_and_conflicts(self):
        result = t.capabilities_for_model("Anycubic Kobra S1", {})
        self.assertEqual(result["box_fan_level_limit"], [0, 100])
        self.assertIn("machine_type20025", result["box_fan_limit_source"])
        for model, detail in [
            ("Anycubic Kobra S1 Max", {}),
            (True, {}),
            ("Anycubic Kobra S1", {"machine_type": 20029}),
        ]:
            r = t.capabilities_for_model(model, detail)
            self.assertIsNone(r["light_type"])
            self.assertIsNone(r["box_fan_level_limit"])
        r = t.capabilities_for_model(
            20025,
            {
                "box_fan_level_limit": [0, True],
                "multi_color_box": [
                    {"id": 0, "model_id": 40002},
                    {"id": 0, "model_id": 40001},
                    {"id": 0, "model_id": 40002},
                    {"id": 1, "model_id": 0},
                ],
            },
        )
        self.assertEqual(r["ace_models_by_box"], {})
        self.assertIsNone(r["box_fan_level_limit"])
        self.assertEqual(r["box_fan_limit_source"], "received_range_not_qualified")
        self.assertFalse(r["commands_enabled"])

    def test_catalog_no_mutations(self):
        for route in t.ROUTE_CATALOG:
            self.assertNotIn("sendOrder", route["path"])
            self.assertNotIn("/delete", route["path"])
        self.assertTrue(t.ROUTE_CATALOG[-1]["eligible_for_personal_bridge"])
        self.assertEqual(t.ROUTE_CATALOG[-1]["body"]["update_status"], 0)

    def test_detail_for_another_printer_is_not_projected(self):
        detail = {"id": 8, "base": {"print_count": 99}, "multi_color_box": [{"id": 0}]}
        result = t.project_telemetry(detail, printer_id=FACTICE_4)
        self.assertEqual(result["detail_scope"], "conflicting_printer")
        self.assertIsNone(result["statistics"]["print_count"])
        self.assertEqual(result["ace_boxes"], [])
        self.assertFalse(result["commands_enabled"])

    def test_unscoped_detail_never_becomes_an_observation(self):
        detail = {"base": {"print_count": 4}}
        self.assertIsNone(t.project_telemetry(detail)["statistics"]["print_count"])
        result = t.project_telemetry(detail, printer_id=FACTICE_4)
        self.assertEqual(result["detail_scope"], "caller_scope_unverified")
        self.assertEqual(result["statistics"]["print_count"], 4)

    def test_zero_is_not_a_printer_or_project_identity(self):
        self.assertEqual(t.project_history({"data": [{"printerID": FACTICE_6}]}, 0)["items"], [])
        self.assertEqual(t.project_notifications({"data": [{"aggregate_mode": 0}]}, 0)["items"], [])
        self.assertIsNone(t.select_active_project({"data": [{"printer_id": FACTICE_4, "id": 0, "print_status": 1}]}, 7))
        self.assertEqual(
            t.project_telemetry({"id": 0}, printer_id=FACTICE_6, job={"printer_id": FACTICE_6, "name": "invalid"})[
                "job"
            ]["quality"],
            "not_reported",
        )

    def test_duplicate_ace_box_stays_unknown(self):
        first = {"id": 0, "model_id": 40002, "slots": [{"index": 0, "edit_status": 0}]}
        for boxes in ([first, dict(first)], [first, {"id": 1}, dict(first)]):
            result = t.project_telemetry({"id": 7, "multi_color_box": boxes}, printer_id=FACTICE_4)
            box = result["ace_boxes"][0]
            self.assertEqual(box["quality"], "conflicting_reports")
            self.assertIsNone(box["model_id"])
            self.assertTrue(all((s["presence"] == "unknown" for s in box["slots"])))
            self.assertEqual(result["ace_inventory_truncated"], len(boxes) > 2)

    def test_freshness_uses_timezone_and_never_grants_commands(self):
        now = datetime(2026, 10, 30, 12, 0, 0, tzinfo=timezone.utc)
        self.assertIsNone(t.freshness(None, now=now)["fresh"])
        for stamp in ("2026-10-30T11:59:30Z", "2026-10-30T12:59:30+01:00"):
            r = t.freshness(stamp, now=now)
            self.assertTrue(r["fresh"])
            self.assertEqual(r["age_seconds"], 30)
        self.assertFalse(t.freshness("2026-10-30T11:59:29Z", now=now)["fresh"])
        for stamp in ("2026-10-30T12:00:01Z", "2026-10-30T12:00:00", True, float("nan"), "bad"):
            self.assertEqual(t.freshness(stamp, now=now)["quality"], "invalid_timestamp")
        self.assertFalse(
            t.project_telemetry({"id": 7}, printer_id=FACTICE_4, observed_at=now.isoformat(), now=now)[
                "commands_enabled"
            ]
        )

    def test_conflicting_slot_beyond_budget_never_stays_present(self):
        slots = [{"index": 0, "edit_status": 0, "type": "PLA"}] + [{}] * 15 + [{"index": 0, "edit_status": 2}]
        result = t.project_ace([{"id": 0, "slots": slots}])[0]
        self.assertTrue(result["slot_reports_truncated"])
        self.assertEqual(result["quality"], "truncated_reports")
        self.assertTrue(all((s["presence"] == "unknown" and s["material"] is None for s in result["slots"])))

    def test_conflicting_box_beyond_budget_never_keeps_temperature_limit(self):
        boxes = [{"id": 0, "model_id": 40002}] + [{}] * 15 + [{"id": 0, "model_id": 40001}]
        result = t.capabilities_for_model(20025, {"multi_color_box": boxes})
        self.assertTrue(result["ace_inventory_truncated"])
        self.assertEqual(result["ace_models_by_box"], {})

    def test_freshness_invalid_clock_is_unknown(self):
        for now in (float("nan"), 1, "now", {}, True):
            self.assertEqual(t.freshness("2026-10-30T12:00:00Z", now=now)["quality"], "invalid_timestamp")


class LocalCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.input = self.root / "snapshot.json"
        self.data = {
            "printer_id": FACTICE_4,
            "model": "Anycubic Kobra S1",
            "detail": {"id": 7, "base": {"print_count": 0}, "multi_color_box": [{"id": 0, "model_id": 40002}]},
        }

    def run_cli(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = t.main([str(self.input)])
        return (code, json.loads(output.getvalue()))

    def test_json_file_to_report_without_network_or_mutation(self):
        self.input.write_text(json.dumps(self.data))
        before = self.input.read_bytes()
        code, report = self.run_cli()
        self.assertEqual(code, 0)
        self.assertEqual(report["schema"], "atelier.telemetrie.v1")
        self.assertEqual(report["statistics"]["print_count"], 0)
        self.assertEqual(report["declared_capabilities"]["ace_models_by_box"]["0"]["name"], "ACE 2 Pro")
        self.assertFalse(report["commands_enabled"])
        self.assertEqual(before, self.input.read_bytes())

    def test_invalid_json_duplicate_keys_and_nonfinite_rejected(self):
        for raw in (
            "{}",
            "[]",
            "printer_id".join(('{"', '":7,"detail":{},"', '":8}')),
            "printer_id".join(('{"', '":7,"detail":{"x":NaN}}')),
            "printer_id".join(('{"', '":7,"detail":{"x":1e999}}')),
            "[" * 2000 + "]" * 2000,
            "printer_id".join(('{"', '":7,"detail":{},"token":"TEST_PRIVATE_VALUE"}')),
        ):
            self.input.write_text(raw)
            code, report = self.run_cli()
            self.assertEqual(code, 2)
            self.assertNotIn("TEST_PRIVATE_VALUE", json.dumps(report))

    def test_symlink_and_oversize_input_refused(self):
        real = self.root / "real.json"
        real.write_text(json.dumps(self.data))
        self.input.symlink_to(real)
        self.assertEqual(self.run_cli()[0], 2)
        self.input.unlink()
        with self.input.open("wb") as stream:
            stream.truncate(1024 * 1024 + 1)
        self.assertEqual(self.run_cli()[0], 2)

    def test_conflicting_detail_cannot_become_declared_capability(self):
        self.data["detail"]["id"] = 8
        self.input.write_text(json.dumps(self.data))
        _, report = self.run_cli()
        self.assertIsNone(report["declared_capabilities"]["light_type"])


if __name__ == "__main__":
    unittest.main()
