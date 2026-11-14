"""Contrats de la simulation privée de conservation."""

import hashlib
import json

import pytest

from benchab.donnees.base import Base
from benchab.projets.conservation import simuler


@pytest.fixture
def atelier(tmp_path):
    """Prépare une base privée et un projet temporaire pour les scénarios synthétiques."""
    base = Base(tmp_path / "prive")
    projet = base.prive / "projets" / "fixture"
    projet.mkdir()
    yield base, projet
    base.fermer()


def _instantane(projet):
    """Capture les chemins et octets du projet afin de prouver l’absence d’écriture."""
    return {
        chemin.relative_to(projet).as_posix(): chemin.read_bytes() for chemin in projet.rglob("*") if chemin.is_file()
    }


@pytest.mark.parametrize("statut", ["ABANDONNE", "EN_PREPARATION"])
def test_propose_intermediaire_abandonne_dedoublonne_et_preserve_le_reste(atelier, statut):
    """Propose des intermédiaires même pendant le travail, sans toucher aux sources ni aux références protégées."""
    base, projet = atelier
    (projet / "sources").mkdir()
    (projet / "sources/source.txt").write_text("source nécessaire")
    (projet / "historique-code").mkdir()
    (projet / "historique-code/Makefile").write_text("trace du processus historique")
    (projet / "docs").mkdir()
    (projet / "docs/contrat.md").write_text("contrat")
    (projet / "preparations/P01").mkdir(parents=True)
    (projet / "preparations/P01/PREPARATION.json").write_text('{"id":"P01"}')
    (projet / "candidats").mkdir()
    (projet / "candidats/courant.stl").write_bytes(b"courant")
    (projet / "candidats/abandonne.stl").write_bytes(b"intermediaire identique")
    (projet / "archives").mkdir()
    (projet / "archives/cop ie.stl").write_bytes(b"intermediaire identique")
    (projet / "travail.step").write_bytes(b"travail courant")
    (projet / "preuve.png").write_bytes(b"image observation")
    suivi = {
        "statut": statut,
        "references_imprimees": [
            {
                "id": "active-1",
                "preuve": "retour humain",
                "fichiers": [{"chemin": "candidats/courant.stl", "sha256": hashlib.sha256(b"courant").hexdigest()}],
            }
        ],
        "references_historiques": [
            {
                "id": "historique-1",
                "preuve": "ancienne",
                "fichiers": [{"chemin": "travail.step", "sha256": hashlib.sha256(b"travail courant").hexdigest()}],
            }
        ],
        "travail_en_cours": {"fichiers": ["travail.step"]},
        "sources_necessaires": ["sources/source.txt"],
        "preuves_utiles": ["preuve.png"],
    }
    (projet / "qualite.json").write_text(json.dumps({"suivi": suivi}))
    avant = _instantane(projet)

    resultat = simuler(base, projet)

    propositions = {item["chemin"]: item for item in resultat["intermediaires"]}
    assert set(propositions) == {"candidats/abandonne.stl", "archives/cop ie.stl"}
    assert propositions["candidats/abandonne.stl"]["statut_proposition"] == "PROPOSITION"
    assert propositions["candidats/abandonne.stl"]["accord_requis"] is True
    assert propositions["candidats/abandonne.stl"]["gain_accessible_octets"] == 0
    assert len(resultat["doublons"]) == 1
    assert resultat["doublons"][0]["chemins"] == ["archives/cop ie.stl", "candidats/abandonne.stl"]
    retenus = {item["chemin"]: item for item in resultat["fichiers_retenus"]}
    assert "candidats/courant.stl" in retenus
    assert "référence imprimée active" in retenus["candidats/courant.stl"]["roles"]
    assert "référence imprimée historique" in retenus["travail.step"]["roles"]
    assert "travail courant" in retenus["travail.step"]["roles"]
    assert "sources/source.txt" in retenus
    assert "historique-code/Makefile" in retenus
    assert "docs/contrat.md" in retenus
    assert "preparations/P01/PREPARATION.json" in retenus
    assert "preuve.png" in retenus
    assert resultat["simulation"] is True
    assert resultat["suppression_possible"] is False
    assert _instantane(projet) == avant


def test_refuse_lien_sortant_sans_le_lire(atelier, tmp_path):
    base, projet = atelier
    cible = tmp_path / "hors-projet.txt"
    cible.write_text("secret externe")
    (projet / "sortie.txt").symlink_to(cible)

    with pytest.raises(ValueError, match="Lien symbolique"):
        simuler(base, projet)

    assert cible.read_text() == "secret externe"
