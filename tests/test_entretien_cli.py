"""Contrats CLI de l'entretien issus d'un historique privé synthétique."""

import hashlib
import json
from pathlib import Path

import pytest

from benchab import cli


def _historique():
    """Construit un historique fictif ; aucun seuil n'est réputé fabricant."""
    return {
        "schema": 1,
        "couverture": "COMPLETE",
        "evenements": [
            {
                "id": "m-1", "type": "entretien", "date": "2026-01-01T09:00:00+00:00",
                "organe": "extrudeur", "compteur": "filament", "valeur": 0, "unite": "g",
                "source": "observation fictive", "preuve": "DECLARATION_HUMAINE",
            },
            {
                "id": "u-1", "type": "usage", "date": "2026-01-02T09:00:00+00:00",
                "organe": "extrudeur", "compteur": "filament", "valeur": 12, "unite": "g",
                "source": "mesure fictive", "preuve": "MESURE",
            },
        ],
        "seuils": [{
            "organe": "extrudeur", "compteur": "filament", "maximum": 10,
            "unite": "g", "source": "seuil fictif explicite",
        }],
    }


def _ecrire_historique(racine: Path, donnees=None) -> Path:
    """Écrit une entrée jetable sous la racine privée de test."""
    chemin = racine / "historique.json"
    chemin.write_text(json.dumps(donnees or _historique()), encoding="utf-8")
    return chemin


def test_commande_lit_historique_explicitement_et_n_ouvre_pas_base(tmp_path, monkeypatch, capsys):
    """La commande calcule depuis le fichier demandé, empreinte incluse, sans Base SQLite."""
    prive = tmp_path / "prive"
    prive.mkdir()
    historique = _ecrire_historique(prive)

    def base_interdite(*args, **kwargs):
        raise AssertionError("SQLite ne doit pas être ouvert")

    monkeypatch.setattr(cli, "Base", base_interdite)
    code = cli.main(["--prive", str(prive), "controle", "entretien", "--historique", str(historique)])
    rapport = json.loads(capsys.readouterr().out)
    assert code == 1
    assert rapport["statut"] == "WARN"
    assert rapport["source"]["sha256"] == hashlib.sha256(historique.read_bytes()).hexdigest()
    assert rapport["compteurs"][0]["valeur"] == 12


def test_sortie_optionnelle_est_privee_neuve_et_conserve_le_sha(tmp_path, capsys):
    """La preuve JSON privée est neuve, complète et liée aux octets d'entrée."""
    prive = tmp_path / "prive"
    prive.mkdir()
    historique = _ecrire_historique(prive)
    sortie = prive / "rapports" / "entretien.json"
    code = cli.main([
        "--prive", str(prive), "controle", "entretien", "--historique", str(historique), "--sortie", str(sortie),
    ])
    assert code == 1
    assert json.loads(sortie.read_text(encoding="utf-8"))["source"]["sha256"]
    assert json.loads(capsys.readouterr().out)["statut"] == "WARN"

    code = cli.main([
        "--prive", str(prive), "controle", "entretien", "--historique", str(historique), "--sortie", str(sortie),
    ])
    assert code == 2


def test_periode_incomplete_et_entrees_dupliquees_restent_conservatrices(tmp_path, capsys):
    """Les doublons identiques sont dédoublonnés et les périodes lacunaires ne sont pas PASS."""
    prive = tmp_path / "prive"
    prive.mkdir()
    donnees = _historique()
    donnees["evenements"].append(dict(donnees["evenements"][1]))
    donnees["couverture"] = "INCOMPLETE"
    historique = _ecrire_historique(prive, donnees)
    code = cli.main(["--prive", str(prive), "controle", "entretien", "--historique", str(historique)])
    rapport = json.loads(capsys.readouterr().out)
    assert code == 2
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert len(rapport["compteurs"][0]["sources_evenements"]) == 2


def test_historique_hors_prive_et_cles_json_dupliquees_refuses(tmp_path, capsys):
    """La source explicite reste privée et le parseur refuse les clés JSON ambigües."""
    prive = tmp_path / "prive"
    prive.mkdir()
    dehors = _ecrire_historique(tmp_path)
    code = cli.main(["--prive", str(prive), "controle", "entretien", "--historique", str(dehors)])
    assert code == 2

    ambigu = prive / "ambigu.json"
    ambigu.write_text('{"schema":1,"schema":1,"evenements":[],"seuils":[]}', encoding="utf-8")
    code = cli.main(["--prive", str(prive), "controle", "entretien", "--historique", str(ambigu)])
    assert code == 2

    ambigu.write_text('{"schema":true,"evenements":[],"seuils":[]}', encoding="utf-8")
    code = cli.main(["--prive", str(prive), "controle", "entretien", "--historique", str(ambigu)])
    assert code == 2
    assert "refusé" in capsys.readouterr().err


@pytest.mark.parametrize("date", ["sans-date", "9999-12-31T23:59:59-23:59"])
def test_date_invalide_n_est_jamais_ignoree_avant_le_dernier_entretien(tmp_path, capsys, date):
    """Une date invalide, y compris UTC hors limites, empêche un PASS ou WARN calculé."""
    prive = tmp_path / "prive"
    prive.mkdir()
    donnees = _historique()
    usage_inconnu = dict(donnees["evenements"][1], id="usage-inconnu", date=date, valeur=1)
    donnees["evenements"].insert(0, usage_inconnu)
    historique = _ecrire_historique(prive, donnees)

    code = cli.main(["--prive", str(prive), "controle", "entretien", "--historique", str(historique)])

    rapport = json.loads(capsys.readouterr().out)
    assert code == 2
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["compteurs"][0]["valeur"] is None


def test_preuve_non_chaine_devient_non_verifiee_sans_traceback(tmp_path, capsys):
    """Une preuve JSON mal typée ne provoque pas de TypeError."""
    prive = tmp_path / "prive"
    prive.mkdir()
    donnees = _historique()
    donnees["evenements"][0]["preuve"] = []
    historique = _ecrire_historique(prive, donnees)

    code = cli.main(["--prive", str(prive), "controle", "entretien", "--historique", str(historique)])

    rapport = json.loads(capsys.readouterr().out)
    assert code == 2
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert "TypeError" not in capsys.readouterr().err


def test_erreur_technique_de_lecture_renvoie_code_2(tmp_path, capsys):
    """Un refus technique a son code propre, différent du WARN de seuil."""
    prive = tmp_path / "prive"
    prive.mkdir()
    invalide = prive / "historique.json"
    invalide.write_text("{", encoding="utf-8")

    code = cli.main(["--prive", str(prive), "controle", "entretien", "--historique", str(invalide)])

    assert code == 2
    assert "refusé" in capsys.readouterr().err


def test_nombre_json_hors_domaine_ne_produit_pas_un_rapport(tmp_path, capsys):
    """Un exposant hors domaine ne publie pas un seuil infini dans le JSON."""
    prive = tmp_path / "prive"
    prive.mkdir()
    historique = _ecrire_historique(prive)
    texte = historique.read_text()
    assert '"maximum": 10' in texte
    historique.write_text(texte.replace('"maximum": 10', '"maximum": 1e999'), encoding="utf-8")

    code = cli.main(["--prive", str(prive), "controle", "entretien", "--historique", str(historique)])

    sortie = capsys.readouterr()
    assert code == 2
    assert sortie.out == ""
    assert "non fini" in sortie.err


def test_dix_dixiemes_atteignent_le_seuil_declare(tmp_path, capsys):
    """Dix consommations de 0,1 g donnent WARN au seuil déclaré de 1 g."""
    prive = tmp_path / "prive"
    prive.mkdir()
    donnees = _historique()
    entretien, usage = donnees["evenements"]
    donnees["evenements"] = [entretien] + [{**usage, "id": f"usage-{i}", "valeur": 0.1} for i in range(10)]
    donnees["seuils"][0]["maximum"] = 1
    historique = _ecrire_historique(prive, donnees)
    assert cli.main(["--prive", str(prive), "controle", "entretien", "--historique", str(historique)]) == 1
    rapport = json.loads(capsys.readouterr().out)
    assert rapport["compteurs"][0]["valeur"] == 1
    assert rapport["statut"] == "WARN"
