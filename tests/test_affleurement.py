"""Témoins synthétiques d'une languette et de sa rainure dans le repère de montage."""

import json

import pytest

from benchab.cli import main
from benchab.controles.affleurement import mesurer


def declaration(profondeur=3.0, tournee=False):
    """Déclare les enveloppes de deux interfaces prismatiques dans la pose de montage."""
    axes = ["x", "y", "z"] if not tournee else ["x", "z", "y"]
    return {
        "schema": 1, "repere_montage": dict(zip(("longueur", "largeur", "profondeur"), axes)),
        "languette_mm": [15, 4, 3] if not tournee else [15, 3, 4],
        "rainure_mm": [16, 4.2, profondeur] if not tournee else [16, profondeur, 4.2],
        "tolerance_affleurement_mm": 0.02,
    }


@pytest.mark.parametrize(("profondeur", "ecart", "statut"), [(3, 0, "PASS"), (3.2, -.2, "WARN"), (2.8, .2, "WARN")])
def test_cotes_dans_les_deux_poses(profondeur, ecart, statut):
    """Les axes déclarés gouvernent la mesure ; le signe distingue retrait et saillie."""
    a = mesurer(declaration(profondeur))
    assert mesurer(declaration(profondeur, tournee=True)) == a
    assert a["statut"] == statut
    assert a["desaffleurement_mm"] == pytest.approx(ecart)
    assert a["jeu_transversal_mm"] == pytest.approx(.2)
    assert a["jeu_longitudinal_mm"] == pytest.approx(1)
    assert not a["validation_physique"]


def test_refuse_pose_absente_et_signale_interference():
    """Une déclaration incomplète est indéterminée ; une rainure étroite est FAIL."""
    entree = declaration()
    entree["rainure_mm"][1] = 3.9
    assert mesurer(entree)["statut"] == "FAIL"
    del entree["repere_montage"]
    assert mesurer(entree)["statut"] == "NON VÉRIFIÉ"


def test_cli_produit_une_coupe_et_lie_le_sha(tmp_path, capsys, atelier_initialise):
    """La sortie de la CLI identifie la déclaration et illustre la section nominale."""
    source = tmp_path / "montage.json"
    coupe = tmp_path / "coupe.svg"
    source.write_text(json.dumps(declaration()))
    arguments = ["--prive", str(atelier_initialise), "controle", "affleurement", str(source), "--coupe", str(coupe)]
    assert main(arguments) == 0
    assert len(json.loads(capsys.readouterr().out)["source_sha256"]) == 64
    assert "Coupe transversale" in coupe.read_text()


def test_resume_affleurement_conserve_le_refus_dans_le_rapport(tmp_path, capsys, atelier_initialise):
    """Une déclaration incomplète conserve le verdict et le motif sur les deux sorties."""
    source = tmp_path / "montage.json"
    source.write_text('{"schema":1}')
    cible = atelier_initialise / "rapport.json"
    arguments = ["--prive", str(atelier_initialise), "controle", "affleurement", str(source)]
    assert main([*arguments, "--resume", "--sortie", str(cible)]) == 2
    court = json.loads(capsys.readouterr().out)
    complet = json.loads(cible.read_text())
    assert court == {"statut": complet["statut"], "raison": complet["raison"], "rapport": str(cible)}
    assert complet["statut"] == "NON VÉRIFIÉ" and len(complet["source_sha256"]) == 64
