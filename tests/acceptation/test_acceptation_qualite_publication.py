"""Les contrôles complets de publication remplacent l'attente du socle initial."""

from pathlib import Path

from benchab.qualite.edition import documentation, licences
from benchab.qualite.frontiere import verifier

R = Path(__file__).resolve().parents[2]


def test_frontiere_et_licences_reelles():
    assert verifier(R) == []
    assert licences(R) == []


def test_documentation_sans_lien_local_mort():
    # Les URL distantes sont testées par la commande réelle du lot, pas par la CI.
    assert documentation(R, lecteur_url=lambda _url: None) == []
    for famille in ("guides", "reference", "explications", "decisions"):
        assert list((R / "docs" / famille).glob("*.md"))
    assert (R / "docs/identite/benchab3d-logo.png").is_file()


def test_ci_sans_slicer():
    texte = (R / ".github/workflows/tests.yml").read_text()
    assert "pytest" in texte and "ruff" in texte
    assert "fabrication trancher" not in texte
