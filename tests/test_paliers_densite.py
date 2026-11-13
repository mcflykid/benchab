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


def test_paliers_depuis_un_dessus_incline_gardent_profondeurs_et_volumes():
    """La profondeur est mesurée selon la normale du plan, sans supposer Z vertical."""
    from benchab.conception.paliers_densite import modificateurs_depuis_plan

    piece = trimesh.creation.box((12, 8, 10))
    matrice = trimesh.transformations.rotation_matrix(np.pi / 5, (0, 1, 0))
    piece.apply_transform(matrice)
    normale = matrice[:3, 2]
    point = (5 * normale).tolist()
    avant = piece.vertices.copy()
    volumes, r = modificateurs_depuis_plan(
        piece, point, normale.tolist(),
        [{"profondeur_mm": 2, "densite": "60%"}, {"profondeur_mm": 5, "densite": "20%"}],
    )
    assert r["statut"] == "PASS"
    assert [v["mesh"].volume for v in volumes] == pytest.approx([12 * 8 * 2, 12 * 8 * 3])
    limites = [sorted((float(d.min()), float(d.max()))) for v in volumes if (d := v["mesh"].vertices @ normale).size]
    assert np.allclose(limites, [[3, 5], [0, 3]], atol=1e-7, rtol=0)
    assert r["volume_non_attribue_mm3"] == pytest.approx(12 * 8 * 5)
    assert r["effet_gcode"] == "NON VÉRIFIÉ"
    assert np.array_equal(piece.vertices, avant)


@pytest.mark.parametrize(
    ("point", "normale"),
    [([0, 0, 4], [0, 0, 1]), ([0, 0, 6], [0, 0, 1]), ([0, 0, 5], [0, 0, -1])],
)
def test_plan_traversant_detache_ou_inverse_ne_donne_pas_de_paliers(point, normale):
    """Un plan déclaré doit être tangent par une vraie face extérieure."""
    from benchab.conception.paliers_densite import modificateurs_depuis_plan

    piece = trimesh.creation.box((12, 8, 10))
    with pytest.raises(ValueError, match="Plan"):
        modificateurs_depuis_plan(piece, point, normale, [{"profondeur_mm": 2, "densite": "60%"}])


def test_palier_refuse_un_maillage_converti_deplace_hors_tranche(monkeypatch):
    """Un volume correct ne suffit pas si ses sommets sortent des bornes de profondeur."""
    from benchab.conception import paliers_densite

    convertir = paliers_densite._vers_trimesh

    def deplacer(solide):
        mesh = convertir(solide)
        mesh.apply_translation((0, 0, 1))
        return mesh

    monkeypatch.setattr(paliers_densite, "_vers_trimesh", deplacer)
    volumes, r = paliers_densite.modificateurs_depuis_plan(
        trimesh.creation.box((12, 8, 10)), [0, 0, 5], [0, 0, 1],
        [{"profondeur_mm": 2, "densite": "60%"}],
    )
    assert not volumes and r["statut"] == "NON VÉRIFIÉ"
    assert "Sommet" in r["raison"]


def test_paliers_au_voisinage_d_une_fixation_suivent_sa_surface(tmp_path):
    """Les paliers partent d’une fixation extérieure et suivent sa surface courbe."""
    from benchab.conception.decoupe.geometrie import solide
    from benchab.conception.paliers_densite import modificateurs_autour_fixation
    from benchab.fabrication.anycubic import ecrire_3mf, ranger
    from benchab.integrite_3mf import instantane

    piece = trimesh.creation.box((10, 12, 12))
    fixation = trimesh.creation.icosphere(subdivisions=1, radius=2).apply_translation([8, 0, 0])
    paliers = [{"distance_mm": 2, "densite": "60%"}, {"distance_mm": 4, "densite": "35%"}]
    zones, rapport = modificateurs_autour_fixation(piece, fixation, paliers)
    assert rapport["statut"] == "PASS", rapport
    assert rapport["effet_gcode"] == "NON VÉRIFIÉ"
    premier, second = [solide(zone["mesh"]) for zone in zones]
    assert (premier ^ second).volume() == pytest.approx(0, abs=1e-8)
    assert zones[0]["mesh"].bounds[0, 0] == pytest.approx(4)
    assert zones[1]["mesh"].bounds[0, 0] == pytest.approx(2)
    item = {"name": "Temoin", "mesh": piece, "w": 10, "h": 12, "extruder": 1, "volumes": zones}
    ranger([item], 0, 1, 10.0, cli=True)
    fichier = ecrire_3mf([{"name": "Plaque", "items": [item]}], {}, tmp_path / "fixation.3mf", cli=True)
    reglages_relus = instantane(fichier)
    assert [v for k, v in reglages_relus.items() if k.endswith("/sparse_infill_density")] == ["60%", "35%"]



def test_fixation_dans_la_matiere_refuse_les_paliers():
    """Le parcours d'une fixation séparée refuse une intersection initiale."""
    from benchab.conception.paliers_densite import modificateurs_autour_fixation

    piece = trimesh.creation.box((10, 12, 12))
    fixation = trimesh.creation.icosphere(subdivisions=1, radius=2)
    zones, rapport = modificateurs_autour_fixation(piece, fixation, [{"distance_mm": 2, "densite": "60%"}])
    assert not zones and rapport["statut"] == "NON VÉRIFIÉ"
    assert "chevauche" in rapport["raison"]


def test_paliers_depuis_surface_mesurent_les_coquilles_interieures():
    """Un bloc permet de calculer exactement les volumes à chaque profondeur."""
    from benchab.conception.paliers_densite import modificateurs_depuis_surface

    piece = trimesh.creation.box((10, 12, 14))
    zones, rapport = modificateurs_depuis_surface(piece, [
        {"profondeur_mm": 1, "densite": "60%"}, {"profondeur_mm": 2, "densite": "35%"},
    ])
    assert rapport["statut"] == "PASS", rapport
    assert [z["mesh"].volume for z in zones] == pytest.approx([1680 - 960, 960 - 480])
    assert rapport["volume_non_attribue_mm3"] == pytest.approx(480)
    assert not rapport["validation_physique"]
