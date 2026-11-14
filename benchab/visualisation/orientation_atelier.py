"""Prépare une autre pose dans une copie 3MF, en conservant les ressources d’origine."""

import re
import shutil
from hashlib import file_digest
from zipfile import ZipFile

import numpy as np

from benchab.archives_3mf import Archive3MF
from benchab.fabrication import anycubic
from benchab.imports3d.lecture_3mf import NS, lire
from benchab.machine.configuration import charger


def copier_pose(source, cible, maillage, identite, proposition, *, plaque=1, plaques=1, voisines=()):
    """Change une instance en mm et relit toutes les géométries, sans modifier la source."""
    membre = identite["modele_principal"]
    if identite["unite_source"] != "millimeter" or not proposition:
        raise ValueError("La pose alternative requiert une géométrie identifiée en millimètres.")
    if identite["transformations"][0].get("modele_cible") != membre:
        raise ValueError("Instance directe du modèle principal requise.")
    deplacement = np.asarray(proposition["transformation_mm"], dtype=float).copy()
    attendu = maillage.copy().apply_transform(deplacement)
    # Conserver le centre de l’empreinte XY autant que l’encombrement le permet.
    placement = charger()["placement"]
    colonnes = anycubic.colonnes(plaques)
    colonne, ligne = (plaque - 1) % colonnes, (plaque - 1) // colonnes
    origines = np.array([
        [colonne * float(placement[k]), -ligne * float(placement[k])]
        for k in ("pas_cli_mm", "pas_interface_mm")
    ])
    # L’intersection couvre les deux pas observés sans deviner lequel a produit le 3MF.
    minimum = origines.max(axis=0) + np.asarray(placement["zone_min_mm"])
    maximum = origines.min(axis=0) + np.asarray(placement["zone_max_mm"])
    if np.any(maillage.bounds[0, :2] < minimum) or np.any(maillage.bounds[1, :2] > maximum):
        raise ValueError("Position source hors de la zone commune des deux placements connus.")
    if np.any(attendu.extents[:2] > maximum - minimum):
        raise ValueError("La pose dépasse la zone d’étude de la plaque d’origine.")
    centre = maillage.bounds[:, :2].mean(axis=0)
    deplacement[:2, 3] += np.clip(centre - attendu.extents[:2] / 2, minimum, maximum - attendu.extents[:2])
    attendu = maillage.copy().apply_transform(deplacement)
    for voisine in voisines:
        # Une séparation XY par boîtes suffit à prouver que les pièces ne se chevauchent pas.
        if np.all(attendu.bounds[1, :2] + 1 > voisine.bounds[0, :2]) and np.all(
            voisine.bounds[1, :2] + 1 > attendu.bounds[0, :2]
        ):
            raise ValueError("Séparation d’au moins 1 mm avec les autres pièces non établie sur cette plaque.")
    scene_source = lire(source, identites=True)
    rang = identite["build_item"] - 1
    with Archive3MF(source) as archive:
        racine = archive.xml(membre)
        objets = racine.findall(NS + "build/" + NS + "item")
        if not 0 <= rang < len(objets) or racine.get("unit", "millimeter") != "millimeter":
            raise ValueError("Instance en millimètres introuvable.")
        if objets[rang].get("objectid") != identite["objet_id_racine"]:
            raise ValueError("Identifiant de l’instance différent de celui déclaré.")
        ancien = objets[rang].get("transform")
        if ancien is None:
            raise ValueError("Transformation native explicite requise pour la pose.")
        matrice = np.asarray(identite["transformations"][0]["matrice_mm"])
        nouveau = " ".join(format(x, ".16g") for x in (deplacement @ matrice)[:3].T.ravel())
        xml = archive.lire(membre)
        # Remplacer exactement cet attribut préserve les namespaces et extensions de peinture.
        prefixe = rb'(?:[\w.-]+:)?'
        build = re.findall(rb'<' + prefixe + rb'build(?:\s[^<>]*)?>(.*?)</' + prefixe + rb'build\s*>', xml, re.S)
        if len(build) != 1:
            raise ValueError("Bloc de placement 3MF absent ou ambigu.")
        items = list(re.finditer(rb'<' + prefixe + rb'item\s[^<>]*>', build[0]))
        if len(items) != len(objets):
            raise ValueError("Les instances XML ne correspondent pas aux placements relus.")
        balise = items[rang].group()
        motif = rb'(\s+transform=)(["\'])' + re.escape(ancien.encode()) + rb'\2'
        nouvelle_balise, nombre = re.subn(motif, lambda m: m[1] + m[2] + nouveau.encode() + m[2], balise)
        if nombre != 1:
            raise ValueError("Attribut de pose absent ou ambigu dans le membre source.")
        debut, fin = items[rang].span()
        bloc = build[0][:debut] + nouvelle_balise + build[0][fin:]
        if xml.count(build[0]) != 1:
            raise ValueError("Bloc de placement non unique.")
        copie = xml.replace(build[0], bloc, 1)
        empreintes = {}
        with ZipFile(cible, "x") as sortie:
            sortie.comment = archive.commentaire
            for nom in archive.noms():
                info = archive.information(nom)
                if nom == membre:
                    sortie.writestr(info, copie)
                else:
                    with archive.ouvrir_flux(nom) as entree, sortie.open(info, "w") as destination:
                        shutil.copyfileobj(entree, destination)
                    with archive.ouvrir_flux(nom) as entree:
                        empreintes[nom] = file_digest(entree, "sha256").hexdigest()
        archive.verifier_source(source)
        with Archive3MF(cible) as resultat:
            if resultat.noms() != archive.noms() or resultat.lire(membre) != copie:
                raise ValueError("Un autre membre a changé pendant la copie de pose.")
            for nom, empreinte in empreintes.items():
                with resultat.ouvrir_flux(nom) as entree:
                    if file_digest(entree, "sha256").hexdigest() != empreinte:
                        raise ValueError("Un autre membre a changé pendant la copie de pose.")
    scene_cible = lire(cible, identites=True)
    if list(scene_source.geometry) != list(scene_cible.geometry):
        raise ValueError("Le nombre ou l’ordre des maillages a changé.")
    cibles = scene_source.metadata["identites_import"]
    if sum(i["build_item"] == rang + 1 for i in cibles) != 1:
        raise ValueError("L’instance choisie doit contenir un seul maillage imprimable.")
    for (nom, original), i in zip(scene_source.geometry.items(), cibles, strict=True):
        reference = attendu if i["build_item"] == rang + 1 else original
        relu = scene_cible.geometry[nom]
        if not np.array_equal(relu.faces, reference.faces) or not np.allclose(
            relu.vertices, reference.vertices, atol=1e-6, rtol=0,
        ):
            raise ValueError("La géométrie relue ne correspond pas à la pose déclarée.")
    return {"membre": membre, "instance": rang + 1, "plaque": plaque,
            "avant": ancien, "apres": nouveau, "statut": "PASS", "transformation_mm": deplacement.tolist()}
