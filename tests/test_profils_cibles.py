"""Les noms proches et les compatibilités inconnues ne passent pas le filtre S1."""

import json
import tempfile
import unittest
from pathlib import Path

from benchab.machine.profils_cibles import inventorier, selectionner


class Profils(unittest.TestCase):
    def test_modele_exact_et_buse(self):
        for model in ["Anycubic Kobra S1 Max", "Anycubic Kobra 3", None]:
            self.assertFalse(selectionner({"printer_model": model, "nozzle_diameter": ["0.4"]}, "machine", [])[0])
        self.assertTrue(
            selectionner({"printer_model": "Anycubic Kobra S1", "nozzle_diameter": ["0.4"]}, "machine", [])[0]
        )
        self.assertFalse(
            selectionner({"printer_model": "Anycubic Kobra S1", "nozzle_diameter": ["0.6"]}, "machine", [])[0]
        )

    def test_pas_de_compatibilite_par_sous_chaine(self):
        cible = ["Anycubic Kobra S1 0.4 nozzle"]
        for values in [{}, {"compatible_printers": []}, {"compatible_printers": ["Anycubic Kobra S1 Max 0.4 nozzle"]}]:
            self.assertFalse(selectionner(values, "filament", cible)[0])
        self.assertTrue(selectionner({"compatible_printers": cible}, "filament", cible)[0])

    def test_heritage_et_sources_preservees(self):
        with tempfile.TemporaryDirectory() as d:
            r = Path(d)
            for cat in ["machine", "process", "filament"]:
                (r / cat).mkdir()
            rows = {
                "machine": [{"name": "S1", "printer_model": "Anycubic Kobra S1", "nozzle_diameter": ["0.4"]}],
                "process": [
                    {"name": "parent", "instantiation": "false", "compatible_printers": ["S1"]},
                    {"name": "enfant", "inherits": "parent", "instantiation": "true"},
                ],
                "filament": [{"name": "incomplet", "inherits": "absent", "compatible_printers": ["S1"]}],
            }
            for cat, items in rows.items():
                for row in items:
                    (r / cat / (row["name"] + ".json")).write_text(json.dumps(dict(row, type=cat)))
            before = {str(p): p.read_bytes() for p in r.rglob("*.json")}
            rep = inventorier(r)
            self.assertEqual([x["nom"] for x in rep["profils"] if x["retenu"]], ["S1", "enfant"])
            self.assertEqual(before, {str(p): p.read_bytes() for p in r.rglob("*.json")})


if __name__ == "__main__":
    unittest.main()


def test_modele_et_buse_sont_lus_dans_la_configuration(tmp_path, monkeypatch):
    configuration = tmp_path / "machine.toml"
    configuration.write_text('[machine]\nnom="Fixture"\nmodeles_slicer=["Fixture"]\nbuse_mm=0.6\n')
    monkeypatch.setenv("BENCHAB_MACHINE", str(configuration))
    assert selectionner({"printer_model": "Fixture", "nozzle_diameter": ["0.6"]}, "machine", [])[0]
    assert not selectionner({"printer_model": "Fixture", "nozzle_diameter": ["0.4"]}, "machine", [])[0]
