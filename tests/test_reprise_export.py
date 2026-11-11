"""Prépare une copie d’export sans modifier l’atelier canonique."""

import json
import sqlite3
from pathlib import Path

import pytest

from benchab.donnees.base import Base


@pytest.fixture
def atelier(tmp_path):
    """Atelier jetable contenant un export non vide, avant retrait de la table."""
    racine = tmp_path / "prive"
    base = Base(racine)
    try:
        base.ecrire(
            "INSERT INTO projets(id,nom,etat,donnees_json) VALUES (?,?,?,?)",
            ("fixture", "Copie épreuve", "actif", "{}"),
        )
    finally:
        base.fermer()
    cible = racine / "export/projets.jsonl"
    octets = cible.read_bytes()
    cible.unlink()
    return racine, cible, octets


def test_preparation_restitue_les_octets_sans_appliquer(tmp_path, atelier):
    """SQLite et le manifeste prouvent les mêmes lignes que l’export original."""
    from benchab.qualite.reprise_export import preparer

    prive, cible, attendu = atelier
    avant = (prive / "benchab.sqlite").read_bytes()
    manifeste = (prive / "export/manifest.json").read_bytes()
    sortie = tmp_path / "reprise"
    rapport = preparer(prive, "projets", sortie)
    assert rapport["statut"] == "PASS" and rapport["applique"] is False
    assert rapport["table"] == "projets" and rapport["lignes"] == 1
    assert (sortie / "projets.jsonl").read_bytes() == attendu
    assert json.loads((sortie / "preuve.json").read_bytes()) == rapport
    assert not cible.exists()
    assert (prive / "benchab.sqlite").read_bytes() == avant
    assert (prive / "export/manifest.json").read_bytes() == manifeste
    assert not (prive / "benchab.sqlite-journal").exists()
    with pytest.raises(FileExistsError):
        preparer(prive, "projets", sortie)


def test_refus_si_manifeste_et_sqlite_divergent(tmp_path, atelier):
    """Une preuve attendue mais différente ne produit aucune copie publiable."""
    from benchab.qualite.reprise_export import preparer

    prive, cible, _ = atelier
    manifeste = prive / "export/manifest.json"
    data = json.loads(manifeste.read_bytes())
    data["sha256"]["projets"] = "0" * 64
    manifeste.write_text(json.dumps(data))
    sortie = tmp_path / "reprise"
    with pytest.raises(ValueError, match="empreinte"):
        preparer(prive, "projets", sortie)
    assert not sortie.exists() and not cible.exists()


def test_refus_si_cible_presente_ou_sortie_dans_export(tmp_path, atelier):
    """Le dossier de preuve ne peut pas servir à restaurer en place."""
    from benchab.qualite.reprise_export import preparer

    prive, cible, _ = atelier
    sortie = tmp_path / "reprise"
    with pytest.raises(ValueError, match="hors.*export"):
        preparer(prive, "projets", cible.parent / "reprise")
    lien = tmp_path / "copie"
    lien.write_bytes(b"fichier a conserver")
    cible.symlink_to(lien)
    with pytest.raises(ValueError, match="absente"):
        preparer(prive, "projets", sortie)
    assert not sortie.exists() and cible.is_symlink()
    assert lien.read_bytes() == b"fichier a conserver"


def test_refus_wal_sans_creation_de_sidecar(tmp_path, atelier):
    """Une base en WAL reste hors du contrat de préparation en lecture seule."""
    from benchab.qualite.reprise_export import preparer

    prive, cible, _ = atelier
    with sqlite3.connect(prive / "benchab.sqlite") as cx:
        assert cx.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    cx.close()
    avant = {p.name for p in prive.iterdir()}
    with pytest.raises(ValueError, match="journal"):
        preparer(prive, "projets", tmp_path / "reprise")
    assert {p.name for p in prive.iterdir()} == avant
    assert not cible.exists() and not (tmp_path / "reprise").exists()


def test_cli_prepare_sans_ouvrir_base_ni_enregistrer_mesure(tmp_path, atelier, capsys, monkeypatch):
    """L’entrée CLI produit seulement la preuve et conserve le registre et ses mesures."""
    from benchab import cli

    def interdire_base(*_args, **_kwargs):
        raise AssertionError("La reprise préparée ne doit pas ouvrir Base().")

    monkeypatch.setattr(cli, "Base", interdire_base)
    prive, cible, attendu = atelier
    avant = (prive / "benchab.sqlite").read_bytes()
    sortie = tmp_path / "reprise"
    arguments = [
        "--prive", str(prive), "controle", "preparer-reprise-export",
        "--table", "projets", "--sortie", str(sortie),
    ]
    assert cli.main(arguments) == 0
    assert json.loads(capsys.readouterr().out) == json.loads((sortie / "preuve.json").read_bytes())
    assert (sortie / "projets.jsonl").read_bytes() == attendu
    assert (prive / "benchab.sqlite").read_bytes() == avant and not cible.exists()
    assert cli.main(arguments) == 2


@pytest.mark.parametrize("membre", ["projets.jsonl", "preuve.json"])
def test_course_sur_membre_publie_est_quarantaine_sans_pass(tmp_path, atelier, monkeypatch, membre):
    """Une mutation entre hachage et renommage garde ses octets en quarantaine et refuse PASS."""
    import benchab.qualite.reprise_export as reprise

    prive, _cible, attendu = atelier
    sortie = tmp_path / "reprise"
    renommer = reprise.renommer_exclusif

    def modifier_au_renommage(source, destination, **kwargs):
        if Path(destination) == sortie:
            (Path(source) / membre).write_bytes(b"modification concurrente\n")
        return renommer(source, destination, **kwargs)

    monkeypatch.setattr(reprise, "renommer_exclusif", modifier_au_renommage)
    with pytest.raises(ValueError, match="Contenu publié illisible ou modifié"):
        reprise.preparer(prive, "projets", sortie)

    quarantaine, = tmp_path.glob(".reprise.quarantaine-*")
    assert not sortie.exists()
    assert (quarantaine / membre).read_bytes() == b"modification concurrente\n"
    assert (quarantaine / "projets.jsonl").read_bytes() == (
        b"modification concurrente\n" if membre == "projets.jsonl" else attendu
    )
    assert (quarantaine / "preuve.json").is_file()


def test_destination_remplacee_apres_renommage_reste_intacte(tmp_path, atelier, monkeypatch):
    """Une cible qui ne désigne plus le stage est refusée sans toucher au tiers."""
    import benchab.qualite.reprise_export as reprise

    prive, _cible, _attendu = atelier
    sortie = tmp_path / "reprise"
    detour = tmp_path / "stage-deplace"
    renommer = reprise.renommer_exclusif

    def substituer_apres_renommage(source, destination, **kwargs):
        if Path(destination) == sortie:
            renommer(source, detour, **kwargs)
            sortie.mkdir()
            (sortie / "projets.jsonl").write_bytes(b"dossier tiers\n")
            return None
        return renommer(source, destination, **kwargs)

    monkeypatch.setattr(reprise, "renommer_exclusif", substituer_apres_renommage)
    with pytest.raises(ValueError, match="ne désigne plus le dossier préparé"):
        reprise.preparer(prive, "projets", sortie)

    assert (sortie / "projets.jsonl").read_bytes() == b"dossier tiers\n"
    assert (detour / "projets.jsonl").is_file()
    assert not list(tmp_path.glob(".reprise.quarantaine-*"))


def test_preuve_modifiee_avant_premier_hachage_est_refusee(tmp_path, atelier, monkeypatch):
    """Le SHA attendu vient des octets prévus, jamais du fichier déjà altéré."""
    from contextlib import contextmanager

    from benchab.qualite import reprise_export as reprise

    prive, cible, _ = atelier
    sortie = tmp_path / "reprise"
    ouvrir = reprise._ouvrir_dossier_epingle
    premier = True

    @contextmanager
    def ouverture_alteree(dossier):
        nonlocal premier
        if premier:
            premier = False
            (dossier / "preuve.json").write_bytes(b'{"statut":"PASS","faux":true}\n')
        with ouvrir(dossier) as fd:
            yield fd

    monkeypatch.setattr(reprise, "_ouvrir_dossier_epingle", ouverture_alteree)
    with pytest.raises(ValueError, match="SHA|empreinte"):
        reprise.preparer(prive, "projets", sortie)
    assert not sortie.exists() and not cible.exists()
