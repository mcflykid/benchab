"""Planification sûre du lien d’entrée du skill BENCHAB."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

VERSION_PLAN = 1
MARQUEURS_SOURCE = ("SKILL.md", "AGENTS.md", "docs")


def _empreinte_stat(statut):
    return {
        "mode": statut.st_mode,
        "device": statut.st_dev,
        "inode": statut.st_ino,
        "taille": statut.st_size,
        "mtime_ns": statut.st_mtime_ns,
        "ctime_ns": statut.st_ctime_ns,
    }


def _identite(statut):
    return (statut.st_dev, statut.st_ino, stat.S_IFMT(statut.st_mode))


def _chemin_absolu_sans_liens(valeur, *, dossier):
    chemin = Path(valeur)
    if not chemin.is_absolute() or ".." in chemin.parts:
        raise ValueError("Un chemin absolu sans traversée est exigé")
    # Refuser les parents symboliques évite qu'un chemin relu redirige les opérations (docs/guides/installation.md).
    courant = Path(chemin.anchor)
    for composant in chemin.parts[1:]:
        courant /= composant
        statut = courant.lstat()
        if stat.S_ISLNK(statut.st_mode):
            raise ValueError(f"Composant symbolique refusé : {courant}")
    statut = chemin.lstat()
    attendu = stat.S_ISDIR if dossier else stat.S_ISREG
    if not attendu(statut.st_mode):
        raise ValueError(f"Type de chemin invalide : {chemin}")
    return chemin


def _fichier_source(chemin):
    avant = chemin.lstat()
    if not stat.S_ISREG(avant.st_mode):
        raise ValueError(f"Marqueur source non régulier : {chemin.name}")
    empreinte = hashlib.sha256()
    with chemin.open("rb") as flux:
        for bloc in iter(lambda: flux.read(1024 * 1024), b""):
            empreinte.update(bloc)
    apres = chemin.lstat()
    if _empreinte_stat(avant) != _empreinte_stat(apres):
        raise ValueError(f"Marqueur source modifié pendant la lecture : {chemin.name}")
    return {"stat": _empreinte_stat(apres), "sha256": empreinte.hexdigest()}


def _source_valide(racine):
    marqueurs = {}
    for nom in MARQUEURS_SOURCE:
        chemin = racine / nom
        statut = chemin.lstat()
        if nom == "docs":
            if not stat.S_ISDIR(statut.st_mode):
                raise ValueError("La source exige un dossier docs réel")
            marqueurs[nom] = _empreinte_stat(statut)
        else:
            marqueurs[nom] = _fichier_source(chemin)
    contenu = (racine / "SKILL.md").read_text(encoding="utf-8")
    blocs = contenu.split("---", 2)
    noms = (
        []
        if len(blocs) < 3
        else [ligne.strip() for ligne in blocs[1].splitlines() if ligne.strip().startswith("name:")]
    )
    if noms != ["name: benchab"]:
        raise ValueError("SKILL.md doit déclarer exactement le nom benchab dans son en-tête")
    return marqueurs


def _instantane_dossier(dossier):
    entrees = []
    for entree in sorted(dossier.iterdir(), key=lambda item: item.name):
        statut = entree.lstat()
        item = {"nom": entree.name, "stat": _empreinte_stat(statut)}
        if stat.S_ISLNK(statut.st_mode):
            item["lien"] = os.readlink(entree)
        entrees.append(item)
    return {"stat": _empreinte_stat(dossier.lstat()), "entrees": entrees}


def _dans(chemin, racine):
    return chemin == racine or racine in chemin.parents


def _cible_lien(lien, dossier):
    valeur = Path(os.readlink(lien))
    if not valeur.is_absolute():
        valeur = dossier / valeur
    return valeur.resolve(strict=False)


def planifier(racine_source, destinations, *, ancien_atelier=None):
    """Produit un plan JSON sans écriture ni installation.

    Entrées : racine source, dossiers skills explicites et ancien atelier optionnel ;
    sorties : opérations et préconditions, sans unité. Statut PLANIFIÉ ; aucune
    suppression hors symlinks explicitement rattachés à l’ancien atelier.
    """
    source = _chemin_absolu_sans_liens(racine_source, dossier=True)
    marqueurs = _source_valide(source)
    vieux = _chemin_absolu_sans_liens(ancien_atelier, dossier=True) if ancien_atelier is not None else None
    if vieux == source:
        raise ValueError("La source BENCHAB ne peut pas être l’ancien atelier")
    if not destinations:
        raise ValueError("Au moins un dossier skills explicite est requis")
    cibles = []
    for valeur in destinations:
        dossier = _chemin_absolu_sans_liens(valeur, dossier=True)
        if dossier in cibles or _dans(dossier, source) or (vieux is not None and _dans(dossier, vieux)):
            raise ValueError("Dossiers skills dupliqués ou situés dans une source protégée")
        cibles.append(dossier)
    cibles.sort(key=lambda p: str(p))
    operations = []
    instantanes = {}
    for dossier in cibles:
        instantanes[str(dossier)] = _instantane_dossier(dossier)
        entree_benchab = dossier / "benchab"
        if entree_benchab.is_symlink() and _cible_lien(entree_benchab, dossier) == source:
            cible_deja_presente = True
        else:
            cible_deja_presente = False
        for entree in dossier.iterdir():
            if not entree.is_symlink() or vieux is None:
                continue
            # Une cible résolue sous l'ancien atelier autorise le retrait (docs/guides/installation.md).
            cible = _cible_lien(entree, dossier)
            if _dans(cible, vieux) and not (entree.name == "benchab" and cible == source):
                operations.append(
                    {
                        "action": "retirer_lien_ancien",
                        "chemin": str(entree),
                        "readlink": os.readlink(entree),
                        "identite": list(_identite(entree.lstat())),
                    }
                )
        if not cible_deja_presente:
            retrait_prevu = any(
                operation["chemin"] == str(entree_benchab) and operation["action"] == "retirer_lien_ancien"
                for operation in operations
            )
            if (entree_benchab.exists() or entree_benchab.is_symlink()) and not retrait_prevu:
                raise FileExistsError(f"Collision conservée : {entree_benchab}")
            operations.append({"action": "creer_lien_benchab", "chemin": str(entree_benchab), "cible": str(source)})
    return {
        "schema": VERSION_PLAN,
        "statut": "PLANIFIÉ",
        "source": str(source),
        "source_marqueurs": marqueurs,
        "ancien_atelier": str(vieux) if vieux is not None else None,
        "destinations": [str(cible) for cible in cibles],
        "instantanes_destinations": instantanes,
        "operations": operations,
    }


def _verifier_preconditions(plan):
    if plan.get("schema") != VERSION_PLAN:
        raise ValueError("Version de plan inconnue")
    try:
        nouveau = planifier(
            plan.get("source", ""),
            plan.get("destinations", []),
            ancien_atelier=plan.get("ancien_atelier"),
        )
    except (OSError, ValueError) as erreur:
        # Une collision apparue après plan est une course, pas une nouvelle décision (docs/guides/installation.md).
        raise RuntimeError("État modifié depuis la planification ; rien n’a été appliqué") from erreur
    if nouveau != plan:
        raise RuntimeError("État modifié depuis la planification ; rien n’a été appliqué")
    return nouveau


def appliquer(plan):
    """Applique uniquement un plan frais et vérifie les liens créés.

    Entrée : plan produit par `planifier` ; sortie : rapport PASS ou exception
    FAIL. Aucune installation de dépendance, écrasement ou modification de cible.
    """
    frais = _verifier_preconditions(plan)
    retires = []
    crees = []
    try:
        for operation in frais["operations"]:
            chemin = Path(operation["chemin"])
            parent_stat = chemin.parent.lstat()
            parent_plan = frais["instantanes_destinations"][str(chemin.parent)]["stat"]
            # L'identité du parent et du lien détecte une substitution après plan (docs/guides/installation.md).
            if (parent_stat.st_dev, parent_stat.st_ino) != (parent_plan["device"], parent_plan["inode"]):
                raise RuntimeError(f"Dossier destination remplacé depuis le plan : {chemin.parent}")
            if operation["action"] == "retirer_lien_ancien":
                if (
                    not chemin.is_symlink()
                    or os.readlink(chemin) != operation["readlink"]
                    or list(_identite(chemin.lstat())) != operation["identite"]
                ):
                    raise RuntimeError(f"Lien changé avant retrait : {chemin}")
                identite_parent = _identite(chemin.parent.lstat())
                os.unlink(chemin)
                retires.append((chemin, operation["readlink"], identite_parent))
            else:
                if chemin.exists() or chemin.is_symlink():
                    raise FileExistsError(f"Collision apparue avant création : {chemin}")
                os.symlink(operation["cible"], chemin, target_is_directory=True)
                crees.append((chemin, os.readlink(chemin), _identite(chemin.lstat()), _identite(chemin.parent.lstat())))
        for destination in frais["destinations"]:
            lien = Path(destination) / "benchab"
            if not lien.is_symlink() or _cible_lien(lien, lien.parent) != Path(frais["source"]):
                raise RuntimeError(f"Lien final invalide : {lien}")
        chemins_crees = {
            operation["chemin"]
            for operation in frais["operations"]
            if operation["action"] == "creer_lien_benchab"
        }
        for operation in frais["operations"]:
            if operation["action"] == "retirer_lien_ancien":
                chemin = Path(operation["chemin"])
                if str(chemin) in chemins_crees:
                    continue
                if chemin.exists() or chemin.is_symlink():
                    raise RuntimeError(f"Ancien lien encore présent après retrait : {chemin}")
        return {"statut": "PASS", "operations": frais["operations"], "liens_verifies": len(frais["destinations"])}
    except Exception as erreur_application:
        echecs_retour = []
        for chemin, lien, identite_lien, identite_parent in reversed(crees):
            try:
                if (
                    _identite(chemin.parent.lstat()) == identite_parent
                    and chemin.is_symlink()
                    and os.readlink(chemin) == lien
                    and _identite(chemin.lstat()) == identite_lien
                ):
                    os.unlink(chemin)
            except OSError:
                echecs_retour.append(str(chemin))
                continue
        for chemin, lien, identite_parent in reversed(retires):
            try:
                if (
                    _identite(chemin.parent.lstat()) == identite_parent
                    and not chemin.exists()
                    and not chemin.is_symlink()
                ):
                    os.symlink(lien, chemin, target_is_directory=True)
            except OSError:
                echecs_retour.append(str(chemin))
                continue
        if echecs_retour:
            message = "Échec d’application et restauration incomplète : " + ", ".join(echecs_retour)
            raise RuntimeError(message) from erreur_application
        raise


def main(argv=None):
    """Expose plan sec par défaut et application explicite ; codes 0 PASS, 1 FAIL."""
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--racine", type=Path, required=True, help="Racine BENCHAB contenant SKILL.md, AGENTS.md et docs"
    )
    parser.add_argument("--destination", type=Path, action="append", required=True, help="Dossier skills existant")
    parser.add_argument(
        "--ancien-atelier", type=Path, help="Racine explicite de l’ancien atelier à désinstaller par lien"
    )
    parser.add_argument(
        "--appliquer", action="store_true", help="Applique le plan après vérification des préconditions"
    )
    args = parser.parse_args(argv)
    plan = planifier(args.racine, args.destination, ancien_atelier=args.ancien_atelier)
    if args.appliquer:
        resultat = appliquer(plan)
    else:
        resultat = plan
    print(json.dumps(resultat, ensure_ascii=False, indent=2))
    return 0
