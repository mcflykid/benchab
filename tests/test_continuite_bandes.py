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


def test_resume_continuite_publie_les_mesures_et_garde_le_verdict(tmp_path, capsys, atelier_initialise):
    """Un résumé de 100 bandes reste court ; le rapport garde le détail et les erreurs."""
    import json

    from benchab.cli import main

    prive = tmp_path / "prive"
    fichier = tmp_path / "paroi.gcode"
    fichier.write_text("M83\n;TYPE:Outer wall\nG1 X0 Y0 Z0.2 E1\nG1 X1 Y0 E1\n")
    commande = ["--prive", str(prive), "controle", "continuite-paroi", str(fichier)]
    options = ["--bandes-z", "0.2", "--z-min", "0", "--z-max", "20"]
    assert main([*commande, "--resume"]) != 0
    assert "--sortie" in capsys.readouterr().err
    assert main([*commande, *options]) == 1  # La micro-extrusion verticale donne WARN.
    detail = json.loads(capsys.readouterr().out)
    sortie = prive / "rapports/continuite-resume.json"
    assert main([*commande, *options, "--resume", "--sortie", str(sortie)]) == detail["code"]
    stdout = capsys.readouterr().out
    resume = json.loads(stdout)
    assert json.loads(sortie.read_bytes()) == detail
    assert resume["rapport"] == str(sortie)
    assert resume["statut"] == detail["statut"] and resume["code"] == detail["code"]
    assert resume["bandes_z"] == len(detail["bandes_z"]) == 100
    assert resume["couches_analysees"] == detail["couches_analysees"]
    assert len(stdout.encode()) < sortie.stat().st_size / 10

    erreur = prive / "rapports/continuite-absente.json"
    commande[4] = str(tmp_path / "absente.gcode")
    assert main([*commande, "--resume", "--sortie", str(erreur)]) == 2
    refuse = json.loads(capsys.readouterr().out)
    assert refuse["avertissements"] == json.loads(erreur.read_bytes())["avertissements"] == ["Fichier G-code absent."]
    assert refuse["source"] == str(tmp_path / "absente.gcode")
