"""Captures locales du viewer : sorties déterministes sans lancer de navigateur."""

import json

import pytest

from benchab.qualite.parcours_navigateur import capturer_vues


class _Canvas:
    def bounding_box(self):
        return {"width": 400, "height": 300}

    def screenshot(self, path):
        contenu = b"\x89PNG\r\n\x1a\nfixture"
        with open(path, "wb") as fichier:
            fichier.write(contenu)
        return contenu


class _Page:
    def __init__(self):
        self.canvas = _Canvas()
        self.etats = []

    def goto(self, url, wait_until):
        assert url.startswith("file:")
        assert wait_until == "networkidle"

    def wait_for_function(self, expression, timeout):
        assert "visionneuse" in expression
        assert timeout == 30000

    def locator(self, cible):
        assert cible == "#vue canvas"
        return self.canvas

    def evaluate(self, expression, argument=None):
        if argument is None:
            return None
        vue, axe = argument
        etat = {"vue": vue, "axe_coupe": axe, "coupes_actives": int(axe is not None)}
        self.etats.append(etat)
        return etat


def test_captures_multi_angle_produisent_sept_png_et_manifeste(tmp_path):
    """Les sept vues nommées sont des captures locales, Safari restant non exécuté."""
    page_source = tmp_path / "viewer.html"
    page_source.write_text("<html></html>")
    sortie = tmp_path / "captures"
    sortie.mkdir()

    resultat = capturer_vues(_Page(), page_source, sortie)

    assert resultat["statut"] == "PASS"
    assert len(resultat["captures"]) == 7
    assert resultat["navigateurs"]["safari"] == "NON EXÉCUTÉ"
    assert all(
        (sortie / entree["fichier"]).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        for entree in resultat["captures"]
    )
    assert json.loads(next(sortie.glob("*-captures.json")).read_text()) == resultat


def test_captures_refusent_la_page_dans_le_dossier_de_sortie(tmp_path):
    """La fixture source ne peut pas être confondue avec un résultat neuf."""
    sortie = tmp_path / "captures"
    sortie.mkdir()
    page_source = sortie / "viewer.html"
    page_source.write_text("<html></html>")

    with pytest.raises(ValueError, match="page source"):
        capturer_vues(_Page(), page_source, sortie)
