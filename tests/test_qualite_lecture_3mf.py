"""Conserve l’ordre des plaques des rapports et refuse les G-code 3MF ambigus ou vides."""

import json
from zipfile import ZipFile

from benchab.archives_3mf import Archive3MF
from benchab.qualite import qualite


def _ecrire_tranche(path, *, gcode, alias=False, plaques=None):
    """Écrit des plaques synthétiques avec mesures exploitables et réglages typés."""
    plaques = plaques or [("Plaque A", 60, 1)]
    infos = "<config>" + "".join(
        f'<plate><metadata key="index" value="{index}"/>'
        f'<metadata key="prediction" value="{secondes}"/>'
        f'<metadata key="weight" value="{grammes}"/></plate>'
        for index, (_nom, secondes, grammes) in enumerate(plaques, 1)
    ) + "</config>"
    objets = "<config>" + "".join(
        f'<object id="{index}"><metadata key="name" value="Objet {index}"/></object>'
        for index in range(1, len(plaques) + 1)
    ) + "".join(
        f'<plate><metadata key="plater_name" value="{nom}"/>'
        f'<metadata key="object_id" value="{index}"/></plate>'
        for index, (nom, _secondes, _grammes) in enumerate(plaques, 1)
    ) + "</config>"
    with ZipFile(path, "w") as archive:
        archive.writestr("Metadata/slice_info.config", infos)
        archive.writestr("Metadata/model_settings.config", objets)
        archive.writestr("Metadata/project_settings.config", '{"reduce_crossing_wall":"1"}')
        for index in range(1, len(plaques) + 1):
            archive.writestr(f"Metadata/plate_{index}.gcode", gcode)
        if alias:
            archive.writestr("Metadata/plate_01.gcode", "G1 X2\n")


def test_objets_3mf_conserve_ordre_noms_objets_et_plaques(tmp_path):
    archive = tmp_path / "plaques.3mf"
    with ZipFile(archive, "w") as paquet:
        paquet.writestr(
            "Metadata/model_settings.config",
            '<config><object id="1"><metadata key="name" value="Pièce A"/></object>'
            '<object id="2"><metadata key="name" value="Pièce B"/></object>'
            '<plate><metadata key="plater_name" value="S"/>'
            '<metadata key="object_id" value="2"/><metadata key="object_id" value="1"/></plate>'
            '<plate><metadata key="plater_name" value="M"/><metadata key="object_id" value="1"/></plate>'
            '<plate><metadata key="plater_name" value="L"/><metadata key="object_id" value="2"/></plate></config>',
        )
    assert qualite.objets_3mf(archive) == [
        {"nom": "S", "objets": ["Pièce B", "Pièce A"]},
        {"nom": "M", "objets": ["Pièce A"]},
        {"nom": "L", "objets": ["Pièce B"]},
    ]


def test_niveau3_conserve_ordre_et_categories_du_rapport_sml(tmp_path):
    """Le rapport conserve les plaques S/M/L dans l'ordre déclaré et leurs seuils."""
    tranche = tmp_path / "tranche.3mf"
    _ecrire_tranche(tranche, gcode="G1 X1\n", plaques=[("S", 1800, 1), ("M", 7200, 2), ("L", 18000, 3)])
    cfg = tmp_path / "qualite.json"
    cfg.write_text(
        json.dumps({
            "tranche_3mf": tranche.name,
            "placement": "absent.json",
            "plaques_3mf": "absent.3mf",
            "objectifs_duree_h": {"courte": 1, "moyenne": 3, "longue": 6},
        }),
        encoding="utf-8",
    )

    qualite.niveau3(cfg)
    rapport = json.loads((tmp_path / "preuves/qualite/plaques.json").read_text(encoding="utf-8"))
    assert [(plaque["nom"], plaque["categorie"]) for plaque in rapport] == [
        ("S", "courte"), ("M", "moyenne"), ("L", "longue"),
    ]


def test_niveau3_refuse_index_gcode_ambigu(tmp_path):
    tranche = tmp_path / "tranche.3mf"
    _ecrire_tranche(tranche, gcode="G1 X1\n", alias=True)
    cfg = tmp_path / "qualite.json"
    cfg.write_text(
        json.dumps({"tranche_3mf": tranche.name, "placement": "absent.json", "plaques_3mf": "absent.3mf"}),
        encoding="utf-8",
    )

    assert qualite.niveau3(cfg) == qualite.KO
    controles = json.loads((tmp_path / "preuves/qualite/niveau3.json").read_text(encoding="utf-8"))["controles"]
    resultat, = [item for item in controles if item["controle"] == "résultats du tranchage"]
    assert resultat["statut"] == qualite.KO
    assert "dupliqué" in resultat["detail"]


def test_niveau3_refuse_gcode_de_plaque_vide(tmp_path):
    tranche = tmp_path / "tranche.3mf"
    _ecrire_tranche(tranche, gcode=" \n\t\n")
    cfg = tmp_path / "qualite.json"
    cfg.write_text(
        json.dumps({"tranche_3mf": tranche.name, "placement": "absent.json", "plaques_3mf": "absent.3mf"}),
        encoding="utf-8",
    )

    assert qualite.niveau3(cfg) == qualite.KO
    controles = json.loads((tmp_path / "preuves/qualite/niveau3.json").read_text(encoding="utf-8"))["controles"]
    resultat, = [item for item in controles if item["controle"] == "résultats du tranchage"]
    assert resultat["statut"] == qualite.KO
    assert "G-code vide" in resultat["detail"]


def test_niveau3_lit_reglages_types_une_fois(tmp_path, monkeypatch):
    tranche = tmp_path / "tranche.3mf"
    _ecrire_tranche(tranche, gcode="G1 X1\n")
    cfg = tmp_path / "qualite.json"
    cfg.write_text(
        json.dumps({"tranche_3mf": tranche.name, "placement": "absent.json", "plaques_3mf": "absent.3mf"}),
        encoding="utf-8",
    )
    original = Archive3MF.reglages
    appels = []

    def compter(archive, membre="Metadata/project_settings.config"):
        appels.append(membre)
        return original(archive, membre)

    monkeypatch.setattr(Archive3MF, "reglages", compter)
    qualite.niveau3(cfg)
    assert appels == ["Metadata/project_settings.config"]
