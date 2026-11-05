"""Régressions des correctifs utilisateur du 05/11 : alias système et atelier choisi explicitement."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from benchab.chemins import atelier_prive, est_temporaire, racine_depot, sans_lien


def _cli(cwd, atelier, *arguments):
    env = dict(os.environ, BENCHAB_PRIVE=str(atelier), PYTHONDONTWRITEBYTECODE='1')
    return subprocess.run([sys.executable, '-B', '-m', 'benchab.cli', *arguments], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=20)


def test_alias_temporaire_systeme_accepte_par_les_gardes_communes():
    """Le temporaire système natif est légitime ; chemin canonique rendu sans suivre de lien interne."""
    from benchab.fabrication.preparation import _sans_lien as preparation
    from benchab.qualite.complet import _sans_lien as complet
    from benchab.qualite.ecritures import _sans_lien as ecritures

    with tempfile.TemporaryDirectory(prefix='benchab-chemins-') as tmp:
        fichier = Path(tmp) / 'fixture.txt'
        fichier.write_text('abc')
        for garde in (sans_lien, ecritures, complet, preparation):
            assert garde(fichier) == fichier.resolve()
        assert est_temporaire(fichier)
        assert fichier.read_text() == 'abc'


def test_parent_lie_au_dessus_confiance_accepte_et_lien_interne_refuse(tmp_path):
    """Un alias au-dessus de la confiance est accepté ; lien interne, même suivi de .., toujours FAIL."""
    reel = tmp_path / 'reel'
    prive = reel / 'prive'
    prive.mkdir(parents=True)
    fichier = prive / 'fixture.txt'
    fichier.write_text('abc')
    alias = tmp_path / 'alias'
    alias.symlink_to(reel, target_is_directory=True)
    assert sans_lien(alias / 'prive/fixture.txt', racine=alias / 'prive') == fichier
    lien = prive / 'interne'
    lien.symlink_to(tmp_path, target_is_directory=True)
    for chemin in (lien / 'sortie', lien / '..' / 'sortie'):
        with pytest.raises(ValueError, match='symbolique'):
            sans_lien(chemin, racine=prive)
    with pytest.raises(ValueError, match='symbolique'):
        sans_lien(lien, racine=lien)
    assert fichier.read_text() == 'abc'


def test_resolution_atelier_independante_du_cwd_et_priorites(tmp_path, monkeypatch):
    """Défaut dépôt/prive, puis variable puis argument ; aucune création, chemins sans unité."""
    monkeypatch.delenv('BENCHAB_PRIVE', raising=False)
    monkeypatch.chdir(tmp_path)
    assert atelier_prive() == racine_depot() / 'prive'
    choisi = tmp_path / 'choisi'
    monkeypatch.setenv('BENCHAB_PRIVE', str(choisi))
    assert atelier_prive() == choisi
    explicite = tmp_path / 'explicite'
    assert atelier_prive(explicite) == explicite
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('option', ['--help', '--version'])
def test_aide_version_hors_depot_ne_creent_rien(tmp_path, option):
    """Aide/version hors dépôt : code zéro, aucun atelier, verrou ou mesure créé."""
    cwd = tmp_path / 'ailleurs'
    cwd.mkdir()
    choisi = tmp_path / 'choisi'
    r = _cli(cwd, choisi, option)
    assert r.returncode == 0, r.stderr
    assert not choisi.exists() and list(cwd.iterdir()) == []
    r = _cli(cwd, choisi, '--prive', str(tmp_path / 'autre'), option)
    assert r.returncode == 0, r.stderr
    assert not (tmp_path / 'autre').exists()


def test_seul_init_cree_atelier_cli_absent_et_verrou_reste_au_choix(tmp_path):
    """Séance sur atelier absent FAIL sans écriture ; init explicite puis séance utilisent le même choix."""
    cwd = tmp_path / 'ailleurs'
    cwd.mkdir()
    choisi = tmp_path / 'choisi'
    r = _cli(cwd, choisi, 'seance', 'debut')
    assert r.returncode != 0 and 'Atelier absent' in r.stderr
    assert not choisi.exists() and list(cwd.iterdir()) == []
    r = _cli(cwd, choisi, 'init')
    assert r.returncode == 0, r.stderr
    assert (choisi / 'benchab.sqlite').is_file()
    r = _cli(cwd, choisi, 'seance', 'debut')
    assert r.returncode == 0, r.stderr
    assert not (cwd / 'prive').exists()
    r = _cli(cwd, choisi, 'seance', 'etat')
    assert r.returncode == 0
    assert json.loads(r.stdout)['lot'] is None


def test_base_ouverture_sans_creation_refuse_avant_mkdir(tmp_path):
    """Mode creer=False sur chemin absent : FAIL, aucun dossier ni fichier de base créé."""
    from benchab.donnees.base import Base

    cible = tmp_path / 'absent'
    with pytest.raises(FileNotFoundError, match='Atelier absent'):
        Base(cible, creer=False)
    assert not cible.exists()


def test_exportable_accepte_temporaire_natif_et_preserve_source(tmp_path):
    """Export STL sous temporaire natif légitime ; source en mm intacte, liens internes toujours contrôlés."""
    import trimesh

    from benchab.fabrication.exportable import main

    prive = tmp_path / 'prive'
    prive.mkdir()
    source, sortie = prive / 'source.stl', prive / 'sortie.stl'
    trimesh.creation.box((10, 10, 10)).export(source)
    avant = source.read_bytes()
    assert main([str(source), '--sortie', str(sortie)], prive=prive) == 0
    assert sortie.is_file() and source.read_bytes() == avant


def test_atelier_etranger_ne_cree_aucun_dossier_et_choix_vide_refuse(tmp_path):
    """Atelier étranger et choix vide FAIL avant mutation ; aucun schéma, dossier ou verrou créé."""
    import sqlite3

    with pytest.raises(ValueError, match="vide"):
        atelier_prive("")
    choisi = tmp_path / "etranger"
    choisi.mkdir()
    fichier = choisi / "benchab.sqlite"
    with sqlite3.connect(fichier) as db:
        db.execute("CREATE TABLE personnelle (valeur TEXT)")
    avant = fichier.read_bytes()
    r = _cli(tmp_path, choisi, "etat")
    assert r.returncode != 0 and "étrangère" in r.stderr
    assert list(choisi.iterdir()) == [fichier]
    assert fichier.read_bytes() == avant


@pytest.mark.parametrize("arguments", [("projet", "demo", "--help"),
                                      ("conception", "cad-engine", "--", "--help")])
def test_aide_deleguee_sans_base_et_sans_mesure(tmp_path, arguments):
    """Aide métier avant/après init : code zéro, aucun fichier ni mesure supplémentaire, aucune commande machine."""
    choisi = tmp_path / "prive"
    cwd = tmp_path / "ailleurs"
    cwd.mkdir()
    r = _cli(cwd, choisi, *arguments)
    assert r.returncode == 0 and "usage:" in r.stdout, r.stderr
    assert not choisi.exists()
    assert _cli(cwd, choisi, "init").returncode == 0
    avant = {p.relative_to(choisi).as_posix(): p.read_bytes()
             for p in choisi.rglob("*") if p.is_file()}
    r = _cli(cwd, choisi, *arguments)
    assert r.returncode == 0 and "usage:" in r.stdout, r.stderr
    apres = {p.relative_to(choisi).as_posix(): p.read_bytes()
             for p in choisi.rglob("*") if p.is_file()}
    assert avant == apres


def test_prive_explicite_transmis_aux_modules_et_environnement_restaure(tmp_path, monkeypatch):
    """Le module enfant voit le choix explicite, puis l'environnement initial revient ; aucun autre atelier créé."""
    from benchab import cli
    from benchab.controles import debit_gcode
    from benchab.donnees.base import Base

    choisi, autre = tmp_path / "choisi", tmp_path / "autre"
    Base(choisi, creer=True).fermer()
    monkeypatch.setenv("BENCHAB_PRIVE", str(autre))
    observe = []

    def controler(_arguments):
        observe.append(atelier_prive())
        return 0

    monkeypatch.setattr(debit_gcode, "main", controler)
    assert cli.main(["--prive", str(choisi), "controle", "debit-gcode", "fixture.3mf"]) == 0
    assert observe == [choisi]
    assert os.environ["BENCHAB_PRIVE"] == str(autre)
    assert not autre.exists()


def test_aide_controle_historique_ne_lance_pas_analyse_ni_rapport(tmp_path, monkeypatch, capsys):
    """Un contrôle ignorant --help n'est jamais exécuté : aide zéro, aucun atelier ni rapport créé."""
    from zipfile import ZipFile

    from benchab import cli
    from benchab.controles import debit_gcode

    choisi = tmp_path / "absent"
    fichier = tmp_path / "fixture.3mf"
    sortie = tmp_path / "resultat.json"
    with ZipFile(fichier, "w") as z:
        z.writestr("Metadata/plate_1.gcode", "M83\nG1 X0 Y0 Z.2\nG1 X10 E1 F600\n")
    argv = ["controle", "debit-gcode", str(fichier), "--sortie", str(sortie), "--help"]
    r = _cli(tmp_path, choisi, *argv)
    assert r.returncode == 0 and "usage:" in r.stdout, r.stderr
    assert not choisi.exists() and not sortie.exists()

    def interdit(*_args, **_kwargs):
        raise AssertionError("aucune fonction métier exécutée pour l’aide")

    monkeypatch.setattr(debit_gcode, "main", interdit)
    assert cli.main(["--prive", str(choisi), *argv]) == 0
    assert "usage:" in capsys.readouterr().out
    assert not choisi.exists() and not sortie.exists()
