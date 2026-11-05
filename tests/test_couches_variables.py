"""Régressions de calcul et de publication sur des 3MF synthétiques."""

import hashlib
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import trimesh

from benchab.fabrication import couches_variables as couches

PROFILS = {1: [0, 0.2, 1, 0.2]}


class PublicationProfilsTests(unittest.TestCase):
    def setUp(self):
        temporaire = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.addCleanup(temporaire.cleanup)
        self.racine = Path(temporaire.name)
        self.source = self.racine / "source.3mf"
        self.destination = self.racine / "destination.3mf"
        self.ecrire_archive(self.source)

    @staticmethod
    def ecrire_archive(chemin, extra=b"original"):
        chemin.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(chemin, "w") as archive:
            archive.comment = b"commentaire"
            archive.writestr("3D/3dmodel.model", b"<model/>")
            archive.writestr("original.bin", extra)

    def test_nouvelle_sortie_preserve_source_membres_et_commentaire(self):
        avant = self.source.read_bytes()
        couches.ecrire_profils(self.source, self.destination, PROFILS)
        self.assertEqual(self.source.read_bytes(), avant)
        with zipfile.ZipFile(self.destination) as archive:
            self.assertEqual(archive.read("original.bin"), b"original")
            self.assertEqual(archive.comment, b"commentaire")
            self.assertIn(b"layer_height", archive.read("Metadata/layer_config_ranges.xml"))
            self.assertIsNone(archive.testzip())

    def test_reecriture_mutable_conserve_les_autres_membres(self):
        couches.ecrire_profils(self.source, self.source, PROFILS)
        couches.ecrire_profils(self.source, self.source, {1: [0, 0.24, 1, 0.24]})
        with zipfile.ZipFile(self.source) as archive:
            self.assertIn(b">0.24<", archive.read("Metadata/layer_config_ranges.xml"))
            self.assertEqual(archive.read("original.bin"), b"original")

    def test_preparation_figee_refusee_sans_modifier_le_3mf(self):
        destination = self.racine / "projet/preparations/P01/tranche.3mf"
        self.ecrire_archive(destination)
        avant = destination.read_bytes()
        with self.assertRaisesRegex(ValueError, "figé"):
            couches.ecrire_profils(destination, destination, PROFILS)
        self.assertEqual(destination.read_bytes(), avant)
        fichiers = {chemin.name for chemin in destination.parent.iterdir() if not chemin.name.endswith(".lock")}
        self.assertEqual(fichiers, {destination.name})

    def test_livrable_et_reference_imprimee_refuses(self):
        for type_protection in ("livrable", "reference"):
            with self.subTest(type_protection=type_protection):
                projet = self.racine / type_protection
                cible = projet / "mutable.3mf"
                self.ecrire_archive(cible)
                contenu_initial = cible.read_bytes()
                item = {"chemin": cible.name, "sha256": hashlib.sha256(contenu_initial).hexdigest()}
                if type_protection == "livrable":
                    (projet / "LIVRABLES.json").write_text(json.dumps({"fichiers": [item]}), encoding="utf-8")
                else:
                    (projet / "qualite.json").write_text(
                        json.dumps({"suivi": {"references_imprimees": [{"fichiers": [item]}]}}),
                        encoding="utf-8",
                    )
                with self.assertRaisesRegex(ValueError, "protégé"):
                    couches.ecrire_profils(cible, cible, PROFILS)
                self.assertEqual(cible.read_bytes(), contenu_initial)

    def test_echec_decriture_preserve_destination(self):
        self.ecrire_archive(self.destination, b"ancien")
        avant = self.destination.read_bytes()
        ecrire = zipfile.ZipFile.writestr

        def echec(archive, nom, *args, **kwargs):
            if nom == "Metadata/layer_config_ranges.xml":
                raise OSError("panne synthétique")
            return ecrire(archive, nom, *args, **kwargs)

        with patch.object(zipfile.ZipFile, "writestr", side_effect=echec, autospec=True):
            with self.assertRaisesRegex(OSError, "panne synthétique"):
                couches.ecrire_profils(self.source, self.destination, PROFILS)
        self.assertEqual(self.destination.read_bytes(), avant)
        self.assertFalse(list(self.racine.glob(".couches-variables-*")))

    def test_creation_concurrente_apres_controle_necrase_pas(self):
        lien = os.link

        def course(source, destination, **options):
            self.destination.write_bytes(b"version concurrente")
            return lien(source, destination, **options)

        with patch.object(couches.os, "link", side_effect=course):
            with self.assertRaises(FileExistsError):
                couches.ecrire_profils(self.source, self.destination, PROFILS)
        self.assertEqual(self.destination.read_bytes(), b"version concurrente")

    def test_modification_tierce_destination_existante_preservee(self):
        """Refuse le remplacement si une autre session modifie la cible pendant le calcul."""
        self.ecrire_archive(self.destination, b"ancienne sortie")
        self.destination.write_bytes(b"version utilisateur")
        sha_original = couches.sha256_fichier
        mutation_faite = False

        def modifier_pendant_lecture(chemin):
            nonlocal mutation_faite
            if Path(chemin) == self.source and not mutation_faite:
                mutation_faite = True
                self.destination.write_bytes(b"version utilisateur concurrente")
            return sha_original(chemin)

        with patch.object(couches, "sha256_fichier", side_effect=modifier_pendant_lecture):
            with self.assertRaisesRegex(ValueError, "modifiée"):
                couches.ecrire_profils(self.source, self.destination, PROFILS)

        self.assertEqual(self.destination.read_bytes(), b"version utilisateur concurrente")
        self.assertFalse(list(self.racine.glob(".couches-variables-*")))

    def test_profil_geometrique_variable_et_plages(self):
        maillage = trimesh.creation.box(extents=(10, 10, 5))
        points = couches.profil(maillage, base=0.2, hmin=0.12, hmax=0.28, marche=0.1, pas=0.5)
        plages = couches.plages(points)
        self.assertGreaterEqual(len(points), 4)
        self.assertEqual(points[-2], 5.0)
        self.assertTrue(all(fin > debut and 0.12 <= hauteur <= 0.28 for debut, fin, hauteur in plages))

    def test_hauteurs_gcode_avec_lecteur_partage(self):
        archive = self.racine / "hauteurs.3mf"
        with zipfile.ZipFile(archive, "w") as paquet:
            paquet.writestr("Metadata/plate_1.gcode", ";HEIGHT:0.20\n;HEIGHT:0.24\n")
        resultat = couches.hauteurs_gcode(archive)
        self.assertEqual(resultat, {"couches": 2, "min": 0.2, "max": 0.24, "distinctes": 2})

    def test_etude_privee_tranche_par_api_commune_et_applique_seulement_sur_demande(self):
        prive = self.racine / "prive"
        projet = prive / "projets" / "essai"
        preuves = projet / "preuves"
        candidats = projet / "candidats"
        preuves.mkdir(parents=True)
        candidats.mkdir()
        source = candidats / "plaques.3mf"
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr(
                "Metadata/project_settings.config",
                json.dumps(
                    {
                        "layer_height": "0.2",
                        "filament_type": ["PLA"],
                        "filament_colour": ["#ffffff"],
                        "filament_ids": ["filament-permanent-001"],
                        "filament_id": "filament-permanent-001",
                        "filament_settings_id": ["PLA"],
                    }
                ),
            )
        placement = preuves / "placement.json"
        placement.write_text(json.dumps({"objects": [{"name": "Piece"}]}), encoding="utf-8")
        (preuves / "tranchage.json").write_text(
            json.dumps({"plaques": [{"index": 1, "secondes": 7200, "grammes": 20}]}), encoding="utf-8"
        )
        trimesh.creation.box(extents=(10, 10, 5)).export(candidats / "Piece.stl")

        def simuler_tranchage(source_3mf, dossier, nom, *, usage, prive):
            self.assertEqual(usage, "impression")
            self.assertEqual(Path(prive), self.racine / "prive")
            with zipfile.ZipFile(source_3mf) as archive:
                reglages = json.loads(archive.read("Metadata/project_settings.config"))
            self.assertEqual(reglages["filament_ids"], ["filament-permanent-001"])
            destination = Path(dossier) / nom
            with zipfile.ZipFile(destination, "w") as archive:
                archive.writestr(
                    "Metadata/slice_info.config",
                    '<config><plate><metadata key="index" value="1" />'
                    '<metadata key="prediction" value="3600" />'
                    '<metadata key="weight" value="10" /></plate></config>',
                )
                archive.writestr("Metadata/plate_1.gcode", ";HEIGHT:0.2\n")
            return destination, 1.0

        sortie = preuves / "couches-variables"
        with patch.object(couches.anycubic, "trancher", side_effect=simuler_tranchage) as lancer:
            resultat = couches.main(
                source,
                prive=prive,
                placement=placement,
                candidats=candidats,
                sortie=sortie,
                mode="qualite",
                appliquer=True,
            )
        lancer.assert_called_once()
        self.assertEqual(resultat["applique"], "qualite")
        plaque = resultat["modes"]["qualite"]["plaques"][0]
        self.assertEqual(
            (plaque["comparaison_fixe_statut"], plaque["fixe_min"], plaque["fixe_g"], plaque["gain_temps_pct"]),
            ("PASS", 120, 20.0, 50.0),
        )
        with zipfile.ZipFile(source) as archive:
            self.assertIn("Metadata/layer_config_ranges.xml", archive.namelist())
        self.assertTrue((sortie / "comparaison.json").is_file())


if __name__ == "__main__":
    unittest.main()
