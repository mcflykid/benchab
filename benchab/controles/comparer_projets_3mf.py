"""Compare les réglages reconnus de deux projets 3MF, en lecture seule."""

import math
import re
from pathlib import Path

from benchab import reglages
from benchab.archives_3mf import Archive3MF

_CONFIG = "Metadata/project_settings.config"
_NS_3MF = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
_NS_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_REL_MODELE = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
_NS_PRUSA = "{http://schemas.slic3r.org/3mf/2017/06}"
_DIALECTES_PEINTURE = {
    "paint_color": "paint_color",
    _NS_PRUSA + "mmu_segmentation": "slic3rpe:mmu_segmentation",
}
_UNITES_3MF = {"micron", "millimeter", "centimeter", "inch", "foot", "meter"}


def _lire(chemin):
    """Lit les réglages projet, peinture directe et SHA dans un contexte 3MF."""
    with Archive3MF(chemin) as archive:
        empreinte = archive.sha256()
        if _CONFIG in archive.noms():
            donnees = archive.reglages(_CONFIG)
        else:
            donnees = None
        peinture = _lire_peinture_directe(archive)
        archive.verifier_source(chemin)
    return (dict(donnees) if donnees is not None else None, empreinte, peinture)


def _lire_peinture_directe(archive):
    """Lit uniquement un modèle OPC direct à objet/maillage unique ; refuse composants et alias."""
    noms = set(archive.noms())
    relation_root = "_rels/.rels"
    inconnu = {"statut": "NON VÉRIFIÉ", "raison": "StartPart OPC et maillage direct non qualifiés."}
    if relation_root not in noms:
        return inconnu
    relations = archive.xml(relation_root)
    if relations.tag != _NS_REL + "Relationships":
        return inconnu
    candidates, ids = [], set()
    for relation in relations:
        identifiant, cible = relation.get("Id"), relation.get("Target")
        mode, type_relation = relation.get("TargetMode", "Internal"), relation.get("Type", "")
        if not identifiant or identifiant in ids or not cible or mode not in {"Internal", "External"}:
            return inconnu
        ids.add(identifiant)
        if type_relation.endswith("/3dmodel"):
            candidates.append((type_relation, mode, cible))
    if len(candidates) != 1 or candidates[0][:2] != (_REL_MODELE, "Internal"):
        return {**inconnu, "raison": "Relation OPC StartPart absente, externe ou ambiguë."}
    import posixpath
    from urllib.parse import unquote

    cible = unquote(candidates[0][2]).replace("\\", "/")
    modele = posixpath.normpath(cible.lstrip("/") if cible.startswith("/") else cible)
    if modele.startswith("../") or modele in {"", "."} or modele not in noms:
        return {**inconnu, "raison": "Cible StartPart hors archive ou absente."}
    racine = archive.xml(modele)
    if racine.tag != _NS_3MF + "model":
        return {**inconnu, "raison": "StartPart ne désigne pas un modèle Core reconnu."}
    unite = racine.get("unit", "millimeter")
    ressources = racine.find(_NS_3MF + "resources")
    construction = racine.find(_NS_3MF + "build")
    objets = [] if ressources is None else ressources.findall(_NS_3MF + "object")
    items = [] if construction is None else construction.findall(_NS_3MF + "item")
    if unite not in _UNITES_3MF or len(objets) != 1 or len(items) != 1:
        return {**inconnu, "raison": "Unité, cardinalité des objets ou ordre du build non qualifié."}
    objet, item = objets[0], items[0]
    ident = objet.get("id")
    attributs_path = [cle for cle in item.attrib if cle.rsplit("}", 1)[-1] == "path"]
    if (
        not ident or item.get("objectid") != ident or item.get("printable", "1") != "1"
        or attributs_path or item.get("transform") is not None or objet.get("type") != "model"
        or objet.find(_NS_3MF + "components") is not None
    ):
        return {**inconnu, "raison": "Instance, objet, composant ou p:path hors du sous-cas direct."}
    maillage = objet.find(_NS_3MF + "mesh")
    if maillage is None or objet.findall(_NS_3MF + "mesh") != [maillage]:
        return {**inconnu, "raison": "Maillage direct absent ou ambigu."}
    sommets = maillage.findall(_NS_3MF + "vertices/" + _NS_3MF + "vertex")
    triangles = maillage.findall(_NS_3MF + "triangles/" + _NS_3MF + "triangle")
    if not sommets or not triangles:
        return {**inconnu, "raison": "Maillage vide."}
    coordonnees = []
    for sommet in sommets:
        valeurs = tuple(sommet.get(cle) for cle in ("x", "y", "z"))
        try:
            numeriques = tuple(float(valeur) for valeur in valeurs)
        except (TypeError, ValueError):
            return {**inconnu, "raison": "Coordonnées de sommets invalides."}
        if any(not math.isfinite(valeur) for valeur in numeriques):
            return {**inconnu, "raison": "Coordonnées non finies."}
        coordonnees.append(valeurs)
    faces = []
    peintures = []
    proprietes_core, dialectes, autres_extensions = [], set(), []
    core = {"v1", "v2", "v3", "pid", "p1", "p2", "p3"}
    for triangle in triangles:
        indices = tuple(triangle.get(cle) for cle in ("v1", "v2", "v3"))
        try:
            entiers = tuple(int(valeur) for valeur in indices)
        except (TypeError, ValueError):
            return {**inconnu, "raison": "Indices de facettes invalides."}
        if any(indice < 0 or indice >= len(sommets) for indice in entiers):
            return {**inconnu, "raison": "Indice de facette hors du maillage."}
        faces.append(indices)
        proprietes_core.append({cle: triangle.get(cle) for cle in ("pid", "p1", "p2", "p3") if cle in triangle.attrib})
        peinture = {}
        for cle, valeur in triangle.attrib.items():
            if cle in core:
                continue
            dialecte = _DIALECTES_PEINTURE.get(cle)
            if dialecte is None:
                autres_extensions.append(cle)
            else:
                dialectes.add(cle)
                peinture[cle] = valeur
        peintures.append(peinture)
    if autres_extensions:
        return {
            **inconnu,
            "raison": "Attribut de facette hors du dialecte de peinture reconnu.",
            "attributs_non_qualifies": sorted(set(autres_extensions)),
        }
    return {
        "statut": "PASS",
        "identite": {
            "partie_opc": modele, "objet_id": ident, "unite": unite,
            "transform": item.get("transform"), "build_index": 1,
        },
        "coordonnees": coordonnees,
        "facettes": faces,
        "peintures": peintures,
        "dialectes": sorted(dialectes),
        "proprietes_core": proprietes_core,
    }


def _comparer_peinture(source, candidat):
    """Compare les valeurs paint littérales après preuve exacte du modèle et de ses facettes."""
    nv = {
        "statut": "NON VÉRIFIÉ",
        "dialecte": None,
        "differences_litterales": [],
    }
    if source["statut"] != "PASS" or candidat["statut"] != "PASS":
        return {**nv, "raison": source.get("raison") or candidat.get("raison") or "Modèle non qualifié."}
    for cle in ("identite", "coordonnees", "facettes"):
        if source[cle] != candidat[cle]:
            raison = (
                "Ordre ou indices des facettes différents."
                if cle == "facettes" else "Identité/coordonnées différente."
            )
            return {**nv, "raison": raison}
    dialectes_source, dialectes_candidat = source["dialectes"], candidat["dialectes"]
    if dialectes_source != dialectes_candidat or len(dialectes_source) != 1:
        return {**nv, "raison": "Dialecte/QName absent, mélangé ou différent entre les deux projets."}
    dialecte_qname = dialectes_source[0]
    dialecte = _DIALECTES_PEINTURE[dialecte_qname]
    differences = []
    for index, (peinture_source, peinture_candidat) in enumerate(
        zip(source["peintures"], candidat["peintures"], strict=True), start=1
    ):
        valeur_source = peinture_source.get(dialecte_qname)
        valeur_candidat = peinture_candidat.get(dialecte_qname)
        if valeur_source != valeur_candidat:
            differences.append({
                "objet_id": source["identite"]["objet_id"], "facette": index,
                "attribut": dialecte,
                "source": valeur_source, "candidat": valeur_candidat,
                "present_source": dialecte_qname in peinture_source,
                "present_candidat": dialecte_qname in peinture_candidat,
                "statut": "DIFFÉRENT",
            })
    statut = "WARN" if differences else "PASS"
    return {"statut": statut, "dialecte": dialecte, "differences_litterales": differences}


def _type_qualifie(cle, valeur):
    """Vérifie la forme native d’une valeur sans normaliser le dictionnaire."""
    definition = reglages.definition(cle, valeur=valeur)
    formes = definition.get("formes_certifiees", definition.get("formes", ()))
    return definition.get("type") not in (None, "inconnu") and type(valeur).__name__ in formes


def _comparer_reglages(source, candidat):
    """Compare les clés projet typées exactement et liste les absences/inconnus."""
    if source is None or candidat is None:
        return {
            "statut": "NON VÉRIFIÉ", "identiques": [], "differences": [], "absences": [],
            "non_verifies": [{
                "cle": _CONFIG, "statut": "NON VÉRIFIÉ",
                "raison": "Le membre de réglages projet manque d’une archive.",
                "present_source": source is not None, "present_candidat": candidat is not None,
            }],
        }
    identiques, differences, absences, inconnus = [], [], [], []
    qualifications = []
    for donnees in (source, candidat):
        contexte = reglages.contexte_filaments(donnees)
        qualifications.append({
            ligne["cle"]: ligne["statut"]
            for ligne in reglages.verifier(donnees, contexte)
        })
    qualification_source, qualification_candidat = qualifications
    for cle in sorted(set(source) | set(candidat)):
        present_source, present_candidat = cle in source, cle in candidat
        entree = {
            "cle": cle,
            "type": reglages.definition(cle).get("type", "inconnu"),
            "valeur_source": source.get(cle),
            "valeur_candidat": candidat.get(cle),
        }
        if not present_source or not present_candidat:
            absences.append({
                **entree,
                "present_source": present_source,
                "present_candidat": present_candidat,
                "statut": "DIFFÉRENT",
            })
        elif (
            not _type_qualifie(cle, source[cle])
            or not _type_qualifie(cle, candidat[cle])
            or type(source[cle]) is not type(candidat[cle])
            or qualification_source.get(cle) != "PASS"
            or qualification_candidat.get(cle) != "PASS"
        ):
            inconnus.append({
                **entree, "statut": "NON VÉRIFIÉ",
                "raison": "Type, forme ou cardinalité non qualifié par le dictionnaire.",
            })
        elif source[cle] != candidat[cle]:
            differences.append({**entree, "statut": "DIFFÉRENT"})
        else:
            identiques.append({**entree, "statut": "IDENTIQUE"})
    statut = "NON VÉRIFIÉ" if inconnus else "WARN" if differences or absences else "PASS"
    return {
        "statut": statut, "identiques": identiques, "differences": differences,
        "absences": absences, "non_verifies": inconnus,
    }


def _palette(source, candidat):
    """Compare la palette littérale et qualifie séparément son lien aux profils."""
    resultats = {
        "statut": "NON VÉRIFIÉ",
        "identites_profil": {"statut": "NON VÉRIFIÉ", "source": None, "candidat": None},
        "associations_profils": [],
        "association_profil_couleur": {"statut": "NON VÉRIFIÉ"},
        "differences_litterales": [],
        "couleurs_residuelles": {"source": [], "candidat": []},
        "attribution_physique_piece": {
            "statut": "NON VÉRIFIÉ",
            "raison": "Les identifiants de profil ne qualifient ni une bobine physique ni une attribution de pièce.",
        },
    }
    if source is None or candidat is None:
        resultats["raison"] = "Réglages projet manquants."
        return resultats
    identites_source = source.get("filament_settings_id")
    identites_candidat = candidat.get("filament_settings_id")
    couleurs_source = source.get("filament_colour")
    couleurs_candidat = candidat.get("filament_colour")
    resultats["identites_profil"] = {
        "statut": (
            "PASS" if isinstance(identites_source, list) and identites_source
            and all(isinstance(x, str) and x.strip() for x in identites_source)
            and isinstance(identites_candidat, list)
            and all(isinstance(x, str) and x.strip() for x in identites_candidat)
            and identites_source == identites_candidat
            else "NON VÉRIFIÉ" if identites_source is None and identites_candidat is None
            else "DIFFÉRENT"
        ),
        "source": identites_source,
        "candidat": identites_candidat,
        "nature": "Références de profils déclarées ; pas des identités physiques.",
    }
    resultats["differences_litterales"] = _differences_couleurs_litterales(
        source, candidat, couleurs_source, couleurs_candidat
    )
    resultats["couleurs_residuelles"] = {
        "source": _couleurs_residuelles(identites_source, couleurs_source),
        "candidat": _couleurs_residuelles(identites_candidat, couleurs_candidat),
    }
    raison = _raison_association_palette(
        source, candidat, identites_source, identites_candidat, couleurs_source, couleurs_candidat
    )
    if raison is not None:
        resultats["raison"] = raison
        return resultats

    profils = []
    for index, (identite, couleur_source, couleur_candidat) in enumerate(
        zip(identites_source, couleurs_source, couleurs_candidat, strict=True), start=1
    ):
        profils.append({
            "index_declaration": index,
            "identite_profil": identite,
            "couleur_source": couleur_source,
            "couleur_candidat": couleur_candidat,
            "statut": "IDENTIQUE" if couleur_source == couleur_candidat else "DIFFÉRENT",
        })
    statut = "WARN" if any(p["statut"] == "DIFFÉRENT" for p in profils) else "PASS"
    resultats.update(
        statut=statut,
        associations_profils=profils,
        association_profil_couleur={
            "statut": "PASS",
            "portee": "Profil unique déclaré et index de palette ; aucune identité physique ou de pièce.",
        },
    )
    return resultats


def _differences_couleurs_litterales(source, candidat, couleurs_source, couleurs_candidat):
    """Compare les valeurs présentes par indice JSON sans en faire des slots actifs."""
    present_source = "filament_colour" in source
    present_candidat = "filament_colour" in candidat
    if not present_source and not present_candidat:
        return []
    if not present_source or not present_candidat:
        return [{
            "index_litteral": None,
            "couleur_source": couleurs_source,
            "couleur_candidat": couleurs_candidat,
            "present_source": present_source,
            "present_candidat": present_candidat,
            "statut": "DIFFÉRENT",
        }]
    if not isinstance(couleurs_source, list) or not isinstance(couleurs_candidat, list):
        return [{
            "index_litteral": None,
            "couleur_source": couleurs_source,
            "couleur_candidat": couleurs_candidat,
            "statut": "IDENTIQUE" if type(couleurs_source) is type(couleurs_candidat)
            and couleurs_source == couleurs_candidat else "DIFFÉRENT",
        }]
    differences = []
    for index in range(max(len(couleurs_source), len(couleurs_candidat))):
        present_source = index < len(couleurs_source)
        present_candidat = index < len(couleurs_candidat)
        valeur_source = couleurs_source[index] if present_source else None
        valeur_candidat = couleurs_candidat[index] if present_candidat else None
        different = (
            not present_source or not present_candidat or type(valeur_source) is not type(valeur_candidat)
            or valeur_source != valeur_candidat
        )
        statut = "DIFFÉRENT" if different else "IDENTIQUE"
        if statut == "DIFFÉRENT":
            differences.append({
                "index_litteral": index + 1,
                "couleur_source": valeur_source,
                "couleur_candidat": valeur_candidat,
                "present_source": present_source,
                "present_candidat": present_candidat,
                "statut": statut,
            })
    return differences


def _couleurs_residuelles(identites, couleurs):
    """Liste les couleurs au-delà des identités de profil, sans les promouvoir en slots."""
    if not isinstance(identites, list) or not isinstance(couleurs, list):
        return []
    return [
        {"index_litteral": index + 1, "valeur": couleur, "statut": "NON VÉRIFIÉ"}
        for index, couleur in enumerate(couleurs[len(identites):], start=len(identites))
    ]


def _raison_association_palette(source, candidat, identites_source, identites_candidat,
                                couleurs_source, couleurs_candidat):
    """Refuse le lien profil-couleur si unicité, couleur RGB ou contexte typé manquent."""
    if (
        not isinstance(identites_source, list)
        or not identites_source
        or not all(isinstance(x, str) and x.strip() for x in identites_source)
        or not isinstance(identites_candidat, list)
        or not all(isinstance(x, str) and x.strip() for x in identites_candidat)
        or identites_source != identites_candidat
    ):
        return "Identités de profils absentes, invalides ou d’ordre différent."
    if len(set(identites_source)) != len(identites_source):
        return "Identifiant de profil répété ; association profil-couleur ambiguë."
    for donnees, identites, couleurs in (
        (source, identites_source, couleurs_source),
        (candidat, identites_candidat, couleurs_candidat),
    ):
        contexte = reglages.contexte_filaments(donnees)
        if (
            not _type_qualifie("filament_settings_id", identites)
            or contexte["correspondances_filaments"].get("filament_colour") != identites
            or not isinstance(couleurs, list)
            or len(couleurs) != len(identites)
            or not _type_qualifie("filament_colour", couleurs)
            or any(not isinstance(couleur, str) or re.fullmatch(r"#[0-9A-Fa-f]{6}", couleur) is None
                   for couleur in couleurs)
        ):
            return "Palette vide, mal formée ou de cardinalité non qualifiée par le contexte typé."
    return None


def comparer(source: str | Path, candidat: str | Path) -> dict:
    """Compare deux 3MF sans les modifier ; valeurs sans conversion, empreintes SHA-256 exactes.

    Les réglages projet reconnus sont identiques, différents ou NON VÉRIFIÉS.
    La peinture par facette reste NON VÉRIFIÉE, faute d’identité et d’ordre de maillage
    comparés dans cette première étape.
    """
    source = Path(source).expanduser()
    candidat = Path(candidat).expanduser()
    etat_source, sha_source, peinture_source = _lire(source)
    etat_candidat, sha_candidat, peinture_candidat = _lire(candidat)
    reglages_comparaison = _comparer_reglages(etat_source, etat_candidat)
    palette = _palette(etat_source, etat_candidat)
    extension_peinture = _comparer_peinture(peinture_source, peinture_candidat)
    return {
        "schema": 1,
        "statut": "NON VÉRIFIÉ",  # La peinture par facette n’est pas qualifiée dans ce lot.
        "source": str(source),
        "candidat": str(candidat),
        "sha256": {
            "source": sha_source,
            "candidat": sha_candidat,
        },
        "reglages": reglages_comparaison,
        "palette": palette,
        "peinture": {
            "statut": "NON VÉRIFIÉ",
            "proprietes_standard_3mf": {
                "statut": "NON VÉRIFIÉ",
                "present_source": bool(peinture_source.get("proprietes_core")),
                "present_candidat": bool(peinture_candidat.get("proprietes_core")),
                "raison": "Les références Core pid/p1–p3 et leurs ressources ne sont pas résolues sémantiquement.",
            },
            "extensions_asn_prusa": extension_peinture,
            "raison": (
                "Aucune équivalence n’est déduite entre matériaux standard 3MF et peinture par facette "
                "spécifique aux slicers. Aucun transcodage ni verdict de compatibilité n’est produit."
            ),
        },
        "limites": [
            "Comparaison en lecture seule des seuls réglages projet décodés par le dictionnaire typé.",
            "Les valeurs sont comparées dans leur forme native, sans normalisation ni réindexation.",
            "La peinture de facettes standard ou spécifique au slicer reste NON VÉRIFIÉE.",
            "Aucune conversion, réécriture, compatibilité de slicer ou validation physique n’est établie.",
        ],
    }
