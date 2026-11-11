"""Vérifie les intentions de couverture Z sur de petits 3MF synthétiques."""

import json
import zipfile

from benchab.cli import main as main_benchab
from benchab.controles.couverture_plages_z import analyser, main


def _cube_3mf(path, *, transform="1 0 0 0 1 0 0 0 1 20 30 0", ranges=None):
    """Construit un cube 10 mm et une plage simple, sans lancer de trancheuse."""
    vertices = [
        (0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0),
        (0, 0, 10), (10, 0, 10), (10, 10, 10), (0, 10, 10),
    ]
    triangles = [
        (0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
        (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
        (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7),
    ]
    sommets = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in vertices)
    faces = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in triangles)
    model = (
        '<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" unit="millimeter">'
        f'<resources><object id="1" type="model"><mesh><vertices>{sommets}</vertices>'
        f'<triangles>{faces}</triangles></mesh></object></resources>'
        f'<build><item objectid="1" transform="{transform}"/></build></model>'
    )
    xml_ranges = ranges or (
        '<objects><object id="1"><range min_z="0" max_z="10">'
        '<option opt_key="layer_height">0.2</option></range></object></objects>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("3D/3dmodel.model", model)
        archive.writestr("Metadata/layer_config_ranges.xml", xml_ranges)
    return path


def test_couverture_totale_accepte_une_translation_xy_objet_et_conserve_source(tmp_path):
    source = _cube_3mf(tmp_path / "cube.3mf")
    avant = source.read_bytes()
    rapport = analyser(source, {"schema": 1, "intentions": [{"objet_id": "1", "type": "totale"}]})
    assert rapport["statut"] == "PASS"
    assert rapport["objets"][0]["geometrie_z_mm"] == [0.0, 10.0]
    assert source.read_bytes() == avant


def test_plage_partielle_volontaire_est_acceptee_sur_intervalle_declare(tmp_path):
    xml = ('<objects><object id="1"><range min_z="2" max_z="8">'
           '<option opt_key="layer_height">0.2</option></range></object></objects>')
    source = _cube_3mf(tmp_path / "partiel.3mf", ranges=xml)
    rapport = analyser(source, {"schema": 1, "intentions": [{
        "objet_id": "1", "type": "partielle_volontaire", "min_z_mm": 2, "max_z_mm": 8,
    }]})
    assert rapport["statut"] == "PASS"


def test_sommet_exige_sans_plage_est_fail(tmp_path):
    xml = ('<objects><object id="1"><range min_z="0" max_z="8">'
           '<option opt_key="layer_height">0.2</option></range></object></objects>')
    source = _cube_3mf(tmp_path / "sommet-manquant.3mf", ranges=xml)
    rapport = analyser(source, {"schema": 1, "intentions": [{"objet_id": "1", "type": "totale"}]})
    assert rapport["statut"] == "FAIL"
    assert rapport["objets"][0]["couverture_explicite_mm"] == [[0.0, 8.0]]


def test_zone_fine_sans_couverture_reste_fail_et_transformation_z_inconnue_nv(tmp_path):
    fine = {"schema": 1, "intentions": [{
        "objet_id": "1", "type": "zone_fine", "min_z_mm": 4, "max_z_mm": 6, "hauteur_max_mm": 0.12,
    }]}
    source = _cube_3mf(tmp_path / "fine-absente.3mf")
    rapport = analyser(source, fine)
    assert rapport["statut"] == "FAIL"
    transformee_z = _cube_3mf(tmp_path / "z-inconnu.3mf", transform="1 0 0 0 1 0 0 0 1 0 0 2")
    rapport_z = analyser(transformee_z, {"schema": 1, "intentions": [{"objet_id": "1", "type": "totale"}]})
    assert rapport_z["statut"] == "NON VÉRIFIÉ"


def test_zone_fine_pass_quand_l_intervalle_est_couvert_sous_le_seuil(tmp_path):
    xml = ('<objects><object id="1"><range min_z="4" max_z="6">'
           '<option opt_key="layer_height">0.08</option></range></object></objects>')
    source = _cube_3mf(tmp_path / "fine-couverte.3mf", ranges=xml)
    rapport = analyser(source, {"schema": 1, "intentions": [{
        "objet_id": "1", "type": "zone_fine", "min_z_mm": 4, "max_z_mm": 6, "hauteur_max_mm": 0.1,
    }]})
    assert rapport["statut"] == "PASS"


def test_objet_ou_intention_inconnue_reste_non_verifie(tmp_path):
    source = _cube_3mf(tmp_path / "inconnu.3mf")
    rapport = analyser(source, {"schema": 1, "intentions": [{"objet_id": "9", "type": "totale"}]})
    assert rapport["statut"] == "NON VÉRIFIÉ"


def test_xml_de_plages_invalide_reste_non_verifie_sans_modifier_archive(tmp_path):
    source = _cube_3mf(tmp_path / "xml-invalide.3mf", ranges="<objects>")
    avant = source.read_bytes()
    rapport = analyser(source, {"schema": 1, "intentions": [{"objet_id": "1", "type": "totale"}]})
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert source.read_bytes() == avant


def test_plage_etrangere_au_seul_objet_est_non_verifiee(tmp_path):
    xml = (
        '<objects><object id="1"><range min_z="0" max_z="10">'
        '<option opt_key="layer_height">0.2</option></range></object>'
        '<object id="2"><range min_z="0" max_z="10">'
        '<option opt_key="layer_height">0.2</option></range></object></objects>'
    )
    source = _cube_3mf(tmp_path / "plages-etrangeres.3mf", ranges=xml)
    rapport = analyser(source, {"schema": 1, "intentions": [{"objet_id": "1", "type": "totale"}]})
    assert rapport["statut"] == "NON VÉRIFIÉ"


def test_fichier_intention_json_est_charge_par_api(tmp_path):
    source = _cube_3mf(tmp_path / "cube.3mf")
    declaration = tmp_path / "intention.json"
    declaration.write_text(json.dumps({"schema": 1, "intentions": [{"objet_id": "1", "type": "totale"}]}))
    assert analyser(source, declaration)["statut"] == "PASS"


def test_cli_imprime_rapport_json_et_code_pass(tmp_path, capsys):
    source = _cube_3mf(tmp_path / "cube.3mf")
    declaration = tmp_path / "intention.json"
    declaration.write_text(json.dumps({"schema": 1, "intentions": [{"objet_id": "1", "type": "totale"}]}))
    assert main([str(source), str(declaration)]) == 0
    assert json.loads(capsys.readouterr().out)["statut"] == "PASS"


def test_commande_benchab_route_le_controle_sans_slicer(tmp_path, capsys):
    source = _cube_3mf(tmp_path / "cube.3mf")
    declaration = tmp_path / "intention.json"
    declaration.write_text(json.dumps({"schema": 1, "intentions": [{"objet_id": "1", "type": "totale"}]}))
    prive = tmp_path / "prive"
    assert main_benchab(["--prive", str(prive), "init"]) == 0
    assert main_benchab(["--prive", str(prive), "controle", "plages-z", str(source), str(declaration)]) == 0
    assert '"statut": "PASS"' in capsys.readouterr().out
