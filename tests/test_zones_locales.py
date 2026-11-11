"""Cas synthétiques du blocage local ; aucune impression."""

import json
import tempfile
import unittest
from pathlib import Path

from benchab.controles.zones_locales import analyser_gcode, evaluer, sha

BASE = "G90\nM83\n;LAYER_CHANGE\nG1 Z0.2\n;TYPE:Outer wall\nG1 X0 Y0 F1200\nG1 X20 E1\n"


class ZonesLocales(unittest.TestCase):
    def test_purge_anycubic_apres_marqueur_premiere_couche_exclue(self):
        g = (
            "G90\nM83\n;LAYER_CHANGE\n;TYPE:Custom\n ; PURGE LINE\nG1 X89 Y255 F3000\n"
            "G1 X158 E3.7\n; AFTER_LAYER_CHANGE 0 @ 0.2mm\n;TYPE:Outer wall\nG1 X10 Y10 E1"
        )
        self.assertFalse(analyser_gcode(g)["erreurs"])

    def test_pont_court_derniere_couche(self):
        r = analyser_gcode(BASE + ";LAYER_CHANGE\nG1 Z5\n;TYPE:Bridge\nG1 X22 E.1 F3000\n")
        self.assertEqual(len(r["zones"]), 1)
        self.assertFalse(r["erreurs"])
        self.assertEqual(r["zones"][0]["z_max_mm"], 5)
        self.assertEqual(r["zones"][0]["vitesse_max_mm_s"], 50)

    def test_surplomb_explicite(self):
        self.assertEqual(analyser_gcode(BASE + ";TYPE:Overhang wall\nG1 X21 E.2")["zones"][0]["type"], "Overhang wall")

    def test_pont_interne_et_reamorcage_exclus(self):
        r = analyser_gcode(BASE + ";TYPE:Internal Bridge\nG1 X22 E.2\n;TYPE:Bridge\nG1 E2\nG1 X25 E-.1")
        self.assertFalse(r["zones"])
        self.assertFalse(r["erreurs"])

    def test_extrusion_absolue_et_coordonnees_relatives(self):
        r = analyser_gcode(BASE + "M82\nG92 E0\nG91\n;TYPE:Bridge\nG1 X2 E.2\nG1 X2 E.4")
        self.assertEqual(r["zones"][0]["bbox_xy_mm"], [20, 0, 24, 0])
        self.assertEqual(r["zones"][0]["segments"], 2)

    def test_type_absent_arc_et_mode_absent_ne_passent_pas(self):
        for g in [BASE.replace(";TYPE:Outer wall\n", ""), BASE + "G2 X30 Y1 E.2 I2 J1", BASE.replace("M83\n", "")]:
            with self.subTest(g=g):
                self.assertTrue(analyser_gcode(g)["erreurs"])

    def test_revue_manquante_perimee_ou_preuve_modifiee_bloque(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t)
            g = p / "tranche.3mf"
            g.write_bytes(b"synthetic")
            proof = p / "preuve.txt"
            proof.write_text("appuis et parcours examines")
            review = p / "revue.json"
            zones = analyser_gcode(BASE + ";TYPE:Bridge\nG1 X22 E.2")["zones"]
            # Identifiant publié avant l'ajout des mesures : la revue existante reste valable.
            self.assertEqual(zones[0]["id"], "11447dedfd4477af")
            self.assertEqual(evaluer(zones, [], g)["statut"], "FAIL")
            doc = {
                "tranche_sha256": sha(g),
                "geometrie_sha256": None,
                "zones": {
                    "11447dedfd4477af": {
                        "decision": "justifie",
                        "justification": "Cas de test uniquement",
                        "derniere_couche_verifiee": True,
                        "preuves": [{"fichier": proof.name, "sha256": sha(proof)}],
                    }
                },
            }
            review.write_text(json.dumps(doc))
            self.assertEqual(evaluer(zones, [], g, revue=review)["statut"], "PASS")
            proof.write_text("modifie")
            self.assertEqual(evaluer(zones, [], g, revue=review)["statut"], "FAIL")
            g.write_bytes(b"change")
            self.assertEqual(evaluer(zones, [], g, revue=review)["statut"], "FAIL")

    def test_changement_objet_ne_redecoupe_pas_identite_historique(self):
        """Le nouveau contexte distingue l'ambiguïté sans changer l'ancien groupe ni sa clé."""
        g = BASE + (
            ";TYPE:Bridge\nEXCLUDE_OBJECT_START NAME=a\nG1 X22 E.2\nEXCLUDE_OBJECT_END NAME=a\n"
            "EXCLUDE_OBJECT_START NAME=b\nG1 X24 E.2\nEXCLUDE_OBJECT_END NAME=b\n"
        )
        zones = analyser_gcode(g)["zones"]
        self.assertEqual([(z["id"], z["segments"]) for z in zones], [("3abb901d288de2f8", 2)])
        self.assertTrue(zones[0]["objet_ambigu"])

    def test_document_revue_non_objet_bloque_sans_exception(self):
        """Un JSON valide mais hors schéma ne devient pas un acquittement ni un plantage."""
        with tempfile.TemporaryDirectory() as t:
            p = Path(t)
            tranche = p / "tranche.3mf"
            tranche.write_bytes(b"fixture")
            revue = p / "revue.json"
            revue.write_text("[]", encoding="utf-8")
            resultat = evaluer([], [], tranche, revue=revue)
            self.assertEqual(resultat["statut"], "FAIL")
            self.assertTrue(resultat["erreurs"])

    def test_geometrie_avertie_ou_absente_bloque(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t)
            g = p / "tranche.3mf"
            g.write_bytes(b"synthetic")
            geo = p / "geom.json"
            self.assertEqual(evaluer([], [], g, geo)["statut"], "FAIL")
            geo.write_text(
                json.dumps(
                    {
                        "fichiers": {
                            "pic.stl": [{"controle": "couches courtes", "statut": "WARN", "detail": "petit sommet"}]
                        }
                    }
                )
            )
            r = evaluer([], [], g, geo)
            self.assertEqual(r["statut"], "FAIL")
            self.assertEqual(len(r["non_revues"]), 1)

    def test_paroi_ordinaire_sans_alerte(self):
        r = analyser_gcode(BASE)
        self.assertFalse(r["zones"])
        self.assertFalse(r["erreurs"])

    def test_fail_geometrique_non_acquittable_par_revue_locale(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t)
            g = p / "tranche.3mf"
            g.write_bytes(b"synthetic")
            geo = p / "geom.json"
            for controle in ("maillage fermé", "dimensions", "surplombs"):
                geo.write_text(json.dumps({"fichiers": {"piece.stl": [{"controle": controle, "statut": "FAIL"}]}}))
                r = evaluer([], [], g, geo)
                self.assertEqual(r["statut"], "FAIL")
                self.assertTrue(r["erreurs"])

    def test_geometrie_sans_controles_ou_statut_inconnu_ne_passe_pas(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t)
            g = p / "tranche.3mf"
            g.write_bytes(b"synthetic")
            geo = p / "geom.json"
            for controles in (
                [],
                [{"controle": "maillage fermé", "statut": "INCONNU"}],
                [{"controle": "maillage fermé", "statut": None}],
            ):
                geo.write_text(json.dumps({"fichiers": {"piece.stl": controles}}))
                self.assertEqual(evaluer([], [], g, geo)["statut"], "FAIL")


if __name__ == "__main__":
    unittest.main()
