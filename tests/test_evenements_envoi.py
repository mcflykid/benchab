"""Associations archivées sans réécrire le snapshot ni inventer une impression."""

import json
import shutil

import pytest

from benchab.donnees.base import Base
from benchab.fabrication.preparation import gcodes
from benchab.projets.envois import enregistrer, lire_fiche
from benchab.projets.preparations import enregistrer as inscrire


@pytest.fixture
def atelier(tmp_path):
    from zipfile import ZipFile

    base = Base(tmp_path / "prive")
    projet = base.prive / "projets/fixture"
    preparation = projet / "preparations/P01"
    preparation.mkdir(parents=True)
    tranche = preparation / "tranche.3mf"
    with ZipFile(tranche, "w") as archive:
        archive.writestr("Metadata/plate_1.gcode", "G1 X1 Y1 E1\n")
    fiche = preparation / "PREPARATION.json"
    fiche.write_text(json.dumps({"id": "P01", "date": "2026-11-04", "gcode": gcodes(tranche), "envoi": None}))
    inscrire(base, fiche)
    try:
        yield base, projet, fiche
    finally:
        base.fermer()


def test_association_preserve_snapshot_et_ne_cree_pas_impression(atelier):
    base, projet, fiche = atelier
    source = base.prive / "export.gcode"
    source.write_text("G1 X1 Y1 E1 ; commentaire\n")
    avant = fiche.read_bytes()
    ligne = tuple(base.connexion.execute("SELECT empreinte,donnees_json FROM preparations").fetchone())
    envoi = enregistrer(base, projet, "P01", source)
    assert "commandes identiques" in envoi["comparaison"]["1"]
    assert envoi["demarrage_physique_verifie"] is False
    assert (base.prive / envoi["fichier"]).read_bytes() == source.read_bytes()
    assert fiche.read_bytes() == avant
    assert tuple(base.connexion.execute("SELECT empreinte,donnees_json FROM preparations").fetchone()) == ligne
    assert base.connexion.execute("SELECT count(*) FROM impressions").fetchone()[0] == 0
    assert lire_fiche(base, projet, "P01")["envoi"]["sha256"] == envoi["sha256"]
    with pytest.raises(ValueError, match="déjà enregistré"):
        enregistrer(base, projet, "P01", source, ajouter=True)
    assert base.connexion.execute("SELECT count(*) FROM decisions").fetchone()[0] == 1


def test_ajout_explicite_et_commandes_differentes_restent_visibles(atelier):
    base, projet, _ = atelier
    a, b = base.prive / "a.gcode", base.prive / "b.gcode"
    a.write_text("G1 X1 Y1 E1\n")
    b.write_text("G1 X9 Y9 E1\n")
    with pytest.raises(ValueError, match="sans premier"):
        enregistrer(base, projet, "P01", a, ajouter=True)
    enregistrer(base, projet, "P01", a)
    with pytest.raises(ValueError, match="ajout explicite"):
        enregistrer(base, projet, "P01", b)
    dernier = enregistrer(base, projet, "P01", b, ajouter=True)
    assert "DIFFÉRENTES" in dernier["comparaison"]["1"]
    assert len(lire_fiche(base, projet, "P01")["envois_supplementaires"]) == 1


def test_associations_restent_resolvables_apres_deplacement(tmp_path):
    from zipfile import ZipFile

    prive = tmp_path / "avant"
    base = Base(prive)
    projet = prive / "projets/fixture"
    dossier = projet / "preparations/P01"
    dossier.mkdir(parents=True)
    tranche = dossier / "tranche.3mf"
    with ZipFile(tranche, "w") as archive:
        archive.writestr("Metadata/plate_1.gcode", "G1 X1 E1\n")
    (dossier / "PREPARATION.json").write_text(json.dumps({"id": "P01", "gcode": gcodes(tranche), "envoi": None}))
    source = prive / "export.gcode"
    source.write_text("G1 X1 E1\n")
    enregistrer(base, projet, "P01", source)
    base.fermer()
    nouveau = tmp_path / "apres"
    shutil.copytree(prive, nouveau)
    base = Base(nouveau)
    try:
        envoi = lire_fiche(base, nouveau / "projets/fixture", "P01")["envoi"]
        assert not envoi["fichier"].startswith(str(prive))
        assert (nouveau / envoi["fichier"]).is_file()
    finally:
        base.fermer()
