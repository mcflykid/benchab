"""Identifiants Production scindés par fichier, unités et archives refusées."""

from xml.etree import ElementTree as E
from zipfile import ZipFile

import numpy as np
import pytest
import trimesh

from benchab.imports3d.lecture_3mf import NS, lire


def fabriquer(path, *, cycle=False, unite="millimeter"):
    racine = E.Element(NS + "model", unit=unite)
    ressources = E.SubElement(racine, NS + "resources")
    objet = E.SubElement(ressources, NS + "object", id="1", type="model")
    composant = E.SubElement(E.SubElement(objet, NS + "components"), NS + "component", objectid="1")
    if not cycle:
        composant.set(
            "{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}path", "/3D/Objects/piece.model"
        )
    build = E.SubElement(racine, NS + "build")
    E.SubElement(build, NS + "item", objectid="1", transform="1 0 0 0 1 0 0 0 1 1 2 3")
    enfant = E.Element(NS + "model", unit=unite)
    ressources = E.SubElement(enfant, NS + "resources")
    objet = E.SubElement(ressources, NS + "object", id="1", type="model")
    maillage = E.SubElement(objet, NS + "mesh")
    vertices, faces = E.SubElement(maillage, NS + "vertices"), E.SubElement(maillage, NS + "triangles")
    cube = trimesh.creation.box((1, 2, 3))
    for v in cube.vertices:
        E.SubElement(vertices, NS + "vertex", **dict(zip(("x", "y", "z"), map(str, v), strict=True)))
    for f in cube.faces:
        E.SubElement(faces, NS + "triangle", **dict(zip(("v1", "v2", "v3"), map(str, f), strict=True)))
    with ZipFile(path, "w") as archive:
        archive.writestr("3D/3dmodel.model", E.tostring(racine))
        archive.writestr("3D/Objects/piece.model", E.tostring(enfant))


def test_meme_id_deux_modeles_et_transformation_en_centimetres(tmp_path):
    source = tmp_path / "production.3mf"
    fabriquer(source, unite="centimeter")
    modele = lire(source).to_mesh()
    assert modele.is_volume
    assert modele.volume == pytest.approx(6000)
    assert np.allclose(modele.extents, [10, 20, 30])
    assert np.allclose(modele.bounds.mean(axis=0), [10, 20, 30])


def test_cycle_budget_et_zip_invalide_refuses(tmp_path):
    source = tmp_path / "cycle.3mf"
    fabriquer(source, cycle=True)
    with pytest.raises(ValueError, match="Cycle"):
        lire(source)
    fabriquer(source)
    with pytest.raises(ValueError, match="Budget"):
        lire(source, max_faces=1)
    source.write_bytes(b"archive invalide")
    with pytest.raises(ValueError, match="illisible"):
        lire(source)
