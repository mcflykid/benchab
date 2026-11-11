"""Parcours : refus sûrs, reprise vérifiable et copie de préparation figée."""

import json
import shlex
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from benchab.organisation import automatisation as A


class Automatisation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / "root"
        self.p = self.root / "projets/test"
        self.prep = self.p / "preparations/P01"
        self.prep.mkdir(parents=True)
        self.out = Path(self.tmp.name).resolve() / "sortie"
        self.qualite = {
            "suivi": {
                "schema": 1,
                "type": "conception",
                "statut": "EN_PREPARATION",
                "preparation_courante": "P01",
                "physique": {"imprime": False, "resultat": "inconnu"},
            }
        }
        (self.p / "qualite.json").write_text(json.dumps(self.qualite))
        self.ctrl = self.prep / "controle.json"
        self.ctrl.write_text('{"statut":"PASS"}')
        self.d = {
            "id": "P01",
            "controles": {"preparations/P01/controle.json": {"sha256": A.sha(self.ctrl), "statuts": {"PASS": 1}}},
        }
        for k, n in [("plaques_3mf", "plaques.3mf"), ("tranche_3mf", "tranche.3mf")]:
            f = self.prep / n
            with zipfile.ZipFile(f, "w") as z:
                z.writestr("3D/3dmodel.model", "geometry")
            self.d[k] = {"fichier": "projets/test/preparations/P01/" + n, "sha256": A.sha(f)}
        self.man = self.prep / "PREPARATION.json"
        self.save()

    def save(self):
        self.man.write_text(json.dumps(self.d))

    def tearDown(self):
        self.tmp.cleanup()

    def snapshot(self):
        return {str(p.relative_to(self.root)): A.sha(p) for p in self.root.rglob("*") if p.is_file()}

    def test_plan_pure_et_preparation_absente(self):
        before = self.snapshot()
        self.assertEqual(A.planifier(self.root, "test")["statut"], "PLANIFIE")
        self.assertEqual(before, self.snapshot())
        self.qualite["suivi"].pop("preparation_courante")
        (self.p / "qualite.json").write_text(json.dumps(self.qualite))
        self.assertEqual(A.executer(self.root, "test", self.out)["statut"], "BLOQUE")
        self.assertFalse(self.out.exists())

    def test_diagnostic_detecte_lien_casse_et_recuperation(self):
        f = self.root / "README.md"
        f.write_text("[Source](absente.md)")
        with patch.object(A, "environnement", return_value={"statut": "PASS"}):
            d = A.diagnostiquer(self.root, "test")
            self.assertEqual(d["statut"], "BLOQUE")
            self.assertEqual(d["references"]["statut"], "FAIL")
            self.assertTrue(any(("absente.md" in x["cause"] for x in d["references"]["erreurs"])))
            f.write_text("[Source](projets/test/qualite.json)")
            d = A.diagnostiquer(self.root, "test")
            self.assertEqual(d["references"]["statut"], "PASS")
            self.assertEqual(d["statut"], "PASS")

    def test_commande_reference_relance_le_controle_sur_le_bon_atelier(self):
        """La commande du diagnostic relit le même atelier, même avec un espace."""
        atelier = self.root.with_name("atelier avec espaces")
        self.root.rename(atelier)
        (atelier / "README.md").write_text("[Source](absente.md)")
        with patch.object(A, "environnement", return_value={"statut": "PASS"}):
            diagnostic = A.diagnostiquer(atelier, "test")
        erreur = next(x for x in diagnostic["references"]["erreurs"] if "absente.md" in x["cause"])
        commande = shlex.split(erreur["action"])
        self.assertEqual(commande[:3], ["uv", "run", "python"])
        self.assertEqual(commande[commande.index("--root") + 1], str(atelier))
        # Réutiliser le Python du test sans demander à uv de refaire l’installation.
        execution = subprocess.run([sys.executable, *commande[3:]], capture_output=True, check=False)
        self.assertEqual(execution.returncode, 1, execution.stderr.decode())
        self.assertTrue(any("absente.md" in x for x in json.loads(execution.stdout)["errors"]))

    def test_document_json_trop_profond_refuse_proprement(self):
        f = self.root / "profond.json"
        f.write_text("[" * 10000 + "0" + "]" * 10000)
        with self.assertRaisesRegex(ValueError, "trop profond"):
            A.lire_json(f)

    def test_traversee_lien_et_archive_alteree_refuses(self):
        for nom in ("../test", "test/../test", "/test"):
            with self.assertRaises(ValueError):
                A.projet(self.root, nom)
        f = self.prep / "plaques.3mf"
        original = f.read_bytes()
        f.write_bytes(b"altered")
        with patch.object(A, "demander_ouverture") as run:
            self.assertEqual(A.ouvrir_slicer(self.root, "test", self.out, ouvrir=True)["statut"], "FAIL")
            run.assert_not_called()
        f.write_bytes(original)
        other = self.prep / "other.3mf"
        f.rename(other)
        f.symlink_to(other)
        with self.assertRaises(ValueError):
            A.controle_preparation(self.root, "test")

    def test_controle_fail_refuse_ouvrir(self):
        self.ctrl.write_text('{"statut":"FAIL"}')
        self.d["controles"]["preparations/P01/controle.json"]["sha256"] = A.sha(self.ctrl)
        self.save()
        with patch.object(A, "demander_ouverture") as run:
            self.assertEqual(A.ouvrir_slicer(self.root, "test", self.out, ouvrir=True)["statut"], "FAIL")
            run.assert_not_called()

    def test_ouverture_copie_preserve_original(self):
        before = self.snapshot()
        with (
            patch.object(A, "informations", return_value={"present": True}),
            patch.object(A, "demander_ouverture") as run,
        ):
            run.return_value = {"code": 0}
            result = A.ouvrir_slicer(self.root, "test", self.out, ouvrir=True)
        copy = Path(result["copie"])
        self.assertTrue(copy.is_file())
        self.assertFalse(copy.is_relative_to(self.root))
        self.assertEqual(before, self.snapshot())
        self.assertEqual(A.sha(copy), self.d["tranche_3mf"]["sha256"])

    def vues(self, root, nom, sortie, ident):
        sortie.mkdir()
        (sortie / "piece.html").write_text("<h1>Vue réelle fixture</h1>")
        return {"statut": "PASS", "vues": [{"statut": "DISPONIBLE"}]}

    def execute(self):
        with (
            patch.object(A, "environnement", return_value={"statut": "WARN"}),
            patch("benchab.organisation.synthese_atelier.bilan", return_value={"statut": "PASS"}),
            patch("benchab.organisation.synthese_atelier.connaissances", return_value={"statut": "PASS"}),
            patch("benchab.organisation.synthese_atelier.generer_vues", side_effect=self.vues),
        ):
            return A.executer(self.root, "test", self.out)

    def test_reprise_sortie_source_et_outil_invalident(self):
        state = self.execute()
        self.assertEqual(state["statut"], "PASS")
        self.assertTrue(all((x["etat"] == "REUTILISABLE" for x in A.reprise(self.root, "test", self.out)["etapes"])))
        (self.out / "resume.json").write_text("{}")
        etapes = {x["id"]: x for x in A.reprise(self.root, "test", self.out)["etapes"]}
        self.assertEqual(etapes["resume"]["etat"], "A_REFAIRE")
        self.assertEqual(etapes["cache"]["etat"], "REUTILISABLE")
        self.execute()
        (self.p / "BESOIN.md").write_text("nouvelle demande")
        self.assertTrue(all((x["etat"] == "A_REFAIRE" for x in A.reprise(self.root, "test", self.out)["etapes"])))
        state = self.execute()
        sig = state["etapes"]["resume"]["entrees"]
        new = {**sig, "outil:test": "different"}
        self.assertIn(
            "entrées, paramètres ou outils modifiés", A.raison_cache(state["etapes"]["resume"], new, self.out)
        )

    def test_interruption_et_verrou_non_force(self):
        with patch.object(A, "environnement", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                A.executer(self.root, "test", self.out)
        state = json.loads((self.out / "execution.json").read_text())
        self.assertEqual(state["etapes"]["environnement"]["statut"], "INTERROMPU")
        with A.verrou_execution(self.out):
            with self.assertRaises(ValueError):
                with A.verrou_execution(self.out):
                    pass
        self.assertEqual(self.execute()["statut"], "PASS")

    def test_sortie_dans_projet_refusee(self):
        with self.assertRaises(ValueError):
            A.dossier_sortie(self.root, self.p / "rapport")

    def test_impact_test_connu_et_repli_complet(self):
        folder = self.root / "outils/test"
        folder.mkdir(parents=True)
        (folder / "test_exemple.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n def test_ok(self):self.assertTrue(True)\n"
        )
        t = "outils/test/test_exemple.py"
        self.assertEqual(A.tests_impact(self.root, [t])["mode"], "CIBLE")
        plan = A.tests_impact(self.root, ["inconnu.py"])
        self.assertEqual(plan["mode"], "COMPLET")
        self.assertIn(t, plan["tests"])

    def test_cache_consultation_sans_journal(self):
        d = self.p / "organisation"
        d.mkdir()
        f = self.p / "source"
        f.write_text("x")
        (d / "cache.json").write_text(
            json.dumps(
                {
                    "k": {
                        "entrees": {"source": A.sha(f)},
                        "dependances": [],
                        "outils": {},
                        "hypotheses": ["sécheresse non mesurée"],
                        "invalide_si": ["humide"],
                    }
                }
            )
        )
        before = self.snapshot()
        data = A.cache_projet(self.root, "test")
        self.assertEqual(data["entrees"]["k"]["statut"], "VALIDE")
        self.assertEqual(before, self.snapshot())
        f.write_text("changed")
        self.assertEqual(A.cache_projet(self.root, "test")["entrees"]["k"]["statut"], "INVALIDE")

    def test_conforme_false_contredit_metadata_pass(self):
        self.ctrl.write_text('{"conforme":false}')
        self.d["controles"]["preparations/P01/controle.json"]["sha256"] = A.sha(self.ctrl)
        self.save()
        self.assertEqual(A.controle_preparation(self.root, "test")["statut"], "FAIL")

    def test_reprise_etat_symbolique_et_malforme_refuses(self):
        self.out.mkdir()
        f = self.out / "execution.json"
        f.symlink_to(self.man)
        with self.assertRaises(ValueError):
            A.reprise(self.root, "test", self.out)
        f.unlink()
        f.write_text("[]")
        with self.assertRaises(ValueError):
            A.reprise(self.root, "test", self.out)

    def test_parent_substitue_ne_cree_pas_verrou_protege(self):
        self.out.mkdir()
        self.out.rmdir()
        self.out.symlink_to(self.prep)
        with self.assertRaises((ValueError, OSError)):
            with A.verrou_execution(self.out):
                pass
        self.assertFalse((self.prep / ".execution.lock").exists())

    def test_placement_et_manifeste_invalident(self):
        self.execute()
        c = self.p / "candidats"
        c.mkdir()
        (c / "assemblage.json").write_text("{}")
        self.assertTrue(all((x["etat"] == "A_REFAIRE" for x in A.reprise(self.root, "test", self.out)["etapes"])))
        self.execute()
        (self.p / "preuves").mkdir()
        (self.p / "preuves/placement.json").write_text("{}")
        self.assertTrue(all((x["etat"] == "A_REFAIRE" for x in A.reprise(self.root, "test", self.out)["etapes"])))

    def test_preparer_absence_inputs_n_execute_rien(self):
        with patch.object(A, "demander_ouverture") as run:
            self.assertEqual(A.creer_preparation(self.root, "test", self.out, True)["statut"], "BLOQUE")
            run.assert_not_called()

    def test_ouverture_delai_issue_inconnue_sans_relance(self):
        with (
            patch.object(A, "informations", return_value={"present": True}),
            patch.object(A, "demander_ouverture", side_effect=A.subprocess.TimeoutExpired("slicer", 20)) as run,
        ):
            result = A.ouvrir_slicer(self.root, "test", self.out, ouvrir=True)
        self.assertEqual(result["ouverture"], "INCONNUE")
        self.assertEqual(run.call_count, 1)

    def test_code_cli_ouverture_inconnue(self):
        """Le délai simulé rend le code 2 et un rapport INCONNU, sans ouvrir le slicer."""
        import contextlib
        import io

        sortie = io.StringIO()
        with (
            patch.object(A, "informations", return_value={"present": True}),
            patch.object(A, "demander_ouverture", side_effect=A.subprocess.TimeoutExpired("slicer", 20)),
            contextlib.redirect_stdout(sortie),
        ):
            code = A.main(["ouvrir-slicer", "--projet", "test", "--ouvrir", "--sortie", str(self.out)],
                          prive=self.root)
        rapport = json.loads(sortie.getvalue())
        self.assertEqual(rapport["statut"], "INCONNU")
        self.assertEqual(code, 2)
        self.assertEqual(rapport["code"], code)

    def test_environnement_reel_ne_modifie_pas_projet(self):
        before = self.snapshot()
        result = A.environnement(self.root)
        self.assertIn(result["statut"], ("PASS", "WARN"))
        self.assertEqual(before, self.snapshot())


if __name__ == "__main__":
    unittest.main()


def test_source_connaissance_sqlite_hors_projet_invalide_signature(tmp_path):
    """Une preuve modifiée/absente invalide le cache même sans registre JSON ; aucune écriture Source."""
    from benchab.donnees.base import Base

    root = tmp_path / "prive"
    base = Base(root)
    try:
        p = root / "projets/fixture"
        p.mkdir(parents=True)
        f = p / "PREPARATION.json"
        f.write_text("{}")
        preuve = root / "preuve.md"
        preuve.write_text("avant")
        base.ecrire("INSERT INTO connaissances VALUES (?,?,?,?,?,?,?)", (
            "fixture", "Savoir synthétique", "numerique", "fixture", None, 0,
            json.dumps({"sources": [{"chemin": "preuve.md", "sha256": A.sha(preuve)}]}),
        ))
        with patch.object(A, "preparation", return_value=(p, {"suivi": {}}, f, {})):
            avant = A.empreintes(root, "fixture", "P01")
            preuve.write_text("après")
            apres = A.empreintes(root, "fixture", "P01")
            preuve.unlink()
            absent = A.empreintes(root, "fixture", "P01")
        assert avant != apres != absent
        assert avant["connaissances:canoniques"] == apres["connaissances:canoniques"]
        assert absent["connaissance-source:preuve.md"] == "ABSENT"
        assert not (root / "donnees/connaissances/registre.json").exists()
    finally:
        base.fermer()
