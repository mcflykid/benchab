"""Tests du plan documentaire et de la restauration exclusive des matrices 3MF."""

import copy
import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
import trimesh

from benchab import reglages
from benchab.fabrication import anycubic, origine_3mf


def _binaire(racine):
    chemin = racine / "slicer-fixture"
    chemin.write_bytes(b"binaire qualifie de test")
    empreinte = hashlib.sha256(chemin.read_bytes()).hexdigest()
    configuration = {
        "machine": {"modeles_slicer": ["Anycubic Kobra S1", "Anycubic Kobra S1 Combo"], "buse_mm": 0.4},
        "slicer": {
            "version_qualifiee": "2.0.0.3",
            "sha256_binaire_matrices_origine": empreinte,
            "executable": str(chemin),
        }
    }
    return chemin, configuration


def _source(
    racine, *, layer_height="0.2", boite_centre=(10, 10, 4), modificateur_centre=(10, 10, 4), settings_suppl=None
):
    """Crée une géométrie synthétique en mm avec ses seuls réglages explicitement fournis."""
    chemin = racine / "source.3mf"
    boite = trimesh.creation.box((20, 20, 8))
    if boite_centre is not None:
        boite.apply_translation(boite_centre)
    modificateur = trimesh.creation.box((5, 5, 4))
    if modificateur_centre is not None:
        modificateur.apply_translation(modificateur_centre)
    settings = {"filament_colour": ["#FFFFFF"], "layer_height": layer_height, **(settings_suppl or {})}
    plaque = [
        {
            "name": "",
            "items": [
                {
                    "name": "Boite-sonde",
                    "mesh": boite,
                    "x": 10,
                    "y": 10,
                    "extruder": 1,
                    "volumes": [
                        {
                            "name": "Modificateur-sonde",
                            "mesh": modificateur,
                            "subtype": "modifier_part",
                            "settings": {"sparse_infill_density": "20%"},
                        }
                    ],
                }
            ],
        }
    ]
    anycubic.ecrire_3mf(plaque, settings, chemin, canonique=True, nom_source=chemin.name)
    return chemin


def _reecrire(source, cible, modifications, *, renommer=None):
    renommer = renommer or {}
    with ZipFile(source) as entree, ZipFile(cible, "w") as sortie:
        for info in entree.infolist():
            donnees = modifications.get(info.filename, entree.read(info.filename))
            info_sortie = copy.copy(info)
            info_sortie.filename = renommer.get(info.filename, info.filename)
            sortie.writestr(info_sortie, donnees)
        for nom, donnees in modifications.items():
            if nom not in entree.namelist():
                sortie.writestr(nom, donnees)
    return cible


def _modifier_xml(source, cible, membre, modification):
    with ZipFile(source) as archive:
        racine = ET.fromstring(archive.read(membre))
    modification(racine)
    return _reecrire(source, cible, {membre: ET.tostring(racine, encoding="utf-8", xml_declaration=True)})


def _native(source, cible, plan, *, gcode=b"G1 X1 Y2 Z3 F600\n"):
    with ZipFile(source) as archive:
        racine = ET.fromstring(archive.read("Metadata/model_settings.config"))
        modele = ET.fromstring(archive.read("3D/3dmodel.model"))
        pieces = ET.fromstring(archive.read("3D/Objects/pieces.model"))
    for lien in plan["liaisons"]:
        objet = next(
            o for o in racine.findall("object") if (o.find("metadata[@key='name']").get("value") == lien["objet_nom"])
        )
        partie = objet.findall("part")[lien["partie_index"]]
        partie.find("metadata[@key='matrix']").set("value", lien["matrice_predite_texte"])
    for composant in modele.findall(
        ".//{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}component"
    ):
        cle_path = next(cle for cle in composant.attrib if cle.endswith("}path") or cle == "path")
        identifiant = composant.get("objectid")
        composant.set(cle_path, f"/3D/Objects/native_{identifiant}.model")
    modifs = {
        "Metadata/model_settings.config": ET.tostring(racine, encoding="utf-8", xml_declaration=True),
        "3D/3dmodel.model": ET.tostring(modele, encoding="utf-8", xml_declaration=True),
        "Metadata/plate_1.gcode": gcode,
    }
    ns = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
    objects = pieces.find(ns + "resources").findall(ns + "object")
    fichiers_maillage = {}
    for objet in objects:
        identifiant = objet.get("id")
        local = copy.deepcopy(pieces)
        ressources = local.find(ns + "resources")
        for candidat in list(ressources.findall(ns + "object")):
            if candidat.get("id") != identifiant:
                ressources.remove(candidat)
        fichiers_maillage[f"3D/Objects/native_{identifiant}.model"] = ET.tostring(
            local, encoding="utf-8", xml_declaration=True
        )
    with ZipFile(source) as entree, ZipFile(cible, "w") as sortie:
        noms_entree = set(entree.namelist())
        for info in entree.infolist():
            if info.filename == "3D/Objects/pieces.model":
                continue
            info_sortie = copy.copy(info)
            sortie.writestr(info_sortie, modifs.get(info.filename, entree.read(info.filename)))
        for nom, donnees in modifs.items():
            if nom not in noms_entree:
                sortie.writestr(nom, donnees)
        for nom, donnees in fichiers_maillage.items():
            sortie.writestr(nom, donnees)
    return cible


def _avec_binaire(configuration, fonction):
    with patch.object(origine_3mf, "charger", return_value=configuration):
        return fonction()


def test_plan_compose_translation_et_restaure_seulement_les_matrices():
    """Vérifie V @ M en mm, les identités uniques et la conservation octet des autres membres natifs."""
    with tempfile.TemporaryDirectory(dir="/private/tmp") as dossier:
        racine = Path(dossier)
        binaire, configuration = _binaire(racine)
        source = _source(racine)

        def exercice():
            plan = origine_3mf.planifier(source, binaire=binaire)
            assert plan["statut"] == "PASS"
            assert len(plan["liaisons"]) == len(plan["permis"]) == 2
            assert plan["liaisons"][0]["identite"] == {
                "source_file": "source.3mf",
                "source_object_id": "0",
                "source_volume_id": "0",
            }
            assert plan["liaisons"][1]["identite"]["source_volume_id"] == "1"
            matrice = plan["liaisons"][1]["matrice_predite"]
            assert [matrice[i][3] for i in range(3)] == [20.0, 20.0, 8.0]
            natif = _native(source, racine / "natif.3mf", plan)
            sortie = racine / "restaure.3mf"
            resultat = origine_3mf.restaurer(source, natif, sortie, plan)
            assert resultat["statut"] == "PASS"
            assert resultat["integrite_finale"]["differences"] == []
            assert resultat["membres_non_config_identiques_au_natif"] is True
            with ZipFile(natif) as archive_natif, ZipFile(sortie) as archive_sortie:
                assert archive_natif.namelist() == archive_sortie.namelist()
                for nom in archive_natif.namelist():
                    if nom != "Metadata/model_settings.config":
                        assert hashlib.sha256(archive_natif.read(nom)).digest() == hashlib.sha256(
                            archive_sortie.read(nom)
                        ).digest()
            assert reglages.definition("matrix")["type"] == "inconnu"

        _avec_binaire(configuration, exercice)


def test_rotation_non_qualifiee_et_identite_dupliquee_restent_sans_permis():
    """Les rotations et identités de volume dupliquées restent NON VÉRIFIÉES."""
    with tempfile.TemporaryDirectory(dir="/private/tmp") as dossier:
        racine = Path(dossier)
        binaire, configuration = _binaire(racine)
        source = _source(racine)

        def exercice():
            rotation = _modifier_xml(
                source,
                racine / "rotation.3mf",
                "3D/3dmodel.model",
                lambda xml: xml.find(".//{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}component").set(
                    "transform", "0 -1 0 1 0 0 0 0 1 10 10 4"
                ),
            )
            plan_rotation = origine_3mf.planifier(rotation, binaire=binaire)
            assert plan_rotation["statut"] == "NON VÉRIFIÉ" and plan_rotation["permis"] == []

            def dupliquer(racine_xml):
                parties = racine_xml.find("object").findall("part")
                parties[1].find("metadata[@key='source_volume_id']").set("value", "0")

            duplicate = _modifier_xml(
                source, racine / "duplique.3mf", "Metadata/model_settings.config", dupliquer
            )
            plan_duplicate = origine_3mf.planifier(duplicate, binaire=binaire)
            assert plan_duplicate["statut"] == "NON VÉRIFIÉ" and plan_duplicate["permis"] == []

        _avec_binaire(configuration, exercice)


def test_subtype_non_officiel_parameter_modifier_est_non_qualifie():
    """Refuse le libellé inconnu parameter_modifier au lieu de le traiter comme un rôle 3MF valide."""
    with tempfile.TemporaryDirectory(dir="/private/tmp") as dossier:
        racine = Path(dossier)
        binaire, configuration = _binaire(racine)
        valide = _source(racine)

        def exercice():
            invalide = _modifier_xml(
                valide,
                racine / "subtype-inconnu.3mf",
                "Metadata/model_settings.config",
                lambda xml: xml.find("object/part[2]").set("subtype", "parameter_modifier"),
            )
            plan = origine_3mf.planifier(invalide, binaire=binaire)
            assert plan["statut"] == "NON VÉRIFIÉ"
            assert plan["permis"] == []
            assert any("Type de volume hors qualification" in raison for raison in plan["raisons"])

        _avec_binaire(configuration, exercice)


def test_matrice_identite_garde_liaison_sans_permission_inutile():
    """Conserve les deux liaisons ; seul le volume dont V @ M change reçoit une permission exacte."""
    with tempfile.TemporaryDirectory(dir="/private/tmp") as dossier:
        racine = Path(dossier)
        binaire, configuration = _binaire(racine)
        original_source = _source(racine, modificateur_centre=None)

        def decimaliser_identite(xml):
            partie = xml.find("object/part[2]")
            matrice = "1.0 0.0 0.0 0.0 0.0 1.0 0.0 0.0 0.0 0.0 1.0 0.0 0.0 0.0 0.0 1.0"
            partie.find("metadata[@key='matrix']").set("value", matrice)

        source = _modifier_xml(
            original_source, racine / "source-decimale.3mf", "Metadata/model_settings.config", decimaliser_identite
        )

        def exercice():
            plan = origine_3mf.planifier(source, binaire=binaire)
            assert plan["statut"] == "PASS"
            assert len(plan["liaisons"]) == 2
            assert plan["liaisons"][1]["matrice_source"] == [
                [1.0, 0.0, 0.0, 0.0],
                [0.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, 1.0, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ]
            assert plan["liaisons"][1]["matrice_predite_texte"] == plan["liaisons"][1]["matrice_source_texte"]
            assert [permission["chemin"] for permission in plan["permis"]] == [
                "objet/Boite-sonde/part/0/matrix"
            ]
            natif = _native(source, racine / "natif.3mf", plan)
            resultat = origine_3mf.restaurer(source, natif, racine / "restaure.3mf", plan)
            assert resultat["statut"] == "PASS"
            assert resultat["integrite_finale"]["differences"] == []

        _avec_binaire(configuration, exercice)


def test_deplacement_composant_natif_refuse_sans_creer_sortie():
    """Refuse un changement de translation composant même si les matrices de réglages correspondent au plan."""
    with tempfile.TemporaryDirectory(dir="/private/tmp") as dossier:
        racine = Path(dossier)
        binaire, configuration = _binaire(racine)
        source = _source(racine)

        def exercice():
            plan = origine_3mf.planifier(source, binaire=binaire)
            natif = _native(source, racine / "natif.3mf", plan)

            def deplacer(xml):
                component = xml.find(".//{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}component")
                component.set("transform", "1 0 0 0 1 0 0 0 1 11 10 4")

            deplace = _modifier_xml(natif, racine / "deplace.3mf", "3D/3dmodel.model", deplacer)
            sortie = racine / "refuse.3mf"
            with pytest.raises(ValueError, match="déplacée"):
                origine_3mf.restaurer(source, deplace, sortie, plan)
            assert not sortie.exists()

        _avec_binaire(configuration, exercice)


@pytest.mark.parametrize("mutation", ["role", "liaison", "permutation"])
def test_composant_natif_doit_garder_role_liaison_et_geometrie(mutation):
    """Refuse un changement de rôle, de référence composant ou de géométrie locale float32 en mm."""
    with tempfile.TemporaryDirectory(dir="/private/tmp") as dossier:
        racine = Path(dossier)
        binaire, configuration = _binaire(racine)
        source = _source(racine)

        def exercice():
            plan = origine_3mf.planifier(source, binaire=binaire)
            natif = _native(source, racine / "natif.3mf", plan)
            if mutation == "role":
                def changer_role(xml):
                    xml.find("object/part[2]").set("subtype", "normal_part")

                mauvais = _modifier_xml(
                    natif, racine / "role.3mf", "Metadata/model_settings.config", changer_role
                )
            elif mutation == "liaison":
                def casser_liaison(xml):
                    composants = xml.findall(
                        ".//{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}component"
                    )
                    composants[0].set("objectid", "999999")

                mauvais = _modifier_xml(natif, racine / "liaison.3mf", "3D/3dmodel.model", casser_liaison)
            else:
                with ZipFile(natif) as archive:
                    model = ET.fromstring(archive.read("3D/3dmodel.model"))
                    settings = ET.fromstring(archive.read("Metadata/model_settings.config"))
                composants = model.findall(
                    ".//{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}component"
                )
                id_premier, id_second = composants[0].get("objectid"), composants[1].get("objectid")
                composants[0].set("objectid", id_second)
                composants[1].set("objectid", id_premier)
                parties = settings.find("object").findall("part")
                parties[0].set("id", id_second)
                parties[1].set("id", id_premier)
                # Les IDs sont réindexés de façon cohérente dans les deux XML ; seule la liaison à la géométrie change.
                mauvais = _reecrire(
                    natif,
                    racine / "permutation.3mf",
                    {
                        "3D/3dmodel.model": ET.tostring(model, encoding="utf-8", xml_declaration=True),
                        "Metadata/model_settings.config": ET.tostring(
                            settings, encoding="utf-8", xml_declaration=True
                        ),
                    },
                )
            with pytest.raises(ValueError, match="Identité|Rôle|Géométrie|Liaison"):
                origine_3mf.restaurer(source, mauvais, racine / "refuse.3mf", plan)
            assert not (racine / "refuse.3mf").exists()

        _avec_binaire(configuration, exercice)


@pytest.mark.parametrize("cle,inconnue", [("layer_height", False), ("option_vendeur_inconnue", True)])
def test_reglage_connu_ou_inconnu_modifie_refuse(cle, inconnue):
    """Toute modification hors matrice, connue ou NON VÉRIFIÉE, interdit la restauration."""
    with tempfile.TemporaryDirectory(dir="/private/tmp") as dossier:
        racine = Path(dossier)
        binaire, configuration = _binaire(racine)
        source = _source(racine)

        def exercice():
            plan = origine_3mf.planifier(source, binaire=binaire)
            natif = _native(source, racine / "natif.3mf", plan)
            membre = "Metadata/project_settings.config"
            with ZipFile(natif) as archive:
                donnees = json.loads(archive.read(membre))
            donnees[cle] = "unseen" if inconnue else "0.3"
            modifie = _reecrire(natif, racine / "modifie.3mf", {membre: json.dumps(donnees).encode()})
            sortie = racine / "refuse.3mf"
            with pytest.raises(ValueError, match="Intégrité"):
                origine_3mf.restaurer(source, modifie, sortie, plan)
            assert not sortie.exists()

        _avec_binaire(configuration, exercice)


def test_matrice_native_inattendue_est_refusee():
    """Une matrice native qui dépasse la prédiction exacte ne bénéficie d’aucune permission joker."""
    with tempfile.TemporaryDirectory(dir="/private/tmp") as dossier:
        racine = Path(dossier)
        binaire, configuration = _binaire(racine)
        source = _source(racine)

        def exercice():
            plan = origine_3mf.planifier(source, binaire=binaire)
            natif = _native(source, racine / "natif.3mf", plan)

            def changer(xml):
                partie = xml.find("object/part[2]")
                matrice = partie.find("metadata[@key='matrix']")
                matrice.set("value", matrice.get("value").replace("20", "21", 1))

            mauvais = _modifier_xml(natif, racine / "mauvaise-matrice.3mf", "Metadata/model_settings.config", changer)
            with pytest.raises(ValueError, match="Intégrité"):
                origine_3mf.restaurer(source, mauvais, racine / "refuse.3mf", plan)

        _avec_binaire(configuration, exercice)


def test_gcode_altere_pendant_restauration_est_detecte():
    """Tout changement d’octets G-code pendant la copie atomique annule la sortie finale."""
    with tempfile.TemporaryDirectory(dir="/private/tmp") as dossier:
        racine = Path(dossier)
        binaire, configuration = _binaire(racine)
        source = _source(racine)

        def exercice():
            plan = origine_3mf.planifier(source, binaire=binaire)
            natif = _native(source, racine / "natif.3mf", plan)
            from zipfile import ZipFile

            original = ZipFile.open

            class EcritureCorrompue:
                def __init__(self, cible):
                    self.cible = cible

                def __enter__(self):
                    self.cible.__enter__()
                    return self

                def __exit__(self, *args):
                    return self.cible.__exit__(*args)

                def write(self, donnees):
                    return self.cible.write(donnees + b"; modification injectee\n")

            def ouverture_corrompue(archive, info, mode="r", *args, **kwargs):
                nom = info.filename if hasattr(info, "filename") else str(info)
                flux = original(archive, info, mode, *args, **kwargs)
                return EcritureCorrompue(flux) if mode == "w" and nom.endswith(".gcode") else flux

            sortie = racine / "refuse.3mf"
            with patch.object(ZipFile, "open", ouverture_corrompue):
                with pytest.raises(ValueError, match="membre natif"):
                    origine_3mf.restaurer(source, natif, sortie, plan)
            assert not sortie.exists()

        _avec_binaire(configuration, exercice)


@pytest.mark.parametrize("moment", ["avant", "apres"])
def test_sha_source_reverifie_autour_publication(moment):
    """Une mutation de l’entrée avant ou après le lien exclusif annule la livraison."""
    with tempfile.TemporaryDirectory(dir="/private/tmp") as dossier:
        racine = Path(dossier)
        binaire, configuration = _binaire(racine)
        source = _source(racine)

        def exercice():
            plan = origine_3mf.planifier(source, binaire=binaire)
            natif = _native(source, racine / "natif.3mf", plan)
            sortie = racine / "restaure.3mf"

            def modifier_source():
                source.write_bytes(source.read_bytes() + b"\nsource modifiee")

            if moment == "avant":
                original = origine_3mf._destination_neuve
                appels = 0

                def destination_mutable(valeur):
                    nonlocal appels
                    resultat = original(valeur)
                    appels += 1
                    if appels == 2:
                        modifier_source()
                    return resultat

                patcher = patch.object(origine_3mf, "_destination_neuve", side_effect=destination_mutable)
            else:
                original = origine_3mf.os.link

                def lien_mutable(source_temp, destination):
                    original(source_temp, destination)
                    modifier_source()

                patcher = patch.object(origine_3mf.os, "link", side_effect=lien_mutable)
            with patcher, pytest.raises(ValueError, match="Entrée modifiée"):
                origine_3mf.restaurer(source, natif, sortie, plan)
            assert not sortie.exists()

        _avec_binaire(configuration, exercice)


def test_cible_remplacee_par_tiers_est_preservee_au_nettoyage():
    """Le rollback ne supprime pas une cible tierce qui remplace l’inode publié."""
    with tempfile.TemporaryDirectory(dir="/private/tmp") as dossier:
        racine = Path(dossier)
        binaire, configuration = _binaire(racine)
        source = _source(racine)

        def exercice():
            plan = origine_3mf.planifier(source, binaire=binaire)
            natif = _native(source, racine / "natif.3mf", plan)
            sortie = racine / "sortie.3mf"
            original = origine_3mf.os.link

            def remplacer(source_temp, destination):
                original(source_temp, destination)
                destination.unlink()
                destination.write_bytes(b"cible tierce")

            with patch.object(origine_3mf.os, "link", side_effect=remplacer):
                with pytest.raises(ValueError, match="Cible remplacée"):
                    origine_3mf.restaurer(source, natif, sortie, plan)
            assert sortie.read_bytes() == b"cible tierce"

        _avec_binaire(configuration, exercice)


def test_racine_privee_depuis_cwd_et_non_depuis_le_module(monkeypatch):
    """Une installation en wheel utilise le privé du cwd explicite, sans site-packages/prive."""
    with tempfile.TemporaryDirectory(dir="/private/tmp") as dossier:
        racine = Path(dossier) / "atelier"
        prive = racine / "prive"
        prive.mkdir(parents=True)
        binaire, configuration = _binaire(prive)
        source = _source(prive)
        monkeypatch.chdir(racine)

        def exercice():
            plan = origine_3mf.planifier(source, binaire=binaire)
            natif = _native(source, prive / "natif.3mf", plan)
            sortie = prive / "restaure.3mf"
            resultat = origine_3mf.restaurer(source, natif, sortie, plan)
            assert resultat["statut"] == "PASS"
            assert sortie.is_relative_to(prive)

        _avec_binaire(configuration, exercice)


def test_binaire_non_qualifie_retourne_permis_vides():
    """Un binaire dont la SHA ne correspond pas reste NON VÉRIFIÉ, sans autorisation de matrice."""
    with tempfile.TemporaryDirectory(dir="/private/tmp") as dossier:
        racine = Path(dossier)
        binaire, configuration = _binaire(racine)
        configuration["slicer"]["sha256_binaire_matrices_origine"] = "0" * 64
        source = _source(racine)

        def exercice():
            plan = origine_3mf.planifier(source, binaire=binaire)
            assert plan["statut"] == "NON VÉRIFIÉ"
            assert plan["permis"] == []

        _avec_binaire(configuration, exercice)


def test_mono_volume_et_plan_perime_ne_recoivent_aucune_restaurabilite():
    """Aucune matrice mono-volume n’est normalisée et un plan lié à un autre SHA est refusé."""
    with tempfile.TemporaryDirectory(dir="/private/tmp") as dossier:
        racine = Path(dossier)
        binaire, configuration = _binaire(racine)
        source = _source(racine)

        def exercice():
            mono = racine / "mono.3mf"
            mesh = trimesh.creation.box((10, 10, 4))
            mesh.apply_translation((5, 5, 2))
            anycubic.ecrire_3mf(
                [{"name": "", "items": [{"name": "Bloc", "mesh": mesh, "x": 10, "y": 10, "extruder": 1}]}],
                {"filament_colour": ["#FFFFFF"]},
                mono,
                canonique=True,
                nom_source=mono.name,
            )
            plan_mono = origine_3mf.planifier(mono, binaire=binaire)
            assert plan_mono["statut"] == "NON VÉRIFIÉ" and plan_mono["permis"] == []

            plan = origine_3mf.planifier(source, binaire=binaire)
            natif = _native(source, racine / "natif.3mf", plan)
            source.write_bytes(source.read_bytes() + b"\n")
            with pytest.raises(ValueError, match="périmé|différent"):
                origine_3mf.restaurer(source, natif, racine / "refuse.3mf", plan)
            assert not (racine / "refuse.3mf").exists()

        _avec_binaire(configuration, exercice)


def test_symlink_traversee_archive_et_parent_de_sortie_refuses():
    """Les liens, chemins de traversée et membres ZIP dangereux ne donnent aucun plan ni sortie."""
    with tempfile.TemporaryDirectory(dir="/private/tmp") as dossier:
        racine = Path(dossier)
        binaire, configuration = _binaire(racine)
        source = _source(racine)

        def exercice():
            lien_source = racine / "alias.3mf"
            lien_source.symlink_to(source)
            plan_lien = origine_3mf.planifier(lien_source, binaire=binaire)
            assert plan_lien["statut"] == "NON VÉRIFIÉ" and plan_lien["permis"] == []
            plan_traversee = origine_3mf.planifier(racine / ".." / source.name, binaire=binaire)
            assert plan_traversee["statut"] == "NON VÉRIFIÉ" and plan_traversee["permis"] == []

            avec_traversee = _reecrire(
                source,
                racine / "zip-traverse.3mf",
                {"../escape.txt": b"refuse"},
            )
            plan_archive = origine_3mf.planifier(avec_traversee, binaire=binaire)
            assert plan_archive["statut"] == "NON VÉRIFIÉ" and plan_archive["permis"] == []

            plan = origine_3mf.planifier(source, binaire=binaire)
            natif = _native(source, racine / "natif.3mf", plan)
            parent_lie = racine / "parent-lie"
            parent_lie.symlink_to(racine, target_is_directory=True)
            sortie = parent_lie / "sortie.3mf"
            with pytest.raises(ValueError, match="symbolique"):
                origine_3mf.restaurer(source, natif, sortie, plan)
            assert not (racine / "sortie.3mf").exists()

        _avec_binaire(configuration, exercice)


@pytest.mark.parametrize('mutation', [None, 'reglage', 'matrice', 'destination', 'symlink'])
def test_lanceur_raccorde_plan_avant_moteur_et_refuse_imprevus(tmp_path, monkeypatch, mutation):
    """Simule le contrat prévu ou une mutation ; seuls les octets validés atteignent la destination."""
    from types import SimpleNamespace

    from benchab.fabrication import trancher

    source = _source(tmp_path, settings_suppl={
        'filament_type': ['PLA'], 'filament_settings_id': ['PLA fixture'], 'filament_ids': ['ID-fixture'],
        'printer_model': 'Anycubic Kobra S1', 'nozzle_diameter': ['0.4']
    })
    _, configuration = _binaire(tmp_path)
    configuration['slicer']['plaques_max'] = 30
    monkeypatch.setattr(origine_3mf, 'charger', lambda: configuration)
    monkeypatch.setattr(trancher, 'charger', lambda: configuration)
    validations = []
    monkeypatch.setattr(anycubic, '_valider_tranchage', lambda *a: validations.append(a))

    def moteur(commande, **options):
        """Simule les seuls octets de sortie ; aucun processus ni réglage source modifié."""
        entree = Path(commande[-1])
        plan = origine_3mf.planifier(entree)
        assert plan['statut'] == 'PASS'
        cible = Path(commande[commande.index('--outputdir') + 1]) / commande[commande.index('--export-3mf') + 1]
        _native(entree, cible, plan)
        if mutation in {'reglage', 'matrice'}:
            with ZipFile(cible) as z:
                membres = {i.filename: (copy.copy(i), z.read(i.filename)) for i in z.infolist()}
            if mutation == 'reglage':
                nom = 'Metadata/project_settings.config'
                config = json.loads(membres[nom][1])
                config['layer_height'] = '0.28'
                membres[nom] = (membres[nom][0], json.dumps(config).encode())
            else:
                nom = 'Metadata/model_settings.config'
                xml = ET.fromstring(membres[nom][1])
                xml.find("object/part/metadata[@key='matrix']").set('value', 'matrice inattendue')
                membres[nom] = (membres[nom][0], ET.tostring(xml))
            with ZipFile(cible, 'w') as z:
                for info, donnees in membres.values():
                    z.writestr(info, donnees)
        if mutation == 'destination':
            destination.write_bytes(b'cible tierce apparue pendant le moteur')
        elif mutation == 'symlink':
            tiers = tmp_path / 'tiers.3mf'
            tiers.write_bytes(b'cible du lien tierce intouchable')
            destination.symlink_to(tiers)
        return SimpleNamespace(pid=987654321, returncode=None)

    monkeypatch.setattr(trancher.subprocess, 'Popen', moteur)
    monkeypatch.setattr(
        trancher.os, 'wait4', lambda pid, flags: (pid, 0, SimpleNamespace(ru_utime=0, ru_stime=0, ru_maxrss=0))
    )
    destination = tmp_path / 'final.3mf'
    resultat = trancher._executer({'source': str(source), 'destination': str(destination), 'usage': 'temoin'})
    assert resultat['plan_matrices_origine']['statut'] == 'PASS'
    if mutation is None:
        assert resultat['code'] == 0
        assert resultat['restauration_matrices_origine']['integrite_finale']['differences'] == []
        assert destination.is_file() and len(validations) == 1
    else:
        assert resultat['code'] == 2 and resultat['validation_sortie']['statut'] == 'FAIL'
        if mutation == 'destination':
            assert destination.read_bytes() == b'cible tierce apparue pendant le moteur'
        elif mutation == 'symlink':
            assert destination.is_symlink()
            assert destination.read_bytes() == b'cible du lien tierce intouchable'
        else:
            assert not destination.exists() and validations == []
