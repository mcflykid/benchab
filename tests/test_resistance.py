import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from benchab.fabrication.resistance import (
    auditer_projet,
    calculer,
    cle_classement,
    evaluer_piece,
    fleche_ef,
    section,
)

REGISTRE_FIXTURE = {
    "materiaux": {
        "materiau_fixture": {
            "traction_MPa": {"XY": 50, "Z": 40},
            "young_MPa": {"XY": 1000, "Z": 1000},
            "source": "fixture de test synthétique",
        }
    },
    "remplissages": {"motifs": ["gyroid"]},
}


def cas():
    return {
        "modele": "traction",
        "materiau": "materiau_fixture",
        "direction_couches": "XY",
        "source_effort": "Témoin analytique, 100 N déclarés",
        "justification_section": "Rectangle synthétique 10×20 plein",
        "section": {"forme": "rectangle", "largeur_mm": 10, "hauteur_mm": 20, "epaisseur_mm": 5},
        "force_N": 100,
        "longueur_mm": 200,
        "densite_pct": 100,
        "motif": "gyroid",
        "conditions_representatives": True,
    }


class ResistanceTests(unittest.TestCase):
    def test_registre_absent_ne_suppose_aucun_materiau(self):
        resultat = calculer(cas())
        self.assertEqual(resultat["statut"], "NON VERIFIE")
        self.assertIn("Registre matière privé explicite requis", resultat["detail"])

    def test_main_utilise_argv_et_ecrit_seulement_un_rapport_neuf(self):
        from benchab.fabrication.resistance import main

        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporaire:
            racine = Path(temporaire)
            projet = racine / "projet"
            projet.mkdir()
            materiaux = racine / "materiaux.json"
            materiaux.write_text(json.dumps(REGISTRE_FIXTURE))
            rapport = racine / "rapport.json"
            arguments = ["--projet", str(projet), "--json", str(rapport), "--materiaux", str(materiaux)]
            with patch("sys.argv", ["resistance.py", "--projet", "/chemin/ignore"]), redirect_stdout(StringIO()):
                self.assertEqual(main(arguments), 1)
            self.assertEqual(json.loads(rapport.read_text())["statut"], "WARN")
            with redirect_stdout(StringIO()):
                with self.assertRaises(SystemExit) as erreur:
                    main(arguments)
            self.assertEqual(erreur.exception.code, 2)

    def test_traction_reference_manuelle(self):
        r = calculer(cas(), registre=REGISTRE_FIXTURE)
        self.assertEqual(r["statut"], "PASS")
        self.assertAlmostEqual(r["contrainte_MPa"], 0.5)
        self.assertAlmostEqual(r["coefficient_securite"], 100)
        self.assertAlmostEqual(r["allongement_prudent_mm"], 100 * 200 / (1000 * 200))

    def test_z_plus_faible(self):
        c = cas()
        r = calculer(c, registre=REGISTRE_FIXTURE)
        c["direction_couches"] = "Z"
        z = calculer(c, registre=REGISTRE_FIXTURE)
        self.assertAlmostEqual(z["coefficient_securite"] / r["coefficient_securite"], 40 / 50)

    def test_surcharge_echoue(self):
        c = cas()
        c["force_N"] = 20000
        self.assertEqual(calculer(c, registre=REGISTRE_FIXTURE)["statut"], "FAIL")

    def test_pas_de_substitution_filament(self):
        c = cas()
        c["materiau"] = "esun_non_documente"
        self.assertEqual(calculer(c, registre=REGISTRE_FIXTURE)["statut"], "NON VERIFIE")
        c = cas()
        c.pop("conditions_representatives")
        self.assertEqual(calculer(c, registre=REGISTRE_FIXTURE)["statut"], "WARN")

    def test_coeur_pas_de_propriete_inventee(self):
        c = cas()
        c["section"]["epaisseur_mm"] = 1
        c["densite_pct"] = 10
        a = calculer(c, registre=REGISTRE_FIXTURE)
        c["densite_pct"] = 70
        b = calculer(c, registre=REGISTRE_FIXTURE)
        self.assertAlmostEqual(a["coefficient_securite"], b["coefficient_securite"])
        self.assertEqual(a["remplissage"]["fraction_coeur_creditee_resistance"], 0)
        self.assertGreater(
            b["remplissage"]["module_coeur_encadrement_MPa"][1], a["remplissage"]["module_coeur_encadrement_MPa"][1]
        )

    def test_parois_variantes(self):
        c = cas()
        c["largeur_ligne_mm"] = 0.4
        a = calculer(
            c, {"wall_loops": "2", "sparse_infill_density": "20%", "sparse_infill_pattern": "gyroid"}, REGISTRE_FIXTURE
        )
        b = calculer(
            c, {"wall_loops": "4", "sparse_infill_density": "20%", "sparse_infill_pattern": "gyroid"}, REGISTRE_FIXTURE
        )
        self.assertGreater(b["coefficient_securite"], a["coefficient_securite"])
        c.pop("largeur_ligne_mm")
        self.assertEqual(calculer(c, {"wall_loops": "2"}, REGISTRE_FIXTURE)["statut"], "NON VERIFIE")

    def test_flexion_main_et_elements_finis_1d(self):
        c = cas()
        c["modele"] = "flexion_console"
        c["longueur_mm"] = 300
        c["force_N"] = 10
        r = calculer(c, registre=REGISTRE_FIXTURE)
        i = 10 * 20**3 / 12
        self.assertAlmostEqual(r["contrainte_MPa"], 10 * 300 * 10 / i)
        self.assertAlmostEqual(r["fleche_prudente_mm"], 10 * 300**3 / (3 * 1000 * i))
        self.assertAlmostEqual(r["fleche_ef_1d_mm"], r["fleche_prudente_mm"], places=7)
        self.assertEqual(r["statut"], "WARN")

    def test_ef_convergence_et_condition_limite(self):
        for n in [1, 2, 8, 16]:
            self.assertAlmostEqual(fleche_ef(10, 100, 2000, n), 10 * 100**3 / (3 * 2000), places=5)

    def test_console_courte_refusee(self):
        c = cas()
        c["modele"] = "flexion_console"
        c["longueur_mm"] = 10
        self.assertEqual(calculer(c, registre=REGISTRE_FIXTURE)["statut"], "NON VERIFIE")

    def test_coque_mince(self):
        c = cas()
        c.update(
            modele="pression_cylindre",
            pression_MPa=0.1,
            section={"forme": "tube", "diametre_mm": 100, "epaisseur_mm": 1},
        )
        r = calculer(c, registre=REGISTRE_FIXTURE)
        self.assertAlmostEqual(r["contrainte_MPa"], 4.95)
        self.assertEqual(r["statut"], "WARN")

    def test_donnees_invalides(self):
        for k, v in [
            ("force_N", float("nan")),
            ("force_N", -1),
            ("force_N", True),
            ("densite_pct", 101),
            ("direction_couches", "?"),
            ("source_effort", ""),
            ("securite_cible", 0.1),
        ]:
            c = cas()
            c[k] = v
            self.assertEqual(calculer(c, registre=REGISTRE_FIXTURE)["statut"], "NON VERIFIE", (k, v))
        c = cas()
        c["section"]["epaisseur_mm"] = 100
        self.assertEqual(calculer(c, registre=REGISTRE_FIXTURE)["statut"], "NON VERIFIE")

    def test_une_piece_manquante_ne_passe_pas(self):
        self.assertEqual(evaluer_piece(None, "support")["statut"], "NON VERIFIE")
        self.assertEqual(evaluer_piece(None, "decoratif")["statut"], "NON APPLICABLE")

    def test_cas_multiples_pire_effort(self):
        a = cas()
        b = cas()
        b["force_N"] = 20000
        self.assertEqual(evaluer_piece([a, b], registre=REGISTRE_FIXTURE)["statut"], "FAIL")

    def test_lecture_ast_sans_execution_et_absence_effort(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "src").mkdir()
            (p / "src/parametres.py").write_text(
                "raise RuntimeError('ne doit pas tourner')\nREMPLISSAGE={'Piece':'support'}\n"
            )
            r = auditer_projet(p)
            self.assertEqual(r["statut"], "WARN")
            self.assertEqual(r["pieces"]["Piece"]["statut"], "NON VERIFIE")

    def test_reglages_reels_3mf_et_absence_de_faux_pass(self):
        from zipfile import ZipFile

        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "src").mkdir()
            c = cas()
            c["largeur_ligne_mm"] = 0.4
            (p / "src/parametres.py").write_text("EFFORTS = " + repr({"Piece": c}))
            archive = p / "tranche.3mf"
            with ZipFile(archive, "w") as z:
                z.writestr(
                    "Metadata/project_settings.config",
                    json.dumps({"wall_loops": "2", "sparse_infill_density": "15%", "sparse_infill_pattern": "gyroid"}),
                )
                z.writestr(
                    "Metadata/model_settings.config",
                    (
                        '<config><object id="1"><metadata key="name" value="Piece.stl"/>'
                        '<metadata key="wall_loops" value="4"/></object></config>'
                    ),
                )
            r = auditer_projet(p, archive, REGISTRE_FIXTURE)
            calc = r["pieces"]["Piece"]["cas"][0]
            self.assertAlmostEqual(calc["section"]["A_parois"], section(c["section"], 1.6)["A_parois"])
            self.assertEqual(calc["remplissage"]["densite_pct"], 15)
            self.assertIsNotNone(r["tranche_sha256"])
            self.assertEqual(auditer_projet(p, registre=REGISTRE_FIXTURE)["statut"], "WARN")

    def test_aucun_coefficient_orca_ou_motif_inconnu_promu(self):
        c = cas()
        c.update(densite_pct=15, motif="inconnu")
        self.assertEqual(calculer(c, registre=REGISTRE_FIXTURE)["statut"], "WARN")

    def test_classement_resistance_avant_temps(self):
        def v(st, t, alerte=False):
            return {"resistance_calculee": {"statut": st}, "heures": t, "grammes": 1, "alerte_imprimabilite": alerte}

        self.assertEqual(min([v("FAIL", 1), v("PASS", 4)], key=cle_classement)["heures"], 4)
        self.assertFalse(min([v("PASS", 1, True), v("WARN", 4)], key=cle_classement)["alerte_imprimabilite"])


if __name__ == "__main__":
    unittest.main()
