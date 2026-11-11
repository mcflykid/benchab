"""Tests des portées progressives de Ruff D10x."""

import subprocess
from pathlib import Path

import pytest

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
    # Git accepte une identité synthétique sans adresse réelle dans ce dépôt jetable.
    git("config", "user.email", "fixture-synthetique")
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


@pytest.mark.parametrize(("avant", "apres"), [("", "@memoiser\n"), ("@memoiser\n", "")])
def test_decorateur_modifie_qualifie_la_portee(tmp_path, avant, apres):
    """L'ajout ou le retrait d'un décorateur modifie la définition publique."""
    corps = "def publique():\n    return 1\n"
    racine, base, fichier = _depot(tmp_path, '"""Module."""\n' + avant + corps)
    tete = _terminer(racine, fichier, '"""Module."""\n' + apres + corps)
    assert any("D103" in erreur for erreur in controler(racine, base, tete))


def test_suppression_de_portee_nimpute_pas_la_dette_au_voisin(tmp_path):
    """Supprimer un autre symbole ne modifie pas la fonction historique conservée."""
    historique = "def historique():\n    return 1\n"
    racine, base, fichier = _depot(tmp_path, historique + "\ndef retiree():\n    return 2\n")
    tete = _terminer(racine, fichier, historique)
    assert controler(racine, base, tete) == []


def test_constructeur_public_modifie_exige_sa_docstring(tmp_path):
    """Une méthode spéciale soumise à D107 ne disparaît pas du filtre AST."""
    avant = 'class Publique:\n    """Rôle."""\n    def __init__(self):\n        self.valeur = 1\n'
    racine, base, fichier = _depot(tmp_path, avant)
    tete = _terminer(racine, fichier, avant.replace("= 1", "= 2"))
    assert any("D107" in erreur for erreur in controler(racine, base, tete))


def test_nouveau_paquet_sans_docstring_est_refuse(tmp_path):
    """L'initialisation d'un paquet relève de D104 plutôt que D100."""
    racine, base, _ = _depot(tmp_path, '"""Module."""\n')
    nouveau = racine / "benchab" / "qualite" / "__init__.py"
    tete = _terminer(racine, str(nouveau), "VALEUR = 1\n")
    assert any("D104" in erreur for erreur in controler(racine, base, tete))
