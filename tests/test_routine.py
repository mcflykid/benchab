"""La routine reste rejouable et transmet chaque échec, même face à des JSON anciens conformes."""

import json
from zipfile import ZipFile

import pytest
import trimesh

from benchab.controles import debit_gcode, disposition_reelle, plaques_interface, routine, verifier_reglages
from benchab.fabrication.anycubic import ecrire_3mf


def fixture(chemin):
    mesh = trimesh.creation.box((10, 10, 10))
    mesh.apply_translation((5, 5, 5))
    item = {"name": "Piece", "mesh": mesh, "w": 10, "h": 10, "x": 10, "y": 10, "extruder": 1}
    ecrire_3mf(
        [{"name": "Courte", "items": [item]}],
        {
            "filament_type": ["PLA"],
            "filament_colour": ["#FFFFFF"],
            "filament_settings_id": ["Fixture"],
            "filament_ids": ["GFPLA"],
            "filament_id": "GFPLA",
            "filament_max_volumetric_speed": ["32"],
            "filament_diameter": ["1.75"],
            "initial_layer_print_height": "0.2",
            "top_shell_layers": "5",
        },
        chemin,
        cli=True,
    )
    with ZipFile(chemin, "a") as archive:
        archive.writestr(
            "Metadata/slice_info.config",
            (
                '<config><plate><metadata key="index" value="1"/><filament id="1"/>'
                '<object name="Piece" skipped="false"/></plate></config>'
            ),
        )
        archive.writestr(
            "Metadata/plate_1.gcode",
            (
                "G90\nM83\nT0\nEXCLUDE_OBJECT_DEFINE NAME=Piece\nEXCLUDE_OBJECT_START NAME=Piece\n"
                ";LAYER_CHANGE\n;Z:0.2\n;HEIGHT:0.2\n;TYPE:Outer wall\n"
                "G1 X10 Y10 Z0.2 F1200\nG1 X20 Y10 E0.2\nG1 X20 Y20 E0.2\nEXCLUDE_OBJECT_END NAME=Piece\n"
            ),
        )
    placement = chemin.with_suffix(".json")
    placement.write_text(
        json.dumps(
            {
                "plates": [{"name": "Courte"}],
                "objects": [{"name": "Piece", "plate": 1, "xy": [10, 10], "size": [10, 10], "reglages": {}}],
            }
        )
    )
    return placement


def test_routine_complete_rejouable_sans_slicer_et_source_intacte(tmp_path):
    source = tmp_path / "fixture.3mf"
    placement = fixture(source)
    avant = source.read_bytes()
    rapports = [routine.controler(source, source, placement, tmp_path / "rapports") for _ in range(2)]
    assert all(r["statut"] == "PASS" for r in rapports), rapports
    assert rapports[0] == rapports[1]
    assert source.read_bytes() == avant


@pytest.mark.parametrize(
    "mutation,localisation",
    [
        (lambda p: p.clear(), "plates"),
        (lambda p: p.update(schema=2), "schema"),
        (lambda p: p["objects"][0].update(plate=2), "objects[0].plate"),
        (lambda p: p["objects"][0].update(xy=[float("nan"), 1]), "objects[0].xy"),
        (lambda p: p["objects"][0].update(size=[-1, 2]), "objects[0].size"),
        (lambda p: p["objects"][0].update(reglages=[]), "objects[0].reglages"),
        (lambda p: p["plates"].append(dict(p["plates"][0])), "plates[1].name"),
        (lambda p: p.update(assembly_manifest_sha256="invalide"), "assembly_manifest_sha256"),
    ],
)
def test_placement_invalide_refuse_avant_premier_controle(tmp_path, monkeypatch, mutation, localisation):
    """Le placement invalide est localisé avant tout contrôle métier et toute création de rapport."""
    source = tmp_path / "fixture.3mf"
    placement = fixture(source)
    document = json.loads(placement.read_text())
    mutation(document)
    placement.write_text(json.dumps(document))

    def controle_interdit(*args):
        raise AssertionError("Le contrôle métier a commencé avec un placement invalide")

    monkeypatch.setattr(plaques_interface, "main", controle_interdit)
    sortie = tmp_path / "rapports"
    with pytest.raises(ValueError) as erreur:
        routine.controler(source, source, placement, sortie)
    assert localisation in str(erreur.value)
    assert not sortie.exists()


@pytest.mark.parametrize("dossier", ["rapports", "rapports avec espaces"])
@pytest.mark.parametrize("echec", ["plaques", "reglages", "disposition", "debit"])
def test_routine_ne_masque_pas_echec_par_ancien_json(tmp_path, monkeypatch, dossier, echec):
    source = tmp_path / "fixture.3mf"
    placement = fixture(source)
    sortie = tmp_path / dossier
    sortie.mkdir()
    for nom in (
        "plaques-interface-plaques",
        "plaques-interface-tranche",
        "reglages-gcode",
        "disposition-reelle",
        "debit-gcode",
    ):
        (sortie / f"{nom}.json").write_text('{"conforme":true,"tout_conforme":true}')
    modules = {
        "plaques": plaques_interface,
        "reglages": verifier_reglages,
        "disposition": disposition_reelle,
        "debit": debit_gcode,
    }

    def faux(arguments):
        raise SystemExit(7)

    monkeypatch.setattr(modules[echec], "main", faux)
    rapport = routine.controler(source, source, placement, sortie)
    assert rapport["statut"] == "FAIL"
    assert any(r["code"] == 7 and r["statut"] == "FAIL" for r in rapport["resultats"])


def test_collision_refusee_avant_lecture_et_cli_mesuree(tmp_path, capsys, atelier_initialise):
    """Vérifie collision refusee avant lecture et cli mesuree ; atelier initialisé, aucune machine touchée."""
    from benchab.cli import main

    dossier = tmp_path / "rapports"
    dossier.mkdir()
    collision = dossier / "debit-gcode.json"
    collision.write_bytes(b"entree a conserver")
    with pytest.raises(ValueError, match="Collision"):
        routine.controler(collision, collision, tmp_path / "placement.json", dossier)
    assert collision.read_bytes() == b"entree a conserver"
    source = tmp_path / "fixture.3mf"
    placement = fixture(source)
    assert (
        main(
            [
                "--prive",
                str(tmp_path / "prive"),
                "controle",
                "routine",
                str(source),
                str(source),
                str(placement),
            ]
        )
        == 0
    )
    rapport = json.loads(capsys.readouterr().out)
    assert rapport["statut"] == "PASS"
    assert rapport["validation_physique"] is False
