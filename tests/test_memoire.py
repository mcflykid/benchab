import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchab.connaissances import memoire as M


class Memoire(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.r = Path(self.tmp.name).resolve()
        (self.r / "donnees/connaissances").mkdir(parents=True)
        (self.r / "suivi/journal").mkdir(parents=True)
        self.item = {
            "id": "K1",
            "titre": "jeu eSUN",
            "description": "0,3 glissant",
            "domaine": "ajustements",
            "conditions": "traversant vertical 10 mm",
            "exclusions": "pas Ø5 borgne",
            "niveau_preuve": "MESURE_PHYSIQUE",
            "sources": [{"chemin": "suivi/journal/source.md"}],
            "validite": "contextuelle",
            "filament": ["eSUN os"],
            "geometrie": "traversant vertical 10 mm",
            "comparabilite": "10mm",
            "faits": {"jeu": 0.3},
        }
        (self.r / "suivi/journal/source.md").write_text("# Éprouvette\nmesure utilisateur")
        self.write([self.item])

    def write(self, items):
        (self.r / "donnees/connaissances/registre.json").write_text(
            json.dumps({"connaissances": items, "journaux": {}})
        )

    def test_recherche_filtrage_et_limites(self):
        r = M.chercher(self.r, "", {"filament": "esun", "geometrie": "traversant"})
        self.assertEqual(len(r), 1)
        self.assertIn("borgne", r[0]["exclusions"])
        self.assertEqual(M.chercher(self.r, "", {"geometrie": "couchee"}), [])

    def test_journal_date_type_numero_sans_ecrasement(self):
        a = M.creer_journal(self.r, "impression", "test", date="2026-10-31")
        before = a.read_bytes()
        b = M.creer_journal(self.r, "impression", "test", date="2026-10-31")
        self.assertNotEqual(a, b)
        self.assertEqual(a.read_bytes(), before)
        for value in ("../x", "Étrange", "final v2"):
            with self.assertRaises(ValueError):
                M.creer_journal(self.r, "impression", value)
        with self.assertRaises(ValueError):
            M.creer_journal(self.r, "impression", "test", date="2026-02-31")

    def test_capitalisation_ne_devient_pas_validation_physique(self):
        before = (self.r / "donnees/connaissances/registre.json").read_bytes()
        records, proposals = M.analyser_journaux(self.r)
        self.assertEqual(before, (self.r / "donnees/connaissances/registre.json").read_bytes())
        self.assertEqual(records["suivi/journal/source.md"]["statut"], "ACTIF")
        self.assertTrue(proposals)
        data = M.charger(self.r)
        data["journaux"] = records
        data["journaux"]["suivi/journal/source.md"]["qualification"] = "source relue, limites conservées"
        (self.r / "donnees/connaissances/registre.json").write_text(json.dumps(data))
        updated, _ = M.analyser_journaux(self.r)
        self.assertEqual(updated["suivi/journal/source.md"]["qualification"], "source relue, limites conservées")

    def test_resultat_explicitement_declare_ne_prouve_pas_resistance(self):
        p = M.creer_journal(self.r, "impression", "plaque")
        p.write_text(p.read_text() + "\n| 1 | réussie | | |\n")
        records, proposals = M.analyser_journaux(self.r)
        rel = p.relative_to(self.r).as_posix()
        self.assertEqual(records[rel]["resultats_declares"][0]["niveau_preuve"], "OBSERVATION_UTILISATEUR")
        self.assertNotIn("resistance", json.dumps(records[rel]))
        self.assertEqual(records[rel]["statut"], "À EXAMINER")

    def test_contradictions_seulement_contexte_comparable(self):
        second = dict(self.item, id="K2", faits={"jeu": 0.4})
        self.write([self.item, second])
        self.assertEqual(len(M.contradictions(self.r)), 1)
        second["comparabilite"] = "5mm"
        self.write([self.item, second])
        self.assertEqual(M.contradictions(self.r), [])

    def test_provenance_absente_ou_perimee_refusee(self):
        self.assertEqual(M.verifier(self.r), [])
        (self.r / "suivi/journal/source.md").unlink()
        self.assertTrue(M.verifier(self.r))

    def test_journal_mensuel_et_parent_symbolique_refuse(self):
        p = M.creer_journal(self.r, "audit", "organisation", date="2026-10-31")
        self.assertEqual(p.relative_to(self.r).as_posix(), "suivi/journal/2026-10/2026-10-31-audit-organisation.md")
        self.assertFalse((self.r / "journal").exists())
        (self.r / "suivi/journal/2026-11").symlink_to(self.r / "suivi/journal/2026-10", target_is_directory=True)
        with self.assertRaises(ValueError):
            M.creer_journal(self.r, "audit", "organisation", date="2026-11-01")

    def test_membre_archive_empreinte_obligatoire(self):
        import hashlib
        import io
        import tarfile

        p = self.r / "source.tar.gz"
        with tarfile.open(p, "w:gz") as t:
            member = tarfile.TarInfo("source.md")
            member.size = 4
            t.addfile(member, io.BytesIO(b"fait"))
        self.item["sources"] = [
            {"chemin": "source.tar.gz", "membre": "source.md", "sha256_membre": hashlib.sha256(b"fait").hexdigest()}
        ]
        self.write([self.item])
        self.assertEqual(M.verifier(self.r), [])
        self.item["sources"][0]["sha256_membre"] = "0" * 64
        self.write([self.item])
        self.assertTrue(M.verifier(self.r))

    def test_blob_git_verifie_sha_exact_sans_configuration_globale(self):
        """Vérifie le blob par SHA dans un dépôt jetable ; aucune configuration Git globale modifiée."""
        fichier = self.r / "suivi/journal/source.md"
        contenu = fichier.read_bytes()
        env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_OPTIONAL_LOCKS="0")
        subprocess.run(["git", "init", "-q", str(self.r)], check=True, env=env)
        subprocess.run(["git", "-C", str(self.r), "config", "user.name", "Fixture"], check=True, env=env)
        # L'identité synthétique n'existe que dans le dépôt jetable ; frontière publique sans adresse littérale
        # (docs/decisions/2026-11-04-frontiere-public-prive.md).
        subprocess.run(
            ["git", "-C", str(self.r), "config", "user.email", "fixture" + "@" + "example.invalid"],
            check=True, env=env,
        )
        subprocess.run(["git", "-C", str(self.r), "add", "suivi/journal/source.md"], check=True, env=env)
        subprocess.run(["git", "-C", str(self.r), "commit", "-q", "-m", "fixture"], check=True, env=env)
        revision = subprocess.run(
            ["git", "-C", str(self.r), "rev-parse", "HEAD"], capture_output=True, text=True, check=True, env=env
        ).stdout.strip()
        source = {
            "chemin": "suivi/journal/source.md", "revision_originale": revision,
            "sha256": hashlib.sha256(contenu).hexdigest(),
        }
        erreurs, nv = [], []
        M._verifier_fichier_source(self.r, "K1", source, erreurs, nv)
        self.assertEqual(erreurs, [])
        self.assertEqual(nv, [])

        source["sha256"] = "0" * 64
        erreurs, nv = [], []
        M._verifier_fichier_source(self.r, "K1", source, erreurs, nv)
        self.assertTrue(any("blob Git modifié" in erreur for erreur in erreurs))

        source.pop("sha256")
        erreurs, nv = [], []
        M._verifier_fichier_source(self.r, "K1", source, erreurs, nv)
        self.assertEqual(erreurs, [])
        self.assertTrue(any("sans SHA de contenu" in note for note in nv))

        with patch.object(M, "MAX_BLOB_GIT_OCTETS", 4):
            erreurs, nv = [], []
            M._verifier_fichier_source(self.r, "K1", source, erreurs, nv)
        self.assertTrue(any("dépasse la limite" in erreur for erreur in erreurs))


if __name__ == "__main__":
    unittest.main()
