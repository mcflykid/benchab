"""Prépare et restaure les matrices d’origine qualifiées des 3MF multi-volumes."""

from __future__ import annotations

import copy
import hashlib
import math
import os
import shutil
import struct
import tempfile
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from benchab import integrite_3mf, reglages
from benchab.archives_3mf import Archive3MF
from benchab.machine.configuration import charger

_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
_PROD = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
_MAX_ARCHIVE = 512 * 1024 * 1024
_MAX_SETTINGS = 8 * 1024 * 1024
_MAX_VERTICES = 3_000_000
_TOLERANCE_MM = 1e-7


class _NonQualifie(ValueError):
    """Forme lisible mais hors contrat de restauration matricielle qualifiée."""


def _sha256(chemin: Path, maximum: int) -> str:
    if chemin.stat().st_size > maximum:
        raise ValueError("Fichier trop volumineux pour l’inspection bornée")
    hachage = hashlib.sha256()
    with chemin.open("rb") as flux:
        for bloc in iter(lambda: flux.read(1024 * 1024), b""):
            hachage.update(bloc)
    return hachage.hexdigest()


def _fichier_regulier(valeur, *, doit_exister=True) -> Path:
    """Valide un chemin sans lien ; FAIL sur traversée/non-fichier, aucune modification du 3MF."""
    from benchab.chemins import sans_lien

    brut = Path(valeur).expanduser()
    if ".." in brut.parts:
        raise ValueError("Traversée de chemin refusée")
    chemin = sans_lien(brut, strict=doit_exister)
    if doit_exister:
        chemin = chemin.resolve(strict=True)
        if not chemin.is_file():
            raise ValueError("Fichier régulier requis")
    return chemin


def _racine_privee(chemin: Path) -> bool:
    """Indique si la copie reste privée ou jetable selon la politique commune ; aucune écriture."""
    from benchab.qualite.ecritures import sortie_privee

    try:
        sortie_privee(chemin)
    except ValueError:
        return False
    return True


def _destination_neuve(valeur) -> tuple[Path, Path]:
    """Valide une copie neuve et son parent existant ; FAIL sur lien/hors atelier, sans écriture."""
    from benchab.qualite.ecritures import destination_neuve

    sortie = destination_neuve(_fichier_regulier(valeur, doit_exister=False))
    parent = sortie.parent.resolve(strict=True)
    return parent / sortie.name, parent


def _matrice_4x4(texte: str, origine: str) -> list[list[float]]:
    try:
        valeurs = [float(nombre) for nombre in texte.split()]
    except (AttributeError, ValueError) as exc:
        raise _NonQualifie(f"Matrice {origine} illisible") from exc
    if len(valeurs) != 16 or not all(math.isfinite(v) for v in valeurs):
        raise _NonQualifie(f"Matrice {origine} non finie ou de forme différente de 4×4")
    return [valeurs[i : i + 4] for i in range(0, 16, 4)]


def _matrice_composant(texte: str | None) -> list[list[float]]:
    if texte is None:
        return [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0, 0, 0, 1]]
    try:
        valeurs = [float(nombre) for nombre in texte.split()]
    except ValueError as exc:
        raise _NonQualifie("Transformation de composant illisible") from exc
    if len(valeurs) != 12 or not all(math.isfinite(v) for v in valeurs):
        raise _NonQualifie("Transformation de composant invalide")
    return [
        [valeurs[0], valeurs[3], valeurs[6], valeurs[9]],
        [valeurs[1], valeurs[4], valeurs[7], valeurs[10]],
        [valeurs[2], valeurs[5], valeurs[8], valeurs[11]],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _translation_pure(matrice) -> bool:
    attendu = [[1, 0, 0, matrice[0][3]], [0, 1, 0, matrice[1][3]], [0, 0, 1, matrice[2][3]], [0, 0, 0, 1]]
    return all(abs(matrice[i][j] - attendu[i][j]) <= 1e-12 for i in range(4) for j in range(4))


def _multiplier(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _texte_matrice(matrice) -> str:
    return " ".join(format(float(valeur), ".17g") for ligne in matrice for valeur in ligne)


def _matrices_egales(a, b) -> bool:
    return all(abs(a[i][j] - b[i][j]) <= 1e-12 for i in range(4) for j in range(4))


def _valeur_meta(noeud, cle, *, portee="origine_part") -> str:
    definitions = reglages.definition(cle, portee=portee)
    if definitions.get("type") != "chaine":
        raise _NonQualifie(f"Portée structurelle non définie pour {cle}")
    elements = noeud.findall(f"metadata[@key='{cle}']")
    if len(elements) != 1:
        raise _NonQualifie(f"Métadonnée absente ou ambiguë : {cle}")
    valeur = elements[0].get("value", elements[0].text or "")
    if not isinstance(valeur, str):
        raise _NonQualifie(f"Métadonnée structurelle non textuelle : {cle}")
    return valeur


def _archive_lecture(chemin: Path) -> Archive3MF:
    """Ouvre une archive de lecture avec les plafonds de matrice qualifiés, sans suivre de lien."""
    return Archive3MF(
        chemin,
        taille_max_membre=_MAX_ARCHIVE,
        taille_max_totale=_MAX_ARCHIVE,
        taille_max_xml=64 * 1024 * 1024,
    )


def _lire_xml(archive: Archive3MF, nom: str, maximum: int) -> ET.Element:
    """Lit un XML depuis le contexte 3MF commun ; taille en octets, DTD/ENTITY refusés."""
    return _lire_xml_borne(archive, nom, maximum)


def _lire_xml_borne(archive: Archive3MF, nom: str, maximum: int) -> ET.Element:
    """Lit un XML depuis le contexte 3MF unique ; limite en octets et DTD/ENTITY refusés."""
    try:
        if archive.taille_membre(nom) > maximum:
            raise _NonQualifie(f"Membre XML trop volumineux : {nom}")
        return archive.xml(nom)
    except KeyError as exc:
        raise _NonQualifie(f"Membre requis absent : {nom}") from exc
    except ValueError as exc:
        raise _NonQualifie(str(exc)) from exc
    except ET.ParseError as exc:
        raise _NonQualifie(f"Membre XML illisible : {nom}") from exc


def _configuration_binaire(binaire=None):
    configuration = charger()["slicer"]
    version = configuration.get("version_qualifiee")
    attendu = configuration.get("sha256_binaire_matrices_origine")
    chemin_configure = configuration.get("executable")
    chemin = _fichier_regulier(binaire or chemin_configure)
    empreinte = _sha256(chemin, 2 * 1024 * 1024 * 1024)
    qualifie = version == "2.0.0.3" and isinstance(attendu, str) and empreinte == attendu
    return {
        "chemin": str(chemin),
        "sha256": empreinte,
        "sha256_attendu": attendu,
        "version_qualifiee": version,
        "qualifie": qualifie,
    }


def _objets_composants(archive: Archive3MF):
    """Lit les objets du modèle principal depuis l’archive épinglée, sans écrire le 3MF."""
    racine = _lire_xml(archive, "3D/3dmodel.model", 64 * 1024 * 1024)
    modeles = {"3D/3dmodel.model": racine}
    objets = {}
    for objet in racine.findall(f"{{{_NS}}}resources/{{{_NS}}}object"):
        identifiant = objet.get("id")
        if identifiant is None or identifiant in objets:
            raise _NonQualifie("Objet 3MF principal absent ou ambigu")
        objets[identifiant] = objet
    return modeles, objets


def _lire_modeles_pieces(archive: Archive3MF, modeles):
    """Lit les objets de pièces avec les budgets du même contexte ZIP."""
    nom = "3D/Objects/pieces.model"
    return _lire_objets_maillage(archive, "/" + nom, {})


def _membre_composant(chemin: str | None) -> str:
    """Convertit un chemin 3MF absolu de composant en membre interne sûr du ZIP."""
    if not isinstance(chemin, str) or not chemin.startswith("/") or "\\" in chemin or "%" in chemin:
        raise _NonQualifie("Chemin de composant absent ou alias non admis")
    pur = PurePosixPath(chemin)
    if (
        pur.is_absolute() is False
        or ".." in pur.parts
        or "." in pur.parts
        or chemin != "/" + "/".join(pur.parts[1:])
    ):
        raise _NonQualifie("Chemin de composant hors archive ou ambigu")
    return "/".join(pur.parts[1:])


def _lire_objets_maillage(archive: Archive3MF, chemin: str, cache: dict[str, dict]):
    """Charge une fois les objets d’un modèle 3MF référencé par composant."""
    membre = _membre_composant(chemin)
    if membre in cache:
        return cache[membre]
    racine = _lire_xml(archive, membre, 64 * 1024 * 1024)
    objets = {}
    for objet in racine.findall(f"{{{_NS}}}resources/{{{_NS}}}object"):
        identifiant = objet.get("id")
        if identifiant is None or identifiant in objets:
            raise _NonQualifie("Objet de volume absent ou ambigu")
        objets[identifiant] = objet
    cache[membre] = objets
    return objets


def _maillage_centre(objet) -> bool:
    ns = f"{{{_NS}}}"
    if objet.find(ns + "components") is not None:
        return False
    mesh = objet.find(ns + "mesh")
    if mesh is None:
        return False
    conteneur = mesh.find(ns + "vertices")
    sommets = [] if conteneur is None else conteneur.findall(ns + "vertex")
    triangles_conteneur = mesh.find(ns + "triangles")
    triangles = [] if triangles_conteneur is None else triangles_conteneur.findall(ns + "triangle")
    if not sommets or len(sommets) > _MAX_VERTICES or not triangles or len(triangles) > _MAX_VERTICES:
        return False
    try:
        xyz = [[float(sommet.get(axe)) for axe in "xyz"] for sommet in sommets]
    except (TypeError, ValueError):
        return False
    if not all(math.isfinite(valeur) for sommet in xyz for valeur in sommet):
        return False
    try:
        indices = [[int(triangle.get(f"v{i}")) for i in (1, 2, 3)] for triangle in triangles]
    except (TypeError, ValueError):
        return False
    if min(min(face) for face in indices) < 0 or max(max(face) for face in indices) >= len(sommets):
        return False
    for axe in range(3):
        centre = (min(sommet[axe] for sommet in xyz) + max(sommet[axe] for sommet in xyz)) / 2
        if abs(centre) > _TOLERANCE_MM:
            return False
    return True


def _empreinte_maillage_local(objet) -> str:
    """Empreinte le maillage local en mm : float32, triangles non ordonnés, winding et doublons préservés."""
    ns = f"{{{_NS}}}"
    maillage = objet.find(ns + "mesh")
    if maillage is None:
        raise _NonQualifie("Maillage local absent")
    sommets_xml = maillage.find(ns + "vertices")
    triangles_xml = maillage.find(ns + "triangles")
    if sommets_xml is None or triangles_xml is None:
        raise _NonQualifie("Géométrie locale incomplète")
    try:
        sommets = [
            struct.pack("!3f", *(float(sommet.get(axe)) for axe in "xyz"))
            for sommet in sommets_xml.findall(ns + "vertex")
        ]
        faces = []
        for triangle in triangles_xml.findall(ns + "triangle"):
            indices = [int(triangle.get(f"v{i}")) for i in (1, 2, 3)]
            if any(indice < 0 or indice >= len(sommets) for indice in indices):
                raise _NonQualifie("Indice de triangle hors maillage")
            face = [sommets[indice] for indice in indices]
            # Les références de chemins et l’orientation sont des invariants 3MF : docs/reference/integrite-3mf.md.
            # Une rotation cyclique conserve le winding ; inverser les sommets changerait l’orientation.
            faces.append(min(face[i:] + face[:i] for i in range(3)))
    except (TypeError, ValueError, OverflowError, struct.error) as exc:
        raise _NonQualifie("Géométrie locale float32 illisible") from exc
    hachage = hashlib.sha256()
    for face in sorted(faces):
        for sommet in face:
            hachage.update(sommet)
    return hachage.hexdigest()


def _chemin_composant(composant) -> str | None:
    """Lit le chemin de ressource associé au composant 3MF, ou None s’il manque."""
    return next(
        (valeur for cle, valeur in composant.attrib.items() if cle.endswith("}path") or cle == "path"),
        None,
    )


def _plan_geometrie(chemin: Path):
    # Garder les budgets qualifiés tout en refusant DTD/ENTITY via le lecteur commun.
    with Archive3MF(
        chemin,
        taille_max_membre=_MAX_ARCHIVE,
        taille_max_totale=_MAX_ARCHIVE,
        taille_max_xml=64 * 1024 * 1024,
    ) as archive:
        if "Metadata/model_settings.config" not in archive.noms():
            raise _NonQualifie("Configuration de modèle absente")
        model_settings = _lire_xml(archive, "Metadata/model_settings.config", _MAX_SETTINGS)
        modeles, objets_3mf = _objets_composants(archive)
        composants_pieces = _lire_modeles_pieces(archive, modeles)
        plans, identities, multiplumes = [], set(), 0
        vus_noms = set()
        for objet_settings in model_settings.findall("object"):
            nom_element = objet_settings.find("metadata[@key='name']")
            nom = nom_element.get("value") if nom_element is not None else objet_settings.get("id")
            if not nom or nom in {".", ".."} or "/" in nom or "\\" in nom or nom in vus_noms:
                raise _NonQualifie("Nom d’objet absent ou ambigu")
            vus_noms.add(nom)
            parties = objet_settings.findall("part")
            if len(parties) <= 1:
                continue
            multiplumes += 1
            objet_3mf = objets_3mf.get(objet_settings.get("id"))
            composants = [] if objet_3mf is None else objet_3mf.findall(f"{{{_NS}}}components/{{{_NS}}}component")
            if len(composants) != len(parties):
                raise _NonQualifie("Nombre de composants différent des parties de réglages")
            for index, (partie, composant) in enumerate(zip(parties, composants, strict=True)):
                if partie.get("id") != composant.get("objectid"):
                    raise _NonQualifie("Identité partie/composant différente")
                type_partie = partie.get("subtype", "normal_part")
                # Anycubic Model.cpp (6103ed8b, L2402-L2427) rabat les valeurs inconnues sur normal_part.
                # Le rôle doit rester reconnu ; voir docs/reference/integrite-3mf.md.
                if type_partie not in {"normal_part", "modifier_part"}:
                    raise _NonQualifie("Type de volume hors qualification")
                fichier = _valeur_meta(partie, "source_file")
                objet_source = _valeur_meta(partie, "source_object_id")
                volume_source = _valeur_meta(partie, "source_volume_id")
                if (
                    not fichier
                    or "/" in fichier
                    or "\\" in fichier
                    or Path(fichier).name != fichier
                ):
                    raise _NonQualifie("source_file ne contient pas un basename privé sûr")
                if not objet_source.isdecimal() or not volume_source.isdecimal():
                    raise _NonQualifie("Identifiants source non numériques")
                identite = (fichier, str(int(objet_source)), str(int(volume_source)))
                if identite in identities:
                    raise _NonQualifie("Identité source de volume dupliquée")
                identities.add(identite)
                component_path = _chemin_composant(composant)
                if component_path != "/3D/Objects/pieces.model":
                    raise _NonQualifie("Chemin de composant hors format qualifié")
                objet_maillage = composants_pieces.get(composant.get("objectid"))
                if objet_maillage is None or not _maillage_centre(objet_maillage):
                    raise _NonQualifie("Sommets de volume non centrés ou géométrie non qualifiée")
                composant_matrice = _matrice_composant(composant.get("transform"))
                if not _translation_pure(composant_matrice):
                    raise _NonQualifie("Composant non limité à une translation")
                source_texte = _valeur_meta(partie, "matrix")
                matrice_source = _matrice_4x4(source_texte, "source")
                if not _translation_pure(matrice_source) or any(
                    abs(matrice_source[i][j] - composant_matrice[i][j]) > 1e-9
                    for i in range(4)
                    for j in range(4)
                ):
                    raise _NonQualifie("Matrice source différente de la translation du composant")
                offsets = [_valeur_meta(partie, f"source_offset_{axe}") for axe in "xyz"]
                try:
                    offset_xyz = [float(v) for v in offsets]
                except ValueError as exc:
                    raise _NonQualifie("Offsets source invalides") from exc
                if not all(math.isfinite(v) for v in offset_xyz) or any(
                    abs(offset_xyz[i] - matrice_source[i][3]) > 1e-9 for i in range(3)
                ):
                    raise _NonQualifie("Offsets source différents de la matrice canonique")
                prediction = _multiplier(composant_matrice, matrice_source)
                prediction_texte = (
                    source_texte if _matrices_egales(matrice_source, prediction) else _texte_matrice(prediction)
                )
                chemin_matrix = f"objet/{nom}/part/{index}/matrix"
                plans.append(
                    {
                        "chemin": chemin_matrix,
                        "identite": {
                            "source_file": fichier,
                            "source_object_id": objet_source,
                            "source_volume_id": volume_source,
                        },
                        "matrice_composant": composant_matrice,
                        "matrice_source": matrice_source,
                        "matrice_source_texte": source_texte,
                        "matrice_predite": prediction,
                        "matrice_predite_texte": prediction_texte,
                        "partie_index": index,
                        "objet_nom": nom,
                        "objet_id": objet_settings.get("id"),
                        "subtype": type_partie,
                        "empreinte_maillage_local": _empreinte_maillage_local(objet_maillage),
                        "component_path": component_path,
                    }
                )
        if multiplumes == 0 or not plans:
            raise _NonQualifie("Aucun objet multi-volume canonique à restaurer")
        archive.verifier_source(chemin)
        return plans


def planifier(entree, *, binaire=None) -> dict:
    """Prépare les matrices 4×4 en mm ; renvoie plan JSON PASS/NON VÉRIFIÉ, sans écrire ni lancer le slicer.

    Le plan ne qualifie que les multi-volumes centrés, à translations pures et provenance unique ; aucun réglage
    d’impression n’est modifié (docs/reference/integrite-3mf.md).
    """
    try:
        source = _fichier_regulier(entree)
        if not _racine_privee(source):
            raise ValueError("Entrée 3MF privée ou temporaire requise")
        empreinte = _sha256(source, _MAX_ARCHIVE)
        binaire_info = _configuration_binaire(binaire)
        base = {
            "schema": 1,
            "source": {"chemin": str(source), "sha256": empreinte},
            "binaire": binaire_info,
            "permis": [],
            "liaisons": [],
            "statut": "NON VÉRIFIÉ",
            "raisons": [],
        }
        if not binaire_info["qualifie"]:
            base["raisons"].append("Binaire absent de la qualification d’origine matrice")
            return base
        plans = _plan_geometrie(source)
        etat, types = integrite_3mf.instantane(source, avec_types=True)
        permis = []
        for item in plans:
            definition = reglages.definition("matrix", portee="origine_part")
            if definition.get("type") != "chaine":
                raise _NonQualifie("Clé structurelle matrix non typée dans origine_part")
            if item["chemin"] not in etat or types[item["chemin"]].get("type") != "chaine":
                raise _NonQualifie("Chemin matrix absent ou non typé dans l’instantané strict")
            if not _matrices_egales(item["matrice_source"], item["matrice_predite"]):
                permis.append(
                    integrite_3mf.autorisation(
                        item["chemin"], item["matrice_source_texte"], item["matrice_predite_texte"]
                    )
                )
        base.update(statut="PASS", liaisons=plans, permis=permis)
        return base
    except (_NonQualifie, ValueError, OSError, BadZipFile, KeyError, ET.ParseError) as exc:
        return {
            "schema": 1,
            "source": {"chemin": str(entree)},
            "binaire": None,
            "permis": [],
            "liaisons": [],
            "statut": "NON VÉRIFIÉ",
            "raisons": [str(exc)],
        }


def _manifeste_zip(chemin: Path) -> tuple[list[str], dict[str, str]]:
    """Retourne noms ordonnés et SHA des membres ; lecture complète bornée et CRC vérifiés."""
    with Archive3MF(
        chemin,
        taille_max_membre=_MAX_ARCHIVE,
        taille_max_totale=_MAX_ARCHIVE,
        taille_max_xml=64 * 1024 * 1024,
    ) as archive:
        noms = archive.noms()
        empreintes = {}
        for nom in noms:
            hachage = hashlib.sha256()
            with archive.ouvrir_flux(nom) as membre:
                for bloc in iter(lambda: membre.read(1024 * 1024), b""):
                    hachage.update(bloc)
            empreintes[nom] = hachage.hexdigest()
        archive.verifier_source(chemin)
        return list(noms), empreintes


def _membre_borne(chemin: Path, nom: str, maximum: int) -> bytes:
    """Lit un membre 3MF en octets sous son plafond ; refuse lien et source remplacée."""
    with _archive_lecture(chemin) as archive:
        contenu = archive.lire(nom, limite_octets=maximum)
        archive.verifier_source(chemin)
        return contenu


def _parties_par_chemin(racine):
    resultat = {}
    noms = set()
    for objet in racine.findall("object"):
        meta_nom = objet.find("metadata[@key='name']")
        nom = meta_nom.get("value") if meta_nom is not None else objet.get("id")
        if not nom or nom in noms:
            raise ValueError("Objets dupliqués ou sans identité pendant restauration")
        noms.add(nom)
        for index, partie in enumerate(objet.findall("part")):
            chemin = f"objet/{nom}/part/{index}/matrix"
            resultat[chemin] = partie
    return resultat


def _xml_hors_matrices(texte: bytes, chemins_cibles: set[str]) -> bytes:
    racine = ET.fromstring(texte)
    for chemin, partie in _parties_par_chemin(racine).items():
        if chemin in chemins_cibles:
            meta = partie.find("metadata[@key='matrix']")
            if meta is None:
                raise ValueError("Matrice planifiée absente")
            meta.set("value", "__MATRICE_PLANIFIEE__")
    return ET.tostring(racine, encoding="utf-8")


def _remplacer_matrices(texte: bytes, liaisons, valeur_cle: str) -> bytes:
    racine = ET.fromstring(texte)
    parties = _parties_par_chemin(racine)
    for liaison in liaisons:
        partie = parties.get(liaison["chemin"])
        if partie is None:
            raise ValueError("Partie planifiée absente du 3MF natif")
        meta = partie.find("metadata[@key='matrix']")
        if meta is None or meta.get("value", meta.text or "") != liaison[valeur_cle]:
            raise ValueError("Matrice native différente de la prédiction exacte")
        meta.set("value", liaison["matrice_source_texte"])
    return ET.tostring(racine, encoding="utf-8", xml_declaration=True)


def _verifier_plan_entree(entree: Path, plan: dict) -> dict:
    recalcul = planifier(entree, binaire=plan.get("binaire", {}).get("chemin"))
    if plan.get("statut") != "PASS" or recalcul != plan:
        raise ValueError("Plan absent, périmé ou différent du calcul exact")
    if _sha256(entree, _MAX_ARCHIVE) != plan["source"]["sha256"]:
        raise ValueError("Entrée modifiée après planification")
    return recalcul


def _verifier_composants(source: Path, natif: Path, liaisons):
    """Vérifie rôle, liaison et géométrie locale float32 en mm, en tolérant les nouveaux identifiants natifs."""
    def lire(chemin):
        with _archive_lecture(chemin) as archive:
            settings = _lire_xml(archive, "Metadata/model_settings.config", _MAX_SETTINGS)
            _, objets = _objets_composants(archive)
            caches_maillages = {}
            resultats = {}
            par_nom = {}
            for objet in settings.findall("object"):
                meta = objet.find("metadata[@key='name']")
                nom = meta.get("value") if meta is not None else objet.get("id")
                if not nom or nom in par_nom:
                    raise ValueError("Identité d’objet ambiguë pendant la validation native")
                par_nom[nom] = objet
            for nom in {liaison["objet_nom"] for liaison in liaisons}:
                objet_settings = par_nom.get(nom)
                if objet_settings is None:
                    raise ValueError("Objet du plan absent du 3MF natif")
                objet_modele = objets.get(objet_settings.get("id"))
                if objet_modele is None:
                    raise ValueError("Objet modèle absent du 3MF natif")
                composants = objet_modele.findall(f"{{{_NS}}}components/{{{_NS}}}component")
                parties = objet_settings.findall("part")
                if len(composants) != len(parties):
                    raise ValueError("Nombre de composants différent du plan")
                for index, (partie, composant) in enumerate(zip(parties, composants, strict=True)):
                    if partie.get("id") != composant.get("objectid"):
                        raise ValueError("Identité partie/composant différente dans l’archive native")
                    component_path = _chemin_composant(composant)
                    _membre_composant(component_path)
                    subtype = partie.get("subtype", "normal_part")
                    try:
                        identite = tuple(
                            _valeur_meta(partie, cle)
                            for cle in ("source_file", "source_object_id", "source_volume_id")
                        )
                    except _NonQualifie as exc:
                        raise ValueError("Provenance de partie absente du natif") from exc
                    objets_maillage = _lire_objets_maillage(archive, component_path, caches_maillages)
                    objet_maillage = objets_maillage.get(composant.get("objectid"))
                    if objet_maillage is None or not _maillage_centre(objet_maillage):
                        raise ValueError("Géométrie locale native absente ou non centrée")
                    cle = (identite[0], str(int(identite[1])), str(int(identite[2])))
                    if cle in resultats:
                        raise ValueError("Provenance de partie dupliquée dans l’archive native")
                    resultats[cle] = {
                        "objet_nom": nom,
                        "partie_index": index,
                        "subtype": subtype,
                        "empreinte_maillage_local": _empreinte_maillage_local(objet_maillage),
                        "component_path": component_path,
                        "matrice_composant": _matrice_composant(composant.get("transform")),
                    }
            archive.verifier_source(chemin)
            return resultats

    avant, apres = lire(source), lire(natif)
    for liaison in liaisons:
        identite = liaison["identite"]
        cle = (
            identite["source_file"],
            str(int(identite["source_object_id"])),
            str(int(identite["source_volume_id"])),
        )
        if cle not in avant or cle not in apres:
            raise ValueError("Liaison de provenance du composant planifié absente")
        source_item, natif_item = avant[cle], apres[cle]
        if source_item["component_path"] != liaison["component_path"]:
            raise ValueError("Chemin de composant source différent du plan")
        for item in (source_item, natif_item):
            if item["subtype"] != liaison["subtype"]:
                raise ValueError("Rôle subtype de partie différent du plan")
            if item["empreinte_maillage_local"] != liaison["empreinte_maillage_local"]:
                raise ValueError("Géométrie locale de partie différente du plan")
        attendu = liaison["matrice_composant"]
        for item in (source_item, natif_item):
            observe = item["matrice_composant"]
            if any(abs(observe[i][j] - attendu[i][j]) > 1e-9 for i in range(4) for j in range(4)):
                raise ValueError("Transformation de composant déplacée depuis le plan")


def restaurer(entree, natif, sortie_neuve, plan) -> dict:
    """Restaure les seules matrices planifiées ; renvoie PASS/FAIL sans écraser ni modifier entrée ou natif.

    Entrées : 3MF source, sortie native et plan JSON ; sortie neuve privée. Préserve les membres ZIP autres que
    model_settings octet par octet, ne modifie aucun réglage d’impression (docs/reference/integrite-3mf.md).
    """
    source = _fichier_regulier(entree)
    moteur = _fichier_regulier(natif)
    if not _racine_privee(source) or not _racine_privee(moteur):
        raise ValueError("Entrée et sortie native privées ou temporaires requises")
    sortie, parent = _destination_neuve(sortie_neuve)
    plan_valide = _verifier_plan_entree(source, plan)
    empreinte_natif_avant = _sha256(moteur, _MAX_ARCHIVE)
    _verifier_composants(source, moteur, plan_valide["liaisons"])
    integrite_natif = integrite_3mf.verifier(source, moteur, plan_valide["permis"], relecture_moteur=True)
    nom_settings = "Metadata/model_settings.config"
    noms_natifs, sha_natif = _manifeste_zip(moteur)
    noms_source, _ = _manifeste_zip(source)
    if nom_settings not in noms_natifs or nom_settings not in noms_source:
        raise ValueError("Configuration de modèle absente")
    chemins_cibles = {liaison["chemin"] for liaison in plan_valide["liaisons"]}
    xml_natif = _membre_borne(moteur, nom_settings, _MAX_SETTINGS)
    _membre_borne(source, nom_settings, _MAX_SETTINGS)
    xml_restaure = _remplacer_matrices(xml_natif, plan_valide["liaisons"], "matrice_predite_texte")
    if _xml_hors_matrices(xml_natif, chemins_cibles) != _xml_hors_matrices(xml_restaure, chemins_cibles):
        raise ValueError("Restauration a modifié des métadonnées hors matrices planifiées")
    temporaire = None
    publie = False
    try:
        fd, nom_temp = tempfile.mkstemp(prefix=".origine-3mf-", suffix=".tmp", dir=parent)
        os.close(fd)
        temporaire = Path(nom_temp)
        with Archive3MF(
            moteur,
            taille_max_membre=_MAX_ARCHIVE,
            taille_max_totale=_MAX_ARCHIVE,
            taille_max_xml=64 * 1024 * 1024,
        ) as entree_archive, ZipFile(temporaire, "w") as sortie_zip:
            # Recopier les métadonnées et le commentaire d’archive depuis l’index épinglé.
            sortie_zip.comment = entree_archive.commentaire
            for nom in entree_archive.noms():
                info = entree_archive.information(nom)
                cible = sortie_zip.open(copy.copy(info), "w")
                with cible:
                    if nom == nom_settings:
                        cible.write(xml_restaure)
                    else:
                        with entree_archive.ouvrir_flux(nom) as membre:
                            shutil.copyfileobj(membre, cible, length=1024 * 1024)
            entree_archive.verifier_source(moteur)
        noms_sortie, sha_sortie = _manifeste_zip(temporaire)
        if noms_sortie != noms_natifs:
            raise ValueError("Restauration a modifié la liste ou l’ordre des membres ZIP")
        for nom, empreinte in sha_natif.items():
            if nom != nom_settings and sha_sortie[nom] != empreinte:
                raise ValueError(f"Restauration a modifié un membre natif : {nom}")
        if _xml_hors_matrices(xml_natif, chemins_cibles) != _xml_hors_matrices(
            _membre_borne(temporaire, nom_settings, _MAX_SETTINGS), chemins_cibles
        ):
            raise ValueError("XML réouvert différent hors matrices planifiées")
        inverse = [
            integrite_3mf.autorisation(
                liaison["chemin"], liaison["matrice_predite_texte"], liaison["matrice_source_texte"]
            )
            for liaison in plan_valide["liaisons"]
            if not _matrices_egales(liaison["matrice_source"], liaison["matrice_predite"])
        ]
        integrite_restauration = integrite_3mf.verifier(moteur, temporaire, inverse)
        integrite_finale = integrite_3mf.verifier(source, temporaire, relecture_moteur=True)
        if integrite_finale["differences"]:
            raise ValueError("Écart de réglages résiduel après restauration")
        if _sha256(moteur, _MAX_ARCHIVE) != empreinte_natif_avant:
            raise ValueError("Archive native modifiée pendant la restauration")
        if not _racine_privee(sortie):
            raise ValueError("Destination non privée")
        sortie_reverifiee, parent_reverifie = _destination_neuve(sortie)
        if sortie_reverifiee != sortie or parent_reverifie != parent:
            raise ValueError("Destination privée modifiée pendant la restauration")
        if sortie.exists() or sortie.is_symlink() or parent.is_symlink():
            raise ValueError("Destination modifiée avant publication exclusive")
        if _sha256(source, _MAX_ARCHIVE) != plan_valide["source"]["sha256"]:
            raise ValueError("Entrée modifiée juste avant publication")
        inode_temporaire = temporaire.lstat()
        os.link(temporaire, sortie)
        publie = True
        inode_sortie = sortie.lstat()
        if (inode_sortie.st_dev, inode_sortie.st_ino) != (inode_temporaire.st_dev, inode_temporaire.st_ino):
            raise ValueError("Cible remplacée pendant la publication")
        if _sha256(source, _MAX_ARCHIVE) != plan_valide["source"]["sha256"]:
            raise ValueError("Entrée modifiée pendant la publication")
        verification = integrite_3mf.verifier(source, sortie, relecture_moteur=True)
        if verification["differences"]:
            raise ValueError("Écart final après publication exclusive")
        inode_final = sortie.lstat()
        if (inode_final.st_dev, inode_final.st_ino) != (inode_temporaire.st_dev, inode_temporaire.st_ino):
            raise ValueError("Cible remplacée après vérification")
        if _sha256(source, _MAX_ARCHIVE) != plan_valide["source"]["sha256"]:
            raise ValueError("Entrée modifiée après publication")
        return {
            "statut": "PASS",
            "source_sha256": plan_valide["source"]["sha256"],
            "natif_sha256": _sha256(moteur, _MAX_ARCHIVE),
            "sortie_sha256": _sha256(sortie, _MAX_ARCHIVE),
            "sortie": str(sortie),
            "integrite_natif": integrite_natif,
            "integrite_restauration": integrite_restauration,
            "integrite_finale": verification,
            "membres_non_config_identiques_au_natif": True,
            "membres_sha256_natif": {nom: digest for nom, digest in sha_natif.items() if nom != nom_settings},
            "membres_sha256_restaure": {nom: digest for nom, digest in sha_sortie.items() if nom != nom_settings},
            "xml_hors_matrices_identique": True,
            "reouverture": True,
            "aucun_reglage_impression_modifie": True,
            "validation_physique": False,
        }
    except Exception:
        if publie and sortie.exists():
            try:
                inode_actuel = sortie.lstat()
                if (inode_actuel.st_dev, inode_actuel.st_ino) == (inode_temporaire.st_dev, inode_temporaire.st_ino):
                    sortie.unlink()
            except FileNotFoundError:
                pass
        raise
    finally:
        if temporaire is not None:
            temporaire.unlink(missing_ok=True)
