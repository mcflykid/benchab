"""Compare les réglages d’un 3MF à des profils explicitement fournis."""

import argparse
import hashlib
import json
import math
import os
import stat
from pathlib import Path

from benchab import integrite_3mf, reglages
from benchab.chemins import sans_lien
from benchab.controles import coherence_profils
from benchab.machine.profil_maitre import resoudre_preset
from benchab.qualite.ecritures import ecrire_json_neuf, sortie_privee
from benchab.qualite.empreintes import sha256_fichier

SENSIBLES = {
    "debit": ("flow", "volumetric"),
    "vitesse": ("speed",),
    "temperature": ("temperature", "temp_", "bed_temp"),
    "acceleration": ("acceleration",),
    "materiel": ("nozzle", "filament", "material", "bed_type"),
}
PORTÉES = ("projet/", "objet/", "plaque/")
MAX_PROFIL_OCTETS = 8 * 1024 * 1024


def _lire_profil(chemin):
    """Décode un profil typé depuis au plus 8 Mio et renvoie le SHA des octets décodés."""
    fichier = sans_lien(chemin, strict=True)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descripteur = os.open(fichier, flags)
    with os.fdopen(descripteur, "rb") as flux:
        avant = os.fstat(flux.fileno())
        if not stat.S_ISREG(avant.st_mode):
            raise ValueError("Fichier de profil ordinaire requis : " + str(fichier))
        if avant.st_size > MAX_PROFIL_OCTETS:
            raise ValueError("Profil trop volumineux (limite 8 Mio) : " + str(fichier))
        contenu = flux.read(MAX_PROFIL_OCTETS + 1)
        apres = os.fstat(flux.fileno())
        if len(contenu) > MAX_PROFIL_OCTETS:
            raise ValueError("Profil trop volumineux (limite 8 Mio) : " + str(fichier))
        if (avant.st_dev, avant.st_ino, avant.st_size, avant.st_mtime_ns) != (
            apres.st_dev, apres.st_ino, apres.st_size, apres.st_mtime_ns
        ) or len(contenu) != apres.st_size:
            raise ValueError("Profil modifié pendant sa lecture : " + str(fichier))
    donnees = reglages.decoder(contenu.decode("utf-8"))
    return donnees, hashlib.sha256(contenu).hexdigest()


def _charger_profils(fichiers, dossiers):
    """Résout les presets explicitement choisis et leurs parents ; renvoie les empreintes lues."""
    resultats, empreintes, chemins, debut_profils = {}, {}, {}, {}
    sources, chemins_noms, bruts = {}, {}, {}
    for brut in fichiers:
        fichier = sans_lien(Path(brut).expanduser(), strict=True)
        if not fichier.is_file():
            raise ValueError("Profil ordinaire requis : " + str(brut))
        donnees, debut = _lire_profil(fichier)
        debut_profils[str(fichier)] = debut
        categorie, nom = donnees.get("type"), donnees.get("name")
        if categorie not in {"machine", "process", "filament"} or not isinstance(nom, str) or not nom:
            raise ValueError("Profil explicite de type machine, process ou filament requis : " + str(fichier))
        if nom in resultats:
            raise ValueError("Nom de profil répété : " + nom)
        if categorie != "filament" and any(p["categorie"] == categorie for p in resultats.values()):
            raise ValueError("Un seul profil explicite par famille est accepté : " + categorie)
        sources[nom] = dict(donnees)
        chemins_noms[nom] = fichier
        bruts[nom] = donnees
        resultats[nom] = {"categorie": categorie, "chemin": fichier}
    racines = [*sorted({p["chemin"].parent for p in resultats.values()}, key=str)]
    for dossier in dossiers:
        racine = sans_lien(Path(dossier).expanduser(), strict=True)
        if not racine.is_dir():
            raise ValueError("Dossier explicite de presets requis : " + str(dossier))
        if racine not in racines:
            racines.append(racine)
    en_attente = [parent for donnees in bruts.values() for parent in _parents(donnees)]
    while en_attente:
        nom_parent = en_attente.pop()
        if nom_parent in sources:
            continue
        candidats = []
        for racine in racines:
            fichier_parent = sans_lien(racine / (nom_parent + ".json"))
            if fichier_parent.is_file():
                candidats.append(fichier_parent)
        if len(candidats) > 1:
            raise ValueError("Profil parent ambigu dans les dossiers explicites : " + nom_parent)
        if not candidats:
            continue
        source_parent = sans_lien(candidats[0], strict=True)
        donnees_parent, empreinte_parent = _lire_profil(source_parent)
        if donnees_parent.get("name", nom_parent) != nom_parent:
            raise ValueError("Identité du profil parent incohérente : " + nom_parent)
        debut_profils[str(source_parent)] = empreinte_parent
        sources[nom_parent] = dict(donnees_parent)
        chemins_noms[nom_parent] = source_parent
        bruts[nom_parent] = donnees_parent
        en_attente.extend(_parents(donnees_parent))
    for nom, profil in resultats.items():
        categorie = profil["categorie"]
        resolution = resoudre_preset(nom, categorie, sources)
        for origine in resolution["heritages"]:
            chemin = chemins_noms.get(origine["name"])
            if chemin is None or origine["empreinte_type"] != "JSON canonique en mémoire":
                raise ValueError("Profil résolu sans source explicite ou empreinte typée : " + origine["name"])
            chemin = sans_lien(chemin, strict=True)
            empreinte = debut_profils.get(str(chemin))
            empreinte_canonique = hashlib.sha256(json.dumps(
                sources[origine["name"]], sort_keys=True, ensure_ascii=False, allow_nan=False
            ).encode()).hexdigest()
            # resoudre_preset hashe la table typée en mémoire ; le SHA octet couvre sa source exacte (#38).
            if empreinte is None or empreinte_canonique != origine["sha256"]:
                raise ValueError("Profil incohérent pendant sa résolution : " + str(chemin))
            empreintes[str(chemin)] = empreinte
            chemins[str(chemin)] = chemin
        profil["resolution"] = resolution
        profil["fichiers_par_nom"] = chemins_noms
        profil["sha_par_nom"] = {
            nom_source: debut_profils[str(chemin_source)]
            for nom_source, chemin_source in chemins_noms.items()
            if str(chemin_source) in debut_profils
        }
        profil["contexte_filaments"] = reglages.contexte_filaments(
            {**dict(resolution["valeurs"]), "filament_settings_id": [nom]}
        ) if categorie == "filament" else None
    return resultats, chemins, empreintes


def _parents(donnees):
    """Retourne les noms d’héritage déclarés sans en inventer ni les normaliser."""
    parents = donnees.get("inherits") or []
    parents = [parents] if isinstance(parents, str) else parents
    if not isinstance(parents, list):
        raise ValueError("Héritage invalide dans un profil explicite.")
    for nom in parents:
        if (
            not isinstance(nom, str) or not nom or Path(nom).name != nom
            or nom in (".", "..") or "\\" in nom
        ):
            raise ValueError("Nom de preset hors périmètre : " + str(nom))
    return parents


def _sensibilites(cle):
    """Classe une clé typée par domaines sensibles, sans en déduire son effet physique."""
    nom = cle.lower()
    return [domaine for domaine, fragments in SENSIBLES.items() if any(x in nom for x in fragments)]


def _forme_connue(valeur, definition):
    # Deux valeurs identiques dans une forme hors dictionnaire ne prouvent pas l'égalité typée (#38).
    formes = definition.get("formes_certifiees", definition.get("formes", ()))
    return definition.get("type") not in (None, "inconnu") and (not formes or type(valeur).__name__ in formes)


def comparer(archive, fichiers_profils, *, dossiers_presets=(), avant_intervention=None, champs_manuels=()):
    """Compare les portées projet/objet/partie aux presets explicitement fournis.

    Les valeurs restent dans leurs unités natives. Retourne PASS, WARN ou
    NON VÉRIFIÉ, les sources par clé et les SHA avant/après ; aucune écriture.
    Une déclaration manuelle reste NON VÉRIFIÉE sauf delta scalaire typé entre
    archives. Elle n’authentifie ni auteur ni geste.
    """
    archive = sans_lien(Path(archive).expanduser(), strict=True)
    sha_3mf_avant = sha256_fichier(archive)
    if isinstance(champs_manuels, str):
        raise ValueError("--champ-manuel doit être répété, pas fourni comme chaîne unique.")
    champs_manuels = tuple(champs_manuels)
    for chemin in champs_manuels:
        if not isinstance(chemin, str) or not chemin or "/" not in chemin:
            raise ValueError("Un champ manuel doit être un chemin typé exact, par exemple projet/outer_wall_speed.")
    if len(champs_manuels) != len(set(champs_manuels)):
        raise ValueError("Un champ manuel ne peut être déclaré qu’une fois.")
    avant_path = None
    sha_intervention_avant = None
    etat_avant, types_avant = {}, {}
    if avant_intervention is not None:
        avant_path = sans_lien(Path(avant_intervention).expanduser(), strict=True)
        sha_intervention_avant = sha256_fichier(avant_path)
        etat_avant, types_avant = integrite_3mf.instantane(avant_path, avec_types=True)
    profils, chemins, sha_avant = _charger_profils(fichiers_profils, dossiers_presets)
    etat, types = integrite_3mf.instantane(archive, avec_types=True)
    projet = {cle.removeprefix("projet/"): valeur for cle, valeur in etat.items() if cle.startswith("projet/")}
    contexte = reglages.contexte_filaments(projet)
    comparaisons, différences, non_verifies = [], [], []

    def ligne_nv(chemin, cle, raison, valeur=None):
        non_verifies.append({
            "chemin": chemin, "cle": cle, "valeur_3mf": valeur,
            "statut": "NON VÉRIFIÉ", "raison": raison,
            "provenance_3mf": {"source": str(archive), "chemin": chemin},
            "modification_manuelle": "NON VÉRIFIÉ",
        })

    for chemin, valeur in sorted(etat.items()):
        if not chemin.startswith(PORTÉES):
            continue
        definition = types.get(chemin, {})
        cle = chemin.rsplit("/", 1)[-1]
        if cle == "name" and chemin.startswith("objet/"):
            # Le nom identifie l’objet XML ; ce n’est pas un réglage de slicer à comparer.
            continue
        if not _forme_connue(valeur, definition):
            ligne_nv(chemin, cle, "Clé ou forme non qualifiée par le dictionnaire typé.", valeur)
            continue
        familles = definition.get("familles", [])
        categories = [famille for famille in ("machine", "process", "filament") if famille in familles]
        if not categories:
            continue
        if "filament" in categories:
            if not chemin.startswith("projet/") or contexte["statut_contexte_filaments"] != "PASS":
                ligne_nv(chemin, cle, "Identités de slots filament absentes ou ambiguës.", valeur)
                continue
            ids = contexte["correspondances_filaments"].get(cle)
            if ids is None or not isinstance(valeur, list) or len(ids) != len(valeur):
                ligne_nv(chemin, cle, "Correspondance filament typée absente ou de cardinalité différente.", valeur)
                continue
            for indice, identite in enumerate(ids):
                preset = profils.get(identite)
                if preset is None or preset["categorie"] != "filament":
                    ligne_nv(
                        chemin, cle,
                        "Profil filament explicite absent pour l’identité du slot " + identite,
                        valeur[indice],
                    )
                    continue
                valeur_profil = preset["resolution"]["valeurs"].get(cle)
                definition_profil = reglages.definition(cle, valeur=valeur_profil)
                if (
                    not _forme_connue(valeur_profil, definition_profil)
                    or "filament" not in definition_profil.get("familles", [])
                ):
                    ligne_nv(chemin, cle, "Forme ou famille du profil filament NON VÉRIFIÉE.", valeur[indice])
                    continue
                if (
                    preset["contexte_filaments"]["statut_contexte_filaments"] != "PASS"
                    or preset["contexte_filaments"]["correspondances_filaments"].get(cle) != [identite]
                ):
                    ligne_nv(
                        chemin, cle,
                        "Identité ou cardinalité du profil filament NON VÉRIFIÉE.",
                        valeur[indice],
                    )
                    continue
                valeur_profil = reglages.valeur_brute_indice(
                    preset["resolution"]["valeurs"], cle, 0, lecteur="index_exact"
                )
                if valeur_profil is None:
                    ligne_nv(chemin, cle, "Valeur de slot absente du profil filament.", valeur[indice])
                    continue
                if type(valeur[indice]) is not type(valeur_profil):
                    ligne_nv(chemin, cle, "Formes natives différentes, aucune normalisation appliquée.", valeur[indice])
                    continue
                ligne = _comparaison(
                    chemin, cle, valeur[indice], valeur_profil, preset,
                    slot_id=identite, slot=indice + 1, archive=archive,
                )
                comparaisons.append(ligne)
                if ligne["statut"] == "DIFFÉRENT":
                    différences.append(ligne)
            continue
        compare = False
        for categorie in categories:
            preset = next((p for p in profils.values() if p["categorie"] == categorie), None)
            if preset is None:
                continue
            compare = True
            valeurs = preset["resolution"]["valeurs"]
            if cle not in valeurs:
                ligne_nv(chemin, cle, "Clé absente des valeurs résolues du profil explicite.", valeur)
                continue
            profil_valeur = valeurs[cle]
            definition_profil = reglages.definition(cle, valeur=profil_valeur)
            if (
                not _forme_connue(profil_valeur, definition_profil)
                or categorie not in definition_profil.get("familles", [])
            ):
                ligne_nv(chemin, cle, "Forme ou famille du profil NON VÉRIFIÉE.", valeur)
                continue
            if type(valeur) is not type(profil_valeur):
                ligne_nv(chemin, cle, "Formes natives différentes, aucune normalisation appliquée.", valeur)
                continue
            ligne = _comparaison(chemin, cle, valeur, profil_valeur, preset, archive=archive)
            comparaisons.append(ligne)
            if ligne["statut"] == "DIFFÉRENT":
                différences.append(ligne)
        if not compare:
            ligne_nv(chemin, cle, "Aucun profil explicite ne couvre la famille typée.", valeur)

    for profil in profils.values():
        resolution = profil["resolution"]
        for parent in resolution["manquants"]:
            nom_profil = resolution["valeurs"].get("name", "")
            non_verifies.append({
                "cle": None, "chemin": None, "statut": "NON VÉRIFIÉ",
                "raison": f"Héritage absent du preset {nom_profil} : {parent}",
                "profil": {"name": parent}, "modification_manuelle": "NON VÉRIFIÉ",
            })
    interventions = []
    for chemin in champs_manuels:
        avant = etat_avant.get(chemin)
        apres = etat.get(chemin)
        type_avant = types_avant.get(chemin, {})
        type_apres = types.get(chemin, {})
        raison = None
        valide = avant_intervention is not None
        if not valide:
            raison = "Archive antérieure absente."
        elif chemin not in etat_avant:
            valide, raison = False, "Champ absent de l’instantané avant."
        elif chemin not in etat:
            valide, raison = False, "Champ absent de l’instantané après."
        elif type(avant) not in (str, int, float, bool) or type(apres) not in (str, int, float, bool):
            valide, raison = False, "Le champ n’est pas scalaire ; slot ou liste ambiguë."
        elif (type(avant) is float and not math.isfinite(avant)) or (type(apres) is float and not math.isfinite(apres)):
            valide, raison = False, "Valeur scalaire non finie."
        elif not _forme_connue(avant, type_avant) or not _forme_connue(apres, type_apres):
            valide, raison = False, "Forme ou définition typée inconnue."
        elif type_avant.get("type") != type_apres.get("type") or type(avant) is not type(apres):
            valide, raison = False, "Types natifs ou définitions avant/après différents."
        elif avant == apres:
            valide, raison = False, "Aucun changement typé constaté."
        if valide:
            statut_intervention = "DÉCLARÉE — ÉCART VÉRIFIÉ"
            raison = "Déclaration explicite et delta scalaire typé constaté ; auteur et geste non authentifiés."
            for ligne in comparaisons:
                if ligne["chemin"] == chemin:
                    ligne["modification_manuelle"] = statut_intervention
        else:
            statut_intervention = "NON VÉRIFIÉ"
        interventions.append({
            "chemin": chemin, "statut": statut_intervention, "raison": raison,
            "valeur_avant": avant, "valeur_apres": apres,
            "provenance_avant": ({"source": str(avant_path), "sha256": sha_intervention_avant, "chemin": chemin}
                                  if avant_path is not None else None),
            "provenance_apres": {"source": str(archive), "sha256": sha_3mf_avant, "chemin": chemin},
        })
    sha_apres = {nom: _lire_profil(chemin)[1] for nom, chemin in chemins.items()}
    sha_3mf_apres = sha256_fichier(archive)
    sha_intervention_apres = sha256_fichier(avant_path) if avant_path is not None else None
    if sha_3mf_apres != sha_3mf_avant or sha_apres != sha_avant or sha_intervention_apres != sha_intervention_avant:
        raise ValueError("Archive ou profil modifié pendant la comparaison.")
    if not comparaisons and not non_verifies:
        non_verifies.append({"cle": None, "chemin": None, "statut": "NON VÉRIFIÉ",
                             "raison": "Aucun réglage qualifié n’a pu être comparé.",
                             "modification_manuelle": "NON VÉRIFIÉ"})
    statut = "NON VÉRIFIÉ" if non_verifies else "WARN" if différences else "PASS"
    return {
        "schema": 1, "statut": statut, "archive": str(archive),
        "sha256_3mf_avant": sha_3mf_avant, "sha256_3mf_apres": sha_3mf_apres,
        "profils_sha256_avant": sha_avant, "profils_sha256_apres": sha_apres,
        "comparaisons": comparaisons, "differences": différences, "non_verifies": non_verifies,
        "interventions_manuelles": interventions,
        "coherence": coherence_profils.evaluer(comparaisons, contexte, sha_3mf_avant),
        "profils": [
            {"categorie": p["categorie"], "name": nom,
             "heritages": [
                 {
                     **heritage,
                     "fichier": str(p["fichiers_par_nom"][heritage["name"]]),
                     "sha256_fichier": p["sha_par_nom"][heritage["name"]],
                 }
                 for heritage in p["resolution"]["heritages"]
             ],
             "manquants": p["resolution"]["manquants"]}
            for nom, p in sorted(profils.items())
        ],
        "contexte_filaments": contexte,
        "limites": [
            "Une intervention déclarée atteste seulement un delta scalaire explicitement demandé entre deux archives ; "
            "elle n’authentifie ni auteur ni geste.",
            "Une clé, forme ou correspondance non qualifiée reste NON VÉRIFIÉE.",
            "Aucune conclusion fabricant, compatibilité physique, ni validation d’impression.",
            "Les profils fournis et leurs parents sont lus en lecture seule.",
        ],
    }


def _comparaison(chemin, cle, valeur_3mf, valeur_profil, preset, *, slot_id=None, slot=None, archive):
    """Décrit une égalité ou un écart exact et ses deux origines, avec sensibilité lexicale."""
    domaines = _sensibilites(cle)
    origine = preset["resolution"]["origine_par_cle"].get(cle)
    fichier_origine = preset["fichiers_par_nom"].get(origine, preset["chemin"])
    ligne = {
        "chemin": chemin, "portee": _portee(chemin), "cle": cle,
        "valeur_3mf": valeur_3mf, "valeur_profil": valeur_profil,
        "statut": "IDENTIQUE" if valeur_3mf == valeur_profil else "DIFFÉRENT",
        "sensible": bool(domaines), "domaines_sensibles": domaines,
        "profil": {
            "name": preset["resolution"]["valeurs"].get("name"),
            "categorie": preset["categorie"],
            "source": str(fichier_origine),
            "sha256": preset["sha_par_nom"].get(origine),
        },
        "provenance_3mf": {"source": str(archive), "chemin": chemin},
        "modification_manuelle": "NON VÉRIFIÉ",
    }
    if slot_id is not None:
        ligne["slot_id"] = slot_id
        ligne["slot"] = slot
    return ligne


def _portee(chemin):
    """Nom propre la portée 3MF, en séparant les métadonnées de partie de l’objet parent."""
    if chemin.startswith("projet/"):
        return "projet"
    if chemin.startswith("plaque/"):
        return "plaque"
    return "partie" if "/part/" in chemin else "objet"


def main(argv=None, *, prive=None):
    """Produit un rapport privé ; codes PASS 0, WARN 1, NON VÉRIFIÉ 2, sans modifier profils/3MF."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--profil", type=Path, action="append", required=True)
    parser.add_argument("--dossier-presets", type=Path, action="append", default=[])
    parser.add_argument("--avant-intervention", type=Path)
    parser.add_argument("--champ-manuel", action="append", default=[])
    parser.add_argument("--rapport", type=Path, required=True)
    parser.add_argument("--detail", action="store_true")
    parser.add_argument("--prive", type=Path, default=prive)
    args = parser.parse_args(argv)
    try:
        rapport = comparer(args.archive, args.profil, dossiers_presets=args.dossier_presets,
                           avant_intervention=args.avant_intervention, champs_manuels=args.champ_manuel)
        cible = sortie_privee(args.rapport, prive=args.prive)
        entrees = [args.archive, *args.profil]
        if args.avant_intervention is not None:
            entrees.append(args.avant_intervention)
        entrees.extend(
            heritage["fichier"]
            for profil in rapport["profils"]
            for heritage in profil["heritages"]
        )
        ecrire_json_neuf(cible, rapport, entrees=entrees)
        # L’affichage court reste compatible avec les lecteurs du rapport de profil (#35).
        affiche = rapport if args.detail else {
            "statut": rapport["statut"], "coherence": rapport["coherence"]["statut"], "rapport": str(cible),
        }
        print(json.dumps(affiche, ensure_ascii=False))
        return {"PASS": 0, "WARN": 1, "NON VÉRIFIÉ": 2}[rapport["statut"]]
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as erreur:
        parser.error(str(erreur))
