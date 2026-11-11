"""Compare les réglages d’un 3MF à des profils explicitement fournis."""

import argparse
import json
from pathlib import Path

from benchab import integrite_3mf, reglages
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


def _charger_profils(fichiers, dossiers):
    """Résout les presets explicitement choisis et leurs parents ; renvoie les empreintes lues."""
    resultats, empreintes, chemins, debut_profils = {}, {}, {}, {}
    sources, bruts = {}, {}
    for brut in fichiers:
        fichier = Path(brut).expanduser().resolve(strict=True)
        if not fichier.is_file() or fichier.is_symlink():
            raise ValueError("Profil ordinaire requis : " + str(brut))
        debut = sha256_fichier(fichier)
        debut_profils[str(fichier)] = debut
        donnees = reglages.decoder(fichier.read_text(encoding="utf-8"))
        categorie, nom = donnees.get("type"), donnees.get("name")
        if categorie not in {"machine", "process", "filament"} or not isinstance(nom, str) or not nom:
            raise ValueError("Profil explicite de type machine, process ou filament requis : " + str(fichier))
        if nom in resultats:
            raise ValueError("Nom de profil répété : " + nom)
        if categorie != "filament" and any(p["categorie"] == categorie for p in resultats.values()):
            raise ValueError("Un seul profil explicite par famille est accepté : " + categorie)
        sources[nom] = fichier
        bruts[nom] = donnees
        debut_profils[nom] = debut
        resultats[nom] = {"categorie": categorie, "chemin": fichier}
    racines = [*sorted({str(p["chemin"].parent) for p in resultats.values()})]
    for dossier in dossiers:
        racine = Path(dossier).expanduser().resolve(strict=True)
        if not racine.is_dir():
            raise ValueError("Dossier explicite de presets requis : " + str(dossier))
        racines.append(str(racine))
    en_attente = [parent for donnees in bruts.values() for parent in _parents(donnees)]
    while en_attente:
        nom_parent = en_attente.pop()
        if nom_parent in sources:
            continue
        for racine in racines:
            fichier_parent = Path(racine) / (nom_parent + ".json")
            if fichier_parent.is_file() and not fichier_parent.is_symlink():
                donnees_parent = reglages.decoder(fichier_parent.read_text(encoding="utf-8"))
                if donnees_parent.get("name", nom_parent) != nom_parent:
                    raise ValueError("Identité du profil parent incohérente : " + nom_parent)
                source_parent = fichier_parent.resolve(strict=True)
                empreinte_parent = sha256_fichier(source_parent)
                if empreinte_parent != sha256_fichier(fichier_parent):
                    raise ValueError("Profil parent modifié pendant sa lecture : " + str(source_parent))
                debut_profils[str(source_parent)] = empreinte_parent
                sources[nom_parent] = source_parent
                bruts[nom_parent] = donnees_parent
                en_attente.extend(_parents(donnees_parent))
                break
    for nom, profil in resultats.items():
        categorie, fichier = profil["categorie"], profil["chemin"]
        resolution = resoudre_preset(nom, categorie, sources)
        for origine in resolution["heritages"]:
            source = origine["source"]
            if origine["empreinte_type"] != "octets du fichier source":
                raise ValueError("Profil résolu sans empreinte d’octets : " + origine["name"])
            chemin = Path(source).resolve(strict=True)
            empreinte = debut_profils.get(str(chemin))
            # Le SHA de resoudre_preset lie l’objet résolu aux octets de référence (#38).
            if empreinte is None or empreinte != origine["sha256"]:
                raise ValueError("Profil modifié pendant sa résolution : " + str(chemin))
            empreintes[str(chemin)] = empreinte
            chemins[str(chemin)] = chemin
        if str(fichier) not in empreintes:
            raise ValueError("Le profil sélectionné n’apparaît pas dans sa résolution : " + str(fichier))
        if empreintes[str(fichier)] != debut_profils[str(fichier)]:
            raise ValueError("Profil modifié pendant sa lecture : " + str(fichier))
        profil["resolution"] = resolution
        profil["contexte_filaments"] = reglages.contexte_filaments(
            {**dict(resolution["valeurs"]), "filament_settings_id": [nom]}
        ) if categorie == "filament" else None
    return resultats, chemins, empreintes


def _parents(donnees):
    """Retourne les noms d’héritage déclarés sans en inventer ni les normaliser."""
    parents = donnees.get("inherits") or []
    parents = [parents] if isinstance(parents, str) else parents
    return [nom for nom in parents if isinstance(nom, str) and nom]


def _sensibilites(cle):
    """Classe une clé typée par domaines sensibles, sans en déduire son effet physique."""
    nom = cle.lower()
    return [domaine for domaine, fragments in SENSIBLES.items() if any(x in nom for x in fragments)]


def comparer(archive, fichiers_profils, *, dossiers_presets=()):
    """Compare les portées projet/objet/partie aux presets explicitement fournis.

    Les valeurs restent dans leurs unités natives. Retourne PASS, WARN ou
    NON VÉRIFIÉ, les sources par clé et les SHA avant/après ; aucune écriture.
    Les écarts ne prouvent jamais une modification manuelle ni un fait fabricant.
    """
    archive = Path(archive).resolve(strict=True)
    sha_3mf_avant = sha256_fichier(archive)
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
        if definition.get("type") == "inconnu":
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
                if definition_profil["type"] == "inconnu" or "filament" not in definition_profil.get("familles", []):
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
                    slot_id=identite, archive=archive,
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
            if definition_profil["type"] == "inconnu" or categorie not in definition_profil.get("familles", []):
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
    sha_apres = {nom: sha256_fichier(chemin) for nom, chemin in chemins.items()}
    sha_3mf_apres = sha256_fichier(archive)
    if sha_3mf_apres != sha_3mf_avant or sha_apres != sha_avant:
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
        "profils": [
            {"categorie": p["categorie"], "name": nom,
             "heritages": p["resolution"]["heritages"],
             "manquants": p["resolution"]["manquants"]}
            for nom, p in sorted(profils.items())
        ],
        "contexte_filaments": contexte,
        "limites": [
            "Les différences sont des observations numériques en unités natives, pas des changements manuels établis.",
            "Une clé, forme ou correspondance non qualifiée reste NON VÉRIFIÉE.",
            "Aucune conclusion fabricant, compatibilité physique, ni validation d’impression.",
            "Les profils fournis et leurs parents sont lus en lecture seule.",
        ],
    }


def _comparaison(chemin, cle, valeur_3mf, valeur_profil, preset, *, slot_id=None, archive):
    """Décrit une égalité ou un écart exact et ses deux origines, avec sensibilité lexicale."""
    domaines = _sensibilites(cle)
    ligne = {
        "chemin": chemin, "portee": _portee(chemin), "cle": cle,
        "valeur_3mf": valeur_3mf, "valeur_profil": valeur_profil,
        "statut": "IDENTIQUE" if valeur_3mf == valeur_profil else "DIFFÉRENT",
        "sensible": bool(domaines), "domaines_sensibles": domaines,
        "profil": {
            "name": preset["resolution"]["valeurs"].get("name"),
            "source": preset["resolution"]["origine_par_cle"].get(cle, str(preset["chemin"])),
        },
        "provenance_3mf": {"source": str(archive), "chemin": chemin},
        "modification_manuelle": "NON VÉRIFIÉ",
    }
    if slot_id is not None:
        ligne["slot_id"] = slot_id
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
    parser.add_argument("--rapport", type=Path, required=True)
    parser.add_argument("--prive", type=Path, default=prive)
    args = parser.parse_args(argv)
    try:
        rapport = comparer(args.archive, args.profil, dossiers_presets=args.dossier_presets)
        cible = sortie_privee(args.rapport, prive=args.prive)
        entrees = [args.archive, *args.profil]
        entrees.extend(
            heritage["source"]
            for profil in rapport["profils"]
            for heritage in profil["heritages"]
            if heritage["empreinte_type"] == "octets du fichier source"
        )
        ecrire_json_neuf(cible, rapport, entrees=entrees)
        print(json.dumps({"statut": rapport["statut"], "rapport": str(cible)}, ensure_ascii=False))
        return {"PASS": 0, "WARN": 1, "NON VÉRIFIÉ": 2}[rapport["statut"]]
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as erreur:
        parser.error(str(erreur))
