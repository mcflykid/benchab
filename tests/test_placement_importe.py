"""Contrat d’un placement déclaré à partir d’une source 3MF, sans G-code."""

import json
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from benchab.cli import main as main_benchab
from benchab.controles.placement import valider as valider_placement
from benchab.imports3d.placement import generer
from benchab.qualite.empreintes import sha256_fichier

NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
PROD = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"


def _modele(path, items, *, composant=False, unite="millimeter"):
    """Construit une source 3MF synthétique avec des objets et instances identifiables."""
    racine = ET.Element(f"{{{NS}}}model", unit=unite)
    ressources = ET.SubElement(racine, f"{{{NS}}}resources")
    if composant:
        objet = ET.SubElement(ressources, f"{{{NS}}}object", id="20", type="model")
        composants = ET.SubElement(objet, f"{{{NS}}}components")
        ET.SubElement(
            composants, f"{{{NS}}}component", objectid="1",
            **{f"{{{PROD}}}path": "/3D/Objects/piece.model",
               "transform": "1 0 0 0 1 0 0 0 1 4 5 0"},
        )
    else:
        for ident in sorted({item["objectid"] for item in items}):
            objet = ET.SubElement(ressources, f"{{{NS}}}object", id=ident, type="model", name=f"Objet {ident}")
            _maillage(objet)
    build = ET.SubElement(racine, f"{{{NS}}}build")
    for item in items:
        ET.SubElement(build, f"{{{NS}}}item", **item)
    return ET.tostring(racine, encoding="utf-8")


def _maillage(objet):
    mesh = ET.SubElement(objet, f"{{{NS}}}mesh")
    sommets, faces = ET.SubElement(mesh, f"{{{NS}}}vertices"), ET.SubElement(mesh, f"{{{NS}}}triangles")
    for x, y, z in ((0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0),
                    (0, 0, 10), (10, 0, 10), (10, 10, 10), (0, 10, 10)):
        ET.SubElement(sommets, f"{{{NS}}}vertex", x=str(x), y=str(y), z=str(z))
    for a, b, c in ((0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
                    (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)):
        ET.SubElement(faces, f"{{{NS}}}triangle", v1=str(a), v2=str(b), v3=str(c))


def _source(path, *, items=None, composant=False, unite="millimeter"):
    """Écrit les seuls membres géométriques nécessaires à la lecture 3MF."""
    items = items or [{"objectid": "1", "transform": "1 0 0 0 1 0 0 0 1 5 6 0"}]
    with ZipFile(path, "w") as archive:
        archive.writestr("3D/3dmodel.model", _modele(path, items, composant=composant, unite=unite))
        if composant:
            racine = ET.Element(f"{{{NS}}}model", unit=unite)
            ressources = ET.SubElement(racine, f"{{{NS}}}resources")
            _maillage(ET.SubElement(ressources, f"{{{NS}}}object", id="1", type="model"))
            archive.writestr("3D/Objects/piece.model", ET.tostring(racine, encoding="utf-8"))
    return path


def _declaration(source, objets, plaques=None):
    return {
        "schema": 1,
        "source_sha256": sha256_fichier(source),
        "repere_xy": "plateau",
        "plates": plaques or [{"index": 1, "name": "Plateau explicite"}],
        "objects": objets,
    }


def test_placement_3mf_est_valide_et_trace_la_transformation_xy(tmp_path):
    source = _source(tmp_path / "piece.3mf")
    avant = source.read_bytes()
    declaration = _declaration(source, [{"build_item": 1, "plate": 1, "name": "Pièce témoin"}])

    rapport = generer(source, declaration)

    assert rapport["statut"] == "PASS"
    placement = valider_placement(rapport["placement"])
    assert placement["source_sha256"] == sha256_fichier(source)
    assert placement["declaration_sha256"]
    assert placement["objects"][0]["xy"] == [5.0, 6.0]
    assert placement["objects"][0]["size"] == [10.0, 10.0, 10.0]
    trace = placement["objects"][0]["transformations"][0]["etapes"][0]
    assert trace["build_item"] == 1 and trace["objet_id"] == "1"
    assert trace["translation_mm"] == [5.0, 6.0, 0.0]
    assert source.read_bytes() == avant


def test_composant_interne_transforme_est_trace_sans_a_platir_la_source(tmp_path):
    source = _source(
        tmp_path / "assemblage.3mf",
        items=[{"objectid": "20", "transform": "1 0 0 0 1 0 0 0 1 1 2 0"}],
        composant=True,
    )
    declaration = _declaration(source, [{"build_item": 1, "plate": 1, "name": "Assemblage"}])

    rapport = generer(source, declaration)

    assert rapport["statut"] == "PASS"
    objet = rapport["placement"]["objects"][0]
    assert objet["xy"] == [5.0, 7.0]
    etapes = objet["transformations"][0]["etapes"]
    assert len(etapes) == 2
    assert etapes[1]["modele_cible"] == "3D/Objects/piece.model"


def test_multi_plaque_exige_une_affectation_explicite_et_valide_les_liens(tmp_path):
    source = _source(
        tmp_path / "multi.3mf",
        items=[
            {"objectid": "1", "transform": "1 0 0 0 1 0 0 0 1 2 3 0"},
            {"objectid": "2", "transform": "1 0 0 0 1 0 0 0 1 30 40 0"},
        ],
    )
    declaration = _declaration(
        source,
        [
            {"build_item": 1, "plate": 1, "name": "Gauche"},
            {"build_item": 2, "plate": 2, "name": "Droite"},
        ],
        plaques=[{"index": 1, "name": "Plaque A"}, {"index": 2, "name": "Plaque B"}],
    )

    rapport = generer(source, declaration)

    assert rapport["statut"] == "PASS"
    placement = valider_placement(rapport["placement"])
    assert [plaque["name"] for plaque in placement["plates"]] == ["Plaque A", "Plaque B"]
    assert [objet["plate"] for objet in placement["objects"]] == [1, 2]


def test_liaison_absente_donne_un_placement_indetermine_sans_origine_inventee(tmp_path):
    source = _source(tmp_path / "incomplet.3mf")
    declaration = _declaration(source, [{"build_item": 9, "plate": 1, "name": "Inconnue"}])

    rapport = generer(source, declaration)

    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["placement"] is None
    assert "indisponible" in rapport["raisons"][0]


def test_declaration_perimee_est_non_verifiee_sans_placer(tmp_path):
    source = _source(tmp_path / "modifiee.3mf")
    declaration = _declaration(source, [{"build_item": 1, "plate": 1, "name": "Pièce"}])
    source.write_bytes(source.read_bytes() + b" ")

    rapport = generer(source, declaration)

    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["placement"] is None
    assert "SHA" in rapport["raisons"][0]


def test_cli_ecrit_le_rapport_neuf_dans_le_prive(atelier_initialise, tmp_path, capsys):
    source = _source(tmp_path / "piece.3mf")
    declaration = _declaration(source, [{"build_item": 1, "plate": 1, "name": "Pièce"}])
    fichier_declaration = tmp_path / "declaration.json"
    fichier_declaration.write_text(json.dumps(declaration), encoding="utf-8")
    sortie = atelier_initialise / "preuves" / "placement.json"
    sortie.parent.mkdir(parents=True)

    code = main_benchab([
        "--prive", str(atelier_initialise), "imports", "placement",
        str(source), str(fichier_declaration), "--sortie", str(sortie),
    ])

    console = json.loads(capsys.readouterr().out)
    rapport = json.loads(sortie.read_text(encoding="utf-8"))
    assert code == console["code"] == rapport["code"] == 0
    assert rapport["placement"]["objects"][0]["source_instance"]["build_item"] == 1


