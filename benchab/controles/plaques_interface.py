"""Vérifie les objets 3MF sur les pas d'interface et de ligne de commande."""

import argparse
import math
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np

from benchab.archives_3mf import Archive3MF
from benchab.controles.rapports import RapportCourant
from benchab.machine.configuration import charger as charger_configuration
from benchab.qualite.statuts import code_statut


def chemin(value):
    """Convertit une valeur en chemin absolu et harmonise l’alias macOS /var, sans lire le fichier."""
    path = Path(value).absolute()
    if sys.platform == "darwin" and path.is_relative_to("/var") and Path("/var").resolve() == Path("/private/var"):
        return Path("/private/var") / path.relative_to("/var")
    return path


def mat(texte):
    """Lit une transformation 3MF, translations en mm ; un attribut absent signifie identité."""
    if texte is None:
        return np.eye(4)
    valeurs = [float(valeur) for valeur in texte.split()]
    if len(valeurs) != 12 or not np.isfinite(valeurs).all():
        raise ValueError("Transformation 3MF : douze valeurs finies requises")
    matrice = np.eye(4)
    matrice[:3, :3] = np.array(valeurs[:9]).reshape(3, 3).T
    matrice[:3, 3] = valeurs[9:12]
    return matrice


def points(key, oid, matrice, meshes, composants):
    """Retourne les sommets transformés d’un objet en coordonnées XYZ mm, en suivant ses composants déclarés."""
    identite = (key, oid)
    if identite in meshes:
        sommets = meshes[identite]
        return (np.c_[sommets, np.ones(len(sommets))] @ matrice.T)[:, :3]
    enfants = composants.get(identite)
    if not enfants:
        raise ValueError(f"objet 3MF absent ou sans sommets : {key}#{oid}")
    return np.vstack(
        [points(path, objet, matrice @ transform, meshes, composants) for path, objet, transform in enfants]
    )


def analyser(fichier, *, pas=None, plateau=None, marge=None):
    """Renvoie les limites de chaque objet sans modifier le 3MF."""
    configuration = charger_configuration()
    placement = configuration.get("placement", {})
    pas = pas or (placement.get("pas_interface_mm"), placement.get("pas_cli_mm"))
    plateau = plateau or placement["plateau_xy_mm"]
    marge = placement["marge_objet_mm"] if marge is None else marge
    if len(pas) != 2 or any(
        not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0 for value in pas
    ):
        raise ValueError("Deux pas de plaque finis et positifs sont requis")
    if len(plateau) != 2 or any(
        not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0 for value in plateau
    ):
        raise ValueError("Dimensions de plateau XY finies et positives requises")
    if not isinstance(marge, (int, float)) or not math.isfinite(marge) or marge < 0:
        raise ValueError("Marge d'objet finie et positive ou nulle requise")
    fichier = chemin(fichier)
    try:
        with Archive3MF(fichier) as archive:
            config = archive.xml("Metadata/model_settings.config")
            modeles = {"/" + nom: archive.xml(nom)
                       for nom in archive.noms() if nom.endswith(".model")}
    except ET.ParseError as erreur:
        statut = "NON VÉRIFIÉ"
        return {
            "fichier": fichier.name, "plaques": [], "conforme": False,
            "statut": statut, "code": code_statut(statut), "motif": f"XML 3MF invalide : {erreur}",
        }
    noms = {
        objet.attrib["id"]: meta.get("value")
        for objet in config.findall("{*}object")
        for meta in objet.findall("{*}metadata[@key='name']")
    }
    meshes = {}
    composants = {}
    for cle, racine in modeles.items():
        for objet in racine.findall("{*}resources/{*}object"):
            oid = objet.attrib["id"]
            sommets = objet.findall("{*}mesh/{*}vertices/{*}vertex")
            if sommets:
                valeurs = np.array([[s.attrib[a] for a in ("x", "y", "z")] for s in sommets], dtype=float)
                if not np.isfinite(valeurs).all():
                    raise ValueError("Coordonnées 3MF non finies")
                meshes[(cle, oid)] = valeurs
            refs = objet.findall("{*}components/{*}component")
            if refs:
                composants[(cle, oid)] = [
                    (
                        next((v for k, v in ref.attrib.items() if k == "path" or k.endswith("}path")), cle),
                        ref.attrib["objectid"],
                        mat(ref.get("transform")),
                    )
                    for ref in refs
                ]
    modele = modeles["/3D/3dmodel.model"]
    # Le Core 3MF donne l’identité aux items sans transform ; l’ordre XML
    # des attributs n’a aucune signification (docs/reference/regles.md, U20).
    items = {}
    for item in modele.findall("{*}build/{*}item"):
        items.setdefault(item.attrib["objectid"], []).append(mat(item.get("transform")))
    plaques_xml = config.findall("{*}plate")
    colonnes = math.ceil(math.sqrt(len(plaques_xml)))
    rapports = []
    for index, plaque_xml in enumerate(plaques_xml):
        liaisons = []
        for instance in plaque_xml.findall("{*}model_instance") or [plaque_xml]:
            indice = instance.find("{*}metadata[@key='instance_id']")
            rang = int(indice.attrib["value"]) if indice is not None else 0
            liaisons.extend((m.attrib["value"], rang) for m in instance.findall("{*}metadata[@key='object_id']"))
        col, row = index % colonnes, index // colonnes
        resultat = {"plaque": index + 1, "objets": len(liaisons)}
        for step in pas:
            hors_plateau = []
            for oid, instance in liaisons:
                if oid not in items or not 0 <= instance < len(items[oid]):
                    raise ValueError(f"instance {instance} absente pour objet {oid}")
                sommets = points("/3D/3dmodel.model", oid, items[oid][instance], meshes, composants)
                if not len(sommets) or not np.isfinite(sommets).all():
                    raise ValueError(f"coordonnées invalides pour objet {oid}")
                bas, haut = sommets.min(0), sommets.max(0)
                x0, y0 = bas[0] - col * step, bas[1] + row * step
                x1, y1 = haut[0] - col * step, haut[1] + row * step
                if x0 < marge or y0 < marge or x1 > plateau[0] - marge or y1 > plateau[1] - marge:
                    hors_plateau.append((noms.get(oid), round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)))
            resultat[f"hors_plateau_pas_{step:g}"] = hors_plateau
        rapports.append(resultat)
    conforme = bool(rapports) and all(
        plaque["objets"] > 0 and all(not values for key, values in plaque.items() if key.startswith("hors"))
        for plaque in rapports
    )
    statut = "PASS" if conforme else "FAIL"
    return {
        "fichier": fichier.name, "plaques": rapports, "conforme": conforme,
        "statut": statut, "code": code_statut(statut),
    }


def main(argv=None):
    """Écrit le rapport U20 en mm et retourne son code, sans modifier le 3MF."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("rapport", type=Path)
    args = parser.parse_args(argv)
    try:
        source, destination = chemin(args.source), chemin(args.rapport)
        rapport = RapportCourant(destination, [source], "plaques_interface")
        resultat = analyser(source)
        rapport.publier(resultat)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print(f"Contrôle refusé : {error}", file=sys.stderr)
        return 2
    for plaque in resultat["plaques"]:
        print(plaque)
    if resultat.get("motif"):
        print(resultat["statut"], resultat["motif"])
    print("conforme", resultat["conforme"])
    return resultat["code"]


if __name__ == "__main__":
    raise SystemExit(main())
