"""Régressions d'analyse G-code sur des extrusions synthétiques."""

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from benchab.donnees.base import Base
from benchab.fabrication import comparer_remplissages as comparaison

AIGUILLES = "\n".join(
    [
        ";TYPE:Outer wall",
        "G1 X0 Y0 E1",
        ";TYPE:Sparse infill",
        "G1 X1 Y0 E.05",
        "G1 X21 Y0",
        "G1 X22 Y0 E.05",
        "G1 X42 Y0",
        "G1 X43 Y0 E.05",
        ";TYPE:Top surface",
        "G1 X50 Y0 E1",
    ]
)
CONTINU = "\n".join(
    [
        ";TYPE:Sparse infill",
        "G1 X0 Y0",
        "G1 X30 Y0 E1.5",
        "G1 E-1.8",
        "G1 X30 Y10",
        "G1 E1.8",
        "G1 X0 Y10 E1.5",
        ";TYPE:Inner wall",
        "G1 X0 Y20 E1",
    ]
)


class TrajetsRemplissageTests(unittest.TestCase):
    def test_deplacements_et_ilots_courts(self):
        mesure = comparaison.trajets_remplissage(AIGUILLES)
        self.assertEqual(
            (
                mesure["deplacements"],
                mesure["retractions"],
                mesure["longs_sans_retraction"],
                mesure["ilots"],
                mesure["ilots_courts"],
            ),
            (2, 0, 2, 3, 3),
        )
        self.assertEqual(mesure["longueur_deplacements_mm"], 40)

    def test_retraction_interrompt_un_long_deplacement(self):
        mesure = comparaison.trajets_remplissage(CONTINU)
        self.assertEqual(
            (mesure["retractions"], mesure["longs_sans_retraction"], mesure["ilots_courts"]),
            (1, 0, 0),
        )
        self.assertEqual(mesure["ilots"], 2)

    def test_sections_hors_remplissage_ignorees(self):
        mesure = comparaison.trajets_remplissage(";TYPE:Outer wall\nG1 X0 Y0 E1\nG1 X50 Y0\nG1 X51 Y0 E.05")
        self.assertEqual((mesure["deplacements"], mesure["ilots"]), (0, 0))

    def test_indices_orca_indicatifs_conserves(self):
        self.assertEqual(comparaison.RESISTANCE_ORCA["lightning"], (2, 2))
        self.assertEqual(comparaison.RESISTANCE_ORCA["gyroid"], (6, 6))
        self.assertEqual(len(comparaison.FAMILLES["mecanique"]), 4)

    def test_precision_complete_conservee_pour_le_classement(self):
        premier = comparaison.estimations({"secondes": 100.1, "grammes": 1.01})
        second = comparaison.estimations({"secondes": 100.2, "grammes": 1.02})
        self.assertEqual(round(premier["heures"], 3), round(second["heures"], 3))
        self.assertEqual(round(premier["grammes"], 1), round(second["grammes"], 1))
        self.assertLess(comparaison.cle_classement(premier), comparaison.cle_classement(second))
        self.assertGreater((1 - premier["heures"] / second["heures"]) * 100, 0)

    def test_estimations_non_finies_ou_negatives_refusees(self):
        for secondes in (float("nan"), float("inf"), -1):
            with self.subTest(secondes=secondes), self.assertRaises(ValueError):
                comparaison.estimations({"secondes": secondes, "grammes": 1})

    def test_etude_remplissage_reprend_variantes_zones_et_cavite(self):
        plaque = {"items": [{"piece": "boite", "extruder": 2, "reglages": {"wall_loops": "3"}}]}
        declarations = {
            "*": "contenant",
            "variantes": [{"nom": "variante libre", "reglages": {"sparse_infill_density": "30%"}}],
        }
        pieces, avertissements = comparaison.construire_pieces_remplissages(
            [plaque], {"sparse_infill_line_width": "0.45", "wall_loops": "2"}, declarations,
            zones={"boite": [{"nom": "base", "role": "structurel"}]}, cavites={"boite": [{"nom": "cavité"}]},
        )
        self.assertEqual(avertissements, [])
        self.assertEqual([piece["nom"] for piece in pieces], ["boite (cavités)", "boite"])
        self.assertEqual(pieces[1]["reference"], {"wall_loops": "3", "sparse_infill_line_width": "0.45"})
        self.assertTrue(all(variante["acceptable"] for variante in pieces[1]["variantes"]))
        self.assertEqual(pieces[1]["variantes"][-1]["zones"][0]["nom"], "base")
        self.assertEqual(pieces[0]["variantes"], [])
        self.assertEqual(pieces[0]["stl"], "../../candidats/optimise/boite.stl")

    def test_synthese_remplissage_signale_seuil_robustesse_et_cavite_couteuse(self):
        ref = {"piece": "boite", "nom": "référence", "reglages": {"wall_loops": "1", "sparse_infill_density": "2%"},
               "heures": 1.0, "grammes": 10.0, "acceptable": True, "gain_temps_pct": 0, "gain_matiere_pct": 0}
        var = {"piece": "boite", "nom": "variante", "reglages": {"wall_loops": "3", "sparse_infill_density": "10%"},
               "heures": 0.9, "grammes": 9.0, "acceptable": True, "gain_temps_pct": 10, "gain_matiere_pct": 10}
        cavite = {**ref, "piece": "boite (cavités)", "heures": 1.2, "grammes": 11.0}
        pieces = [{"nom": "boite", "usage": "contenant"}, {"nom": "boite (cavités)", "usage": "contenant"}]
        rapport = comparaison.rapport_remplissages(pieces, {"resultats": [ref, var, cavite]})
        self.assertEqual(rapport["statut"], "WARN")
        self.assertTrue(rapport["pieces"]["boite"]["sous_le_minimum_de_la_famille"])
        self.assertEqual(rapport["pieces"]["boite"]["meilleure"]["nom"], "variante")
        self.assertIn("plus coûteuses", rapport["pieces"]["boite (cavités)"].get("conseil", ""))

        cavite["resistance_calculee"] = {"statut": "FAIL"}
        cavite["acceptable"] = False
        echec = comparaison.rapport_remplissages([pieces[1]], {"resultats": [cavite]})
        self.assertEqual(echec["pieces"]["boite (cavités)"]["statut"], "FAIL")

    def test_classement_prefere_resistance_mecanique_pass_a_variante_plus_lente(self):
        piece = {"nom": "liaison", "usage": "mecanique"}
        reference = {
            "piece": "liaison",
            "nom": "rapide non vérifiée",
            "reglages": {"wall_loops": "4", "sparse_infill_density": "25%"},
            "heures": 0.5,
            "grammes": 12.0,
            "acceptable": True,
            "gain_temps_pct": 20,
            "gain_matiere_pct": 4, "resistance_calculee": {"statut": "NON VÉRIFIÉ"},
        }
        robuste = {
            "piece": "liaison",
            "nom": "plus lente résistance PASS",
            "reglages": {"wall_loops": "5", "sparse_infill_density": "40%"},
            "heures": 1.5,
            "grammes": 20.0,
            "acceptable": True,
            "gain_temps_pct": -80,
            "gain_matiere_pct": -60, "resistance_calculee": {"statut": "PASS"},
        }
        decoratif = {"nom": "aspect", "usage": "decoratif"}
        decor_ref = {
            "piece": "aspect",
            "nom": "decor fin rapide",
            "reglages": {"wall_loops": "2", "sparse_infill_density": "8%"},
            "heures": 0.25, "grammes": 5.0, "acceptable": True, "gain_temps_pct": 0,
            "gain_matiere_pct": 0, "resistance_calculee": {"statut": "NON APPLICABLE"},
        }
        decor_lent = {**decor_ref, "nom": "decor fin lent", "heures": 0.75, "grammes": 6.0}
        rapport = comparaison.rapport_remplissages(
            [piece, decoratif], {"resultats": [reference, robuste, decor_ref, decor_lent]}
        )
        self.assertEqual(rapport["pieces"]["liaison"]["meilleure"]["nom"], robuste["nom"])
        self.assertEqual(rapport["pieces"]["aspect"]["meilleure"]["nom"], decor_ref["nom"])

    def test_resistance_recoit_les_surcharges_de_la_ligne_complete(self):
        piece = {"usage": "mecanique", "efforts": {"force_n": 5}}
        ligne = {"reglages": {"wall_loops": "7", "sparse_infill_density": "35%"}}
        with patch("benchab.fabrication.resistance.evaluer_piece", return_value={"statut": "PASS"}) as evaluer:
            self.assertEqual(comparaison.remplissage_resistance(piece, {"wall_loops": "2"}, ligne), {"statut": "PASS"})
        self.assertEqual(evaluer.call_args.args[2], {"wall_loops": "7", "sparse_infill_density": "35%"})

    def test_rapport_lissage_chiffre_le_surcout_sans_validation_physique(self):
        pieces = [{"nom": "dessus", "mode": "topmost"}]
        comparaison_ab = {"resultats": [
            {"piece": "dessus", "heures": 1.0},
            {"piece": "dessus", "heures": 1.25, "gain_temps_pct": -25.0},
        ]}
        rapport = comparaison.rapport_lissage(pieces, comparaison_ab)
        self.assertEqual(
            rapport["pieces"]["dessus"],
            {"mode": "topmost", "sans_min": 60, "avec_min": 75, "cout_temps_pct": 25.0},
        )


class ComparaisonCliTests(unittest.TestCase):
    def setUp(self):
        temporaire = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.addCleanup(temporaire.cleanup)
        self.base = Base(Path(temporaire.name) / "prive")
        self.addCleanup(self.base.fermer)
        self.dossier_etude = self.base.prive / "projets" / "essai" / "etudes"
        self.dossier_etude.mkdir(parents=True)
        self.etude = self.dossier_etude / "etude.json"
        (self.dossier_etude / "base.3mf").write_bytes("archive synthétique".encode())
        (self.dossier_etude / "piece.stl").write_bytes("maillage synthétique".encode())
        self.etude.write_text(
            json.dumps(
                {
                    "base_3mf": "base.3mf",
                    "sortie": "resultats",
                    "pieces": [{"stl": "piece.stl"}],
                }
            ),
            encoding="utf-8",
        )

    def test_cli_dossier_neuf_et_sources_inchangees(self):
        with patch.object(comparaison, "main", return_value={"resultats": [1]}) as main:
            code = comparaison.cli(["--prive", str(self.base.prive), str(self.etude)])
        self.assertEqual(code, 0)
        self.assertEqual(main.call_args.args, (self.etude.resolve(),))
        self.assertEqual(
            main.call_args.kwargs,
            {"prive": self.base.prive.resolve(), "dossier_sortie_precree": True},
        )
        preuve = json.loads((self.dossier_etude / "resultats/provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(preuve["statut"], "TERMINE")
        self.assertFalse(preuve["validation_physique"])
        with patch.object(comparaison, "main") as main, self.assertRaises(SystemExit) as erreur:
            comparaison.cli(["--prive", str(self.base.prive), str(self.etude)])
        self.assertEqual(erreur.exception.code, 2)
        main.assert_not_called()

    def test_source_modifiee_laisse_preuve_incomplete(self):
        source = self.dossier_etude / "base.3mf"

        def modifier(_etude, *, prive, dossier_sortie_precree):
            self.assertTrue(dossier_sortie_precree)
            source.write_bytes("archive modifiée".encode())
            return {"resultats": [1]}

        with patch.object(comparaison, "main", side_effect=modifier), self.assertRaises(SystemExit) as erreur:
            comparaison.cli(["--prive", str(self.base.prive), str(self.etude)])
        self.assertEqual(erreur.exception.code, 2)
        preuve = json.loads((self.dossier_etude / "resultats/provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(preuve["statut"], "INCOMPLET")

    def test_orchestration_etude_sans_lancement_slicer(self):
        import trimesh

        archive_base = self.dossier_etude / "base.3mf"
        with zipfile.ZipFile(archive_base, "w") as archive:
            archive.writestr(
                "Metadata/project_settings.config",
                json.dumps(
                    {
                        "sparse_infill_pattern": "gyroid",
                        "filament_type": ["PLA"],
                        "filament_colour": ["#ffffff"],
                        "filament_ids": ["filament-permanent-001"],
                        "filament_id": "filament-permanent-001",
                        "filament_settings_id": ["PLA"],
                    }
                ),
            )
        maillage = self.dossier_etude / "piece.stl"
        trimesh.creation.box(extents=(20, 20, 10)).export(maillage)
        self.etude.write_text(
            json.dumps(
                {
                    "base_3mf": "base.3mf",
                    "sortie": "etude-sans-slicer",
                    "pieces": [
                        {
                            "nom": "objet",
                            "stl": "piece.stl",
                            "extruder": 1,
                            "reference": {"sparse_infill_pattern": "gyroid"},
                            "variantes": [
                                {
                                    "nom": "variante",
                                    "reglages": {"sparse_infill_pattern": "lightning"},
                                    "acceptable": True,
                                    "justification": "cas synthétique",
                                }
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        def simuler_tranchage(source, dossier, nom, *, usage, prive):
            self.assertEqual(usage, "impression")
            self.assertEqual(prive, self.base.prive)
            with zipfile.ZipFile(source) as archive:
                reglages = json.loads(archive.read("Metadata/project_settings.config"))
                model_settings = archive.read("Metadata/model_settings.config").decode("utf-8")
            self.assertEqual(reglages["filament_ids"], ["filament-permanent-001"])
            self.assertFalse(any("BENCHAB-temporaire-" in identifiant for identifiant in reglages["filament_ids"]))
            self.assertIn("etude-plaques.3mf", model_settings)
            self.assertIn("source_volume_id", model_settings)
            destination = Path(dossier) / nom
            with zipfile.ZipFile(destination, "w") as archive:
                archive.writestr(
                    "Metadata/slice_info.config",
                    '<config><plate><metadata key="index" value="1" />'
                    '<metadata key="prediction" value="3600" />'
                    '<metadata key="weight" value="10" /></plate>'
                    '<plate><metadata key="index" value="2" />'
                    '<metadata key="prediction" value="4200" />'
                    '<metadata key="weight" value="12" /></plate></config>',
                )
                archive.writestr(
                    "Metadata/plate_1.gcode",
                    ";TYPE:Sparse infill\nG1 X0 Y0\nG1 X10 Y0 E1\n",
                )
                archive.writestr(
                    "Metadata/plate_2.gcode",
                    ";TYPE:Sparse infill\nG1 X0 Y0\nG1 X10 Y0 E1\n",
                )
            return destination, 4.0

        with (
            patch.object(comparaison.anycubic, "trancher", side_effect=simuler_tranchage) as lancer,
            patch.object(comparaison, "remplissage_resistance", wraps=comparaison.remplissage_resistance) as resistance,
        ):
            code = comparaison.cli(["--prive", str(self.base.prive), str(self.etude)])
        lancer.assert_called_once()
        self.assertTrue(resistance.call_args_list)
        self.assertTrue(all("reglages" in appel.args[2] for appel in resistance.call_args_list))
        self.assertEqual(code, 0)
        dossier_resultats = self.dossier_etude / "etude-sans-slicer"
        resultat = json.loads((dossier_resultats / "comparaison.json").read_text(encoding="utf-8"))
        self.assertEqual(resultat["tranchage_s"], 4.0)
        self.assertFalse(resultat["validation_physique"])
        self.assertTrue((self.dossier_etude / "etude-sans-slicer/comparaison.md").is_file())
        preuve = json.loads((dossier_resultats / "provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(preuve["statut"], "TERMINE")


if __name__ == "__main__":
    unittest.main()
