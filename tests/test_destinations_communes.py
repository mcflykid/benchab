"""Régressions #17 : sorties publiques refusées, liens conservés et temporaires uniques."""

import json

import pytest


@pytest.mark.parametrize("outil", ["remplissage", "decoupe", "cao"])
def test_checkout_temporaire_ne_devient_pas_destination_publique(tmp_path, monkeypatch, outil):
    """Un checkout sous TMPDIR reste public ; aucune sortie n'y est créée."""
    import benchab.chemins as chemins

    depot = tmp_path / "checkout"
    prive = depot / "prive"
    prive.mkdir(parents=True)
    monkeypatch.setattr(chemins, "racine_depot", lambda: depot)
    monkeypatch.setenv("BENCHAB_PRIVE", str(prive))
    cible = depot / "export"
    if outil == "remplissage":
        from benchab.fabrication.remplissage_localise import _sortie_privee
        def appel():
            return _sortie_privee(cible)
    elif outil == "decoupe":
        from benchab.conception.decoupe.atelier import _destination
        def appel():
            return _destination(cible, prive=prive)
    else:
        from benchab.conception.cad_engine import destination
        def appel():
            return destination(cible)
    with pytest.raises(ValueError):
        appel()
    assert not cible.exists()


def test_json_trancher_ne_touche_pas_un_temporaire_voisin(tmp_path):
    """Une collision avec l'ancien nom fixe ne détruit aucun fichier voisin."""
    from benchab.fabrication.trancher import ecrire_json

    cible = tmp_path / "rapport.json"
    voisin = tmp_path / "rapport.tmp"
    voisin.write_text("preuve à conserver", encoding="utf-8")
    ecrire_json(cible, {"état": "terminé"})
    assert voisin.read_text(encoding="utf-8") == "preuve à conserver"
    assert json.loads(cible.read_text(encoding="utf-8")) == {"état": "terminé"}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["rapport.json", "rapport.tmp"]


def test_hook_refuse_lien_avant_resolution(tmp_path):
    """L'alias interne ne masque pas la cible d'une modification de source."""
    from benchab.qualite.hooks import pre

    source = tmp_path / "source"
    source.mkdir()
    lien = tmp_path / "alias"
    lien.symlink_to(source, target_is_directory=True)
    with pytest.raises(ValueError, match="Lien symbolique"):
        pre({"cwd": str(tmp_path), "tool_input": {"file_path": str(lien / "piece.py")}})


def test_origine_accepte_temporaire_natif_sans_cwd_implicite(tmp_path):
    """La restauration emploie la même politique du temporaire natif que les exports."""
    from benchab.fabrication.origine_3mf import _destination_neuve

    cible, parent = _destination_neuve(tmp_path / "restaure.3mf")
    assert cible == tmp_path / "restaure.3mf"
    assert parent == tmp_path
    assert not cible.exists()


def test_echec_publication_preserve_ancien_etat_et_voisins(tmp_path, monkeypatch):
    """Une publication interrompue ne détruit ni l'état antérieur ni un temporaire étranger."""
    from pathlib import Path

    from benchab.qualite.ecritures import remplacer_texte

    cible = tmp_path / "etat.json"
    cible.write_text("ancien", encoding="utf-8")
    voisin = tmp_path / ".publication-autre"
    voisin.write_text("autre", encoding="utf-8")

    def interrompre(self, destination):
        raise OSError("publication interrompue")

    monkeypatch.setattr(Path, "replace", interrompre)
    with pytest.raises(OSError, match="interrompue"):
        remplacer_texte(cible, "nouveau")
    assert cible.read_text(encoding="utf-8") == "ancien"
    assert voisin.read_text(encoding="utf-8") == "autre"
    assert len(list(tmp_path.iterdir())) == 2


def test_dossier_prive_reste_epingle_avant_creation_temporaire(tmp_path, monkeypatch):
    """Un parent redirigé après la garde ne peut recevoir les octets du lot."""
    import os

    from benchab.qualite.ecritures import publier_dossier_neuf

    prive = tmp_path / "atelier"
    parent = prive / "rapports"
    parent.mkdir(parents=True)
    externe = tmp_path / "externe"
    externe.mkdir()
    deplace = prive / "ancien-parent"
    mkdir = os.mkdir

    def remplacer(nom, mode=0o777, *, dir_fd=None):
        if dir_fd is not None and str(nom).startswith(".publication-dossier-"):
            parent.rename(deplace)
            parent.symlink_to(externe, target_is_directory=True)
        return mkdir(nom, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "mkdir", remplacer)
    with pytest.raises(ValueError):
        publier_dossier_neuf(parent / "preuve", {"rapport.json": "preuve privée".encode()}, prive=prive)
    assert list(externe.iterdir()) == []
    assert list(deplace.iterdir()) == []


def test_dossier_prive_refuse_les_noms_imbriques(tmp_path):
    """Les fichiers du lot portent des noms simples ; aucun chemin n'est créé."""
    from benchab.qualite.ecritures import publier_dossier_neuf

    with pytest.raises(ValueError, match="nom simple"):
        publier_dossier_neuf(tmp_path / "preuve", {"../hors.json": b""}, prive=tmp_path)
    assert list(tmp_path.iterdir()) == []
