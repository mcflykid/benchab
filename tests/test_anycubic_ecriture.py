"""Écritures 3MF vérifiées sur des pièces synthétiques privées."""

import json
from unittest.mock import patch
from xml.etree import ElementTree as E
from zipfile import ZipFile

import pytest
import trimesh

from benchab.fabrication import anycubic


def _plaques():
    mesh = trimesh.creation.box((10, 10, 10))
    mesh.apply_translation((5, 5, 5))
    item = {"name": "Pièce fixture", "mesh": mesh, "x": 1, "y": 2, "extruder": 1}
    return [{"name": "Plaque A", "items": [item]}, {"name": "Plaque B", "items": []}]


def test_ecrire_3mf_apparie_les_tours_aux_plaques_et_declare_overrides(tmp_path):
    """Conserve les inconnus et déclare les coordonnées de tour par plaque, en mm."""
    destination = tmp_path / "sortie.3mf"
    reglages = {"future_fixture": ["valeur intacte"], "top_shell_layers": "5"}
    anycubic.ecrire_3mf(_plaques(), reglages, destination)
    with ZipFile(destination) as archive:
        config = json.loads(archive.read("Metadata/project_settings.config"))
        meta = json.loads(archive.read("Metadata/benchab_geometry.json"))
    assert config["wipe_tower_x"] == ["200", "200"]
    assert config["wipe_tower_y"] == ["10", "10"]
    assert config["future_fixture"] == ["valeur intacte"]
    transformations = {x["reglage"]: x for x in meta["transformations_reglages"]}
    assert transformations["wipe_tower_x"]["avant_present"] is False
    assert transformations["wipe_tower_x"]["apres"] == ["200", "200"]
    assert transformations["wipe_tower_y"]["avant_present"] is False
    assert transformations["wipe_tower_y"]["apres"] == ["10", "10"]


def test_ecrire_3mf_refuse_mutation_pendant_serialisation_sans_abimer_cible(tmp_path):
    """Une mutation de réglage après gel des attendus bloque la publication atomique."""
    destination = tmp_path / "existant.3mf"
    original = b"contenu preexistant intact"
    destination.write_bytes(original)
    encoder_original = anycubic.schema_reglages.encoder

    def encoder_mutant(settings, **options):
        altered = dict(settings)
        altered["top_shell_layers"] = "99"
        return encoder_original(altered, **options)

    with patch.object(anycubic.schema_reglages, "encoder", side_effect=encoder_mutant):
        with pytest.raises(ValueError, match="modifiés pendant la sérialisation"):
            anycubic.ecrire_3mf(_plaques(), {"top_shell_layers": "5"}, destination)
    assert destination.read_bytes() == original
    assert list(tmp_path.glob(".existant.3mf.*.tmp")) == []


def test_producteur_canonique_provenance_et_placement_neufs(tmp_path):
    """Déclare index zéro, origine embarquée en mm et héritage mono-volume ; aucun réglage projet changé."""
    from benchab.imports3d.lecture_3mf import lire

    cible = tmp_path / "nouveau.3mf"
    plaques = _plaques()[:1]
    settings = {"filament_colour": ["#000000"], "top_shell_layers": "5"}
    plaques[0]["items"][0]["settings"] = {"top_shell_layers": "8"}
    anycubic.ecrire_3mf(plaques, settings, cible, canonique=True, nom_source=cible.name)
    with ZipFile(cible) as z:
        model = E.fromstring(z.read("Metadata/model_settings.config"))
        part = model.find("object/part")
        metadata = {m.get("key"): m.get("value") for m in part.findall("metadata")}
        assert "extruder" not in metadata
        assert metadata["source_file"] == cible.name
        assert metadata["source_object_id"] == metadata["source_volume_id"] == "0"
        assert [metadata["source_offset_" + a] for a in "xyz"] == ["5", "5", "5"]
        assert model.find("object/metadata[@key='extruder']").get("value") == "1"
        assert model.find("object/metadata[@key='top_shell_layers']").get("value") == "8"
        assert json.loads(z.read("Metadata/project_settings.config"))["top_shell_layers"] == "5"
        assert model.find("plate/metadata[@key='filament_maps']").get("value") == "1"
    assert lire(cible).bounds.tolist() == [[1.0, 2.0, 0.0], [11.0, 12.0, 10.0]]


def test_origine_fractionnaire_sans_arrondi_qui_change_sa_serialisation(tmp_path):
    """Une origine locale non entière garde la précision double du moteur ; aucun réglage d'impression modifié."""
    plaques = _plaques()[:1]
    plaques[0]["items"][0]["mesh"].apply_translation((0, 0, 37.3))
    cible = tmp_path / "fractionnaire.3mf"
    anycubic.ecrire_3mf(plaques, {"filament_colour": ["#FFFFFF"]}, cible, canonique=True, nom_source=cible.name)
    with ZipFile(cible) as z:
        r = E.fromstring(z.read("Metadata/model_settings.config"))
        assert r.find("object/part/metadata[@key='source_offset_z']").get("value") == "42.299999999999997"


def test_type_volume_inconnu_refuse_avant_creation(tmp_path):
    """Un rôle non reconnu ne doit jamais devenir un corps imprimé par défaut dans le moteur."""
    import trimesh

    plaques = [{'name': 'Fixture', 'items': [{
        'name': 'Corps', 'mesh': trimesh.creation.box(), 'extruder': 1, 'x': 20, 'y': 20,
        'volumes': [{'name': 'Zone', 'mesh': trimesh.creation.box(), 'subtype': 'parameter_modifier'}],
    }]}]
    sortie = tmp_path / 'refuse.3mf'
    with pytest.raises(ValueError, match='Type de volume inconnu'):
        anycubic.ecrire_3mf(plaques, {}, sortie)
    assert not sortie.exists()


def test_nommer_plaques_refuse_livrable_protege_sans_appeler_writer(tmp_path):
    import hashlib

    cible = tmp_path / "prive" / "projets" / "gel" / "sortie.3mf"
    cible.parent.mkdir(parents=True)
    cible.write_bytes(b"archive fixture")
    digest = hashlib.sha256(cible.read_bytes()).hexdigest()
    (cible.parent / "LIVRABLES.json").write_text(
        json.dumps({"fichiers": [{"chemin": "sortie.3mf", "sha256": digest}]}), encoding="utf-8"
    )
    with patch.object(anycubic, "restaurer_noms") as writer, pytest.raises(ValueError, match="protégé"):
        anycubic.nommer_plaques(cible, ["Nom original"])
    writer.assert_not_called()
    assert cible.read_bytes() == b"archive fixture"


def test_nommer_plaques_refuse_chemin_public_sans_appeler_writer(tmp_path):
    """Un chemin public reste refusé, même si le checkout réside sous le temporaire système."""
    cible = __file__
    with (
        patch.object(anycubic, "restaurer_noms") as writer,
        pytest.raises(ValueError, match="atelier privé ou temporaire système"),
    ):
        anycubic.nommer_plaques(cible, ["Nom original"])
    writer.assert_not_called()


def test_temoins_binaires_petit_et_multicolore_reouverts(tmp_path):
    """Fixtures 20×20×4 et deux 12×20×4 mm : volumes/topologie/outils relus, aucun slicer ni modèle tiers."""
    import struct

    from benchab.imports3d.lecture_3mf import lire

    petit = trimesh.creation.box((20, 20, 4))
    stl = tmp_path / "petit.stl"
    petit.export(stl)
    octets = stl.read_bytes()
    triangles = struct.unpack_from("<I", octets, 80)[0]
    assert len(octets) == 84 + 50 * triangles
    relu = trimesh.load_mesh(stl)
    assert relu.is_watertight and relu.volume == pytest.approx(1600)
    assert relu.extents.tolist() == [20, 20, 4]
    import numpy as np

    assert (np.bincount(relu.edges_unique_inverse) == 2).all()
    pieces = [{"name": nom, "mesh": trimesh.creation.box((12, 20, 4)),
               "x": x, "y": 0, "extruder": outil}
              for nom, x, outil in (("A", 0, 1), ("B", 20, 2))]
    cible = tmp_path / "multicolore.3mf"
    anycubic.ecrire_3mf([{"name": "Fixture", "items": pieces}],
                        {"filament_colour": ["#FF0000", "#0000FF"]}, cible)
    scene = lire(cible)
    mesh = scene.to_mesh()
    assert mesh.is_watertight and mesh.volume == pytest.approx(1920)
    composants = mesh.split()
    assert len(composants) == 2
    assert all(m.volume == pytest.approx(960) for m in composants)
    with ZipFile(cible) as archive:
        racine = E.fromstring(archive.read("Metadata/model_settings.config"))
        outils = [m.get("value") for m in racine.findall("object/metadata[@key='extruder']")]
        assert outils == ["1", "2"]
