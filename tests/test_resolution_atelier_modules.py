"""Résolutions privées des commandes restent indépendantes du dossier courant."""

import json
from datetime import date
from pathlib import Path

import pytest

from benchab.chemins import atelier_prive
from benchab.conception import cad_engine, eprouvette_jeux, eprouvette_trous, trous, vissage
from benchab.organisation import automatisation
from benchab.projets import nouveau
from benchab.visualisation import apercu_gcode


def test_resolutions_par_defaut_ne_suivent_pas_le_dossier_courant(monkeypatch, tmp_path):
    monkeypatch.delenv("BENCHAB_PRIVE", raising=False)
    monkeypatch.chdir(tmp_path)
    attendu = Path(__file__).resolve().parents[1] / "prive"

    assert atelier_prive() == attendu
    assert nouveau.racine_privee() == attendu
    assert trous.chemin_table() == attendu / "config/compensation-trous.json"
    assert not (tmp_path / "prive").exists()


def test_automatisation_utilise_l_atelier_commun_sans_le_creer(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("BENCHAB_PRIVE", raising=False)
    monkeypatch.chdir(tmp_path)
    observe = {}

    def diagnostic(racine, projet, preparation):
        observe["racine"] = racine
        return {"statut": "PASS", "projet": projet}

    monkeypatch.setattr(automatisation, "diagnostiquer", diagnostic)

    assert automatisation.main(["diagnostic", "--projet", "fixture"]) == 0
    assert observe["racine"] == Path(__file__).resolve().parents[1] / "prive"
    assert json.loads(capsys.readouterr().out)["statut"] == "PASS"
    assert not (tmp_path / "prive").exists()


def test_destination_cad_privee_et_temporaire_et_refus_externe(monkeypatch, tmp_path):
    prive = tmp_path / "atelier"
    (prive / "cache").mkdir(parents=True)
    monkeypatch.setenv("BENCHAB_PRIVE", str(prive))

    privee = cad_engine.destination(prive / "cache" / "cad-prive")
    temporaire = cad_engine.destination(tmp_path / "cad-temp")
    assert privee.is_dir() and temporaire.is_dir()
    with pytest.raises(ValueError, match="atelier privé ou temporaire"):
        cad_engine.destination(Path.home() / "cad-hors-atelier")


def test_eprouvette_jeux_reconnait_sortie_temporaire_sans_suivre_liens(monkeypatch, tmp_path):
    prive = tmp_path / "atelier"
    prive.mkdir()
    monkeypatch.setenv("BENCHAB_PRIVE", str(prive))
    sortie = tmp_path / "sortie-jeux"

    assert eprouvette_jeux._autoriser_sortie(sortie) == sortie
    cible = prive / "cible"
    cible.mkdir()
    lien = prive / "lien"
    lien.symlink_to(cible, target_is_directory=True)
    with pytest.raises(ValueError, match="Lien symbolique refusé"):
        eprouvette_jeux._autoriser_sortie(lien / "nouveau")


def test_eprouvette_trous_et_vissage_prennent_l_atelier_explicite(monkeypatch, tmp_path, capsys):
    prive = tmp_path / "atelier"
    captures = {}

    def generer(sortie, *, table_path=None):
        captures["sortie"] = sortie
        captures["table"] = table_path

    monkeypatch.setattr(eprouvette_trous, "generer", generer)
    assert eprouvette_trous.main(["generer"], prive=prive) == 0
    assert captures["sortie"] == prive / "eprouvettes" / f"{date.today().isoformat()}-trous.stl"
    assert captures["table"] == prive / "config/compensation-trous.json"

    monkeypatch.setattr(vissage, "profil_fichier", lambda *args, **kwargs: {"statut": "PASS"})
    rapport = prive / "mesures" / "vissage.json"
    assert vissage.main(["maillage.stl", "--json", str(rapport)], prive=prive) == 0
    assert json.loads(rapport.read_text(encoding="utf-8"))["statut"] == "PASS"
    assert json.loads(capsys.readouterr().out)["statut"] == "PASS"


def test_apercu_gcode_accepte_tmp_par_resolveur_commun(monkeypatch, tmp_path, capsys):
    prive = tmp_path / "atelier"
    prive.mkdir()
    sortie = tmp_path / "apercu.html"
    monkeypatch.setenv("BENCHAB_PRIVE", str(prive))
    monkeypatch.setattr(apercu_gcode, "page", lambda *args: {"fichier": str(args[1]), "statut": "PASS"})

    assert apercu_gcode.main(["entree.gcode", str(sortie)]) == 0
    assert json.loads(capsys.readouterr().out)["fichier"] == str(sortie)
