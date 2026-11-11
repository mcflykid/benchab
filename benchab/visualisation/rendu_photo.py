"""Rendu Blender en situation : image indicative, sorties privées nouvelles et sources intactes."""

import argparse
import json
import math
import os
import shutil
import tempfile
from pathlib import Path

from benchab.chemins import atelier_prive, sans_lien
from benchab.processus import executer
from benchab.qualite.ecritures import PublicationNeuve
from benchab.qualite.empreintes import sha256_fichier

MATERIAUX = {
    "pla_glow", "pla", "plastique_noir", "plastique_mat", "aluminium_brosse", "rouge", "verre", "blanc_casse",
    "melamine", "parquet", "mur", "tissu", "feuille",
}


def _vecteur(valeur, nom):
    if (
        not isinstance(valeur, list)
        or len(valeur) != 3
        or any(type(x) not in (int, float) or not math.isfinite(x) for x in valeur)
    ):
        raise ValueError(nom + " : trois nombres finis attendus.")


def preparer(spec, dossier, prive):
    """Valide chemins et paramètres d'une simulation; taille px, mesures mm³, aucun objet source modifié."""
    prive = atelier_prive(prive)
    dossier = sans_lien(dossier, racine=prive, strict=True)
    resultat = json.loads(json.dumps(spec, allow_nan=False))
    if spec.get("scene", "kallax") != "kallax":
        raise ValueError("Scène inconnue.")
    resultat.setdefault("titre", "Simulation indicative — ni photo réelle ni preuve physique")
    taille = spec.get("taille", [1600, 1200])
    if (
        not isinstance(taille, list)
        or len(taille) != 2
        or any(type(n) is not int or not 16 <= n <= 4096 for n in taille)
    ):
        raise ValueError("taille : deux entiers entre 16 et 4096.")
    if type(spec.get("echantillons", 160)) is not int or not 1 <= spec.get("echantillons", 160) <= 1024:
        raise ValueError("Échantillons : entier de 1 à 1024.")
    pieces = resultat.get("pieces")
    if not isinstance(pieces, list) or not pieces:
        raise ValueError("Au moins une pièce requise.")
    entrees = []
    for piece in pieces:
        if not isinstance(piece, dict) or piece.get("materiau") not in MATERIAUX:
            raise ValueError("Matériau de pièce inconnu.")
        chemin = Path(piece["stl"])
        chemin = sans_lien(chemin if chemin.is_absolute() else dossier / chemin, strict=True)
        if chemin.suffix.lower() != ".stl" or not chemin.is_relative_to(prive):
            raise ValueError("Les modèles STL du rendu restent dans l'espace privé.")
        chemin = sans_lien(chemin, racine=prive, strict=True)
        piece["stl"] = str(chemin)
        if "couleur" in piece:
            _vecteur(piece["couleur"], "couleur")
            if any(not 0 <= n <= 1 for n in piece["couleur"]):
                raise ValueError("Couleur hors de [0,1].")
        entrees.append(chemin)
    scripts = resultat.get("scripts", [])
    if not isinstance(scripts, list):
        raise ValueError("scripts : liste explicite attendue.")
    for i, valeur in enumerate(scripts):
        chemin = Path(valeur)
        chemin = sans_lien(chemin if chemin.is_absolute() else dossier / chemin, strict=True)
        if not chemin.is_relative_to(prive) or chemin.suffix != ".py":
            raise ValueError("Un accessoire procédural doit être un script privé explicitement fourni.")
        chemin = sans_lien(chemin, racine=prive, strict=True)
        scripts[i] = str(chemin)
        entrees.append(chemin)
    sortie = Path(resultat["sortie"])
    sortie = sans_lien(sortie if sortie.is_absolute() else dossier / sortie)
    if sortie.suffix.lower() != ".png" or not sortie.is_relative_to(prive):
        raise ValueError("Sortie PNG privée requise.")
    sortie = sans_lien(sortie, racine=prive)
    vues = resultat.get("vues", [{}])
    if not isinstance(vues, list) or not vues:
        raise ValueError("Liste de vues non vide requise.")
    sorties = []
    for vue in vues:
        if not isinstance(vue, dict):
            raise ValueError("Vue invalide.")
        suffixe = vue.get("suffixe", "")
        if not isinstance(suffixe, str) or any(not (c.isalnum() or c in "-_") for c in suffixe):
            raise ValueError("Suffixe de vue non sûr.")
        for cle in ("camera", "cible"):
            if cle in vue:
                _vecteur(vue[cle], cle)
        for cle in ("focale", "ouverture"):
            if cle in vue and (type(vue[cle]) not in (int, float) or not math.isfinite(vue[cle]) or vue[cle] <= 0):
                raise ValueError(cle + " : nombre fini positif attendu.")
        sorties.append(sortie.with_name(sortie.stem + suffixe + ".png"))
    if len(set(sorties)) != len(sorties):
        raise ValueError("Plusieurs vues écraseraient la même image.")
    return resultat, entrees, sorties, sortie.with_suffix(".rapport.json")


def rendre(fichier, *, prive, blender=None, delai=600):
    """Rend une simulation visuelle privée; délai en s, statut PASS ou exception, sans preuve physique."""
    fichier = sans_lien(fichier, strict=True)
    prive = atelier_prive(prive)
    spec, entrees, sorties, rapport = preparer(json.loads(fichier.read_text()), fichier.parent, prive)
    entrees = [fichier, *entrees]
    empreintes = {str(p): sha256_fichier(p) for p in entrees}
    moteur = blender or shutil.which("blender") or "/Applications/Blender.app/Contents/MacOS/Blender"
    if not Path(moteur).is_file():
        raise ValueError("Blender indisponible.")
    with PublicationNeuve(entrees, [*sorties, rapport]) as publication:
        with tempfile.TemporaryDirectory(prefix="benchab-photo-") as tmp:
            travail = Path(tmp)
            for nom in ("home", "cache", "tmp"):
                (travail / nom).mkdir()
            spec["sortie"] = str(travail / "rendu.png")
            instruction = travail / "spec.json"
            instruction.write_text(json.dumps(spec, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            env = dict(
                os.environ,
                HOME=str(travail / "home"),
                XDG_CACHE_HOME=str(travail / "cache"),
                TMPDIR=str(travail / "tmp"),
                PYTHONDONTWRITEBYTECODE="1",
            )
            resultat = executer(
                [
                    str(moteur),
                    "--background",
                    "--factory-startup",
                    "--python",
                    str(Path(__file__).with_name("_photo_blender.py")),
                    "--",
                    str(instruction),
                ],
                cwd=travail,
                diagnostics=Path(prive) / "diagnostics/rendus",
                delai=delai,
                env=env,
            )
            if not resultat.ok:
                raise ValueError("Rendu interrompu/refusé : " + (resultat.stderr[-500:] or resultat.raison))
            from PIL import Image

            for destination, vue in zip(sorties, spec.get("vues", [{}]), strict=True):
                image = travail / ("rendu" + vue.get("suffixe", "") + ".png")
                with Image.open(image) as verification:
                    verification.verify()
                shutil.copyfile(image, publication.temporaire(destination))
            preuve = {
                "statut": "PASS",
                "nature": "SIMULATION_VISUELLE",
                "validation_physique": False,
                "outils_sha256": {
                    p.name: sha256_fichier(p) for p in (Path(__file__), Path(__file__).with_name("_photo_blender.py"))
                },
                "sources": empreintes,
                "images": [str(p) for p in sorties],
                "limite": "Meuble et contexte schématiques ; aucun aspect imprimé, ajustement ou résistance prouvé.",
            }
            publication.temporaire(rapport).write_text(
                json.dumps(preuve, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            if any(sha256_fichier(Path(p)) != sha for p, sha in empreintes.items()):
                raise ValueError("Source modifiée pendant le rendu.")
            publication.publier()
    return preuve


def inspecter_scene(fichier, *, prive, sortie=None, blender=None, delai=600, mm_par_unite=None):
    """Inspecte une scène privée : bornes mm, volume signé mm³, topologie ; PASS/WARN/NV sans sauvegarde.

    mm_par_unite déclare une échelle positive finie facultative ; sans unité physique,
    les dimensions restent NON VÉRIFIÉES.
    """
    if mm_par_unite is not None and (
        isinstance(mm_par_unite, bool) or not isinstance(mm_par_unite, (int, float))
        or not math.isfinite(mm_par_unite) or mm_par_unite <= 0
    ):
        raise ValueError("Échelle mm par unité : nombre positif fini exigé")
    racine = atelier_prive(prive)
    source_brute = Path(fichier).expanduser()
    source = sans_lien(source_brute, strict=True)
    if not source.is_relative_to(racine) or source.suffix.lower() != ".blend" or not source.is_file():
        raise ValueError("La scène .blend doit être un fichier privé explicite.")
    source = sans_lien(source, racine=racine, strict=True)
    destination = Path(sortie) if sortie is not None else source.with_suffix(".inspection.json")
    destination = sans_lien(destination if destination.is_absolute() else racine / destination, racine=racine)
    if not destination.is_relative_to(racine) or destination.suffix.lower() != ".json":
        raise ValueError("Le rapport JSON doit rester dans l’espace privé.")
    empreinte = sha256_fichier(source)
    moteur = blender or shutil.which("blender") or "/Applications/Blender.app/Contents/MacOS/Blender"
    moteur = Path(moteur).expanduser()
    if not moteur.is_file() or not os.access(moteur, os.X_OK):
        raise ValueError("Blender indisponible.")
    with PublicationNeuve([source], [destination]) as publication:
        with tempfile.TemporaryDirectory(prefix="benchab-inspection-") as tmp:
            travail = Path(tmp)
            for nom in ("home", "cache", "tmp"):
                (travail / nom).mkdir()
            rapport_temp = travail / "inspection.json"
            env = dict(
                os.environ,
                HOME=str(travail / "home"),
                XDG_CACHE_HOME=str(travail / "cache"),
                TMPDIR=str(travail / "tmp"),
                PYTHONDONTWRITEBYTECODE="1",
            )
            resultat = executer(
                [
                    str(moteur),
                    "--background",
                    "--factory-startup",
                    # Les scripts embarqués d’un fichier Blender ne sont pas exécutés (docs/reference/commandes.md).
                    "--disable-autoexec",
                    "--python",
                    str(Path(__file__).with_name("_inspect_scene_blender.py")),
                    "--",
                    *(["--mm-par-unite", str(mm_par_unite)] if mm_par_unite is not None else []),
                    str(source),
                    str(rapport_temp),
                ],
                cwd=travail,
                diagnostics=racine / "diagnostics/inspection-scene",
                delai=delai,
                env=env,
            )
            if not resultat.ok or not rapport_temp.is_file():
                detail = resultat.stderr[-500:] or resultat.raison
                raise ValueError("Inspection Blender interrompue/refusée : " + detail)
            preuve = json.loads(rapport_temp.read_text(encoding="utf-8"))
            if preuve.get("schema") != 1 or not isinstance(preuve.get("objets"), list):
                raise ValueError("Rapport Blender incomplet.")
            if sha256_fichier(source) != empreinte:
                raise ValueError("Scène modifiée pendant l’inspection.")
            preuve.update(
                statut=(
                    "PASS"
                    if preuve.get("statut_geometrique") == "PASS" and preuve["objets"]
                    else "NON VÉRIFIÉ"
                    if preuve["objets"]
                    else "WARN"
                ),
                source=str(source),
                source_sha256=empreinte,
                validation_physique=False,
                operation="lecture seule; aucun fichier .blend sauvegardé",
            )
            publication.temporaire(destination).write_text(
                json.dumps(preuve, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            publication.publier()
    return preuve


def main(argv=None, *, prive=None):
    """Lance une simulation/inspection privée (mm, pixels, s) ; codes CLI conservés, aucune validation
    physique inférée.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--blender", type=Path)
    parser.add_argument("--delai", type=float, default=600)
    args = parser.parse_args(argv)
    print(
        json.dumps(
            rendre(
                args.spec,
                prive=atelier_prive(prive),
                blender=args.blender,
                delai=args.delai,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0
