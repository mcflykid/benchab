"""Régressions du contrôle documentaire et de la syntaxe privée, sans exécution."""

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from benchab.donnees.base import Base
from benchab.qualite import references as checker


class ReferenceChecks(unittest.TestCase):
    """Vérifie le contrôle de liens, d'historique et de scripts privés sur des fixtures."""

    def setUp(self):
        """Isole les fixtures dans un dossier temporaire supprimé après chaque test."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, rel, content):
        """Écrit une fixture de test sous la racine temporaire, en octets ou texte."""
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)
        return path

    def test_generic_links_and_syntax(self):
        """Compte les liens locaux et analyse Python et JSON sans exécuter les sources."""
        self.write("a (b).md", "# Valid")
        self.write("valid.json", "{}")
        self.write("valid.py", 'raise RuntimeError("must not run")')
        self.write(
            "README.md",
            """[space](<a%20(b).md> \"Title\")
[paren](a%20(b).md)
[web](https://example.invalid/missing)
[anchor](#missing)
`[example](missing)`
```md
[example](missing)
```
""",
        )
        report = checker.verify(self.root, historical=False, cache=False)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["checks"]["local_links"], 2)
        self.assertEqual(report["checks"]["json"], 1)
        self.assertEqual(report["checks"]["python"], 1)

    def test_broken_inputs_reported_together(self):
        """Vérifie qu’un seul rapport réunit les trois erreurs de fixture."""
        self.write("bad.json", "{")
        self.write("bad.py", "if")
        self.write("README.md", "[missing](absent.md)")
        report = checker.verify(self.root, historical=False, cache=False)
        self.assertFalse(report["ok"])
        self.assertEqual(len(report["errors"]), 3)

    def test_project_template_three_doc_links_in_destination_context(self):
        """Résout les trois liens documentaires du gabarit après déplacement."""
        rel = "outils/gabarits/projet-commun/README.md"
        # Le gabarit source n'est pas une dépendance de l'exécution native : cette
        # fixture reproduit ses trois liens de documentation dans le même contexte.
        text = """[cycle](../../docs/guides/cycle-projet.md)
[besoin](BESOIN.md)
[contrôles](../../docs/reference/outils/controles/README.md#revue-locale-avant-envoi)
[références](../../docs/guides/references-et-imports.md)
"""
        links = [target for target in checker.markdown_targets(text) if target.startswith("../../docs/")]
        self.assertEqual(len(links), 3)
        self.write(rel, text)
        self.write("outils/gabarits/projet-commun/BESOIN.md", "# Besoin")
        self.write("docs/guides/cycle-projet.md", "# Cycle de vie")
        self.write("docs/guides/references-et-imports.md", "# Références")
        self.write("docs/reference/outils/controles/README.md", "# Revue locale avant envoi")
        report = checker.verify(self.root, historical=False, anchors=True, cache=False)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["checks"]["local_links"], 4)

    def test_project_template_missing_destination_doc_not_ignored(self):
        """Refuse un lien dont la cible manque dans le contexte de destination."""
        rel = "outils/gabarits/projet-commun/README.md"
        self.write(rel, "[missing](../../docs/absent.md)")
        self.write("outils/docs/absent.md", "# Wrong context")
        report = checker.verify(self.root, historical=False, cache=False)
        self.assertEqual(report["errors"], [f"Link {rel} -> ../../docs/absent.md"])

    def test_project_template_missing_destination_anchor_not_ignored(self):
        """Refuse une ancre absente même lorsque son fichier est présent."""
        rel = "outils/gabarits/projet-commun/README.md"
        self.write(rel, "[missing](../../docs/guide.md#missing)")
        self.write("docs/guide.md", "# Present")
        report = checker.verify(self.root, historical=False, anchors=True, cache=False)
        self.assertEqual(report["errors"], [f"Anchor {rel} -> ../../docs/guide.md#missing"])

    def test_other_template_doc_links_keep_normal_context(self):
        """Vérifie que l’exception du gabarit commun ne s’étend pas aux autres."""
        rel = "outils/gabarits/autre/README.md"
        self.write(rel, "[normal](../../docs/guide.md)")
        self.write("docs/guide.md", "# Different context")
        report = checker.verify(self.root, historical=False, cache=False)
        self.assertEqual(report["errors"], [f"Link {rel} -> ../../docs/guide.md"])

    def test_dependencies_are_not_project_sources(self):
        """Vérifie que les dépendances vendues ne comptent pas comme sources du projet."""
        self.write("outils/.venv-dfam/lib/site-packages/vendor.py", "invalid python !")
        self.write("outils/node_modules/vendor/README.md", "[missing](absent.md)")
        self.write("own.py", "x = 1")
        report = checker.verify(self.root, historical=False, cache=False)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["checks"]["python"], 1)
        self.assertTrue(any("node_modules" in note for note in report["notes"]))

    def test_snapshot_links_preserved_but_current_links_checked(self):
        """Contrôle les liens courants sans imposer de réécrire les archives."""
        self.write("recherches/sources-code/SKILL.md", "[external fragment](missing.md)")
        self.write("sauvegardes/before/README.md", "[old](missing.md)")
        self.write("README.md", "[current](missing.md)")
        report = checker.verify(self.root, historical=False, cache=False)
        self.assertEqual(report["errors"], ["Link README.md -> missing.md"])

    def test_historical_change_needs_exact_backup(self):
        """Refuse la perte d’un original si la sauvegarde exacte manque."""
        self.write("new/item.txt", "changed")
        old_hash = hashlib.sha256(b"original").hexdigest()
        inventory = [{"path": "old/item.txt", "sha256": old_hash}]
        self.write(checker.HISTORY + "/inventaire-avant.json", json.dumps(inventory))
        self.write(checker.HISTORY + "/deplacements.json", json.dumps([{"avant": "old", "apres": "new"}]))
        self.assertFalse(checker.verify(self.root, cache=False)["ok"])
        self.write("sauvegardes/2026-10-15-organisation/old/item.txt", "original")
        report = checker.verify(self.root, cache=False)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["checks"]["changed_originals_backed_up"], 1)

    def test_missing_original_does_not_crash(self):
        """Exige un diagnostic lisible quand la référence historique manque."""
        self.write(checker.HISTORY + "/inventaire-avant.json", json.dumps([{"path": "missing", "sha256": "0" * 64}]))
        self.write(checker.HISTORY + "/deplacements.json", "[]")
        self.assertIn("Missing original: missing", checker.verify(self.root, cache=False)["errors"])

    def test_exact_original_in_later_dated_backup(self):
        """Accepte une sauvegarde ultérieure seulement si le SHA correspond."""
        self.write("README.md", "updated")
        digest = hashlib.sha256(b"original").hexdigest()
        inventory = [{"path": "README.md", "sha256": digest}]
        self.write(checker.HISTORY + "/inventaire-avant.json", json.dumps(inventory))
        self.write(checker.HISTORY + "/deplacements.json", "[]")
        self.write("sauvegardes/2026-10-16-organisation/README.md", "wrong content")
        self.assertFalse(checker.verify(self.root, cache=False)["ok"])
        self.write("sauvegardes/2026-10-16-organisation/README.md", "original")
        report = checker.verify(self.root, cache=False)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["checks"]["changed_originals_backed_up"], 1)

    def test_optional_and_malformed_history(self):
        """Refuse l’historique invalide si le contrôle historique est demandé."""
        self.write(checker.HISTORY + "/inventaire-avant.json", "{")
        self.write(checker.HISTORY + "/deplacements.json", "[]")
        self.assertFalse(checker.verify(self.root, cache=False)["ok"])
        report = checker.verify(self.root, historical=False, cache=False)
        self.assertEqual(len(report["errors"]), 1)

    def test_missing_finder_metadata_is_note_not_missing_document(self):
        """Distingue une métadonnée Finder absente d’un fichier métier manquant."""
        self.write(
            checker.HISTORY + "/inventaire-avant.json",
            json.dumps(
                [
                    {"path": "folder/.DS_Store", "sha256": "0" * 64},
                    {"path": "important.3mf", "sha256": "0" * 64},
                ]
            ),
        )
        self.write(checker.HISTORY + "/deplacements.json", "[]")
        report = checker.verify(self.root, cache=False)
        self.assertEqual(report["errors"], ["Missing original: important.3mf"])
        self.assertIn("Finder metadata absent: folder/.DS_Store", report["notes"])

    def base(self):
        """Crée une base explicitement privée limitée au répertoire temporaire du test."""
        base = Base(self.root / "atelier")
        self.addCleanup(base.fermer)
        (base.prive / "projets" / "fixture").mkdir(parents=True)
        return base

    def test_private_python_syntax_is_static_and_creates_no_pyc(self):
        """Exige PASS pour la syntaxe valide sans exécution ni cache Python."""
        base = self.base()
        script = base.prive / "projets/fixture/main.py"
        script.write_text('raise RuntimeError("ne doit pas être exécuté")\n')
        report = checker.verifier_scripts_projet(base, "fixture")
        self.assertEqual(report["statut"], "PASS", report)
        self.assertEqual(report["nombre_scripts"], 1)
        self.assertFalse(list((base.prive / "projets/fixture").rglob("*.pyc")))

    def test_private_python_syntax_reports_invalid_source(self):
        """Exige FAIL pour un script privé dont la syntaxe est invalide."""
        base = self.base()
        (base.prive / "projets/fixture/bad.py").write_text("if\n")
        report = checker.verifier_scripts_projet(base, "fixture")
        self.assertEqual(report["statut"], "FAIL", report)
        self.assertEqual(report["fichiers"][0]["statut"], "FAIL")

    def test_private_python_empty_project_is_unverified(self):
        """Exige NON VÉRIFIÉ quand aucun script privé ne peut être relu."""
        base = self.base()
        report = checker.verifier_scripts_projet(base, "fixture")
        self.assertEqual(report["statut"], "NON VÉRIFIÉ")

    def test_private_python_rejects_invalid_project_and_symlink_escape(self):
        """Exige FAIL si le projet ou un lien sort de la racine privée."""
        base = self.base()
        self.assertEqual(checker.verifier_scripts_projet(base, "../autre")["statut"], "FAIL")
        outside = self.root / "outside.py"
        outside.write_text("pass\n")
        link = base.prive / "projets/fixture/linked.py"
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            self.skipTest("Création de lien symbolique indisponible")
        report = checker.verifier_scripts_projet(base, "fixture")
        self.assertEqual(report["statut"], "FAIL", report)


if __name__ == "__main__":
    unittest.main()
