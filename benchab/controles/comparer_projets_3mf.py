"""Compare les réglages reconnus de deux projets 3MF, en lecture seule."""

import math
import re
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from benchab import reglages
from benchab.archives_3mf import Archive3MF
from benchab.controles.composants_production_3mf import REL_MODELE, feuille_production, relations_opc
from benchab.controles.materiaux_core_3mf import NS_CORE, resoudre

_CONFIG = "Metadata/project_settings.config"
_NS_PRUSA = "{http://schemas.slic3r.org/3mf/2017/06}"
_DIALECTES_PEINTURE = {
    "paint_color": "paint_color",
    _NS_PRUSA + "mmu_segmentation": "slic3rpe:mmu_segmentation",
}
_UNITES_3MF = {"micron", "millimeter", "centimeter", "inch", "foot", "meter"}


_NOMBRE_MATRICE = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]{1,3})?")
_IDENTITE_LINEAIRE = (1, 0, 0, 0, 1, 0, 0, 0, 1)


def _translation_directe_qualifiee(texte):
    """Vérifie une translation pure Core, dans les unités déclarées du modèle."""
    if texte is None:
        return True
    if len(texte) > 512 or not re.fullmatch(r"[0-9eE+ .\t\r\n-]+", texte):
        return False
    champs = texte.split()
    if len(champs) != 12 or any(not _NOMBRE_MATRICE.fullmatch(c) for c in champs):
        return False
    # Core §3.3 : neuf coefficients linéaires puis trois translations.
    # Decimal évite de transformer un cisaillement minuscule en zéro.
    valeurs = tuple(map(Decimal, champs))
    return valeurs[:9] == _IDENTITE_LINEAIRE and all(v.copy_abs() <= 10**9 for v in valeurs[9:])


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


def _composants_core_locaux(racine, ressources, objets, item):
    """Identifie au plus seize feuilles locales uniques, dans l’ordre du parent."""
    if (
        not 2 <= len(objets) <= 17 or len(ressources) > 4096 or racine.get("requiredextensions", "").strip()
        or set(item.attrib) - {"objectid", "transform", "printable"} or len(item)
    ):
        return None
    *feuilles, parent = objets
    # Core §4 : les feuilles précèdent leurs références et partagent l’espace d’ID des matériaux.
    ids = set()
    for ressource in ressources:
        ident = ressource.get("id", "")
        if (
            ressource.tag not in {NS_CORE + "object", NS_CORE + "basematerials"}
            or re.fullmatch(r"[1-9][0-9]{0,9}", ident) is None
            or int(ident) >= 2**31 or ident in ids
        ):
            return None
        ids.add(ident)
    if (
        parent.get("id") != item.get("objectid") or set(parent.attrib) - {"id", "type"}
        or (len(feuilles) > 1 and parent.get("type", "model") != "model")
        or len(parent) != 1 or parent[0].tag != NS_CORE + "components"
        or any(set(f.attrib) - {"id", "type", "pid", "pindex"}
               or len(f) != 1 or f[0].tag != NS_CORE + "mesh" for f in feuilles)
    ):
        return None
    composants = parent[0]
    if composants.attrib or len(composants) != len(feuilles):
        return None
    disponibles = {f.get("id"): f for f in feuilles}
    resultat = []
    for composant in composants:
        translation = composant.get("transform")
        if (
            composant.tag != NS_CORE + "component" or set(composant.attrib) - {"objectid", "transform"}
            or len(composant) or not _translation_directe_qualifiee(translation)
        ):
            return None
        feuille = disponibles.pop(composant.get("objectid"), None)
        if feuille is None:
            return None
        resultat.append((feuille, {"parent_id": parent.get("id"), "transform_composant": translation}))
    return resultat


def _lire_peinture_directe(archive):
    """Lit le maillage direct ou les feuilles locales d’un assemblage Core borné."""
    noms = set(archive.noms())
    relation_root = "_rels/.rels"
    inconnu = {"statut": "NON VÉRIFIÉ", "raison": "StartPart OPC et maillage direct non qualifiés."}
    if relation_root not in noms:
        return inconnu
    try:
        relations = relations_opc(archive, relation_root)
    except ValueError as erreur:
        return {**inconnu, "raison": str(erreur)}
    candidates = [relation for relation in relations if relation[0].endswith("/3dmodel")]
    if len(candidates) != 1 or candidates[0][:2] != (REL_MODELE, "Internal"):
        return {**inconnu, "raison": "Relation OPC StartPart absente, externe ou ambiguë."}
    from urllib.parse import unquote, urlsplit

    cible_brute = candidates[0][2]
    uri = urlsplit(cible_brute)
    cible = unquote(uri.path)
    segments = cible.split("/")
    if (
        uri.scheme or uri.netloc or uri.query or uri.fragment or "\\" in cible
        or any(segment == ".." for segment in segments)
    ):
        return {**inconnu, "raison": "Cible StartPart externe ou chemin OPC non qualifié."}
    modele = cible.lstrip("/") if cible.startswith("/") else cible
    if modele in {"", "."} or modele not in noms:
        return {**inconnu, "raison": "Cible StartPart hors archive ou absente."}
    prefixes = {}
    racine = archive.xml(modele, namespaces_racine=prefixes)
    if racine.tag != NS_CORE + "model":
        return {**inconnu, "raison": "StartPart ne désigne pas un modèle Core reconnu."}
    unite = racine.get("unit", "millimeter")
    ressources_xml = racine.findall(NS_CORE + "resources")
    constructions = racine.findall(NS_CORE + "build")
    if len(ressources_xml) != 1 or len(constructions) != 1:
        return {**inconnu, "raison": "Ressources ou build absents ou ambigus."}
    ressources, construction = ressources_xml[0], constructions[0]
    objets = ressources.findall(NS_CORE + "object")
    items = construction.findall(NS_CORE + "item")
    if unite not in _UNITES_3MF or len(items) != 1 or not 1 <= len(objets) <= 17:
        return {**inconnu, "raison": "Unité, cardinalité des objets ou ordre du build non qualifié."}
    item = items[0]
    attributs_path = [cle for cle in item.attrib if cle.rsplit("}", 1)[-1] == "path"]
    transform = item.get("transform")
    if item.get("printable", "1") != "1" or attributs_path:
        return {**inconnu, "raison": "Instance ou p:path hors du sous-cas direct."}
    if not _translation_directe_qualifiee(transform):
        return {**inconnu, "raison": "Placement hors des translations directes qualifiées."}
    contexte = {"partie_opc": modele, "unite": unite, "transform": transform, "build_index": 1}
    if len(objets) == 1:
        objet = objets[0]
        if not objet.get("id") or item.get("objectid") != objet.get("id"):
            return {**inconnu, "raison": "Objet du build direct non identifié."}
        if objet.find(NS_CORE + "components") is not None:
            try:
                feuille, sous_modele, ressources_feuille, provenance = feuille_production(
                    archive, modele, racine, prefixes, construction,
                    translation_qualifiee=_translation_directe_qualifiee,
                )
            except ValueError as erreur:
                return {**inconnu, "raison": str(erreur)}
            return _lire_feuille_core(
                feuille, {**contexte, "production": provenance}, sous_modele, ressources_feuille, inconnu,
                strict=True,
            )
        lecture = _lire_feuille_core(objet, contexte, racine, ressources, inconnu)
        # Le rapprochement entre conteneurs exige aussi la forme stricte du maillage direct.
        if lecture["statut"] == "PASS":
            lecture["direct_qualifie"] = _direct_qualifie(racine, construction, ressources, objet, item)
        return lecture

    choix = _composants_core_locaux(racine, ressources, objets, item)
    if choix is None:
        return {**inconnu, "raison": "Assemblage hors du sous-cas Core à feuilles locales distinctes."}
    if len(choix) == 1:
        objet, assemblage = choix[0]
        return _lire_feuille_core(objet, {**contexte, "assemblage": assemblage}, racine, ressources, inconnu)
    if construction.attrib or len(construction) != 1:
        return {**inconnu, "raison": "Build hors du sous-cas d’assemblage local."}
    lectures = [
        _lire_feuille_core(objet, {**contexte, "assemblage": assemblage}, racine, ressources, inconnu, strict=True)
        for objet, assemblage in choix
    ]
    non_qualifie = next((lecture for lecture in lectures if lecture["statut"] != "PASS"), None)
    if non_qualifie is not None:
        return non_qualifie
    return _assembler_feuilles(lectures)


def _direct_qualifie(racine, construction, ressources, objet, item):
    """Écarte les extensions et ambiguïtés du modèle direct avant un rapprochement Production."""
    identifiants = [ressource.get("id", "") for ressource in ressources]
    return (
        not racine.get("requiredextensions", "").strip()
        and set(racine.attrib) <= {"unit", "{http://www.w3.org/XML/1998/namespace}lang"}
        and all(e.tag in {NS_CORE + t for t in ("metadata", "resources", "build")} for e in racine)
        and len(construction) == 1 and not construction.attrib and not len(item)
        and set(item.attrib) <= {"objectid", "transform", "printable"}
        and len(objet) == 1 and set(objet.attrib) <= {"id", "type", "pid", "pindex", "name", "partnumber"}
        and all(ressource.tag in {NS_CORE + "object", NS_CORE + "basematerials"} for ressource in ressources)
        and all(re.fullmatch(r"[1-9][0-9]{0,9}", ident) and int(ident) < 2**31 for ident in identifiants)
        and len(identifiants) == len(set(identifiants))
    )


def _maillage_strict(maillage, groupes, sommets, triangles):
    """Vérifie que seuls les éléments géométriques et attributs attendus sont présents."""
    return not (
        maillage.attrib or len(maillage) != 2 or any(g.attrib for g in groupes)
        or len(groupes[0]) != len(sommets) or len(groupes[1]) != len(triangles)
        or any(set(s.attrib) != {"x", "y", "z"} or len(s) for s in sommets)
        or any(len(t) for t in triangles)
    )


def _lire_feuille_core(objet, contexte, racine, ressources, inconnu, *, strict=False):
    """Lit les attributs et le maillage d’une feuille ; l’identité reste littérale."""
    ident = objet.get("id")
    if objet.get("type") != "model" or objet.find(NS_CORE + "components") is not None:
        return {**inconnu, "raison": "Objet de type model sans composants requis."}
    proprietes_objet = {cle: objet.get(cle) for cle in ("pid", "pindex") if cle in objet.attrib}
    maillage = objet.find(NS_CORE + "mesh")
    if maillage is None or objet.findall(NS_CORE + "mesh") != [maillage]:
        return {**inconnu, "raison": "Maillage direct absent ou ambigu."}
    groupes_sommets = maillage.findall(NS_CORE + "vertices")
    groupes_triangles = maillage.findall(NS_CORE + "triangles")
    if len(groupes_sommets) != 1 or len(groupes_triangles) != 1:
        return {**inconnu, "raison": "Listes de sommets ou de facettes absentes ou ambiguës."}
    sommets = groupes_sommets[0].findall(NS_CORE + "vertex")
    triangles = groupes_triangles[0].findall(NS_CORE + "triangle")
    if not sommets or not triangles:
        return {**inconnu, "raison": "Maillage vide."}
    maillage_strict = _maillage_strict(maillage, (*groupes_sommets, *groupes_triangles), sommets, triangles)
    if strict and not maillage_strict:
        return {**inconnu, "raison": "Extension du maillage local non qualifiée."}
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
        if len(set(entiers)) != 3:
            return {**inconnu, "raison": "Facette avec indices de sommets répétés."}
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
        "maillage_strict": maillage_strict,
        "identite": {**contexte, "objet_id": ident},
        "coordonnees": coordonnees,
        "facettes": faces,
        "peintures": peintures,
        "dialectes": sorted(dialectes),
        "proprietes_core": resoudre(racine, ressources, proprietes_objet, proprietes_core),
    }


def _assembler_feuilles(lectures):
    """Rassemble les comparaisons sans perdre l’identité et l’index local des facettes."""
    identites = [f["identite"] for f in lectures]
    ids = [i["objet_id"] for i in identites]
    proprietes = [f["proprietes_core"] for f in lectures]
    present = any(c["present"] for c in proprietes)
    refus = next((c for c in proprietes if c["statut"] != "PASS"), None)
    if refus is None:
        core = {
            "statut": "PASS", "present": present, "objet": [c["objet"] for c in proprietes],
            "facettes": [facette for c in proprietes for facette in c["facettes"]],
        }
    else:
        core = {**refus, "present": present}
    return {
        "statut": "PASS",
        "identite": {"feuilles_locales": identites},
        "objets_locaux": ids,
        "facettes_locales": [
            (ident, numero) for ident, f in zip(ids, lectures, strict=True)
            for numero in range(1, len(f["facettes"]) + 1)
        ],
        # Garder les séparations de maillage : deux préfixes concaténés ne sont pas une preuve de correspondance.
        "coordonnees": [f["coordonnees"] for f in lectures],
        "facettes": [f["facettes"] for f in lectures],
        "peintures": [valeur for f in lectures for valeur in f["peintures"]],
        "dialectes": sorted({d for f in lectures for d in f["dialectes"]}),
        "proprietes_core": core,
    }


def _identite_facette(source, index):
    """Renvoie l’objet et l’index d’origine de la facette, à partir de un."""
    if "facettes_locales" in source:
        return source["facettes_locales"][index - 1]
    return source["identite"]["objet_id"], index


def _correspondance_production(source, candidat):
    """Établit la correspondance d’une feuille unique entre placements en translation pure."""
    identites = [lecture["identite"] for lecture in (source, candidat)]
    if not any("production" in identite for identite in identites):
        return None
    if not all(
        lecture.get("maillage_strict") and ("production" in identite or lecture.get("direct_qualifie"))
        for lecture, identite in zip((source, candidat), identites, strict=True)
    ):
        return False

    def pose(identite):
        production = identite.get("production", {})
        # Les translations Core se composent par addition. Les fractions
        # gardent exactes les décimales, y compris un très petit déplacement.
        deplacements = [
            tuple(map(Fraction, texte.split()[-3:])) if texte else (0, 0, 0)
            for texte in (identite["transform"], production.get("transform_composant"))
        ]
        return identite["unite"], identite["objet_id"], tuple(map(sum, zip(*deplacements, strict=True)))

    return pose(identites[0]) == pose(identites[1])


def _comparer_peinture(source, candidat):
    """Compare les valeurs paint littérales après preuve exacte du modèle et de ses facettes."""
    nv = {
        "statut": "NON VÉRIFIÉ",
        "dialecte": None,
        "differences_litterales": [],
    }
    if source["statut"] != "PASS" or candidat["statut"] != "PASS":
        return {**nv, "raison": source.get("raison") or candidat.get("raison") or "Modèle non qualifié."}
    production = _correspondance_production(source, candidat)
    if production is False:
        return {**nv, "raison": "Identité ou placement Production différents ou non qualifiés."}
    cles = ("coordonnees", "facettes") if production else ("identite", "coordonnees", "facettes")
    for cle in cles:
        if source[cle] != candidat[cle]:
            raison = (
                "Ordre ou indices des facettes différents."
                if cle == "facettes" else "Identité/coordonnées différente."
            )
            return {**nv, "raison": raison}
    dialectes_source, dialectes_candidat = source["dialectes"], candidat["dialectes"]
    raison_perte = None
    if dialectes_source == dialectes_candidat and len(dialectes_source) != 1:
        return {**nv, "raison": "Dialecte/QName absent, mélangé ou différent entre les deux projets."}
    if len(dialectes_source) == len(dialectes_candidat) == 1:
        if dialectes_source != dialectes_candidat:
            return {**nv, "raison": "Dialecte/QName différent entre les deux projets."}
        dialecte_qname = dialectes_source[0]
    elif len(dialectes_source) == 1 and not dialectes_candidat:
        dialecte_qname = dialectes_source[0]
        raison_perte = "peinture absente du candidat"
    elif not dialectes_source and len(dialectes_candidat) == 1:
        dialecte_qname = dialectes_candidat[0]
        raison_perte = "peinture absente de la source"
    else:
        return {**nv, "raison": "Dialecte/QName absent, mélangé ou différent entre les deux projets."}
    dialecte = _DIALECTES_PEINTURE[dialecte_qname]
    differences = []
    for index, (peinture_source, peinture_candidat) in enumerate(
        zip(source["peintures"], candidat["peintures"], strict=True), start=1
    ):
        valeur_source = peinture_source.get(dialecte_qname)
        valeur_candidat = peinture_candidat.get(dialecte_qname)
        if valeur_source != valeur_candidat:
            objet, facette = _identite_facette(source, index)
            differences.append({
                "objet_id": objet, "facette": facette,
                "attribut": dialecte,
                "source": valeur_source, "candidat": valeur_candidat,
                "present_source": dialecte_qname in peinture_source,
                "present_candidat": dialecte_qname in peinture_candidat,
                "statut": "DIFFÉRENT",
            })
    statut = "WARN" if differences else "PASS"
    resultat = {"statut": statut, "dialecte": dialecte, "differences_litterales": differences}
    if production:
        resultat["correspondance_production"] = {
            cote: lecture["identite"] for cote, lecture in (("source", source), ("candidat", candidat))
        }
    if differences and raison_perte is not None:
        resultat["cause"] = raison_perte
    return resultat


def _comparer_core(source, candidat):
    """Compare nom et sRGB déclaré après résolution des matériaux et preuve du même maillage."""
    avant, apres = source.get("proprietes_core", {}), candidat.get("proprietes_core", {})
    resultat = {
        "statut": "NON VÉRIFIÉ",
        "present_source": avant.get("present", False),
        "present_candidat": apres.get("present", False),
        "facettes_comparees": 0,
        "differences": [],
    }
    if any(p["statut"] != "PASS" for p in (source, candidat)):
        return {**resultat, "raison": "StartPart ou maillage hors du sous-cas qualifié."}
    if any(source[cle] != candidat[cle] for cle in ("identite", "coordonnees", "facettes")):
        return {**resultat, "raison": "Identité, coordonnées ou ordre des facettes différents."}
    if avant["statut"] != "PASS" or apres["statut"] != "PASS":
        return {**resultat, "raison": avant.get("raison") or apres["raison"]}
    if not (avant["present"] or apres["present"]):
        return {**resultat, "raison": "Aucun matériau de base attribué dans les deux modèles."}
    differences = []
    if "objets_locaux" in source:
        for ident, a, b in zip(source["objets_locaux"], avant["objet"], apres["objet"], strict=True):
            if a != b:
                differences.append({"portee": "objet", "objet_id": ident, "source": a, "candidat": b})
    elif avant["objet"] != apres["objet"]:
        differences.append({"portee": "objet", "source": avant["objet"], "candidat": apres["objet"]})
    for index, (a, b) in enumerate(zip(avant["facettes"], apres["facettes"], strict=True), start=1):
        if a != b:
            ident, facette = _identite_facette(source, index)
            localisation = {"objet_id": ident} if "objets_locaux" in source else {}
            differences.append({"portee": "facette", **localisation, "facette": facette, "source": a, "candidat": b})
    return {
        **resultat, "statut": "WARN" if differences else "PASS",
        "facettes_comparees": len(avant["facettes"]), "differences": differences,
        "limite": "Nom et sRGB d’affichage Core ; aucune correspondance de filament ni compatibilité du slicer.",
    }


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
    La peinture et les matériaux Core par facette sont comparés dans les
    sous-cas directs ou locaux, et la peinture d’une feuille Production
    peut être comparée à géométrie et pose strictement identiques.
    Les translations sont déclarées dans l’unité du modèle. La compatibilité
    du slicer et la tenue physique restent NON VÉRIFIÉES.
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
        "statut": "NON VÉRIFIÉ",  # La compatibilité de conversion reste hors qualification.
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
            "proprietes_standard_3mf": _comparer_core(peinture_source, peinture_candidat),
            "extensions_asn_prusa": extension_peinture,
            "raison": (
                "Aucune équivalence n’est déduite entre matériaux standard 3MF et peinture par facette "
                "spécifique aux slicers. Aucun transcodage ni verdict de compatibilité n’est produit."
            ),
        },
        "limites": [
            "Comparaison en lecture seule des seuls réglages projet décodés par le dictionnaire typé.",
            "Les valeurs sont comparées dans leur forme native, sans normalisation ni réindexation.",
            "Seuls les matériaux de base Core déclarés et les attributs littéraux de peinture connus sont "
            "comparés dans les sous-cas directs ou d’assemblage local qualifiés. Le sous-cas Production "
            "à une feuille externe couvre les attributs littéraux de peinture.",
            "Aucune conversion, réécriture, compatibilité de slicer ou validation physique n’est établie.",
        ],
    }



def resumer(rapport, chemin_rapport):
    """Rend les statuts et nombres d’écarts, avec le chemin du JSON complet.

    Les compteurs sont sans unité et les statuts restent ceux du rapport
    en entrée. Ne réinterprète ni un réglage, ni la couleur ni la compatibilité.
    La publication du détail est du ressort de la commande.
    """
    reglages_projet = rapport["reglages"]
    extensions = rapport["peinture"]["extensions_asn_prusa"]
    core = rapport["peinture"]["proprietes_standard_3mf"]
    return {
        "statut": rapport["statut"],
        "rapport": str(chemin_rapport),
        "sha256": rapport["sha256"],
        "reglages": {
            "statut": reglages_projet["statut"],
            **{cle: len(reglages_projet[cle]) for cle in ("identiques", "differences", "absences", "non_verifies")},
        },
        "palette": {"statut": rapport["palette"]["statut"]},
        "peinture": {
            "statut": rapport["peinture"]["statut"],
            "extensions": extensions["statut"],
            "differences_litterales": len(extensions["differences_litterales"]),
            "materiaux_core": core["statut"],
            "differences_core": len(core["differences"]),
        },
    }
