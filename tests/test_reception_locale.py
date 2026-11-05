"""Réceptions de pages locales : fichier autonome autorisé, aucun accès réseau dans ces tests."""

import pytest

from benchab.qualite.reception import url_locale


def test_fichier_html_autonome_prive_avec_unicode_et_options(tmp_path, monkeypatch):
    """Accepte un HTML privé explicite, conserve requête/fragment ; aucune page ni machine ouverte."""
    prive = tmp_path / "prive"
    prive.mkdir()
    monkeypatch.setenv("BENCHAB_PRIVE", str(prive))
    page = prive / "paquet été.html"
    page.write_text("<html></html>")
    url = page.as_uri() + "?mode=partage#vue"
    assert url_locale(url) == url


@pytest.mark.parametrize("url", ["file://distant.invalid/page.html", "file://user:secret@localhost/page.html",
                                  "https://externe.invalid/page.html", "data:text/html,<html></html>"])
def test_reception_refuse_source_distante_ou_identifiants(url):
    """Les identifiants et autorités distantes donnent FAIL avant lecture ou requête ; aucune unité."""
    with pytest.raises(ValueError):
        url_locale(url)


def test_reception_fichier_refuse_lien_et_type_non_html(tmp_path):
    """Un lien ou un fichier non HTML reste refusé ; aucun contenu n'est exécuté."""
    autre = tmp_path / "fixture.json"
    autre.write_text("{}")
    with pytest.raises(ValueError, match="HTML"):
        url_locale(autre.as_uri())
    cible = tmp_path / "fixture.html"
    cible.write_text("<html></html>")
    lien = tmp_path / "lien.html"
    lien.symlink_to(cible)
    with pytest.raises(ValueError, match="Lien symbolique"):
        url_locale(lien.as_uri())
