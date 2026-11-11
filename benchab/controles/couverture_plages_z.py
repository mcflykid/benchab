"""Contrôle les plages Z d’un 3MF contre une intention explicite, sans écriture."""

import argparse
import json
import math
from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile

import numpy as np

from benchab import reglages as schema_reglages
from benchab.archives_3mf import Archive3MF
from benchab.chemins import sans_lien
from benchab.imports3d.lecture_3mf import NS
from benchab.imports3d.lecture_3mf import lire as lire_scene
from benchab.qualite.empreintes import sha256_fichier

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
        contenu = Path(source).read_text(encoding="utf-8")
        declaration = json.loads(
            contenu,
            object_pairs_hook=_json_sans_doublons,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Constante JSON interdite.")),
        )
    else:
        declaration = source
    if not isinstance(declaration, dict) or declaration.get("schema") != 1:
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
    return bool(np.allclose(nombres[2], [0.0, 0.0, 1.0, 0.0], atol=1e-9, rtol=0.0))


def _lire_geometrie(source, identifiant):
    """Relie un seul objet 3MF directement imprimable à ses bornes Z en mm."""
    with Archive3MF(source, taille_max_xml=_XML_MAX) as archive:
        noms = archive.noms()
        modeles = [nom for nom in noms if nom.lower() == "3d/3dmodel.model"]
        if len(modeles) != 1:
            return None, "modèle principal absent ou ambigu"
        racine = archive.xml(modeles[0])
        ressources = racine.findall(NS + "resources/" + NS + "object")
        objets_mailles = [obj for obj in ressources if obj.find(NS + "mesh") is not None]
        items = racine.findall(NS + "build/" + NS + "item")
        if len(objets_mailles) != 1 or len(items) != 1:
            return None, "plusieurs corps ou instances : liaison objet/plage NON VÉRIFIÉE"
        obj, instance = objets_mailles[0], items[0]
        if obj.get("id") != identifiant or instance.get("objectid") != identifiant:
            return None, "identifiant de plage et géométrie non reliés sans ambiguïté"
        if obj.findall(NS + "components/" + NS + "component"):
            return None, "objet composé : repère des sous-composants NON VÉRIFIÉ"
        if not _matrice_z_inchangee(instance):
            return None, "transformation de l’objet modifie ou rend ambigu le repère Z"

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


def _lire_plages(source, identifiant):
    """Lit les intervalles layer_height en mm et refuse les rangées XML mal formées."""
    with Archive3MF(source, taille_max_xml=_XML_MAX) as archive:
        noms = archive.noms()
        membre = "Metadata/layer_config_ranges.xml"
        if membre not in noms:
            return []
        racine = archive.xml(membre)
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


def _couvre(intervalles, debut, fin, tolerance):
    """Vérifie une couverture contiguë de l’intervalle en mm, sans combler les trous."""
    curseur = debut
    morceaux = []
    for gauche, droite, hauteur in intervalles:
        if droite < debut - tolerance or gauche > fin + tolerance:
            continue
        morceaux.append([max(debut, gauche), min(fin, droite), hauteur])
        if gauche > curseur + tolerance:
            return False, morceaux
        curseur = max(curseur, droite)
        if curseur >= fin - tolerance:
            return True, morceaux
    return curseur >= fin - tolerance, morceaux


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

    couverte, morceaux = _couvre(intervalles, debut, fin, tolerance)
    if not couverte:
        statut = "FAIL"
        erreur = "couverture explicite incomplète de l’intervalle demandé"
    elif seuil is not None and any(hauteur > seuil + tolerance for _, _, hauteur in morceaux):
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
    with Archive3MF(source) as archive:
        archive.noms()
    empreinte_avant = sha256_fichier(source)
    objets = []
    for item in intentions:
        try:
            geometrie, erreur = _lire_geometrie(source, item["objet_id"])
            if erreur:
                objets.append({
                    "objet_id": item["objet_id"], "intention": item["type"],
                    "statut": "NON VÉRIFIÉ", "erreur": erreur,
                })
                continue
            intervalles = _lire_plages(source, item["objet_id"])
            objets.append(_evaluer_intention(item, geometrie, intervalles, tolerance))
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
    return {
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


def main(argv=None):
    """Interface de contrôle Z en lecture seule ; code 0 PASS, 1 FAIL, 2 NON VÉRIFIÉ."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Archive 3MF source, préservée")
    parser.add_argument("intention", type=Path, help="JSON d’intention explicite, unités mm")
    parser.add_argument("--tolerance-mm", type=float, default=0.001)
    arguments = parser.parse_args(argv)
    try:
        rapport = analyser(arguments.source, arguments.intention, tolerance_mm=arguments.tolerance_mm)
    except (OSError, ValueError, KeyError, TypeError) as erreur:
        rapport = {
            "schema": 1, "statut": "NON VÉRIFIÉ", "unite": "mm",
            "erreur": str(erreur), "validation_physique": False,
        }
    print(json.dumps(rapport, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if rapport["statut"] == "PASS" else 1 if rapport["statut"] == "FAIL" else 2


if __name__ == "__main__":
    raise SystemExit(main())
