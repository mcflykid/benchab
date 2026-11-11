"""Entrées de l’optimisation : chemins privés, références et géométrie numérique."""

import hashlib
import json
from pathlib import Path

from benchab.archives_3mf import Archive3MF
from benchab.donnees.base import Base


def _fichier_prive(base: Base, chemin: str | Path) -> Path:
    """Résout un fichier privé ordinaire, refuse un lien ou une sortie de l’atelier."""
    brut = Path(chemin).expanduser()
    candidat = brut if brut.is_absolute() else base.prive / brut
    if any(element.is_symlink() for element in (candidat, *candidat.parents)):
        raise ValueError("Lien symbolique dans un chemin d’optimisation refusé.")
    if not candidat.resolve(strict=False).is_relative_to(base.prive.resolve()):
        raise ValueError("Les fichiers d’optimisation doivent rester dans base.prive.")
    try:
        resultat = candidat.resolve(strict=True)
    except OSError as erreur:
        raise ValueError("Fichier absent de l’espace privé.") from erreur
    if not resultat.is_relative_to(base.prive.resolve()) or not resultat.is_file():
        raise ValueError("Fichier ordinaire sous base.prive requis.")
    return resultat


def _projet_prive(base: Base, projet: str | Path) -> Path:
    """Résout un projet relatif à l’atelier ; Path sans unité, FAIL hors confinement ou sur lien."""
    from benchab.chemins import sans_lien

    brut = Path(projet).expanduser()
    candidat = brut if brut.is_absolute() else base.prive / brut
    # Le contrôle des liens précède resolve : sinon leur trace disparaît (#100).
    dossier = sans_lien(candidat, racine=base.prive, strict=True)
    if not dossier.is_dir():
        raise ValueError("Projet d’optimisation privé requis.")
    return dossier


def _chemin_reference_historique(base: Base, relatif: str | None) -> Path | None:
    """Résout la référence relative ou renvoie None si elle est absente."""
    if relatif is None:
        return None
    if not isinstance(relatif, str) or not relatif:
        raise ValueError("Référence historique invalide.")
    chemin = Path(relatif)
    if chemin.is_absolute() or ".." in chemin.parts:
        raise ValueError("Les références historiques doivent être relatives à base.prive.")
    candidat = base.prive / chemin
    if any(element.is_symlink() for element in (candidat, *candidat.parents)):
        raise ValueError("Lien symbolique dans une référence historique refusé.")
    try:
        resolu = candidat.resolve(strict=True)
    except FileNotFoundError:
        return None
    if not resolu.is_relative_to(base.prive.resolve()) or not resolu.is_file():
        raise ValueError("Référence historique hors base.prive ou non ordinaire.")
    return resolu


def _observations_historiques(base: Base, maitre: dict) -> list[dict]:
    """Relit les observations privées et leur SHA, sans déduire de réglage."""
    observations = maitre.get("observations_contextuelles", [])
    if not isinstance(observations, list):
        raise ValueError("observations_contextuelles doit être une liste.")
    resultats = []
    for observation in observations:
        if not isinstance(observation, dict):
            raise ValueError("Observation contextuelle invalide.")
        chemin = _chemin_reference_historique(base, observation.get("reference"))
        if chemin is None:
            continue
        avant = chemin.stat()
        contenu = chemin.read_bytes()
        apres = chemin.stat()
        if (avant.st_dev, avant.st_ino, avant.st_size, avant.st_mtime_ns, avant.st_ctime_ns) != (
            apres.st_dev,
            apres.st_ino,
            apres.st_size,
            apres.st_mtime_ns,
            apres.st_ctime_ns,
        ):
            raise ValueError("Référence historique modifiée pendant sa lecture.")
        historique = json.loads(contenu)
        if not isinstance(historique, dict) or not isinstance(historique.get("references", []), list):
            raise ValueError("Référence historique : liste references requise.")
        resultats.append(
            {
                "observation": observation,
                "fichier": chemin.relative_to(base.prive.resolve()).as_posix(),
                "sha256": hashlib.sha256(contenu).hexdigest(),
                "references": historique.get("references", []),
            }
        )
    return resultats


def indices_historiques(base: Base, reglages: dict, filaments_profil: dict, maitre: dict) -> list[dict]:
    """Présente les références privées et écarts natifs ; aucun transfert automatique de réglages."""
    sorties = []
    for historique in _observations_historiques(base, maitre):
        observation = historique["observation"]
        for reference in historique["references"]:
            if not isinstance(reference, dict):
                raise ValueError("Entrée de référence historique invalide.")
            anciens = reference.get("settings", {})
            if not isinstance(anciens, dict):
                raise ValueError("Réglages historiques invalides.")
            ecarts = []
            filament_id = observation.get("filament_id")
            if not filaments_profil or any(item.get("id") != filament_id for item in filaments_profil.values()):
                ecarts.append("matière différente ou mélange non établi")
            for cle in ("layer_height", "nozzle_temperature", "nozzle_temperature_initial_layer"):
                if anciens.get(cle) != reglages.get(cle):
                    ecarts.append(cle + " différent ou absent")
            ecarts.extend(
                (
                    "buse historique non établie",
                    "similarité de géométrie non mesurée",
                    "vitesses atteintes et accélérations historiques non mesurées",
                )
            )
            fichier_source = _chemin_reference_historique(base, reference.get("file"))
            sorties.append(
                {
                    "reference": historique["fichier"],
                    "sha256_reference": historique["sha256"],
                    "nom": reference.get("name"),
                    "retour": reference.get("reported_result"),
                    "source_disponible": fichier_source is not None,
                    "conditions_non_etablies": ecarts,
                    "transfert_automatique": False,
                    "limite": reference.get("evidence_limit"),
                }
            )
    return sorties


def _mesurer_geometrie(source: Path) -> dict:
    """Mesure dimensions en mm et volumes en mm³ des maillages, sans valider leur tenue physique."""
    import trimesh

    ns = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
    mesure_archive = []
    with Archive3MF(source, taille_max_xml=Archive3MF.MAX_MEMBRE_OCTETS) as archive:
        modele = archive.xml("3D/Objects/pieces.model")
        if modele.get("unit", "millimeter") != "millimeter":
            raise ValueError("Unité de maillage 3MF non millimétrique.")
        for objet in modele.findall(f".//{ns}object"):
            maillage = objet.find(f"{ns}mesh")
            if maillage is None:
                continue
            sommets = maillage.find(f"{ns}vertices")
            triangles = maillage.find(f"{ns}triangles")
            if sommets is None or triangles is None:
                continue
            vertices = [
                [float(sommet.get(axe)) for axe in ("x", "y", "z")] for sommet in sommets.findall(f"{ns}vertex")
            ]
            faces = [[int(face.get(f"v{index}")) for index in (1, 2, 3)] for face in triangles.findall(f"{ns}triangle")]
            mesure_archive.append(trimesh.Trimesh(vertices=vertices, faces=faces, process=False))
    mesures = []
    for maillage in mesure_archive:
        if not len(maillage.faces):
            continue
        local = maillage.copy()
        local.apply_translation(-local.bounds[0])
        mesures.append(
            {
                "dimensions_mm": [float(valeur) for valeur in local.extents],
                "volume_mm3": float(local.volume),
                "faces": len(local.faces),
                "ferme": bool(local.is_watertight and local.is_volume),
            }
        )
    if not mesures:
        raise ValueError("Aucun maillage triangulé lisible dans le 3MF.")
    return {
        "mesures": mesures,
        "source": "géométrie triangulée issue du 3MF",
        "limites": [
            "mesures par géométrie locale, pas placement des instances",
            "épaisseur locale et détails fins non mesurés",
            "appui géométrique ne prouve ni adhésion ni supports tranchés",
        ],
    }
