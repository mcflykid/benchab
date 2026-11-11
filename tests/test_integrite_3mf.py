"""Une mutation tardive non déclarée ne peut pas devenir un fichier livré."""

import json
from zipfile import ZipFile

import pytest

from benchab import integrite_3mf


def _archive(path, projet="5", objet="8", plaque="200", partie="9"):
    with ZipFile(path, "w") as z:
        z.writestr("Metadata/project_settings.config", json.dumps({"top_shell_layers": projet}))
        z.writestr(
            "Metadata/model_settings.config",
            '<config><object id="1"><metadata key="name" value="Piece"/>'
            f'<metadata key="top_shell_layers" value="{objet}"/><part id="1">'
            f'<metadata key="top_shell_layers" value="{partie}"/></part></object><plate>'
            f'<metadata key="outer_wall_speed" value="{plaque}"/></plate></config>',
        )


def test_mutation_non_declaree_refusee_sur_toutes_les_portees(tmp_path):
    """Injecte une différence projet/objet/plaque, valeurs natives ; attend FAIL, sans autorisation implicite."""
    avant = tmp_path / "avant.3mf"
    apres = tmp_path / "apres.3mf"
    _archive(avant)
    for p, o, pl, partie in [
        ("6", "8", "200", "9"),
        ("5", "9", "200", "9"),
        ("5", "8", "201", "9"),
        ("5", "8", "200", "10"),
    ]:
        _archive(apres, p, o, pl, partie)
        with pytest.raises(ValueError, match="Intégrité"):
            integrite_3mf.verifier(avant, apres)


def test_objet_huit_et_projet_cinq_conserves_et_plan_exact(tmp_path):
    """Conserve 8/5 couches et exige les valeurs prévues ; PASS pour copie, FAIL si différence attendue manque."""
    a, b = tmp_path / "a.3mf", tmp_path / "b.3mf"
    _archive(a)
    _archive(b)
    assert integrite_3mf.verifier(a, b)["statut"] == "PASS"
    plan = [integrite_3mf.autorisation("projet/top_shell_layers", "5", "6")]
    with pytest.raises(ValueError, match="Intégrité"):
        integrite_3mf.verifier(a, b, plan)
    _archive(b, projet="6")
    assert integrite_3mf.verifier(a, b, plan)["statut"] == "PASS"


@pytest.mark.parametrize('role', ['projet', 'objet', 'partie', 'nom_forge'])
def test_portee_matrice_reelle_pas_deduite_du_nom(tmp_path, role):
    """Seule la métadonnée de partie connue est déclarable ; un nom contenant /part/0 ne contourne rien."""
    from xml.etree import ElementTree as ET

    chemins = []
    for indice, valeur in enumerate(['identite fixture', 'translation fixture']):
        fichier = tmp_path / f'fixture-{indice}.3mf'
        projet = {'matrix': valeur} if role == 'projet' else {}
        root = ET.Element('config')
        obj = ET.SubElement(root, 'object', id='1')
        ET.SubElement(obj, 'metadata', key='name', value='Piece/part/0' if role == 'nom_forge' else 'Piece')
        if role != 'projet':
            cible = ET.SubElement(obj, 'part', id='1') if role == 'partie' else obj
            ET.SubElement(cible, 'metadata', key='matrix', value=valeur)
        with ZipFile(fichier, 'w') as z:
            z.writestr('Metadata/project_settings.config', json.dumps(projet))
            z.writestr('Metadata/model_settings.config', ET.tostring(root))
        chemins.append(fichier)
    permis = integrite_3mf.difference(*(integrite_3mf.instantane(p) for p in chemins))
    if role == 'partie':
        assert integrite_3mf.verifier(*chemins, permis)['statut'] == 'PASS'
    else:
        with pytest.raises(ValueError, match='inconnu'):
            integrite_3mf.verifier(*chemins, permis)


def test_role_physique_du_volume_ne_peut_pas_changer_silencieusement(tmp_path):
    """Un modificateur devenu corps imprimé est FAIL, même si toutes ses métadonnées restent identiques."""
    source, natif = tmp_path / 'source.3mf', tmp_path / 'natif.3mf'
    for path, role in [(source, 'parameter_modifier'), (natif, 'normal_part')]:
        with ZipFile(path, 'w') as z:
            z.writestr('Metadata/project_settings.config', '{}')
            z.writestr('Metadata/model_settings.config',
                       '<config><object id="1"><metadata key="name" value="Fixture"/>'
                       f'<part id="1" subtype="{role}"><metadata key="sparse_infill_density" value="20%"/>'
                       '</part></object></config>')
    with pytest.raises(ValueError, match='Intégrité'):
        integrite_3mf.verifier(source, natif)


def test_plages_couches_font_partie_du_differentiel(tmp_path):
    """Une hauteur 0,20 devenue 0,24 mm sans permission échoue ; le XML hors modèle n'est pas ignoré."""
    a, b = tmp_path / 'a.3mf', tmp_path / 'b.3mf'
    for fichier, hauteur in [(a, '0.20'), (b, '0.24')]:
        _archive(fichier)
        with ZipFile(fichier, 'a') as archive:
            archive.writestr('Metadata/layer_config_ranges.xml',
                             '<objects><object id="1"><range min_z="0" max_z="4">'
                             f'<option opt_key="layer_height">{hauteur}</option></range></object></objects>')
    with pytest.raises(ValueError, match='Intégrité'):
        integrite_3mf.verifier(a, b)
    permis = [integrite_3mf.autorisation('couches/objet/0/plage/0/layer_height', '0.20', '0.24')]
    assert integrite_3mf.verifier(a, b, permis)['statut'] == 'PASS'


def test_option_de_plage_inconnue_ne_peut_etre_supprimee_par_l_outil(tmp_path):
    """Un profil futur inconnu reste intact ; ecrire_profils refuse sa suppression, sans livrer le 3MF."""
    from benchab.fabrication.couches_variables import ecrire_profils

    source, destination = tmp_path / 'source.3mf', tmp_path / 'sortie.3mf'
    _archive(source)
    with ZipFile(source, 'a') as archive:
        archive.writestr('Metadata/layer_config_ranges.xml',
                         '<objects><object id="1"><range min_z="0" max_z="4">'
                         '<option opt_key="fixture_option_future">intact</option></range></object></objects>')
    avant = source.read_bytes()
    with pytest.raises(ValueError, match='inconnu'):
        ecrire_profils(source, destination, {1: [0, .2, 4, .2]})
    assert source.read_bytes() == avant and not destination.exists()


def test_profil_de_couches_non_interprete_reste_immuable(tmp_path):
    """Une représentation opaque n'est jamais modifiée, même avec une différence déclarée."""
    a, b = tmp_path / 'a.3mf', tmp_path / 'b.3mf'
    for fichier, valeur in [(a, 'fixture originale'), (b, 'fixture modifiée')]:
        _archive(fichier)
        with ZipFile(fichier, 'a') as archive:
            archive.writestr('Metadata/layer_heights_profile.txt', valeur)
    permis = integrite_3mf.difference(integrite_3mf.instantane(a), integrite_3mf.instantane(b))
    with pytest.raises(ValueError, match='inconnu'):
        integrite_3mf.verifier(a, b, permis)


def test_couches_variables_ne_supprime_pas_un_autre_reglage_de_plage(tmp_path):
    """Une hauteur seule ne permet pas d'effacer top_shell_layers=8 ; source intacte et aucune livraison."""
    from benchab.fabrication.couches_variables import ecrire_profils

    source, destination = tmp_path / 'source.3mf', tmp_path / 'sortie.3mf'
    _archive(source)
    with ZipFile(source, 'a') as archive:
        archive.writestr('Metadata/layer_config_ranges.xml',
                         '<objects><object id="1"><range min_z="0" max_z="4">'
                         '<option opt_key="layer_height">0.20</option>'
                         '<option opt_key="top_shell_layers">8</option></range></object></objects>')
    avant = source.read_bytes()
    with pytest.raises(ValueError, match='seules hauteurs'):
        ecrire_profils(source, destination, {1: [0, .24, 4, .24]})
    assert source.read_bytes() == avant and not destination.exists()


def _archive_structure(path, plaque=0, transform="1 0 0 0 1 0 0 0 1 0 0 0", oid="1"):
    from xml.etree import ElementTree as ET

    racine = ET.Element("config")
    objet = ET.SubElement(racine, "object", id=oid)
    ET.SubElement(objet, "metadata", key="name", value="Fixture")
    for rang in range(2):
        p = ET.SubElement(racine, "plate")
        if rang == plaque:
            i = ET.SubElement(p, "model_instance")
            for cle, valeur in (("object_id", oid), ("instance_id", "0"), ("identify_id", "8")):
                ET.SubElement(i, "metadata", key=cle, value=valeur)
    assemblage = ET.SubElement(racine, "assemble")
    ET.SubElement(assemblage, "assemble_item", object_id=oid, instance_id="0", transform=transform)
    with ZipFile(path, "w") as archive:
        archive.writestr("Metadata/model_settings.config", ET.tostring(racine))
    return path


def test_affectation_d_instance_exige_la_declaration_exacte(tmp_path):
    """Déplacement ou suppression d'une instance : FAIL par défaut, déplacement exact déclaré : PASS."""
    source = _archive_structure(tmp_path / "source.3mf")
    deplace = _archive_structure(tmp_path / "deplace.3mf", plaque=1)
    absent = _archive_structure(tmp_path / "sans-instance.3mf", plaque=None)
    for fichier in (deplace, absent):
        with pytest.raises(ValueError, match="structure/plaque"):
            integrite_3mf.verifier(source, fichier)
    a, b = integrite_3mf.instantane(source), integrite_3mf.instantane(deplace)
    permis = integrite_3mf.difference(a, b)
    assert {d["chemin"] for d in permis} == {"structure/plaque/0/instance/0", "structure/plaque/1/instance/0"}
    assert integrite_3mf.verifier(source, deplace, permis)["statut"] == "PASS"


def test_renumerotation_et_placement_d_assemblage(tmp_path):
    """La renumérotation d'un objet nommé préserve sa liaison, sa translation de 8 mm reste une différence."""
    source = _archive_structure(tmp_path / "source.3mf")
    renumerote = _archive_structure(tmp_path / "renumerote.3mf", oid="42")
    assert integrite_3mf.verifier(source, renumerote)["differences"] == []
    translate = _archive_structure(tmp_path / "translate.3mf", transform="1 0 0 0 1 0 0 0 1 8 0 0")
    with pytest.raises(ValueError, match="structure/assemblage"):
        integrite_3mf.verifier(source, translate)


@pytest.mark.parametrize(
    "membre,contenu",
    [
        ("Metadata/model_settings.config", b"<!DOCTYPE config><config/>"),
        ("Metadata/layer_config_ranges.xml", b"<!DOCTYPE objects><objects/>"),
    ],
)
def test_integrite_refuse_declarations_xml(tmp_path, membre, contenu):
    """L’intégrité refuse les déclarations XML, même si les réglages sont vides."""
    source = tmp_path / "source.3mf"
    with ZipFile(source, "w") as archive:
        archive.writestr(membre, contenu)
    with pytest.raises(ValueError, match="DOCTYPE"):
        integrite_3mf.instantane(source)
    with pytest.raises(ValueError, match="DOCTYPE"):
        if membre.endswith(".xml"):
            integrite_3mf.instantane_config({}, plages_couches=contenu)
        else:
            integrite_3mf.instantane_config({}, modele=contenu)


def test_integrite_profile_opaque_emprunte_les_blocs_bornes(tmp_path):
    """Le profil opaque conserve son SHA sur plus d'un bloc de lecture."""
    import hashlib

    source = tmp_path / "source.3mf"
    membre = "Metadata/layer_heights_profile.txt"
    contenu = b"0 0.2\n" * 14000
    with ZipFile(source, "w") as archive:
        archive.writestr(membre, contenu)
    etat, types = integrite_3mf.instantane(source, avec_types=True)
    cle = "membre_non_interprete/" + membre
    assert etat[cle] == hashlib.sha256(contenu).hexdigest()
    assert types[cle]["type"] == "inconnu"


@pytest.mark.parametrize("lecteur", ["optimisation", "plaques", "resultats"])
def test_lecteurs_fabrication_utilisent_le_xml_borne(tmp_path, lecteur):
    """Les accès de fabrication refusent le même document que le contrôle d'intégrité."""
    from benchab.fabrication import anycubic, optimisation

    source = tmp_path / "source.3mf"
    xml = (
        b'<!DOCTYPE config><config><object id="1"/><plate><metadata key="plater_name" value="T"/>'
        b'<metadata key="index" value="1"/><metadata key="prediction" value="1"/>'
        b'<metadata key="weight" value="1"/></plate></config>'
    )
    with ZipFile(source, "w") as archive:
        archive.writestr("Metadata/project_settings.config", '{"top_shell_layers":"5"}')
        archive.writestr("Metadata/model_settings.config", xml)
        archive.writestr("Metadata/slice_info.config", xml)
        archive.writestr("3D/model.model", b"<model/>")
    lire = {"optimisation": optimisation.lire_3mf, "plaques": anycubic.noms_plaques, "resultats": anycubic.resultats}
    with pytest.raises(ValueError, match="DOCTYPE"):
        lire[lecteur](source)
