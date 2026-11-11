"""Synthèses sur fixtures, provenance, contexte et vraies vues de géométrie."""

import hashlib
import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from benchab.organisation import synthese_atelier as S


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Synthese(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.r = Path(self.tmp.name) / "repo"
        self.r.mkdir()
        self.p = self.r / "projets/test"
        self.p.mkdir(parents=True)
        self.q = {
            "suivi": {
                "titre": "Projet test",
                "statut": "EN_PREPARATION",
                "preparation_courante": "P01",
                "physique": {"imprime": False},
                "filaments": {"1": {"matiere": "PLA", "temp": 220}},
                "connaissances": ["K1"],
                "contexte": {"filament": "PLA"},
            }
        }
        self.ecrire("qualite.json", self.q)
        self.gcode = (
            "M83\nG1 X10 Y10 Z0.2\n;TYPE:Support\nG1 X20 E2 F600\n;TYPE:Outer wall"
            "\nG1 Y20 E3 F600\nM104 S220\n; support_top_z_distance = 0.2\n"
        )
        self.creer_prepa("P01", 220)

    def tearDown(self):
        self.tmp.cleanup()

    def ecrire(self, rel, value):
        p = self.p / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(value))
        return p

    def creer_prepa(self, ident, temp):
        refs = {}
        for role, nom in (("plaques_3mf", "plaques.3mf"), ("tranche_3mf", "tranche.3mf")):
            p = self.p / "preparations" / ident / nom
            p.parent.mkdir(parents=True, exist_ok=True)
            with ZipFile(p, "w") as z:
                z.writestr(
                    "Metadata/project_settings.config",
                    json.dumps(
                        {"nozzle_temperature": [str(temp)], "support_top_z_distance": "0.2", "filament_type": ["PLA"]}
                    ),
                )
                if role == "tranche_3mf":
                    z.writestr("Metadata/plate_1.gcode", self.gcode)
                    z.writestr(
                        "Metadata/slice_info.config",
                        (
                            '<config><plate><metadata key="index" value="1"/><metadata key="pr'
                            'ediction" value="120"/><metadata key="weight" value="3.5"/></plat'
                            "e></config>"
                        ),
                    )
            refs[role] = {"fichier": p.relative_to(self.r).as_posix(), "sha256": sha(p)}
        report = self.ecrire("preparations/" + ident + "/controle.json", {"statut": "PASS", "limite": "fixture"})
        d = {
            "id": ident,
            "statut": "candidate non imprimée",
            "parametres": {"temperature_voulue": temp},
            **refs,
            "gcode": {"1": {"sha256": hashlib.sha256(self.gcode.encode()).hexdigest()}},
            "controles": {report.relative_to(self.r).as_posix(): {"sha256": sha(report), "statuts": {"PASS": 1}}},
        }
        return self.ecrire("preparations/" + ident + "/PREPARATION.json", d)

    @unittest.skipUnless(importlib.util.find_spec("trimesh"), "trimesh absent")
    def test_correspondance_source_pose_et_geometrie(self):
        import trimesh

        from benchab.fabrication import anycubic as A

        mesh = trimesh.creation.box([10, 20, 30])
        mesh.apply_translation(-mesh.bounds[0])
        archive = self.p / "source.3mf"
        A.ecrire_3mf(
            [{"name": "Source", "items": [{"name": "Bloc", "mesh": mesh, "x": 15, "y": 25, "extruder": 1}]}],
            {},
            archive,
        )
        fiche = {"plaques_3mf": {"fichier": "source.3mf", "sha256": sha(archive)}}
        objets = [{"piece": "Bloc", "plaque": 1, "xy_plaque_mm": [15, 25], "geometrie": A.empreinte_geometrie(mesh)}]
        self.assertEqual(
            S.correspondance_geometrie(self.r, self.p.resolve(), fiche, objets)["statut"], "CONFORME_SOURCE"
        )
        objets[0]["xy_plaque_mm"] = [16, 25]
        self.assertEqual(S.correspondance_geometrie(self.r, self.p.resolve(), fiche, objets)["statut"], "DIVERGENTE")
        objets[0]["xy_plaque_mm"] = [15, 25]
        autre = mesh.copy()
        autre.vertices[0, 0] += 0.1
        objets[0]["geometrie"] = A.empreinte_geometrie(autre)
        self.assertEqual(S.correspondance_geometrie(self.r, self.p.resolve(), fiche, objets)["statut"], "DIVERGENTE")
        with self.assertRaises(ValueError):
            S.correspondance_geometrie(
                self.r, self.p.resolve(), {"plaques_3mf": {"fichier": "source.3mf", "sha256": "0" * 64}}, objets
            )

    def test_provenance_malformee_refusee_sans_lecture_geometrie(self):
        archive = self.p / "provenance.3mf"
        with ZipFile(archive, "w") as z:
            z.writestr("Metadata/benchab_geometry.json", "[]")
        fiche = {"plaques_3mf": {"fichier": "provenance.3mf", "sha256": sha(archive)}}
        with self.assertRaisesRegex(ValueError, "malformée"):
            S.correspondance_geometrie(self.r, self.p.resolve(), fiche, [{"piece": "Q"}])

    def test_budget_gcode_distinct_sans_allouer_gros_membre(self):
        from unittest.mock import Mock

        archive = Mock()
        archive.taille_membre.return_value = 70 * 1024 * 1024
        archive.lire.return_value = b"M83\n"
        with self.assertRaises(ValueError):
            S._membre(archive, "autre.model")
        archive.lire.assert_not_called()
        self.assertEqual(S._membre(archive, "Metadata/plate_1.gcode", S.MAX_GCODE), b"M83\n")
        archive.reset_mock()
        archive.taille_membre.return_value = S.MAX_GCODE + 1
        with self.assertRaises(ValueError):
            S._membre(archive, "Metadata/plate_1.gcode", S.MAX_GCODE)
        archive.lire.assert_not_called()
        archive.taille_membre.return_value = S.MAX_JSON + 1
        with self.assertRaises(ValueError):
            S._membre(archive, "Metadata/project_settings.config", S.MAX_JSON)
        archive.lire.assert_not_called()

    def test_bilan_refuse_le_xml_de_modele_hors_contrat(self):
        """Un XML avec déclaration DTD ne doit pas alimenter la synthèse."""
        fiche = self.p / "preparations/P01/PREPARATION.json"
        declaration = json.loads(fiche.read_bytes())
        archive = self.r / declaration["tranche_3mf"]["fichier"]
        with ZipFile(archive, "a") as z:
            z.writestr("Metadata/model_settings.config", "<!DOCTYPE config><config/>")
        declaration["tranche_3mf"]["sha256"] = sha(archive)
        fiche.write_text(json.dumps(declaration))
        empreinte = sha(archive)

        _, tranches, supports, _, alertes = S._archives(self.r, self.p, declaration)

        self.assertEqual((tranches, supports), ([], []))
        self.assertTrue(any("DOCTYPE" in alerte for alerte in alertes))
        self.assertEqual(sha(archive), empreinte)

    def test_bilan_applique_budget128_uniquement_aux_gcodes(self):
        with patch.object(S, "_membre", wraps=S._membre) as lire:
            self.assertTrue(S.bilan(self.r, "test")["supports"])
        calls = [c.args for c in lire.call_args_list]
        self.assertTrue(any((args[1] == "Metadata/plate_1.gcode" and args[2] == S.MAX_GCODE for args in calls)))
        self.assertTrue(all((len(args) > 2 and args[2] == S.MAX_GCODE for args in calls if args[1].endswith(".gcode"))))
        self.assertTrue(all((args[2] == S.MAX_JSON for args in calls if args[1] == "Metadata/project_settings.config")))

    @unittest.skipUnless(importlib.util.find_spec("trimesh"), "trimesh absent")
    def test_plan_source_pnn_remplace_absence_placement_global_sans_ecriture(self):
        import trimesh

        from benchab.fabrication import anycubic as A

        mesh = trimesh.creation.box([10, 20, 30])
        mesh.apply_translation(-mesh.bounds[0])
        self.ecrire(
            "candidats/assemblage.json",
            {"models": {"Bloc": {"couleur": "#794522"}}, "parts": [{"name": "Bloc", "model": "Bloc"}]},
        )
        mesh.export(self.p / "candidats/Bloc.stl")
        self.q["manifeste"] = "candidats/assemblage.json"
        self.ecrire("qualite.json", self.q)
        archive = self.p / "preparations/P01/plaques.3mf"
        A.ecrire_3mf(
            [{"name": "P", "items": [{"name": "Bloc", "mesh": mesh, "x": 15, "y": 25, "extruder": 1}]}], {}, archive
        )
        path = self.p / "preparations/P01/PREPARATION.json"
        d = json.loads(path.read_text())
        d["plaques_3mf"]["sha256"] = sha(archive)
        path.write_text(json.dumps(d))
        avant = path.read_bytes()
        plan = S.placement_preparation(self.r, self.p.resolve(), d)
        self.assertEqual(plan["objects"][0]["xy_interface"], [15, 25])
        out = Path(self.tmp.name).resolve() / "pose-source"
        r = S.generer_vues(self.r, "test", out)
        self.assertEqual([v["statut"] for v in r["vues"]], ["DISPONIBLE"] * 4)
        self.assertEqual(r["correspondance"]["statut"], "CONFORME_SOURCE")
        data = json.loads(re.search('id="donnees">(.*?)</script>', (out / "piece.html").read_text(), re.S)[1])
        self.assertEqual(data["modeles"]["Bloc"]["couleur"], "#794522")
        self.ecrire(
            "candidats/assemblage.json",
            {"models": {"Bloc": {"couleur": "red;url(evil)"}}, "parts": [{"name": "Bloc", "model": "Bloc"}]},
        )
        bad = S.generer_vues(self.r, "test", Path(self.tmp.name).resolve() / "couleur-invalide")
        self.assertEqual(bad["statut"], "INDISPONIBLE")
        self.assertEqual(avant, path.read_bytes())
        self.assertFalse((self.p / "preuves/placement.json").exists())
        d["plaques_3mf"]["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            S.placement_preparation(self.r, self.p.resolve(), d)

    def test_bilan_distingue_intention_config_commandes_et_supports(self):
        b = S.bilan(self.r, "test")
        self.assertEqual(b["preparation"]["id"], "P01")
        self.assertEqual(b["reglages"]["intention"]["filaments"]["1"]["temp"], 220)
        self.assertEqual(b["reglages"]["embarques"]["tranche_3mf"]["nozzle_temperature"], ["220"])
        self.assertEqual(b["reglages"]["tranches"][0]["commandes"]["M104"], [{"S": 220.0}])
        self.assertEqual(b["supports"][0]["filament_extrude_mm"], 2)
        self.assertEqual(b["supports"][0]["z_nominal"]["globaux"]["support_top_z_distance"], "0.2")
        self.assertEqual(b["supports"][0]["bornes_mm"][0], [10, 10, 0.2])
        self.assertIsNone(b["supports"][0]["surface_contact_mm2"])
        self.assertEqual(b["estimations_slicer"][0]["secondes"], 120)
        self.assertEqual(b["controles"][0]["statut"], "CONFORME")

    def test_source_modifiee_ne_produit_pas_mesures(self):
        p = self.p / "preparations/P01/tranche.3mf"
        p.write_bytes(p.read_bytes() + b"x")
        b = S.bilan(self.r, "test")
        self.assertEqual(b["reglages"]["tranches"], [])
        self.assertTrue(any(("modifiée" in x for x in b["avertissements"])))

    def test_gcode_non_lie_est_non_mesure(self):
        f = self.p / "preparations/P01/PREPARATION.json"
        d = json.loads(f.read_text())
        d["gcode"] = {}
        f.write_text(json.dumps(d))
        b = S.bilan(self.r, "test")
        self.assertEqual(b["supports"], [])
        self.assertTrue(any(("G-code plaque" in x for x in b["avertissements"])))

    def test_comparaison_detaillee_et_lecture_sans_ecriture(self):
        self.creer_prepa("P02", 210)
        avant = {str(p): sha(p) for p in self.r.rglob("*") if p.is_file()}
        c = S.comparer_preparations(self.r, "test", "P01", "P02")
        self.assertTrue(
            any(
                (
                    x["champ"] == "embarques.tranche_3mf.nozzle_temperature" and x["apres"] == ["210"]
                    for x in c["ecarts"]["reglages"]
                )
            )
        )
        self.assertEqual(avant, {str(p): sha(p) for p in self.r.rglob("*") if p.is_file()})

    def test_document_lie_rejette_un_autre_contenu(self):
        f = self.ecrire("rapport.json", {"a": 1})
        avant = sha(f)
        f.write_text('{"a":2}')
        with self.assertRaises(ValueError):
            S._json(f, attendu=avant)

    def test_modification_pendant_empreinte_detectee(self):
        f = self.ecrire("rapport.json", {"a": 1})
        original = S._digest

        def modifier(stream):
            digest = original(stream)
            f.write_text('{"nouveau":2}')
            return digest

        with patch.object(S, "_digest", side_effect=modifier):
            with self.assertRaises(ValueError):
                S._preuve(self.r, self.p, "rapport.json")

    def test_estimation_xml_invalide_reste_inconnue(self):
        p = self.p / "preparations/P01/tranche.3mf"
        with ZipFile(p) as z:
            contenu = {i.filename: z.read(i) for i in z.infolist()}
        contenu["Metadata/slice_info.config"] = b"<invalide"
        with ZipFile(p, "w") as z:
            for n, v in contenu.items():
                z.writestr(n, v)
        f = self.p / "preparations/P01/PREPARATION.json"
        d = json.loads(f.read_text())
        d["tranche_3mf"]["sha256"] = sha(p)
        f.write_text(json.dumps(d))
        b = S.bilan(self.r, "test")
        self.assertEqual(b["estimations_slicer"], [])
        self.assertTrue(any(("Durée" in a for a in b["avertissements"])))

    def test_dependances_declarent_absence_optionnelle_et_prepa(self):
        deps = S.dependances_vues(self.r, "test")
        self.assertIn("projets/test/preuves/placement.json", deps)
        self.assertIn("projets/test/preparations/P01/tranche.3mf", deps)

    def test_sources_de_connaissance_perimees_observation_conservee(self):
        src = self.ecrire("retour.json", {"retour": "bon"})
        h = sha(src)
        reg = self.r / "donnees/connaissances/registre.json"
        reg.parent.mkdir(parents=True)
        reg.write_text(
            json.dumps(
                {
                    "connaissances": [
                        {
                            "id": "K1",
                            "titre": "Retour",
                            "niveau_preuve": "MESURE_PHYSIQUE",
                            "sources": [{"chemin": src.relative_to(self.r).as_posix(), "sha256": h}],
                            "contexte": {"filament": "PETG"},
                            "validite": "contextuelle",
                            "exclusions": "Pas universel",
                        }
                    ]
                }
            )
        )
        src.write_text("{}")
        c = S.connaissances(self.r, "test")["elements"][0]
        self.assertEqual(c["statut"], "SOURCE_A_REVOIR")
        self.assertTrue(c["retour_physique_conserve"])
        self.assertEqual(c["contexte"], "INCOMPATIBLE")

    def test_statut_ne_generalise_pas_references_imprimees(self):
        self.assertIn("Candidat", S.statut_clair({"statut": "EN_PREPARATION", "physique": {"imprime": True}}))
        self.assertIn("Prêt numériquement", S.statut_clair({"statut": "PRET_NUMERIQUEMENT"}))
        self.assertIn("Historique", S.statut_clair({"statut": "HISTORIQUE", "physique": {"imprime": True}}))

    def test_chemins_traversants_et_liens_refuses(self):
        with self.assertRaises(ValueError):
            S.bilan(self.r, "../test")
        with self.assertRaises(ValueError):
            S.bilan(self.r, "test", "P01/../P02")
        f = self.p / "preparations/P01/tranche.3mf"
        outside = Path(self.tmp.name) / "outside"
        f.rename(outside)
        f.symlink_to(outside)
        with self.assertRaises(ValueError):
            S.bilan(self.r, "test")

    def test_generation_hors_depot_et_pas_sur_dossier_existant(self):
        with self.assertRaises(ValueError):
            S.generer_vues(self.r, "test", self.r / "sortie")
        dest = Path(self.tmp.name).resolve() / "vues"
        dest.mkdir()
        with self.assertRaises(ValueError):
            S.generer_vues(self.r, "test", dest)

    def test_geometrie_absente_est_indisponible(self):
        r = S.generer_vues(self.r, "test", Path(self.tmp.name).resolve() / "vues")
        self.assertEqual(r["statut"], "INDISPONIBLE")
        self.assertEqual(len(r["vues"]), 4)
        self.assertTrue(all((v["statut"] == "INDISPONIBLE" for v in r["vues"])))

    def test_parent_remplace_avant_publication_ne_touche_pas_pnn(self):
        parent = Path(self.tmp.name).resolve() / "destination"
        parent.mkdir()
        out = parent / "vues"
        protected = self.p / "preparations/P01"
        avant = {p.name: sha(p) for p in protected.iterdir() if p.is_file()}
        original = S._publier_vues

        def remplacer(stage, cible):
            parent.rename(parent.with_name("destination-originale"))
            parent.symlink_to(protected, target_is_directory=True)
            return original(stage, cible)

        with patch.object(S, "_publier_vues", side_effect=remplacer):
            with self.assertRaises((OSError, ValueError)):
                S.generer_vues(self.r, "test", out)
        self.assertEqual(avant, {p.name: sha(p) for p in protected.iterdir() if p.is_file()})
        self.assertFalse((protected / "vues").exists())

    def test_parent_remplace_apres_epinglage_ne_touche_pas_pnn(self):
        parent = Path(self.tmp.name).resolve() / "destination"
        parent.mkdir()
        out = parent / "vues"
        protected = self.p / "preparations/P01"
        avant = {p.name: sha(p) for p in protected.iterdir() if p.is_file()}
        original = S.renommer_exclusif

        def remplacer(source, destination, **kwargs):
            parent.rename(parent.with_name("destination-originale"))
            parent.symlink_to(protected, target_is_directory=True)
            return original(source, destination, **kwargs)

        with patch.object(S, "renommer_exclusif", side_effect=remplacer):
            with self.assertRaises((OSError, ValueError)):
                S.generer_vues(self.r, "test", out)
        self.assertEqual(avant, {p.name: sha(p) for p in protected.iterdir() if p.is_file()})
        self.assertFalse((protected / "vues").exists())

    def test_rename_fd_exclusif_ne_remplace_pas_destination_vide(self):
        import os

        from benchab.qualite.ecritures import renommer_exclusif

        parent = Path(self.tmp.name).resolve() / "rename"
        parent.mkdir()
        (parent / "source").mkdir()
        (parent / "destination").mkdir()
        inode = (parent / "destination").stat().st_ino
        fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with self.assertRaises(FileExistsError):
                renommer_exclusif("source", "destination", src_dir_fd=fd, dst_dir_fd=fd)
            self.assertTrue((parent / "source").is_dir())
            self.assertEqual((parent / "destination").stat().st_ino, inode)
            for nom in ("..", "sous/dossier", "/absolu", "lien\\suite"):
                with self.assertRaises(ValueError):
                    renommer_exclusif(nom, "ok", src_dir_fd=fd, dst_dir_fd=fd)
            renommer_exclusif("source", "neuve", src_dir_fd=fd, dst_dir_fd=fd)
            self.assertTrue((parent / "neuve").is_dir())
        finally:
            os.close(fd)

    @unittest.skipUnless(importlib.util.find_spec("trimesh"), "trimesh absent")
    def test_vue_utilise_copie_figee_meme_si_chemin_original_remplace(self):
        import trimesh

        from benchab.visualisation.visionneuse import Visionneuse

        self.ecrire("candidats/assemblage.json", {"models": {"Bloc": {}}, "parts": [{"name": "Bloc", "model": "Bloc"}]})
        trimesh.creation.box((10, 10, 10)).export(self.p / "candidats/Bloc.stl")
        self.ecrire(
            "preuves/placement.json",
            {"objects": [{"name": "Bloc", "model": "Bloc", "plate": 1, "xy_interface": [100, 100]}]},
        )
        self.q.update(manifeste="candidats/assemblage.json")
        self.ecrire("qualite.json", self.q)
        original = self.p / "preparations/P01/tranche.3mf"
        attendu = sha(original)
        methode = Visionneuse.vue_impression
        observes = []

        def remplacer(v, placement, candidats, tranche=None, *args, **kw):
            observes.append(Path(tranche))
            original.write_bytes(b"contenu remplace apres validation")
            self.assertNotEqual(Path(tranche), original)
            return methode(v, placement, candidats, tranche, *args, **kw)

        with patch.object(Visionneuse, "vue_impression", new=remplacer):
            dest = Path(self.tmp.name).resolve() / "frozen"
            r = S.generer_vues(self.r, "test", dest)
        self.assertEqual(r["statut"], "PASS")
        self.assertEqual(len(observes), 1)
        data = json.loads(re.search('id="donnees">(.*?)</script>', (dest / "supports.html").read_text(), re.S)[1])
        self.assertEqual(data["source"]["tranche_sha256"], attendu)
        self.assertTrue(any((x["nom"] == "Supports plaque 1" for x in data["pieces"])))
        self.assertFalse(observes[0].exists())

    @unittest.skipUnless(importlib.util.find_spec("trimesh"), "trimesh absent")
    def test_vues_refusent_de_presenter_candidat_divergent_comme_preparation(self):
        import trimesh

        from benchab.fabrication import anycubic as A

        self.ecrire("candidats/assemblage.json", {"models": {"Bloc": {}}, "parts": [{"name": "Bloc", "model": "Bloc"}]})
        mesh = trimesh.creation.box((10, 10, 10))
        mesh.export(self.p / "candidats/Bloc.stl")
        self.ecrire(
            "preuves/placement.json",
            {"objects": [{"name": "Bloc", "model": "Bloc", "plate": 1, "xy_interface": [100, 100]}]},
        )
        self.q.update(manifeste="candidats/assemblage.json")
        self.ecrire("qualite.json", self.q)
        mesh.apply_translation(-mesh.bounds[0])
        mesh.vertices[0, 0] += 0.1
        source = self.p / "preparations/P01/plaques.3mf"
        A.ecrire_3mf(
            [{"name": "Plaque", "items": [{"name": "Bloc", "mesh": mesh, "x": 100, "y": 100, "extruder": 1}]}],
            {},
            source,
        )
        fiche = self.p / "preparations/P01/PREPARATION.json"
        d = json.loads(fiche.read_text())
        d["plaques_3mf"]["sha256"] = sha(source)
        fiche.write_text(json.dumps(d))
        r = S.generer_vues(self.r, "test", Path(self.tmp.name).resolve() / "divergence")
        self.assertEqual(r["correspondance"]["statut"], "DIVERGENTE")
        self.assertEqual([v["statut"] for v in r["vues"]], ["DISPONIBLE", "INDISPONIBLE", "INDISPONIBLE", "DISPONIBLE"])

    @unittest.skipUnless(importlib.util.find_spec("trimesh"), "trimesh absent")
    def test_quatre_vues_reelles_liees_et_deterministes(self):
        import trimesh

        self.ecrire("candidats/assemblage.json", {"models": {"Bloc": {}}, "parts": [{"name": "Bloc", "model": "Bloc"}]})
        trimesh.creation.box((10, 10, 10)).export(self.p / "candidats/Bloc.stl")
        self.ecrire(
            "preuves/placement.json",
            {"objects": [{"name": "Bloc", "model": "Bloc", "plate": 1, "xy_interface": [100, 100]}]},
        )
        self.q.update(manifeste="candidats/assemblage.json")
        self.ecrire("qualite.json", self.q)
        dest = Path(self.tmp.name).resolve() / "vues"
        r = S.generer_vues(self.r, "test", dest)
        self.assertEqual([v["statut"] for v in r["vues"]], ["DISPONIBLE"] * 4)
        donnees = []
        for vue in r["vues"]:
            page = (dest / vue["fichier"]).read_text()
            data = json.loads(re.search('id="donnees">(.*?)</script>', page, re.S)[1])
            donnees.append(data)
            self.assertTrue(data["glb"])
            self.assertEqual(data["vue_initiale"], vue["camera_direction"])
        self.assertTrue(any(("Supports plaque 1" == x["nom"] for x in donnees[2]["pieces"])))
        r2 = S.generer_vues(self.r, "test", Path(self.tmp.name).resolve() / "vues2")
        self.assertEqual([x["sha256"] for x in r["vues"]], [x["sha256"] for x in r2["vues"]])


if __name__ == "__main__":
    unittest.main()
