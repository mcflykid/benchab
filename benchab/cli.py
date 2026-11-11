"""Entrée unique ; aucun ordonnanceur CLI n'existait dans le paquet."""

import argparse
import ast
import importlib
import json
import math
import os
import sqlite3
import stat
import sys
import uuid
from datetime import datetime
from pathlib import Path

from benchab import __version__
from benchab.donnees.base import Base
from benchab.machine.configuration import charger
from benchab.mesure.collecte import Mesure
from benchab.qualite.frontiere import verifier
from benchab.qualite.statuts import code_statut

SECRETS = {"--token", "--password", "--secret", "--api-key", "--access-token", "--refresh-token"}
CONTROLES_DIRECTS = {
    "reimpression": "reimpression",
    "decollement": "decollement",
    "plaques-interface": "plaques_interface",
    "ergots-faces": "ergots_faces",
    "reglages": "verifier_reglages",
    "enumerations-asn": "enumerations_asn",
    "pieces": "controles_piece",
    "paquet-3mf": "paquet_3mf",
    "finition-parois": "finition_parois",
    "debit-gcode": "debit_gcode",
    "revue-locale": "revue_locale",
    "livrables": "livrables",
    "ponts-internes": "ponts_internes",
    "ponts-surplombs": "ponts_surplombs",
    "disposition-reelle": "disposition_reelle",
    "colonnes-isolees": "colonnes_isolees",
    "couches-courtes": "couches_courtes",
    "orientations-supports": "orientations_supports",
    "surplombs-connexes": "surplombs_connexes",
    "premiere-couche": "premiere_couche",
    "supports-requis": "supports_requis",
    "appuis-gcode": "appuis_gcode",
    "assemblage": "assemblage",
    "ecarts-profils-3mf": "ecarts_profils_3mf",
    "purge-changements": "purge_changements",
    "plages-z": "couverture_plages_z",
}
CONTROLES_DIRECTS_AVEC_PRIVE = frozenset({
    "appuis-gcode", "ecarts-profils-3mf", "plages-z", "surplombs-connexes", "ponts-surplombs",
    "purge-changements",
})

# Les espaces homogènes gardent leur préfixe, leurs alias et leurs options en un seul endroit.
ROUTES_NAMESPACE = {
    "connaissances": {
        "prefixe": "benchab.connaissances.",
        "normalisation": "aucune",
        "racine_privee": True,
    },
    "conception": {
        "prefixe": "benchab.conception.",
        "normalisation": "tirets_vers_underscore",
        "modules_prives": frozenset(
            {"trous", "eprouvette_trous", "vissage", "cad_engine", "fiche_cao", "filetage_nominal", "decoupe"}
        ),
    },
    "imports": {
        "prefixe": "benchab.imports3d.",
        "normalisation": "tirets_vers_underscore",
        "modules_prives": frozenset({"maillage_avance", "placement"}),
    },
    "visualisation": {
        "prefixe": "benchab.visualisation.",
        "normalisation": "tirets_vers_underscore",
        "alias": {"photo": "rendu_photo"},
        "modules_prives": frozenset({
            "maillage_avance", "rendu_photo", "inspection_scene", "assemblage_dossier",
            "visionneuse", "serveur", "apercu_gcode", "commentaires", "retour_photo",
        }),
    },
    "machine": {
        "prefixe": "benchab.machine.",
        "normalisation": "tirets_vers_underscore",
    },
    "references": {
        "prefixe": "benchab.references_visuelles.",
        "normalisation": "aucune",
        "alias": {"verifier": "references"},
    },
}


def _code_arret(arret):
    if arret.code is None:
        return 0
    if isinstance(arret.code, int):
        return arret.code
    print(arret.code, file=sys.stderr)
    return 1


def _arguments_delegues(arguments):
    """Retire uniquement le séparateur initial de argparse ; conserve l'ordre des arguments métier."""
    return arguments[1:] if arguments[:1] == ["--"] else arguments


def _deleguer(fonction, arguments, *, options=None):
    """Appelle une route métier ; convertit son résultat en code et préserve le texte de SystemExit."""
    try:
        return int(fonction(_arguments_delegues(arguments), **(options or {})) or 0)
    except SystemExit as arret:
        return _code_arret(arret)


def _deleguer_namespace(arguments, base):
    """Résout une famille déclarée, ses alias et ses options sans ouvrir un atelier supplémentaire."""
    route = ROUTES_NAMESPACE[arguments.commande]
    action = arguments.action
    if route["normalisation"] == "tirets_vers_underscore":
        action = action.replace("-", "_")
    nom_module = route.get("alias", {}).get(arguments.action, action)
    module = importlib.import_module(route["prefixe"] + nom_module)
    options = {}
    if route.get("racine_privee"):
        options["racine"] = base.prive
    if nom_module in route.get("modules_prives", ()):
        options["prive"] = base.prive
    return _deleguer(module.main, arguments.arguments, options=options)


def duree_finie(texte):
    try:
        valeur = float(texte)
    except ValueError as erreur:
        raise argparse.ArgumentTypeError("une durée numérique est requise") from erreur
    if not math.isfinite(valeur) or valeur < 0:
        raise argparse.ArgumentTypeError("la durée doit être finie et positive ou nulle")
    return valeur


def commande_sans_secrets(argv):
    resultat = []
    sensible = False
    index = 0
    while index < len(argv):
        argument = argv[index]
        cle = argument.split("=", maxsplit=1)[0]
        if cle == "--prive" or cle in SECRETS:
            if cle in SECRETS:
                sensible = True
                resultat.append(f"{cle}=<masqué>")
            if "=" not in argument:
                index += 1
        else:
            resultat.append(argument)
        index += 1
    return " ".join(resultat), sensible


def parser():
    """Décrit les commandes ; entrée aucune, sortie ArgumentParser, sans effet de bord ni statut métier."""
    resultat = argparse.ArgumentParser(prog="benchab", description="Atelier de conception et préparation 3D")
    resultat.add_argument("--prive", default=None, help="Atelier explicite ; sinon BENCHAB_PRIVE ou dépôt/prive")
    resultat.add_argument("--version", action="version", version=__version__)
    commandes = resultat.add_subparsers(dest="commande", required=True)
    commandes.add_parser("init", help="Initialise l'atelier privé sans écraser ses données")
    commandes.add_parser("etat", help="Compte les projets et préparations de l'atelier privé")
    donnees = commandes.add_parser("donnees").add_subparsers(dest="action", required=True)
    requete = donnees.add_parser("impressions")
    requete.add_argument("--resultat", choices=["reussie", "echec", "interrompue", "inconnu"])
    requete.add_argument("--matiere")
    requete.add_argument("--ponts", action="store_true")
    vues = donnees.add_parser("vues")
    vues.add_argument("--racine-publique", type=Path)
    reconstruction = donnees.add_parser("reconstruire")
    reconstruction.add_argument("export", type=Path)
    reconstruction.add_argument("--destination", type=Path, required=True)
    sauvegarde = donnees.add_parser("sauvegarder")
    sauvegarde.add_argument("--source", type=Path, required=True)
    sauvegarde.add_argument("--sortie", type=Path, required=True)
    sauvegarde.add_argument("--jetable", action="store_true", help="Autorise un export d’essai dans le temporaire")
    comparaison = donnees.add_parser("comparer-arbres")
    comparaison.add_argument("attendu", type=Path)
    comparaison.add_argument("obtenu", type=Path)
    restauration = donnees.add_parser("restaurer")
    restauration.add_argument("archive", type=Path)
    restauration.add_argument("--destination", type=Path, required=True)
    commandes.add_parser("configuration", help="Affiche le profil machine sans contacter une machine")
    controle = commandes.add_parser("controle").add_subparsers(dest="controle", required=True)
    installation = controle.add_parser("installation", help="Diagnostique l'installation en lecture seule")
    installation.add_argument("--racine", type=Path, default=Path(__file__).resolve().parents[1])
    installation.add_argument("--profil", type=Path, action="append", default=[])
    installation.add_argument("--espace-minimum-mio", type=int, default=1024)
    installation.add_argument("--detail", action="store_true", help="Inclut les preuves et chemins privés dans stdout")
    plan_reparation = controle.add_parser(
        "plan-reparation", help="Compare les vues privées et prépare un plan sans écriture"
    )
    plan_reparation.add_argument(
        "--candidat-lien", action="append", default=[], metavar="CHEMIN",
        help="Candidat privé relatif ; propose un lien uniquement si un SHA unique correspond au rendu SQLite",
    )
    reprise_export = controle.add_parser(
        "preparer-reprise-export", help="Prépare la copie vérifiée d’un export absent, sans l’appliquer"
    )
    reprise_export.add_argument("--table", required=True, help="Table canonique BENCHAB à recopier")
    reprise_export.add_argument("--sortie", type=Path, required=True, help="Dossier neuf, hors export")
    reprise_source = controle.add_parser(
        "preparer-reprise-source", help="Prépare une copie non appliquée d’un original identifié dans un tar"
    )
    reprise_source.add_argument("--archive", type=Path, required=True, help="Sauvegarde tar régulière")
    reprise_source.add_argument("--sha256-archive", required=True, help="SHA-256 exact des octets du tar")
    reprise_source.add_argument("--membre", required=True, help="Chemin projets/SLUG/sources/FICHIER")
    reprise_source.add_argument("--sha256-source", required=True, help="SHA-256 exact de l’original")
    reprise_source.add_argument("--sortie", type=Path, required=True, help="Dossier neuf sous prive/rapports/")
    verifier_reprise = controle.add_parser(
        "verifier-reprise-source", help="Relit une copie préparée et compare l’original, sans appliquer"
    )
    verifier_reprise.add_argument("--preuve", type=Path, required=True, help="preuve.json privée à relire")
    verifier_reprise.add_argument("--sha256-preuve", required=True, help="SHA-256 exact du fichier de preuve")
    appliquer_reprise = controle.add_parser(
        "appliquer-reprise-export", help="Applique une table absente depuis une preuve CANDIDAT explicite"
    )
    appliquer_reprise.add_argument("--preuve", type=Path, required=True, help="preuve.json CANDIDAT à relire")
    appliquer_reprise.add_argument("--sha256-preuve", required=True, help="SHA-256 exact des octets de preuve.json")
    coutures = controle.add_parser(
        "coutures", help="Contrôle les coutures de paroi sur les régions visibles ou cachées déclarées"
    )
    coutures.add_argument("source", type=Path, help="3MF tranché exact, lié par son SHA")
    coutures.add_argument("--declaration", type=Path, required=True, help="Déclaration de visibilité privée")
    coutures.add_argument("--sortie", type=Path, help="Dossier neuf pour le rapport et son aperçu SVG")
    coutures.add_argument("--detail", action="store_true", help="Afficher aussi le rapport intégral")
    consommation_retour = controle.add_parser(
        "consommation-retour", help="Lit un plan numérique de filament pour un retour identifié"
    )
    consommation_retour.add_argument("--retour", required=True, help="Identifiant du retour enregistré")
    consommation_retour.add_argument("--preuve-pesee", type=Path, help="Déclaration privée facultative")
    consommation_retour.add_argument("--sha256-preuve", help="SHA-256 exact de la déclaration privée")
    consommation_retour.add_argument("--sortie", type=Path, help="Rapport JSON privé neuf")
    consommation_retour.add_argument("--resume", action="store_true", help="Affichage court ; exige --sortie")
    ecart_z = controle.add_parser(
        "ecart-z-supports", help="Estime les écarts Z depuis les dépôts horizontaux d’une plaque"
    )
    ecart_z.add_argument("source", type=Path, help="3MF exact à lire sans modification")
    ecart_z.add_argument("--plaque", type=int, help="Numéro de plaque si le 3MF en contient plusieurs")
    ecart_z.add_argument("--sortie", type=Path, help="Rapport JSON privé neuf")
    ecart_z.add_argument("--resume", action="store_true", help="Affichage court ; exige --sortie")
    capacites = controle.add_parser("capacites-machine", help="Lit les demandes nominales et les plafonds déclarés")
    capacites.add_argument("source", type=Path, help="3MF tranché dans l’atelier")
    capacites.add_argument("--maitre", type=Path, required=True, help="Profil maître privé explicite")
    capacites.add_argument("--sortie", type=Path, help="Rapport JSON privé neuf")
    capacites.add_argument("--resume", action="store_true", help="Affichage court ; exige --sortie")
    rejeux = controle.add_parser("rejeux-gcode", help="Compare deux ou trois reçus et G-code archivés")
    rejeux.add_argument("recus", nargs="+", type=Path, help="Deux ou trois reçus JSON privés")
    rejeux.add_argument("--plaque", required=True, type=int, help="Numéro exact de la plaque")
    rejeux.add_argument("--sortie", type=Path, help="Rapport JSON privé neuf")
    comparaison_3mf = controle.add_parser(
        "comparer-projets-3mf", help="Compare deux projets 3MF sans modifier les archives"
    )
    comparaison_3mf.add_argument("source", type=Path, help="Projet 3MF d’origine")
    comparaison_3mf.add_argument("candidat", type=Path, help="Projet 3MF candidat à comparer")
    comparaison_3mf.add_argument("--sortie", type=Path, help="Rapport JSON privé neuf")
    comparaison_3mf.add_argument("--resume", action="store_true", help="Affiche statuts et compteurs ; exige --sortie")
    entretien = controle.add_parser("entretien", help="Bilan conditionnel depuis un historique privé explicite")
    entretien.add_argument("--historique", type=Path, required=True, help="Historique JSON privé à relire")
    entretien.add_argument("--sortie", type=Path, help="Rapport JSON privé neuf ; une sortie existante est refusée")
    appliquer_vue = controle.add_parser(
        "appliquer-reparation-vue", help="Applique une seule vue privée depuis un plan explicite"
    )
    appliquer_vue.add_argument("--plan", type=Path, required=True, help="Fichier JSON produit par plan-reparation")
    appliquer_vue.add_argument("--cible", required=True, help="Vue relative canonique à réparer")
    appliquer_vue.add_argument(
        "--candidat", help="Candidat privé du plan ; copie ses octets vers la cible absente du lien cassé"
    )
    complet = controle.add_parser("complet", help="Vérifie le dépôt et, sur demande explicite, les témoins S/M/L")
    complet.add_argument("--racine", type=Path, default=Path.cwd())
    complet.add_argument("--temoins", action="store_true")
    complet.add_argument("--profil-canonique", type=Path)
    complet.add_argument("--index-filament", type=int)
    complet.add_argument("--declaration-envoi", type=Path)
    for nom in CONTROLES_DIRECTS:
        direct = controle.add_parser(nom, add_help=False, help="Exécute le contrôle spécialisé et enregistre sa mesure")
        direct.add_argument("-h", "--help", action="store_true", help="Affiche l’aide métier sans exécution")
        direct.add_argument("arguments", nargs=argparse.REMAINDER)
    controle.add_parser("frontiere").add_argument("--racine", type=Path, default=Path.cwd())
    regles = controle.add_parser("regles")
    regles.add_argument("source", type=Path)
    regles.add_argument("--sortie", type=Path)
    opportunite = controle.add_parser("opportunite-decoupe")
    opportunite.add_argument("source", type=Path)
    opportunite.add_argument("--sortie", type=Path)
    envoi = controle.add_parser("envoi")
    envoi.add_argument("fichier", type=Path)
    envoi.add_argument("--declaration", type=Path)
    envoi.add_argument("--reference", type=Path)
    envoi.add_argument("--geometrie", type=Path)
    envoi.add_argument("--revue-locale", type=Path)
    envoi.add_argument("--sortie", type=Path, help="Rapport complet JSON privé neuf ; destination existante refusée")
    envoi.add_argument("--detail", action="store_true", help="Affiche le rapport complet dans stdout")
    envoi.add_argument(
        "--relation-reference",
        choices=[
            "meme_source_modele",
            "seulement_meme_machine_filament",
            "inconnue",
        ],
        default="inconnue",
    )
    controle.add_parser("publication").add_argument("--racine", type=Path, default=Path.cwd())
    controle.add_parser("typographie").add_argument("chemins", type=Path, nargs="*", default=[Path.cwd()])
    routine = controle.add_parser("routine")
    routine.add_argument("plaques", type=Path)
    routine.add_argument("tranche", type=Path)
    routine.add_argument("placement", type=Path)
    routine.add_argument("--sortie", type=Path)
    continuite = controle.add_parser("continuite-paroi", aliases=["continuite_paroi"])
    continuite.add_argument("fichier", type=Path)
    continuite.add_argument("--plaque", type=int)
    continuite.add_argument("--z-min", type=float)
    continuite.add_argument("--z-max", type=float)
    continuite.add_argument("--bandes-z", type=float, help="Largeur des bandes de synthèse en mm")
    continuite.add_argument("--sortie", type=Path, help="Rapport JSON privé neuf ; destination existante refusée")
    continuite.add_argument("--resume", action="store_true", help="Affichage court ; exige --sortie")
    continuite.add_argument("--reference", type=Path)
    continuite.add_argument("--plaque-reference", type=int)
    continuite.add_argument(
        "--relation",
        choices=[
            "meme_source_modele",
            "seulement_meme_machine_filament",
            "inconnue",
        ],
    )
    fabrication = commandes.add_parser("fabrication").add_subparsers(dest="action", required=True)
    chaine = fabrication.add_parser("chaine")
    chaine.add_argument("projet", type=Path)
    chaine.add_argument("--optimiser", action="store_true")
    chaine.add_argument("--objectif")
    tranchage = fabrication.add_parser("trancher")
    tranchage.add_argument("fichier", type=Path)
    tranchage.add_argument("--sortie", type=Path)
    tranchage.add_argument("--nom")
    tranchage.add_argument("--usage", choices=["impression", "temoin"], default="impression")
    tranchage.add_argument(
        "--identifiants",
        type=Path,
        default=os.environ.get("BENCHAB_IDENTIFIANTS_SLICER"),
        help="Catalogue privé des identifiants permanents des profils de filament",
    )
    revue_archive = fabrication.add_parser(
        "revue-archive", help="Revoit puis contrôle une archive déjà tranchée, sans relancer le slicer"
    )
    revue_archive.add_argument("archive", type=Path)
    revue_archive.add_argument("--declaration", type=Path, required=True)
    revue_archive.add_argument("--revue-source", type=Path)
    revue_archive.add_argument("--geometrie", type=Path)
    revue_archive.add_argument("--reference", type=Path)
    revue_archive.add_argument("--sortie", type=Path, required=True)
    fabrication.add_parser("comparer-remplissages").add_argument("etude", type=Path)
    planning = fabrication.add_parser("planning")
    planning.add_argument("--projets", type=Path)
    planning.add_argument("--sortie", type=Path, required=True)
    for nom in (
        "preparation",
        "optimisation",
        "strategies-procede",
        "couches-variables",
        "plaques-mixtes",
        "resistance",
        "exportable",
        "rattachements",
        "retour",
        "envois-slicer",
        "tracer-3mf",
        "remplacer-maillage",
    ):
        route = fabrication.add_parser(nom, add_help=False)
        route.add_argument("-h", "--help", action="store_true", help="Affiche l’aide métier sans exécution")
        route.add_argument("arguments", nargs=argparse.REMAINDER)
    commandes.add_parser("atelier").add_argument("arguments", nargs=argparse.REMAINDER)
    commandes.add_parser("projet").add_argument("arguments", nargs=argparse.REMAINDER)
    # L’installation du lien est opt-in et son plan reste sec par défaut (docs/guides/installation.md).
    skill = commandes.add_parser("skill", help="Planifie le lien local du skill BENCHAB")
    skill.add_argument("--racine", type=Path, required=True)
    skill.add_argument("--destination", type=Path, action="append", required=True)
    skill.add_argument("--ancien-atelier", type=Path)
    skill.add_argument("--appliquer", action="store_true")
    connaissances = commandes.add_parser("connaissances").add_subparsers(dest="action", required=True)
    for nom in ("memoire", "etudes", "bobine", "pdf", "incidents"):
        connaissances.add_parser(nom).add_argument("arguments", nargs=argparse.REMAINDER)
    conception = commandes.add_parser("conception").add_subparsers(dest="action", required=True)
    for nom in (
        "trous",
        "eprouvette-trous",
        "assistant-projet",
        "variantes",
        "capacites-locales",
        "dfam",
        "vissage",
        "cad-engine",
        "fiche-cao",
        "filetage-nominal",
        "decoupe",
    ):
        conception.add_parser(nom).add_argument("arguments", nargs=argparse.REMAINDER)
    imports = commandes.add_parser("imports").add_subparsers(dest="action", required=True)
    for nom in ("importer", "geometrie", "reparer", "maillage-avance", "echange-blender", "placement"):
        imports.add_parser(nom).add_argument("arguments", nargs=argparse.REMAINDER)
    visualisation = commandes.add_parser("visualisation").add_subparsers(dest="action", required=True)
    for nom in ("photo", "retour-photo", "inspection-scene", "assemblage-dossier", "visionneuse", "serveur",
                "apercu-gcode", "commentaires"):
        visualisation.add_parser(nom).add_argument("arguments", nargs=argparse.REMAINDER)
    references = commandes.add_parser("references").add_subparsers(dest="action", required=True)
    for nom in ("verifier", "analyser", "contours"):
        references.add_parser(nom).add_argument("arguments", nargs=argparse.REMAINDER)
    machine = commandes.add_parser("machine").add_subparsers(dest="action", required=True)
    for nom in (
        "profils-cibles",
        "affectations-ace",
        "cloud-lecture",
        "telemetrie",
        "session-compte",
    ):
        machine.add_parser(nom).add_argument("arguments", nargs=argparse.REMAINDER)
    qualite = commandes.add_parser("qualite")
    qualite.add_argument(
        "action",
        choices=["niveau1", "niveau3", "incrementiel", "resume", "tout", "hooks", "archives",
                 "comparer-fabrication", "syntaxe-privee"],
    )
    qualite.add_argument("arguments", nargs=argparse.REMAINDER)
    vote = commandes.add_parser(
        "vote",
        help="Exige une relecture critique externe GROS sans historique avant de formaliser les votes",
    )
    vote.add_argument("dossier", type=Path, nargs="?")
    operation_vote = vote.add_mutually_exclusive_group()
    operation_vote.add_argument("--etat", action="store_true")
    operation_vote.add_argument("--preparer", type=Path)
    operation_vote.add_argument("--comparer", type=Path)
    operation_vote.add_argument("--contribution", type=Path, help="Importe une contribution privée, sans voter")
    operation_vote.add_argument("--anonymiser-contribution", type=Path)
    operation_vote.add_argument("--derniere-contribution", metavar="OBJET")
    operation_vote.add_argument("--arbitrer", metavar="CHOIX", help="Consigne un choix explicite, sans autorisation")
    vote.add_argument("--id-contribution")
    vote.add_argument("--mode-contribution", choices=("ECONOMIE", "EQUILIBRE", "APPROFONDI"), default="EQUILIBRE")
    vote.add_argument("--etat-contribution", choices=("DESACTIVE", "OBSERVATION", "ACTIF"), default="OBSERVATION")
    vote.add_argument("--sortie", type=Path)
    vote.add_argument("--objet")
    vote.add_argument("--controle", type=Path, action="append", default=[])
    seance = commandes.add_parser("seance").add_subparsers(dest="action", required=True)
    debut = seance.add_parser("debut")
    debut.add_argument("--lot", default=None)
    seance.add_parser("reprendre", help="Renouvelle le verrou d’une séance orpheline")
    fin = seance.add_parser("fin")
    fin.add_argument("--forcer", action="store_true", help="Ferme sans jeton et inscrit la dérogation")
    fin.add_argument("--candidates", type=int, default=0)
    fin.add_argument("--temps-vote", type=duree_finie)
    fin.add_argument("--temps-controles", type=duree_finie)
    note = seance.add_parser("note", help="Ajoute une note courte à la séance active")
    note.add_argument("texte")
    seance.add_parser("etat")
    return resultat


def executer(arguments, base):
    """Distribue Namespace/Base ; codes PASS/WARN/FAIL/NON VÉRIFIÉ, unités propres aux modules.

    La comparaison d'arbres reste en lecture seule ; aucune autorisation machine n'est implicite.
    """
    if arguments.commande == "init":
        from benchab.donnees.reference import importer

        importer(base)
        print("Atelier privé initialisé ; données existantes conservées.")
    elif arguments.commande == "etat":
        comptes = {
            table: base.connexion.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
            for table in ("projets", "preparations", "impressions", "connaissances")
        }
        print(json.dumps(comptes, ensure_ascii=False))
    elif arguments.commande == "configuration":
        print(json.dumps(charger(), ensure_ascii=False, indent=2))
    elif arguments.commande == "donnees":
        if arguments.action == "sauvegarder":
            from benchab.donnees.sauvegarde import creer

            resultat = creer(base, arguments.source, arguments.sortie, jetable=arguments.jetable)
        elif arguments.action == "restaurer":
            from benchab.donnees.sauvegarde import restaurer

            resultat = restaurer(arguments.archive, arguments.destination, base=base)
        elif arguments.action == "comparer-arbres":
            from benchab.donnees.sauvegarde import comparer_arbres

            resultat = comparer_arbres(base, arguments.attendu, arguments.obtenu)
        elif arguments.action == "impressions":
            from benchab.donnees.requetes import impressions

            resultat = impressions(base, resultat=arguments.resultat, matiere=arguments.matiere, ponts=arguments.ponts)
        elif arguments.action == "vues":
            from benchab.donnees.vues import generer

            resultat = generer(base, racine_publique=arguments.racine_publique)
        else:
            from benchab.donnees.reconstruction import reconstruire
            from benchab.qualite.ecritures import _sans_lien

            destination = _sans_lien(arguments.destination)
            if destination == base.prive.resolve():
                raise ValueError("La destination reconstruite doit être distincte de l'atelier courant.")
            # Cette destination de reconstruction est explicitement choisie ;
            # elle ne devient pas l'atelier courant de la commande.
            restauree = Base(destination, creer=True)
            try:
                resultat = reconstruire(restauree, arguments.export)
            finally:
                restauree.fermer()
        print(json.dumps(resultat, ensure_ascii=False, indent=2))
        if arguments.action == "comparer-arbres":
            return code_statut(resultat["statut"])
    elif arguments.commande == "controle":
        if arguments.controle in {"regles", "opportunite-decoupe"}:
            nom = "regles" if arguments.controle == "regles" else "opportunite_decoupe"
            module = importlib.import_module("benchab.controles." + nom)
            source = arguments.source.resolve() if arguments.source.exists() else arguments.source
            directs = [str(source)]
            if arguments.sortie:
                directs.extend(["--sortie", str(arguments.sortie)])
            return module.main(directs, base=base)
        if arguments.controle in CONTROLES_DIRECTS:
            module = importlib.import_module("benchab.controles." + CONTROLES_DIRECTS[arguments.controle])
            options = {"prive": base.prive} if arguments.controle in CONTROLES_DIRECTS_AVEC_PRIVE else {}
            return _deleguer(module.main, arguments.arguments, options=options)
        if arguments.controle == "routine":
            from benchab.controles.routine import controler

            rapport = controler(
                arguments.plaques,
                arguments.tranche,
                arguments.placement,
                arguments.sortie or base.prive / "rapports/routine",
            )
            print(json.dumps(rapport, ensure_ascii=False, indent=2))
            return rapport["code"]
        if arguments.controle in {"continuite-paroi", "continuite_paroi"}:
            from benchab.controles.continuite_paroi import controler, resumer
            from benchab.qualite.ecritures import _sans_lien, ecrire_json_neuf, sortie_durable
            from benchab.qualite.empreintes import sha256_fichier

            if arguments.resume and arguments.sortie is None:
                raise ValueError("--resume exige --sortie pour conserver le rapport complet")
            source = _sans_lien(arguments.fichier.absolute())
            empreinte_avant = sha256_fichier(source) if source.is_file() else None
            reference_source = _sans_lien(arguments.reference.absolute()) if arguments.reference else None
            empreinte_reference = (
                sha256_fichier(reference_source)
                if reference_source is not None and reference_source.is_file() else None
            )

            fenetre = None
            if arguments.z_min is not None or arguments.z_max is not None:
                if arguments.z_min is None or arguments.z_max is None:
                    raise ValueError("La fenêtre d’analyse exige --z-min et --z-max ensemble")
                fenetre = (arguments.z_min, arguments.z_max)
            reference = None
            if arguments.reference:
                if arguments.relation is None:
                    raise ValueError("Une référence exige --relation explicite")
                reference = {
                    "fichier": str(arguments.reference),
                    "relation": arguments.relation,
                    "plaque": arguments.plaque_reference,
                    "identifiant": arguments.reference.name,
                }
            rapport = controler(arguments.fichier, arguments.plaque, fenetre, reference=reference)
            if arguments.bandes_z is not None:
                from benchab.gcode.bandes_continuite import ajouter_bandes

                rapport = ajouter_bandes(rapport, arguments.bandes_z, fenetre)
            if empreinte_avant is not None and sha256_fichier(source) != empreinte_avant:
                raise ValueError("Source modifiée pendant le contrôle de continuité")
            if empreinte_reference is not None and sha256_fichier(reference_source) != empreinte_reference:
                raise ValueError("Référence modifiée pendant le contrôle de continuité")
            # L'export et stdout partagent les mêmes mesures et empreintes ; aucune preuve
            # ne remplace une source ou un résultat gelé (docs/reference/continuite-paroi.md).
            rapport["source"] = {"fichier": str(source), "sha256": empreinte_avant, "plaque": arguments.plaque}
            if reference_source is not None:
                rapport["source_reference"] = {"fichier": str(reference_source), "sha256": empreinte_reference}
            rapport["code"] = code_statut(rapport["statut"])
            if arguments.sortie:
                from benchab.controles.rapports import _protege

                sortie = sortie_durable(arguments.sortie.absolute(), prive=base.prive)
                _protege(sortie)
                entrees = [source] + ([arguments.reference.absolute()] if arguments.reference else [])
                ecrire_json_neuf(sortie, rapport, entrees=entrees)
                if arguments.resume:
                    print(json.dumps(resumer(rapport, sortie), ensure_ascii=False, separators=(",", ":")))
                    return rapport["code"]
            print(json.dumps(rapport, ensure_ascii=False, indent=2))
            return rapport["code"]
        if arguments.controle == "complet":
            from benchab.qualite.complet import executer

            rapport = executer(
                arguments.racine,
                prive=base.prive,
                temoins=arguments.temoins,
                profil_canonique=arguments.profil_canonique,
                index_filament=arguments.index_filament,
                declaration_envoi=arguments.declaration_envoi,
            )
            rapport["code"] = code_statut(rapport["statut"])
            print(json.dumps(rapport, ensure_ascii=False, indent=2))
            return rapport["code"]
        if arguments.controle == "envoi":
            from benchab.controles.envoi import controler
            from benchab.controles.envoi import resumer as resumer_envoi
            from benchab.qualite.ecritures import ecrire_json_neuf, sortie_durable

            rapport = controler(
                arguments.fichier,
                arguments.declaration,
                reference=arguments.reference,
                geometrie=arguments.geometrie,
                revue_locale=arguments.revue_locale,
                relation_reference=arguments.relation_reference,
            )
            if arguments.sortie:
                sortie = arguments.sortie
                if not sortie.is_absolute():
                    sortie = base.prive / sortie
                sortie = sortie_durable(sortie.absolute(), prive=base.prive)
                entrees = [arguments.fichier] + [
                    chemin for chemin in (
                        arguments.declaration, arguments.reference, arguments.geometrie, arguments.revue_locale,
                    ) if chemin is not None
                ]
                ecrire_json_neuf(sortie, rapport, entrees=entrees)
            affichage = rapport
            if arguments.sortie and not arguments.detail:
                affichage = {**resumer_envoi(rapport), "rapport": sortie.relative_to(base.prive).as_posix()}
            print(json.dumps(affichage, ensure_ascii=False, indent=2))
            return rapport["code"]
        if arguments.controle == "typographie":
            from benchab.qualite.edition import typographie

            erreurs = typographie(arguments.chemins)
        elif arguments.controle == "publication":
            from benchab.qualite.edition import verifier as verifier_publication

            erreurs = verifier_publication(arguments.racine, prive=base.prive)
        else:
            erreurs = verifier(arguments.racine, prive=base.prive)
        if erreurs:
            print("\n".join(erreurs), file=sys.stderr)
            return 1
        if arguments.controle == "publication":
            print("PASS : frontière, modèles, secrets, licences, typographie et liens de publication.")
        elif arguments.controle == "typographie":
            print("PASS : typographie des textes contrôlés.")
        else:
            print("PASS : frontière privée et interpréteur.")
    elif arguments.commande == "vote":
        from benchab.vote import conseil

        def chemin_vote(chemin):
            return chemin.resolve() if chemin.exists() else chemin

        if arguments.arbitrer is not None:
            resultat = conseil.arbitrer(base, arguments.objet, arguments.arbitrer)
            print(json.dumps(resultat, ensure_ascii=False, indent=2))
            return 0
        if arguments.contribution or arguments.anonymiser_contribution or arguments.derniere_contribution:
            from benchab.vote import contributions

            mode = arguments.mode_contribution
            if arguments.contribution:
                resultat = contributions.preparer(base, arguments.contribution, arguments.etat_contribution, mode)
            elif arguments.anonymiser_contribution:
                resultat = contributions.anonymiser(
                    contributions.charger(base, arguments.anonymiser_contribution, mode), mode,
                )
            else:
                resultat = contributions.derniere(base, arguments.derniere_contribution,
                                                   identifiant=arguments.id_contribution)
            print(json.dumps(resultat, ensure_ascii=False, indent=2))
            return 0
        if arguments.etat:
            print(json.dumps(conseil.etat(base), ensure_ascii=False, indent=2))
            return 0
        if arguments.comparer:
            print(json.dumps(conseil.comparer(base, chemin_vote(arguments.comparer)), ensure_ascii=False, indent=2))
            return 0
        if arguments.preparer:
            if arguments.sortie is None or arguments.objet is None:
                raise ValueError("--preparer exige --sortie et --objet.")
            chemin = conseil.preparer(
                base,
                chemin_vote(arguments.preparer),
                arguments.sortie,
                arguments.objet,
                controles=[chemin_vote(p) for p in arguments.controle],
                **({"contribution_id": arguments.id_contribution} if arguments.id_contribution else {}),
            )
            print(chemin)
            return 0
        if arguments.dossier is None:
            raise ValueError("Fournir un dossier de vote ou --preparer/--etat.")
        vote = conseil.produire(base, chemin_vote(arguments.dossier))
        print(json.dumps(vote, ensure_ascii=False, indent=2))
        return 0
    elif arguments.commande == "fabrication":
        if arguments.action == "chaine":
            from benchab.fabrication.chaine import executer as chaine_projet

            resultat = chaine_projet(
                arguments.projet, base.prive, optimiser=arguments.optimiser, objectif=arguments.objectif
            )
            print(json.dumps(resultat, ensure_ascii=False, indent=2))
            return resultat["code"]
        if arguments.action == "revue-archive":
            from benchab.fabrication.revue_archive import executer

            resultat = executer(
                arguments.archive,
                arguments.declaration,
                sortie=arguments.sortie,
                prive=base.prive,
                revue_source=arguments.revue_source,
                geometrie=arguments.geometrie,
                reference=arguments.reference,
            )
            print(json.dumps({cle: resultat[cle] for cle in (
                "statut", "code", "rapport", "rapport_initial", "revue", "faits_revue", "resume_revue"
            )}, ensure_ascii=False, indent=2))
            return resultat["code"]
        if arguments.action == "comparer-remplissages":
            from benchab.fabrication.comparer_remplissages import cli

            return cli(["--prive", str(base.prive), str(arguments.etude)])
        if arguments.action == "planning":
            from benchab.fabrication.planning import main as planifier

            return planifier(
                [
                    "--prive",
                    str(base.prive),
                    "--projets",
                    str(arguments.projets or base.prive / "projets"),
                    "--sortie",
                    str(arguments.sortie),
                ]
            )
        if arguments.action in {"preparation", "optimisation", "strategies-procede"}:
            noms_modules = {
                "preparation": "preparation",
                "optimisation": "optimisation",
                "strategies-procede": "strategies_procede",
            }
            module = importlib.import_module("benchab.fabrication." + noms_modules[arguments.action])
            return _deleguer(module.main, arguments.arguments, options={"base": base})
        if arguments.action == "tracer-3mf":
            from benchab.fabrication.tracer_3mf import main as tracer

            return _deleguer(tracer, arguments.arguments, options={"base": base})
        if arguments.action == "remplacer-maillage":
            from benchab.fabrication.remplacement_maillage import main as remplacer_maillage

            return _deleguer(remplacer_maillage, arguments.arguments, options={"base": base})
        if arguments.action == "couches-variables":
            from benchab.fabrication.couches_variables import cli

            directs = _arguments_delegues(arguments.arguments)
            if "--prive" in directs or any(argument.startswith("--prive=") for argument in directs):
                raise ValueError("Utiliser --prive avant `fabrication couches-variables`.")
            return _deleguer(cli, ["--prive", str(base.prive), *directs])
        if arguments.action == "plaques-mixtes":
            from benchab.fabrication.plaques_mixtes import main as preparer_plaques

            return _deleguer(preparer_plaques, arguments.arguments, options={"prive": base.prive})
        if arguments.action == "resistance":
            from benchab.fabrication.resistance import main as auditer_resistance

            return _deleguer(auditer_resistance, arguments.arguments, options={"prive": base.prive})
        if arguments.action == "exportable":
            from benchab.fabrication.exportable import main as verifier_exportable

            return _deleguer(verifier_exportable, arguments.arguments, options={"prive": base.prive})
        if arguments.action == "rattachements":
            from benchab.fabrication.rattachements import main as verifier_rattachements

            return _deleguer(verifier_rattachements, arguments.arguments)
        if arguments.action == "retour":
            from benchab.fabrication.retour import main as enregistrer_retour

            return _deleguer(enregistrer_retour, arguments.arguments, options={"base": base})
        if arguments.action == "envois-slicer":
            from benchab.fabrication.envois_slicer import main as gerer_envois_slicer

            return _deleguer(gerer_envois_slicer, arguments.arguments, options={"prive": base.prive})
        from benchab.fabrication.trancher import trancher_fichier

        sortie, rapport = trancher_fichier(
            arguments.fichier,
            arguments.sortie or base.prive / "fabrication",
            arguments.nom,
            usage=arguments.usage,
            prive=base.prive,
            identifiants=(
                json.loads(Path(arguments.identifiants).read_text(encoding="utf-8"))["filaments"]
                if arguments.identifiants
                else None
            ),
        )
        print(json.dumps({"sortie": str(sortie), "rapport": rapport}, ensure_ascii=False, indent=2))
    elif arguments.commande in ROUTES_NAMESPACE:
        return _deleguer_namespace(arguments, base)
    elif arguments.commande == "projet":
        from benchab.projets import nouveau

        return _deleguer(nouveau.main, arguments.arguments, options={"prive": base.prive, "base": base})
    elif arguments.commande == "atelier":
        from benchab.organisation import automatisation

        return _deleguer(automatisation.main, arguments.arguments, options={"prive": base.prive})
    elif arguments.commande == "skill":
        from benchab import skill as gestion_skill

        plan = gestion_skill.planifier(
            arguments.racine,
            arguments.destination,
            ancien_atelier=arguments.ancien_atelier,
        )
        resultat = gestion_skill.appliquer(plan) if arguments.appliquer else plan
        print(json.dumps(resultat, ensure_ascii=False, indent=2))
        return 0
    elif arguments.commande == "qualite":
        if arguments.action == "syntaxe-privee":
            from benchab.qualite.references import verifier_scripts_projet

            commande = argparse.ArgumentParser(prog="benchab qualite syntaxe-privee")
            commande.add_argument("projet", help="Identifiant explicite du projet privé")
            directs = _arguments_delegues(arguments.arguments)
            rapport = verifier_scripts_projet(base, commande.parse_args(directs).projet)
            rapport["code"] = code_statut(rapport["statut"])
            print(json.dumps(rapport, ensure_ascii=False, indent=2))
            return rapport["code"]
        if arguments.action in {"archives", "comparer-fabrication"}:
            module = importlib.import_module("benchab.qualite." + arguments.action.replace("-", "_"))
            return _deleguer(module.main, arguments.arguments, options={"prive": base.prive})
        special = arguments.action in {"tout", "hooks"}
        module = importlib.import_module("benchab.qualite." + (arguments.action if special else "qualite"))
        if special:
            return _deleguer(module.main, arguments.arguments)
        directs = _arguments_delegues(arguments.arguments)
        return _deleguer(module.main, [arguments.action, *directs])
    elif arguments.commande == "seance":
        from benchab.donnees import seance

        if arguments.action in {"debut", "reprendre"}:
            reprise = arguments.action == "reprendre"
            jeton = seance.ouvrir(base, getattr(arguments, "lot", None), reprise=reprise)
            print("Séance reprise." if reprise else "Séance commencée.")
            print("Jeton de séance : " + jeton)
        elif arguments.action == "fin":
            seance.fermer(
                base,
                os.environ.get("BENCHAB_JETON_SEANCE"),
                forcer=arguments.forcer,
                candidates=arguments.candidates,
                temps_vote=arguments.temps_vote,
                temps_controles=arguments.temps_controles,
            )
            from benchab.donnees.vues import generer

            generer(base)
            print("Séance terminée ; journal enregistré et exporté.")
        elif arguments.action == "note":
            texte = arguments.texte.strip()
            if not texte or len(texte) > 2000:
                raise ValueError("Note non vide de 2 000 caractères au plus requise.")
            with base.transaction() as connexion:
                # La note appartient à une séance encore active au moment de
                # l'écriture (docs/guides/cycle-projet.md).
                connexion.execute("BEGIN IMMEDIATE")
                actif = seance.active_valide(connexion)
                if actif is None:
                    raise ValueError("Aucune séance active avec verrou valide. Voir benchab seance etat.")
                connexion.execute("INSERT INTO decisions VALUES (?,NULL,?,?,?,?)", (
                    uuid.uuid4().hex, datetime.now().astimezone().isoformat(), texte,
                    "journal de séance BENCHAB : outil", json.dumps({
                        "type": "note_seance", "seance_id": actif[0], "observation_humaine": False,
                    }, ensure_ascii=False),
                ))
            print("Note de séance enregistrée.")
        else:
            actif = base.connexion.execute("SELECT id, lot, debut FROM seances WHERE fin IS NULL").fetchone()
            resultat = {"active": False}
            if actif:
                resultat = {"lot": actif["lot"], "debut": actif["debut"]}
                resultat["verrou"] = seance.etat_verrou(base.connexion, actif["id"])
                note = base.connexion.execute(
                    "SELECT texte,date FROM decisions WHERE json_extract(donnees_json,'$.type')='note_seance' "
                    "AND json_extract(donnees_json,'$.seance_id')=? ORDER BY rowid DESC LIMIT 1", (actif["id"],),
                ).fetchone()
                if note:
                    resultat["derniere_note"] = dict(note)
            print(json.dumps(resultat, ensure_ascii=False))
    return 0


def _aide_deleguee(arguments):
    """Affiche l'aide statique de la route ; code zéro, aucun module métier exécuté ni atelier ouvert."""
    courant = parser()
    while True:
        sous = next((a for a in courant._actions if isinstance(a, argparse._SubParsersAction)), None)
        if sous is None or getattr(arguments, sous.dest, None) not in sous.choices:
            break
        courant = sous.choices[getattr(arguments, sous.dest)]
    print(courant.format_help(), end="")
    # Certains parseurs historiques ignorent --help et écrivent un rapport :
    # leur fonction métier ne doit jamais être appelée pour l'aide
    # (docs/reference/chemins-et-atelier.md).
    groupe = arguments.commande
    nom = getattr(arguments, "action", "")
    if groupe == "controle":
        dossier, nom = "controles", CONTROLES_DIRECTS.get(getattr(arguments, "controle", ""), "")
    elif groupe == "projet":
        dossier, nom = "projets", "nouveau"
    elif groupe == "atelier":
        dossier, nom = "organisation", "automatisation"
    else:
        dossier = {"imports": "imports3d", "references": "references_visuelles"}.get(groupe, groupe)
        if groupe == "visualisation":
            nom = {"photo": "rendu_photo", "inspection-scene": "inspection_scene"}.get(nom, nom)
    source = Path(__file__).parent / dossier / (nom.replace("-", "_") + ".py")
    if source.is_file():
        arbre = ast.parse(source.read_text(encoding="utf-8"))
        documentation = ast.get_docstring(arbre)
        if documentation:
            print("\n" + documentation)
        options, positionnels, sous_commandes = [], [], []
        for noeud in ast.walk(arbre):
            if (isinstance(noeud, ast.Call) and isinstance(noeud.func, ast.Attribute)
                    and noeud.func.attr in {"add_argument", "add_parser"}):
                for argument in noeud.args:
                    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                        groupe = sous_commandes if noeud.func.attr == "add_parser" else (
                            options if argument.value.startswith("-") else positionnels
                        )
                        if argument.value not in groupe:
                            groupe.append(argument.value)
        # Les options de finition proviennent d’un dictionnaire déclaré ; lire ses
        # clés dans l’AST évite d’importer ou d’exécuter le contrôle (#98).
        for boucle in ast.walk(arbre):
            if not isinstance(boucle, ast.For):
                continue
            collection = boucle.iter
            if (isinstance(collection, ast.Call) and isinstance(collection.func, ast.Attribute)
                    and collection.func.attr == "items"):
                collection = collection.func.value
            if not isinstance(collection, ast.Name):
                continue
            dynamique = any(
                isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "add_argument"
                and n.args and isinstance(n.args[0], ast.BinOp)
                and isinstance(n.args[0].left, ast.Constant) and n.args[0].left.value == "--"
                for n in ast.walk(boucle)
            )
            if dynamique:
                for declaration in arbre.body:
                    if (isinstance(declaration, ast.Assign)
                            and any(isinstance(c, ast.Name) and c.id == collection.id for c in declaration.targets)
                            and isinstance(declaration.value, ast.Call)
                            and isinstance(declaration.value.func, ast.Name) and declaration.value.func.id == "dict"):
                        options.extend("--" + k.arg.replace("_", "-")
                                       for k in declaration.value.keywords if k.arg)
        if sous_commandes:
            print("\nSous-commandes déclarées : " + ", ".join(sous_commandes))
        if positionnels:
            print("\nPositionnels déclarés (selon la sous-commande) : " + ", ".join(positionnels))
        if options:
            print("\nOptions déclarées par le module (selon la sous-commande) : " + ", ".join(options))
    print("\nGuide des commandes : docs/reference/commandes.md")
    return 0


def _mesurer_refus(argv, commande, code):
    """Journalise un refus seulement dans un atelier déjà initialisé ; durée s, aucune création ni secret stocké."""
    from benchab.chemins import atelier_prive

    if "--help" in argv or "--version" in argv:
        return
    base = None
    try:
        choix = argparse.ArgumentParser(add_help=False)
        choix.add_argument("--prive", default=None)
        preference, _ = choix.parse_known_args(argv)
        base = Base(atelier_prive(preference.prive), creer=False)
        Mesure().enregistrer(base, commande, code)
    except (OSError, ValueError, RuntimeError, sqlite3.Error, SystemExit):
        # Le refus conserve son code même si aucun atelier n'existe ; aucune
        # initialisation implicite (docs/reference/chemins-et-atelier.md).
        pass
    finally:
        if base is not None:
            base.fermer()


def main(argv=None):
    """Distribue les commandes, codes CLI et durées s, sans autorisation machine implicite.

    Les plans et préparations d’export/source ainsi que le contrôle des coutures ne créent
    ni atelier ni mesure. --help/--version restent sans effet ; seul init crée un atelier
    CLI absent (docs/reference/chemins-et-atelier.md).
    """
    from benchab.chemins import atelier_prive

    argv = sys.argv[1:] if argv is None else argv
    diagnostic_seul = any(
        argv[i] == "controle" and argv[i + 1] in {
            "installation",
            "plan-reparation",
            "preparer-reprise-export",
            "preparer-reprise-source",
            "verifier-reprise-source",
            "appliquer-reprise-export",
            "appliquer-reparation-vue",
            "entretien",
            "consommation-retour",
            "ecart-z-supports",
            "capacites-machine",
            "rejeux-gcode",
            "comparer-projets-3mf",
            "coutures",
        }
        for i in range(len(argv) - 1)
    ) or any(
        argv[i] == "visualisation" and argv[i + 1] == "retour-photo"
        for i in range(len(argv) - 1)
    ) or any(
        argv[i] == "fabrication" and argv[i + 1] == "remplacer-maillage"
        for i in range(len(argv) - 1)
    )
    commande, sensible = commande_sans_secrets(argv)
    if sensible:
        print("BENCHAB : Les secrets ne sont pas acceptés dans les arguments de commande.", file=sys.stderr)
        if not diagnostic_seul:
            _mesurer_refus(argv, commande, 1)
        return 1
    try:
        arguments = parser().parse_args(argv)
    except SystemExit as arret:
        code = _code_arret(arret)
        if code and not diagnostic_seul:
            _mesurer_refus(argv, commande, code)
        return code
    if "--version" in argv:
        print(__version__)
        return 0
    if "--help" in argv or "-h" in argv:
        return _aide_deleguee(arguments)
    if arguments.commande == "controle" and arguments.controle == "plan-reparation":
        from benchab.qualite.plan_reparation import planifier

        # Comme le diagnostic d'installation, ce plan reste hors Base() et hors mesure (#119).
        rapport = planifier(atelier_prive(arguments.prive), candidats=arguments.candidat_lien)
        print(json.dumps(rapport, ensure_ascii=False, separators=(",", ":")))
        return rapport["code"]
    if arguments.commande == "controle" and arguments.controle == "preparer-reprise-export":
        from benchab.qualite.reprise_export import preparer

        # Le dossier explicite reçoit la preuve ; Base() n’est jamais ouvert (#119).
        try:
            rapport = preparer(arguments.prive, arguments.table, arguments.sortie)
        except (OSError, ValueError, sqlite3.Error) as erreur:
            print(f"BENCHAB : préparation de reprise refusée : {erreur}", file=sys.stderr)
            return 2
        print(json.dumps(rapport, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    if arguments.commande == "controle" and arguments.controle == "preparer-reprise-source":
        from benchab.donnees.reprise_source import preparer

        # La copie reste sous rapports ; aucun original ni Base() n’est ouvert (#119).
        try:
            rapport = preparer(
                arguments.prive, arguments.archive, arguments.sha256_archive,
                arguments.membre, arguments.sha256_source, arguments.sortie,
            )
        except (OSError, ValueError) as erreur:
            print(f"BENCHAB : préparation de source refusée : {erreur}", file=sys.stderr)
            return 2
        print(json.dumps(rapport, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    if arguments.commande == "controle" and arguments.controle == "verifier-reprise-source":
        from benchab.donnees.reprise_source import verifier_copie

        # La copie est relue comme candidat ; aucun original n’est écrit (#119).
        try:
            rapport = verifier_copie(arguments.prive, arguments.preuve, arguments.sha256_preuve)
        except (OSError, ValueError) as erreur:
            print(f"BENCHAB : vérification de reprise refusée : {erreur}", file=sys.stderr)
            return 2
        print(json.dumps(rapport, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    if arguments.commande == "controle" and arguments.controle == "appliquer-reprise-export":
        from benchab.qualite.reprise_export import appliquer

        # Un candidat fourni et son SHA exact sont requis ; aucune mesure ni Base() n’est ouverte.
        try:
            rapport = appliquer(arguments.prive, arguments.preuve, arguments.sha256_preuve)
        except (OSError, ValueError, sqlite3.Error) as erreur:
            print(f"BENCHAB : application de reprise refusée : {erreur}", file=sys.stderr)
            return 2
        print(json.dumps(rapport, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    if arguments.commande == "controle" and arguments.controle == "coutures":
        from benchab.qualite.statuts import code_statut
        from benchab.visualisation.apercu_coutures import controler_declaration, publier

        try:
            rapport = controler_declaration(arguments.source, arguments.declaration, prive=arguments.prive)
            sortie = publier(rapport, arguments.sortie, prive=arguments.prive) if arguments.sortie else None
            print(json.dumps(rapport if sortie is None or arguments.detail else sortie,
                             ensure_ascii=False, indent=2, allow_nan=False))
        except (OSError, ValueError, KeyError) as erreur:
            print(f"BENCHAB : contrôle des coutures refusé : {erreur}", file=sys.stderr)
            return 2
        return code_statut(rapport["statut"])
    if arguments.commande == "controle" and arguments.controle == "consommation-retour":
        from benchab.projets.plan_consommation import planifier_lecture, resumer
        from benchab.qualite.ecritures import destination_neuve, ecrire_json_neuf

        try:
            if arguments.resume and arguments.sortie is None:
                raise ValueError("--resume exige --sortie pour conserver le rapport complet.")
            sortie = None
            if arguments.sortie is not None:
                if arguments.sortie.suffix.lower() != ".json":
                    raise ValueError("La sortie de consommation doit être un JSON neuf.")
                sortie = destination_neuve(arguments.sortie, prive=arguments.prive, temporaire=False)
            # La consultation reste NON VÉRIFIÉE ; seule la publication explicite écrit (#35, #77).
            rapport = planifier_lecture(
                arguments.prive, arguments.retour,
                preuve_pesee=arguments.preuve_pesee, sha256_preuve=arguments.sha256_preuve,
            )
            if sortie is not None:
                ecrire_json_neuf(sortie, rapport)
            affiche = resumer(rapport, sortie) if arguments.resume else rapport
            print(json.dumps(affiche, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
            return rapport["code"]
        except (OSError, ValueError, RuntimeError) as erreur:
            print(f"BENCHAB : rapport de consommation refusé : {erreur}", file=sys.stderr)
            return 2
    if arguments.commande == "controle" and arguments.controle == "ecart-z-supports":
        from zipfile import BadZipFile

        from benchab.controles.ecart_z_supports import analyser, resumer
        from benchab.qualite.ecritures import destination_neuve, ecrire_json_neuf

        try:
            if arguments.resume and arguments.sortie is None:
                raise ValueError("--resume exige --sortie pour conserver le rapport complet.")
            sortie = None
            if arguments.sortie is not None:
                if arguments.sortie.suffix.lower() != ".json":
                    raise ValueError("La sortie de l’écart Z doit être un JSON neuf.")
                sortie = destination_neuve(arguments.sortie, prive=arguments.prive, temporaire=False)
            rapport = analyser(arguments.source, arguments.plaque)
            if sortie is not None:
                ecrire_json_neuf(sortie, rapport, entrees=[arguments.source])
            affiche = resumer(rapport, sortie) if arguments.resume else rapport
            print(json.dumps(affiche, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
            # Une absence de couverture reste un résultat valide NON MESURABLE ;
            # seules les entrées illisibles relèvent du code technique 2.
            return 0
        except (OSError, ValueError, KeyError, BadZipFile, RuntimeError) as erreur:
            print(f"BENCHAB : lecture de l’écart Z refusée : {erreur}", file=sys.stderr)
            return 2
    if arguments.commande == "controle" and arguments.controle == "capacites-machine":
        from benchab.chemins import sans_lien
        from benchab.controles.capacites_machine import analyser, resumer
        from benchab.qualite.ecritures import destination_neuve, ecrire_json_neuf
        from benchab.qualite.statuts import code_statut as code_capacites

        # Consultation de deux fichiers déclarés ; ni base, ni connexion, ni
        # sélection de profil implicite (docs/reference/commandes.md).
        try:
            if arguments.resume and arguments.sortie is None:
                raise ValueError("--resume exige --sortie pour conserver le rapport complet.")
            atelier = atelier_prive(arguments.prive)
            source = sans_lien(arguments.source.expanduser().absolute(), racine=atelier, strict=True)
            maitre = sans_lien(arguments.maitre.expanduser().absolute(), racine=atelier, strict=True)
            sortie = None
            if arguments.sortie is not None:
                if arguments.sortie.suffix.lower() != ".json":
                    raise ValueError("La sortie de capacités doit être un JSON neuf.")
                sortie = destination_neuve(
                    arguments.sortie.expanduser().absolute(), prive=atelier, temporaire=False,
                )
            rapport = analyser(source, maitre=maitre)
            if sortie is not None:
                ecrire_json_neuf(sortie, rapport, entrees=[source, maitre])
            affiche = resumer(rapport, sortie) if arguments.resume else rapport
            print(json.dumps(affiche, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
            return code_capacites(rapport["statut"])
        except (OSError, ValueError) as erreur:
            print(f"BENCHAB : contrôle des capacités refusé : {erreur}", file=sys.stderr)
            return 2
    if arguments.commande == "controle" and arguments.controle == "rejeux-gcode":
        from benchab.controles.rejeux_gcode import comparer
        from benchab.qualite.ecritures import destination_neuve, ecrire_json_neuf

        # Des rapports archivés sont lus sans Base() ni démarrage du moteur (#208).
        try:
            rapport = comparer(arguments.prive, arguments.recus, plaque=arguments.plaque)
            if arguments.sortie is not None:
                if arguments.sortie.suffix.lower() != ".json":
                    raise ValueError("La sortie de comparaison doit être un fichier JSON neuf.")
                cible = destination_neuve(
                    arguments.sortie.absolute(), prive=atelier_prive(arguments.prive), temporaire=False,
                )
                ecrire_json_neuf(cible, rapport, entrees=arguments.recus)
            print(json.dumps(rapport, ensure_ascii=False, indent=2, allow_nan=False))
            return rapport["code"]
        except (OSError, ValueError) as erreur:
            print(f"BENCHAB : comparaison de rejeux refusée : {erreur}", file=sys.stderr)
            return 2
    if arguments.commande == "controle" and arguments.controle == "comparer-projets-3mf":
        from zipfile import BadZipFile

        from benchab.controles.comparer_projets_3mf import comparer, resumer
        from benchab.qualite.ecritures import destination_neuve, ecrire_json_neuf
        from benchab.qualite.statuts import code_statut as code_statut_comparaison

        try:
            if arguments.resume and arguments.sortie is None:
                raise ValueError("--resume exige --sortie pour conserver le rapport complet.")
            rapport = comparer(arguments.source, arguments.candidat)
            sortie = None
            if arguments.sortie is not None:
                sortie = destination_neuve(arguments.sortie, prive=atelier_prive(arguments.prive), temporaire=False)
                ecrire_json_neuf(sortie, rapport, entrees=[arguments.source, arguments.candidat])
            affiche = resumer(rapport, sortie) if arguments.resume else rapport
            print(json.dumps(affiche, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
            return code_statut_comparaison(rapport["statut"])
        except (OSError, ValueError, KeyError, RuntimeError, BadZipFile) as erreur:
            print(f"BENCHAB : comparaison 3MF refusée : {erreur}", file=sys.stderr)
            return 2
    if arguments.commande == "controle" and arguments.controle == "entretien":
        try:
            from benchab.entretien.bilan import bilan_fichier, code
            from benchab.qualite.ecritures import destination_neuve, ecrire_json_neuf

            rapport = bilan_fichier(arguments.historique, atelier_prive(arguments.prive))
            if arguments.sortie:
                sortie = destination_neuve(arguments.sortie, prive=atelier_prive(arguments.prive), temporaire=False)
                ecrire_json_neuf(sortie, rapport, entrees=[arguments.historique])
            print(json.dumps(rapport, ensure_ascii=False, indent=2, allow_nan=False))
            return code(rapport)
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as erreur:
            print(f"BENCHAB : bilan d'entretien refusé : {erreur}", file=sys.stderr)
            return 2
    if arguments.commande == "controle" and arguments.controle == "appliquer-reparation-vue":
        try:
            fd = os.open(arguments.plan, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as fichier:
                avant = os.fstat(fichier.fileno())
                if not stat.S_ISREG(avant.st_mode) or avant.st_size > 8 * 1024 * 1024:
                    raise ValueError("Le plan doit être un fichier JSON régulier de 8 Mio au plus.")
                brut = fichier.read(8 * 1024 * 1024 + 1)
                apres = os.fstat(fichier.fileno())
                identite_avant = (avant.st_dev, avant.st_ino, avant.st_size, avant.st_mtime_ns, avant.st_ctime_ns)
                identite_apres = (apres.st_dev, apres.st_ino, apres.st_size, apres.st_mtime_ns, apres.st_ctime_ns)
                if len(brut) > 8 * 1024 * 1024 or identite_avant != identite_apres:
                    raise ValueError("Le plan est trop grand ou a changé pendant sa lecture.")

            def sans_doublons(paires):
                resultat = {}
                for cle, valeur in paires:
                    if cle in resultat:
                        raise ValueError("Clé dupliquée dans le plan.")
                    resultat[cle] = valeur
                return resultat

            rapport = json.loads(
                brut.decode("utf-8"),
                object_pairs_hook=sans_doublons,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Constante JSON interdite.")),
            )
            from benchab.qualite import plan_reparation

            # La réparation vérifie sa source SQLite en RO et n'enregistre donc ni mesure ni état (#119).
            if arguments.candidat:
                resultat = plan_reparation.appliquer_candidat_lien(
                    atelier_prive(arguments.prive), rapport, arguments.cible, arguments.candidat
                )
            else:
                resultat = plan_reparation.appliquer_vue(
                    atelier_prive(arguments.prive), rapport, arguments.cible
                )
            print(json.dumps(resultat, ensure_ascii=False, separators=(",", ":")))
            return 0
        except (OSError, ValueError, RuntimeError, sqlite3.Error, json.JSONDecodeError) as erreur:
            print(f"BENCHAB : réparation de vue refusée : {erreur}", file=sys.stderr)
            return 1
    if arguments.commande == "controle" and arguments.controle == "installation":
        from benchab.qualite.installation import diagnostiquer

        # Le diagnostic doit rester possible sur un atelier absent ou endommagé :
        # aucun Base() ni mesure, même en cas de refus (docs/reference/diagnostic-installation.md).
        try:
            if arguments.espace_minimum_mio < 0:
                raise ValueError("Le seuil d'espace doit être positif ou nul, en Mio")
            rapport = diagnostiquer(
                arguments.racine, base=atelier_prive(arguments.prive) / "benchab.sqlite",
                profils=tuple(arguments.profil), espace_minimum_octets=arguments.espace_minimum_mio * 1024**2,
                detail=arguments.detail,
            )
            print(json.dumps(rapport, ensure_ascii=False, indent=2 if arguments.detail else None))
            return rapport["code"]
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as erreur:
            print(f"BENCHAB : {erreur}", file=sys.stderr)
            return 2
    if arguments.commande == "fabrication" and arguments.action == "remplacer-maillage":
        from types import SimpleNamespace

        from benchab.fabrication.remplacement_maillage import main as remplacer_maillage

        # Le remplacement reste indépendant de SQLite et de ses mesures automatiques.
        base_ephemere = SimpleNamespace(prive=atelier_prive(arguments.prive))
        try:
            return _deleguer(remplacer_maillage, arguments.arguments, options={"base": base_ephemere})
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as erreur:
            print(f"BENCHAB : remplacement de maillage refusé : {erreur}", file=sys.stderr)
            return 1
    if arguments.commande == "visualisation" and arguments.action == "retour-photo":
        from types import SimpleNamespace

        # Import photo et confirmation ne créent ni base SQLite ni mesure automatique.
        base_ephemere = SimpleNamespace(prive=atelier_prive(arguments.prive))
        try:
            return _deleguer_namespace(arguments, base_ephemere)
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as erreur:
            print(f"BENCHAB : retour photo refusé : {erreur}", file=sys.stderr)
            return 2
    mesure = Mesure()
    base = None
    code = 1
    seance_id = None
    ancien_atelier = os.environ.get("BENCHAB_PRIVE")
    try:
        base = Base(atelier_prive(arguments.prive), creer=arguments.commande == "init")
        from benchab.donnees.seance import active_valide

        actif = active_valide(base.connexion)
        seance_id = actif["id"] if actif else None
        # Les modules enfants lisent le même choix que la CLI, sans laisser une
        # préférence persistante (docs/reference/chemins-et-atelier.md).
        os.environ["BENCHAB_PRIVE"] = str(base.prive)
        try:
            code = executer(arguments, base)
        except SystemExit as arret:
            code = _code_arret(arret)
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as erreur:
        print(f"BENCHAB : {erreur}", file=sys.stderr)
        code = 1
    finally:
        if ancien_atelier is None:
            os.environ.pop("BENCHAB_PRIVE", None)
        else:
            os.environ["BENCHAB_PRIVE"] = ancien_atelier
        if base is not None:
            try:
                mesure.enregistrer(base, commande, code, seance_id)
            except (OSError, ValueError, sqlite3.Error):
                print("BENCHAB : impossible d'enregistrer la mesure et son export.", file=sys.stderr)
                code = 1
                try:
                    mesure.enregistrer(base, commande, code, seance_id)
                except (OSError, ValueError, sqlite3.Error):
                    print("BENCHAB : reprise de la mesure ou de son export incomplète.", file=sys.stderr)
            finally:
                base.fermer()
    return code


if __name__ == "__main__":
    sys.exit(main())
