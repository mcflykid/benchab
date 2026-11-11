"""Portée des règles et frontière des preuves durables."""

import json
from zipfile import ZipFile

import pytest

from benchab.controles import livrables, regles
from benchab.donnees.base import Base
from benchab.donnees.sauvegarde import creer
from benchab.qualite.ecritures import sortie_privee


def test_plafond_u25_manquant_ne_devient_pas_pass(tmp_path, monkeypatch):
    """Une extrusion identifiable sans plafond produit NON VÉRIFIÉ et code 2."""
    source = tmp_path / "fixture.3mf"
    with ZipFile(source, "w") as archive:
        archive.writestr("Metadata/project_settings.config", "{}")
        archive.writestr("Metadata/slice_info.config",
                         '<config><plate><metadata key="index" value="1"/>'
                         '<filament id="1"/><object name="Fixture"/></plate></config>')
        archive.writestr("Metadata/plate_1.gcode",
                         "M83\nT0\nEXCLUDE_OBJECT_START NAME=Fixture\nG1 X10 E0.2 F600\n")
    monkeypatch.setattr(regles.plaques_interface, "analyser", lambda _: {"conforme": True})
    monkeypatch.setattr(regles, "verifier_nommage", lambda _: {"controle": "U22", "statut": "PASS"})
    rapport = regles.controler(source)
    assert rapport["statut"] == "NON VÉRIFIÉ" and rapport["code"] == 2
    u25 = next(r for r in rapport["resultats"] if r["controle"] == "U25")
    assert u25["statut"] == "NON VÉRIFIÉ"
    assert "plafond absent" in " ".join(u25["rapport"]["plaques"][0]["erreurs"])


def test_rapport_jetable_autorise_mais_lien_refuse(atelier_initialise, tmp_path):
    """Un rapport temporaire est admissible ; une traversée de lien reste FAIL."""
    essais = tmp_path / "essais"
    essais.mkdir()
    cible = essais / "rapport.json"
    assert sortie_privee(cible, prive=atelier_initialise) == cible.resolve()
    alias = tmp_path / "alias"
    alias.symlink_to(essais, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolique"):
        sortie_privee(alias / "rapport.json", prive=atelier_initialise)


def test_preuve_archive_et_ligne_sqlite_ne_sortent_pas_de_l_atelier(atelier_initialise, tmp_path):
    """Le gel, la sauvegarde durable et une référence SQLite au temporaire hors atelier sont refusés."""
    essais = tmp_path / "essais"
    essais.mkdir()
    piece = essais / "piece.stl"
    piece.write_bytes(b"fixture")
    with pytest.raises(ValueError, match="sortie durable"):
        livrables.figer(essais, ["*.stl"], prive=atelier_initialise)
    assert not (essais / "LIVRABLES.json").exists()
    base = Base(atelier_initialise, creer=False)
    try:
        with pytest.raises(ValueError, match="sortie durable"):
            creer(base, base.prive, essais / "archive.tar")
        assert not (essais / "archive.tar").exists()
        with pytest.raises(ValueError, match="sortie durable"):
            base.ecrire("INSERT INTO projets (id,nom,donnees_json) VALUES (?,?,?)", (
                "fixture", "Fixture", json.dumps({"preuve": {"fichier": str(piece)}}),
            ))
        assert base.connexion.execute("SELECT count(*) FROM projets").fetchone()[0] == 0
    finally:
        base.fermer()


@pytest.mark.parametrize("encodage", ["unicode", "parent"])
def test_reference_json_validee_apres_decodage(tmp_path, atelier_initialise, encodage):
    """Deux écritures JSON équivalentes d’un chemin doivent respecter la même frontière."""
    if encodage == "unicode":
        hors_atelier = json.dumps(str(tmp_path / "rapport.json")).replace("/", r"\u002f")
    else:
        hors_atelier = '".."'
    donnees = '{"preuve":{"chemin":' + hors_atelier + "}}"
    assert "/" not in donnees
    base = Base(atelier_initialise, creer=False)
    try:
        dossier = base.prive / "export"
        avant = {p.name: p.read_bytes() for p in dossier.iterdir() if p.is_file()}
        with pytest.raises(ValueError, match="sortie durable"):
            base.ecrire(
                "INSERT INTO projets (id,nom,donnees_json) VALUES (?,?,?)",
                ("fixture", "Fixture", donnees),
            )
        assert base.connexion.execute("SELECT count(*) FROM projets").fetchone()[0] == 0
        assert {p.name: p.read_bytes() for p in dossier.iterdir() if p.is_file()} == avant
    finally:
        base.fermer()
