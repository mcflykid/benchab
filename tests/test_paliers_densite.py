"""Paliers radiaux disjoints, avec confinement au volume nominal."""

import numpy as np
import pytest
import trimesh


def _paliers():
    return [
        {"rayon_mm": 3, "densite": "65%"},
        {"rayon_mm": 7, "densite": "35%"},
        {"rayon_mm": 11, "densite": "15%"},
    ]


def test_paliers_sont_disjoints_et_restent_dans_la_piece():
    from benchab.conception.decoupe.geometrie import solide
    from benchab.conception.paliers_densite import modificateurs_radiaux

    piece = trimesh.creation.box((18, 18, 18))
    avant = piece.vertices.copy()
    modificateurs, rapport = modificateurs_radiaux(piece, [0, 0, 0], _paliers())
    assert len(modificateurs) == 3 and rapport["statut"] == "PASS"
    assert rapport["effet_gcode"] == "NON VÉRIFIÉ"
    assert np.array_equal(piece.vertices, avant)
    volumes = [solide(m["mesh"]) for m in modificateurs]
    assert [m["subtype"] for m in modificateurs] == ["modifier_part"] * 3
    assert [m["settings"]["sparse_infill_density"] for m in modificateurs] == ["65%", "35%", "15%"]
    for i, region in enumerate(volumes):
        assert (region - solide(piece)).volume() < 1e-7
        for autre in volumes[:i]:
            assert (region ^ autre).volume() < 1e-7


def test_paliers_insuffisants_ne_fournissent_pas_de_candidat_partiel():
    from benchab.conception.paliers_densite import modificateurs_radiaux

    piece = trimesh.creation.box((12, 12, 12))
    modificateurs, rapport = modificateurs_radiaux(piece, [100, 100, 100], _paliers())
    assert not modificateurs and rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["raison"]


@pytest.mark.parametrize(
    "paliers",
    [
        [{"rayon_mm": 3, "densite": "nan"}],
        [{"rayon_mm": 3, "densite": "101%"}],
        [{"rayon_mm": 4, "densite": "30%"}, {"rayon_mm": 3, "densite": "20%"}],
        [{"rayon_mm": True, "densite": "20%"}],
    ],
)
def test_refuse_cotes_ou_densites_hors_contrat(paliers):
    from benchab.conception.paliers_densite import modificateurs_radiaux

    piece = trimesh.creation.box((12, 12, 12))
    with pytest.raises(ValueError):
        modificateurs_radiaux(piece, [0, 0, 0], paliers)


def test_paliers_survivent_comme_modificateurs_dans_un_3mf_neuf(tmp_path):
    """Les trois coquilles restent des modificateurs, avec les densités déclarées."""
    from xml.etree import ElementTree
    from zipfile import ZipFile

    from benchab.conception.paliers_densite import modificateurs_radiaux
    from benchab.fabrication.anycubic import ecrire_3mf, ranger

    piece = trimesh.creation.box((18, 18, 18))
    volumes, rapport = modificateurs_radiaux(piece, [0, 0, 0], _paliers())
    assert rapport["statut"] == "PASS"
    item = {"name": "Cube", "mesh": piece, "w": 18, "h": 18, "extruder": 1, "volumes": volumes}
    ranger([item], 0, 1, 10.0, cli=True)
    sortie = ecrire_3mf([{"name": "Plaque", "items": [item]}], {}, tmp_path / "paliers.3mf", cli=True)
    with ZipFile(sortie) as archive:
        assert archive.testzip() is None
        config = ElementTree.fromstring(archive.read("Metadata/model_settings.config"))
    parties = config.findall("./object/part")
    assert [p.attrib["subtype"] for p in parties] == ["normal_part", *["modifier_part"] * 3]
    assert [p.find("./metadata[@key='sparse_infill_density']").get("value") for p in parties[1:]] == [
        "65%", "35%", "15%",
    ]
