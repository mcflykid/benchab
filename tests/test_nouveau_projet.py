"""Régressions du créateur natif; toutes les mutations restent dans des bases temporaires privées."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

from benchab.donnees.base import Base
from benchab.projets import nouveau as N
from benchab.qualite.publication import charger


class NouveauProjet(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="benchab-nouveau-", dir="/private/tmp")
        self.prive = Path(self.temporary.name).resolve()
        self.base = Base(self.prive)

    def tearDown(self):
        self.base.fermer()
        self.temporary.cleanup()

    def creer(self, nom, **kw):
        return N.creer(self.prive, nom, base=self.base, **kw)

    def assert_absent(self, nom):
        self.assertFalse((self.prive / "projets" / nom).exists())
        self.assertIsNone(self.base.connexion.execute("SELECT id FROM projets WHERE nom=?", (nom,)).fetchone())
        self.assertFalse(list((self.prive / "projets").glob(".creation-*")))

    def test_defaut_s_m_l_sans_fabrication(self):
        for niveau in ("S", "M", "L"):
            projet = self.creer("demo-" + niveau.lower(), niveau=niveau)
            self.assertFalse((projet / "FABRICATION.json").exists())
            self.assertTrue((projet / "organisation/organisation.json").is_file())
            self.assertTrue((projet / "src/__init__.py").is_file())
            self.assertTrue((projet / "src/preparer.py").is_file())
            self.assertTrue((projet / "src/trancher.py").is_file())
            self.assertFalse((projet / "preparations").exists())
            if niveau in ("M", "L"):
                self.assertTrue((projet / "interfaces.json").is_file())
                self.assertTrue((projet / "src/controler_interfaces.py").is_file())
            if niveau == "L":
                self.assertTrue((projet / "src/verifier.py").is_file())
                self.assertTrue((projet / "src/controles/exemple_mouvement.py").is_file())
            data, suivi = charger(projet)
            self.assertEqual(suivi["statut"], "EN_CONCEPTION")
            self.assertFalse(suivi["physique"]["imprime"])
            for source in projet.rglob("*.py"):
                text = source.read_text()
                self.assertNotIn("sys.path", text)
                self.assertNotIn("outils/", text)
                compile(text, str(source), "exec")
            make = shutil.which("make")
            dry = subprocess.run(
                [make, "-n", "PYTHON=" + sys.executable, "MESURES=0", "tout"],
                cwd=projet,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertEqual(self.base.connexion.execute("SELECT COUNT(*) FROM projets").fetchone()[0], 3)

    def test_controles_l_ne_promouvoient_pas_un_statut_inconnu(self):
        """Un contrôle privé inconnu reste NV/code3, un FAIL domine ; aucun moteur ni impression."""
        for nom, statuts, attendu, code in (
            ("inconnu", ["INCONNU"], "NON VÉRIFIÉ", 3),
            ("non-standard", ["surprise"], "NON VÉRIFIÉ", 3),
            ("dominance", ["INCONNU", "FAIL"], "FAIL", 2),
        ):
            projet = self.creer(nom, niveau="L")
            for index, statut in enumerate(statuts):
                (projet / f"src/controles/fixture_{index}.py").write_text(
                    "def verifier(projet):\n    return {'statut': " + repr(statut) + "}\n"
                )
            execution = subprocess.run(
                [sys.executable, "-B", "-m", "src.verifier"], cwd=projet,
                env={**os.environ, "BENCHAB_PRIVE": str(self.prive), "PYTHONDONTWRITEBYTECODE": "1"},
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(execution.returncode, code, execution.stderr)
            self.assertEqual(json.loads((projet / "preuves/qualite-projet.json").read_text())["statut"], attendu)

    def test_interfaces_conservent_essai_na_et_verifient_partenaire(self):
        """Essai sans minimum NA ; fichier partenaire et SHA contrôlés numériquement, aucun essai physique."""
        for nom, present, bon_sha, attendu in (
            ("partenaire-valide", True, True, "PASS"),
            ("partenaire-altere", True, False, "FAIL"),
            ("partenaire-absent", False, True, "FAIL"),
        ):
            projet = self.creer(nom, niveau="M")
            contenu = b"fixture partenaire imprime declare"
            if present:
                (projet / "partenaire.stl").write_bytes(contenu)
            (projet / "interfaces.json").write_text(json.dumps({"interfaces": [{
                "id": "IF-1", "type": "essai", "justification": "Essai de jeu non mesure",
                "partenaire_imprime": {"fichier": "partenaire.stl", "sha256":
                    hashlib.sha256(contenu).hexdigest() if bon_sha else "0" * 64},
            }]}))
            execution = subprocess.run(
                [sys.executable, "-B", "-m", "src.controler_interfaces"], cwd=projet,
                env={**os.environ, "BENCHAB_PRIVE": str(self.prive), "PYTHONDONTWRITEBYTECODE": "1"},
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(execution.returncode, 0 if attendu == "PASS" else 2, execution.stderr)
            rapport = json.loads((projet / "preuves/interfaces.json").read_text())
            self.assertEqual(rapport["resultats"][0]["politique"], "NON APPLICABLE")
            self.assertEqual(rapport["resultats"][0]["partenaire"]["statut"], attendu)

    def test_controles_l_conservent_erreurs_import_et_execution(self):
        """Import cassé et RuntimeError donnent FAIL avec pile et sortie complète ; aucun moteur lancé."""
        projet = self.creer("erreurs-controles", niveau="L")
        (projet / "src/controles/import_casse.py").write_text("raise RuntimeError('import fixture exact')\n")
        (projet / "src/controles/execution_cassee.py").write_text(
            "def verifier(projet):\n    print('sortie fixture exacte')\n"
            "    raise RuntimeError('execution fixture exacte')\n"
        )
        execution = subprocess.run(
            [sys.executable, "-B", "-m", "src.verifier"], cwd=projet,
            env={**os.environ, "BENCHAB_PRIVE": str(self.prive), "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(execution.returncode, 2, execution.stderr)
        rapport = json.loads((projet / "preuves/qualite-projet.json").read_text())
        self.assertEqual(rapport["statut"], "FAIL")
        for nom, erreur in (("import_casse", "import fixture exact"), ("execution_cassee", "execution fixture exacte")):
            journal = json.loads((projet / f"preuves/controles/{nom}.json").read_text())
            self.assertEqual(journal["statut"], "FAIL")
            self.assertIn("RuntimeError: " + erreur, journal["pile"])
        self.assertIn("sortie fixture exacte", json.loads(
            (projet / "preuves/controles/execution_cassee.json").read_text())["sortie_complete"])

    def test_source_3mf_crc_invalide_ne_cree_pas_de_projet(self):
        """Une copie 3MF au CRC incorrect ne publie ni projet ni ligne SQLite."""
        source = self.prive / "source.3mf"
        with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("Metadata/notes.txt", b"preuve 1234")
        brut = source.read_bytes().replace(b"preuve 1234", b"preuve 5678")
        source.write_bytes(brut)
        with self.assertRaises((ValueError, zipfile.BadZipFile)):
            self.creer("crc-invalide", sources=[source])
        self.assert_absent("crc-invalide")
        self.assertEqual(source.read_bytes(), brut)

    def test_objectifs_opt_in_et_collision_preserve(self):
        for objectif in sorted(N.OBJECTIFS):
            projet = self.creer("objectif-" + objectif, objectif=objectif)
            config = json.loads((projet / "FABRICATION.json").read_text())
            self.assertEqual(config["schema"], 1)
            self.assertEqual(config["objectif"], objectif)
            self.assertIsNone(config["machine"]["profil"])
            before = (projet / "FABRICATION.json").read_bytes()
            with self.assertRaises(FileExistsError):
                self.creer("objectif-" + objectif, objectif="rapide")
            self.assertEqual((projet / "FABRICATION.json").read_bytes(), before)

    def test_objectif_invalide_refuse_avant_publication(self):
        with self.assertRaisesRegex(ValueError, "objectif invalide"):
            self.creer("invalide", objectif="turbo")
        self.assert_absent("invalide")

    def test_make_optimisation_est_opt_in(self):
        """Vérifie l’option explicite sans fabrication ; aucune unité, retour None, aucun slicer."""
        projet = self.creer("demo")
        make = shutil.which("make")
        self.assertIsNotNone(make)
        base = [make, "-n", "PYTHON=" + sys.executable, "MESURES=0"]
        normal = subprocess.run([*base, "tout"], cwd=projet, capture_output=True, text=True, check=False)
        opt = subprocess.run([*base, "tout", "OPTIMISER=1"], cwd=projet, capture_output=True, text=True, check=False)
        direct = subprocess.run([*base, "optimiser"], cwd=projet, capture_output=True, text=True, check=False)
        self.assertEqual(normal.returncode, 0, normal.stderr)
        self.assertEqual(opt.returncode, 0, opt.stderr)
        self.assertEqual(direct.returncode, 0, direct.stderr)
        self.assertNotIn("src.optimiser", normal.stdout)
        self.assertIn("--optimiser", opt.stdout)
        self.assertNotIn("--optimiser", normal.stdout)
        self.assertIn("src.optimiser", direct.stdout)

    def test_build_et_preparation_sont_locaux_et_ne_lancent_aucun_trancheur(self):
        projet = self.creer("workflow")
        parametres = projet / "src/parametres.py"
        parametres.write_text("""P={'titre':'workflow','longueur':12,'largeur':8,'epaisseur':2}
FILAMENTS={'1':{'type':'PLA','color':'#eeeeee','id':'fixture-material-id','settings_id':'fixture-profile',
                'temp':210,'temp_1re':205,'plateau_c':50,'debit_max':10}}
REGLAGES={'layer_height':'0.2','wall_loops':'3','sparse_infill_density':'20%','sparse_infill_pattern':'gyroid','top_shell_layers':'4','enable_support':'1','brim_width':'7'}
PLAQUES=[{'name':'Plaque','ecart_mm':4,'items':[{'piece':'Piece','copies':2,'extruder':1,
         'reglages':{'top_shell_layers':6,'wall_loops':5}}]}]
VUE={}; REMPLISSAGE={}; LISSAGE={}; EFFORTS={}
""")
        env = {**os.environ, "BENCHAB_PRIVE": str(self.prive), "TMPDIR": "/private/tmp", "PYTHONDONTWRITEBYTECODE": "1"}
        built = subprocess.run(
            [sys.executable, "-B", "-m", "src.construire"],
            cwd=projet,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(built.returncode, 0, built.stderr)
        self.assertTrue((projet / "candidats/Piece.stl").is_file())
        self.assertTrue((projet / "candidats/assemblage.json").is_file())
        prepared = subprocess.run(
            [sys.executable, "-B", "-m", "src.preparer"],
            cwd=projet,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        with zipfile.ZipFile(projet / "candidats/plaques.3mf") as archive:
            self.assertIn("Metadata/project_settings.config", archive.namelist())
            settings = json.loads(archive.read("Metadata/project_settings.config"))
            self.assertEqual(settings["top_shell_layers"], "4")
            self.assertEqual(settings["filament_settings_id"], ["fixture-profile"])
            self.assertEqual(settings["filament_type"], ["PLA"])
            self.assertEqual(settings["filament_colour"], ["#eeeeee"])
            self.assertEqual(settings["filament_ids"], ["fixture-material-id"])
            model = ElementTree.fromstring(archive.read("Metadata/model_settings.config"))
        objects = model.findall("object")
        self.assertEqual(len(objects), 2)
        noms_objets = [
            next(m.get("value") for m in obj.findall("metadata") if m.get("key") == "name") for obj in objects
        ]
        self.assertEqual(noms_objets, ["Piece_1", "Piece_2"])
        reglages_objets = [{m.get("key"): m.get("value") for m in obj.findall("metadata")} for obj in objects]
        for reglages_objet in reglages_objets:
            self.assertEqual(reglages_objet["top_shell_layers"], "6")
            self.assertEqual(reglages_objet["wall_loops"], "5")
            self.assertNotIn("layer_height", reglages_objet)
            self.assertNotIn("sparse_infill_density", reglages_objet)
        placement = json.loads((projet / "preuves/placement.json").read_text())
        self.assertEqual(len(placement["objects"]), 2)
        self.assertEqual(
            placement["assembly_manifest_sha256"],
            hashlib.sha256((projet / "candidats/assemblage.json").read_bytes()).hexdigest(),
        )
        for objet in placement["objects"]:
            self.assertEqual(objet["reglages_effectifs"]["enable_support"], "1")
            self.assertEqual(objet["reglages_effectifs"]["brim_width"], "7")
            self.assertEqual(objet["reglages_effectifs"]["top_shell_layers"], "6")
        self.assertAlmostEqual(
            placement["objects"][1]["xy_interface"][0] - placement["objects"][0]["xy_interface"][0],
            4 + placement["objects"][0]["size_mm"][0],
        )
        m = self.creer("assemblage", niveau="M")
        checked = subprocess.run(
            [sys.executable, "-B", "-m", "src.controler_interfaces"],
            cwd=m,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(checked.returncode, 1)
        self.assertEqual(json.loads((m / "preuves/interfaces.json").read_text())["statut"], "WARN")
        niveau_l = self.creer("mecanisme", niveau="L")
        references = subprocess.run(
            [sys.executable, "-B", "-m", "src.references"],
            cwd=niveau_l,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(references.returncode, 0, references.stderr)
        self.assertEqual((niveau_l / "src/controles/exemple_mouvement.py").is_file(), True)
        fills = subprocess.run(
            [sys.executable, "-B", "-m", "src.remplissages"],
            cwd=niveau_l,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(fills.returncode, 1, fills.stderr)
        rapport = json.loads((niveau_l / "preuves/remplissages.json").read_text())
        self.assertEqual(rapport["statut"], "WARN")

    def test_preparation_applique_cavites_zones_et_lissage_via_les_apis_communes(self):
        projet = self.creer("geometrie-locale")
        candidates = projet / "candidats"
        candidates.mkdir(exist_ok=True)
        import trimesh

        trimesh.creation.box(extents=(30, 30, 12)).export(candidates / "Piece.stl")
        (candidates / "assemblage.json").write_text(json.dumps({"pieces": {"Piece": {}}}))
        (projet / "src/parametres.py").write_text(
            "P={'titre':'fixture','longueur':30,'largeur':30,'epaisseur':12}\n"
            "FILAMENTS={'1':{'type':'PLA','color':'#ffffff','id':'fixture-id','settings_id':'fixture-profile',"
            "'temp':210,'temp_1re':205,'plateau_c':50,'debit_max':10}}\n"
            "REGLAGES={'wall_loops':'3'}\n"
            "PLAQUES=[{'name':'P','ecart_mm':6,'items':[{'piece':'Piece','copies':1,'extruder':1,'reglages':{'ironing_type':'top'}}]}]\n"
            "CAVITES={'Piece':[{'nom':'cav','forme':'toit','min':[4,4,3],'max':[12,12,9]}]}\n"
            "CONTRAINTES={}\n"
            "ZONES_REMPLISSAGE={'Piece':[{'nom':'zone','forme':'boite','min':[15,15,3],"
            "'max':[20,20,6],'role':'structurel'}]}\n"
            "LISSAGE={'Piece':'topmost'}\n"
            "VUE={}; REMPLISSAGE={}; EFFORTS={}\n"
        )
        env = {**os.environ, "BENCHAB_PRIVE": str(self.prive), "TMPDIR": "/private/tmp", "PYTHONDONTWRITEBYTECODE": "1"}
        prepared = subprocess.run(
            [sys.executable, "-B", "-m", "src.preparer"],
            cwd=projet,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        self.assertTrue((candidates / "optimise/Piece.stl").is_file())
        cavites = json.loads((projet / "preuves/optimisation-interne.json").read_text())
        self.assertIn("Piece", cavites)
        zones = json.loads((projet / "preuves/remplissage-zones.json").read_text())
        self.assertEqual(zones["Piece"][0]["nom"], "zone")
        with zipfile.ZipFile(candidates / "plaques.3mf") as archive:
            geometry = json.loads(archive.read("Metadata/benchab_geometry.json"))
            self.assertEqual(len(geometry["objets"][0]["modificateurs"]), 1)
            model = ElementTree.fromstring(archive.read("Metadata/model_settings.config"))
        object_metadata = {m.get("key"): m.get("value") for m in model.findall("object")[0].findall("metadata")}
        self.assertEqual(object_metadata["ironing_type"], "top")

    def test_preparation_texture_cree_variante_sans_modifier_le_candidat_source(self):
        """Texture la géométrie via l’API commune; les cotes source restent intactes."""
        projet = self.creer("texture-geometrique")
        candidates = projet / "candidats"
        candidates.mkdir(exist_ok=True)
        import trimesh

        source = candidates / "Piece.stl"
        trimesh.creation.box(extents=(12, 12, 4)).export(source)
        (candidates / "assemblage.json").write_text(json.dumps({"pieces": {"Piece": {}}}))
        (projet / "src/parametres.py").write_text(
            "P={'titre':'texture','longueur':12,'largeur':12,'epaisseur':4}\n"
            "FILAMENTS={'1':{'type':'PLA','color':'#ffffff','id':'fixture-id','settings_id':'fixture-profile',"
            "'temp':210,'temp_1re':205,'plateau_c':50,'debit_max':10}}\n"
            "REGLAGES={'wall_loops':'3'}\n"
            "PLAQUES=[{'name':'P','items':[{'piece':'Piece','copies':1,'extruder':1}]}]\n"
            "CONTRAINTES={}\n"
            "TEXTURES={'Piece':{'motif':'moletage','periode':3.0,'amplitude':0.3,'angle_haut':30,'arete_max':0.8}}\n"
        )
        source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
        env = {**os.environ, "BENCHAB_PRIVE": str(self.prive), "TMPDIR": "/private/tmp", "PYTHONDONTWRITEBYTECODE": "1"}

        prepared = subprocess.run(
            [sys.executable, "-B", "-m", "src.preparer"],
            cwd=projet,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), source_sha256)
        texture = candidates / "texture/Piece.stl"
        self.assertTrue(texture.is_file())
        self.assertNotEqual(hashlib.sha256(texture.read_bytes()).hexdigest(), source_sha256)
        self.assertTrue((candidates / "plaques.3mf").is_file())
        bilan = json.loads((projet / "preuves/textures.json").read_text())["Piece"]
        self.assertIn(bilan["statut"], ("PASS", "WARN"))
        self.assertFalse(any(c["statut"] == "FAIL" for c in bilan["controles"]))
        texture_mesh = trimesh.load_mesh(texture)
        self.assertAlmostEqual(float(texture_mesh.bounds[0][2]), 0.0, places=6)

    def test_filament_sans_identifiants_explicites_refuse_avant_ecriture(self):
        projet = self.creer("filament-incomplet")
        candidates = projet / "candidats"
        candidates.mkdir(exist_ok=True)
        import trimesh

        trimesh.creation.box(extents=(10, 10, 4)).export(candidates / "Piece.stl")
        (candidates / "assemblage.json").write_text(json.dumps({"pieces": {"Piece": {}}}))
        (projet / "src/parametres.py").write_text(
            "P={'titre':'fixture','longueur':10,'largeur':10,'epaisseur':4}\n"
            "FILAMENTS={'1':{'type':'PLA','color':'#ffffff','settings_id':'fixture-profile',"
            "'temp':210,'temp_1re':205,'plateau_c':50,'debit_max':10}}\n"
            "REGLAGES={}\nPLAQUES=[{'name':'P','items':[{'piece':'Piece'}]}]\n"
        )
        env = {**os.environ, "BENCHAB_PRIVE": str(self.prive), "TMPDIR": "/private/tmp", "PYTHONDONTWRITEBYTECODE": "1"}
        prepared = subprocess.run(
            [sys.executable, "-B", "-m", "src.preparer"],
            cwd=projet,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(prepared.returncode, 0)
        self.assertIn("type, color, id et settings_id", prepared.stderr)
        self.assertFalse((candidates / "plaques.3mf").exists())
        self.assertFalse((projet / "preuves/placement.json").exists())

    def test_etude_lissage_dedoublonne_piece_sur_plusieurs_plaques(self):
        """Une pièce répétée n'envoie qu'une étude A/B ; temps en h simulés, aucun slicer ni changement imprimé."""
        projet = self.creer("lissage-unique")
        (projet / "src/parametres.py").write_text(
            "FILAMENTS={'1':{}}; REGLAGES={}; LISSAGE={'Piece':'top'}\n"
            "PLAQUES=[{'items':[{'piece':'Piece','extruder':1}]}, {'items':[{'piece':'Piece','extruder':1}]}]\n"
        )
        (projet / "candidats/plaques.3mf").write_bytes(b"fixture non lue par moteur")
        code = r"""import json
from pathlib import Path
from unittest.mock import patch
import src.lissage as L
def faux(argv):
 etude=Path(argv[-1]);data=json.loads(etude.read_text())
 assert len(data['pieces'])==1
 sortie=etude.parent/'resultat';sortie.mkdir()
 (sortie/'comparaison.json').write_text(json.dumps({'resultats':[
  {'piece':'Piece','heures':1.0}, {'piece':'Piece','heures':1.2,'gain_temps_pct':-20}]}))
 return 0
with patch.object(L.comparer,'cli',side_effect=faux) as comparaison:
 assert L.main()==0
 assert comparaison.call_count==1
"""
        execution = subprocess.run(
            [sys.executable, "-B", "-c", code], cwd=projet, capture_output=True, text=True, check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "BENCHAB_PRIVE": str(self.prive)},
        )
        self.assertEqual(execution.returncode, 0, execution.stderr)
        rapport = json.loads((projet / "preuves/lissage.json").read_text())
        self.assertEqual(list(rapport["pieces"]), ["Piece"])
        self.assertEqual(rapport["pieces"]["Piece"]["cout_temps_pct"], 20)

    def test_trancher_transmet_tous_les_reglages_filaments_sans_lancer_slicer(self):
        projet = self.creer("multi")
        parametres = projet / "src/parametres.py"
        parametres.write_text("""P={'titre':'fixture','longueur':2,'largeur':2,'epaisseur':1}
FILAMENTS={'1':{'type':'PLA','color':'#aaaaaa','id':'fixture-id-a','settings_id':'fixture-profile-a',
                'temp':220,'temp_1re':210,'plateau_c':55,'debit_max':12},
           '2':{'type':'PETG','color':'#bbbbbb','id':'fixture-id-b','settings_id':'fixture-profile-b',
                'temp':240,'temp_1re':230,'plateau_c':60,'debit_max':14}}
REGLAGES={'slow_down_layer_time':['8','12']}
PLAQUES=[{'name':'P','items':[{'piece':'Piece','copies':1,'extruder':1,'reglages':{}}]}]
VUE={}; REMPLISSAGE={}; LISSAGE={}; EFFORTS={}
""")
        (projet / "candidats/plaques.3mf").write_bytes(b"3mf-fixture")
        code = r"""from pathlib import Path
from unittest.mock import patch
import src.trancher as T
def fake(source, output, name, **kw):
 p=Path(output);p.mkdir(parents=True,exist_ok=True);f=p/name
 import zipfile
 with zipfile.ZipFile(f,'w') as z:
  z.writestr('Metadata/slice_info.config', '<config><plate><metadata key="index" value="1"/>'
    '<metadata key="prediction" value="120"/><metadata key="weight" value="3.5"/></plate></config>')
 (Path.cwd()/"attendu.json").write_text(__import__("json").dumps(kw["attendu"]))
 return f,{"code":0,"duree_s":0.0}
with patch("src.trancher.trancher_fichier",side_effect=fake) as call:
 T.main();assert call.call_count==1
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", code], cwd=projet, capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        attendu = json.loads((projet / "attendu.json").read_text())
        self.assertEqual(attendu["nozzle_temperature"], ["220", "240"])
        self.assertEqual(attendu["nozzle_temperature_initial_layer"], ["210", "230"])
        self.assertEqual(attendu["textured_plate_temp"], ["55", "60"])
        self.assertEqual(attendu["slow_down_layer_time"], ["8", "12"])
        self.assertTrue((projet / "candidats/tranche.3mf").is_file())
        rapport = json.loads((projet / "preuves/tranchage.json").read_text())
        self.assertEqual(rapport["plaques"][0]["secondes"], 120)
        self.assertEqual(rapport["plaques"][0]["grammes"], 3.5)

    def test_nom_argument_et_collision_casse_refuses_sans_ecrasement(self):
        for nom, options in (
            ("Avec-Majuscule", {}),
            ("mauvais_nom", {}),
            ("../sortie", {}),
            ("correct", {"titre": "x\nmalforme"}),
            ("niveau", {"niveau": "XL"}),
            ("option", {"objectif": "turbo"}),
        ):
            with self.subTest(nom=nom), self.assertRaises(ValueError):
                self.creer(nom, **options)
        self.assertEqual(self.base.connexion.execute("SELECT COUNT(*) FROM projets").fetchone()[0], 0)
        old = self.prive / "projets/EXISTANT"
        old.mkdir()
        (old / "user.txt").write_text("conserver")
        with self.assertRaises(FileExistsError):
            self.creer("existant")
        self.assertEqual((old / "user.txt").read_text(), "conserver")

    def test_creation_actualise_registre_et_regles_git_privees(self):
        projet = self.creer("demain")
        ligne = self.base.connexion.execute(
            "SELECT origine,etat,donnees_json FROM projets WHERE nom='demain'"
        ).fetchone()
        self.assertEqual(ligne["origine"], "gabarit")
        self.assertEqual(ligne["etat"], "conception")
        self.assertEqual(json.loads(ligne["donnees_json"])["suivi"]["statut"], "EN_CONCEPTION")
        ignore = (projet / ".gitignore").read_text()
        self.assertIn("!/src/*.py", ignore)
        self.assertFalse((self.prive / "projets/INDEX-LOCAL.md").exists())
        self.assertFalse((projet / "docs").exists())
        self.assertFalse((projet / "FABRICATION.json").exists())

    def test_echec_export_rollback_et_reprise(self):
        original = self.base.exporter
        calls = 0

        def fail_once():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("export indisponible")
            return original()

        with patch.object(self.base, "exporter", side_effect=fail_once):
            with self.assertRaises(OSError):
                self.creer("rollback")
        self.assert_absent("rollback")
        self.creer("rollback")
        self.assertTrue((self.prive / "projets/rollback").is_dir())

    def test_echec_ecriture_et_interruption_ne_publient_pas_de_dossier_partiel(self):
        for erreur in (OSError("écriture interrompue"), KeyboardInterrupt()):
            with patch.object(N, "_remplir_gabarit", side_effect=erreur):
                with self.assertRaises(type(erreur)):
                    self.creer("interrompu")
            self.assert_absent("interrompu")

    def test_manifeste_publication_invalide_rollback(self):
        with patch.object(N, "charger_publication", side_effect=ValueError("fiche invalide")):
            with self.assertRaisesRegex(ValueError, "fiche invalide"):
                self.creer("invalide")
        self.assert_absent("invalide")

    def test_index_manuel_existant_reste_intact_et_inoffensif(self):
        index = self.prive / "projets/INDEX.md"
        index.write_text("notes privées conservées")
        self.creer("projet")
        self.assertEqual(index.read_text(), "notes privées conservées")
        self.assertTrue((self.prive / "projets/projet").is_dir())

    def test_remplissage_interrompu_est_un_echec(self):
        projet = self.creer("remplissage")
        script = projet / "src/remplissages.py"
        script.write_text("raise SystemExit(1)\n")
        result = subprocess.run(
            [shutil.which("make"), "PYTHON=" + sys.executable, "MESURES=0", "remplissages"],
            cwd=projet,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_gabarit_incomplet_refuse_sans_publication(self):
        original = N.fichiers_gabarit

        def incomplet(niveau):
            contenu = original(niveau)
            contenu.pop("src/controler_interfaces.py", None)
            return contenu

        with patch.object(N, "fichiers_gabarit", side_effect=incomplet):
            with self.assertRaisesRegex(ValueError, "gabarit incomplet"):
                self.creer("incomplet", niveau="M")
        self.assert_absent("incomplet")

    def test_gabarit_python_invalide_refuse_avant_publication(self):
        """Un gabarit Python invalide donne FAIL sans dossier projet ni ligne SQLite ; aucun import du script."""
        original = N.fichiers_gabarit

        def invalide(niveau):
            contenu = original(niveau)
            contenu["src/modele.py"] = "def fonction_incomplete(\n"
            return contenu

        with patch.object(N, "fichiers_gabarit", side_effect=invalide):
            with self.assertRaises(SyntaxError):
                self.creer("syntaxe-invalide")
        self.assert_absent("syntaxe-invalide")

    def test_import_reussi_n_est_pas_reference_implicitement(self):
        source = self.prive / "original.stl"
        source.write_text("mesh source fixture")
        projet = self.creer("importe", sources=[source], resultat="reussie", preuve="retour utilisateur daté")
        data, suivi = charger(projet)
        self.assertTrue(suivi["physique"]["imprime"])
        self.assertEqual(suivi["references_imprimees"], [])
        self.assertEqual(data["fichiers_copies"][0]["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertEqual(source.read_text(), "mesh source fixture")

    def test_creation_concurrente_unique(self):
        commande = [
            sys.executable,
            "-B",
            "-c",
            "from pathlib import Path; from os import environ; "
            "from benchab.projets.nouveau import main; "
            "raise SystemExit(main(['unique'], prive=Path(environ['BENCHAB_PRIVE'])))",
        ]
        env = {**os.environ, "BENCHAB_PRIVE": str(self.prive)}
        first = subprocess.Popen(
            commande, env=env, cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        second = subprocess.Popen(
            commande, env=env, cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        out_a, err_a = first.communicate(timeout=20)
        out_b, err_b = second.communicate(timeout=20)
        self.assertEqual(sorted([first.returncode, second.returncode]), [0, 2], (out_a, err_a, out_b, err_b))
        self.assertEqual(
            self.base.connexion.execute("SELECT COUNT(*) FROM projets WHERE nom='unique'").fetchone()[0], 1
        )
        self.assertFalse(list((self.prive / "projets").glob(".creation-*")))

    def test_publication_exclusive_refuse_destination_vide(self):
        source, cible = self.prive / "source-test", self.prive / "cible-test"
        source.mkdir()
        cible.mkdir()
        (source / "important").write_text("garder")
        with self.assertRaises(OSError):
            renommer = __import__("benchab.qualite.ecritures", fromlist=["renommer_exclusif"]).renommer_exclusif
            renommer(source, cible)
        self.assertEqual((source / "important").read_text(), "garder")
        self.assertEqual(list(cible.iterdir()), [])

    def test_import_formats_sha_et_suivi_physique_inconnu(self):
        for index, suffix in enumerate((".stl", ".step", ".py", ".gcode.3mf", ".3mf")):
            source = self.prive / ("source" + str(index) + suffix)
            if suffix.endswith(".3mf"):
                with zipfile.ZipFile(source, "w") as archive:
                    archive.writestr("Metadata/plate_1.gcode", "G1 X1")
            else:
                source.write_text("source fixture " + str(index))
            before = source.read_bytes()
            projet = self.creer("historique-" + str(index), sources=[source])
            _, suivi = charger(projet)
            copie = next((projet / "sources").iterdir())
            self.assertIsNone(suivi["physique"]["imprime"])
            self.assertEqual(suivi["physique"]["resultat"], "inconnu")
            self.assertEqual(suivi["references_imprimees"], [])
            self.assertFalse((projet / "src").exists())
            self.assertEqual(hashlib.sha256(copie.read_bytes()).digest(), hashlib.sha256(before).digest())
            self.assertEqual(source.read_bytes(), before)

    def test_preparation_refuse_preuves_existantes_et_liens_avant_toute_sortie(self):
        import trimesh

        original = self.prive / "original-simule.json"
        original.write_bytes(b"original intact")
        cas = [
            (mode, rapport)
            for mode in ("existant", "lien", "parent-lie", "lien-pendant")
            for rapport in ("placement.json", "optimisation-interne.json", "remplissage-zones.json")
        ]
        for index, (mode, rapport) in enumerate(cas):
            with self.subTest(mode=mode, rapport=rapport):
                projet = self.creer(f"preuve-protegee-{index}")
                candidats = projet / "candidats"
                candidats.mkdir(exist_ok=True)
                trimesh.creation.box(extents=(30, 30, 12)).export(candidats / "Piece.stl")
                (candidats / "assemblage.json").write_text(json.dumps({"pieces": {"Piece": {}}}))
                parametres = projet / "src/parametres.py"
                parametres.write_text(
                    parametres.read_text().replace(
                        "FILAMENTS = {}",
                        "FILAMENTS = {'1':{'type':'PLA','color':'#ffffff','id':'fixture-id',"
                        "'settings_id':'fixture-profile','temp':210,'temp_1re':205,'plateau_c':50,'debit_max':10}}",
                    )
                )
                with parametres.open("a") as fichier:
                    fichier.write(
                        "\nCAVITES={'Piece':[{'nom':'cav','forme':'toit','min':[4,4,3],'max':[12,12,9]}]}\n"
                        "ZONES_REMPLISSAGE={'Piece':[{'nom':'zone','forme':'boite','min':[15,15,3],"
                        "'max':[20,20,6],'role':'structurel'}]}\n"
                    )
                preuves = projet / "preuves"
                if mode == "parent-lie":
                    preuves.rmdir()
                    exterieur = self.prive / f"preuves-exterieures-{index}"
                    exterieur.mkdir()
                    preuves.symlink_to(exterieur, target_is_directory=True)
                cible = preuves / rapport
                if mode == "existant":
                    cible.write_bytes(b"preuve intacte")
                elif mode == "lien":
                    cible.symlink_to(original)
                elif mode == "lien-pendant":
                    cible.symlink_to(self.prive / "absent.json")
                env = {**os.environ, "BENCHAB_PRIVE": str(self.prive), "TMPDIR": "/private/tmp"}
                resultat = subprocess.run(
                    [sys.executable, "-B", "-m", "src.preparer"],
                    cwd=projet,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(resultat.returncode, 0, resultat.stdout)
                self.assertTrue(
                    "symbolique" in resultat.stderr or "écrasement refusé" in resultat.stderr, resultat.stderr
                )
                self.assertFalse((candidats / "plaques.3mf").exists())
                self.assertEqual(original.read_bytes(), b"original intact")
                self.assertFalse((self.prive / "absent.json").exists())
                if mode == "existant":
                    self.assertEqual(cible.read_bytes(), b"preuve intacte")
                if mode == "parent-lie":
                    self.assertEqual(list(exterieur.iterdir()), [])

    def test_rapports_auxiliaires_refusent_un_parent_lie(self):
        for index, module in enumerate(("remplissages", "lissage", "controler_interfaces", "verifier")):
            with self.subTest(module=module):
                projet = self.creer(f"rapport-auxiliaire-{index}", niveau="L")
                preuves = projet / "preuves"
                preuves.rmdir()
                exterieur = self.prive / f"exterieur-auxiliaire-{index}"
                exterieur.mkdir()
                preuves.symlink_to(exterieur, target_is_directory=True)
                env = {**os.environ, "BENCHAB_PRIVE": str(self.prive), "TMPDIR": "/private/tmp"}
                resultat = subprocess.run(
                    [sys.executable, "-B", "-m", "src." + module],
                    cwd=projet,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(resultat.returncode, 0)
                self.assertIn("symbolique", resultat.stderr)
                self.assertEqual(list(exterieur.iterdir()), [])

    def test_import_retour_physique_exige_preuve_et_reference_explicite(self):
        source = self.prive / "source.stl"
        source.write_text("original")
        with self.assertRaises(ValueError):
            self.creer("sans-preuve", sources=[source], resultat="reussie")
        self.assert_absent("sans-preuve")
        ok = self.creer("preuve", sources=[source], resultat="reussie", preuve="déclaration explicite")
        self.assertTrue(charger(ok)[1]["physique"]["imprime"])
        reference = self.creer(
            "reference", sources=[source], resultat="reussie", preuve="référence déclarée", reference=True
        )
        self.assertEqual(len(charger(reference)[1]["references_imprimees"]), 1)

    def test_profil_reference_et_noms_source_conserves_sans_modifier_l_original(self):
        projet = self.creer("profil-explicite")
        profil = self.prive / "profil-reference.3mf"
        reglages = {
            "printer_model": "Anycubic Kobra S1",
            "outer_wall_acceleration": "2000",
            "top_shell_layers": "5",
            "nozzle_temperature_BRASS": ["180"],
        }
        with zipfile.ZipFile(profil, "w") as z:
            z.writestr("Metadata/project_settings.config", json.dumps(reglages))
        original = profil.read_bytes()
        p = projet / "src/parametres.py"
        p.write_text(
            p.read_text().replace(
                "FILAMENTS = {}",
                """FILAMENTS = {'1': {'type':'PLA','color':'#ffffff',
 'id':'fixture-id','settings_id':'fixture-profile','temp':210,'temp_1re':205,'plateau_c':50,'debit_max':10}}""",
            )
            + "\nPROFIL_REFERENCE = "
            + repr(str(profil))
            + "\n"
        )
        env = dict(os.environ, BENCHAB_PRIVE=str(self.prive), PYTHONDONTWRITEBYTECODE="1", TMPDIR="/private/tmp")
        for module in ("src.construire", "src.preparer"):
            resultat = subprocess.run(
                [sys.executable, "-B", "-m", module], cwd=projet, env=env, capture_output=True, text=True
            )
            self.assertEqual(resultat.returncode, 0, resultat.stderr)
        with zipfile.ZipFile(projet / "candidats/plaques.3mf") as z:
            s = json.loads(z.read("Metadata/project_settings.config"))
        self.assertEqual(s["printer_model"], reglages["printer_model"])
        self.assertEqual(s["outer_wall_acceleration"], "2000")
        self.assertEqual(s["top_shell_layers"], "5")
        self.assertEqual(s["nozzle_temperature_BRASS"], ["210"])
        from benchab.fabrication.anycubic import noms_plaques

        self.assertEqual(noms_plaques(projet / "candidats/plaques.3mf"), ["Plaque"])
        self.assertEqual(profil.read_bytes(), original)
        rapport = json.loads((projet / "preuves/profil-source.json").read_text())
        self.assertEqual(rapport["source_sha256"], hashlib.sha256(original).hexdigest())
        self.assertFalse(rapport["source_modifiee"])


if __name__ == "__main__":
    unittest.main()


def test_visualiseur_genere_reprend_poses_et_vues_complementaires(tmp_path):
    """Vérifie les délégations de vues en mm ; aucun rendu, slicer ou changement des modèles n'est exécuté."""
    import trimesh

    base = Base(tmp_path / "prive")
    try:
        projet = N.creer(base.prive, "fixture-vues", base=base)
        candidats = projet / "candidats"
        (candidats / "assemblage").mkdir(parents=True)
        (candidats / "assemblage.json").write_text(json.dumps({"pieces": {"Piece": {}, "Pion": {}}}))
        for nom in ("Piece", "Pion"):
            trimesh.creation.box((12, 8, 2)).export(candidats / (nom + ".stl"))
        trimesh.creation.box((12, 8, 2)).export(candidats / "assemblage/Piece.stl")
        reference = projet / "reference/v01/assemblage"
        reference.mkdir(parents=True)
        shutil.copyfile(candidats / "Piece.stl", reference / "Piece.stl")
        (projet / "preuves/placement.json").write_text('{"objects":[]}')
        matrice = [[1, 0, 0, 10], [0, 1, 0, 20], [0, 0, 1, 0], [0, 0, 0, 1]]
        with (projet / "src/parametres.py").open("a") as flux:
            flux.write("\nVUE = " + repr({"Piece": {"matrice": matrice, "groupe": "Assemblage"}}))
            flux.write("\nZONES_REMPLISSAGE = {'Piece':[{'nom':'zone'}]}\nCAVITES = {'Piece':[{'nom':'cavite'}]}\n")
        code = '''import json
from pathlib import Path
from unittest.mock import patch
appels=[]
class Vue:
    def __init__(self,*a,**k):pass
    def __getattr__(self,nom):
        def appeler(*a,**k):
            appels.append({'fonction':nom,'args':a,'options':k})
            return a[0] if nom=='ecrire' else None
        return appeler
with patch('benchab.visualisation.visionneuse.Visionneuse',Vue):
    from src.visualiser import main
    main()
Path('preuves/appels-vues.json').write_text(json.dumps(appels,default=str))
'''
        resultat = subprocess.run([sys.executable, "-B", "-c", code], cwd=projet,
                                  env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": "/private/tmp"},
                                  capture_output=True, text=True, check=False)
        assert resultat.returncode == 0, resultat.stderr
        appels = json.loads((projet / "preuves/appels-vues.json").read_text())
        piece = next(a for a in appels if a["fonction"] == "piece" and a["args"][0] == "Piece")
        assert piece["options"]["matrice"] == matrice
        modele = next(a for a in appels if a["fonction"] == "modele" and a["args"][0] == "Piece")
        assert modele["args"][1] == str(candidats / "assemblage/Piece.stl")
        assert {"comparer", "montage", "zones_remplissage", "optimisation_interne", "vue_impression", "ecrire"} <= {
            a["fonction"] for a in appels
        }
    finally:
        base.fermer()


def test_remplissage_interrompu_est_fail_et_preuve_existante_preservee(tmp_path):
    """Sans plaques, l'étude retourne FAIL2 et rapport FAIL ; aucun ancien PASS n'est remplacé ou réutilisé."""
    base = Base(tmp_path / "prive")
    try:
        projet = N.creer(base.prive, "fixture-remplissage", base=base)
        with (projet / "src/parametres.py").open("a") as flux:
            flux.write("\nREMPLISSAGE={'*':'support'}\n")
        commande = [sys.executable, "-B", "-m", "src.remplissages"]
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": "/private/tmp"}
        resultat = subprocess.run(commande, cwd=projet, env=env, capture_output=True, text=True, check=False)
        assert resultat.returncode == 2 and "Préparer les plaques" in resultat.stderr
        rapport = projet / "preuves/remplissages.json"
        assert json.loads(rapport.read_text())["statut"] == "FAIL"
        rapport.write_text('{"statut":"PASS","nature":"preuve anterieure a conserver"}')
        avant = rapport.read_bytes()
        resultat = subprocess.run(commande, cwd=projet, env=env, capture_output=True, text=True, check=False)
        assert resultat.returncode == 2 and "Destination existante" in resultat.stderr
        assert rapport.read_bytes() == avant
    finally:
        base.fermer()
