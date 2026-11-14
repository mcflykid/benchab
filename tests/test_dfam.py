"""Régressions du faux rapport complet d'un assemblage partiellement mesuré."""

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchab.conception import dfam


class DfamLocalTests(unittest.TestCase):
    def test_nonfinite_or_impossible_measurement_options_are_refused(self):
        for arguments in (
            ["--angle-limit", "nan"],
            ["--angle-limit", "inf"],
            ["--angle-limit=-1"],
            ["--angle-limit", "91"],
            ["--samples", "0"],
            ["--samples=-2"],
            ["--angle-l=nan"],
            ["--sam=0"],
        ):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                dfam.valider_arguments(["measure", "fixture.stl", *arguments])

    def test_physical_angle_boundaries_and_positive_budget_are_accepted(self):
        for angle in ("0", "45", "90"):
            dfam.valider_arguments(["measure", "fixture.stl", "--samples", "1", "--angle-limit", angle])

    def test_missing_body_marks_partial_without_losing_measured_body(self):
        facts = {
            "samples_valid": 12,
            "min_mm": 10.0,
            "per_body": [
                {"body": 0, "samples_valid": 12, "min_mm": 10.0},
                {"body": 1, "samples_valid": 0, "min_mm": None},
            ],
        }
        result = dfam.couverture_epaisseur(facts)
        self.assertIn("error", result)
        self.assertEqual(result["unmeasured_bodies"], [1])
        self.assertEqual(result["min_mm"], 10.0)
        self.assertEqual(result["per_body"], facts["per_body"])
        self.assertNotIn("error", facts)

    def test_degenerate_single_body_is_not_a_complete_thickness_measure(self):
        result = dfam.couverture_epaisseur({"samples": 0, "note": "no positive face area"})
        self.assertIn("error", result)
        self.assertEqual(result["note"], "no positive face area")

    def test_fully_measured_assembly_keeps_its_result(self):
        result = {"samples_valid": 20, "per_body": [{"body": 0, "samples_valid": 12}, {"body": 1, "samples_valid": 8}]}
        self.assertIs(dfam.couverture_epaisseur(result), result)

    def test_original_error_is_preserved(self):
        result = {"error": "ray casting produced no hits"}
        self.assertIs(dfam.couverture_epaisseur(result), result)

    def test_changed_upstream_is_refused_before_import(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "changed.py"
            source.write_text('raise RuntimeError("must not execute")')
            with self.assertRaisesRegex(ValueError, "Skill DfAM modifie"):
                dfam.charger(source)

    def test_only_the_verified_snapshot_runs_if_source_changes_after_read(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "upstream.py"
            trusted = b'def _wall_facts(*args, **kwargs): return {"samples_valid": 1}\ndef main(): return 23\n'
            source.write_bytes(trusted)
            read_bytes = Path.read_bytes

            def read_then_change(path):
                data = read_bytes(path)
                if path == source:
                    source.write_text('raise RuntimeError("changed content executed")')
                return data

            with (
                patch.object(dfam, "SOURCE_SHA256", hashlib.sha256(trusted).hexdigest()),
                patch.object(Path, "read_bytes", read_then_change),
            ):
                module = dfam.charger(source)
            self.assertEqual(module.main(), 23)
            self.assertIn("changed content executed", source.read_text())


if __name__ == "__main__":
    unittest.main()
