"""Vérifie les objets 3MF sur les pas d'interface et de ligne de commande."""

import argparse
import json
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


class _FluxXMLSansDeclarations:
    """Filtre les déclarations XML interdites dans un flux de modèle 3MF."""

    def __init__(self, flux):
        """Entrée : flux binaire séquentiel ; sortie : filtre avec recouvrement de 16 octets."""
        self.flux = flux
        self.recouvrement = b""

    def read(self, taille=-1):
        """Entrée : taille demandée en octets ; sortie : octets filtrés ou ValueError si DTD/ENTITY."""
        bloc = self.flux.read(taille)
        controle = (self.recouvrement + bloc).upper()
        if b"<!DOCTYPE" in controle or b"<!ENTITY" in controle:
            raise ValueError("déclaration DTD/ENTITY interdite dans le modèle 3MF")
        self.recouvrement = controle[-16:]
        return bloc


def _lire_modele_flux(archive, nom, *, extraire_build=False):
    """Lit les liens 3MF et sommets XYZ en mm, sans conserver les facettes ni modifier l’archive.

    Entrées : archive ouverte, nom de membre et option build ; sortie : objets et liens de build.
    Les triangles sont ignorés pour limiter la mémoire, sans changer le calcul des bornes U20.
    """
    objets = {}
    items = []
    racine_objet = None
    identifiant = None
    sommets = []
    composants = []
    pile = []

    def local(element):
        """Renvoie le nom XML local, indépendamment de l'espace de noms."""
        return element.tag.rsplit("}", 1)[-1]

    with archive.ouvrir_flux(nom) as flux:
        parser = ET.iterparse(_FluxXMLSansDeclarations(flux), events=("start", "end"))
        for evenement, element in parser:
            tag = local(element)
            if evenement == "start" and tag == "object":
                racine_objet = element
                identifiant = element.attrib["id"]
                sommets, composants = [], []
            if evenement == "start":
                pile.append(element)
            elif evenement == "end" and tag == "vertex" and racine_objet is not None:
                sommets.append([element.attrib[axe] for axe in ("x", "y", "z")])
            elif evenement == "end" and tag == "component" and racine_objet is not None:
                cle = next((v for k, v in element.attrib.items() if k == "path" or k.endswith("}path")), "/" + nom)
                composants.append((cle, element.attrib["objectid"], mat(element.get("transform"))))
            elif evenement == "end" and tag == "object" and racine_objet is element:
                if sommets:
                    valeurs = np.asarray(sommets, dtype=float)
                    if not np.isfinite(valeurs).all():
                        raise ValueError("Coordonnées 3MF non finies")
                    objets[identifiant] = ("mesh", valeurs)
                elif composants:
                    objets[identifiant] = ("components", composants)
                else:
                    objets[identifiant] = ("empty", None)
                racine_objet, identifiant, sommets, composants = None, None, [], []
            elif evenement == "end" and tag == "item" and extraire_build:
                items.append((element.attrib["objectid"], mat(element.get("transform"))))
            if evenement == "end":
                # Retirer les enfants terminés évite que les triangles vides restent
                # attachés à la racine pendant un grand XML exporté par un slicer.
                if len(pile) > 1:
                    pile[-2].remove(element)
                element.clear()
                pile.pop()
    return objets, items


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
    """Renvoie les limites XYZ en mm et un statut PASS/FAIL sans modifier le 3MF."""
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
        with Archive3MF(fichier, taille_max_xml=512 * 1024 * 1024) as archive:
            if archive.taille_membre("Metadata/model_settings.config") > Archive3MF.MAX_XML_OCTETS:
                raise ValueError("budget XML dépassé : Metadata/model_settings.config")
            config = archive.xml("Metadata/model_settings.config")
            noms_modeles = [nom for nom in archive.noms() if nom.endswith(".model")]
            taille_modeles = sum(archive.taille_membre(nom) for nom in noms_modeles)
            if taille_modeles > 512 * 1024 * 1024:
                raise ValueError("budget XML cumulé des modèles dépassé")
            modeles = {}
            meshes, composants = {}, {}
            items = {}
            for nom in noms_modeles:
                cle = "/" + nom
                objets, liens = _lire_modele_flux(
                    archive, nom, extraire_build=nom == "3D/3dmodel.model"
                )
                modeles[cle] = objets
                for oid, (type_objet, contenu) in objets.items():
                    if type_objet == "mesh":
                        meshes[(cle, oid)] = contenu
                    elif type_objet == "components":
                        composants[(cle, oid)] = contenu
                if nom == "3D/3dmodel.model":
                    for oid, transformation in liens:
                        items.setdefault(oid, []).append(transformation)
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
    if "/3D/3dmodel.model" not in modeles:
        raise ValueError("modèle principal 3MF absent")
    # Le Core 3MF donne l’identité aux items sans transform (docs/reference/regles.md, U20).
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
    """Écrit le rapport U20 en mm et peut afficher les écarts seuls.

    Renvoie le code du verdict (PASS 0, FAIL ou NON VÉRIFIÉ 2).
    Le 3MF reste intact.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("rapport", type=Path)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    try:
        source, destination = chemin(args.source), chemin(args.rapport)
        rapport = RapportCourant(destination, [source], "plaques_interface")
        resultat = analyser(source)
        rapport.publier(resultat)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print(f"Contrôle refusé : {error}", file=sys.stderr)
        return 2
    if args.resume:
        # Les bornes hors plateau identifient la plaque et l’objet à déplacer (#35).
        alertes = [p for p in resultat["plaques"] if not p["objets"] or
                   any(v for k, v in p.items() if k.startswith("hors_plateau"))]
        court = {"statut": resultat["statut"], "code": resultat["code"], "plaques": len(resultat["plaques"]),
                 "alertes": alertes, "rapport": str(destination)}
        if resultat.get("motif"):
            court["motif"] = resultat["motif"]
        elif not resultat["plaques"]:
            court["motif"] = "Aucune plaque."
        print(json.dumps(court, ensure_ascii=False))
        return resultat["code"]
    for plaque in resultat["plaques"]:
        print(plaque)
    if resultat.get("motif"):
        print(resultat["statut"], resultat["motif"])
    print("conforme", resultat["conforme"])
    return resultat["code"]


if __name__ == "__main__":
    raise SystemExit(main())
