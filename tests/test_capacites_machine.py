"""Capacités de commandes : archives synthétiques et profil flat déclaré, aucun slicer."""

import hashlib
import json
import math
import tempfile
import unittest
import zipfile
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from benchab.controles import capacites_machine as C

MACHINE = {
    "vitesse_xy_mm_s": 600,
    "vitesse_z_mm_s": 20,
    "vitesse_e_mm_s": 40,
    "acceleration_xy_mm_s2": 10000,
    "acceleration_z_mm_s2": 500,
    "acceleration_e_mm_s2": 5000,
    "acceleration_extruding_mm_s2": 20000,
    "acceleration_travel_mm_s2": 20000,
    "acceleration_retracting_mm_s2": 5000,
    "volume_mm": [250, 250, 250],
    "buse_mm": 0.4,
    "buse_matiere": "laiton",
    "sources": {},
    "non_verifie": [],
}
BASE = (
    "G90\nM83\nT0\nG9111 extruderTemp=220 bedTemp=55\n;LAYER_CHANGE\n;Z:.2\n;HEIGHT:.2\n;WIDTH:.42\n"
    ";TYPE:Outer wall\nEXCLUDE_OBJECT_START NAME=Piece_id_0_copy_0\nG1 X0 Y0 Z.2 F600\nG1 X10 E1\n"
)


class Capacites(unittest.TestCase):
    def analyser(self, g=BASE, settings=None, tools=(0,), limits=None, machine=None, save=None, *,
                 lecture_directe_interdite=False):
        """Analyse un 3MF synthétique privé et vérifie que ses octets ne changent pas."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "tranche.3mf"
            st = {
                "printer_model": "Anycubic Kobra S1",
                "nozzle_type": "brass",
                "nozzle_diameter": [".4"],
                "filament_type": ["PLA", "PLA"],
                "filament_settings_id": ["a", "b"],
                "filament_max_volumetric_speed": ["32", "16"],
                "filament_diameter": ["1.75", "1.75"],
                "nozzle_temperature": ["220", "210"],
                "nozzle_temperature_initial_layer": ["220", "210"],
            }
            st.update(settings or {})
            xml = (
                '<config><object id="1"><metadata key="name" value="Piece"/><metadata key="extruder" value="1"/>'
                + "".join(f'<part><metadata key="extruder" value="{t + 1}"/></part>' for t in tools)
                + '</object><plate><metadata key="plater_id" value="1"/>'
                + '<model_instance><metadata key="object_id" value="1"/></model_instance></plate></config>'
            )
            with zipfile.ZipFile(path, "w") as z:
                z.writestr("Metadata/project_settings.config", json.dumps(st))
                z.writestr("Metadata/model_settings.config", xml)
                z.writestr(
                    "Metadata/slice_info.config",
                    '<config><plate><metadata key="index" value="1"/>'
                    + "".join(f'<filament id="{t + 1}"/>' for t in tools)
                    + "</plate></config>",
                )
                z.writestr("Metadata/plate_1.gcode", g)
            before = path.read_bytes()
            garde = (
                patch.object(Path, "read_bytes", side_effect=AssertionError("Lecture intégrale du chemin"))
                if lecture_directe_interdite else nullcontext()
            )
            with garde:
                r = C.analyser(path, maitre=MACHINE if machine is None else machine, limites_filaments=limits)
            if lecture_directe_interdite:
                self.assertEqual(r["entrees"]["tranche_sha256"], hashlib.sha256(before).hexdigest())
            self.assertEqual(path.read_bytes(), before)
            json.dumps(r, allow_nan=False)
            if save:
                Path(save).write_text(json.dumps(r, indent=2, ensure_ascii=False))
            return r

    def test_schema_base_et_provenance(self):
        r = self.analyser()
        self.assertEqual(r["statut"], "PASS")
        self.assertEqual(r["decision"], "GO")
        self.assertAlmostEqual(r["metriques"]["volume_depose_mm3"], math.pi * (1.75 / 2) ** 2)
        self.assertEqual(len(r["entrees"]["tranche_sha256"]), 64)
        self.assertTrue(r["entrees"]["membres"])
        group = next(x for x in r["plaques"][0]["groupes"] if x["volume_depose_mm3"] > 0)
        self.assertEqual(group["longueur_max_mm"], 10)
        self.assertEqual(group["vitesse_max_mm_s"], 10)
        volume = next(c for c in r["contraintes"] if c["controle"] == "volume XYZ déposé")
        self.assertEqual(set(volume["detail"]), {"bornes_mm", "debut", "fin"})

    def test_empreinte_capacites_depuis_le_lecteur_3mf_epingle(self):
        """Le 3MF analysé est haché sans relire intégralement le chemin avant les gardes."""
        self.assertEqual(self.analyser(lecture_directe_interdite=True)["statut"], "PASS")

    def test_strict_minoritaire_meme_objet(self):
        g = BASE.replace("G1 X10 E1", "G1 X990 E10") + "T1\nM104 S210\nG1 X990.1 E.1\n"
        r = self.analyser(
            g,
            tools=(0, 1),
            limits={"1": {"plafond_mm3_s": 32, "diametre_mm": 1.75}, "2": {"plafond_mm3_s": 1, "diametre_mm": 1.75}},
        )
        self.assertEqual(r["statut"], "FAIL")
        self.assertTrue(
            any(x["controle"] == "débit strict" and x["outil"] == 1 and x["statut"] == "FAIL" for x in r["contraintes"])
        )

    def test_m82_m83_et_retraction_equivalents(self):
        rel = BASE + "G1 E-1\nG1 X20 E.5\nG1 X30 E1.5\n"
        abs_ = rel.replace("M83", "M82\nG92 E0").replace("G1 E-1", "G1 E0").replace("G1 X30 E1.5", "G1 X30 E2")
        a, b = self.analyser(rel), self.analyser(abs_)
        self.assertAlmostEqual(a["metriques"]["volume_depose_mm3"], b["metriques"]["volume_depose_mm3"])
        self.assertEqual(sum(g["retractions"] for g in a["metriques"]["groupes"]), 1)

    def test_limites_absentes_pas_de_substitution(self):
        g = BASE + "T1\nM104 S210\nG1 X20 E1\n"
        r = self.analyser(g, settings={"filament_max_volumetric_speed": ["32"]}, tools=(0, 1))
        self.assertEqual(r["statut"], "NON VÉRIFIÉ")
        self.assertIsNone(r["filaments"]["2"]["plafond_mm3_s"])

    def test_pas_de_diametre_implicite(self):
        r = self.analyser(settings={"filament_diameter": []})
        self.assertEqual(r["statut"], "NON VÉRIFIÉ")
        self.assertEqual(r["metriques"]["volume_depose_mm3"], 0)

    def test_opcodes_inconnus_et_commentaires(self):
        self.assertEqual(self.analyser(BASE + "C X20 E1")["statut"], "FAIL")
        self.assertEqual(self.analyser(BASE + "; C X20 E1")["statut"], "PASS")
        for command in ["M220 S200", "M221 S110", "G2 X20 E1 I3"]:
            with self.subTest(command=command):
                self.assertNotEqual(self.analyser(BASE + command)["statut"], "PASS")

    def test_arc_valide_reste_non_verifie_pour_limites_geometriques(self):
        """Une corde ne qualifie ni les vitesses par axe ni le volume d'un arc."""
        resultat = self.analyser(BASE + "G2 X20 E1 I5 F600")
        self.assertEqual(resultat["statut"], "NON VÉRIFIÉ")

    def test_arc_depose_hors_volume_est_fail_meme_si_sa_corde_y_reste(self):
        """Les extrema courbes détectent le dépassement Y que masque la corde."""
        gcode = BASE.replace("G1 X10 E1", "G3 X10 Y0 R5 E1 F600")

        resultat = self.analyser(gcode, machine=dict(MACHINE, volume_mm=[10, 4, 250]))

        volume = next(c for c in resultat["contraintes"] if c["controle"] == "volume XYZ déposé")
        self.assertEqual(volume["statut"], "FAIL")
        self.assertLess(volume["detail"]["enveloppe_xyz_mm"][1][0], 0)
        self.assertEqual(resultat["statut"], "FAIL")

    def test_arc_cw_partiellement_retracte_verifie_seulement_le_depot(self):
        """La partie rétractée avant le dépôt n'élargit pas l'enveloppe opposable."""
        gcode = BASE.replace("G1 X10 E1", "G1 E-3\nG2 X10 Y0 R5 E4 F600")

        resultat = self.analyser(gcode, machine=dict(MACHINE, volume_mm=[10, 4, 250]))

        volume = next(c for c in resultat["contraintes"] if c["controle"] == "volume XYZ déposé")
        self.assertEqual(volume["statut"], "PASS")
        self.assertEqual(volume["detail"]["debut_depot_fraction"], 0.75)
        self.assertAlmostEqual(volume["detail"]["enveloppe_xyz_mm"][1][1], 5 / math.sqrt(2))
        self.assertEqual(resultat["statut"], "NON VÉRIFIÉ")
        self.assertTrue(
            any(
                c["controle"] == "trajectoire géométrique" and c["statut"] == "NON VÉRIFIÉ"
                for c in resultat["contraintes"]
            )
        )

    def test_arc_apres_g92_utilise_le_repere_spatial_pour_le_volume(self):
        """Le contrôle du volume garde le repère physique continu après G92 XYZ."""
        gcode = BASE.replace(
            "G1 X10 E1",
            "G1 X10 Y0 F600\nG92 X0 Y0\nG2 X10 Y0 I5 J0 E1 F600",
        )

        resultat = self.analyser(gcode, machine=dict(MACHINE, volume_mm=[15, 10, 250]))

        volume = next(c for c in resultat["contraintes"] if c["controle"] == "volume XYZ déposé")
        self.assertEqual(volume["statut"], "FAIL")
        self.assertEqual(volume["detail"]["enveloppe_xyz_mm"][0], (10.0, 20.0))

    def test_tolerance_enveloppe_arc_est_limitee_a_un_micrometre(self):
        """Le petit flottement numérique est explicite et ne masque pas un vrai dépassement."""
        gcode = BASE.replace("G1 X10 E1", "G1 E-3\nG2 X10 Y0 R5 E4 F600")
        ymax = 5 / math.sqrt(2)

        proche = self.analyser(gcode, machine=dict(MACHINE, volume_mm=[10, ymax - 5e-7, 250]))
        depasse = self.analyser(gcode, machine=dict(MACHINE, volume_mm=[10, ymax - 2e-6, 250]))

        proche_volume = next(c for c in proche["contraintes"] if c["controle"] == "volume XYZ déposé")
        depasse_volume = next(c for c in depasse["contraintes"] if c["controle"] == "volume XYZ déposé")
        self.assertEqual(proche_volume["statut"], "PASS")
        self.assertEqual(proche_volume["detail"]["tolerance_mm"], 1e-6)
        self.assertEqual(depasse_volume["statut"], "FAIL")

    def test_accelerations_axes_et_categories(self):
        cmds = "M201 X10000 Y10000 Z500 E5000\nM203 X600 Y600 Z20 E40\nM204 S10000\nM205 X9 Y9\n"
        r = self.analyser(cmds + BASE)
        self.assertEqual(r["statut"], "PASS")
        self.assertEqual(r["metriques"]["accelerations_mm_s2"]["axes"]["X"], 10000)
        group = next(g for g in r["metriques"]["groupes"] if g["volume_depose_mm3"])
        self.assertEqual(group["acceleration_commandee_mm_s2"], 10000)
        self.assertEqual(self.analyser("M201 X20000\n" + BASE)["statut"], "FAIL")
        self.assertEqual(self.analyser("M204 P21000\n" + BASE)["statut"], "FAIL")
        self.assertEqual(r["plaques"][0]["cinematique"]["statut"], "NON VÉRIFIÉ")

    def test_vitesses_axes_et_e_separees(self):
        r = self.analyser(BASE + "G1 X20 Y10 Z10 E1 F600\n")
        v = r["metriques"]["vitesses_mm_s"]["axes_max"]
        self.assertLess(v["X"], 11)
        self.assertGreater(v["Z"], 0)
        self.assertEqual(self.analyser(BASE + "G1 E-1 F6000")["statut"], "NON VÉRIFIÉ")

    def test_temperature_cible_m104_t_ne_change_pas_outil_actif(self):
        g = BASE + "M104 T1 S210\nG1 X20 E1\nT1\nG1 X30 E1\n"
        r = self.analyser(g, tools=(0, 1), settings={"printer_model": "autre dialecte déclaré"})
        self.assertEqual(r["statut"], "PASS")
        ts = r["plaques"][0]["temperatures_commandes_c"]
        self.assertTrue(any(x["outil"] == 1 and x["temperature_c"] == 210 for x in ts))
        self.assertEqual(self.analyser(BASE.replace("extruderTemp=220", "extruderTemp=250"))["statut"], "FAIL")

    def test_variantes_buse_et_temperature(self):
        r = self.analyser(BASE, settings={"nozzle_temperature_initial_layer_BRASS": ["230", "210"]})
        self.assertEqual(r["statut"], "FAIL")
        r = self.analyser(
            BASE, settings={"nozzle_type": "hardened_steel", "nozzle_temperature_initial_layer_HS": ["220", "210"]}
        )
        self.assertEqual(r["statut"], "FAIL")

    def test_macro_opaque_purge_support_et_adhesion(self):
        g = (
            BASE
            + "EXCLUDE_OBJECT_END NAME=Piece_id_0_copy_0\n;TYPE:Support interface\nG1 X20 E1\n"
            + ";TYPE:Prime tower\nG1 X30 E1\n;TYPE:Skirt\nG1 X40 E1\n"
        )
        r = self.analyser(g)
        self.assertEqual(r["statut"], "PASS")
        self.assertGreater(r["metriques"]["support_mm3"], 0)
        self.assertGreater(r["metriques"]["purge_mm3"], 0)
        self.assertGreater(r["plaques"][0]["adhesion_mm3"], 0)
        self.assertEqual(r["plaques"][0]["temps_macros"]["statut"], "NON VÉRIFIÉ")
        self.assertTrue(any(e["command"] == "G9111" and e["opaque"] for e in r["plaques"][0]["commandes_exemples"]))

    def test_outils_incoherents_et_changements(self):
        r = self.analyser(BASE + "T1\nM104 S210\nG1 X20 E1\n")
        self.assertEqual(r["statut"], "FAIL")
        self.assertEqual(r["metriques"]["changements_filament"], 1)

    def test_mono_implicite_garde_s1_stricte(self):
        st = {
            "filament_type": ["PLA"],
            "filament_settings_id": ["a"],
            "filament_max_volumetric_speed": ["32"],
            "filament_diameter": ["1.75"],
            "nozzle_temperature": ["220"],
            "nozzle_temperature_initial_layer": ["220"],
        }
        g = BASE.replace("T0\n", "")
        r = self.analyser(g, settings=st)
        self.assertTrue(r["plaques"][0]["outil_initial_implicite"])
        self.assertEqual(r["statut"], "PASS")
        st["printer_model"] = "Autre"
        r = self.analyser(g, settings=st)
        self.assertFalse(r["plaques"][0]["outil_initial_implicite"])
        self.assertNotEqual(r["statut"], "PASS")

    def test_prime_immobile_et_petits_segments(self):
        r = self.analyser(BASE + "G1 E-1\nG1 E1\nG1 X10.1 E.01\nG1 X10.2 E.01\n")
        g = next(g for g in r["metriques"]["groupes"] if g["volume_depose_mm3"])
        self.assertEqual(g["longueur_max_mm"], 10)
        self.assertAlmostEqual(r["metriques"]["volume_depose_mm3"], 1.02 * math.pi * (1.75 / 2) ** 2)
        self.assertTrue(g["longueurs_histogramme_mm"])

    def test_source_vide_et_mode_absent(self):
        self.assertNotEqual(self.analyser("")["statut"], "PASS")
        self.assertNotEqual(self.analyser(BASE.replace("M83\n", ""))["statut"], "PASS")

    def test_plafond_invalide_et_derive_non_fini(self):
        for cap in [0, -1, float("nan"), float("inf")]:
            with self.subTest(cap=cap):
                self.assertNotEqual(
                    self.analyser(limits={"1": {"plafond_mm3_s": cap, "diametre_mm": 1.75}})["statut"], "PASS"
                )

    def test_temperatures_constructeur_si_fournies(self):
        m = dict(MACHINE, temperature_buse_constructeur_max_c=230, temperature_plateau_constructeur_max_c=60)
        self.assertEqual(self.analyser(BASE.replace("bedTemp=55", "bedTemp=70"), machine=m)["statut"], "FAIL")

    def test_archive_invalide_rapport_fail(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "invalide.3mf"
            p.write_bytes(b"pasunzip")
            r = C.analyser(p, maitre=MACHINE)
            self.assertEqual(r["statut"], "FAIL")
            self.assertIsNone(r["metriques"]["temps_nominal_s"])

    def test_maitre_invalide_et_temperature_non_finie(self):
        m = dict(MACHINE, vitesse_xy_mm_s=float("nan"))
        self.assertEqual(self.analyser(machine=m)["statut"], "FAIL")
        self.assertEqual(self.analyser(BASE.replace("extruderTemp=220", "extruderTemp=nan"))["statut"], "FAIL")

    def test_deplacement_sans_avance_non_verifie(self):
        self.assertEqual(self.analyser(BASE.replace(" F600", ""))["statut"], "NON VÉRIFIÉ")

    def test_f_seul_pas_un_segment_et_pas_de_mauvais_ace(self):
        a = self.analyser(BASE)
        b = self.analyser(BASE + "G1 F1200\n")
        self.assertEqual(a["metriques"]["groupes"][0]["segments"], b["metriques"]["groupes"][0]["segments"])
        self.assertEqual(a["ace"]["statut"], "NON VÉRIFIÉ")

    def test_last_event_ne_change_pas_retour_mouvement(self):
        from benchab.controles.gcode_interpreter import Interpreter

        it = Interpreter()
        self.assertIsNone(it.feed_line("M204 S10000"))
        self.assertEqual(it.last_event["parameters"]["S"], 10000)
        self.assertIsNone(it.feed_line("M83"))
        m = it.feed_line("G1 X10 E1 F600")
        self.assertEqual(m["deposited"], 1)
        self.assertEqual(it.last_event["command"], "G1")

    def test_categories_20k_et_axes_10k_restent_distincts(self):
        r = self.analyser("M201 X10000 Y10000 Z500 E5000\nM204 S20000\n" + BASE)
        self.assertEqual(r["statut"], "PASS")
        g = next(g for g in r["metriques"]["groupes"] if g["volume_depose_mm3"])
        self.assertEqual(g["acceleration_commandee_mm_s2"], 20000)
        self.assertEqual(g["acceleration_limitee_axes_mm_s2"], 10000)

    def test_loader_maitre_partage(self):
        from test_profil_maitre import profil_synthetique

        r = self.analyser(machine=profil_synthetique())
        self.assertIn("maitre", r["entrees"])
        self.assertNotEqual(r["statut"], "FAIL")

    def test_volume_xyz_et_purge_explicitement_hors_objet(self):
        self.assertEqual(self.analyser(BASE + "G1 Z260 X20 E1")["statut"], "FAIL")
        g = BASE + "EXCLUDE_OBJECT_END NAME=Piece_id_0_copy_0\n;TYPE:Custom\n; PURGE LINE\nG1 Y255 E1\n"
        self.assertEqual(self.analyser(g)["statut"], "PASS")

    def test_temp_unique_s1_ne_restaure_pas_ancienne_consigne_au_changement(self):
        g = BASE + "T1\nM104 S210\nG1 X20 E1\nT0\nG1 X30 E1\n"
        r = self.analyser(g, tools=(0, 1))
        self.assertEqual(r["statut"], "FAIL")
        self.assertTrue(
            any(
                x["controle"] == "température commandée" and x["outil"] == 0 and x["statut"] == "FAIL"
                for x in r["contraintes"]
            )
        )

    def test_s1_temperature_cible_outil_non_actif_reste_inconnue(self):
        r = self.analyser(BASE + "M104 T1 S210\nG1 X20 E1\n", tools=(0, 1))
        self.assertEqual(r["statut"], "NON VÉRIFIÉ")

    def test_demande_nominale_et_limites_emises_distinctes(self):
        g = (
            "M203 X600 Y600 Z15 E40\nSET_VELOCITY_LIMIT SQUARE_CORNER_VELOCITY=9\n"
            + BASE
            + "G1 Z.6 F18000\nG1 E-1 F6000\n"
        )
        r = self.analyser(g)
        self.assertEqual(r["statut"], "NON VÉRIFIÉ")
        self.assertFalse(
            any(c["controle"] in ("vitesse Z", "vitesse E") and c["statut"] == "FAIL" for c in r["contraintes"])
        )
        self.assertEqual(r["plaques"][0]["limites_vitesse_emises_mm_s"]["Z"], 15)
        self.assertEqual(r["metriques"]["vitesses_mm_s"]["statut_application_firmware"], "NON VÉRIFIÉ")
        self.assertEqual(r["metriques"]["accelerations_mm_s2"]["SET_VELOCITY_LIMIT"]["SQUARE_CORNER_VELOCITY"], 9)


if __name__ == "__main__":
    unittest.main()
