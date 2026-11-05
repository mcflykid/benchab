"""Géométries synthétiques ; aucune fiche de projet active."""

import json

import pytest

from benchab.qualite import qualite as Q


def fixture(base):
    """Deux pièces génériques : une protégée, une à fabriquer."""
    import hashlib
    import zipfile

    import trimesh

    c = base / "candidats"
    c.mkdir()
    models = {}
    for name in ("Reference", "Piece"):
        mesh = trimesh.creation.box((10, 10, 4))
        mesh.apply_translation(-mesh.bounds[0])
        path = c / (name + ".stl")
        mesh.export(path)
        models[name] = {
            "stl_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "print_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    (c / "assemblage.json").write_text(
        json.dumps({"models": models, "parts": [{"name": n, "model": n} for n in models]})
    )
    with zipfile.ZipFile(c / "plaques.3mf", "w") as z:
        z.writestr(
            "Metadata/model_settings.config",
            '<config><object id="1"><metadata key="name" value="Piece"/></object><plate>'
            '<metadata key="plater_name" value="Plaque"/><model_instance>'
            '<metadata key="object_id" value="1"/></model_instance></plate></config>',
        )
    h = models["Reference"]["stl_sha256"]
    p = base / "qualite.json"
    p.write_text(
        json.dumps(
            {
                "manifeste": "candidats/assemblage.json",
                "stl_assemblage": "candidats",
                "stl_impression": "candidats",
                "plaques_3mf": "candidats/plaques.3mf",
                "protegees": {
                    "Reference": {"sha256_assemblage": h, "sha256_impression": h, "instances": ["Reference"]}
                },
            }
        )
    )
    return p


def test_niveau1_protection_empreintes_et_instances(tmp_path):
    cfg_path = fixture(tmp_path)
    assert Q.niveau1(cfg_path) != Q.KO
    cfg = json.loads(cfg_path.read_text())
    modele = next(iter(cfg["protegees"]))
    autre = tmp_path / ".qualite-test.json"
    falsifie = json.loads(cfg_path.read_text())
    falsifie["protegees"][modele]["sha256_impression"] = "0" * 64
    autre.write_text(json.dumps(falsifie))
    assert Q.niveau1(autre) == Q.KO
    manquant = json.loads(cfg_path.read_text())
    manquant["protegees"][modele]["instances"].append("Instance_inexistante")
    autre.write_text(json.dumps(manquant))
    assert Q.niveau1(autre) == Q.KO
    assert Q.niveau1(cfg_path) != Q.KO


@pytest.mark.parametrize(
    "objet,attendu",
    [
        ({"name": "Vis_x", "accroche": {"assise_mm2": 30, "elancement": 5}, "reglages": {"brim_type": "no_brim"}}, 8),
        (
            {
                "name": "Vis_x",
                "accroche": {"assise_mm2": 30, "elancement": 5},
                "reglages": {"brim_type": "outer_only", "brim_width": "8"},
            },
            4,
        ),
        (
            {
                "name": "Plaque",
                "accroche": {"assise_mm2": 5000, "elancement": 0.1},
                "reglages": {"brim_type": "no_brim"},
            },
            0,
        ),
    ],
)
def test_score_accroche_suit_ses_regles(objet, attendu):
    score, _ = Q.score_accroche(objet, {"familles_echecs": ["Vis_"]})
    assert score == attendu
