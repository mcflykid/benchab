"""Synthèses de continuité par hauteur et export privé sans altérer les détails."""
import json

import pytest

from benchab.cli import main
from benchab.donnees.base import Base


def test_bandes_bornes_et_derniere_partielle_preservent_details():
    """Les bornes intérieures sont à droite ; la dernière borne est incluse, en mm."""
    from benchab.gcode.bandes_continuite import ajouter_bandes

    couches = [{'z_mm': z, 'morceaux': 1, 'segments': 2, 'microsegments': 1,
                'fraction_microsegments': 0.5} for z in (0.0, 1.0, 2.0, 2.5)]
    rapport = {'statut': 'WARN', 'couches': couches}
    resultat = ajouter_bandes(rapport, 1.0)
    assert 'bandes_z' not in rapport
    assert resultat['couches'] == couches
    assert [b['couches_analysees'] for b in resultat['bandes_z']] == [1, 1, 2]
    assert resultat['bandes_z'][-1]['z_max_mm'] == 2.5
    assert resultat['bandes_z'][-1]['partielle'] is True
    assert resultat['statut'] == 'WARN'


def test_bandes_intervalle_vide_et_largeur_invalide():
    """Une fenêtre sans couche reste explicitement vide ; aucune bande valide déduite d'un NaN."""
    from benchab.gcode.bandes_continuite import ajouter_bandes

    assert ajouter_bandes({'couches': []}, 1.0)['bandes_z'] == []
    rapport = ajouter_bandes({'couches': []}, 1.0, (0.0, 2.0))
    assert [b['couverture'] for b in rapport['bandes_z']] == ['NON VÉRIFIÉE'] * 2
    for largeur in (0, -1, float('nan'), float('inf'), True, 1e-12, '1', None):
        with pytest.raises(ValueError):
            ajouter_bandes({'couches': []}, largeur, (0.0, 2.0))
    for bornes in ((0, 'x'), (True, 2), 3):
        with pytest.raises(ValueError):
            ajouter_bandes({'couches': []}, 1.0, bornes)


def test_export_cli_identique_au_rapport_et_refus_ecrasement(tmp_path, capsys):
    """La sortie neuve reprend stdout avec source/SHA ; le fichier source et un ancien export restent intacts."""
    prive = tmp_path / 'prive'
    base = Base(prive)
    base.fermer()
    source = prive / 'piece.gcode'
    source.write_text('G90\nM83\n; outer_wall_acceleration = 2000\nM204 S2000\n'
                      ';Z:0.2\n;TYPE:Outer wall\nG0 X0 Y0 Z0.2\nG1 X10 E1 F600\n')
    avant = source.read_bytes()
    sortie = prive / 'rapports/continuite.json'
    args = ['--prive', str(prive), 'controle', 'continuite-paroi', str(source),
            '--bandes-z', '1', '--sortie', str(sortie)]
    assert main(args) == 0
    rapport = json.loads(capsys.readouterr().out)
    assert json.loads(sortie.read_text()) == rapport
    assert rapport['source']['sha256']
    assert rapport['bandes_z'][0]['couches_analysees'] == 1
    sauvegarde = sortie.read_bytes()
    assert main(args) != 0
    assert sortie.read_bytes() == sauvegarde
    assert source.read_bytes() == avant
