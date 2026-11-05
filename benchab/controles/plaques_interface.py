"""Vérifie les objets 3MF sur les pas d'interface et de ligne de commande."""

import argparse
import math
import re
import sys
import zipfile
from pathlib import Path

import numpy as np

from benchab.controles.rapports import RapportCourant
from benchab.machine.configuration import charger as charger_configuration


def chemin(value):
    path = Path(value).absolute()
    if sys.platform == "darwin" and path.is_relative_to("/var") and Path("/var").resolve() == Path("/private/var"):
        return Path("/private/var") / path.relative_to("/var")
    return path


def mat(texte):
    valeurs = [float(valeur) for valeur in texte.split()]
    if len(valeurs) != 12 or not np.isfinite(valeurs).all():
        raise ValueError("Transformation 3MF : douze valeurs finies requises")
    matrice = np.eye(4)
    matrice[:3, :3] = np.array(valeurs[:9]).reshape(3, 3).T
    matrice[:3, 3] = valeurs[9:12]
    return matrice


def points(key, oid, matrice, meshes, composants):
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
    with zipfile.ZipFile(fichier) as archive:
        modele = archive.read("3D/3dmodel.model").decode()
        config = archive.read("Metadata/model_settings.config").decode()
    noms = dict(re.findall(r'<object id="(\d+)">\s*<metadata key="name" value="([^"]+)"', config))
    meshes = {}
    composants = {}
    # Le parcours reste borné aux membres .model de l'archive demandée.
    with zipfile.ZipFile(fichier) as archive:
        membres = [name for name in archive.namelist() if name.endswith(".model")]
        for name in membres:
            texte = archive.read(name).decode()
            key = "/" + name
            for oid, body in re.findall(r'<object id="(\d+)"[^>]*>(.*?)</object>', texte, re.S):
                sommets = re.findall(r'<vertex x="([^"]+)" y="([^"]+)" z="([^"]+)"', body)
                if sommets:
                    valeurs = np.array(sommets, float)
                    if not np.isfinite(valeurs).all():
                        raise ValueError("Coordonnées 3MF non finies")
                    meshes[(key, oid)] = valeurs
                refs = re.findall(r"<component ([^>]*)/>", body)
                if refs:
                    composants[(key, oid)] = [
                        (
                            re.search(r'p:path="([^"]+)"', ref).group(1) if "p:path" in ref else key,
                            re.search(r'objectid="(\d+)"', ref).group(1),
                            mat(re.search(r'transform="([^"]+)"', ref).group(1)) if "transform" in ref else np.eye(4),
                        )
                        for ref in refs
                    ]
    items = {
        oid: mat(transform) for oid, transform in re.findall(r'<item objectid="(\d+)"[^>]*?transform="([^"]+)"', modele)
    }
    plaques_xml = re.findall(r"<plate>(.*?)</plate>", config, re.S)
    colonnes = math.ceil(math.sqrt(len(plaques_xml)))
    rapports = []
    for index, plaque_xml in enumerate(plaques_xml):
        oids = re.findall(r'key="object_id" value="(\d+)"', plaque_xml)
        col, row = index % colonnes, index // colonnes
        resultat = {"plaque": index + 1, "objets": len(oids)}
        for step in pas:
            hors_plateau = []
            for oid in oids:
                if oid not in items:
                    raise ValueError(f"transformation absente pour objet {oid}")
                sommets = points("/3D/3dmodel.model", oid, items[oid], meshes, composants)
                if not len(sommets) or not np.isfinite(sommets).all():
                    raise ValueError(f"coordonnées invalides pour objet {oid}")
                bas, haut = sommets.min(0), sommets.max(0)
                x0, y0 = bas[0] - col * step, bas[1] + row * step
                x1, y1 = haut[0] - col * step, haut[1] + row * step
                if x0 < marge or y0 < marge or x1 > plateau[0] - marge or y1 > plateau[1] - marge:
                    hors_plateau.append((noms.get(oid), round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)))
            resultat[f"hors_plateau_pas_{step:g}"] = hors_plateau
        rapports.append(resultat)
    return {
        "fichier": fichier.name,
        "plaques": rapports,
        "conforme": bool(rapports)
        and all(
            plaque["objets"] > 0 and all(not values for key, values in plaque.items() if key.startswith("hors"))
            for plaque in rapports
        ),
    }


def main(argv=None):
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
    print("conforme", resultat["conforme"])
    return 0 if resultat["conforme"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
