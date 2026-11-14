"""Les références numériques restent privées ; leur absence en CI est un saut explicite."""

import os
from pathlib import Path

import pytest

from benchab.qualite.references_or import verifier


@pytest.mark.parametrize("reference", ["reference-1", "reference-2", "reference-3"])
def test_references_or_privees_strictes(reference):
    """Relit les références fixes sans unité ni mutation ; absence ou G-code manquant = non exécuté, jamais PASS."""
    # Le privé jetable de l'autouse sert aux écritures des autres tests. Les
    # références fixes sont lues seules (docs/reference/reglages-et-integrite.md).
    prive = Path(os.environ.get("BENCHAB_REFERENCES_OR", Path(__file__).resolve().parents[1] / "prive"))
    rapport = verifier(prive, reference=reference)
    assert rapport["statut"] != "FAIL", rapport
    if not rapport["execute"]:
        pytest.skip("NON EXÉCUTÉ : " + rapport.get("raison", "Références privées absentes"))
    assert rapport["statut"] == "PASS"


def test_reference_detecte_regression_mode_sans_changer_le_fichier(monkeypatch):
    """Simule la lecture du mode sport au lieu du standard ; FAIL sur analyse, empreintes et archives inchangées."""
    from benchab.controles import commandes_machine

    prive = Path(os.environ.get("BENCHAB_REFERENCES_OR", Path(__file__).resolve().parents[1] / "prive"))
    avant = verifier(prive, reference="reference-2")
    if not avant["execute"]:
        pytest.skip("NON EXÉCUTÉ : références privées absentes")
    assert avant["statut"] == "PASS", avant
    original = commandes_machine.reglages.valeur_mode
    monkeypatch.setattr(
        commandes_machine.reglages, "valeur_mode", lambda donnees, cle, mode="standard": original(donnees, cle, "sport")
    )
    apres = verifier(prive, reference="reference-2")
    assert apres["statut"] == "FAIL"
    assert apres["references"][0]["ecarts"] == ["analyse_demarrage"]


def test_calcul_machine_supplementaire_ne_remplace_pas_original(tmp_path, monkeypatch):
    """Le calcul daté est comparé séparément ; toute mutation de ses réglages reste FAIL."""
    import copy
    import json

    from benchab.qualite import references_or

    prive = tmp_path / 'prive'
    dossier = prive / 'references-or'
    dossier.mkdir(parents=True)
    (prive / 'geometrie.3mf').write_bytes(b'geometrie fixture')
    (prive / 'calcul.3mf').write_bytes(b'calcul fixture')
    geom = {'sha256': 'a' * 64, 'reglages': {}, 'commandes': {}, 'analyse_demarrage': None}
    calcul = {
        'sha256': 'b' * 64, 'reglages': {'projet': {'vitesse': '600'}},
        'commandes': {'plate': {'sha256': 'c' * 64, 'nombre': 1}}, 'analyse_demarrage': {'statut': 'PASS'},
    }
    entree = {'id': 'fixture', 'fichier': 'geometrie.3mf', **geom,
              'qualification_machine': {'fichier': 'calcul.3mf', **copy.deepcopy(calcul)}}
    (dossier / 'manifest.json').write_text(json.dumps({'references': [entree]}))
    monkeypatch.setattr(
        references_or, 'relever', lambda p: copy.deepcopy(geom if p.name == 'geometrie.3mf' else calcul)
    )
    resultat = references_or.verifier(prive)
    assert resultat['statut'] == 'PASS'
    assert resultat['validation_physique'] is False
    assert resultat['references'][0]['gcode_original_present'] is False
    assert resultat['references'][0]['qualification_machine'] == 'calcul.3mf'
    calcul['reglages']['projet']['vitesse'] = '780'
    resultat = references_or.verifier(prive)
    assert resultat['statut'] == 'FAIL'
    assert 'qualification_machine.reglages' in resultat['references'][0]['ecarts']
