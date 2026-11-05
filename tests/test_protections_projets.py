"""Protection des rapports et fichiers de préparation avec projets synthétiques."""

import json
from pathlib import Path

import pytest

from benchab.projets.protections import protections


def _json(path: Path, value):
    path.write_text(json.dumps(value))
    return path


def test_consolide_livrables_verrou_preparations_et_qualite(tmp_path):
    _json(tmp_path / "LIVRABLES.json", {"fichiers": [{"chemin": "livrable.stl", "sha256": "a" * 64}]})
    _json(
        tmp_path / "VERROU-REIMPRESSION.json",
        {"etat": "actif", "fichiers": {"gel.stl": "b" * 64}},
    )
    preparation = tmp_path / "preparations/P01"
    preparation.mkdir(parents=True)
    (preparation / "tranche.3mf").write_bytes(b"synthetic")
    (preparation / "temp.lock").write_text("lock")
    (preparation / ".cache").write_text("cache")
    _json(
        tmp_path / "qualite.json",
        {
            "stl_assemblage": "assemblage",
            "stl_impression": "impression",
            "protegees": {
                "Piece": {
                    "fichier": "preuve.json",
                    "sha256": "c" * 64,
                    "sha256_assemblage": "d" * 64,
                    "sha256_impression": "e" * 64,
                }
            },
            "suivi": {"references_imprimees": [{"fichiers": [{"chemin": "reference.stl", "sha256": "f" * 64}]}]},
        },
    )

    result = protections(tmp_path)

    assert result["livrable.stl"] == "a" * 64
    assert result["gel.stl"] == "b" * 64
    assert result["preparations/P01/tranche.3mf"] is None
    assert "preparations/P01/temp.lock" not in result
    assert "preparations/P01/.cache" not in result
    assert result["preuve.json"] == "c" * 64
    assert result["assemblage/Piece.stl"] == "d" * 64
    assert result["impression/Piece.stl"] == "e" * 64
    assert result["reference.stl"] == "f" * 64


def test_refuse_empreintes_protegees_contradictoires(tmp_path):
    _json(tmp_path / "LIVRABLES.json", {"fichiers": [{"chemin": "reference.stl", "sha256": "a" * 64}]})
    _json(
        tmp_path / "qualite.json",
        {"suivi": {"references_historiques": [{"fichiers": [{"chemin": "reference.stl", "sha256": "b" * 64}]}]}},
    )

    with pytest.raises(ValueError, match="empreintes protégées contradictoires"):
        protections(tmp_path)


def test_verrou_inactif_ne_gel_pas_ses_fichiers(tmp_path):
    _json(
        tmp_path / "VERROU-REIMPRESSION.json",
        {"etat": "inactif", "fichiers": {"ancien.stl": "a" * 64}},
    )

    assert protections(tmp_path) == {}
