"""Vérifie les empreintes d'un manifeste de réimpression local.

Ce contrôle numérique vérifie l'identité des références. Il n'autorise pas un
envoi et ne déduit jamais un résultat physique.
"""

import argparse
import json
from pathlib import Path

from benchab.chemins import sans_lien
from benchab.qualite.ecritures import destination_rapport_optionnel, ecrire_json_neuf
from benchab.qualite.empreintes import sha256_fichier


def verifier(projet):
    """Compare les SHA d'un projet privé ; PASS/FAIL/NON APPLICABLE, aucune écriture ni commande machine."""
    projet = sans_lien(projet, strict=True)
    manifeste = projet / "VERROU-REIMPRESSION.json"
    if manifeste.is_symlink():
        raise ValueError("Manifeste de réimpression lié refusé.")
    if not manifeste.exists():
        return {"statut": "NON APPLICABLE", "projet": projet.name, "detail": "Aucun verrou de réimpression déclaré."}
    data = json.loads(manifeste.read_text(encoding="utf-8"))
    fichiers = data.get("fichiers")
    erreurs = []
    if data.get("schema") != 1 or not isinstance(fichiers, dict) or not fichiers:
        raise ValueError("Manifeste de réimpression invalide.")
    etat = data.get("etat", "actif")
    if etat not in ("actif", "retire"):
        raise ValueError("État du manifeste de réimpression invalide.")
    for rel, digest in fichiers.items():
        chemin = Path(rel)
        if (chemin.is_absolute() or ".." in chemin.parts) and etat == "retire":
            raise ValueError("Chemin historique invalide : " + str(rel))
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Empreinte invalide : " + str(rel))
    if etat == "retire":
        if not data.get("date_retrait") or not data.get("origine_retrait"):
            raise ValueError("Retrait du verrou sans date ou origine.")
        return {
            "statut": "INACTIF",
            "projet": projet.name,
            "fichiers_historiques": len(fichiers),
            "detail": "Verrou retiré ; empreintes conservées comme historique, fichiers non comparés.",
        }
    for rel, attendu in fichiers.items():
        cible = projet / rel
        if Path(rel).is_absolute() or ".." in Path(rel).parts:
            erreurs.append("Chemin non sûr : " + rel)
            continue
        try:
            cible = sans_lien(cible, racine=projet)
        except ValueError:
            erreurs.append("Chemin non sûr : " + rel)
            continue
        try:
            dans_projet = cible.resolve().is_relative_to(projet)
        except OSError:
            dans_projet = False
        if not dans_projet:
            erreurs.append("Chemin non sûr : " + rel)
        elif not cible.is_file():
            erreurs.append("Fichier absent : " + rel)
        elif sha256_fichier(cible) != attendu:
            erreurs.append("Référence modifiée : " + rel)
    for rel in data.get("repertoires_figes", []):
        dossier_rel = Path(rel)
        dossier = projet / dossier_rel
        if (
            dossier_rel.is_absolute()
            or ".." in dossier_rel.parts
            or dossier.is_symlink()
            or not dossier.resolve().is_relative_to(projet)
        ):
            raise ValueError("Périmètre de verrou invalide.")
        for cible in dossier.rglob("*"):
            relatif = cible.relative_to(projet).as_posix()
            if cible.is_symlink():
                erreurs.append("Lien dans la version figée : " + relatif)
            elif (
                cible.is_file()
                and "__pycache__" not in cible.parts
                and cible.name != ".DS_Store"
                and cible.suffix != ".pyc"
                and relatif not in fichiers
            ):
                erreurs.append("Nouveau fichier dans la version figée : " + relatif)
    return {
        "statut": "FAIL" if erreurs else "PASS",
        "projet": projet.name,
        "fichiers_verifies": len(fichiers),
        "erreurs": erreurs,
        "limite": "Identité des fichiers, pas un accord de lancement ni une validation physique.",
    }


def main(argv=None):
    """Vérifie un projet et publie facultativement le rapport complet hors du projet.

    Entrées : chemins sans unité. Codes 0 pour PASS, 1 pour FAIL et 2 pour
    les autres statuts. La publication laisse le projet contrôlé intact.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projet", type=Path, required=True)
    parser.add_argument("--sortie", type=Path, help="Rapport JSON privé neuf, hors du projet contrôlé")
    parser.add_argument("--resume", action="store_true", help="Affichage court ; exige --sortie")
    args = parser.parse_args(argv)
    try:
        sortie = destination_rapport_optionnel(args.sortie, resume=args.resume, nom="de réimpression")
        projet = sans_lien(args.projet, strict=True)
        # Écrire dans le périmètre contrôlé pourrait rendre le verdict périmé à sa publication (#35).
        if sortie is not None and sortie.is_relative_to(projet):
            raise ValueError("Le rapport de réimpression doit rester hors du projet contrôlé.")
        rapport = verifier(projet)
        if sortie is not None:
            ecrire_json_neuf(sortie, rapport)
    except (OSError, ValueError) as erreur:
        if args.sortie is None and not args.resume:
            raise
        parser.error(str(erreur))
    if args.resume:
        resume = {
            cle: rapport[cle] for cle in ("statut", "projet", "fichiers_historiques")
            if cle in rapport
        }
        if "fichiers_verifies" in rapport:
            resume["references_attendues"] = rapport["fichiers_verifies"]
        if "erreurs" in rapport:
            resume["erreurs"] = len(rapport["erreurs"])
        resume["rapport"] = str(sortie)
        print(json.dumps(resume, ensure_ascii=False, separators=(",", ":")))
    else:
        print(json.dumps(rapport, ensure_ascii=False, indent=2))
    return 1 if rapport["statut"] == "FAIL" else 0 if rapport["statut"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
