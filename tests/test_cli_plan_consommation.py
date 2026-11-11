"""Recettes CLI synthétiques de lecture seule #77 ; aucun atelier réel ni appareil."""

import hashlib
import json
import os
import sqlite3
import stat
import zipfile
from pathlib import Path

from benchab import cli
from benchab.donnees.base import Base
from benchab.fabrication import preparation as PR
from benchab.projets import plan_consommation
from benchab.projets.envois import enregistrer as enregistrer_envoi
from benchab.projets.preparations import enregistrer as enregistrer_preparation
from benchab.projets.retours import confirmer


def _atelier_retour(prive):
    """Crée un retour complet par les APIs, sans SQL de fixture ni commande machine."""
    base = Base(prive)
    projet = base.prive / "projets/recette-cli"
    fiche = projet / "preparations/P01/PREPARATION.json"
    fiche.parent.mkdir(parents=True)
    lignes = b"T1\nG1 X1 Y1 E1\n"
    fiche.write_text(json.dumps({
        "id": "P01",
        "gcode": {"1": {
            "sha256": hashlib.sha256(lignes).hexdigest(),
            "commandes_sha256": PR.commandes(lignes),
        }},
    }), encoding="utf-8")
    enregistrer_preparation(base, fiche)
    source = Path(prive).parent / "envoi.gcode.3mf"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("Metadata/project_settings.config", json.dumps({"filament_ids": ["f1", "f2"]}))
        archive.writestr("Metadata/plate_1.gcode", "; filament used [g] = 0, 12.5\n" + lignes.decode())
    envoi = enregistrer_envoi(base, projet, "P01", source)
    retour = confirmer(
        base, projet, "reussie", "retour synthétique déclaré", preparation="P01",
        ensemble_complet=True, envois=[envoi["fichier"]],
    )
    identifiant = retour["retour"]["id"]
    base.fermer()
    return identifiant


def _empreintes(racine):
    """Capture noms de fichiers/dossiers et SHA sans suivre de liens."""
    resultat = {}
    for chemin in sorted(Path(racine).rglob("*")):
        info = chemin.lstat()
        relatif = chemin.relative_to(racine).as_posix()
        if stat.S_ISLNK(info.st_mode):
            resultat[relatif] = ("lien", os.readlink(chemin))
        elif stat.S_ISDIR(info.st_mode):
            resultat[relatif] = ("dossier",)
        elif stat.S_ISREG(info.st_mode):
            resultat[relatif] = ("fichier", hashlib.sha256(chemin.read_bytes()).hexdigest())
        else:
            resultat[relatif] = ("special", info.st_mode)
    return resultat


def test_commande_cli_lit_un_retour_sans_base_mesure_ni_ecriture(tmp_path, monkeypatch, capsys):
    """La commande retourne le plan JSON et ne modifie aucune source ni le stock."""
    prive = tmp_path / "prive"
    retour = _atelier_retour(prive)
    avant = _empreintes(prive)
    sqlite_connect = sqlite3.connect
    requetes = []

    class ConnexionObservee(sqlite3.Connection):
        """Trace les PRAGMA et le début du snapshot sur la connexion CLI."""

        def execute(self, sql, *args):
            requetes.append(sql.strip().upper())
            return super().execute(sql, *args)

    def connecter_ro(chemin, *args, **kwargs):
        assert chemin.endswith("?mode=ro")
        assert kwargs.get("uri") is True
        assert kwargs.get("timeout") == 1.0
        kwargs["factory"] = ConnexionObservee
        return sqlite_connect(chemin, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connecter_ro)

    def interdit(*_args, **_kwargs):
        raise AssertionError("aucune Base() ni mesure ne doit être appelée")

    monkeypatch.setattr(cli, "Base", interdit)
    monkeypatch.setattr(cli, "Mesure", interdit)
    monkeypatch.setattr(plan_consommation, "Base", interdit)
    code = cli.main([
        "--prive", str(prive), "controle", "consommation-retour", "--retour", retour,
    ])

    sortie = capsys.readouterr()
    rapport = json.loads(sortie.out)
    assert sortie.err == ""
    assert code == rapport["code"] == 2
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["estimation"]["nature"] == "ESTIMATION_SLICER"
    assert rapport["estimation"]["plaques"][0]["outils"][1]["grammes_estimes"] == 12.5
    assert "PRAGMA QUERY_ONLY = ON" in requetes
    assert "BEGIN" in requetes
    assert _empreintes(prive) == avant


def test_commande_cli_atelier_absent_ne_cree_pas_de_dossier(tmp_path, capsys):
    """Un atelier manquant reste absent et produit un rapport JSON NON VÉRIFIÉ."""
    prive = tmp_path / "absent"

    code = cli.main([
        "--prive", str(prive), "controle", "consommation-retour", "--retour", "retour-inconnu",
    ])

    sortie = capsys.readouterr()
    assert sortie.err == ""
    rapport = json.loads(sortie.out)
    assert code == rapport["code"] == 2
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert not prive.exists()


def test_wal_refuse_avant_connection_sans_sidecar(tmp_path, monkeypatch, capsys):
    """Un fichier WAL est refusé avant l'ouverture en lecture seule et reste inchangé."""
    prive = tmp_path / "prive"
    base = Base(prive)
    base.fermer()
    with sqlite3.connect(prive / "benchab.sqlite") as connexion:
        assert connexion.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    chemin = prive / "benchab.sqlite"
    assert chemin.read_bytes()[18:20] == b"\x02\x02"
    avant = _empreintes(prive)

    def interdit(*_args, **_kwargs):
        raise AssertionError("un fichier WAL ne doit pas être ouvert")

    monkeypatch.setattr(sqlite3, "connect", interdit)
    code = cli.main([
        "--prive", str(prive), "controle", "consommation-retour", "--retour", "retour-inconnu",
    ])

    sortie = capsys.readouterr()
    rapport = json.loads(sortie.out)
    assert sortie.err == ""
    assert code == rapport["code"] == 2
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert _empreintes(prive) == avant
