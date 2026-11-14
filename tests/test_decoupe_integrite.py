"""La découpe d'un 3MF déclare ses différences avant sérialisation, jamais après sa sortie."""

import json
from zipfile import ZipFile

import pytest
import trimesh

from benchab import integrite_3mf
from benchab.conception.decoupe import atelier
from benchab.fabrication import anycubic


def _source(tmp_path):
    source = tmp_path / 'source.3mf'
    mesh = trimesh.creation.box((40, 40, 40))
    anycubic.ecrire_3mf(
        [{'name': 'Origine', 'items': [{'name': 'Cube', 'mesh': mesh, 'extruder': 1,
                                      'x': 100, 'y': 100, 'settings': {'top_shell_layers': '8'}}]}],
        {'top_shell_layers': '5'}, source, nommer_source=True,
    )
    return source


def test_export_3mf_compare_toutes_les_portees_et_garde_sa_declaration(tmp_path):
    """Transforme un cube 40 mm ; PASS d'intégrité ne valide jamais son impression physique."""
    source = _source(tmp_path)
    original = source.read_bytes()
    resultat = atelier.calculer(atelier.nouveau_plan(source, nombre=2))
    sortie = atelier.exporter(resultat, tmp_path / 'decoupe', '3mf')
    rapport = json.loads((sortie / 'decoupe.json').read_text())
    for nom, controles in rapport['controles_export'].items():
        preuve = controles['integrite_source']
        assert preuve['statut'] == 'PASS' and preuve['declaration_avant_serialisation']
        assert integrite_3mf.verifier(source, sortie / (nom + '.3mf'), preuve['differences'])['statut'] == 'PASS'
        assert any(d['chemin'] == 'objet/Cube/top_shell_layers' for d in preuve['differences'])
    assert rapport['reglages_impression']['export'].startswith('NON PRÊT')
    assert source.read_bytes() == original


def test_mutation_non_prevue_refuse_l_export_entier(tmp_path, monkeypatch):
    """Injecte 5000 mm/s après sérialisation ; exige FAIL et absence de livraison, sans moteur slicer."""
    source = _source(tmp_path)
    resultat = atelier.calculer(atelier.nouveau_plan(source, nombre=2))
    verifier = integrite_3mf.verifier

    def corrompre(avant, apres, permis=()):
        with ZipFile(apres) as archive:
            membres = {i.filename: archive.read(i) for i in archive.infolist()}
        projet = json.loads(membres['Metadata/project_settings.config'])
        projet['outer_wall_speed'] = '5000'
        membres['Metadata/project_settings.config'] = json.dumps(projet).encode()
        with ZipFile(apres, 'w') as archive:
            for nom, contenu in membres.items():
                archive.writestr(nom, contenu)
        return verifier(avant, apres, permis)

    monkeypatch.setattr(integrite_3mf, 'verifier', corrompre)
    with pytest.raises(ValueError, match='Intégrité'):
        atelier.exporter(resultat, tmp_path / 'refuse', '3mf')
    assert not (tmp_path / 'refuse').exists()
