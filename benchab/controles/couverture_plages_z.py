"""Contrôle les plages Z d’un 3MF contre une intention explicite, sans écriture."""

import argparse
import json
import math
import posixpath
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree
from zipfile import BadZipFile

import numpy as np

from benchab import reglages as schema_reglages
from benchab.archives_3mf import Archive3MF
from benchab.chemins import sans_lien
from benchab.imports3d.lecture_3mf import NS
from benchab.imports3d.lecture_3mf import lire as lire_scene
from benchab.qualite.empreintes import sha256_fichier
from benchab.qualite.statuts import code_statut

_XML_MAX = 32 * 1024 * 1024
_NOMS_INTENTION = {"totale", "partielle_volontaire", "zone_fine"}


def _json_sans_doublons(paires):
    """Refuse les clés JSON répétées ; entrée paires brutes, sortie dictionnaire non ambigu."""
    resultat = {}
    for cle, valeur in paires:
        if cle in resultat:
            raise ValueError("Clé JSON répétée dans la déclaration d’intention.")
        resultat[cle] = valeur
    return resultat


def _intentions(source):
    """Charge une déclaration explicite ; renvoie les intentions et leurs identifiants uniques."""
    if isinstance(source, (str, Path)):
        chemin = sans_lien(Path(source), strict=True)
        contenu = chemin.read_text(encoding="utf-8")
        declaration = json.loads(
            contenu,
            object_pairs_hook=_json_sans_doublons,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Constante JSON interdite.")),
        )
    else:
        declaration = source
    schema = declaration.get("schema") if isinstance(declaration, dict) else None
    if not isinstance(declaration, dict) or type(schema) is not int or schema != 1:
        raise ValueError("Déclaration d’intention schema 1 requise.")
    items = declaration.get("intentions")
    if not isinstance(items, list) or len(items) != 1:
        raise ValueError("Une seule intention Z est vérifiée par 3MF dans cette étape.")
    resultat, vus = [], set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Chaque intention Z doit être un objet JSON.")
        identifiant = item.get("objet_id")
        type_intention = item.get("type")
        if not isinstance(identifiant, str) or not identifiant.isdigit() or identifiant in vus:
            raise ValueError("Identifiant d’objet absent, invalide ou répété.")
        if type_intention not in _NOMS_INTENTION:
            raise ValueError("Type d’intention Z inconnu.")
        champs = {"objet_id", "type"}
        if type_intention in {"partielle_volontaire", "zone_fine"}:
            champs |= {"min_z_mm", "max_z_mm"}
        if type_intention == "zone_fine":
            champs.add("hauteur_max_mm")
        if set(item) != champs:
            raise ValueError("Champs d’intention manquants ou inconnus.")
        vus.add(identifiant)
        resultat.append(dict(item))
    return resultat


def _nombre_fini(valeur, libelle):
    """Convertit une valeur en mm finie ; refuse les booléens et les conversions ambiguës."""
    if isinstance(valeur, bool):
        raise ValueError(f"{libelle} doit être une valeur numérique en mm.")
    try:
        resultat = float(valeur)
    except (TypeError, ValueError) as erreur:
        raise ValueError(f"{libelle} doit être une valeur numérique en mm.") from erreur
    if not math.isfinite(resultat):
        raise ValueError(f"{libelle} doit être fini en mm.")
    return resultat


def _nombre_type(texte, cle, portee, libelle):
    """Valide la chaîne XML via le dictionnaire typé avant de lire sa valeur numérique en mm."""
    definition = schema_reglages.definition(cle, valeur=texte, portee=portee)
    if not isinstance(texte, str) or definition.get("type") != "chaine":
        raise ValueError(f"{libelle} absent ou de forme non qualifiée.")
    return _nombre_fini(texte, libelle)


def _matrice_z_inchangee(item):
    """Vérifie que le transform 3MF conserve exactement le repère Z local en mm."""
    texte = item.get("transform")
    if texte is None:
        nombres = np.eye(4)
    else:
        try:
            valeurs = np.asarray([float(partie) for partie in texte.split()], dtype=float)
        except (TypeError, ValueError):
            return False
        if valeurs.shape != (12,) or not np.isfinite(valeurs).all():
            return False
        nombres = np.eye(4)
        nombres[:3] = valeurs.reshape(4, 3).T
    # Les couches suivent Z objet depuis zéro ; rotation/translation XY garde ce repère,
    # alors qu’un Z déplacé, incliné ou mis à l’échelle n’est pas encore prouvé ici.
    return bool(np.array_equal(nombres[2], [0.0, 0.0, 1.0, 0.0]))


def _chemin_modele_principal(archive, noms):
    """Résout la même cible OPC que le lecteur 3MF ; membre ZIP ou None si ambigu."""
    if "_rels/.rels" in noms:
        relations = archive.xml("_rels/.rels")
        candidates = [
            relation for relation in relations
            if relation.get("Type", "").endswith("/3dmodel") and relation.get("TargetMode") != "External"
        ]
        if len(candidates) > 1:
            return None
        if candidates:
            cible = unquote(candidates[0].get("Target", "")).replace("\\", "/")
            cible = posixpath.normpath(cible.lstrip("/") if cible.startswith("/") else cible)
            return cible if cible in noms else None
    return next((nom for nom in sorted(noms) if nom.lower() == "3d/3dmodel.model"), None)


def _est_path(element):
    """Reconnaît les extensions p:path même si leur URI de namespace varie."""
    return any(nom == "path" or nom.rsplit("}", 1)[-1] == "path" for nom in element.attrib)


def _valeur_path(element):
    """Renvoie l’unique valeur p:path ; plusieurs extensions path restent ambiguës."""
    valeurs = [
        valeur for nom, valeur in element.attrib.items()
        if nom == "path" or nom.rsplit("}", 1)[-1] == "path"
    ]
    return valeurs[0] if len(valeurs) == 1 else None


def _lire_contexte(source, identifiant):
    """Lit une fois les déclarations XML et les réglages de projet typés, sans écrire."""
    with Archive3MF(source, taille_max_xml=_XML_MAX) as archive:
        noms = archive.noms()
        modele_principal = _chemin_modele_principal(archive, noms)
        if modele_principal is None:
            return None, None, None, "modèle principal absent ou ambigu"
        racine = archive.xml(modele_principal)
        membre_plages = "Metadata/layer_config_ranges.xml"
        plages = archive.xml(membre_plages) if membre_plages in noms else None
        membre_reglages = "Metadata/project_settings.config"
        reglages = archive.reglages(membre_reglages) if membre_reglages in noms else None
        ressources = racine.findall(NS + "resources/" + NS + "object")
        objets_par_id = {}
        for objet in ressources:
            identifiant_objet = objet.get("id")
            if identifiant_objet in objets_par_id:
                return racine, plages, reglages, "identifiant d’objet répété dans le modèle principal"
            objets_par_id[identifiant_objet] = objet
        items = racine.findall(NS + "build/" + NS + "item")
        if len(items) != 1:
            return racine, plages, reglages, "plusieurs instances : liaison objet/plage NON VÉRIFIÉE"
        instance = items[0]
        identifiant_racine = instance.get("objectid")
        if identifiant_racine != identifiant:
            return racine, plages, reglages, "identifiant de plage et géométrie non reliés sans ambiguïté"
        if instance.get("printable", "1") != "1":
            return racine, plages, reglages, "instance explicitement non imprimable"
        if _est_path(instance):
            return racine, plages, reglages, "instance liée à un autre modèle par p:path : NON VÉRIFIÉ"
        if set(instance.attrib) - {"objectid", "transform", "printable"}:
            return racine, plages, reglages, "attribut d’instance non qualifié"
        if not _matrice_z_inchangee(instance):
            return racine, plages, reglages, "transformation de l’objet modifie ou rend ambigu le repère Z"
        objet_racine = objets_par_id.get(identifiant_racine)
        if objet_racine is None:
            return racine, plages, reglages, "objet d’instance absent du modèle principal"
        composants = objet_racine.findall(NS + "components/" + NS + "component")
        if objet_racine.find(NS + "mesh") is not None and not composants:
            objets_mailles = [obj for obj in ressources if obj.find(NS + "mesh") is not None]
            if len(objets_mailles) != 1 or objets_mailles[0] is not objet_racine:
                return racine, plages, reglages, "plusieurs corps : liaison objet/plage NON VÉRIFIÉE"
            return racine, plages, reglages, None
        # Le producteur BENCHAB met les maillages dans pieces.model et lie chaque
        # plaque par un seul component (docs/reference/integrite-3mf.md).
        if objet_racine.find(NS + "mesh") is not None or len(composants) != 1:
            return racine, plages, reglages, "assemblage ou liaison objet/plage non qualifiée"
        composant = composants[0]
        noms_path = {
            nom for nom in composant.attrib if nom.endswith("}path")
        }
        if len(noms_path) > 1 or set(composant.attrib) - {"objectid", "transform"} - noms_path:
            return racine, plages, reglages, "attribut de composant non qualifié"
        if not _matrice_z_inchangee(composant):
            return racine, plages, reglages, "transformation de composant modifie ou rend ambigu le repère Z"
        identifiant_composant = composant.get("objectid")
        if not identifiant_composant:
            return racine, plages, reglages, "identifiant de composant absent"
        chemin_composant = _valeur_path(composant)
        if _est_path(composant) and chemin_composant is None:
            return racine, plages, reglages, "chemin p:path ambigu"
        if chemin_composant is None:
            modele_composant, objets_composants = racine, objets_par_id
        else:
            chemin = unquote(chemin_composant).replace("\\", "/")
            chemin = posixpath.normpath(chemin.lstrip("/") if chemin.startswith("/") else (
                posixpath.join(posixpath.dirname(modele_principal), chemin)
            ))
            if chemin.startswith("../") or chemin not in noms:
                return racine, plages, reglages, "chemin de composant absent ou hors archive"
            modele_composant = archive.xml(chemin)
            if modele_composant.get("unit", "millimeter") != racine.get("unit", "millimeter"):
                return racine, plages, reglages, "unités différentes entre modèles : NON VÉRIFIÉ"
            objets_composants = {}
            for objet in modele_composant.findall(NS + "resources/" + NS + "object"):
                cle = objet.get("id")
                if cle in objets_composants:
                    return racine, plages, reglages, "identifiant d’objet composant répété"
                objets_composants[cle] = objet
        cible = objets_composants.get(identifiant_composant)
        maillages = [obj for obj in objets_composants.values() if obj.find(NS + "mesh") is not None]
        if cible is None or cible.find(NS + "mesh") is None or len(maillages) != 1:
            return racine, plages, reglages, "liaison du composant vers un maillage unique non vérifiée"
        if cible.findall(NS + "components/" + NS + "component"):
            return racine, plages, reglages, "composants imbriqués non qualifiés"
        return racine, plages, reglages, None


def _lire_geometrie(source):
    """Lit les bornes Z (mm) d’une archive déjà reliée à son instance imprimable."""

    # Le lecteur partagé valide les unités 3MF et applique les transforms d’instance.
    # Dans cette étape, le contrôle ci-dessus garantit que la coordonnée résultante Z
    # reste identique au Z objet auquel les plages sont référencées.
    scene = lire_scene(source)
    geometries = list(scene.geometry.values())
    if len(geometries) != 1 or geometries[0].is_empty:
        return None, "géométrie vide ou non unique"
    bornes = np.asarray(geometries[0].bounds, dtype=float)
    if bornes.shape != (2, 3) or not np.isfinite(bornes).all() or bornes[1, 2] <= bornes[0, 2]:
        return None, "bornes géométriques invalides"
    return [float(bornes[0, 2]), float(bornes[1, 2])], None


def _lire_plages(racine, identifiant):
    """Lit les intervalles layer_height en mm et refuse les rangées XML mal formées."""
    if racine is None:
        return []
    if racine.tag != "objects":
        raise ValueError("Racine de plages Z inconnue.")
    objets = list(racine)
    if any(obj.tag != "object" or obj.get("id") != identifiant for obj in objets) or len(objets) > 1:
        raise ValueError("Objets de plages inconnus, liés ou dupliqués.")
    intervalles = []
    if not objets:
        return intervalles
    objet = objets[0]
    if set(objet.attrib) != {"id"}:
        raise ValueError("Attribut d’objet de plage inconnu.")
    for element in objet:
        if element.tag != "range" or set(element.attrib) != {"min_z", "max_z"}:
            raise ValueError("Élément de plage Z inconnu.")
        try:
            debut = _nombre_type(element.get("min_z"), "@min_z", "plage_couches", "min_z")
            fin = _nombre_type(element.get("max_z"), "@max_z", "plage_couches", "max_z")
            options = [
                option
                for option in element
                if option.tag == "option" and option.get("opt_key") == "layer_height"
            ]
            if any(
                option.tag != "option" or set(option.attrib) != {"opt_key"}
                for option in element
            ) or len(options) != 1 or list(options[0]):
                raise ValueError("Option de hauteur de couche absente ou ambiguë.")
            hauteur = _nombre_type(options[0].text, "layer_height", "reglage", "layer_height")
        except (TypeError, AttributeError) as erreur:
            raise ValueError("Plage Z incomplète ou mal formée.") from erreur
        if fin <= debut or hauteur <= 0:
            raise ValueError("Plage Z ou hauteur de couche non positive.")
        intervalles.append((debut, fin, hauteur))
    resultat = sorted(intervalles)
    if any(courant[0] < precedent[1] - 1e-9 for precedent, courant in zip(resultat, resultat[1:])):
        raise ValueError("Plages Z chevauchantes : interprétation ambiguë.")
    return resultat


def _couvre(intervalles, debut, fin):
    """Vérifie une couverture contiguë de l’intervalle en mm, sans combler les trous."""
    curseur = debut
    # Une frontière seule n'apporte ni couverture ni hauteur dans la zone (docs/reference/plages-z.md).
    morceaux = [
        (max(debut, gauche), min(fin, droite), hauteur)
        for gauche, droite, hauteur in intervalles
        if droite > debut and gauche < fin
    ]
    for gauche, droite, _ in morceaux:
        if gauche > curseur:
            return False, morceaux
        curseur = max(curseur, droite)
    return bool(morceaux) and curseur >= fin, morceaux


def _evaluer_intention(item, geometrie, intervalles, tolerance):
    """Compare une déclaration au domaine géométrique et aux intervalles explicites en mm."""
    identifiant = item["objet_id"]
    intention = item["type"]
    if geometrie is None:
        return {"objet_id": identifiant, "intention": intention, "statut": "NON VÉRIFIÉ"}
    gmin, gmax = geometrie
    for debut, fin, _ in intervalles:
        if debut < gmin - tolerance or fin > gmax + tolerance:
            return {
                "objet_id": identifiant, "intention": intention, "statut": "NON VÉRIFIÉ",
                "erreur": "plage hors du domaine Z géométrique",
            }
    if intention == "totale":
        debut, fin = gmin, gmax
        seuil = None
    else:
        debut = _nombre_fini(item.get("min_z_mm"), "min_z_mm")
        fin = _nombre_fini(item.get("max_z_mm"), "max_z_mm")
        if not gmin - tolerance <= debut < fin <= gmax + tolerance:
            return {
                "objet_id": identifiant, "intention": intention, "statut": "NON VÉRIFIÉ",
                "erreur": "intervalle déclaré hors du domaine Z géométrique",
            }
        if intention == "partielle_volontaire" and (
            debut <= gmin + tolerance and fin >= gmax - tolerance
        ):
            return {
                "objet_id": identifiant, "intention": intention, "statut": "NON VÉRIFIÉ",
                "erreur": "l’intention partielle doit nommer un intervalle intérieur au modèle",
            }
        seuil = _nombre_fini(item.get("hauteur_max_mm"), "hauteur_max_mm") if intention == "zone_fine" else None
        if seuil is not None and seuil <= 0:
            raise ValueError("hauteur_max_mm doit être strictement positive.")

    couverte, morceaux = _couvre(intervalles, debut, fin)
    if not couverte:
        statut = "FAIL"
        erreur = "couverture explicite incomplète de l’intervalle demandé"
    elif seuil is not None and any(hauteur > seuil for _, _, hauteur in morceaux):
        statut = "FAIL"
        erreur = "zone déclarée fine couverte par une hauteur au-delà du seuil"
    else:
        statut, erreur = "PASS", None
    resultat = {
        "objet_id": identifiant,
        "intention": intention,
        "statut": statut,
        "geometrie_z_mm": [round(gmin, 6), round(gmax, 6)],
        "intervalle_attendu_mm": [round(debut, 6), round(fin, 6)],
        "couverture_explicite_mm": [[round(a, 6), round(b, 6)] for a, b, _ in intervalles],
        "repere": "Z du modèle en mm ; seules les transformations conservant Z sont qualifiées",
    }
    if seuil is not None:
        resultat["hauteur_max_mm"] = seuil
    if erreur:
        resultat["erreur"] = erreur
    return resultat


def analyser(source, declaration, *, tolerance_mm=0.001):
    """Contrôle une intention Z en mm ; PASS/FAIL/NON VÉRIFIÉ, sans modifier l’archive."""
    source = sans_lien(Path(source), strict=True)
    tolerance = _nombre_fini(tolerance_mm, "tolérance_mm")
    if tolerance < 0 or tolerance > 0.1:
        raise ValueError("tolérance_mm doit être comprise entre 0 et 0,1 mm.")
    intentions = _intentions(declaration)
    # Vérifier l’index et le budget avant le SHA, pour ne jamais lire sans borne une archive refusée.
    with Archive3MF(source, taille_max_xml=_XML_MAX) as archive:
        archive.noms()
    empreinte_avant = sha256_fichier(source)
    objets = []
    for item in intentions:
        try:
            _, plages_xml, reglages_projet, erreur = _lire_contexte(source, item["objet_id"])
            if erreur:
                objets.append({
                    "objet_id": item["objet_id"], "intention": item["type"],
                    "statut": "NON VÉRIFIÉ", "erreur": erreur,
                })
                continue
            geometrie, erreur = _lire_geometrie(source)
            if erreur:
                objets.append({
                    "objet_id": item["objet_id"], "intention": item["type"],
                    "statut": "NON VÉRIFIÉ", "erreur": erreur,
                })
                continue
            intervalles = _lire_plages(plages_xml, item["objet_id"])
            resultat = _evaluer_intention(item, geometrie, intervalles, tolerance)
            if reglages_projet is None:
                resultat["hauteur_base_projet"] = {"statut": "NON VÉRIFIÉ", "valeur_mm": None}
            else:
                hauteur = reglages_projet.get("layer_height")
                try:
                    valeur = _nombre_type(str(hauteur), "layer_height", "reglage", "hauteur de base projet")
                    if valeur <= 0:
                        raise ValueError("Hauteur de base projet non positive.")
                    resultat["hauteur_base_projet"] = {"statut": "PASS", "valeur_mm": valeur}
                except (TypeError, ValueError):
                    resultat["hauteur_base_projet"] = {"statut": "NON VÉRIFIÉ", "valeur_mm": None}
            objets.append(resultat)
        except (OSError, ValueError, KeyError, TypeError, ElementTree.ParseError, BadZipFile) as erreur:
            objets.append({
                "objet_id": item["objet_id"], "intention": item["type"],
                "statut": "NON VÉRIFIÉ", "erreur": str(erreur),
            })
    empreinte_apres = sha256_fichier(source)
    if empreinte_apres != empreinte_avant:
        raise ValueError("Archive source modifiée pendant le contrôle.")
    statuts = {item["statut"] for item in objets}
    statut = "FAIL" if "FAIL" in statuts else "NON VÉRIFIÉ" if "NON VÉRIFIÉ" in statuts else "PASS"
    rapport = {
        "schema": 1,
        "statut": statut,
        "source_sha256": empreinte_avant,
        "unite": "mm",
        "tolerance_mm": tolerance,
        "validation_physique": False,
        "modifie_source": False,
        "objets": objets,
        "limites": [
            "Un objet, un corps et une instance imprimable par contrôle ; les assemblages sont NON VÉRIFIÉS.",
            "Les transforms avec déplacement, inclinaison ou échelle Z sont NON VÉRIFIÉS.",
            "Contrôle de couverture des plages explicitement enregistrées, pas de hauteur réelle de chaque couche.",
        ],
    }
    rapport["code"] = code_statut(statut)
    return rapport


def main(argv=None):
    """Interface de contrôle Z en mm et en lecture seule ; code 0 PASS, 2 FAIL ou NON VÉRIFIÉ."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Archive 3MF source, préservée")
    parser.add_argument("intention", type=Path, help="JSON d’intention explicite, unités mm")
    parser.add_argument("--tolerance-mm", type=float, default=0.001)
    arguments = parser.parse_args(argv)
    try:
        rapport = analyser(arguments.source, arguments.intention, tolerance_mm=arguments.tolerance_mm)
    except (OSError, ValueError, KeyError, TypeError, BadZipFile, ElementTree.ParseError) as erreur:
        rapport = {
            "schema": 1, "statut": "NON VÉRIFIÉ", "unite": "mm",
            "erreur": str(erreur), "validation_physique": False,
            "code": code_statut("NON VÉRIFIÉ"),
        }
    print(json.dumps(rapport, ensure_ascii=False, indent=2, allow_nan=False))
    return code_statut(rapport["statut"])


if __name__ == "__main__":
    raise SystemExit(main())
