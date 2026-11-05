"""Archives et journal de retour privés ; aucune impression ni commande machine implicite."""

import argparse
import json
import sys
import time
from pathlib import Path

from benchab.donnees.base import Base
from benchab.fabrication import envois_slicer as ES
from benchab.fabrication import preparation as PR
from benchab.projets.envois import enregistrer as associer
from benchab.projets.envois import lire_fiche


def enregistres(fiche):
    return {e["sha256"] for e in [fiche.get("envoi") or {}] + fiche.get("envois_supplementaires", []) if e}


def correspondants(fiche, archive):
    """Envois archivés, non encore enregistrés, dont une plaque a les commandes d'une plaque de la préparation."""
    deja = enregistres(fiche)
    out, autres = [], []
    fiche_envois = archive / "ENVOIS.json"
    for e in json.loads(fiche_envois.read_text()) if fiche_envois.exists() else []:
        p = archive / e["archive"]
        try:
            p.resolve().relative_to(archive.resolve())
        except ValueError:
            continue
        if e["sha256"] in deja or not p.is_file() or p.is_symlink() or ES.sha(p) != e["sha256"]:
            continue
        try:
            g = PR.gcodes(p)
        except Exception:
            continue
        comparaisons = PR.comparer_envoi(g, fiche["gcode"])
        unique = bool(comparaisons) and all(v.startswith("commandes identiques") for v in comparaisons.values())
        (out if unique else autres).append(p)
    return out, autres


def enregistrer(base, projet, ident, fichiers):
    faits = []
    for fichier in fichiers:
        fiche = lire_fiche(base, projet, ident)
        try:
            resultat = associer(base, projet, ident, fichier, ajouter=bool(fiche.get("envoi")))
            faits.append((Path(fichier).name, True, json.dumps(resultat["comparaison"], ensure_ascii=False)))
        except (ValueError, OSError) as erreur:
            faits.append((Path(fichier).name, False, str(erreur)))
    return faits


def reglages(fichier):
    try:
        export = ES.lire(Path(fichier))
    except (ValueError, OSError) as erreur:
        return f"illisible ({erreur})"

    def valeur(donnee):
        return ", ".join(map(str, donnee)) if isinstance(donnee, list) else ("—" if donnee is None else str(donnee))

    reg = {cle: valeur(v) for cle, v in export["reglages"].items()}
    temp = {cle: valeur(v) for cle, v in (export.get("temperature_appliquee") or {}).items()}
    return (
        f"{reg.get('filament_settings_id', '—')} ; buse déclarée {valeur(export.get('buse_declaree'))} ; "
        f"buse appliquée {temp.get('premiere_couche', '—')} puis {temp.get('suite', '—')} °C ; "
        f"plateau {reg.get('textured_plate_temp_initial_layer', '—')}/{reg.get('textured_plate_temp', '—')} °C ; "
        f"flow {reg.get('filament_flow_ratio', '—')} ; couche {reg.get('layer_height', '—')} ; "
        f"remplissage {reg.get('sparse_infill_density', '—')} ; filament {valeur(export.get('filament_utilise_g'))} g"
    )


def journal(projet, ident, fiche, *, prive):
    racine = Path(prive)
    dossier = racine / "suivi/journal" / time.strftime("%Y-%m")
    chemin = dossier / f"{time.strftime('%Y-%m-%d')}-impression-{projet.name}-{ident.lower()}.md"
    ancien = dossier / f"{time.strftime('%Y-%m-%d')}-{projet.name}-{ident.lower()}-retour.md"
    for existant in (ancien, chemin):
        if existant.exists():
            return existant, False

    def source(motif):
        return next((racine / nom for nom in fiche.get("sources", {}) if nom.endswith(motif)), None)

    declaration = source("DECLARATION-ENVOI.json")
    revue = source("REVUE-ZONES-LOCALES.json")
    filaments = "déclaration introuvable"
    if declaration and declaration.is_file():
        entrees = json.loads(declaration.read_text()).get("filaments", {})
        filaments = "; ".join(
            f"filament {cle} : {v.get('matiere')}, ACE {v.get('emplacement_ace')}, "
            f"{v.get('temp_1re')}/{v.get('temp')} °C"
            for cle, v in entrees.items()
        )
    zones = "revue locale introuvable"
    if revue and revue.is_file():
        entrees = json.loads(revue.read_text()).get("zones", {})
        humains = sum(not v.get("automatique") for v in entrees.values())
        zones = f"{len(entrees)} zones revues avant envoi ({humains} par décision écrite)"
    lignes = [
        f"# Retour d'impression — {projet.name} {ident}",
        "",
        f"Date : {time.strftime('%Y-%m-%d')}",
        "Type : impression",
        "Niveau de preuve : À VÉRIFIER",
        "Statut : À COMPLÉTER",
        "",
        "## Faits des fichiers",
        "",
        f"Préparation : {ident}",
        f"Filaments déclarés : {filaments}",
        f"Revues : {zones}",
        "Les fichiers archivés ne prouvent ni démarrage ni réussite physique.",
    ]
    envois = ([fiche["envoi"]] + fiche.get("envois_supplementaires", [])) if fiche.get("envoi") else []
    for envoi in envois:
        fichier = racine / envoi["fichier"]
        comparaison = ", ".join(f"plaque {k} : {v}" for k, v in envoi["comparaison"].items())
        lignes.extend(
            [
                f"- Copie `{fichier.name}` ({envoi['date']}) : {comparaison}.",
                f"  Réglages embarqués : {reglages(fichier)}.",
            ]
        )
    if not envois:
        lignes.append("Aucun envoi associé ; désigner explicitement --fichier pour une copie connue.")
    lignes.extend(
        [
            "",
            "## Observé par l'utilisateur (À COMPLÉTER)",
            "",
            "| Plaque | Résultat (réussie / défaut / arrêtée) | Défauts et endroit | Photos |",
            "|---|---|---|---|",
        ]
    )
    lignes.extend(f"| {numero} | À COMPLÉTER | | |" for numero in sorted(fiche.get("gcode", {}), key=int))
    lignes.extend(
        [
            "",
            "- Bobine réellement chargée et emplacement ACE : À COMPLÉTER.",
            "- Retrait des supports et marques : À COMPLÉTER.",
            "- Assemblage ou fonctionnement : À COMPLÉTER.",
            "",
            "## Écart avec l'estimation de réussite",
            "",
            "- Estimation avant impression et sa base : À COMPLÉTER.",
            "- Résultat, écart et cause supposée ou vérifiée : À COMPLÉTER.",
            "",
            "## Ce qui change pour la suite",
            "",
            "- Connaissance à capitaliser, avec conditions et limites : À COMPLÉTER.",
            "- Erreur et contrôle à ajouter : À COMPLÉTER.",
            "",
        ]
    )
    dossier.mkdir(parents=True, exist_ok=True)
    try:
        with chemin.open("x") as fichier:
            fichier.write("\n".join(lignes))
    except FileExistsError:
        return chemin, False
    return chemin, True


def main(argv=None, *, base=None):
    """Enregistre un retour dans la base existante ; faits/statuts déclarés conservés, aucune preuve physique
    inventée."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prive", type=Path)
    parser.add_argument("--projet", type=Path, required=True)
    parser.add_argument("--id")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--fichier", action="append", type=Path, default=[])
    parser.add_argument("--resultat")
    parser.add_argument("--preuve-retour")
    parser.add_argument("--reference-imprimee", action="append", default=[])
    parser.add_argument("--remplace-reference", action="append", default=[])
    parser.add_argument("--ensemble-complet", action="store_true")
    arguments = parser.parse_args(argv)
    possede = base is None
    if possede:
        if arguments.prive is None:
            parser.error("--prive explicite requis")
        base = Base(arguments.prive, creer=False)
    try:
        projet = arguments.projet if arguments.projet.is_absolute() else base.prive / arguments.projet
        projet = projet.resolve(strict=True)
        if not projet.is_relative_to(base.prive.resolve()):
            raise ValueError("Projet hors de base.prive")
        if arguments.resultat:
            from benchab.projets.retours import confirmer

            resultat = confirmer(
                base,
                projet,
                arguments.resultat,
                arguments.preuve_retour,
                arguments.reference_imprimee,
                arguments.id,
                arguments.remplace_reference,
                ensemble_complet=arguments.ensemble_complet,
            )
            print(json.dumps(resultat, ensure_ascii=False, indent=2))
            return 0
        if not arguments.id:
            raise ValueError("--id requis pour associer les archives d'une préparation.")
        ident = PR.identifiant(arguments.id)
        fiche = lire_fiche(base, projet, ident)
        archive = arguments.archive or base.prive / "archives/slicer"
        archive = archive.resolve()
        if not archive.is_relative_to(base.prive.resolve()):
            raise ValueError("Archive hors de base.prive")
        if arguments.source:
            copies, deja, illisibles = ES.archiver(arguments.source, archive)
            print(f"1. archivage : {len(copies)} nouveau(x), {deja} déjà archivé(s) ; {len(illisibles)} illisible(s)")
        else:
            archive.mkdir(parents=True, exist_ok=True)
        if arguments.fichier:
            choisis, autres = arguments.fichier, []
        else:
            choisis, autres = correspondants(fiche, archive)
        code = 0
        if choisis:
            for nom, ok, message in enregistrer(base, projet, ident, choisis):
                print(f"2. envoi {nom} : {'enregistré' if ok else 'REFUSÉ'} — {message[:200]}")
                if not ok:
                    code = 2
        elif enregistres(fiche):
            print("2. envois : déjà enregistrés, aucun nouveau")
        else:
            code = 1
            print("2. envois : AUCUN envoi archivé correspondant ; désigner --fichier explicitement")
            for autre in autres:
                print("Autre archive non associée :", autre.name)
        fiche = lire_fiche(base, projet, ident)
        chemin, cree = journal(projet, ident, fiche, prive=base.prive)
        print("3. journal :", chemin, "créé" if cree else "existant conservé")
        return code
    except (ValueError, OSError, KeyError) as erreur:
        print(str(erreur), file=sys.stderr)
        return 2
    finally:
        if possede:
            base.fermer()


if __name__ == "__main__":
    raise SystemExit(main())
