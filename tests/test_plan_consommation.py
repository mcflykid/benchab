"""Preuves synthétiques du plan numérique #77 ; aucun stock ni appareil touché."""

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from benchab.donnees.base import Base
from benchab.fabrication import preparation as PR
from benchab.projets.envois import enregistrer as enregistrer_envoi
from benchab.projets.plan_consommation import estimer_retour
from benchab.projets.preparations import enregistrer as enregistrer_preparation
from benchab.projets.retours import confirmer


class PlanConsommation(unittest.TestCase):
    def setUp(self):
        """Crée un atelier jetable avec retour, préparation et envoi liés par leurs SHA."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.racine = Path(self.temp.name).resolve()
        self.base = Base(self.racine)
        self.addCleanup(self.base.fermer)
        self.projet_id = "projets/fixture"
        self.preparation_id = "preparation-fixture"
        self.retour_id = "retour-fixture"
        self.projet = self.racine / self.projet_id
        self.fiche = self.projet / "preparations/P01/PREPARATION.json"
        self.fiche.parent.mkdir(parents=True)
        fiche = {"id": "P01", "gcode": {"1": {}, "2": {}}}
        octets_fiche = json.dumps(fiche, separators=(",", ":")).encode()
        self.fiche.write_bytes(octets_fiche)
        self.fiche_sha = hashlib.sha256(octets_fiche).hexdigest()
        with zipfile.ZipFile(self.racine / "envoi.gcode.3mf", "w") as archive:
            archive.writestr("Metadata/project_settings.config", json.dumps({"filament_ids": ["f1", "f2"]}))
            archive.writestr("Metadata/plate_1.gcode", "; filament used [g] = 0, 12.5\nT1\nM83\nG1 X1 E1\n")
            archive.writestr("Metadata/plate_2.gcode", "; filament used [g] = 3.25, 0\nT0\nG1 X2 E1\n")
        self.envoi = self.racine / "envoi.gcode.3mf"
        self.envoi_sha = hashlib.sha256(self.envoi.read_bytes()).hexdigest()
        self.chemin_envoi = "archives/envois/envoi.gcode.3mf"
        self.archive_prive = self.racine / self.chemin_envoi
        self.archive_prive.parent.mkdir(parents=True)
        self.archive_prive.write_bytes(self.envoi.read_bytes())
        with self.base.transaction() as connexion:
            connexion.execute(
                "INSERT INTO filaments(id,nom,matiere) VALUES(?,?,?)",
                ("filament-fixture", "Bobine fixture", "PLA"),
            )
            connexion.execute(
                "INSERT INTO bobines(id,filament_id,masse_initiale_g,masse_restante_g) VALUES(?,?,?,?)",
                ("bobine-fixture", "filament-fixture", 100.0, 80.0),
            )
            connexion.execute(
                "INSERT INTO projets(id,nom,origine,donnees_json) VALUES(?,?,?,?)",
                (self.projet_id, "fixture", self.projet_id, "{}"),
            )
            connexion.execute(
                "INSERT INTO preparations(id,projet_id,nom,empreinte,donnees_json) VALUES(?,?,?,?,?)",
                (self.preparation_id, self.projet_id, "P01", self.fiche_sha, json.dumps(fiche)),
            )
            association = {
                "type": "association_fichier_preparation",
                "preparation_id": self.preparation_id,
                "fiche_sha256": self.fiche_sha,
                "envoi": {"fichier": self.chemin_envoi, "sha256": self.envoi_sha},
            }
            connexion.execute(
                "INSERT INTO decisions(id,projet_id,date,texte,source,donnees_json) VALUES(?,?,?,?,?,?)",
                ("association-fixture", self.projet_id, "2026-11-11", "association", self.chemin_envoi,
                 json.dumps(association)),
            )
            retour = {
                "id": self.retour_id,
                "type": "retour_physique",
                "preparation": "P01",
                "resultat": "reussie",
                "portee": "ENSEMBLE",
                "envois_identifies": [{"chemin": self.chemin_envoi, "sha256": self.envoi_sha}],
                "preuve_statut": "DECLARATION_HUMAINE",
            }
            connexion.execute(
                "INSERT INTO impressions(id,preparation_id,resultat,donnees_json) VALUES(?,?,?,?)",
                (self.retour_id, self.preparation_id, "reussie", json.dumps(retour)),
            )
            connexion.execute(
                "INSERT INTO retours(id,impression_id,texte,source,donnees_json) VALUES(?,?,?,?,?)",
                (self.retour_id, self.retour_id, "fixture", "declaration_humaine", json.dumps(retour)),
            )

    def test_plan_lie_estimation_par_plaque_et_index_outil_sans_imputation(self):
        """Le plan expose les grammes du slicer, tout en laissant la consommation NV."""
        avant = tuple(self.base.connexion.execute("SELECT id,masse_restante_g FROM bobines"))
        changements_avant = self.base.connexion.total_changes
        sha_archive_avant = hashlib.sha256(self.archive_prive.read_bytes()).hexdigest()

        resultat = estimer_retour(self.base, self.retour_id)

        self.assertEqual(resultat["statut"], "NON VÉRIFIÉ")
        self.assertEqual(resultat["estimation"]["nature"], "ESTIMATION_SLICER")
        plaques = resultat["estimation"]["plaques"]
        self.assertEqual([plaque["numero"] for plaque in plaques], [1, 2])
        self.assertEqual(plaques[0]["outils"][1]["grammes_estimes"], 12.5)
        self.assertEqual(plaques[1]["outils"][0]["grammes_estimes"], 3.25)
        self.assertIsNone(resultat["affectation_bobine"])
        apres = tuple(self.base.connexion.execute("SELECT id,masse_restante_g FROM bobines"))
        self.assertEqual(apres, avant)
        self.assertEqual(self.base.connexion.total_changes, changements_avant)
        self.assertEqual(self.envoi_sha, hashlib.sha256(self.envoi.read_bytes()).hexdigest())
        self.assertEqual(sha_archive_avant, hashlib.sha256(self.archive_prive.read_bytes()).hexdigest())

    def test_lire_envoi_associe_la_duree_au_numero_du_gcode(self):
        """Les prédictions XML désordonnées sont rattachées au même 3MF par indice."""
        from benchab.fabrication.envois_slicer import lire

        with zipfile.ZipFile(self.envoi, "a") as archive:
            archive.writestr("Metadata/slice_info.config", b"""
                <config>
                  <plate><metadata key="index" value="2"/><metadata key="prediction" value="245"/>
                  <metadata key="weight" value="3.25"/></plate>
                  <plate><metadata key="index" value="1"/><metadata key="prediction" value="125"/>
                  <metadata key="weight" value="12.5"/></plate>
                </config>""")
        self.assertNotIn("duree_estimee_s", lire(self.envoi)["details_plaques"][0])
        rapport = lire(self.envoi, inclure_durees=True)
        self.assertEqual([p["duree_estimee_s"] for p in rapport["details_plaques"]], [125, 245])

    def test_duree_comparee_seulement_pour_un_envoi_a_plaque_unique(self):
        """Une durée globale ne peut pas être attribuée à l’une de plusieurs plaques."""
        from benchab.projets.plan_consommation import _comparer_duree

        retour = {"duree_observee_s": 125}
        detail = {"gcode": "Metadata/plate_2.gcode", "duree_estimee_s": 100}
        comparaison = _comparer_duree(retour, [detail])
        self.assertEqual(comparaison["ecart_s"], 25)
        self.assertEqual(comparaison["gcode"], detail["gcode"])
        self.assertIsNone(_comparer_duree(retour, [detail, {**detail, "gcode": "Metadata/plate_1.gcode"}]))
        self.assertIsNone(_comparer_duree({"duree_observee_s": True}, [detail]))
        self.assertIsNone(_comparer_duree(retour, [{"gcode": detail["gcode"]}]))

    def _declaration_pesee(self, **modifications):
        """Écrit un témoin privé qui déclare la masse et l’identité du membre."""
        membre = "Metadata/plate_1.gcode"
        with zipfile.ZipFile(self.archive_prive) as archive:
            sha_gcode = hashlib.sha256(archive.read(membre)).hexdigest()
        valeurs = {
            "schema": 1, "retour_id": self.retour_id, "envoi_sha256": self.envoi_sha,
            "plaque": 1, "membre_gcode": membre, "gcode_sha256": sha_gcode,
            "index_gcode": 1, "emplacement_declare": "ACE 1 emplacement 2",
            "bobine_id": "bobine-fixture", "masse_avant_g": 100, "masse_apres_g": 88.25,
            **modifications,
        }
        source = self.racine / "pesee.json"
        octets = json.dumps(valeurs).encode()
        source.write_bytes(octets)
        return source, hashlib.sha256(octets).hexdigest()

    def test_pesee_liee_reste_distincte_du_stock_et_du_slicer(self):
        """Un rapprochement reproductible ne change aucune masse enregistrée."""
        from benchab.projets.plan_consommation import planifier_lecture

        preuve, sha = self._declaration_pesee()
        avant = {p: p.read_bytes() for p in (preuve, self.archive_prive, self.racine / "benchab.sqlite")}
        rapport = planifier_lecture(self.racine, self.retour_id, preuve_pesee=preuve, sha256_preuve=sha)
        self.assertEqual(rapport["statut"], "NON VÉRIFIÉ")
        self.assertEqual(rapport["code"], 2)
        self.assertIsNone(rapport["affectation_bobine"])
        observation = rapport["declaration_pesee"]
        self.assertEqual(observation["statut"], "DÉCLARATION LIÉE")
        self.assertEqual(observation["variation_declaree_g"], 11.75)
        self.assertEqual(observation["estimation_slicer_g"], 12.5)
        self.assertFalse(observation["imputation"])
        self.assertFalse(observation["validation_physique"])
        self.assertEqual(avant, {p: p.read_bytes() for p in avant})

    def _confirmation_pesee(self, sha, **modifications):
        """Crée une attestation synthétique distincte du relevé de masse."""
        attestation = {
            "schema": 1, "pesee_sha256": sha, "confirmee": True,
            "date": "2026-01-01T12:00:00+00:00", "auteur": "Fixture",
            "observation": "Bobine, emplacement et pesée avant/après vérifiés sur ce témoin.",
            **modifications,
        }
        source = self.racine / "confirmation.json"
        octets = json.dumps(attestation).encode()
        source.write_bytes(octets)
        return source, hashlib.sha256(octets).hexdigest()

    def test_imputation_cli_et_rejeu_atomique(self):
        """Un rejeu sur une connexion neuve retrouve la trace sans débiter le stock."""
        from contextlib import redirect_stdout
        from io import StringIO

        from benchab.cli import main

        preuve, sha = self._declaration_pesee()
        confirmation, sha_confirmation = self._confirmation_pesee(sha)
        sources = {p: p.read_bytes() for p in (preuve, confirmation, self.archive_prive)}
        arguments = [
            "--prive", str(self.racine), "donnees", "imputer-bobine",
            "--retour", self.retour_id, "--preuve-pesee", str(preuve), "--sha256-preuve", sha,
            "--confirmation", str(confirmation), "--sha256-confirmation", sha_confirmation,
        ]
        rapports = []
        for _ in range(2):
            sortie = StringIO()
            with redirect_stdout(sortie):
                self.assertEqual(main(arguments), 0)
            rapports.append(json.loads(sortie.getvalue()))
        self.assertEqual([r["deja_imputee"] for r in rapports], [False, True])
        self.assertEqual(rapports[0]["decision_id"], rapports[1]["decision_id"])
        self.assertEqual(rapports[0]["consommation_declaree_g"], 11.75)
        self.assertEqual(rapports[0]["stock_apres_g"], 68.25)
        stock = self.base.connexion.execute("SELECT masse_restante_g FROM bobines").fetchone()[0]
        self.assertEqual(stock, 68.25)
        self.assertEqual(sources, {p: p.read_bytes() for p in sources})
        decision = self.base.connexion.execute(
            "SELECT donnees_json FROM decisions WHERE id=?", (rapports[0]["decision_id"],),
        ).fetchone()[0]
        self.assertEqual(json.loads(decision)["confirmation_sha256"], sha_confirmation)
        self.assertFalse(rapports[0]["validation_physique"])

    def test_imputation_refuse_confirmation_stock_et_depot_insuffisants(self):
        """Un refus conserve stock, trace et exports pour une nouvelle tentative."""
        from benchab.projets.imputation_bobine import imputer

        for cas in ("confirmation", "stock", "retraction"):
            with self.subTest(cas=cas):
                if cas == "stock":
                    self.base.ecrire("UPDATE bobines SET masse_restante_g=1")
                if cas == "retraction":
                    self.base.ecrire("UPDATE bobines SET masse_restante_g=80")
                    self.envoi_sha = self._remplacer_archive_et_sha({
                        "Metadata/plate_1.gcode": "; filament used [g] = 0, 12.5\nT1\nM83\nG1 E-1\nG1 X1 E1\n",
                    })
                preuve, sha = self._declaration_pesee()
                options = {"confirmee": False} if cas == "confirmation" else {}
                confirmation, sha_confirmation = self._confirmation_pesee(sha, **options)
                exports = self.racine / "export"
                avant = {p: p.read_bytes() for p in exports.iterdir() if p.is_file()}
                attendu = {"confirmation": "confirmation", "stock": "Stock", "retraction": "dépôt"}[cas]
                with self.assertRaisesRegex(ValueError, attendu):
                    imputer(self.base, self.retour_id, preuve, sha, confirmation, sha_confirmation)
                self.assertEqual(avant, {p: p.read_bytes() for p in avant})

    def test_rejeu_imputation_repare_un_export_interrompu(self):
        """Après le commit SQLite, un échec d’export ne permet pas de débiter à nouveau."""
        from unittest.mock import patch

        from benchab.projets.imputation_bobine import imputer

        preuve, sha = self._declaration_pesee()
        confirmation, sha_confirmation = self._confirmation_pesee(sha)
        with patch.object(self.base, "exporter", side_effect=OSError("export interrompu")):
            with self.assertRaisesRegex(OSError, "interrompu"):
                imputer(self.base, self.retour_id, preuve, sha, confirmation, sha_confirmation)
        with self.assertRaisesRegex(ValueError, "imputation différente"):
            variante, sha_variante = self._confirmation_pesee(sha, observation="Autre confirmation.")
            imputer(self.base, self.retour_id, preuve, sha, variante, sha_variante)
        confirmation, sha_confirmation = self._confirmation_pesee(sha)
        rapport = imputer(self.base, self.retour_id, preuve, sha, confirmation, sha_confirmation)
        self.assertTrue(rapport["deja_imputee"])
        self.assertEqual(rapport["stock_apres_g"], 68.25)
        export = (self.racine / "export/bobines.jsonl").read_text()
        self.assertEqual(json.loads(export)["masse_restante_g"], 68.25)

    def test_deux_connexions_ne_peuvent_pas_imputer_la_meme_pesee(self):
        """Les deux écrivains voient le même débit final et une trace unique."""
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        from benchab.projets.imputation_bobine import imputer

        preuve, sha = self._declaration_pesee()
        confirmation, sha_confirmation = self._confirmation_pesee(sha)
        barrier = Barrier(2, timeout=5)

        def lancer():
            base = Base(self.racine, creer=False)
            try:
                barrier.wait()
                return imputer(base, self.retour_id, preuve, sha, confirmation, sha_confirmation)
            finally:
                base.fermer()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(lancer) for _ in range(2)]
            rapports = [f.result(timeout=20) for f in futures]
        self.assertEqual({r["deja_imputee"] for r in rapports}, {False, True})
        self.assertEqual({r["decision_id"] for r in rapports}, {rapports[0]["decision_id"]})
        stock = self.base.connexion.execute("SELECT masse_restante_g FROM bobines").fetchone()[0]
        self.assertEqual(stock, 68.25)

    def test_imputation_refuse_deux_pesees_qui_se_chevauchent(self):
        """Une même baisse de poids ne peut pas être affectée à deux outils."""
        from benchab.projets.imputation_bobine import imputer

        self.envoi_sha = self._remplacer_archive_et_sha({
            "Metadata/plate_1.gcode": "; filament used [g] = 5, 12.5\nM83\nT1\nG1 X1 E1\nT0\nG1 X2 E1\n",
        })
        preuve, sha = self._declaration_pesee()
        confirmation, sha_confirmation = self._confirmation_pesee(sha)
        imputer(self.base, self.retour_id, preuve, sha, confirmation, sha_confirmation)
        preuve, sha = self._declaration_pesee(index_gcode=0, masse_avant_g=98, masse_apres_g=86)
        confirmation, sha_confirmation = self._confirmation_pesee(sha)
        with self.assertRaisesRegex(ValueError, "se chevauchent"):
            imputer(self.base, self.retour_id, preuve, sha, confirmation, sha_confirmation)
        self.assertEqual(self.base.connexion.execute("SELECT masse_restante_g FROM bobines").fetchone()[0], 68.25)

    def test_imputation_compare_les_pesees_de_la_bobine_entre_impressions(self):
        """Un intervalle commun est refusé et une deuxième pesée adjacente est permise."""
        from benchab.projets.imputation_bobine import imputer

        preuve, sha = self._declaration_pesee()
        confirmation, sha_confirmation = self._confirmation_pesee(sha)
        imputer(self.base, self.retour_id, preuve, sha, confirmation, sha_confirmation)
        retour = json.loads(self.base.connexion.execute(
            "SELECT donnees_json FROM retours WHERE id=?", (self.retour_id,),
        ).fetchone()[0])
        autre = "retour-seconde-impression"
        retour["id"] = autre
        brut = json.dumps(retour)
        with self.base.transaction() as connexion:
            connexion.execute(
                "INSERT INTO impressions(id,preparation_id,resultat,donnees_json) VALUES(?,?,'reussie',?)",
                ("impression-2", self.preparation_id, brut),
            )
            connexion.execute(
                "INSERT INTO retours(id,impression_id,texte,source,donnees_json) VALUES(?,?,?,?,?)",
                (autre, "impression-2", "Fixture", "fixture", brut),
            )
        preuve, sha = self._declaration_pesee(retour_id=autre, masse_avant_g=98, masse_apres_g=86)
        confirmation, sha_confirmation = self._confirmation_pesee(sha)
        with self.assertRaisesRegex(ValueError, "se chevauchent"):
            imputer(self.base, autre, preuve, sha, confirmation, sha_confirmation)
        preuve, sha = self._declaration_pesee(retour_id=autre, masse_avant_g=88.25, masse_apres_g=87.25)
        confirmation, sha_confirmation = self._confirmation_pesee(sha)
        self.assertEqual(imputer(self.base, autre, preuve, sha, confirmation, sha_confirmation)["stock_apres_g"], 67.25)

    def test_imputation_refuse_une_declaration_modifiee_pendant_le_controle(self):
        """Le débit conserve un lien vérifiable vers la déclaration de pesée."""
        from unittest.mock import patch

        from benchab.projets import imputation_bobine

        preuve, sha = self._declaration_pesee()
        confirmation, sha_confirmation = self._confirmation_pesee(sha)
        original = imputation_bobine._confirmation

        def modifier(*args):
            resultat = original(*args)
            preuve.write_bytes(preuve.read_bytes() + b"\n")
            return resultat

        with patch.object(imputation_bobine, "_confirmation", side_effect=modifier):
            with self.assertRaisesRegex(ValueError, "preuve a changé"):
                imputation_bobine.imputer(self.base, self.retour_id, preuve, sha, confirmation, sha_confirmation)
        self.assertEqual(self.base.connexion.execute("SELECT masse_restante_g FROM bobines").fetchone()[0], 80)

    def test_pesee_refuse_identite_membre_outil_ou_bobine_non_etablie(self):
        """Un décalage ne doit pas fabriquer une quantité de stock imputable."""
        from benchab.projets.plan_consommation import planifier_lecture

        for modification in (
            {"envoi_sha256": "0" * 64}, {"gcode_sha256": "0" * 64}, {"index_gcode": 0},
            {"bobine_id": "inconnue"}, {"masse_apres_g": 101},
        ):
            with self.subTest(modification=modification):
                preuve, sha = self._declaration_pesee(**modification)
                rapport = planifier_lecture(self.racine, self.retour_id, preuve_pesee=preuve, sha256_preuve=sha)
                observation = rapport["declaration_pesee"]
                self.assertEqual(observation["statut"], "NON VÉRIFIÉ")
                self.assertFalse(observation["imputation"])
                self.assertIsNone(observation["variation_declaree_g"])
        preuve, _ = self._declaration_pesee()
        rapport = planifier_lecture(self.racine, self.retour_id, preuve_pesee=preuve, sha256_preuve="0" * 64)
        self.assertEqual(rapport["declaration_pesee"]["statut"], "NON VÉRIFIÉ")

    def test_cli_ne_lie_pas_une_pesee_sur_la_seule_masse_positive(self):
        """Une mention T dans un commentaire ne remplace pas la sélection explicite."""
        from contextlib import redirect_stdout
        from io import StringIO

        from benchab.cli import main

        self.envoi_sha = self._remplacer_archive_et_sha({
            "Metadata/plate_1.gcode": "; filament used [g] = 0, 12.5\n; T1\nG1 X1 E1\n",
        })
        preuve, sha = self._declaration_pesee()
        commande = ["--prive", str(self.racine), "controle", "consommation-retour", "--retour", self.retour_id]
        sortie = StringIO()
        with redirect_stdout(sortie):
            code = main([*commande, "--preuve-pesee", str(preuve), "--sha256-preuve", sha])
        self.assertEqual(code, 2)
        rapport = json.loads(sortie.getvalue())
        self.assertEqual(rapport["declaration_pesee"]["statut"], "NON VÉRIFIÉ")
        self.assertIn("aucune sélection T explicite", rapport["declaration_pesee"]["raison"])
        self.assertEqual(rapport["estimation"]["plaques"][0]["outils"][1]["grammes_estimes"], 12.5)

    def test_cli_resume_conserve_le_rapport_complet_et_le_code(self):
        """Le résumé #35 reste exploitable sans perdre le rapport ni les limites #77."""
        from contextlib import redirect_stdout
        from io import StringIO

        from benchab.cli import main

        preuve, sha = self._declaration_pesee()
        commande = [
            "--prive", str(self.racine), "controle", "consommation-retour",
            "--retour", self.retour_id, "--preuve-pesee", str(preuve), "--sha256-preuve", sha,
        ]
        complet = StringIO()
        with redirect_stdout(complet):
            self.assertEqual(main(commande), 2)
        cible = self.racine / "rapports/consommation.json"
        cible.parent.mkdir(exist_ok=True)
        court = StringIO()
        with redirect_stdout(court):
            self.assertEqual(main([*commande, "--resume", "--sortie", str(cible)]), 2)
        self.assertEqual(json.loads(cible.read_text()), json.loads(complet.getvalue()))
        resume = json.loads(court.getvalue())
        self.assertEqual(resume["statut"], "NON VÉRIFIÉ")
        self.assertEqual(resume["rapport"], str(cible))
        self.assertEqual(resume["plaques_estimees"], 2)
        self.assertEqual(resume["declaration_pesee"]["statut"], "DÉCLARATION LIÉE")
        self.assertLess(len(court.getvalue()), len(complet.getvalue()))
        self.assertNotIn("bobine-fixture", court.getvalue())

    def test_cli_resume_ne_divulgue_pas_le_chemin_de_la_declaration_refusee(self):
        """Les refus système et de frontière gardent le diagnostic sans chemin privé."""
        from contextlib import redirect_stdout
        from io import StringIO

        from benchab.cli import main

        rapports = self.racine / "rapports"
        rapports.mkdir(exist_ok=True)
        absent = self.racine / "declaration-privee-absente.json"
        lien = self.racine / "declaration-privee-liee.json"
        lien.symlink_to(absent)
        for n, preuve in enumerate((absent, lien, self.racine.parent / "declaration-hors-atelier.json")):
            with self.subTest(n=n):
                sortie = StringIO()
                complet = rapports / f"consommation-{n}.json"
                with redirect_stdout(sortie):
                    code = main([
                        "--prive", str(self.racine), "controle", "consommation-retour",
                        "--retour", self.retour_id, "--preuve-pesee", str(preuve), "--sha256-preuve", "0" * 64,
                        "--resume", "--sortie", str(complet),
                    ])
                self.assertEqual(code, 2)
                self.assertNotIn(str(preuve), sortie.getvalue())
                self.assertNotIn(str(preuve), complet.read_text())
                declaration = json.loads(sortie.getvalue())["declaration_pesee"]
                self.assertEqual(declaration["statut"], "NON VÉRIFIÉ")
                self.assertIn("déclaration", declaration["raison"].lower())

    def test_cli_resume_refuse_absence_de_rapport_et_ecrasement(self):
        """Une sortie courte sans rapport serait une perte de preuve (#35)."""
        from contextlib import redirect_stderr, redirect_stdout
        from io import StringIO

        from benchab.cli import main

        commande = [
            "--prive", str(self.racine), "controle", "consommation-retour",
            "--retour", "inconnu", "--resume",
        ]
        cible = self.racine / "rapports/precedent.json"
        cible.parent.mkdir(exist_ok=True)
        cible.write_text("précédent")
        for options, motif in (([], "exige --sortie"), (["--sortie", str(cible)], "Sortie existante")):
            with self.subTest(options=options):
                erreur, stdout = StringIO(), StringIO()
                with redirect_stderr(erreur), redirect_stdout(stdout):
                    self.assertEqual(main([*commande, *options]), 2)
                self.assertIn(motif, erreur.getvalue())
                self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(cible.read_text(), "précédent")

    def test_retour_interrompu_est_non_verifie_sans_masses_utilisables(self):
        """Un retour interrompu n'est jamais transformé en quantité imputable."""
        retour = json.loads(self.base.connexion.execute(
            "SELECT donnees_json FROM retours WHERE id=?", (self.retour_id,)
        ).fetchone()[0])
        retour["resultat"] = "arretee"
        with self.base.transaction() as connexion:
            connexion.execute("UPDATE retours SET donnees_json=? WHERE id=?", (json.dumps(retour), self.retour_id))
            connexion.execute("UPDATE impressions SET donnees_json=? WHERE id=?", (json.dumps(retour), self.retour_id))
            connexion.execute("UPDATE impressions SET resultat='interrompue' WHERE id=?", (self.retour_id,))

        resultat = estimer_retour(self.base, self.retour_id)

        self.assertEqual(resultat["statut"], "NON VÉRIFIÉ")
        self.assertIsNone(resultat["estimation"])
        self.assertIn("retour non réussi ou portée incomplète", resultat["motifs"])

    def test_envoi_sans_association_ne_fournit_pas_d_estimation(self):
        """Un envoi non rattaché à cette préparation ne fournit aucune estimation."""
        retour = json.loads(self.base.connexion.execute(
            "SELECT donnees_json FROM retours WHERE id=?", (self.retour_id,)
        ).fetchone()[0])
        retour["envois_identifies"][0]["sha256"] = "0" * 64
        with self.base.transaction() as connexion:
            connexion.execute("UPDATE retours SET donnees_json=? WHERE id=?", (json.dumps(retour), self.retour_id))
            connexion.execute("UPDATE impressions SET donnees_json=? WHERE id=?", (json.dumps(retour), self.retour_id))

        resultat = estimer_retour(self.base, self.retour_id)

        self.assertEqual(resultat["statut"], "NON VÉRIFIÉ")
        self.assertIsNone(resultat["estimation"])
        self.assertIn("association d’envoi absente ou ambiguë", resultat["motifs"])

    def test_plan_ouvre_une_fois_l_archive_associee(self):
        """L’inventaire valide le SHA dans le lecteur qui extrait les masses (#223)."""
        import os
        from unittest.mock import patch

        ouvrir = os.open
        ouvertures = []

        def compter(chemin, *args, **options):
            if isinstance(chemin, (str, Path)) and Path(chemin) == self.archive_prive:
                ouvertures.append(chemin)
            return ouvrir(chemin, *args, **options)

        with patch.object(os, "open", compter):
            resultat = estimer_retour(self.base, self.retour_id)
        self.assertEqual(len(ouvertures), 1)
        self.assertEqual(resultat["estimation"]["plaques"][0]["outils"][1]["grammes_estimes"], 12.5)

    def test_envoi_modifie_apres_association_ne_fournit_pas_d_estimation(self):
        """Un changement d'octets après l'association invalide son SHA enregistré."""
        chemin = self.racine / self.chemin_envoi
        chemin.write_bytes(b"archive modifiee")

        resultat = estimer_retour(self.base, self.retour_id)

        self.assertEqual(resultat["statut"], "NON VÉRIFIÉ")
        self.assertIsNone(resultat["estimation"])
        self.assertIn("envoi absent, ambigu ou empreinte divergente", resultat["motifs"])

    def test_masse_absente_sur_une_plaque_invalide_le_plan_entier(self):
        """Une plaque incomplète ne laisse pas passer une estimation partielle."""
        incomplet = self.racine / "envoi-incomplet.3mf"
        with zipfile.ZipFile(incomplet, "w") as archive:
            archive.writestr("Metadata/project_settings.config", json.dumps({"filament_ids": ["f1", "f2"]}))
            archive.writestr("Metadata/plate_1.gcode", "T1\nG1 X1 E1\n")
            archive.writestr("Metadata/plate_2.gcode", "; filament used [g] = 3.25, 0\nT0\nG1 X2 E1\n")
        nouveau_sha = hashlib.sha256(incomplet.read_bytes()).hexdigest()
        (self.racine / self.chemin_envoi).write_bytes(incomplet.read_bytes())
        with self.base.transaction() as connexion:
            association = json.loads(connexion.execute(
                "SELECT donnees_json FROM decisions WHERE id='association-fixture'"
            ).fetchone()[0])
            association["envoi"]["sha256"] = nouveau_sha
            connexion.execute(
                "UPDATE decisions SET donnees_json=? WHERE id='association-fixture'",
                (json.dumps(association),),
            )
            retour = json.loads(connexion.execute(
                "SELECT donnees_json FROM retours WHERE id=?", (self.retour_id,)
            ).fetchone()[0])
            retour["envois_identifies"][0]["sha256"] = nouveau_sha
            contenu = json.dumps(retour)
            connexion.execute("UPDATE retours SET donnees_json=? WHERE id=?", (contenu, self.retour_id))
            connexion.execute("UPDATE impressions SET donnees_json=? WHERE id=?", (contenu, self.retour_id))

        resultat = estimer_retour(self.base, self.retour_id)

        self.assertEqual(resultat["statut"], "NON VÉRIFIÉ")
        self.assertIsNone(resultat["estimation"])
        self.assertIn("masse ou identité d’une plaque absente", resultat["motifs"])

    def test_associations_dupliquees_restent_non_verifiees(self):
        """Deux associations compatibles ne permettent pas de choisir une source par intuition."""
        with self.base.transaction() as connexion:
            original = connexion.execute(
                "SELECT projet_id,date,texte,source,donnees_json FROM decisions WHERE id='association-fixture'"
            ).fetchone()
            connexion.execute(
                "INSERT INTO decisions(id,projet_id,date,texte,source,donnees_json) VALUES(?,?,?,?,?,?)",
                ("association-fixture-2", *tuple(original)),
            )

        resultat = estimer_retour(self.base, self.retour_id)

        self.assertEqual(resultat["statut"], "NON VÉRIFIÉ")
        self.assertIsNone(resultat["estimation"])
        self.assertIn("association d’envoi absente ou ambiguë", resultat["motifs"])

    def _remplacer_archive_et_sha(self, plaques):
        """Remplace le 3MF synthétique et relie sa nouvelle empreinte aux lignes de fixture."""
        source = self.racine / "remplacement.gcode.3mf"
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("Metadata/project_settings.config", json.dumps({"filament_ids": ["f1", "f2"]}))
            for nom, gcode in plaques.items():
                archive.writestr(nom, gcode)
        nouveau_sha = hashlib.sha256(source.read_bytes()).hexdigest()
        self.archive_prive.write_bytes(source.read_bytes())
        with self.base.transaction() as connexion:
            association = json.loads(connexion.execute(
                "SELECT donnees_json FROM decisions WHERE id='association-fixture'"
            ).fetchone()[0])
            association["envoi"]["sha256"] = nouveau_sha
            connexion.execute(
                "UPDATE decisions SET donnees_json=? WHERE id='association-fixture'",
                (json.dumps(association),),
            )
            retour = json.loads(connexion.execute(
                "SELECT donnees_json FROM retours WHERE id=?", (self.retour_id,)
            ).fetchone()[0])
            retour["envois_identifies"][0]["sha256"] = nouveau_sha
            contenu = json.dumps(retour)
            connexion.execute("UPDATE retours SET donnees_json=? WHERE id=?", (contenu, self.retour_id))
            connexion.execute("UPDATE impressions SET donnees_json=? WHERE id=?", (contenu, self.retour_id))
        return nouveau_sha

    def test_index_repere_par_masse_positive_meme_sans_commande_t(self):
        """Le champ décrit un repère dans l'envoi, jamais une sélection de T prouvée."""
        self._remplacer_archive_et_sha({
            "Metadata/plate_1.gcode": "; filament used [g] = 0, 12.5\nG1 X1 E1\n",
        })

        resultat = estimer_retour(self.base, self.retour_id)

        outil = resultat["estimation"]["plaques"][0]["outils"][1]
        self.assertEqual(outil["grammes_estimes"], 12.5)
        self.assertTrue(outil["indice_repere_dans_envoi"])
        self.assertNotIn("selectionne_dans_gcode", outil)

    def test_numero_de_plaque_zero_refuse_le_plan(self):
        """Une plaque doit commencer à l'index 1 ; l'index zéro reste NON VÉRIFIÉ."""
        self._remplacer_archive_et_sha({
            "Metadata/plate_0.gcode": "; filament used [g] = 12.5\nT0\nG1 X1 E1\n",
        })

        resultat = estimer_retour(self.base, self.retour_id)

        self.assertEqual(resultat["statut"], "NON VÉRIFIÉ")
        self.assertIsNone(resultat["estimation"])
        self.assertIn("numéro de plaque inférieur à 1", resultat["motifs"])

    def test_outil_hors_vecteur_de_masse_refuse_le_plan(self):
        """Un T hors du vecteur, même sans supposer un index réservé, bloque l'estimation."""
        self._remplacer_archive_et_sha({
            "Metadata/plate_1.gcode": "; filament used [g] = 0, 0\nT2\nG1 X1 E1\n",
        })

        resultat = estimer_retour(self.base, self.retour_id)

        self.assertEqual(resultat["statut"], "NON VÉRIFIÉ")
        self.assertIsNone(resultat["estimation"])
        self.assertIn("index d’outil hors du vecteur de masse", resultat["motifs"])

    def test_parcours_reel_envoi_et_retour_sans_sql_de_fixture(self):
        """Les APIs de préparation, d'envoi et de retour produisent un plan lisible."""
        import contextlib
        import io

        from benchab.fabrication.retour import main

        temporaire = tempfile.TemporaryDirectory()
        self.addCleanup(temporaire.cleanup)
        atelier = Path(temporaire.name).resolve()
        base = Base(atelier)
        self.addCleanup(base.fermer)
        projet = base.prive / "projets/recette-api"
        fiche = projet / "preparations/P01/PREPARATION.json"
        fiche.parent.mkdir(parents=True)
        lignes = b"T1\nG1 X1 Y1 E1\n"
        fiche.write_text(json.dumps({
            "id": "P01",
            "gcode": {"1": {
                "sha256": hashlib.sha256(lignes).hexdigest(),
                "commandes_sha256": PR.commandes(lignes),
            }},
        }), encoding="utf-8")
        enregistrer_preparation(base, fiche)
        source = atelier / "envoi.gcode.3mf"
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("Metadata/project_settings.config", json.dumps({"filament_ids": ["f1", "f2"]}))
            archive.writestr("Metadata/plate_1.gcode", "; filament used [g] = 0, 12.5\n" + lignes.decode())
            archive.writestr("Metadata/slice_info.config", b"""
                <config><plate><metadata key="index" value="1"/>
                <metadata key="prediction" value="100"/><metadata key="weight" value="12.5"/>
                </plate></config>""")
        envoi = enregistrer_envoi(base, projet, "P01", source)
        with contextlib.redirect_stdout(io.StringIO()) as affichage:
            code = main([
                "--projet", str(projet), "--id", "P01", "--resultat", "reussie",
                "--preuve-retour", "retour synthétique déclaré", "--ensemble-complet",
                "--envoi-identifie", envoi["fichier"], "--duree-observee-s", "125",
            ], base=base)
        self.assertEqual(code, 0)
        retour = json.loads(affichage.getvalue())

        resultat = estimer_retour(base, retour["retour"]["id"])

        self.assertEqual(resultat["statut"], "NON VÉRIFIÉ")
        self.assertEqual(resultat["estimation"]["plaques"][0]["outils"][1]["grammes_estimes"], 12.5)
        self.assertIsNone(resultat["affectation_bobine"])
        self.assertEqual(resultat["comparaison_duree"]["ecart_s"], 25)
        self.assertEqual(resultat["comparaison_duree"]["gcode"], "Metadata/plate_1.gcode")

    def test_json_de_retour_a_cles_dupliquees_reste_non_verifie(self):
        """Une déclaration JSON ambiguë ne fournit aucune estimation."""
        valeur = self.base.connexion.execute(
            "SELECT donnees_json FROM retours WHERE id=?", (self.retour_id,)
        ).fetchone()[0]
        chaine = valeur.replace('"id": "retour-fixture"', '"id":"autre","id":"retour-fixture"')
        self.assertNotEqual(chaine, valeur)
        with self.base.transaction() as connexion:
            connexion.execute("UPDATE retours SET donnees_json=? WHERE id=?", (chaine, self.retour_id))
            connexion.execute("UPDATE impressions SET donnees_json=? WHERE id=?", (chaine, self.retour_id))

        resultat = estimer_retour(self.base, self.retour_id)

        self.assertEqual(resultat["statut"], "NON VÉRIFIÉ")
        self.assertIsNone(resultat["estimation"])

    def test_erreur_sqlite_est_rendue_non_verifiee(self):
        """Une connexion indisponible produit un refus structuré, sans traceback applicatif."""
        self.base.fermer()

        resultat = estimer_retour(self.base, self.retour_id)

        self.assertEqual(resultat["statut"], "NON VÉRIFIÉ")
        self.assertIsNone(resultat["estimation"])


if __name__ == "__main__":
    unittest.main()
