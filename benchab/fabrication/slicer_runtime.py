"""Informations passives sur le moteur déclaré dans la configuration machine.

Ce module ne lance jamais le slicer. Les exécutions passent exclusivement par
``benchab.fabrication.trancher``.
"""

import hashlib
import os
import plistlib
from pathlib import Path

from benchab.machine.configuration import charger


def configuration() -> dict:
    """Retourne la section slicer après validation de ses champs d'identité."""
    machine = charger()
    slicer = machine.get("slicer") if isinstance(machine, dict) else None
    if not isinstance(slicer, dict):
        raise ValueError("Configuration slicer absente du profil machine.")
    nom = slicer.get("nom")
    version = slicer.get("version_qualifiee")
    executable = slicer.get("executable")
    if not isinstance(nom, str) or not nom.strip():
        raise ValueError("Nom du slicer absent de la configuration machine.")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("Version qualifiée du slicer absente de la configuration machine.")
    if not isinstance(executable, str) or not Path(executable).is_absolute():
        raise ValueError("Chemin absolu du slicer requis dans la configuration machine.")
    return dict(slicer)


def executable() -> Path:
    """Retourne le chemin configuré, sans vérifier ni démarrer le binaire."""
    return Path(configuration()["executable"])


def informations(chemin: str | Path | None = None, empreinte: bool = False) -> dict:
    """Décrit le moteur désigné et son bundle sans jamais le lancer."""
    config = configuration()
    executable_configure = Path(config["executable"])
    cible = Path(chemin).expanduser() if chemin is not None else executable_configure
    est_configuree = cible == executable_configure
    information = {
        "nom": config["nom"] if est_configuree else cible.name,
        "executable": str(cible),
        "present": cible.is_file() and os.access(cible, os.X_OK),
        "version": config["version_qualifiee"] if est_configuree else None,
        "build": None,
        "prerelease": None,
    }
    bundle = next((parent for parent in cible.parents if parent.suffix == ".app"), None)
    if bundle is not None:
        information["bundle"] = str(bundle)
        try:
            with (bundle / "Contents/Info.plist").open("rb") as flux:
                metadonnees = plistlib.load(flux)
        except (OSError, ValueError, plistlib.InvalidFileException):
            metadonnees = {}
        information.update(
            nom=metadonnees.get("CFBundleName") or information["nom"],
            version=metadonnees.get("CFBundleShortVersionString") or information["version"],
            build=metadonnees.get("CFBundleVersion"),
        )
    version = information["version"]
    if version:
        information["prerelease"] = any(
            marqueur in str(version).lower() for marqueur in ("alpha", "beta", "rc", "dev", "nightly")
        )
    if empreinte and information["present"]:
        with cible.open("rb") as flux:
            information["sha256"] = hashlib.file_digest(flux, "sha256").hexdigest()
    return information
