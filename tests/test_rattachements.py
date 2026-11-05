"""F2 : aucune source exécutée, aucune fiche historique réécrite."""

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchab.fabrication import rattachements as L


class Rattachements(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.r = Path(self.tmp.name)
        self.p = self.r / "projet"
        self.p.mkdir()
        for d in ("src", "reference/v1", "preparations/P01"):
            (self.p / d).mkdir(parents=True)
        (self.p / "src/a.py").write_text("raise RuntimeError('ne doit jamais être exécuté')\n")
        self.h = L.sha(self.p / "src/a.py")
        (self.p / "reference/v1/a.txt").write_text("référence figée")
        (self.p / "reference/v1/EMPREINTES.txt").write_text(L.sha(self.p / "reference/v1/a.txt") + "  ./a.txt\n")
        self.fiche = {
            "id": "P01",
            "statut": "préparation candidate — NON imprimée",
            "envoi": None,
            "retour_physique": None,
            "demande": {},
            "sources": {"src/a.py": self.h},
            "modeles": {},
            "controles": {},
        }
        for k, n in (("plaques_3mf", "plaques.3mf"), ("tranche_3mf", "tranche.3mf")):
            f = self.p / "preparations/P01" / n
            f.write_bytes(b"archive fictive, hachage seulement")
            self.fiche[k] = {"fichier": str(f), "sha256": L.sha(f)}
        self.meta = self.p / "preparations/P01/PREPARATION.json"
        self.meta.write_text(json.dumps(self.fiche))
        self.index = {
            "schema": 1,
            "versions": {
                "V1": {
                    "role": "reference_historique",
                    "dossier": "reference/v1",
                    "reproductibilite": "À VÉRIFIER",
                    "manifeste": {
                        "chemin": "reference/v1/EMPREINTES.txt",
                        "sha256": L.sha(self.p / "reference/v1/EMPREINTES.txt"),
                    },
                },
                "V2": {"role": "en_developpement", "dossier": "src", "reproductibilite": "À VÉRIFIER"},
            },
            "preparations": {"P01": {"version": "V2", "sha256": L.sha(self.meta)}},
            "preparation_courante": "P01",
            "emplacements": {self.h: {"type": "projet", "chemin": "src/a.py"}},
            "lacunes": {},
        }

    def ecrire(self):
        (self.p / "reference/RATTACHEMENTS.json").write_text(json.dumps(self.index))

    def verifier(self):
        self.ecrire()
        return L.verifier(self.p, self.r)

    def fail(self):
        r = self.verifier()
        self.assertEqual(r["statut"], "FAIL", r)
        self.assertTrue(r["erreurs"])
        return r

    def test_valide_lecture_seule_et_aucune_execution(self):
        self.fiche["controles"] = {"contrôle fictif": {"sha256": self.h, "statuts": {"FAIL": 1}}}
        self.meta.write_text(json.dumps(self.fiche))
        self.index["preparations"]["P01"]["sha256"] = L.sha(self.meta)
        self.ecrire()
        avant = {str(p): L.sha(p) for p in self.p.rglob("*") if p.is_file()}
        r = L.verifier(self.p, self.r)
        self.assertEqual(r["statut"], "PASS")
        self.assertEqual(r["reproductibilite"], "À VÉRIFIER")
        self.assertEqual(avant, {str(p): L.sha(p) for p in self.p.rglob("*") if p.is_file()})
        self.assertEqual(r["preparations"]["P01"]["statut_fiche"], self.fiche["statut"])
        self.assertEqual(r["preparations"]["P01"]["statuts_controles"]["contrôle fictif"], {"FAIL": 1})

    def test_source_modifiee_relecture_a_chaque_appel(self):
        self.assertEqual(self.verifier()["statut"], "PASS")
        (self.p / "src/a.py").write_text("différent")
        self.fail()

    def test_lacune_explicite_warn_et_jamais_reproductible(self):
        self.index["emplacements"] = {}
        self.index["lacunes"] = {self.h: "Non retrouvé lors de F1"}
        r = self.verifier()
        self.assertEqual(r["statut"], "WARN")
        self.assertEqual(len(r["lacunes"]), 1)
        self.assertEqual(r["reproductibilite"], "À VÉRIFIER")

    def test_absence_non_declaree_et_contradiction_refusees(self):
        old = copy.deepcopy(self.index)
        self.index["emplacements"] = {}
        self.fail()
        self.index = old
        self.index["lacunes"] = {self.h: "manquant"}
        self.fail()

    def test_fiche_archive_et_reference_modifiees_refusees(self):
        for rel in (
            "preparations/P01/PREPARATION.json",
            "preparations/P01/tranche.3mf",
            "reference/v1/a.txt",
            "reference/v1/EMPREINTES.txt",
        ):
            with self.subTest(rel=rel):
                f = self.p / rel
                data = f.read_bytes()
                f.write_bytes(data + b"X")
                self.fail()
                f.write_bytes(data)

    def test_versions_preparations_et_courante_distinctes(self):
        for champ, valeur in (("version", "P01"), ("version", "V9")):
            with self.subTest(valeur=valeur):
                old = self.index["preparations"]["P01"][champ]
                self.index["preparations"]["P01"][champ] = valeur
                self.fail()
                self.index["preparations"]["P01"][champ] = old
        self.index["preparation_courante"] = "P09"
        self.fail()

    def test_nouvelle_preparation_non_indexee_refusee(self):
        (self.p / "preparations/P02").mkdir()
        (self.p / "preparations/P02/PREPARATION.json").write_text("{}")
        self.fail()

    def test_depot_et_git_verifies_par_octets(self):
        (self.r / "copie.py").write_bytes((self.p / "src/a.py").read_bytes())
        self.index["emplacements"][self.h] = {"type": "depot", "chemin": "copie.py"}
        self.assertEqual(self.verifier()["statut"], "PASS")
        self.index["emplacements"][self.h] = {"type": "git", "commit": "a" * 40, "chemin": "src/a.py"}
        with patch.object(
            L.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0, (self.p / "src/a.py").read_bytes(), b""),
        ) as m:
            self.assertEqual(self.verifier()["statut"], "PASS")
            self.assertEqual(m.call_args.args[0][:3], ["git", "cat-file", "blob"])
        with patch.object(L.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"mauvais", b"")):
            self.fail()

    def test_chemins_types_et_commit_ambigus_refuses(self):
        for loc in (
            {"type": "projet", "chemin": "../copie.py"},
            {"type": "projet", "chemin": str(self.p / "src/a.py")},
            {"type": "depot", "chemin": "../secret"},
            {"type": "magique", "chemin": "src/a.py"},
            {"type": "git", "chemin": "src/a.py", "commit": "main"},
        ):
            with self.subTest(loc=loc):
                self.index["emplacements"][self.h] = loc
                self.fail()

    def test_lien_symbolique_hors_perimetre_refuse(self):
        (self.r / "dehors.py").write_text("x")
        (self.p / "lien.py").symlink_to(self.r / "dehors.py")
        self.index["emplacements"][self.h] = {"type": "projet", "chemin": "lien.py"}
        self.fail()

    def test_schemas_inconnus_et_faux_statut_refuses(self):
        original = copy.deepcopy(self.index)
        for cle, val in (
            ("schema", 99),
            ("schema", True),
            ("versions", []),
            ("preparations", None),
            ("emplacements", None),
            ("lacunes", []),
        ):
            self.index = copy.deepcopy(original)
            self.index[cle] = val
            self.fail()
        self.index = original
        self.index["versions"]["V2"]["reproductibilite"] = "REPRODUCTIBLE"
        self.fail()

    def test_json_illisible_et_index_absent_refuses(self):
        self.assertEqual(L.verifier(self.p, self.r)["statut"], "FAIL")
        (self.p / "reference/RATTACHEMENTS.json").write_text("{cassé")
        self.assertEqual(L.verifier(self.p, self.r)["statut"], "FAIL")

    def test_cli_warn_n_est_pas_succes(self):
        self.index["emplacements"] = {}
        self.index["lacunes"] = {self.h: "inconnu"}
        self.ecrire()
        from contextlib import redirect_stdout
        from io import StringIO

        sortie = StringIO()
        with redirect_stdout(sortie):
            code = L.main(["--projet", str(self.p), "--depot", str(self.r)])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(sortie.getvalue())["statut"], "WARN")

    def test_environnement_modifie_refuse(self):
        f = self.meta.parent / "environnement.json"
        f.write_text('{"python":"test"}')
        self.index["preparations"]["P01"]["environnement_sha256"] = L.sha(f)
        self.assertEqual(self.verifier()["statut"], "PASS")
        f.write_text('{"python":"autre"}')
        self.fail()

    def test_cli_projet_requis(self):
        from contextlib import redirect_stdout
        from io import StringIO

        sortie = StringIO()
        with redirect_stdout(sortie):
            code = L.main([])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(sortie.getvalue())["statut"], "FAIL")


if __name__ == "__main__":
    unittest.main()
