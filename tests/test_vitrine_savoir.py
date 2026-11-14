"""Contrat de la vitrine reproductible, issue du seul corpus public."""

import json
import subprocess

import pytest

from benchab.connaissances.vitrine import generer


def _depot(tmp_path, *, public=1):
    racine = tmp_path / "depot"
    corpus = racine / "benchab/reference/connaissances.jsonl"
    corpus.parent.mkdir(parents=True)
    ligne = {
        "id": "savoir-test-001",
        "categorie": "observation",
        "public": public,
        "date_source": "2026-11-11",
        "source": "preuve anonymisée",
        "texte": "Nettoyage de buse : <script>risque</script> ; code M104.",
        "donnees_json": json.dumps({
            "portee": "Témoin synthétique et aucune validation physique",
            "statut": "observé une fois",
            "page": "docs/reference/machine-kobra-s1.md",
        }),
    }
    corpus.write_text(json.dumps(ligne) + "\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", racine], check=True)
    subprocess.run(["git", "-C", racine, "add", "--", corpus], check=True)
    subprocess.run(
        ["git", "-C", racine, "-c", "user.name=Test", "-c", "user.email=test",
         "-c", "commit.gpgsign=false", "commit", "-qm", "Corpus synthétique"],
        check=True,
    )
    return racine, corpus


def test_vitrine_stable_navigation_et_contenu_echappe(tmp_path):
    """La même preuve produit les mêmes octets et reste du texte en HTML."""
    racine, _ = _depot(tmp_path)
    prive = racine / "prive"
    prive.mkdir()
    (prive / "personnel.txt").write_text("preuve privée non lue", encoding="utf-8")
    a, b = prive / "a.html", prive / "b.html"

    r1 = generer(racine, a, prive=prive)
    r2 = generer(racine, b, prive=prive)

    assert r1["sha256_html"] == r2["sha256_html"]
    assert r1["nombre_fiches"] == 1
    assert a.read_bytes() == b.read_bytes()
    page = a.read_text(encoding="utf-8")
    assert '<a href="#entretien">Entretien</a>' in page
    assert '<a href="#codes">Codes</a>' in page
    assert "&lt;script&gt;risque&lt;/script&gt;" in page and "<script>" not in page
    assert "observé une fois" in page and "Témoin synthétique" in page
    assert "preuve privée non lue" not in page


def test_vitrine_refuse_fiche_privee_meme_commitee(tmp_path):
    """Le marqueur public explicite est requis sur chaque ligne du corpus."""
    racine, _ = _depot(tmp_path, public=0)
    prive = racine / "prive"
    prive.mkdir()
    destination = prive / "vitrine.html"

    with pytest.raises(ValueError, match="publique"):
        generer(racine, destination, prive=prive)

    assert not destination.exists()


def test_vitrine_refuse_corpus_remplace_par_fichier_tiers(tmp_path):
    """Un autre fichier, même marqué public, ne remplace pas le corpus versionné."""
    racine, corpus = _depot(tmp_path)
    prive = racine / "prive"
    prive.mkdir()
    tiers = tmp_path / "tiers.jsonl"
    tiers.write_bytes(corpus.read_bytes() + b"\n")
    corpus.write_bytes(tiers.read_bytes())
    destination = prive / "vitrine.html"

    with pytest.raises(ValueError, match="versionné"):
        generer(racine, destination, prive=prive)

    assert not destination.exists()


def test_manifeste_public_reproductible_et_reference_versionnee(tmp_path):
    """Le manifeste reprend la provenance, avec des pages publiques réellement suivies."""
    racine, _ = _depot(tmp_path)
    prive = racine / "prive"
    prive.mkdir()
    page = racine / "docs/reference/machine-kobra-s1.md"
    page.parent.mkdir(parents=True)
    page.write_text("# Kobra S1\n")
    subprocess.run(["git", "-C", racine, "add", "--", page], check=True)
    subprocess.run(
        ["git", "-C", racine, "-c", "user.name=Test", "-c", "user.email=test",
         "-c", "commit.gpgsign=false", "commit", "-qm", "Guide synthétique"],
        check=True,
    )
    for nom in ("a", "b"):
        generer(racine, prive / f"{nom}.html", prive=prive, manifeste=prive / f"{nom}.json")
    fichier = prive / "a.json"
    assert fichier.read_bytes() == (prive / "b.json").read_bytes()
    manifeste = json.loads(fichier.read_text())
    assert manifeste["fiches"][0]["date"] == "2026-11-11"
    assert manifeste["fiches"][0]["page"] == "docs/reference/machine-kobra-s1.md"
    assert manifeste["fiches"][0]["statut"] == "observé une fois"
