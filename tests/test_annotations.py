"""Tests des portées progressives de Ruff D10x."""

import subprocess
from pathlib import Path

from benchab.qualite.annotations import controler


def _depot(tmp_path: Path, contenu: str, nom: str = "module.py") -> tuple[Path, str, str]:
    """Prépare deux révisions Git isolées pour éprouver le ratchet."""
    racine = tmp_path / "depot"
    racine.mkdir()

    def git(*arguments: str) -> str:
        return subprocess.run(
            ["git", *arguments], cwd=racine, check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-q")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Test")
    chemin = racine / "benchab" / "qualite"
    chemin.mkdir(parents=True)
    fichier = chemin / nom
    fichier.write_text(contenu, encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    return racine, base, str(fichier)


def _terminer(racine: Path, fichier: str, contenu: str) -> str:
    """Enregistre l'état candidat et retourne son identifiant Git."""
    cible = Path(fichier)
    cible.write_text(contenu, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=racine, check=True)
    subprocess.run(["git", "commit", "-qm", "candidate"], cwd=racine, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=racine, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_portee_publique_nouvelle_incomplete_est_refusee(tmp_path):
    """Refuse une nouvelle fonction publique sans docstring."""
    racine, base, fichier = _depot(tmp_path, """\ndef public():\n    return 1\n""")
    tete = _terminer(racine, fichier, """\ndef public():\n    return 1\n\ndef nouvelle():\n    return 2\n""")
    assert any("D103" in erreur for erreur in controler(racine, base, tete))


def test_module_nouveau_sans_doc_est_refuse(tmp_path):
    """Refuse un nouveau module sans documentation de module."""
    racine, base, _ = _depot(tmp_path, "\n")
    nouveau = racine / "benchab" / "qualite" / "ajout.py"
    nouveau.write_text("def nouvelle():\n    return 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=racine, check=True)
    subprocess.run(["git", "commit", "-qm", "nouveau module"], cwd=racine, check=True)
    tete = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=racine, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert any("D100" in erreur for erreur in controler(racine, base, tete))


def test_portee_existante_touchee_sans_doc_est_refusee(tmp_path):
    """Refuse une fonction publique historique dont le corps change."""
    racine, base, fichier = _depot(tmp_path, """\ndef public():\n    return 1\n""")
    tete = _terminer(racine, fichier, """\ndef public():\n    return 2\n""")
    assert any("D103" in erreur for erreur in controler(racine, base, tete))


def test_suppression_docstring_portee_publique_est_refusee(tmp_path):
    """Détecte une suppression de docstring même sans ligne ajoutée."""
    racine, base, fichier = _depot(
        tmp_path,
        """\ndef publique():
    \"\"\"Docstring présente.\"\"\"
    return 1
""",
    )
    tete = _terminer(
        racine,
        fichier,
        """\ndef publique():
    return 1
""",
    )
    assert any("D103" in erreur for erreur in controler(racine, base, tete))


def test_dette_legacy_intacte_ne_bloque_pas(tmp_path):
    """Ignore une fonction historique inchangée hors portée modifiée."""
    racine, base, fichier = _depot(tmp_path, """\ndef historique():\n    return 1\n""")
    tete = _terminer(
        racine,
        fichier,
        """\ndef historique():\n    return 1\n\ndef nouvelle():\n    \"\"\"Retourne 2.\"\"\"\n    return 2\n""",
    )
    assert controler(racine, base, tete) == []


def test_renommage_controle_le_nouveau_chemin(tmp_path):
    """Analyse les annotations du chemin cible après renommage."""
    racine, base, ancien = _depot(tmp_path, """\ndef publique():\n    return 1\n""")
    nouveau = str(Path(ancien).with_name("renomme.py"))
    Path(ancien).rename(nouveau)
    tete = subprocess.run(["git", "add", "-A"], cwd=racine, check=True)
    del tete
    subprocess.run(["git", "commit", "-qm", "renommage"], cwd=racine, check=True)
    rev = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=racine, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert any("D103" in erreur for erreur in controler(racine, base, rev))


def test_base_invalide_echoue_sans_sauter(tmp_path):
    """Échoue explicitement quand la révision de base est absente."""
    racine, _, _ = _depot(tmp_path, "\n")
    tete = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=racine, check=True, capture_output=True, text=True
    ).stdout.strip()
    try:
        controler(racine, "missing-base", tete)
    except ValueError as erreur:
        assert "Git a échoué" in str(erreur)
    else:
        raise AssertionError("Une base invalide doit échouer explicitement")
