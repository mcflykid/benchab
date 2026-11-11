import json
import zipfile
from pathlib import Path

from benchab.controles.regles import controler, main, verifier_nommage


def _archive(path: Path, names=None, plate_names=None, include_gcode=False):
    names = names or ["Piece"]
    plate_names = plate_names or ["Support"]
    model = ["<config>"]
    for index, name in enumerate(names, 1):
        model.append(
            f'<object id="{index}"><metadata key="name" value="{name}"/><metadata key="extruder" value="1"/></object>'
        )
    for index, name in enumerate(plate_names, 1):
        model.append(
            f'<plate><metadata key="plater_id" value="{index}"/>'
            f'<metadata key="plater_name" value="{name}"/>'
            f'<model_instance><metadata key="object_id" value="{index}"/></model_instance></plate>'
        )
    model.append("</config>")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Metadata/model_settings.config", "".join(model))
        archive.writestr("Metadata/project_settings.config", json.dumps({}))
        if include_gcode:
            archive.writestr("Metadata/plate_1.gcode", "G1 X1 Y1 E1\n")
            archive.writestr("Metadata/slice_info.config", "<config/>")
    return path


def test_les_noms_persistes_simples_et_uniques_sont_acceptes(tmp_path):
    source = _archive(tmp_path / "noms.3mf", ["Coque gauche", "Coque-droite"], ["Base", "Couvercle"])

    rapport = verifier_nommage(source)

    assert rapport["statut"] == "PASS"
    assert rapport["noms_plaques"] == ["Base", "Couvercle"]
    assert rapport["noms_objets"] == ["Coque gauche", "Coque-droite"]
    assert "réouverture" in rapport["limite"]


def test_les_noms_dupliques_et_libelles_automatiques_echouent(tmp_path):
    source = _archive(tmp_path / "noms.3mf", ["Piece", "Piece"], ["Plaque 1 (Base)", "Base"])

    rapport = verifier_nommage(source)

    assert rapport["statut"] == "FAIL"
    assert any("dupliqué" in erreur for erreur in rapport["erreurs"])
    assert any("automatique" in erreur for erreur in rapport["erreurs"])


def test_rapport_agrege_ne_cree_aucun_pass_si_debit_non_verifiable(tmp_path, monkeypatch):
    source = _archive(tmp_path / "source.3mf")
    monkeypatch.setattr(
        "benchab.controles.regles.plaques_interface.analyser",
        lambda _source: {"conforme": True, "plaques": []},
    )

    rapport = controler(source)

    assert rapport["statut"] == "NON VÉRIFIÉ" and rapport["code"] == 2
    assert [resultat["controle"] for resultat in rapport["resultats"]] == ["U20", "U22", "U25"]
    assert [resultat["controle"] for resultat in rapport["hors_portee"]] == ["U24", "U36"]
    assert all(r["statut"] == "NON VÉRIFIÉ" and r["motif"] for r in rapport["hors_portee"])
    assert rapport["resultats"][2]["statut"] == "NON VÉRIFIÉ"
    assert any("aucun plafond universel" in limite for limite in rapport["limites"])


def test_main_utilise_base_prive_et_refuse_sortie_exterieure(tmp_path, monkeypatch, capsys):
    source = _archive(tmp_path / "source.3mf")
    prive = tmp_path / "prive"
    monkeypatch.setattr(
        "benchab.controles.regles.plaques_interface.analyser",
        lambda _source: {"conforme": True, "plaques": []},
    )
    monkeypatch.setattr(
        "benchab.controles.regles.debit_gcode.analyser",
        lambda _source: {"conforme": True},
    )

    assert main([str(source)], prive=prive) == 0
    assert (prive / "rapports/regles-lot4.json").is_file()
    rapport = json.loads((prive / "rapports/regles-lot4.json").read_text())
    assert rapport["statut"] == "PASS" and rapport["code"] == 0
    assert rapport["portee"] == {"evalues": ["U20", "U22", "U25"], "hors_portee": ["U24", "U36"]}
    assert all(r["statut"] == "PASS" for r in rapport["resultats"])
    assert all(r["statut"] == "NON VÉRIFIÉ" and r["motif"] for r in rapport["hors_portee"])
    assert main([str(source), "--sortie", str(tmp_path / "public.json")], prive=prive) == 2
    assert "espace privé" in capsys.readouterr().err


def test_main_refuse_un_rapport_etranger_avec_un_diagnostic(tmp_path, capsys):
    """Un fichier déjà présent et sans provenance reste intact."""
    source = _archive(tmp_path / "source.3mf")
    cible = tmp_path / "rapport.json"
    original = b'{"note":"a conserver"}'
    cible.write_bytes(original)

    assert main([str(source), "--sortie", str(cible)], prive=tmp_path) == 2
    sortie = capsys.readouterr()
    assert "Destination existante non reconnue" in sortie.err
    assert sortie.out == ""
    assert cible.read_bytes() == original
