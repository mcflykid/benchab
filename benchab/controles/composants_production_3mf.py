"""Identifie les feuilles Production d’un assemblage simple dans le même ZIP."""

import re
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from benchab.controles.materiaux_core_3mf import NS_CORE

NS_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
REL_MODELE = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
_PRODUCTION = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
_UUID = "{" + _PRODUCTION + "}UUID"
_CHEMIN = "{" + _PRODUCTION + "}path"
_OBJET = NS_CORE + "object"
_MATERIAUX = NS_CORE + "basematerials"


def relations_opc(archive, membre):
    """Rend les relations OPC (type, mode et URI) d’un membre XML borné.

    Les identifiants doivent être uniques. Un élément ambigu lève ValueError.
    Les cibles ne sont jamais ouvertes ici.
    """
    racine = archive.xml(membre)
    if racine.tag != NS_REL + "Relationships":
        raise ValueError("Racine de relations OPC invalide.")
    ids, liens = set(), []
    for relation in racine:
        if relation.tag != NS_REL + "Relationship":
            raise ValueError("QName de relation OPC inattendu.")
        identifiant, cible = relation.get("Id"), relation.get("Target")
        mode = relation.get("TargetMode", "Internal")
        if not identifiant or identifiant in ids or not cible or mode not in {"Internal", "External"}:
            raise ValueError("Relation OPC mal formée ou ambiguë.")
        ids.add(identifiant)
        liens.append((relation.get("Type", ""), mode, cible))
    return liens


def _partie_modele(texte, *, dossier=""):
    """Résout un URI de partie dans le ZIP, sans traversée, alias ni accès disque."""
    if not texte or texte.startswith("//") or re.search(r"[?#\x00-\x20\x7f]", texte):
        return None
    try:
        uri = urlsplit(texte)
        chemin = unquote(uri.path, errors="strict")
    except ValueError:
        return None
    if uri.scheme or uri.netloc or uri.query or uri.fragment or re.search(r"[\\?#%\x00-\x20\x7f]", chemin):
        return None
    segments = chemin.removeprefix("/").split("/")
    if any(s in {"", ".", ".."} for s in segments) or not segments[-1].endswith(".model"):
        return None
    return "/".join(segments) if chemin.startswith("/") else str(PurePosixPath(dossier) / chemin)


def _ressources(racine):
    """Valide un index local de ressources Core à identifiants entiers uniques."""
    groupes = racine.findall(NS_CORE + "resources")
    if len(groupes) != 1 or not 1 <= len(groupes[0]) <= 4096:
        return None
    ids = set()
    for ressource in groupes[0]:
        identifiant = ressource.get("id", "")
        if (
            ressource.tag not in {_OBJET, _MATERIAUX} or re.fullmatch(r"[1-9][0-9]{0,9}", identifiant) is None
            or int(identifiant) >= 2**31 or identifiant in ids
        ):
            return None
        ids.add(identifiant)
    return groupes[0]


def _modele_qualifie(racine, namespaces):
    """Vérifie les éléments Core et la résolution des préfixes d’extensions requises."""
    return (
        racine.tag == NS_CORE + "model"
        and set(racine.attrib) <= {"unit", "requiredextensions", "{http://www.w3.org/XML/1998/namespace}lang"}
        and all(e.tag in {NS_CORE + t for t in ("metadata", "resources", "build")} for e in racine)
        and all(namespaces.get(prefixe) == _PRODUCTION for prefixe in racine.get("requiredextensions", "").split())
    )


def feuille_production(archive, modele, racine, namespaces, construction, *, translation_qualifiee):
    """Rend la feuille Production unique, sa provenance OPC et ses ressources.

    La cible d’un p:path absolu doit être un membre .model interne relié
    exactement une fois. Le sous-cas exige une unité commune et une translation
    pure, exprimée dans cette unité. ValueError si le lien ou la scène est
    ambigu. Aucun membre n’est extrait ou modifié.
    """
    ressources = _ressources(racine)
    objets = [] if ressources is None else ressources.findall(_OBJET)
    item = construction[0]
    if (
        len(objets) != 1 or len(ressources) != 1 or not _modele_qualifie(racine, namespaces)
        or len(construction) != 1 or set(construction.attrib) - {_UUID}
        or set(item.attrib) - {"objectid", "transform", "printable", _UUID} or len(item)
    ):
        raise ValueError("Racine Production hors du sous-cas à une feuille externe.")
    parent = objets[0]
    if (
        parent.get("id") != item.get("objectid") or parent.get("type", "model") != "model"
        or set(parent.attrib) - {"id", "type", _UUID} or len(parent) != 1
        or parent[0].tag != NS_CORE + "components" or parent[0].attrib or len(parent[0]) != 1
    ):
        raise ValueError("Parent Production ambigu ou à plusieurs composants.")
    composant = parent[0][0]
    uri = composant.get(_CHEMIN, "")
    translation = composant.get("transform")
    if (
        composant.tag != NS_CORE + "component" or len(composant)
        or set(composant.attrib) - {"objectid", "transform", _CHEMIN, _UUID}
        or not uri.startswith("/") or not translation_qualifiee(translation)
    ):
        raise ValueError("Composant Production ou translation non qualifiés.")
    cible = _partie_modele(uri)
    noms = archive.noms()
    if cible is None or cible == modele or cible not in noms:
        raise ValueError("Cible p:path absente ou hors du sous-cas interne.")
    partie = PurePosixPath(modele)
    rels = str(partie.parent / "_rels" / (partie.name + ".rels"))
    if rels not in noms:
        raise ValueError("Lien OPC du composant Production absent.")
    liens = relations_opc(archive, rels)
    if sum(
        type_lien == REL_MODELE and mode == "Internal" and _partie_modele(uri, dossier=partie.parent) == cible
        for type_lien, mode, uri in liens
    ) != 1:
        raise ValueError("Lien OPC interne du composant Production absent ou ambigu.")
    prefixes = {}
    sous_modele = archive.xml(cible, namespaces_racine=prefixes)
    ressources = _ressources(sous_modele)
    objets = [] if ressources is None else ressources.findall(_OBJET)
    if (
        not _modele_qualifie(sous_modele, prefixes)
        or sous_modele.get("unit", "millimeter") != racine.get("unit", "millimeter")
        or len(objets) != 1
    ):
        raise ValueError("Partie Production, unité ou ressources non qualifiées.")
    feuille = objets[0]
    if (
        feuille.get("id") != composant.get("objectid") or set(feuille.attrib) - {"id", "type", "pid", "pindex", _UUID}
        or len(feuille) != 1 or feuille[0].tag != NS_CORE + "mesh"
    ):
        raise ValueError("Feuille Production absente ou non qualifiée.")
    return feuille, sous_modele, ressources, {
        "partie_feuille": cible, "parent_id": parent.get("id"), "transform_composant": translation,
    }
