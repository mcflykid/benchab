"""Vérifie les intentions de couverture Z sur de petits 3MF synthétiques."""

import json
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pytest

from benchab.cli import main as main_benchab
from benchab.controles.couverture_plages_z import analyser, main


def _cube_3mf(
    path,
    *,
    transform="1 0 0 0 1 0 0 0 1 20 30 0",
    ranges=None,
    unite="millimeter",
    printable="1",
    project_layer_height=None,
    model_settings=None,
):
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
        f'<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" unit="{unite}">'
        f'<resources><object id="1" type="model"><mesh><vertices>{sommets}</vertices>'
        f'<triangles>{faces}</triangles></mesh></object></resources>'
        f'<build><item objectid="1" printable="{printable}" transform="{transform}"/></build></model>'
    )
    xml_ranges = ranges or (
        '<objects><object id="1"><range min_z="0" max_z="10">'
        '<option opt_key="layer_height">0.2</option></range></object></objects>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("3D/3dmodel.model", model)
        archive.writestr("Metadata/layer_config_ranges.xml", xml_ranges)
        if project_layer_height is not None:
            archive.writestr("Metadata/project_settings.config", json.dumps({"layer_height": project_layer_height}))
        if model_settings is not None:
            archive.writestr("Metadata/model_settings.config", model_settings)
    return path


def _copier_avec_modele_cible(source, destination, *, relation=False, item_path=False):
    """Crée un 3MF où le lecteur OPC ou p:path choisit une autre partie modèle."""
    with zipfile.ZipFile(source) as archive:
        modele = archive.read("3D/3dmodel.model").decode()
        plages = archive.read("Metadata/layer_config_ranges.xml")
    if relation:
        modele_cible = modele.replace('id="1"', 'id="2"').replace('objectid="1"', 'objectid="2"')
    else:
        modele_cible = modele
    if item_path:
        modele = modele.replace(
            '<item objectid="1"', '<item xmlns:p="urn:3mf:path" p:path="alternate.model" objectid="1"'
        )
    with zipfile.ZipFile(destination, "w") as archive:
        archive.writestr("3D/3dmodel.model", modele)
        archive.writestr("3D/alternate.model", modele_cible)
        archive.writestr("Metadata/layer_config_ranges.xml", plages)
        if relation:
            archive.writestr(
                "_rels/.rels",
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel" '
                'Target="3D/alternate.model"/></Relationships>',
            )
    return destination


def _archive_composant_interne(source, destination, *, transform="1 0 0 0 1 0 0 0 1 5 6 0"):
    """Reproduit le producteur BENCHAB : instance et maillage id2, ressource séparée."""
    with zipfile.ZipFile(source) as archive:
        modele = archive.read("3D/3dmodel.model").decode()
        plages = archive.read("Metadata/layer_config_ranges.xml").decode()
    principal = ElementTree.fromstring(modele)
    resources = principal.find("{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}resources")
    maillage = resources.find("{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}object")
    resources.remove(maillage)
    plaque = ElementTree.SubElement(resources, "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}object", {
        "id": "2", "type": "model",
    })
    composants = ElementTree.SubElement(plaque, "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}components")
    ElementTree.SubElement(composants, "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}component", {
        "objectid": "2", "transform": transform,
        "{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}path": "/3D/Objects/pieces.model",
    })
    principal.find("{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}build").find(
        "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}item"
    ).set("objectid", "2")
    cible = ElementTree.Element("{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}model", unit="millimeter")
    maillage.set("id", "2")
    ElementTree.SubElement(
        cible, "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}resources"
    ).append(maillage)
    plages = plages.replace('<object id="1"', '<object id="2"')
    with zipfile.ZipFile(destination, "w") as archive:
        archive.writestr("3D/3dmodel.model", ElementTree.tostring(principal, encoding="utf-8"))
        archive.writestr("3D/Objects/pieces.model", ElementTree.tostring(cible, encoding="utf-8"))
        archive.writestr("Metadata/layer_config_ranges.xml", plages)
    return destination


def test_couverture_totale_accepte_une_translation_xy_objet_et_conserve_source(tmp_path):
    source = _cube_3mf(tmp_path / "cube.3mf")
    avant = source.read_bytes()
    rapport = analyser(source, {"schema": 1, "intentions": [{"objet_id": "1", "type": "totale"}]})
    assert rapport["statut"] == "PASS"
    assert rapport["objets"][0]["geometrie_z_mm"] == [0.0, 10.0]
    assert rapport["objets"][0]["transformations"]["instance"]["translation_mm"] == [20.0, 30.0, 0.0]
    assert source.read_bytes() == avant


def test_translations_en_pouces_restituees_en_millimetres(tmp_path):
    """La matrice brute garde ses pouces, le rapport convertit le décalage en mm."""
    xml = ('<objects><object id="1"><range min_z="0" max_z="254">'
           '<option opt_key="layer_height">0.2</option></range></object></objects>')
    source = _cube_3mf(tmp_path / "pouces.3mf", ranges=xml, unite="inch")
    rapport = analyser(source, {"schema": 1, "intentions": [{"objet_id": "1", "type": "totale"}]})
    assert rapport["statut"] == "PASS"
    objet = rapport["objets"][0]
    assert objet["geometrie_z_mm"] == [0.0, 254.0]
    t = objet["transformations"]["instance"]
    assert t["unite_source"] == "inch"
    assert t["matrice_3mf"][9:] == [20.0, 30.0, 0.0]
    assert t["translation_mm"] == [508.0, 762.0, 0.0]


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
    t = rapport_z["objets"][0]["transformations"]["instance"]
    assert t["translation_mm"] == [0.0, 0.0, 2.0]
    assert t["z_inchange"] is False


def test_zone_fine_plus_courte_que_tolerance_exige_une_couverture_complete(tmp_path):
    """La tolérance de jointure ne remplace aucune preuve de couverture."""
    source = _cube_3mf(tmp_path / "zone-mince.3mf", ranges="<objects/>")
    rapport = analyser(source, {"schema": 1, "intentions": [{
        "objet_id": "1", "type": "zone_fine", "min_z_mm": 4, "max_z_mm": 4.0005, "hauteur_max_mm": 0.12,
    }]})
    assert rapport["statut"] == "FAIL"
    xml = ('<objects><object id="1"><range min_z="4" max_z="4.0002">'
           '<option opt_key="layer_height">0.1</option></range></object></objects>')
    partielle = _cube_3mf(tmp_path / "zone-mince-partielle.3mf", ranges=xml)
    rapport = analyser(partielle, {"schema": 1, "intentions": [{
        "objet_id": "1", "type": "zone_fine", "min_z_mm": 4, "max_z_mm": 4.0005, "hauteur_max_mm": 0.12,
    }]})
    assert rapport["statut"] == "FAIL"


def test_lacune_interne_meme_inferieure_a_la_tolerance_est_refusee(tmp_path):
    """Une petite lacune entre plages reste une absence de couverture en mm."""
    xml = ('<objects><object id="1"><range min_z="4" max_z="4.5">'
           '<option opt_key="layer_height">0.1</option></range><range min_z="4.5005" max_z="6">'
           '<option opt_key="layer_height">0.1</option></range></object></objects>')
    source = _cube_3mf(tmp_path / "lacune.3mf", ranges=xml)
    rapport = analyser(source, {"schema": 1, "intentions": [{
        "objet_id": "1", "type": "zone_fine", "min_z_mm": 4, "max_z_mm": 6, "hauteur_max_mm": 0.12,
    }]})
    assert rapport["statut"] == "FAIL"


def test_zone_fine_ne_prend_pas_la_plage_voisine_a_sa_frontiere(tmp_path):
    """Une plage adjacente ne doit pas imposer sa hauteur hors de la zone."""
    xml = ('<objects><object id="1"><range min_z="0" max_z="4">'
           '<option opt_key="layer_height">0.2</option></range><range min_z="4" max_z="6">'
           '<option opt_key="layer_height">0.1</option></range></object></objects>')
    source = _cube_3mf(tmp_path / "frontiere.3mf", ranges=xml)
    rapport = analyser(source, {"schema": 1, "intentions": [{
        "objet_id": "1", "type": "zone_fine", "min_z_mm": 4, "max_z_mm": 6, "hauteur_max_mm": 0.12,
    }]})
    assert rapport["statut"] == "PASS"


def test_tolerance_de_couverture_ne_relache_pas_la_hauteur_maximale(tmp_path):
    """Une hauteur déclarée trop forte échoue même avec un seuil de jointure plus large."""
    xml = ('<objects><object id="1"><range min_z="4" max_z="6">'
           '<option opt_key="layer_height">0.15</option></range></object></objects>')
    source = _cube_3mf(tmp_path / "hauteur-excessive.3mf", ranges=xml)
    rapport = analyser(source, {"schema": 1, "intentions": [{
        "objet_id": "1", "type": "zone_fine", "min_z_mm": 4, "max_z_mm": 6, "hauteur_max_mm": 0.12,
    }]}, tolerance_mm=0.05)
    assert rapport["statut"] == "FAIL"


def test_une_inclinaison_z_tres_faible_reste_non_verifiee(tmp_path):
    transform = "1 0 0.0000000005 0 1 0 0 0 1 0 0 0"
    source = _cube_3mf(tmp_path / "inclinaison-epsilon.3mf", transform=transform)
    rapport = analyser(source, {"schema": 1, "intentions": [{"objet_id": "1", "type": "totale"}]})
    assert rapport["statut"] == "NON VÉRIFIÉ"


def test_instance_non_imprimable_est_refusee_avant_lecture_geometrique(tmp_path):
    source = _cube_3mf(tmp_path / "non-imprimable.3mf", printable="0")
    rapport = analyser(source, {"schema": 1, "intentions": [{"objet_id": "1", "type": "totale"}]})
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert "non imprimable" in rapport["objets"][0]["erreur"]


def test_hauteur_de_base_lue_seulement_dans_reglages_projet(tmp_path):
    settings = '<config><object id="1"><metadata key="layer_height" value="0.33"/></object></config>'
    source = _cube_3mf(tmp_path / "base-projet.3mf", project_layer_height="0.2", model_settings=settings)
    rapport = analyser(source, {"schema": 1, "intentions": [{"objet_id": "1", "type": "totale"}]})
    assert rapport["objets"][0]["hauteur_base_projet"] == {"statut": "PASS", "valeur_mm": 0.2}


def test_declaration_json_liee_et_schema_booleen_sont_refuses(tmp_path, capsys):
    source = _cube_3mf(tmp_path / "cube.3mf")
    declaration = tmp_path / "intention.json"
    declaration.write_text('{"schema": true, "intentions": [{"objet_id": "1", "type": "totale"}]}')
    assert main([str(source), str(declaration)]) == 2
    assert json.loads(capsys.readouterr().out)["statut"] == "NON VÉRIFIÉ"
    declaration.unlink()
    cible = tmp_path / "intention-cible.json"
    cible.write_text('{"schema": 1, "intentions": [{"objet_id": "1", "type": "totale"}]}')
    declaration.symlink_to(cible)
    assert main([str(source), str(declaration)]) == 2
    assert json.loads(capsys.readouterr().out)["statut"] == "NON VÉRIFIÉ"


def test_archive_zip_invalide_retourne_non_verifie_sans_traceback(tmp_path, capsys):
    source = tmp_path / "zip-invalide.3mf"
    source.write_bytes(b"pas un ZIP")
    declaration = tmp_path / "intention.json"
    declaration.write_text('{"schema": 1, "intentions": [{"objet_id": "1", "type": "totale"}]}')
    assert main([str(source), str(declaration)]) == 2
    assert json.loads(capsys.readouterr().out)["statut"] == "NON VÉRIFIÉ"


def test_modele_principal_opc_ne_peut_pas_diverger_du_modele_controle(tmp_path):
    source = _cube_3mf(tmp_path / "source.3mf")
    archive = _copier_avec_modele_cible(source, tmp_path / "relation-opc.3mf", relation=True)
    rapport = analyser(archive, {"schema": 1, "intentions": [{"objet_id": "1", "type": "totale"}]})
    assert rapport["statut"] == "NON VÉRIFIÉ"


def test_path_opc_sur_instance_ne_peut_pas_diverger_du_modele_controle(tmp_path):
    source = _cube_3mf(tmp_path / "source.3mf")
    archive = _copier_avec_modele_cible(source, tmp_path / "item-path.3mf", item_path=True)
    rapport = analyser(archive, {"schema": 1, "intentions": [{"objet_id": "1", "type": "totale"}]})
    assert rapport["statut"] == "NON VÉRIFIÉ"


def test_composant_interne_unique_et_transform_z_identique_reste_qualifie(tmp_path):
    source = _cube_3mf(tmp_path / "source.3mf")
    archive = _archive_composant_interne(source, tmp_path / "composant-interne.3mf")
    rapport = analyser(archive, {"schema": 1, "intentions": [{"objet_id": "2", "type": "totale"}]})
    assert rapport["statut"] == "PASS"
    transformations = rapport["objets"][0]["transformations"]
    assert transformations["instance"]["z_inchange"] is True
    assert transformations["composant"]["translation_mm"] == [5.0, 6.0, 0.0]
    assert transformations["composant"]["coefficients_z"] == [0.0, 0.0, 1.0]


def test_transform_z_du_composant_interne_est_aussi_strict(tmp_path):
    source = _cube_3mf(tmp_path / "source.3mf")
    transform = "1 0 0.0000000005 0 1 0 0 0 1 5 6 0"
    archive = _archive_composant_interne(source, tmp_path / "composant-incline.3mf", transform=transform)
    rapport = analyser(archive, {"schema": 1, "intentions": [{"objet_id": "2", "type": "totale"}]})
    assert rapport["statut"] == "NON VÉRIFIÉ"


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
    sortie_historique = capsys.readouterr().out
    assert json.loads(sortie_historique)["statut"] == "PASS"
    rapport = analyser(source, declaration)
    assert sortie_historique == json.dumps(rapport, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def test_commande_benchab_route_le_controle_sans_slicer(tmp_path, capsys):
    source = _cube_3mf(tmp_path / "cube.3mf")
    declaration = tmp_path / "intention.json"
    declaration.write_text(json.dumps({"schema": 1, "intentions": [{"objet_id": "1", "type": "totale"}]}))
    prive = tmp_path / "prive"
    assert main_benchab(["--prive", str(prive), "init"]) == 0
    assert main_benchab(["--prive", str(prive), "controle", "plages-z", str(source), str(declaration)]) == 0
    assert '"statut": "PASS"' in capsys.readouterr().out
    sortie = prive / "rapports" / "plages-z.json"
    assert main_benchab([
        "--prive", str(prive), "controle", "plages-z", str(source), str(declaration),
        "--sortie", str(sortie),
    ]) == 0
    assert json.loads(capsys.readouterr().out)["rapport"] == str(sortie.absolute())
    assert json.loads(sortie.read_text(encoding="utf-8"))["statut"] == "PASS"


def test_cli_rapport_neuf_affiche_resume_court_et_detail_sur_demande(tmp_path, capsys):
    """Le rapport complet est publié une fois ; le résumé donne le statut, les objets et son chemin."""
    plages = "".join(
        f'<range min_z="{i / 10}" max_z="{(i + 1) / 10}">'
        '<option opt_key="layer_height">0.1</option></range>'
        for i in range(100)
    )
    xml = f'<objects><object id="1">{plages}</object></objects>'
    source = _cube_3mf(tmp_path / "cube-volumineux.3mf", ranges=xml)
    declaration = tmp_path / "intention.json"
    declaration.write_text(json.dumps({"schema": 1, "intentions": [{"objet_id": "1", "type": "totale"}]}))
    rapport_dir = Path(str(tmp_path).replace("/private/var/", "/var/", 1)) / "rapports"
    sortie = rapport_dir / "plages-z.json"

    assert main([str(source), str(declaration), "--sortie", str(sortie)]) == 0
    resume_texte = capsys.readouterr().out
    resume = json.loads(resume_texte)
    complet = json.loads(sortie.read_text(encoding="utf-8"))
    assert resume == {
        "statut": "PASS",
        "points_importants": [{
            "objet_id": "1", "intention": "totale", "statut": "PASS",
            "geometrie_z_mm": [0.0, 10.0], "intervalle_attendu_mm": [0.0, 10.0],
        }],
        "rapport": str(sortie.resolve()),
    }
    assert len(resume_texte) < len(json.dumps(complet, ensure_ascii=False, indent=2))
    assert len(complet["objets"][0]["couverture_explicite_mm"]) == 100

    detail_path = tmp_path / "rapports" / "detail.json"
    assert main([str(source), str(declaration), "--sortie", str(detail_path), "--detail"]) == 0
    detail = json.loads(capsys.readouterr().out)
    assert detail["rapport"] == str(detail_path.resolve())
    assert len(detail["objets"][0]["couverture_explicite_mm"]) == 100

    ancien = sortie.read_bytes()
    with pytest.raises(ValueError, match="Destination existante"):
        main([str(source), str(declaration), "--sortie", str(sortie)])
    assert sortie.read_bytes() == ancien


def test_resume_echec_conserve_le_message_exact_et_l_identifiant_objet(tmp_path, capsys):
    """Un échec reste attribué à l’objet dans le résumé et garde son message intégral dans le rapport."""
    source = _cube_3mf(tmp_path / "plages-invalides.3mf", ranges="<objects>")
    declaration = tmp_path / "intention.json"
    declaration.write_text(json.dumps({"schema": 1, "intentions": [{"objet_id": "1", "type": "totale"}]}))
    sortie = tmp_path / "prive" / "plages-z.json"

    assert main([str(source), str(declaration), "--sortie", str(sortie)]) == 2
    resume = json.loads(capsys.readouterr().out)
    rapport = json.loads(sortie.read_text(encoding="utf-8"))
    assert resume["statut"] == "NON VÉRIFIÉ"
    assert resume["points_importants"] == [{
        "objet_id": "1", "intention": "totale", "statut": "NON VÉRIFIÉ",
        "erreur": rapport["objets"][0]["erreur"],
    }]
    assert resume["points_importants"][0]["erreur"]
