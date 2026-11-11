"""Présente les visées échantillonnées sans les exporter comme géométrie."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from benchab.qualite.ecritures import destination_neuve, renommer_exclusif
from benchab.visualisation.visionneuse import Visionneuse

_TYPES = {"DÉGAGÉE": "visee_degagee", "OBSTRUÉE": "visee_obstruee", "AMBIGUË": "visee_ambigue"}
_PIECE = "Maillage STL"
_LIMITE = "Visée ponctuelle vers un plan déclaré ; aucun accès global, pont ou appui réel n’est établi."


def _diagnostic(rapport):
    """Adapte les points XYZ en mm au calque de repères de la visionneuse."""
    marqueurs = []
    omis = 0
    for groupe in rapport["groupes"]:
        mesure = groupe["visibilite_verticale_plateau"]["visées_échantillonnées"]
        omis += mesure["rayons_omis"]
        for vis in mesure["echantillons"]:
            nom = f"{groupe['id']} · face {vis['face_id']}"
            z = vis["point_xyz_mm"][2]
            plan = mesure["plateau_z_mm"]
            detail = (
                f"Obstacle numérique à Z = {vis['obstacle_z_mm']:g} mm."
                if "obstacle_z_mm" in vis else vis["raison"]
            )
            marqueurs.append({
                "id": f"{groupe['id']}:{vis['face_id']}",
                "type": _TYPES[vis["statut"]],
                "etiquette": nom,
                "piece": _PIECE,
                "source": "STL lu pour le rapport joint",
                "sha256": rapport["source_sha256"],
                "repere": "STL",
                "point_modele": vis["point_xyz_mm"],
                "normale_modele": [0.0, 0.0, -1.0],
                "valeur": z,
                "seuil": plan,
                "condition": f"Altitude du départ et plan déclaré, en mm. {detail}",
                "precision": f"Îlot de {groupe['aire_mm2']:g} mm² ; barycentre de la face sélectionnée.",
                "limite": _LIMITE,
            })
    return {"piece": _PIECE, "anomalies": marqueurs}, omis


def publier(maillage, rapport, sortie, *, prive=None):
    """Publie rapport et visionneuse hors ligne dans un dossier neuf, privé ou temporaire.

    Entrées : rapport et maillage en mm, issus du même STL déjà empreinté ; chemin sans unité.
    Sortie : chemins de la page et du rapport. Un marqueur n'est jamais un objet exporté.
    Le contrôle numérique ponctuel ne valide ni appui ni pont ; aucun réglage n'est modifié.
    """
    destination = destination_neuve(sortie, prive=prive)
    diagnostic, omis = _diagnostic(rapport)
    sous_titre = (
        f"{len(rapport['groupes'])} îlots · {len(diagnostic['anomalies'])} visées affichées · {omis} omises"
    )
    vue = Visionneuse(
        "Visées verticales des surplombs",
        sous_titre=sous_titre,
        note=_LIMITE,
        vue_initiale=(1.0, -1.4, -0.8),
        source={
            "sha256": rapport["source_sha256"],
            "visibilite_surplombs": sous_titre,
            "inspection_ephemere": True,
        },
    )
    vue.modele("source_stl", maillage)
    vue.piece(_PIECE, "source_stl", info="Repère du STL source, millimètres déclarés.")
    # Les repères du diagnostic sont posés dans la page, après chargement du
    # GLB. L'export de géométrie ne contient donc que le maillage source.
    vue.diagnostics = [diagnostic]
    with TemporaryDirectory(prefix=".surplombs-", dir=destination.parent) as temporaire:
        dossier = Path(temporaire)
        vue.ecrire(dossier / "apercu.html", prive=prive)
        (dossier / "rapport.json").write_text(
            json.dumps(rapport, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8",
        )
        renommer_exclusif(dossier, destination)
    return {"rapport": str(destination / "rapport.json"), "apercu": str(destination / "apercu.html")}
