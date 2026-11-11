"""Génère un placement déclaré depuis une source 3MF, sans lire de G-code."""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from benchab.chemins import sans_lien
from benchab.controles.placement import valider as valider_placement
from benchab.imports3d.lecture_3mf import lire as lire_3mf
from benchab.qualite.empreintes import sha256_fichier
from benchab.qualite.statuts import code_statut

_MAX_SOURCE_OCTETS = 1024 * 1024 * 1024
_MAX_DECLARATION_OCTETS = 1024 * 1024
_SHA = set("0123456789abcdef")


def _paires_uniques(paires):
    """Refuse les clés JSON répétées dans une déclaration de plaque ou d’objet."""
    resultat = {}
    for cle, valeur in paires:
        if cle in resultat:
            raise ValueError(f"Clé de déclaration répétée : {cle}.")
        resultat[cle] = valeur
    return resultat


def _declaration(source):
    """Charge les octets exacts d’une déclaration privée ou encode un dictionnaire de façon stable."""
    if isinstance(source, (str, Path)):
        chemin = sans_lien(Path(source), strict=True)
        if not chemin.is_file() or chemin.stat().st_size > _MAX_DECLARATION_OCTETS:
            raise ValueError("Déclaration absente ou supérieure à 1 Mio.")
        with chemin.open("rb") as fichier:
            donnees = fichier.read(_MAX_DECLARATION_OCTETS + 1)
        if len(donnees) > _MAX_DECLARATION_OCTETS:
            raise ValueError("Déclaration supérieure à 1 Mio.")
        valeur = json.loads(donnees, object_pairs_hook=_paires_uniques)
        return valeur, hashlib.sha256(donnees).hexdigest(), chemin
    donnees = json.dumps(source, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(donnees) > _MAX_DECLARATION_OCTETS:
        raise ValueError("Déclaration supérieure à 1 Mio.")
    return json.loads(donnees, object_pairs_hook=_paires_uniques), hashlib.sha256(donnees).hexdigest(), None


def _valider_declaration(declaration, empreinte_source):
    """Valide le repère et les liens explicites aux identifiants d’instance source."""
    if not isinstance(declaration, dict) or set(declaration) != {
        "schema", "source_sha256", "repere_xy", "plates", "objects"
    }:
        raise ValueError("La déclaration exige exactement schema, source_sha256, repere_xy, plates et objects.")
    if type(declaration["schema"]) is not int or declaration["schema"] != 1:
        raise ValueError("Schéma de déclaration 1 requis.")
    sha = declaration["source_sha256"]
    if not isinstance(sha, str) or len(sha) != 64 or set(sha) - _SHA or sha != empreinte_source:
        raise ValueError("SHA source de la déclaration absent ou différent de la source 3MF.")
    if declaration["repere_xy"] != "plateau":
        raise ValueError("Le repère XY du plateau doit être déclaré explicitement.")
    plaques = declaration["plates"]
    if not isinstance(plaques, list) or not plaques:
        raise ValueError("La déclaration doit nommer au moins une plaque.")
    noms, indices = set(), set()
    for plaque in plaques:
        if not isinstance(plaque, dict) or set(plaque) != {"index", "name"}:
            raise ValueError("Chaque plaque exige exactement index et name.")
        index, nom = plaque["index"], plaque["name"]
        if type(index) is not int or index < 1 or not isinstance(nom, str) or not nom.strip():
            raise ValueError("Index de plaque positif et nom explicite requis.")
        if index in indices or nom in noms:
            raise ValueError("Index et nom de plaque doivent être uniques.")
        indices.add(index)
        noms.add(nom)
    if indices != set(range(1, len(indices) + 1)):
        raise ValueError("Les index de plaque doivent être contigus, en base 1.")
    objets = declaration["objects"]
    if not isinstance(objets, list) or not objets:
        raise ValueError("La déclaration doit affecter explicitement les objets source.")
    identifiants, noms_objets = set(), set()
    for objet in objets:
        if not isinstance(objet, dict) or set(objet) != {"build_item", "plate", "name"}:
            raise ValueError("Chaque objet exige exactement build_item, plate et name.")
        build_item, plaque, nom = objet["build_item"], objet["plate"], objet["name"]
        if type(build_item) is not int or build_item < 1 or type(plaque) is not int or plaque not in indices:
            raise ValueError("Instance source ou plaque invalide.")
        if not isinstance(nom, str) or not nom.strip() or build_item in identifiants or (plaque, nom) in noms_objets:
            raise ValueError("Identifiants d’instance et noms par plaque doivent être uniques.")
        identifiants.add(build_item)
        noms_objets.add((plaque, nom))


def _rapport_nv(raison, empreinte=None, sha_declaration=None):
    """Construit un résultat indéterminé sans fabriquer de plaque ni de coordonnées."""
    statut = "NON VÉRIFIÉ"
    return {
        "schema": 1,
        "statut": statut,
        "code": code_statut(statut),
        "source_sha256": empreinte,
        "declaration_sha256": sha_declaration,
        "repere_xy": None,
        "placement": None,
        "raisons": [str(raison)],
        "validation_physique": False,
        "modifie_source": False,
        "gcode_consulte": False,
        "reglages_consultes": False,
    }


def generer(source, declaration):
    """Génère un contrat placement JSON ; mm, SHA source/déclaration, PASS ou NON VÉRIFIÉ, aucune écriture.

    La déclaration fournit plaques, noms, affectations et repère plateau. La source 3MF fournit
    géométrie, identifiants et transformations ; aucun G-code, réglage ou profil n’est consulté.
    """
    empreinte = None
    sha_declaration = None
    try:
        chemin = sans_lien(Path(source), strict=True)
        if chemin.suffix.lower() != ".3mf":
            raise ValueError("Seules les sources 3MF avec identifiants d’instance sont qualifiées.")
        if not chemin.is_file() or chemin.stat().st_size > _MAX_SOURCE_OCTETS:
            raise ValueError("Source 3MF absente ou supérieure à 1 Gio.")
        empreinte = sha256_fichier(chemin)
        donnees, sha_declaration, chemin_declaration = _declaration(declaration)
        _valider_declaration(donnees, empreinte)
        scene = lire_3mf(chemin, identites=True)
        if scene.metadata.get("modificateurs_non_geometriques"):
            raise ValueError("Des modificateurs 3MF exclus de la géométrie rendent le placement NON VÉRIFIÉ.")
        identites = scene.metadata.get("identites_import")
        if not isinstance(identites, list) or not identites:
            raise ValueError("Identités de build 3MF indisponibles.")
        par_build = {}
        noms_geometrie = list(scene.geometry)
        if len(noms_geometrie) != len(identites):
            raise ValueError("Géométries et identités d’import ne correspondent pas.")
        for index, identite in enumerate(identites):
            if not isinstance(identite, dict) or type(identite.get("build_item")) is not int:
                raise ValueError("Identité d’instance 3MF incomplète.")
            par_build.setdefault(identite["build_item"], []).append((scene.geometry[noms_geometrie[index]], identite))
        affectations = {objet["build_item"] for objet in donnees["objects"]}
        if affectations != set(par_build):
            raise ValueError("Liaison d’objet absente, en trop ou indisponible dans la source 3MF.")
        placement = {
            "schema": 1,
            "source_sha256": empreinte,
            "declaration_sha256": sha_declaration,
            "repere_xy": "plateau",
            "unites": "mm",
            "plates": [
                {"index": plaque["index"], "name": plaque["name"],
                 "objects": sum(objet["plate"] == plaque["index"] for objet in donnees["objects"])}
                for plaque in donnees["plates"]
            ],
            "objects": [],
        }
        for affectation in donnees["objects"]:
            geometries = par_build[affectation["build_item"]]
            boites = [np.asarray(maillage.bounds, dtype=float) for maillage, _ in geometries]
            if any(boite.shape != (2, 3) or not np.isfinite(boite).all() for boite in boites):
                raise ValueError("Bornes géométriques source invalides.")
            bas = np.min([boite[0] for boite in boites], axis=0)
            haut = np.max([boite[1] for boite in boites], axis=0)
            taille = haut - bas
            if not np.isfinite(taille).all() or np.any(taille < 0):
                raise ValueError("Enveloppe source invalide en mm.")
            identites_objet = [identite for _, identite in geometries]
            if any(
                identite.get("objet_id_racine") != identites_objet[0].get("objet_id_racine")
                for identite in identites_objet
            ):
                raise ValueError("Identifiants d’objet contradictoires dans une même instance.")
            placement["objects"].append({
                "name": affectation["name"],
                "plate": affectation["plate"],
                "xy": [float(bas[0]), float(bas[1])],
                "size": [float(taille[0]), float(taille[1]), float(taille[2])],
                "source_instance": {
                    "build_item": affectation["build_item"],
                    "objet_id": identites_objet[0]["objet_id_racine"],
                    "maillages": [
                        {"modele": identite["modele_maillage"], "objet_id": identite["objet_id_maillage"]}
                        for identite in identites_objet
                    ],
                },
                "transformations": [
                    {"maillage": identite["modele_maillage"], "etapes": identite["transformations"]}
                    for identite in identites_objet
                ],
            })
        valider_placement(placement)
        if sha256_fichier(chemin) != empreinte:
            raise ValueError("Source 3MF modifiée pendant l’analyse.")
        if chemin_declaration is not None and sha256_fichier(chemin_declaration) != sha_declaration:
            raise ValueError("Déclaration modifiée pendant l’analyse.")
        statut = "PASS"
        return {
            "schema": 1,
            "statut": statut,
            "code": code_statut(statut),
            "source_sha256": empreinte,
            "declaration_sha256": sha_declaration,
            "repere_xy": "plateau déclaré explicitement",
            "placement": placement,
            "raisons": [],
            "validation_physique": False,
            "modifie_source": False,
            "gcode_consulte": False,
            "reglages_consultes": False,
        }
    except (OSError, ValueError, KeyError, TypeError) as erreur:
        return _rapport_nv(erreur, empreinte, sha_declaration)


def main(argv=None, *, prive=None):
    """Commande placement importé ; écrit le contrat neuf seulement sur PASS, stdout porte le rapport complet."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Archive 3MF importée, préservée")
    parser.add_argument("declaration", type=Path, help="JSON explicitant plaques, noms, instances et repère")
    parser.add_argument("--sortie", type=Path, required=True, help="Rapport JSON neuf dans le privé ou /tmp")
    arguments = parser.parse_args(argv)
    from benchab.qualite.ecritures import PublicationNeuve, destination_neuve

    try:
        sortie = destination_neuve(arguments.sortie, prive=prive)
        rapport = generer(arguments.source, arguments.declaration)
        if rapport["statut"] == "PASS":
            with PublicationNeuve([arguments.source, arguments.declaration], [sortie]) as publication:
                publication.temporaire(sortie).write_text(
                    json.dumps(rapport["placement"], ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8",
                )
                publication.publier()
    except (OSError, ValueError) as erreur:
        rapport = _rapport_nv(erreur)
        print(json.dumps(rapport, ensure_ascii=False, indent=2))
        return code_statut("NON VÉRIFIÉ")
    print(json.dumps(rapport, ensure_ascii=False, indent=2))
    return rapport["code"]


if __name__ == "__main__":
    raise SystemExit(main())
