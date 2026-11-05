"""La découpe utilise l'atelier privé explicite et l'entrée mesurée commune."""

import json
import sqlite3

import trimesh

from benchab.cli import main


def test_proposition_et_export_mesures_dans_atelier_explicite(tmp_path, capsys, atelier_initialise):
    """Vérifie proposition et export mesures dans atelier explicite ; atelier initialisé, aucune machine touchée."""
    prive = tmp_path / "prive"
    source = tmp_path / "source.stl"
    trimesh.creation.box((40, 40, 40)).export(source)
    plan = prive / "choix.json"
    assert (
        main(
            [
                "--prive",
                str(prive),
                "conception",
                "decoupe",
                "--",
                "proposer",
                str(source),
                "--unites",
                "mm",
                "--pieces",
                "2",
                "--sortie",
                str(plan),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert json.loads(plan.read_text())["nombre_pieces"] == 2
    export = prive / "decoupe-modele"
    assert (
        main(["--prive", str(prive), "conception", "decoupe", "--", "calculer", str(plan), "--sortie", str(export)])
        == 0
    )
    assert (export / "decoupe.json").is_file()
    with sqlite3.connect(prive / "benchab.sqlite") as base:
        mesures = base.execute("SELECT duree_s,code FROM mesures").fetchall()
    assert len(mesures) == 2
    assert all(duree > 0 and code == 0 for duree, code in mesures)
