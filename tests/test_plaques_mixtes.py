"""Régressions de plaques mixtes sur des projets privés synthétiques."""

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import trimesh

from benchab.donnees.base import Base
from benchab.fabrication import plaques_mixtes


class PlaquesMixtesTests(unittest.TestCase):
    def setUp(self):
        temporaire = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.addCleanup(temporaire.cleanup)
        self.base = Base(Path(temporaire.name) / "prive")
        self.addCleanup(self.base.fermer)
        self.projet = self.base.prive / "projets" / "essai"
        (self.projet / "preparations/P01").mkdir(parents=True)
        (self.projet / "preuves").mkdir()
        (self.projet / "candidats").mkdir()
        fiche = {
            "id": "P01",
            "plaques_3mf": {"fichier": "projets/essai/preparations/P01/base.3mf"},
            "tranche_3mf": {"fichier": "projets/essai/preparations/P01/source.3mf"},
        }
        (self.projet / "preparations/P01/PREPARATION.json").write_text(json.dumps(fiche), encoding="utf-8")
        reglages = {cle: ["PLA"] for cle in plaques_mixtes.FILAMENT}
        reglages.update(
            {
                "filament_colour": ["#FFFFFF"],
                "nozzle_temperature": [210],
                "nozzle_temperature_initial_layer": [210],
                "layer_height": [0.2],
            }
        )
        with zipfile.ZipFile(self.projet / "preparations/P01/base.3mf", "w") as archive:
            archive.writestr("Metadata/project_settings.config", json.dumps(reglages))
        with zipfile.ZipFile(self.projet / "preparations/P01/source.3mf", "w") as archive:
            archive.writestr("Metadata/placeholder", "archive synthétique")
        (self.projet / "preuves/placement.json").write_text(
            json.dumps({"objects": [{"name": "Piece", "plate": 1}]}), encoding="utf-8"
        )
        trimesh.creation.box().export(self.projet / "candidats/Piece.stl")
        self.sortie = self.base.prive / "plaques-mixtes/lot"
        self.indice_sortie = 0
        self.ecritures = []

    def lancer(self, seconds=3600, real=None, hmax="3", missing=False, add=False, protected=None):
        if protected:
            source = self.projet / "candidats/Piece.stl"
            if protected == "hash":
                reference = self.projet / "reference/Imprime.stl"
                reference.parent.mkdir(parents=True)
                reference.write_bytes(source.read_bytes())
                source = reference
            (self.projet / "qualite.json").write_text(
                json.dumps(
                    {
                        "suivi": {
                            "references_imprimees": [
                                {
                                    "fichiers": [
                                        {
                                            "chemin": source.relative_to(self.projet).as_posix(),
                                            "sha256": plaques_mixtes.sha256_fichier(source),
                                        }
                                    ]
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )

        def lire_resultats(fichier):
            if "tranche-mixtes" in str(fichier):
                return [] if real == "missing" else [{"index": 1, "secondes": real, "grammes": 1}]
            return [] if missing else [{"index": 1, "secondes": seconds, "grammes": 1}]

        def ecrire(*arguments, **_options):
            self.ecritures.append((arguments, _options))
            Path(arguments[2]).write_bytes("archive de plaques synthétique".encode())

        def trancher(_source, dossier, nom, **_options):
            return Path(dossier) / nom, 0.0

        self.indice_sortie += 1
        sortie = self.sortie.parent / f"{self.sortie.name}-{self.indice_sortie}"
        arguments = ["--projets", "essai", "--duree-max", hmax, "--sortie", str(sortie)]
        if real is not None:
            arguments.append("--trancher")
        if add:
            arguments.extend(["--ajouter", str(self.projet / "candidats/Piece.stl")])
        with (
            patch.object(plaques_mixtes.anycubic, "resultats", side_effect=lire_resultats),
            patch.object(plaques_mixtes.anycubic, "ecrire_3mf", side_effect=ecrire),
            patch.object(plaques_mixtes.anycubic, "trancher", side_effect=trancher),
        ):
            try:
                code = plaques_mixtes.main(arguments, prive=self.base.prive)
            except SystemExit as erreur:
                code = erreur.code if isinstance(erreur.code, int) else 2
        rapport_path = sortie / "rapport.json"
        rapport = json.loads(rapport_path.read_text(encoding="utf-8")) if rapport_path.is_file() else {}
        return code, rapport

    def test_crc_du_membre_non_consomme_reste_requis(self):
        """Le contexte lit les réglages typés sans ignorer le CRC des autres membres."""
        base = self.projet / "preparations/P01/base.3mf"
        with zipfile.ZipFile(base, "a", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("Metadata/notes.txt", b"preuve d'atelier synthetique")
        brut = base.read_bytes().replace(b"preuve d'atelier synthetique", b"preuve d'atelier remplacee!!")
        self.assertEqual(len(brut), base.stat().st_size)
        base.write_bytes(brut)
        with patch.object(plaques_mixtes.anycubic, "resultats") as resultats:
            with self.assertRaisesRegex((ValueError, zipfile.BadZipFile), "corrompue|Bad CRC"):
                plaques_mixtes.pieces_projet(self.base.prive, "essai")
            resultats.assert_not_called()
        self.assertEqual(base.read_bytes(), brut)

    def test_reference_protegee_par_chemin_ou_hash_exclue(self):
        for mode in ("path", "hash"):
            with self.subTest(mode=mode):
                code, rapport = self.lancer(protected=mode)
                self.assertNotEqual(code, 0)
                self.assertEqual(rapport["groupes"], [])
                self.assertIn("pièce protégée", rapport["exclus"]["essai:Piece"])

    def test_sha_qualite_protege_la_copie_renommee_et_l_ajout(self):
        """Le SHA de qualite.json filtre une pièce renommée en projet comme en ajout explicite."""
        maillage = self.projet / "candidats/Piece.stl"
        sha = plaques_mixtes.sha256_fichier(maillage)
        (self.projet / "qualite.json").write_text(
            json.dumps({"protegees": {"reference/AncienNom.stl": sha.upper()}}), encoding="utf-8",
        )
        code, rapport = self.lancer(add=True)
        self.assertNotEqual(code, 0)
        self.assertEqual(rapport["groupes"], [])
        self.assertIn("empreinte", rapport["exclus"]["essai:Piece"])
        self.assertIn("ajout:Piece.stl", rapport["exclus"])

    def test_ajout_ne_contourne_pas_une_reference_imprimee(self):
        code, rapport = self.lancer(protected="hash", add=True)
        self.assertNotEqual(code, 0)
        self.assertEqual(rapport["groupes"], [])
        self.assertIn("ajout:Piece.stl", rapport["exclus"])

    def test_plaque_unique_au_dela_de_la_duree_maximale(self):
        code, rapport = self.lancer(seconds=14400)
        self.assertNotEqual(code, 0)
        self.assertFalse(rapport["conforme"])

    def test_duree_source_absente_non_conforme(self):
        code, rapport = self.lancer(missing=True)
        self.assertNotEqual(code, 0)
        self.assertFalse(rapport["conforme"])

    def test_duree_reelle_estimee_apres_tranchage_depasse(self):
        code, rapport = self.lancer(real=14400)
        self.assertNotEqual(code, 0)
        self.assertFalse(rapport["conforme"])
        self.assertTrue(rapport["groupes"][0]["depasse"])

    def test_limites_non_positives_ou_non_finies_refusees(self):
        for valeur in ("0", "-1", "nan", "inf"):
            with self.subTest(valeur=valeur):
                code, _rapport = self.lancer(hmax=valeur)
                self.assertNotEqual(code, 0)

    def test_duree_sous_limite_est_conforme_apres_tranchage(self):
        code, rapport = self.lancer(real=3600)
        self.assertEqual(code, 0)
        self.assertTrue(rapport["conforme"])
        arguments, options = self.ecritures[-1]
        self.assertTrue(options["canonique"])
        self.assertEqual(options["nom_source"], Path(arguments[2]).name)
        self.assertEqual(rapport["export_3mf"]["statut"], "PASS")

    def test_sans_couleur_de_profil_export_geometrique_non_pret(self):
        """Sans couleur explicite, entrée 3MF privée, rapport NON VÉRIFIÉ, réglages inchangés."""
        base = self.projet / "preparations/P01/base.3mf"
        with zipfile.ZipFile(base) as archive:
            reglages = json.loads(archive.read("Metadata/project_settings.config"))
        reglages.pop("filament_colour")
        with zipfile.ZipFile(base, "w") as archive:
            archive.writestr("Metadata/project_settings.config", json.dumps(reglages))
        _code, rapport = self.lancer()
        arguments, options = self.ecritures[-1]
        self.assertNotIn("canonique", options)
        self.assertEqual(rapport["export_3mf"]["statut"], "NON VÉRIFIÉ")
        self.assertTrue(Path(arguments[2]).is_file())

    def test_tranchage_sans_resultat_non_conforme(self):
        code, rapport = self.lancer(real="missing")
        self.assertNotEqual(code, 0)
        self.assertFalse(rapport["conforme"])

    def test_ajout_sans_duree_ne_peut_etre_declare_conforme(self):
        code, rapport = self.lancer(add=True)
        self.assertNotEqual(code, 0)
        self.assertFalse(rapport["conforme"])

    def test_estimations_invalides_source_et_sortie_reste_non_verifiee(self):
        for seconds in (0, -1, float("nan"), float("inf")):
            with self.subTest(seconds=seconds):
                code, rapport = self.lancer(seconds=seconds)
                self.assertEqual(code, 1)
                self.assertEqual(rapport["statut"], "NON VÉRIFIÉ")
                code, rapport = self.lancer(real=seconds)
                self.assertEqual(code, 1)
                self.assertEqual(rapport["statut"], "NON VÉRIFIÉ")

    def test_limite_exacte_et_depassement_sont_comparés_sans_arrondi(self):
        code, _rapport = self.lancer(real=10800)
        self.assertEqual(code, 0)
        code, rapport = self.lancer(real=10800.1)
        self.assertEqual(code, 1)
        self.assertTrue(rapport["groupes"][0]["depasse"])


if __name__ == "__main__":
    unittest.main()
