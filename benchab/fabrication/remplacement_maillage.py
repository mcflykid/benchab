"""Remplacement borné d'un maillage direct dans une copie 3MF privée neuve."""

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import numpy as np
import trimesh

from benchab import integrite_3mf
from benchab.archives_3mf import Archive3MF, decoder_xml
from benchab.controles.rapports import _protege
from benchab.fabrication.anycubic import empreinte_geometrie
from benchab.imports3d.lecture_3mf import UNITES, lire
from benchab.imports3d.maillage_indexe import lire as lire_npz
from benchab.qualite.ecritures import PublicationNeuve, destination_neuve
from benchab.qualite.empreintes import sha256_fichier

_NS_MODELE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
_NS_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_REL_MODELE = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
_MAX_XML = 64 * 1024 * 1024
_MAX_XML_TOTAL = 128 * 1024 * 1024
_MAX_FACES = 1_000_000
_MAX_COORD_MM = 1e7
_ATTRIBUTS_MODELE = {
    "model": {"unit"},
    "resources": set(),
    "object": {"id", "type"},
    "components": set(),
    "component": {"objectid", "transform"},
    "mesh": set(),
    "vertices": set(),
    "vertex": {"x", "y", "z"},
    "triangles": set(),
    "triangle": {"v1", "v2", "v3"},
    "build": set(),
    "item": {"objectid", "transform"},
    "metadata": {"name"},
}
_ENFANTS_MODELE = {
    "model": {"resources", "build"},
    "resources": {"object"},
    "object": {"mesh", "metadata", "components"},
    "components": {"component"},
    "component": set(),
    "mesh": {"vertices", "triangles"},
    "vertices": {"vertex"},
    "triangles": {"triangle"},
    "build": {"item"},
    "item": set(),
    "vertex": set(),
    "triangle": set(),
    "metadata": set(),
}


def _topologie(mesh: trimesh.Trimesh) -> dict:
    """Décrit faces, volume orienté et caractéristique d'Euler, sans réparer le maillage."""
    return {
        "sommets": int(len(mesh.vertices)),
        "faces": int(len(mesh.faces)),
        "volume_mm3": float(mesh.volume),
        "euler": int(mesh.euler_number),
        "volume_oriente": bool(mesh.is_volume and mesh.is_winding_consistent and mesh.volume > 0),
    }


def _cle_geometrie(identite):
    """Identifie une feuille au sein d’une instance Core à un seul niveau de composants."""
    return (
        identite.get("modele_principal"), identite.get("objet_id_racine"),
        identite.get("build_item"), identite.get("modele_maillage"), identite.get("objet_id_maillage"),
    )


def _geometries(scene) -> dict:
    """Indexe les maillages importés par modèle, objet et instance identifiés."""
    resultat = {}
    identites = scene.metadata.get("identites_import")
    if not isinstance(identites, list) or len(identites) != len(scene.geometry):
        raise ValueError("Identités de maillage absentes ou incomplètes dans la scène relue")
    for mesh, identite in zip(scene.geometry.values(), identites, strict=True):
        if not isinstance(identite, dict):
            raise ValueError("Identité de maillage absente dans la relecture 3MF")
        cle = _cle_geometrie(identite)
        if cle in resultat:
            raise ValueError("Instance de maillage dupliquée ou ambiguë")
        resultat[cle] = mesh
    return resultat


def _sans_maillage(racine: ET.Element, identifiant: str) -> bytes:
    """Sérialise une copie sans le maillage ciblé pour comparer toute autre structure XML."""
    copie_racine = copy.deepcopy(racine)
    objets = [o for o in copie_racine.findall(f"{{{_NS_MODELE}}}resources/{{{_NS_MODELE}}}object")
              if o.get("id") == identifiant]
    if len(objets) != 1:
        raise ValueError("Objet cible absent ou ambigu dans le modèle")
    meshes = objets[0].findall(f"{{{_NS_MODELE}}}mesh")
    if len(meshes) != 1:
        raise ValueError("Un seul maillage direct est requis pour la cible")
    mesh = meshes[0]
    # Le rang et le texte suivant le maillage appartiennent à l’objet, pas à ses triangles.
    trace = ET.Element(mesh.tag)
    trace.tail = mesh.tail
    objets[0][list(objets[0]).index(mesh)] = trace
    return ET.tostring(copie_racine, encoding="utf-8")


def _sha_membres(archive: Archive3MF, noms: tuple[str, ...], sauf: str) -> dict[str, str]:
    """Calcule les empreintes des membres non ciblés en atteignant EOF pour valider leur CRC."""
    resultat = {}
    for nom in noms:
        if nom == sauf:
            continue
        with archive.ouvrir_flux(nom) as flux:
            resultat[nom] = hashlib.file_digest(flux, "sha256").hexdigest()
    return resultat


def _valider_modele_xml(contenu: bytes) -> ET.Element:
    """Parse un modèle UTF-8 borné après refus des commentaires, PI et namespaces d'extension."""
    try:
        texte = contenu.decode("utf-8-sig")
    except UnicodeError as erreur:
        raise ValueError("Le sous-cas ne qualifie que le XML modèle UTF-8") from erreur
    sans_declaration = re.sub(r"\A\s*<\?xml\b[^?]*\?>", "", texte, count=1, flags=re.IGNORECASE)
    if "<!--" in texte or "<?" in sans_declaration:
        raise ValueError("Commentaires XML et instructions de traitement ne sont pas qualifiés")
    if re.search(r"\bxmlns\s*:", texte):
        raise ValueError("Préfixe de namespace XML non qualifié")
    racine = decoder_xml(contenu, limite_octets=_MAX_XML)
    for element in racine.iter():
        if not isinstance(element.tag, str) or not element.tag.startswith(f"{{{_NS_MODELE}}}"):
            raise ValueError("Namespace ou élément d'extension XML non qualifié")
        nom = element.tag.rsplit("}", 1)[-1]
        attributs_autorises = _ATTRIBUTS_MODELE.get(nom)
        enfants_autorises = _ENFANTS_MODELE.get(nom)
        if attributs_autorises is None or set(element.attrib) - attributs_autorises:
            raise ValueError("Attribut ou élément Core non qualifié par ce sous-cas")
        if enfants_autorises is None or any(
            not isinstance(enfant.tag, str)
            or enfant.tag.rsplit("}", 1)[-1] not in enfants_autorises
            or not enfant.tag.startswith(f"{{{_NS_MODELE}}}")
            for enfant in element
        ):
            raise ValueError("Élément Core non pris en charge par ce sous-cas")
        if nom != "metadata" and element.text and element.text.strip():
            raise ValueError("Texte Core non qualifié par ce sous-cas")
    return racine


def _indice_feuille(racine, objet_id, volume_id, instances):
    """Valide une feuille directe et non partagée dans le seul assemblage Core, renvoie son rang."""
    ns = f"{{{_NS_MODELE}}}"
    if len(racine.findall(f"{ns}resources")) != 1 or len(racine.findall(f"{ns}build")) != 1:
        raise ValueError("Ressources et build uniques requis pour les composants")
    objets = racine.findall(f"{ns}resources/{ns}object")
    identifiants = [o.get("id", "") for o in objets]
    if len(set(identifiants)) != len(objets) or any(
        not re.fullmatch(r"[1-9][0-9]{0,9}", identifiant) for identifiant in identifiants
    ):
        raise ValueError("Identifiants Core non canoniques ou ambigus pour les composants")
    table = dict(zip(identifiants, objets, strict=True))
    groupes = [(o.get("id"), groupe) for o in objets for groupe in o.findall(f"{ns}components")]
    if len(groupes) != 1 or groupes[0][0] != objet_id:
        raise ValueError("Un seul assemblage Core de premier niveau est qualifié")
    assemblage = table[objet_id]
    if assemblage.get("type") != "model" or assemblage.find(f"{ns}mesh") is not None:
        raise ValueError("L’assemblage cible doit contenir des composants sans maillage propre")
    ids = [comp.get("objectid") for comp in groupes[0][1]]
    if volume_id not in ids or len(set(ids)) != len(ids) or any(
        i == objet_id or i not in table or table[i].get("type") != "model"
        or len(table[i].findall(f"{ns}mesh")) != 1 for i in ids
    ):
        raise ValueError("Volume absent, ambigu, partagé ou non direct dans l’assemblage")
    if set(ids).intersection(item.get("objectid") for item in instances):
        raise ValueError("Une feuille de l’assemblage est aussi référencée dans le build")
    return ids.index(volume_id) + 1


def _cible_modele(source: Path, objet_id: str, volume_id: str | None):
    """Valide le StartPart, l’instance racine et le maillage direct ou la feuille unique."""
    cible_id = volume_id if volume_id is not None else objet_id
    with Archive3MF(source, taille_max_xml=_MAX_XML) as archive:
        noms = archive.noms()
        modeles = [nom for nom in noms if nom.casefold().endswith(".model")]
        if any(nom.casefold().endswith(".gcode") for nom in noms):
            raise ValueError("G-code embarqué : remplacement refusé pour ne pas périmer un résultat tranché")
        if any(PurePosixPath(nom).name.casefold() == "slice_info.config" for nom in noms):
            raise ValueError("slice_info.config indique une archive tranchée : remplacement refusé")
        noms_xml = [
            nom for nom in noms
            if nom.casefold().endswith((".model", ".rels", ".xml", ".config"))
            or nom == "Metadata/model_settings.config"
        ]
        if any(archive.taille_membre(nom) > _MAX_XML for nom in noms_xml):
            raise ValueError("Budget XML de 64 Mio par membre dépassé")
        if sum(archive.taille_membre(nom) for nom in noms_xml) > _MAX_XML_TOTAL:
            raise ValueError("Budget XML total de 128 Mio dépassé")
        if len(modeles) != 1 or "_rels/.rels" not in noms:
            raise ValueError("StartPart OPC explicite et modèle unique requis")
        if volume_id is not None and any(
            PurePosixPath(nom).name.casefold() == "model_settings.config" for nom in noms
        ):
            raise ValueError("Métadonnées de volumes slicer non qualifiées pour les composants Core")
        relations = archive.xml("_rels/.rels")
        if relations.tag != _NS_REL + "Relationships":
            raise ValueError("StartPart OPC explicite absent ou racine de relations invalide")
        liens_modele = [
            lien for lien in relations
            if lien.tag == _NS_REL + "Relationship" and lien.get("Type") == _REL_MODELE
        ]
        if (
            len(liens_modele) != 1
            or liens_modele[0].get("TargetMode", "Internal") != "Internal"
            or not liens_modele[0].get("Target")
        ):
            raise ValueError("StartPart OPC explicite absent, externe ou ambigu")

    scene = lire(source, max_faces=_MAX_FACES, identites=True)
    geometries = _geometries(scene)
    identites = scene.metadata.get("identites_import", [])
    matches = [
        identite for identite in identites
        if identite.get("objet_id_racine") == objet_id and identite.get("objet_id_maillage") == cible_id
        and identite.get("modele_maillage") == identite.get("modele_principal")
    ]
    if len(matches) != 1:
        raise ValueError("La cible doit être une seule instance directe imprimable du modèle principal")
    principal = matches[0]["modele_principal"]
    cle = _cle_geometrie(matches[0])
    if cle not in geometries:
        raise ValueError("Instance cible absente de la scène relue")

    with Archive3MF(source, taille_max_xml=_MAX_XML) as archive:
        noms = archive.noms()
        if modeles != [principal]:
            raise ValueError("Seul le modèle principal unique est qualifié pour ce remplacement")
        for nom in noms:
            if nom.casefold().endswith(".rels") and nom != "_rels/.rels":
                raise ValueError("Relations OPC de modèle : le sous-cas direct ne les qualifie pas")
        racine = _valider_modele_xml(archive.lire(principal, limite_octets=_MAX_XML))
        objets = [o for o in racine.findall(f"{{{_NS_MODELE}}}resources/{{{_NS_MODELE}}}object")
                  if o.get("id") == cible_id]
        if len(objets) != 1:
            raise ValueError("Identifiant objet absent ou dupliqué")
        objet = objets[0]
        if objet.get("type") != "model":
            raise ValueError("L'objet cible doit être un volume de type model")
        if objet.find(f"{{{_NS_MODELE}}}components") is not None:
            raise ValueError("Objet assemblé : remplacement direct refusé")
        meshes = objet.findall(f"{{{_NS_MODELE}}}mesh")
        if len(meshes) != 1:
            raise ValueError("Un seul volume mesh direct est requis")
        items = racine.findall(f"{{{_NS_MODELE}}}build/{{{_NS_MODELE}}}item")
        instances = [item for item in items if item.get("objectid") == objet_id]
        if len(instances) != 1 or matches[0]["build_item"] != items.index(instances[0]) + 1:
            raise ValueError("Objet ciblé référencé plusieurs fois ou par une autre instance")
        indice_composant = None
        if volume_id is None:
            if next(racine.iter(f"{{{_NS_MODELE}}}components"), None) is not None:
                raise ValueError("Composants présents : demander une feuille explicite")
        else:
            indice_composant = _indice_feuille(racine, objet_id, volume_id, items)
            trace = matches[0]["transformations"]
            if (
                len(trace) != 2 or trace[-1].get("type") != "composant"
                or trace[-1].get("objet_id") != objet_id or trace[-1].get("objet_id_cible") != volume_id
            ):
                raise ValueError("Chemin de composant non vérifié dans la scène")
        if any(key.rsplit("}", 1)[-1] == "path" for noeud in racine.iter() for key in noeud.attrib):
            raise ValueError("Attribut path de production : seule la référence directe est qualifiée")
        mesh = meshes[0]
        if set(mesh.attrib) or [enfant.tag for enfant in mesh] != [
            f"{{{_NS_MODELE}}}vertices", f"{{{_NS_MODELE}}}triangles"
        ]:
            raise ValueError("Métadonnées ou éléments de maillage non qualifiés")
        vertices, triangles = list(mesh)
        if any(set(noeud.attrib) != {"x", "y", "z"} or len(noeud) for noeud in vertices):
            raise ValueError("Attributs de sommet non géométriques non qualifiés")
        if any(set(noeud.attrib) != {"v1", "v2", "v3"} or len(noeud) for noeud in triangles):
            raise ValueError("Attributs de triangle (matériaux/volume) non qualifiés")
        return principal, racine, objet, indice_composant


def remplacer(
    source,
    candidat,
    sortie,
    *,
    objet_id: str,
    source_sha256: str,
    repere: str,
    decalage_mm: tuple[float, float, float],
    volume_id: str | None = None,
    prive=None,
) -> dict:
    """Remplace un mesh direct ou une feuille Core dans une copie 3MF neuve.

    La cible est identifiée par SHA source, objet racine et volume enfant facultatif.
    Le volume est une feuille unique à un niveau ; aucune instance n’est transformée.
    Le candidat NPZ BENCHAB
    est en mm dans le repère local explicite ; le décalage (x, y, z) est en mm.
    Le remplacement préserve les références et transformations du modèle. Sortie
    PASS signifie membres et géométrie recontrôlés, pas compatibilité slicer ni
    validation physique. Toute ambiguïté, référence partagée, G-code ou différence
    non permise est refusée avant publication (issue #112).
    """
    source, candidat, sortie = Path(source), Path(candidat), Path(sortie)
    if sortie.suffix.casefold() != ".3mf":
        raise ValueError("La sortie doit avoir l’extension .3mf")
    sortie = destination_neuve(sortie, prive=prive, temporaire=False)
    # Une sortie inédite peut encore se trouver dans une préparation figée.
    _protege(sortie)
    if not isinstance(objet_id, str) or not objet_id.strip():
        raise ValueError("Identifiant objet explicite requis")
    if volume_id is not None and (not isinstance(volume_id, str) or not re.fullmatch(r"[1-9][0-9]{0,9}", volume_id)):
        raise ValueError("Identifiant de volume Core canonique requis")
    if repere != "local_objet_mm":
        raise ValueError("Le repère explicite qualifié est local_objet_mm")
    if (
        not isinstance(source_sha256, str)
        or len(source_sha256) != 64
        or any(c not in "0123456789abcdef" for c in source_sha256)
    ):
        raise ValueError("SHA-256 source invalide")
    if sha256_fichier(source) != source_sha256:
        raise ValueError("SHA-256 source différent de la déclaration")
    decalage = np.asarray(decalage_mm, dtype=float)
    if decalage.shape != (3,) or not np.isfinite(decalage).all() or np.abs(decalage).max(initial=0) > _MAX_COORD_MM:
        raise ValueError("Décalage XYZ explicite invalide ou hors budget (mm)")
    sha_candidat = sha256_fichier(candidat)
    mesh_candidat = lire_npz(candidat)
    if len(mesh_candidat.faces) > _MAX_FACES:
        raise ValueError("Budget de triangles du candidat dépassé")
    topo_candidat = _topologie(mesh_candidat)
    if not topo_candidat["volume_oriente"]:
        raise ValueError("Le candidat doit être un volume fermé orienté vers l’extérieur")
    cible_id = volume_id if volume_id is not None else objet_id
    principal, racine, objet, indice_composant = _cible_modele(source, objet_id, volume_id)
    scene_avant = lire(source, max_faces=_MAX_FACES, identites=True)
    geometries_avant = _geometries(scene_avant)
    identite_cible = next(
        identite for identite in scene_avant.metadata["identites_import"]
        if identite.get("modele_principal") == principal and identite.get("objet_id_racine") == objet_id
        and identite.get("objet_id_maillage") == cible_id
    )
    cle_cible = _cle_geometrie(identite_cible)
    mesh_avant = geometries_avant[cle_cible]
    topo_avant = _topologie(mesh_avant)
    if not topo_avant["volume_oriente"]:
        raise ValueError("Le maillage source n'est pas un volume fermé orienté qualifié")
    unit = scene_avant.metadata.get("unites_fichier")
    facteur = UNITES.get(unit)
    if facteur is None:
        raise ValueError("Unité du modèle principal non qualifiée")
    # Même composition instance @ composant que le lecteur, après décalage local en mm (#112).
    matrice = np.eye(4)
    for transformation in identite_cible["transformations"]:
        matrice = matrice @ np.asarray(transformation["matrice_mm"], dtype=float)
    candidat_attendu = mesh_candidat.copy()
    candidat_attendu.apply_translation(decalage)
    candidat_attendu.apply_transform(matrice)
    sha_geometrie_attendue = empreinte_geometrie(candidat_attendu)["sha256_triangles"]
    if sha_geometrie_attendue is None:
        raise ValueError("Budget de géométrie du candidat dépassé")

    sommets = np.asarray(mesh_candidat.vertices, dtype=float) + decalage
    faces = np.asarray(mesh_candidat.faces, dtype=np.int64)
    if not np.isfinite(sommets).all() or np.abs(sommets).max(initial=0) > _MAX_COORD_MM:
        raise ValueError("Coordonnées du candidat hors budget après décalage (mm)")
    nouveau_mesh = ET.Element(f"{{{_NS_MODELE}}}mesh")
    nouveau_vertices = ET.SubElement(nouveau_mesh, f"{{{_NS_MODELE}}}vertices")
    nouveau_triangles = ET.SubElement(nouveau_mesh, f"{{{_NS_MODELE}}}triangles")
    for sommet in sommets:
        ET.SubElement(
            nouveau_vertices, f"{{{_NS_MODELE}}}vertex",
            **dict(zip(("x", "y", "z"), (format(float(x / facteur), ".9g") for x in sommet), strict=True)),
        )
    for face in faces:
        ET.SubElement(
            nouveau_triangles, f"{{{_NS_MODELE}}}triangle",
            **dict(zip(("v1", "v2", "v3"), map(str, face), strict=True)),
        )
    anciens_mesh = objet.findall(f"{{{_NS_MODELE}}}mesh")
    if len(anciens_mesh) != 1:
        raise ValueError("Un seul volume mesh direct est requis pour la cible")
    position_mesh = list(objet).index(anciens_mesh[0])
    ET.register_namespace("", _NS_MODELE)
    source_sans_cible = _sans_maillage(racine, cible_id)
    nouveau_mesh.tail = anciens_mesh[0].tail
    objet[position_mesh] = nouveau_mesh
    contenu_modele = ET.tostring(racine, encoding="utf-8", xml_declaration=True)
    source_sha_verifie = sha256_fichier(source)
    if source_sha_verifie != source_sha256:
        raise ValueError("3MF source modifié pendant sa qualification")
    with Archive3MF(source, taille_max_xml=_MAX_XML) as archive_source:
        noms = archive_source.noms()
        empreintes = _sha_membres(archive_source, noms, principal)
        info_modele = archive_source.information(principal)
        commentaire = archive_source.commentaire
    with PublicationNeuve([source, candidat], [sortie]) as publication:
        temp = publication.temporaire(sortie)
        with ZipFile(temp, "w") as archive_sortie:
            archive_sortie.comment = commentaire
            with Archive3MF(source, taille_max_xml=_MAX_XML) as archive_source:
                for nom in noms:
                    info = info_modele if nom == principal else archive_source.information(nom)
                    with archive_sortie.open(copy.copy(info), "w") as flux_sortie:
                        if nom == principal:
                            flux_sortie.write(contenu_modele)
                        else:
                            with archive_source.ouvrir_flux(nom) as flux_source:
                                while bloc := flux_source.read(1024 * 1024):
                                    flux_sortie.write(bloc)
        with Archive3MF(temp, taille_max_xml=_MAX_XML) as archive_apres:
            if archive_apres.noms() != noms:
                raise ValueError("La liste ou l’ordre des membres a changé")
            archive_apres.verifier()
            for nom, attendu in empreintes.items():
                with archive_apres.ouvrir_flux(nom) as flux:
                    if hashlib.file_digest(flux, "sha256").hexdigest() != attendu:
                        raise ValueError("Membre non ciblé altéré : " + nom)
            racine_apres = archive_apres.xml(principal)
            if _sans_maillage(racine_apres, cible_id) != source_sans_cible:
                raise ValueError("Un élément XML hors du maillage cible a changé")
        scene_apres = lire(temp, max_faces=_MAX_FACES, identites=True)
        geometries_apres = _geometries(scene_apres)
        if set(geometries_avant) != set(geometries_apres):
            raise ValueError("Identités/instances de géométrie modifiées")
        for cle, avant in geometries_avant.items():
            empreinte_avant = empreinte_geometrie(avant)["sha256_triangles"]
            empreinte_apres = empreinte_geometrie(geometries_apres[cle])["sha256_triangles"]
            if empreinte_avant is None or empreinte_apres is None:
                raise ValueError("Empreinte géométrique non calculée pour une instance")
            attendu = (
                sha_geometrie_attendue
                if cle == cle_cible
                else empreinte_avant
            )
            if empreinte_apres != attendu:
                raise ValueError("Géométrie cible ou autre instance inattendue : " + str(cle))
        topo_apres = _topologie(
            geometries_apres[cle_cible]
        )
        if not topo_apres["volume_oriente"]:
            raise ValueError("Le maillage sérialisé n'est pas un volume fermé orienté qualifié")
        integrite = integrite_3mf.verifier(source, temp, permis=())
        if sha256_fichier(source) != source_sha_verifie or sha256_fichier(candidat) != sha_candidat:
            raise ValueError("Source ou candidat modifié pendant le remplacement")
        _protege(sortie)
        publication.publier()
    return {
        "statut": "PASS",
        "source_sha256": source_sha256,
        "candidat_sha256": sha_candidat,
        "fichier": str(sortie),
        "sortie_sha256": sha256_fichier(sortie),
        "modele": principal,
        "objet_id": objet_id,
        **({
            "volume_id": volume_id, "indice_composant": indice_composant,
            "indice_build": identite_cible["build_item"],
        } if volume_id is not None else {}),
        "repere": repere,
        "decalage_mm": decalage.tolist(),
        "topologie_avant": topo_avant,
        "topologie_apres": topo_apres,
        "empreinte_geometrie_apres": sha_geometrie_attendue,
        "quantum_geometrie_mm": 1e-5,
        "integrite_reglages": integrite["statut"],
        "membres_non_cibles_sha_identiques": len(empreintes),
        "difference_permise": f"{principal} : géométrie locale de l'objet {cible_id} uniquement",
        "validation_slicer": "NON VÉRIFIÉ",
        "validation_physique": "NON VÉRIFIÉ",
    }


def main(argv=None, *, base):
    """Commande CLI de remplacement explicite ; ne lance pas le slicer ni une action machine."""
    parser = argparse.ArgumentParser(description="Remplace un maillage direct ou un volume Core identifié")
    parser.add_argument("source", type=Path)
    parser.add_argument("candidat", type=Path, help="Maillage indexé NPZ BENCHAB en mm")
    parser.add_argument("--sortie", type=Path, required=True)
    parser.add_argument("--objet-id", required=True, help="Identifiant de l’objet racine du build")
    parser.add_argument("--volume-id", help="Feuille unique d’un assemblage Core à un niveau")
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--repere", required=True, choices=("local_objet_mm",))
    parser.add_argument("--decalage-mm", nargs=3, type=float, required=True, metavar=("X", "Y", "Z"))
    args = parser.parse_args(argv)
    sortie = args.sortie if args.sortie.is_absolute() else base.prive / args.sortie
    rapport = remplacer(
        args.source,
        args.candidat,
        sortie,
        objet_id=args.objet_id,
        volume_id=args.volume_id,
        source_sha256=args.source_sha256,
        repere=args.repere,
        decalage_mm=tuple(args.decalage_mm),
        prive=base.prive,
    )
    print(json.dumps(rapport, ensure_ascii=False, indent=2))
    return 0
