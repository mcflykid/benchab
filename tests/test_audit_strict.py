"""Régressions de couverture et budgets, données synthétiques exclusivement."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from benchab.controles import premiere_couche as PC
from benchab.controles.debit_gcode import analyser
from benchab.controles.gcode_interpreter import Interpreter


class Strict(unittest.TestCase):
    def test_invalides_preservent_tout_etat_modal(self):
        for bad in (
            "G1 X3 X4 E2",
            "G92 E2 E3",
            "G92",
            "M83 X1",
            "M82 E2",
            "G90 X2",
            "G91 F2",
            "T1 X3",
            "T1.2",
            "G1 X3 F0",
            "G1 X3 F-1",
            "G1 X3 Enan",
            "G1 X3 garbage",
            "G2 X3 E1",
            "G9111 extruderTemp=210 extruderTemp=220",
        ):
            with self.subTest(bad=bad):
                it = Interpreter()
                it.feed_line("M82")
                it.feed_line("T0")
                it.feed_line("G92 X10 E5")
                it.feed_line("G1 E4 F600")
                before = (it.xyz, it.relative_xyz, it.relative_e, it.tool, dict(it.e), dict(it.retracted), it.feed)
                self.assertIsNone(it.feed_line(bad))
                self.assertTrue(it.errors)
                self.assertEqual((it.xyz, it.relative_xyz, it.relative_e, it.tool, it.e, it.retracted, it.feed), before)

    def test_opcode_m_inconnu_invalide_couverture(self):
        for command in ("M999 S123", "M600"):
            it = Interpreter()
            it.feed_line(command)
            self.assertTrue(it.errors)

    def test_affichage_anycubic_explicite_et_sans_effet(self):
        it = Interpreter()
        it.feed_line("M73 P0 R19.5 QuietR34.3 SportR17.0")
        self.assertFalse(it.errors)
        self.assertEqual(it.last_event["kind"], "progress")
        it.feed_line("M73 P2 P3")
        self.assertTrue(it.errors)

    def test_marqueur_objet_invalide_ne_change_pas_identite(self):
        it = Interpreter()
        it.feed_line("EXCLUDE_OBJECT_START NAME=A")
        for bad in ("EXCLUDE_OBJECT_START NAME=B", "EXCLUDE_OBJECT_END NAME=B", "EXCLUDE_OBJECT_ENDgarbage"):
            it.feed_line(bad)
            self.assertEqual(it.object, "A")
            self.assertTrue(it.errors)
        it.feed_line("EXCLUDE_OBJECT_END NAME=A")
        self.assertIsNone(it.object)

    def test_modes_dettes_et_succession_apres_invalide(self):
        it = Interpreter()
        it.feed_line("M83")
        it.feed_line("T0")
        it.feed_line("G1 E-2")
        it.feed_line("M82 X4")
        it.feed_line("G91")
        m = it.feed_line("G1 X10 E3 F600")
        self.assertEqual(m["deposited"], 1)
        self.assertEqual(m["end"], (10.0, 0.0, 0.0))
        self.assertTrue(it.errors)


class Debit(unittest.TestCase):
    def archive(self, expected=("A", "B"), actual=("A", "B"), caps=("32", "1"), extra=None):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        p = Path(t.name) / "s.3mf"
        g = "M83\nT0\n"
        for name in actual:
            g += (
                f"EXCLUDE_OBJECT_START NAME={name}\n"
                f"G1 X{10 * (1 + actual.index(name))} E1 F600\n"
                f"EXCLUDE_OBJECT_END NAME={name}\n"
            )
        if extra:
            g += extra
        with ZipFile(p, "w") as z:
            z.writestr("Metadata/project_settings.config", json.dumps({
                "filament_max_volumetric_speed": caps, "filament_diameter": ["1.75"] * max(1, len(caps))
            }))
            z.writestr(
                "Metadata/slice_info.config",
                '<config><plate><metadata key="index" value="1"/>'
                + "".join(f'<object name="{n}"/>' for n in expected)
                + '<filament id="1"/><filament id="2"/></plate></config>',
            )
            z.writestr("Metadata/plate_1.gcode", g)
        return p

    def test_un_deux_trois_objets_et_manquant(self):
        for names in [("A",), ("A", "B"), ("A", "B", "C")]:
            with self.subTest(names=names):
                p = self.archive(names, names)
                self.assertTrue(analyser(p)["conforme"])
                p = self.archive(names, names[:-1])
                r = analyser(p)
                self.assertFalse(r["conforme"])
                self.assertFalse(r["plaques"][0]["couverture_complete"])

    def test_objet_sans_depot_et_inconnu(self):
        for extra in ("EXCLUDE_OBJECT_START NAME=B\nG1 E-1\n", "EXCLUDE_OBJECT_START NAME=C\nG1 X40 E1 F600\n"):
            r = analyser(self.archive(actual=("A",), extra=extra))
            self.assertFalse(r["conforme"])

    def test_second_filament_minuscule_non_masque_par_percentile(self):
        extra = "EXCLUDE_OBJECT_START NAME=A\nT1\nG1 X20.01 E1 F600\n"
        r = analyser(self.archive(extra=extra))
        self.assertFalse(r["conforme"])
        self.assertTrue(any(not t["conforme"] for o in r["plaques"][0]["objets"] for t in o.get("outils", [])))

    def test_plafonds_invalides_et_plaque_supplementaire(self):
        for cap in ("0", "-1", "nan", "inf", None):
            self.assertFalse(analyser(self.archive(caps=("32", cap)))["conforme"])
        p = self.archive()
        with ZipFile(p, "a") as z:
            z.writestr("Metadata/plate_2.gcode", "G1 X1")
        with self.assertRaises(ValueError):
            analyser(p)

    def test_manifeste_prioritaire_et_couverture_non_deduite_des_seuls_mouvements(self):
        p = self.archive(expected=())
        self.assertFalse(analyser(p)["conforme"])
        self.assertTrue(analyser(p, attendus={1: ["A", "B"]})["conforme"])
        self.assertFalse(analyser(p, attendus={1: ["A", "B", "C"]})["conforme"])


class Premiere(unittest.TestCase):
    G = "M83\n;Z:.2\n;TYPE:Outer wall\nG1 X30 E1 F600\n"

    def test_pas_et_seuils_invalides(self):
        for value in (0, -1, float("nan"), float("inf"), 1e-300):
            with self.subTest(value=value):
                self.assertEqual(PC.controler(self.G, pas=value)["statut"], "NON VÉRIFIÉ")

    def test_largeurs_et_coordonnees_invalides(self):
        for value in ("0", "-1", "nan", "inf", "1000000"):
            r = PC.controler("M83\n;Z:.2\n;WIDTH:" + value + "\n;TYPE:Outer wall\nG1 X30 E1 F600")
            self.assertEqual(r["statut"], "NON VÉRIFIÉ")
        self.assertEqual(PC.controler(self.G.replace("X30", "X999999999"))["statut"], "NON VÉRIFIÉ")

    def test_limite_grille_avant_allocation(self):
        with patch.object(PC.np, "zeros", side_effect=AssertionError("allocation interdite")):
            r = PC.controler(self.G.replace("X30", "X900000"))
        self.assertEqual(r["statut"], "NON VÉRIFIÉ")
        self.assertIn("budget", r["detail"])

    def test_allocation_echec_explicite(self):
        with patch.object(PC.np, "zeros", side_effect=MemoryError()):
            self.assertEqual(PC.controler(self.G)["statut"], "NON VÉRIFIÉ")

    def test_grille_normale_reste_analysee(self):
        self.assertEqual(PC.controler(self.G)["statut"], "PASS")


if __name__ == "__main__":
    unittest.main()
