"""Régressions audit 29/10 : uniquement données synthétiques, aucune impression."""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from benchab.controles import revue_locale as RL

C = Path(RL.__file__).parent


def load(path):
    s = importlib.util.spec_from_file_location(path.stem, path)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


class Audit(unittest.TestCase):
    def test_releve_thermique_reutilise_macro_et_evenements(self):
        """Les consignes en °C, outils et extinction sont relevés sans verdict thermique ajouté."""
        p = self.archive("G9111 extruderTemp=210 bedTemp=60\nM104 S205\nM109 S200\nM104 S0\n")
        place = self.d / "placement.json"
        place.write_text(json.dumps({"objects": [], "plates": [{"name": "P"}]}))
        out = self.d / "rapport.json"
        self.runcli("verifier_reglages.py", p, place, out)
        plaque = json.loads(out.read_text())["plaques"][0]
        self.assertEqual(plaque["temperatures_buse"], [0, 200, 205, 210])
        self.assertEqual(
            [(e["commande"], e["temperature_c"], e["extinction"]) for e in plaque["consignes_buse"]],
            [("G9111", 210.0, False), ("M104", 205.0, False), ("M109", 200.0, False), ("M104", 0.0, True)],
        )
        self.assertEqual([e["ligne"] for e in plaque["consignes_buse"]], [1, 2, 3, 4])
        self.assertTrue(all(e["outil"] is None for e in plaque["consignes_buse"]))

    def test_temperature_identifie_cible_explicite_sans_doubler_les_commentaires(self):
        """Une cible T explicite prime sur l’outil actif ; macro invalide et commentaires exclus."""
        p = self.archive("T1\nM104 T0 S215\n; commentaire\nG9111 extruderTemp=nan\nM109 S210\n")
        place = self.d / "placement.json"
        place.write_text(json.dumps({"objects": [], "plates": [{"name": "P"}]}))
        out = self.d / "rapport.json"
        self.runcli("verifier_reglages.py", p, place, out)
        plaque = json.loads(out.read_text())["plaques"][0]
        self.assertEqual([(e["outil"], e["temperature_c"]) for e in plaque["consignes_buse"]], [(0, 215), (1, 210)])
        self.assertEqual(plaque["temperatures_buse"], [210, 215])

    def test_temperature_attendue_par_parametre_r_est_relevee(self):
        """M109 R en °C reste une consigne, distincte d’un acquittement thermique."""
        p = self.archive("M109 R210\nM104 S0\n")
        place = self.d / "placement.json"
        place.write_text(json.dumps({"objects": [], "plates": [{"name": "P"}]}))
        out = self.d / "rapport.json"
        self.runcli("verifier_reglages.py", p, place, out)
        plaque = json.loads(out.read_text())["plaques"][0]
        self.assertEqual(plaque["temperatures_buse"], [0, 210])
        self.assertEqual([e["temperature_c"] for e in plaque["consignes_buse"]], [210, 0])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def archive(self, g, cap=["32"], fil=["1"]):
        p = self.d / "t.3mf"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr(
                "Metadata/project_settings.config",
                json.dumps({"initial_layer_print_height": ".2", "filament_max_volumetric_speed": cap,
                            "filament_diameter": ["1.75"] * max(1, len(cap))}),
            )
            z.writestr(
                "Metadata/slice_info.config",
                '<config><plate><metadata key="index" value="1"/>'
                + "".join('<filament id="' + f + '"/>' for f in fil)
                + "</plate></config>",
            )
            z.writestr("Metadata/plate_1.gcode", g)
        return p

    def runcli(self, name, *args):
        return subprocess.run(
            [sys.executable, "-B", "-m", "benchab.controles." + Path(name).stem, *map(str, args)],
            capture_output=True,
            text=True,
        )

    def debit(self, g, cap=["32"], fil=["1"]):
        out = self.d / "r.json"
        p = self.runcli("debit_gcode.py", self.archive(g, cap, fil), "--sortie", out)
        return p, json.loads(out.read_text()) if out.exists() else {}

    def test_B02_B16_skirt_ne_prouve_pas_brim(self):
        p = self.archive("M83\nEXCLUDE_OBJECT_START NAME=Piece\n;TYPE:Skirt\nG1 X10 E1 F600\n")
        place = self.d / "place.json"
        place.write_text(
            json.dumps(
                {
                    "objects": [
                        {
                            "name": "Piece",
                            "plate": 1,
                            "xy": [0, 0],
                            "size": [10, 10],
                            "reglages": {"brim_type": "outer_only"},
                        }
                    ],
                    "plates": [{"name": "P"}],
                }
            )
        )
        out = self.d / "r.json"
        r = self.runcli("verifier_reglages.py", p, place, out)
        data = json.loads(out.read_text())
        self.assertFalse(data["tout_conforme"])
        self.assertNotEqual(r.returncode, 0)

    def test_B02_objet_absent_refuse(self):
        p = self.archive("M83\n")
        place = self.d / "p.json"
        place.write_text(
            json.dumps(
                {
                    "objects": [
                        {
                            "name": "Piece",
                            "plate": 1,
                            "xy": [0, 0],
                            "size": [10, 10],
                            "reglages": {"outer_wall_speed": "30"},
                        }
                    ],
                    "plates": [{"name": "P"}],
                }
            )
        )
        out = self.d / "r.json"
        r = self.runcli("verifier_reglages.py", p, place, out)
        self.assertFalse(json.loads(out.read_text())["tout_conforme"])
        self.assertNotEqual(r.returncode, 0)

    def test_purge_hors_objet_exclue_et_objet_inconnu_bloque(self):
        prefix = (
            "M83\n;TYPE:Custom\nG1 X5 E1 F600\nEXCLUDE_OBJECT_START NAME=Piece\n"
            ";TYPE:Brim\nG1 X10 E1\nEXCLUDE_OBJECT_END NAME=Piece\n"
        )
        place = self.d / "p.json"
        place.write_text(
            json.dumps(
                {
                    "objects": [
                        {
                            "name": "Piece",
                            "plate": 1,
                            "xy": [0, 0],
                            "size": [10, 10],
                            "reglages": {"brim_type": "outer_only"},
                        }
                    ],
                    "plates": [{"name": "P"}],
                }
            )
        )
        out = self.d / "r.json"
        r = self.runcli("verifier_reglages.py", self.archive(prefix), place, out)
        self.assertTrue(json.loads(out.read_text())["tout_conforme"])
        self.assertEqual(r.returncode, 0, r.stderr)
        out = self.d / "r-inconnu.json"
        r = self.runcli(
            "verifier_reglages.py", self.archive(prefix + "EXCLUDE_OBJECT_START NAME=Inconnu\nG1 X20 E1\n"), place, out
        )
        self.assertFalse(json.loads(out.read_text())["tout_conforme"])
        self.assertEqual(r.returncode, 1, r.stderr)

    def test_B04_vide_refuse(self):
        p = self.d / "vide.gcode"
        p.write_text("")
        r = self.runcli("premiere_couche.py", p)
        self.assertNotEqual(r.returncode, 0)

    def test_B05_plafond_absent_refuse(self):
        r, d = self.debit("M83\nT0\nEXCLUDE_OBJECT_START NAME=Piece\nG1 X10 E1 F600\n", [], ["1"])
        self.assertFalse(d["conforme"])
        self.assertNotEqual(r.returncode, 0)

    def test_B06_absolu_equivalent_relatif(self):
        pre = "T0\nEXCLUDE_OBJECT_START NAME=Piece\nG1 X0 Y0 F600\n"
        r, a = self.debit("M83\n" + pre + "G1 X10 E1\nG1 X20 E1\n")
        r, b = self.debit("M82\nG92 E0\n" + pre + "G1 X10 E1\nG1 X20 E2\n")
        self.assertIsNotNone(a["plaques"][0]["objets"][0]["debit_p99"])
        self.assertEqual(a["plaques"][0]["objets"][0]["debit_p99"], b["plaques"][0]["objets"][0]["debit_p99"])

    def test_B07_support_ailleurs_ne_justifie_pas_geometrie(self):
        zo = {"fichier": "Piece.stl", "type": "surplombs"}
        self.assertIsNone(RL.juger_geometrie(zo, {1: {"min_s": 10, "support": True}}, {1: ";Piece"}))

    def test_B08_retraction_absolue_non_support(self):
        m = RL.mesurer_plaque("M82\nG92 E10\n;Z:0.2\n;TYPE:Support\nG1 X20 Y20 E9 F600\n")
        self.assertFalse(m["support"])

    def test_B21_retraction_non_deposee(self):
        m = load(C.parent / "fabrication/remplissage_localise.py")
        a = m.analyser_gcode("M82\nG92 E10\n;TYPE:Sparse infill\nG1 X10 E9\n", {"zone": [-1, -1, 20, 20, -1, 1]})
        self.assertEqual(a["zone"]["Sparse infill"], 0)


class Modal(unittest.TestCase):
    def parse(self, g):
        from benchab.controles.gcode_interpreter import Interpreter

        it = Interpreter()
        moves = [m for line in g.splitlines() if (m := it.feed_line(line))]
        return it, moves

    def test_modes_g92_coordonnees_manquantes(self):
        it, m = self.parse("M82\nG92 X10 Y20 Z.2 E100\nG1 X20 E101 F600\nG91\nG1 Y5 E102\nG92 X0 E0\nM83\nG1 X2 E1")
        self.assertFalse(it.errors)
        self.assertEqual([v["deposited"] for v in m], [1, 1, 1])
        self.assertEqual(m[-1]["end"], (12.0, 5.0, 0.0))

    def test_dette_retraction_et_reprise_partielle(self):
        it, m = self.parse("M83\nG1 E-2\nG1 X10 E1 F600\nG1 X20 E2")
        self.assertEqual([v["deposited"] for v in m], [0, 0, 1])
        self.assertEqual(m[-1]["deposition_start"], (15.0, 0.0, 0.0))
        self.assertEqual(m[-1]["deposited_length"], 5)

    def test_g92_nefface_pas_dette(self):
        it, m = self.parse("M83\nG1 E-2\nG92 E0\nG1 X10 E2 F600")
        self.assertEqual(m[-1]["deposited"], 0)

    def test_positions_et_dette_sur_axe_partage(self):
        """La dette en mm survit aux changements d’outil et de repère ; l’étiquette d’outil reste correcte."""
        it, m = self.parse(
            "M82\nT0\nG92 E100\nG1 E99\nT1\nG92 E200\nG1 X10 E202 F600\nT0\nG92 E100\nG1 X20 E100\nG1 X30 E101"
        )
        self.assertEqual([v["deposited"] for v in m], [0, 1, 0, 1])
        self.assertEqual(m[-1]["tool"], 0)

    def test_commandes_non_couvertes_et_absence_mode(self):
        for command in [
            "G2 X10 E1 I5",
            "G20",
            "G10",
            "M221 S90",
            "G5 X2 E1",
            "G1 Xnan E1",
            "G1 X1 EInfinity",
            "G1 X10 A20 E1",
        ]:
            with self.subTest(command=command):
                it, m = self.parse("M83\n" + command)
                self.assertTrue(it.errors)
        it, m = self.parse("G1 X10 E1")
        self.assertTrue(it.errors)

    def test_B06_facteur_vitesse_m220_invalide_couverture(self):
        it, m = self.parse("M83\nG1 X10 E1 F600\nM220 S200\nG1 X20 E1")
        self.assertTrue(it.errors)
        self.assertTrue(any("M220" in e for e in it.errors))

    def test_B06_g92_malforme_invalide_sans_changer_etat(self):
        for command in ["G92 Xnan", "G92 Enan", "G92 X1 Enan", "G92 EInfinity", "G92 A1", "G92 X1 F600"]:
            with self.subTest(command=command):
                it, m = self.parse("M82\nG92 X10 E5\n" + command)
                self.assertTrue(it.errors)
                self.assertEqual(it.xyz, (10.0, 0.0, 0.0))
                self.assertEqual(it.e[None], 5.0)

    def test_opcode_inconnu_avec_coordonnees_refuse(self):
        it, m = self.parse("M83\nC X10 E1\nG1 X20 E1 F600")
        self.assertTrue(it.errors)
        it, m = self.parse("M83\n;C X10 E1\nG1 X20 E1 F600")
        self.assertFalse(it.errors)

    def test_set_velocity_limit_documente_evenement_sans_mouvement(self):
        it, m = self.parse("SET_VELOCITY_LIMIT SQUARE_CORNER_VELOCITY=9")
        self.assertFalse(it.errors)
        self.assertFalse(m)
        self.assertEqual(it.last_event["parameters"], {"SQUARE_CORNER_VELOCITY": 9.0})
        it, m = self.parse(
            "SET_VELOCITY_LIMIT VELOCITY=600 ACCEL=10000 MINIMUM_CRUISE_RATIO=0.5 SQUARE_CORNER_VELOCITY=0"
        )
        self.assertFalse(it.errors)
        self.assertEqual(it.last_event["command"], "SET_VELOCITY_LIMIT")

    def test_set_velocity_limit_malforme_refuse(self):
        for command in [
            "SET_VELOCITY_LIMIT INCONNU=2",
            "SET_VELOCITY_LIMIT ACCEL=nan",
            "SET_VELOCITY_LIMIT VELOCITY=inf",
            "SET_VELOCITY_LIMIT ACCEL=-1",
            "SET_VELOCITY_LIMIT SQUARE_CORNER_VELOCITY=-1",
            "SET_VELOCITY_LIMIT MINIMUM_CRUISE_RATIO=1",
            "SET_VELOCITY_LIMIT MINIMUM_CRUISE_RATIO=1.1",
            "SET_VELOCITY_LIMIT ACCEL=1000 ACCEL=2000",
            "SET_VELOCITY_LIMIT ACCEL=",
            "SET_VELOCITY_LIMIT X10",
        ]:
            with self.subTest(command=command):
                it, m = self.parse(command)
                self.assertTrue(it.errors)

    def test_velocity_orca_ancien_et_nouveau_coherents(self):
        for accel in (500, 2000, 2500, 5000, 10000):
            with self.subTest(accel=accel):
                command = f"SET_VELOCITY_LIMIT ACCEL={accel} ACCEL_TO_DECEL={accel / 2} MINIMUM_CRUISE_RATIO=0.5"
                it, m = self.parse(command)
                self.assertFalse(it.errors)
                self.assertFalse(m)
                self.assertEqual(it.last_event["parameters"]["ACCEL_TO_DECEL"], accel / 2)
                self.assertEqual(it.last_event["relation_ancien_nouveau"], "coherente")
                self.assertEqual(it.last_event["application_firmware"], "NON VÉRIFIÉ")
                self.assertFalse(it.last_event["dynamique_simulee"])
        # Ancien dialecte : lissage borné par ACCEL, pas une accélération à sommer.
        it, m = self.parse("SET_VELOCITY_LIMIT ACCEL=1000 ACCEL_TO_DECEL=2000 MINIMUM_CRUISE_RATIO=0")
        self.assertFalse(it.errors)
        self.assertEqual(it.last_event["parameters"]["ACCEL_TO_DECEL"], 2000)
        it, m = self.parse("SET_VELOCITY_LIMIT ACCEL_TO_DECEL=250")
        self.assertFalse(it.errors)
        self.assertEqual(it.last_event["relation_ancien_nouveau"], "non_applicable")

    def test_velocity_relation_ambigue_ou_incoherente_refusee(self):
        for tail in (
            "ACCEL=1000 ACCEL_TO_DECEL=500 MINIMUM_CRUISE_RATIO=0.6",
            "ACCEL_TO_DECEL=500 MINIMUM_CRUISE_RATIO=0.5",
            "ACCEL_TO_DECEL=0",
            "ACCEL_TO_DECEL=-1",
            "ACCEL_TO_DECEL=inf",
            "ACCEL_TO_DECEL=1e999",
            "ACCEL_TO_DECEL=1_000",
            "ACCEL_TO_DECEL=true",
            "ACCEL_TO_DECEL=2 ACCEL_TO_DECEL=3",
        ):
            with self.subTest(tail=tail):
                it, m = self.parse("SET_VELOCITY_LIMIT " + tail)
                self.assertTrue(it.errors)
                self.assertEqual(it.last_event["kind"], "unsupported")
                self.assertEqual(it.last_event["parameters"], {})

    def test_pressure_advance_grammaire_sans_commande_machine(self):
        for command in (
            "SET_PRESSURE_ADVANCE ADVANCE=0.035; Override pressure advance value",
            "SET_PRESSURE_ADVANCE EXTRUDER=extruder ADVANCE=0 SMOOTH_TIME=0",
            "set_pressure_advance extruder=extruder1 advance=3.5e-2 smooth_time=.2",
            "SET_PRESSURE_ADVANCE",
        ):
            with self.subTest(command=command):
                it, m = self.parse(command)
                self.assertFalse(it.errors)
                self.assertFalse(m)
                self.assertEqual(it.last_event["kind"], "pressure_advance")
                self.assertEqual(it.last_event["application_firmware"], "NON VÉRIFIÉ")
                self.assertFalse(it.last_event["compensation_extrusion_simulee"])

    def test_pressure_advance_malforme_refuse(self):
        for tail in (
            "ADVANCE=-.1",
            "ADVANCE=nan",
            "ADVANCE=inf",
            "ADVANCE=1e999",
            "ADVANCE=true",
            "SMOOTH_TIME=-.01",
            "SMOOTH_TIME=.200001",
            "ADVANCE=",
            "ADVANCE=.01 ADVANCE=.02",
            "EXTRUDER=",
            "EXTRUDER=../inconnu",
            "EXTRUDER=extruder EXTRUDER=autre",
            "INCONNU=2",
            "X10",
        ):
            with self.subTest(tail=tail):
                it, m = self.parse("SET_PRESSURE_ADVANCE " + tail)
                self.assertTrue(it.errors)
                self.assertEqual(it.last_event["kind"], "unsupported")

    def test_dynamique_ne_change_pas_trajectoire_ni_extrusion_nominales(self):
        prefix = "M83\nT0\nG1 E-1\nG1 X5 E.5 F600\n"
        suffix = "G1 X10 E1 F1200\nG1 X20 E1\n"
        commands = (
            "SET_PRESSURE_ADVANCE ADVANCE=.035\n"
            "SET_VELOCITY_LIMIT ACCEL=500 ACCEL_TO_DECEL=250 MINIMUM_CRUISE_RATIO=.5\n"
        )
        baseline, a = self.parse(prefix + suffix)
        it, b = self.parse(prefix + commands + suffix)
        self.assertFalse(it.errors)
        for key in ("start", "end", "extrusion", "deposited", "deposition_start", "deposited_length", "feed", "tool"):
            self.assertEqual([m[key] for m in a], [m[key] for m in b])
        self.assertEqual(it.retracted, baseline.retracted)
        self.assertEqual(it.e, baseline.e)

    def test_setup_anycubic_sans_erreur_inventee(self):
        it, m = self.parse(
            "G9111 extruderTemp=220 bedTemp=55\nM104 S220\nM106 S255\nM900 K.03\nG90\nM83\nG1 X10 E1 F600"
        )
        self.assertFalse(it.errors)

    def test_commandes_compactes_commentaires_et_g10_pas_g1(self):
        it, m = self.parse("N10 M83*22\nG1X10Y5E1F600 ; commentaire E100\nG10 X20 E100")
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0]["end"], (10.0, 5.0, 0.0))
        self.assertTrue(it.errors)

    def test_parseurs_voisins_ne_comptent_pas_retraction(self):
        from benchab.controles import colonnes_isolees as CI
        from benchab.controles import zones_locales as ZL

        g = "M82\nG92 E10\n;LAYER_CHANGE\n;Z:.2\n;TYPE:Bridge\nG1 X20 E9 F600"
        self.assertFalse(ZL.analyser_gcode(g)["zones"])
        self.assertFalse(CI.couches(g)[0]["piece"])


class Couverture(unittest.TestCase):
    setUp = Audit.setUp
    tearDown = Audit.tearDown
    archive = Audit.archive
    runcli = Audit.runcli
    debit = Audit.debit

    def test_plafonds_nuls_nonfinis_et_multifilaments(self):
        g = "M83\nT0\nEXCLUDE_OBJECT_START NAME=Piece\nG1 X10 E1 F600\n"
        for cap, fil in [(["0"], ["1"]), (["nan"], ["1"]), (["inf"], ["1"]), (["32"], ["1", "2"])]:
            with self.subTest(cap=cap, fil=fil):
                r, d = self.debit(g, cap, fil)
                self.assertFalse(d["conforme"])
                self.assertEqual(r.returncode, 2)
                self.assertEqual(r.returncode, d["code"])

    def test_debit_vide_et_arc_non_conformes(self):
        for g in ["M83\n", "M83\nT0\nEXCLUDE_OBJECT_START NAME=Piece\nG2 X10 E1 I5 F600"]:
            r, d = self.debit(g)
            self.assertFalse(d["conforme"])
            self.assertNotEqual(r.returncode, 0)

    def test_filament_outil_contradictoire(self):
        r, d = self.debit("M83\nT1\nEXCLUDE_OBJECT_START NAME=Piece\nG1 X10 E1 F600\n")
        self.assertFalse(d["conforme"])

    def test_brim_jupe_et_suffixe_objet_exact(self):
        cas = [("Skirt", False), ("Brim", True), ("Skirt\nG1 X20 E1\n;TYPE:Brim", True)]
        for indice, (kind, expected) in enumerate(cas):
            with self.subTest(kind=kind):
                p = self.archive(
                    "M83\nEXCLUDE_OBJECT_START NAME=Piece_id_0_copy_0\n;TYPE:" + kind + "\nG1 X10 E1 F600\n"
                )
                place = self.d / "p.json"
                place.write_text(
                    json.dumps(
                        {
                            "objects": [
                                {
                                    "name": "Piece",
                                    "plate": 1,
                                    "xy": [0, 0],
                                    "size": [10, 10],
                                    "reglages": {"brim_type": "outer_only"},
                                }
                            ],
                            "plates": [{"name": "P"}],
                        }
                    )
                )
                out = self.d / f"r-{indice}.json"
                r = self.runcli("verifier_reglages.py", p, place, out)
                self.assertEqual(json.loads(out.read_text())["tout_conforme"], expected, r.stderr)

    def test_vitesse_couture_absentes_et_homonyme_refuses(self):
        for indice, (obj, settings) in enumerate([
            ("Piece", {"outer_wall_speed": "30"}),
            ("Piece", {"seam_position": "back"}),
            ("PieceLongue", {"brim_type": "outer_only"}),
        ]):
            p = self.archive("M83\nEXCLUDE_OBJECT_START NAME=" + obj + "\n;TYPE:Brim\nG1 X10 E1 F600\n")
            place = self.d / "p.json"
            place.write_text(
                json.dumps(
                    {
                        "objects": [
                            {"name": "Piece", "plate": 1, "xy": [0, 0], "size": [10, 10], "reglages": settings}
                        ],
                        "plates": [{"name": "P"}],
                    }
                )
            )
            out = self.d / f"r-{indice}.json"
            r = self.runcli("verifier_reglages.py", p, place, out)
            self.assertFalse(json.loads(out.read_text())["tout_conforme"])
            self.assertEqual(r.returncode, 1)

    def test_premiere_couche_normale_defaut_arc_et_3mf_nontranche(self):
        cas = [
            ("M83\n;Z:.2\n;TYPE:Outer wall\nG1 X30 E1 F600", "PASS", 0),
            ("M83\n;Z:.2\n;TYPE:Outer wall\nG1 X1 E.1 F600", "WARN", 1),
            ("M83\n;Z:.2\n;TYPE:Outer wall\nG2 X30 E1 I15 F600", "NON VÉRIFIÉ", 2),
        ]
        for indice, (g, status, code) in enumerate(cas):
            p = self.d / "a.gcode"
            p.write_text(g)
            out = self.d / f"r-{indice}.json"
            r = self.runcli("premiere_couche.py", p, "--json", out)
            self.assertEqual(r.returncode, code, r.stderr)
            self.assertEqual(json.loads(out.read_text())["plaques"]["plaque 1"]["statut"], status)
        p = self.d / "sans.3mf"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("rien", "")
        self.assertEqual(self.runcli("premiere_couche.py", p).returncode, 2)

    def test_support_segments_localises_et_gap(self):
        m = RL.mesurer_plaque(
            "M83\n;Z:.2\n;TYPE:Support interface\nG1 X30 Y0 E1 F600\n;Z:.4\n;TYPE:Outer wall\nG1 X60 E1"
        )
        z = {
            "plaque": 1,
            "type": "Bridge",
            "bbox_xy_mm": [10, -1, 20, 1],
            "z_min_mm": 0.4,
            "z_max_mm": 0.4,
            "separation_support_max_mm": 0.21,
        }
        self.assertTrue(RL.faits_zone(z, m)["support_dessous"])
        self.assertFalse(RL.faits_zone(dict(z, bbox_xy_mm=[100, 100, 120, 120]), m)["support_dessous"])
        self.assertFalse(RL.faits_zone(dict(z, z_min_mm=20, z_max_mm=20), m)["support_dessous"])

    def test_remplissage_coupe_segment_et_dette(self):
        mod = load(C.parent / "fabrication/remplissage_localise.py")
        a = mod.analyser_gcode("M83\nG1 E-1\n;TYPE:Sparse infill\nG1 X20 E2", {"boite": [12, -1, 18, 1, -1, 1]})
        self.assertEqual(a["boite"]["Sparse infill"], 6)

    def test_C02_limites_optionnelles_visibles_sans_faux_pass_global(self):
        from fixtures_communes import DECL, creer_3mf_test

        p = creer_3mf_test(self.d / "envoi.3mf")
        dec = self.d / "dec.json"
        dec.write_text(json.dumps(DECL))
        out = self.d / "out.json"
        r = self.runcli("controle_envoi.py", p, "--declaration", dec, "--json", out)
        data = json.loads(out.read_text())
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertEqual(data["code"], r.returncode)
        self.assertEqual(data["statut"], "WARN")
        self.assertTrue(data["conforme_numerique"])
        self.assertTrue(all(not x["obligatoire"] for x in data["resultats"] if x["statut"] == "NON VÉRIFIÉ"))


if __name__ == "__main__":
    unittest.main()
