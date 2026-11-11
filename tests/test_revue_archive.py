"""Le parcours public revoit une archive figée sans lancer de trancheuse."""

import hashlib
import json
from pathlib import Path

import pytest

from benchab.cli import parser
from benchab.fabrication import revue_archive


def _rapport_initial(archive, geometrie_sha=None):
    return {
        "statut": "NON VÉRIFIÉ",
        "code": 2,
        "zones_locales": {
            "tranche_sha256": hashlib.sha256(Path(archive).read_bytes()).hexdigest(),
            "geometrie_sha256": geometrie_sha,
            "zones": [],
        },
    }


def test_cli_expose_le_parcours_archive_deja_tranche():
    """La commande publique distingue la revue d’archive de la chaîne qui tranche."""
    arguments = parser().parse_args([
        "fabrication", "revue-archive", "tranche.3mf", "--declaration", "DECLARATION.json",
        "--sortie", "prive/rapports/envoi.json",
    ])
    assert arguments.commande == "fabrication"
    assert arguments.action == "revue-archive"
    assert arguments.archive == Path("tranche.3mf")
    assert arguments.declaration == Path("DECLARATION.json")


def test_parcours_revue_archive_reutilise_controles_et_preserve_source(tmp_path, monkeypatch):
    """Le parcours orchestre les contrôles sur les octets figés et ne lance aucun slicer."""
    archive = tmp_path / "tranche.3mf"
    archive.write_bytes(b"archive tranchee intacte")
    declaration = tmp_path / "declaration.json"
    declaration.write_text("{}", encoding="utf-8")
    preuve = tmp_path / "preuve-humaine.json"
    preuve.write_text("source revue", encoding="utf-8")
    revue_source = tmp_path / "REVUE.json"
    revue_source.write_text(json.dumps({
        "tranche_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "geometrie_sha256": None,
        "auteur": "relectrice",
        "zones": {"zone-1": {
            "decision": "justifie", "justification": "inspection humaine", "derniere_couche_verifiee": True,
            "auteur": "humain", "preuves": [{
                "fichier": preuve.name, "sha256": hashlib.sha256(preuve.read_bytes()).hexdigest(),
            }],
        }},
    }), encoding="utf-8")
    revue_octets = revue_source.read_bytes()
    sortie = tmp_path / "final.json"
    appels = []

    def controler(*args, **kwargs):
        if kwargs.get("revue_locale") is None:
            appels.append(("initial", args, kwargs))
            return _rapport_initial(archive)
        appels.append(("final", args, kwargs))
        return {"statut": "NON VÉRIFIÉ", "code": 2, "resultats": []}

    def revoir(tranche, envoi, revue, faits):
        appels.append(("revoir", tranche, envoi, revue, faits))
        decisions_humaines = json.loads(Path(revue).read_text())["zones"]
        return {
            "env": json.loads(Path(envoi).read_text()),
            "faits": {}, "auto": {}, "gardees": decisions_humaines,
            "reste": ["zone-1"], "plaques": {},
        }

    def ecrire(res, revue, faits):
        appels.append(("ecrire", revue, faits))
        Path(faits).write_text("{}", encoding="utf-8")
        Path(revue).write_text(json.dumps({
            "tranche_sha256": res["env"]["zones_locales"]["tranche_sha256"],
            "geometrie_sha256": None,
            "zones": res["gardees"],
        }), encoding="utf-8")

    monkeypatch.setattr(revue_archive.envoi, "controler", controler)
    monkeypatch.setattr(revue_archive.revue_locale, "revoir", revoir)
    monkeypatch.setattr(revue_archive.revue_locale, "ecrire", ecrire)
    monkeypatch.setattr(
        "benchab.processus.executer",
        lambda *a, **k: pytest.fail("le parcours d’archive ne doit pas lancer de trancheuse"),
    )

    resultat = revue_archive.executer(
        archive, declaration, sortie=sortie, revue_source=revue_source, prive=tmp_path
    )

    assert [appel[0] for appel in appels] == ["initial", "revoir", "ecrire", "final"]
    assert resultat["statut"] == "NON VÉRIFIÉ"
    assert appels[-1][2]["revue_locale"] == Path(resultat["revue"])
    assert resultat["resume_revue"]["a_revoir"] == 1
    assert Path(resultat["rapport"]).is_file()
    assert Path(resultat["rapport_initial"]).is_file()
    revue_finale = json.loads(Path(resultat["revue"]).read_text())
    assert revue_finale["zones"]["zone-1"]["decision"] == "justifie"
    assert revue_finale["zones"]["zone-1"]["preuves"][0]["sha256"] == hashlib.sha256(preuve.read_bytes()).hexdigest()
    assert resultat["sources_sha256"][str(preuve)] == hashlib.sha256(preuve.read_bytes()).hexdigest()
    assert archive.read_bytes() == b"archive tranchee intacte"
    assert revue_source.read_bytes() == revue_octets


def test_parcours_refuse_revue_humaine_perimee_sans_ecrire(tmp_path, monkeypatch):
    """Une revue liée à d’autres empreintes est refusée sans réécrire la source."""
    archive = tmp_path / "tranche.3mf"
    archive.write_bytes(b"archive nouvelle")
    declaration = tmp_path / "declaration.json"
    declaration.write_text("{}", encoding="utf-8")
    revue = tmp_path / "REVUE.json"
    revue.write_text(json.dumps({
        "tranche_sha256": "0" * 64,
        "geometrie_sha256": None,
        "zones": {"zone-1": {"decision": "à revoir", "auteur": "humain"}},
    }), encoding="utf-8")
    revue_avant = revue.read_bytes()
    ecritures = []
    monkeypatch.setattr(revue_archive.envoi, "controler", lambda *a, **k: _rapport_initial(archive))
    monkeypatch.setattr(revue_archive.revue_locale, "revoir", lambda *a, **k: pytest.fail("revue périmée relue"))
    monkeypatch.setattr(revue_archive.revue_locale, "ecrire", lambda *a, **k: ecritures.append(a))

    with pytest.raises(ValueError, match="empreintes de l’archive ou de la géométrie"):
        revue_archive.executer(
            archive, declaration, sortie=tmp_path / "sortie.json", revue_source=revue, prive=tmp_path
        )

    assert revue.read_bytes() == revue_avant
    assert not ecritures


def test_parcours_refuse_preuve_humaine_perimee(tmp_path, monkeypatch):
    """Une décision dont la preuve a changé est refusée avant toute nouvelle revue."""
    archive = tmp_path / "tranche.3mf"
    archive.write_bytes(b"archive stable")
    declaration = tmp_path / "declaration.json"
    declaration.write_text("{}", encoding="utf-8")
    preuve = tmp_path / "preuve.json"
    preuve.write_text("preuve initiale", encoding="utf-8")
    revue = tmp_path / "REVUE.json"
    revue.write_text(json.dumps({
        "tranche_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "geometrie_sha256": None,
        "zones": {"zone-1": {
            "decision": "justifie", "justification": "examen conclu", "derniere_couche_verifiee": True,
            "preuves": [{"fichier": "preuve.json", "sha256": hashlib.sha256(b"preuve initiale").hexdigest()}],
        }},
    }), encoding="utf-8")
    preuve.write_text("preuve remplacée", encoding="utf-8")
    revue_avant = revue.read_bytes()
    monkeypatch.setattr(revue_archive.envoi, "controler", lambda *a, **k: _rapport_initial(archive))
    monkeypatch.setattr(revue_archive.revue_locale, "revoir", lambda *a, **k: pytest.fail("preuve périmée relue"))

    with pytest.raises(ValueError, match="Preuve de revue source absente ou périmée"):
        revue_archive.executer(
            archive, declaration, sortie=tmp_path / "sortie.json", revue_source=revue, prive=tmp_path
        )

    assert revue.read_bytes() == revue_avant
