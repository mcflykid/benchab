"""Régressions U20 de syntaxe XML, en mm sur des fixtures géométriques."""

import json
from zipfile import ZipFile

from benchab.controles import plaques_interface, regles


def _fixture(tmp_path, modele):
    chemin = tmp_path / "fixture.3mf"
    config = """
<config><object id="1"><metadata value="Fixture" key="name"/></object>
<plate><model_instance><metadata value="1" key="object_id"/></model_instance></plate></config>
"""
    with ZipFile(chemin, "w") as archive:
        archive.writestr("3D/3dmodel.model", modele)
        archive.writestr("Metadata/model_settings.config", config)
    return chemin


def test_u20_transform_facultatif_et_attributs_non_ordonnes(tmp_path):
    """Un item sans transform hérite de l'identité ; attributs et namespace XML sont acceptés."""
    fichier = _fixture(tmp_path, """
<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" unit="millimeter">
<resources><object type="model" id="1"><mesh><vertices>
<vertex z="0" y="20" x="20"/><vertex y="20" x="25" z="0"/><vertex x="20" z="2" y="25"/>
</vertices></mesh></object></resources><build><item printable="1" objectid="1"/></build></model>
""")
    sortie = tmp_path / "rapport.json"
    code = plaques_interface.main([str(fichier), str(sortie)])
    rapport = json.loads(sortie.read_text(encoding="utf-8"))
    assert code == rapport["code"] == 0
    assert rapport["statut"] == "PASS" and rapport["conforme"] is True
    assert rapport["plaques"][0]["objets"] == 1


def test_u20_xml_invalide_est_non_verifie_dans_les_regles(tmp_path):
    """Le ParseError de XML devient NON VÉRIFIÉ motivé dans le rapport et la règle U20."""
    fichier = _fixture(tmp_path, "<model><incomplet>")
    sortie = tmp_path / "rapport.json"
    code = plaques_interface.main([str(fichier), str(sortie)])
    rapport = json.loads(sortie.read_text(encoding="utf-8"))
    assert code == rapport["code"] == 2
    assert rapport["statut"] == "NON VÉRIFIÉ" and "XML 3MF invalide" in rapport["motif"]
    u20 = next(x for x in regles.controler(fichier)["resultats"] if x["controle"] == "U20")
    assert u20["statut"] == "NON VÉRIFIÉ"
