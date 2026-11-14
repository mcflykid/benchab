"""Tests du contrôle des ponts internes sous le sommet (27/10/2026) : G-code synthétique, aucun tranchage."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchab.controles import ponts_internes as PI


def gcode(z_pont, longueur, z_haut=10.0):
    return "\n".join(
        [
            ";Z:2",
            ";TYPE:Outer wall",
            "G1 X0 Y0 E1",
            f";Z:{z_pont}",
            ";TYPE:Internal Bridge",
            "G1 X0 Y0",
            f"G1 X{longueur} Y0 E1",
            f";Z:{z_haut}",
            ";TYPE:Top surface",
            "G1 X5 Y5 E1",
        ]
    )


class Ponts(unittest.TestCase):
    def test_long_pont_sous_sommet(self):
        m = PI.mesurer(gcode(9.0, 14.0))
        self.assertEqual(m["longs_sous_sommet"], 1)
        self.assertEqual(m["portee_max_sous_sommet_mm"], 14.0)

    def test_pont_court_ignore(self):
        self.assertEqual(PI.mesurer(gcode(9.0, 6.0))["longs_sous_sommet"], 0)

    def test_pont_bas_ignore(self):
        self.assertEqual(PI.mesurer(gcode(5.0, 14.0))["longs_sous_sommet"], 0)

    def test_deplacement_non_compte(self):
        m = PI.mesurer(";Z:9\n;TYPE:Internal Bridge\nG1 X0 Y0\nG1 X20 Y0\n;Z:10\n;TYPE:Top surface\nG1 X1 Y1 E1")
        self.assertEqual(m["ponts_internes"], 0)

    def test_json_ne_peut_pas_remplacer_le_gcode_source(self):
        """Le rapport ne peut pas remplacer le G-code analysé."""
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "source.gcode"
            source.write_text(gcode(9.0, 14.0), encoding="utf-8")
            avant = source.read_bytes()

            code = PI.main([str(source), "--json", str(source)])

            self.assertEqual(code, 2)
            self.assertEqual(source.read_bytes(), avant)

    def test_sortie_json_protege_le_fichier_gcode(self):
        """La publication reçoit le fichier G-code comme entrée protégée."""
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "source.gcode"
            source.write_text(gcode(9.0, 14.0), encoding="utf-8")
            sortie = Path(d) / "rapport.json"
            with patch.object(PI, "ecrire_json_neuf") as ecrire:
                code = PI.main([str(source), "--json", str(sortie)])
            self.assertEqual(code, 0)
            ecrire.assert_called_once()
            self.assertEqual(ecrire.call_args.kwargs["entrees"], [source])

    def test_sortie_json_neuve_est_lisible_et_source_inchangee(self):
        """Une sortie neuve contient le bilan sans modifier la source."""
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "source.gcode"
            source.write_text(gcode(9.0, 14.0), encoding="utf-8")
            avant = source.read_bytes()
            sortie = Path(d) / "rapport.json"

            code = PI.main([str(source), "--json", str(sortie)])

            self.assertEqual(code, 0)
            self.assertEqual(json.loads(sortie.read_text(encoding="utf-8"))["statut"], "PASS")
            self.assertEqual(source.read_bytes(), avant)


if __name__ == "__main__":
    unittest.main()
