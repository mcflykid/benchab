"""Recette privée de l’export d’un procédé différentiel pour Anycubic Slicer Next."""

import json

from benchab.cli import main


def test_export_procede_reduit_au_delta_et_refuse_ecrasement(tmp_path, atelier_initialise, capsys):
    """Le JSON et sa fiche locale sont produits ensemble, hors des profils installés."""
    prive = tmp_path / "prive"
    parent = tmp_path / "Standard.json"
    parent.write_text(json.dumps({
        "name": "Standard", "type": "process", "from": "system",
        "version": "2.0.0.3", "wall_loops": "2", "layer_height": "0.2",
    }))
    delta = tmp_path / "delta.json"
    delta.write_text(json.dumps({"wall_loops": "3", "layer_height": "0.2"}))
    cible = prive / "rapports"
    arguments = [
        "--prive", str(prive), "machine", "export-profil", "--",
        "--parent", str(parent), "--reglages", str(delta), "--nom", "Procédé local", "--dossier", str(cible),
    ]
    assert main(arguments) == 0
    profil = cible / "Procédé local.json"
    info = cible / "Procédé local.info"
    obtenu = json.loads(profil.read_bytes())
    assert obtenu == {
        "name": "Procédé local", "inherits": "Standard", "from": "User", "type": "process", "version": "2.0.0.3",
        "wall_loops": "3",
    }
    assert "user_id = \n" in info.read_text()
    assert json.loads(capsys.readouterr().out)["import_slicer"] == "NON VÉRIFIÉ"
    avant = profil.read_bytes(), info.read_bytes()
    assert main(arguments) == 2
    assert (profil.read_bytes(), info.read_bytes()) == avant
    assert json.loads(delta.read_bytes())["wall_loops"] == "3"


def test_export_procede_refuse_cle_inconnue_ou_nom_du_parent(tmp_path, atelier_initialise, capsys):
    """Les clés non typées et un nom ambigu sont refusés avant toute publication."""
    prive = tmp_path / "prive"
    parent = tmp_path / "Parent.json"
    parent.write_text(json.dumps({"name": "Parent", "type": "process", "from": "system", "version": "2.0.0.3"}))
    delta = tmp_path / "delta.json"
    cible = prive / "rapports"
    arguments = [
        "--prive", str(prive), "machine", "export-profil", "--",
        "--parent", str(parent), "--reglages", str(delta), "--dossier", str(cible),
    ]
    delta.write_text('{"option_inconnue": "1"}')
    assert main([*arguments, "--nom", "Essai"]) == 2
    assert "option_inconnue" in capsys.readouterr().err
    delta.write_text('{"wall_loops": "3"}')
    assert main([*arguments, "--nom", "parent"]) == 2
    assert "parent" in capsys.readouterr().err
    assert not list(cible.glob("*.info"))
