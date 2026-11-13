"""Validation privée de l’aperçu avant le tranchage d’une candidate."""

import json

import pytest

from benchab.fabrication.trancher import trancher_fichier
from benchab.projets import validation_apercu
from benchab.qualite.empreintes import sha256_fichier


def test_validation_refuse_peremption_apercu_et_source(tmp_path):
    """Les deux empreintes doivent correspondre à la décision déclarée."""
    source = tmp_path / "candidate.3mf"
    apercu = tmp_path / "apercu.html"
    fiche = tmp_path / "validation.json"
    source.write_bytes(b"3mf temoin")
    apercu.write_bytes(b"apercu temoin")
    fiche.write_text(json.dumps({
        "schema": 1, "decision": "VALIDÉ", "source_sha256": sha256_fichier(source),
        "apercu": {"fichier": apercu.name, "sha256": sha256_fichier(apercu)},
    }))
    assert validation_apercu.verifier(source, fiche, prive=tmp_path)["statut"] == "PASS"
    apercu.write_bytes(b"apercu modifie")
    with pytest.raises(ValueError, match="empreinte"):
        validation_apercu.verifier(source, fiche, prive=tmp_path)


def test_tranchage_candidate_refuse_approbation_en_attente_avant_le_moteur(tmp_path):
    """La garde de la candidate intervient avant toute invocation native."""
    source = tmp_path / "candidate.3mf"
    source.write_bytes(b"fixture")
    fiche = tmp_path / "candidat.json"
    fiche.write_text('{"schema": 1, "decision": "EN ATTENTE", "source_sha256": "", "apercu": null}')
    with pytest.raises(ValueError, match="VALIDÉ"):
        trancher_fichier(source, tmp_path, prive=tmp_path, validation_visuelle=fiche)
