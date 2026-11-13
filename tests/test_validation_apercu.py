"""Validation privée de l’aperçu avant le tranchage d’une candidate."""

import json

import pytest

from benchab.fabrication.trancher import trancher_fichier
from benchab.projets import validation_apercu
from benchab.qualite.empreintes import sha256_fichier


def _candidat(tmp_path):
    source = tmp_path / "projets" / "piece" / "candidats" / "v1" / "plaques.3mf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"fixture")
    return source


def test_candidat_sans_fiche_refuse_avant_moteur(tmp_path):
    """Le chemin projet déclenche la vérification en usage impression."""
    source = _candidat(tmp_path)
    with pytest.raises(ValueError, match=r"plaques\.3mf\.apercu\.json"):
        trancher_fichier(source, tmp_path, prive=tmp_path)


def test_fiche_candidate_par_defaut(tmp_path):
    """La fiche locale s’applique à sa seule archive de candidate."""
    source = _candidat(tmp_path)
    fiche = source.with_name("plaques.3mf.apercu.json")
    fiche.write_text('{"schema": 1, "decision": "À AJUSTER", "source_sha256": "", "apercu": null}')
    assert validation_apercu.fiche_candidate(source, prive=tmp_path) == fiche
    with pytest.raises(ValueError, match="VALIDÉ"):
        trancher_fichier(source, tmp_path, prive=tmp_path)
    assert validation_apercu.fiche_candidate(tmp_path / "importe.3mf", prive=tmp_path) is None


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
    apercu.write_bytes(b"apercu temoin")
    source.write_bytes(b"3mf modifie")
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
