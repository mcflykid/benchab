"""Collecte locale, en lecture seule, des prérequis logiciels BENCHAB."""

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import plistlib
import shutil
import sys
import time
from pathlib import Path

from benchab.fabrication import slicer_runtime
from benchab.machine.configuration import charger
from benchab.mesure.runtime import controler as controler_runtime

PAQUETS_REQUIS = ("cadgen", "numpy", "scipy", "trimesh", "manifold3d", "build123d", "mapbox-earcut")
ORDRE_STATUT = {"PASS": 0, "WARN": 1, "NON VÉRIFIÉ": 1, "FAIL": 2}
TAILLE_MAX_PROFIL = 64 * 1024 * 1024
OUTILS_COMMUNS = (
    "fabrication/trancher.py", "controles/envoi.py", "qualite/coherence.py", "qualite/frontiere.py",
    "projets/nouveau.py", "donnees/base.py", "imports3d/maillage_avance.py", "visualisation/serveur.py",
)


def _outils_communs(racine=None):
    """Relève les modules requis du paquet, sans unité ; PASS/FAIL de présence, sans importer ni lancer les outils."""
    racine = Path(racine) if racine is not None else Path(__file__).resolve().parents[1]
    absents = [relatif for relatif in OUTILS_COMMUNS if not (racine / relatif).is_file()]
    return {"statut": "FAIL" if absents else "PASS", "modules_absents": absents,
            "modules_declares": len(OUTILS_COMMUNS)}


def _statut_global(resultats: list[dict]) -> int:
    controles = [ligne for ligne in resultats if ligne.get("controle") != "Imprimante"]
    if not controles:
        raise ValueError("Au moins un contrôle local est requis.")
    try:
        return max(ORDRE_STATUT[ligne["statut"]] for ligne in controles)
    except (KeyError, TypeError) as erreur:
        raise ValueError("Statut de collecteur inconnu.") from erreur


def verifier_rapport(rapport: object, code_processus: int | None = None) -> dict:
    """Valide la cohérence du code, des mesures et des statuts d’un collecteur."""
    if not isinstance(rapport, dict):
        raise ValueError("Collecteur : objet JSON attendu.")
    mesures = rapport.get("mesures")
    resultats = rapport.get("resultats")
    code = rapport.get("code")
    if (
        not isinstance(mesures, dict)
        or not mesures
        or not isinstance(resultats, list)
        or not resultats
        or type(code) is not int
        or code not in (0, 1, 2)
    ):
        raise ValueError("Collecteur : rapport incomplet ou code invalide.")
    attendu = _statut_global(resultats)
    if attendu != code or code_processus is not None and code_processus != code:
        raise ValueError("Collecteur : code et statuts mesurés incohérents.")
    return rapport


def _information_blender(candidat: str | Path | None = None) -> dict:
    """Relève passivement version de bundle et SHA du binaire Blender, sans le lancer."""
    bundle = Path("/Applications/Blender.app/Contents/MacOS/Blender")
    # Le wrapper Homebrew n’expose pas de version; lire le bundle sans l’exécuter (docs/reference/commandes.md).
    candidat = candidat or (bundle if bundle.is_file() else shutil.which("blender") or bundle)
    chemin = Path(candidat).expanduser()
    info = {"executable": str(chemin), "present": False, "version": None, "build": None, "sha256": None}
    try:
        chemin = chemin.resolve(strict=True)
        if not chemin.is_file() or not os.access(chemin, os.X_OK):
            info["raison"] = "binaire absent ou non exécutable"
            return info
        info.update(executable=str(chemin), present=True)
        bundle = next((parent for parent in chemin.parents if parent.suffix == ".app"), None)
        if bundle is not None:
            info["bundle"] = bundle.name
            try:
                with (bundle / "Contents/Info.plist").open("rb") as flux:
                    metadata = plistlib.load(flux)
            except (OSError, ValueError, plistlib.InvalidFileException):
                metadata = {}
            info["version"] = metadata.get("CFBundleShortVersionString")
            info["build"] = metadata.get("CFBundleVersion")
        if chemin.stat().st_size > 4 * 1024**3:
            info["raison"] = "binaire au-delà du budget de hachage de 4 Gio"
            return info
        hachage = hashlib.sha256()
        with chemin.open("rb") as flux:
            for bloc in iter(lambda: flux.read(1024 * 1024), b""):
                hachage.update(bloc)
        info["sha256"] = hachage.hexdigest()
    except OSError as erreur:
        info["raison"] = type(erreur).__name__
    return info


def _profil_systeme(configuration: dict) -> dict:
    """Empreinte seulement le profil système explicitement déclaré sous Resources, jamais AppSupport."""
    mouvement = configuration.get("mouvement") if isinstance(configuration, dict) else None
    source = mouvement.get("source") if isinstance(mouvement, dict) else None
    attendu = mouvement.get("source_sha256") if isinstance(mouvement, dict) else None
    executable = configuration.get("slicer", {}).get("executable")
    if not isinstance(source, str) or not isinstance(attendu, str) or not isinstance(executable, str):
        return {"statut": "NON VÉRIFIÉ", "raison": "profil système non déclaré explicitement"}
    relatif = Path(source)
    if relatif.is_absolute() or ".." in relatif.parts:
        return {"statut": "FAIL", "raison": "chemin de profil système hors contrat"}
    app = next((parent for parent in Path(executable).parents if parent.suffix == ".app"), None)
    if app is None or not relatif.parts or relatif.parts[0] != app.name:
        return {"statut": "FAIL", "raison": "profil déclaré différent du bundle slicer"}
    chemin = app.parent / relatif
    try:
        app_reel = app.resolve(strict=True)
        for parent in (chemin, *chemin.parents):
            if parent == app.parent:
                break
            if parent.is_symlink():
                return {"statut": "FAIL", "raison": "lien symbolique dans le profil système"}
        cible = chemin.resolve(strict=True)
        if not cible.is_relative_to(app_reel) or not cible.is_file():
            return {"statut": "FAIL", "raison": "fichier profil absent ou hors Resources"}
        if cible.stat().st_size > TAILLE_MAX_PROFIL:
            return {"statut": "WARN", "fichier": source, "raison": "profil au-delà du budget de 64 Mio"}
        hachage = hashlib.sha256()
        with cible.open("rb") as flux:
            for bloc in iter(lambda: flux.read(1024 * 1024), b""):
                hachage.update(bloc)
        obtenu = hachage.hexdigest()
    except OSError as erreur:
        return {"statut": "WARN", "fichier": source, "raison": type(erreur).__name__}
    concorde = obtenu == attendu.lower()
    return {
        "statut": "PASS" if concorde else "WARN",
        "fichier": source,
        "sha256": obtenu,
        "sha256_attendu": attendu,
        "sha_concorde": concorde,
        "octets": cible.stat().st_size,
    }


def collecter(prive: str | Path | None = None) -> dict:
    """Mesure versions, SHA et espace (Gio), sans lancer de moteur ni lire AppSupport.

    Le rapport expose PASS/WARN/FAIL/NON VÉRIFIÉ selon chaque prérequis.
    """
    configuration = charger()
    resultats = []

    def noter(nom: str, statut: str, detail: str) -> None:
        resultats.append({"controle": nom, "statut": statut, "detail": detail})

    try:
        slicer_info = slicer_runtime.informations(empreinte=True)
    except (OSError, ValueError) as erreur:
        slicer_info = {"present": False, "raison": type(erreur).__name__}
    blender_info = _information_blender()
    profil_info = _profil_systeme(configuration)
    slicer_cfg = configuration.get("slicer", {})
    sha_reference = slicer_cfg.get("sha256_binaire_matrices_origine")
    mesures = {
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": Path(sys.executable).name,
        },
        "machine_benchab": configuration["machine"]["nom"],
        "paquets": {},
        "slicer": {
            "nom": slicer_info.get("nom", slicer_cfg.get("nom")),
            "version_qualifiee": slicer_cfg.get("version_qualifiee"),
            "version": slicer_info.get("version"),
            "build": slicer_info.get("build"),
            "executable": slicer_info.get("executable", slicer_cfg.get("executable")),
            "present": slicer_info.get("present", False),
            "sha256": slicer_info.get("sha256"),
            "sha256_reference": sha_reference,
        },
        "blender": blender_info,
        "profil_systeme_declared": profil_info,
    }
    noter("Python BENCHAB", "PASS", f"{platform.python_implementation()} {platform.python_version()}")
    # Une dépendance installée ne remplace pas un module de l'atelier manquant
    # (docs/guides/installation.md) ; ce relevé ne certifie pas son comportement.
    outils = _outils_communs()
    mesures["outils_communs"] = outils
    noter("Outils communs", outils["statut"],
          "modules absents : " + ", ".join(outils["modules_absents"]) if outils["modules_absents"]
          else f"{outils['modules_declares']} modules présents ; aucun lancement")

    absents = []
    for paquet in PAQUETS_REQUIS:
        try:
            version = importlib.metadata.version(paquet)
        except importlib.metadata.PackageNotFoundError:
            version = None
            absents.append(paquet)
        mesures["paquets"][paquet] = version
    noter(
        "Dépendances géométriques",
        "FAIL" if absents else "PASS",
        "absents : " + ", ".join(absents) if absents else
        "paquets requis présents ; versions et provenance contrôlées séparément",
    )

    runtime = controler_runtime(sys.executable, Path(__file__).resolve().parents[2] / "uv.lock", PAQUETS_REQUIS)
    mesures["runtime_verrouille"] = runtime
    noter("Versions et provenance uv.lock", runtime["statut"],
          runtime.get("raison", "comparaison passive des versions/révisions ; aucune installation ni exécution"))

    noter(
        slicer_info.get("nom", slicer_cfg.get("nom", "Slicer")),
        "FAIL" if not slicer_info.get("present") else "PASS"
        if slicer_info.get("present")
        and slicer_info.get("sha256")
        and sha_reference
        and slicer_info.get("sha256") == sha_reference
        and slicer_info.get("version") == slicer_cfg.get("version_qualifiee")
        else "WARN",
        # La version seule ne distingue pas deux builds : la qualification du
        # binaire est liée à son SHA (docs/reference/reglages-et-integrite.md).
        "version et SHA comparés à la qualification configurée ; aucune exécution"
        if sha_reference and slicer_info.get("sha256") == sha_reference
        else "binaire présent mais référence SHA absente ou différente ; comparer avant livraison"
        if slicer_info.get("present")
        else "binaire déclaré absent ou information passive indisponible",
    )

    noter(
        "Blender",
        "PASS"
        if blender_info.get("present") and blender_info.get("version") and blender_info.get("sha256")
        else "WARN",
        "version de bundle et SHA du binaire lues sans lancer Blender"
        if blender_info.get("present")
        else blender_info.get("raison", "binaire Blender absent"),
    )
    noter(
        "Profil système déclaré",
        profil_info["statut"],
        "SHA du fichier Resources comparée à la configuration"
        if profil_info["statut"] == "PASS"
        else profil_info.get("raison", "empreinte du profil différente"),
    )

    racine_privee = Path(prive).expanduser().resolve() if prive is not None else None
    if racine_privee is not None and racine_privee.exists():
        libre = shutil.disk_usage(racine_privee).free / 1024**3
        mesures["espace_libre_Gio"] = round(libre, 2)
        noter("Espace privé", "PASS" if libre >= 15 else "WARN" if libre >= 10 else "FAIL", f"{libre:.1f} Gio libres")
    else:
        noter("Espace privé", "NON VÉRIFIÉ", "base.prive absente ou non fournie")

    # Seul le profil machine système explicitement déclaré est haché; AppSupport
    # peut contenir des choix personnels implicites et reste hors périmètre (docs/reference/profils-et-procedes.md).
    noter("Imprimante", "NON VÉRIFIÉ", "aucun accès à l’imprimante")
    noter("Profils utilisateur slicer", "NON VÉRIFIÉ", "AppSupport non consulté; aucun profil utilisateur sélectionné")
    rapport = {"date": time.strftime("%Y-%m-%d %H:%M"), "resultats": resultats, "mesures": mesures}
    rapport["code"] = _statut_global(resultats)
    return rapport


def _chemin_sortie(chemin: Path) -> Path:
    brut = chemin.expanduser()
    if any(element.is_symlink() for element in (brut, *brut.parents)):
        raise ValueError("Lien symbolique dans le rapport d’environnement refusé.")
    parent = brut.parent.resolve(strict=True)
    cible = parent / brut.name
    racine = Path(__file__).resolve().parents[2]
    temporaire = Path("/private/tmp").resolve()
    if not cible.is_relative_to(racine / "prive") and not cible.is_relative_to(temporaire):
        raise ValueError("Le rapport doit rester dans prive/ ou /private/tmp.")
    if cible.exists():
        raise FileExistsError("Le rapport d’environnement existe déjà.")
    return cible


def main(argv: list[str] | None = None) -> int:
    """Écrit un rapport neuf dans l’espace privé choisi ou `/private/tmp`."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prive", type=Path)
    parser.add_argument("--json", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        rapport = collecter(args.prive)
        destination = _chemin_sortie(args.json)
        with destination.open("x", encoding="utf-8") as flux:
            json.dump(rapport, flux, ensure_ascii=False, indent=2, allow_nan=False)
            flux.write("\n")
    except (OSError, ValueError) as erreur:
        parser.error(str(erreur))
    for resultat in rapport["resultats"]:
        print(f"{resultat['statut']:12s} {resultat['controle']} — {resultat['detail']}")
    return rapport["code"]


if __name__ == "__main__":
    raise SystemExit(main())
