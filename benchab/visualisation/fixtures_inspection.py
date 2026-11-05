"""Source synthétique de réception, dossier de sortie neuf exigé, aucun slicer."""

import argparse
import json
import shutil
from pathlib import Path

import numpy as np
import trimesh

from benchab.chemins import atelier_prive, est_temporaire, sans_lien
from benchab.visualisation.catalogue_visuel import scene
from benchab.visualisation.hors_ligne import VENDOR
from benchab.visualisation.paquet_inspection import paquet


def valider_sortie(sortie):
    """Valide une sortie privée ou temporaire, sans unité ; lève FAIL avant toute écriture hors racine ou via lien."""
    sortie = Path(sortie).expanduser().absolute()
    if ".." in sortie.parts:
        raise ValueError("Traversée de dossier interdite dans la sortie de fixture")
    sortie = sans_lien(sortie)
    prive = atelier_prive()
    if not (sortie.is_relative_to(prive) or est_temporaire(sortie)):
        raise ValueError("la sortie de fixture doit rester dans BENCHAB_PRIVE ou /private/tmp")
    return sortie


def creer(sortie):
    """Crée une fixture synthétique neuve (géométrie/mm), renvoie sa racine ; aucune source réelle ni slicer."""
    sortie = valider_sortie(sortie)
    sortie.mkdir(parents=True, exist_ok=False)
    p = sortie / "projets/inspection-ete"
    (p / "candidats").mkdir(parents=True)
    meshes = {}
    m = trimesh.creation.box((10, 10, 10))
    m.apply_translation((0, 0, 5))
    m.update_faces(np.arange(len(m.faces)) != 0)
    meshes["Ouvert"] = m
    for nom, hauteur in (("BaseA", 10), ("BaseB", 12)):
        m = trimesh.creation.box((1, 1, hauteur))
        m.apply_translation((0, 0, hauteur / 2))
        meshes[nom] = m
    parts = []
    for i, (nom, m) in enumerate(meshes.items()):
        m.export(p / "candidats" / (nom + ".stl"))
        matrix = np.eye(4)
        matrix[0, 3] = i * 20
        parts.append({"name": nom, "model": nom, "matrix": matrix.tolist()})
    (p / "candidats/assemblage.json").write_text(json.dumps({"models": {n: {} for n in meshes}, "parts": parts}))
    q = {
        "suivi": {
            "titre": "Inspection été — sources synthétiques",
            "statut": "EN_PREPARATION",
            "references_imprimees": [{"fichiers": [{"chemin": "reference/envoi.gcode"}]}],
        }
    }
    (p / "qualite.json").write_text(json.dumps(q, ensure_ascii=False))
    (p / "reference").mkdir()
    (p / "reference/envoi.gcode").write_text(
        "G90\nM83\n;Z:0.2\nG1 Z0.2\n;TYPE:Outer wall\nG1 X0 Y0 F1200\nG1 X10 Y0 E1\n;Z:0.4\nG1 Z0.4\nG1 X10 Y10 E1\n"
    )
    shutil.copytree(VENDOR, sortie / "benchab/visualisation/vendor/three-0.160.0")
    (sortie / "inspection.html").write_bytes(scene(sortie, "inspection-ete"))
    (sortie / "inspection.zip").write_bytes(paquet(sortie, "inspection-ete"))
    return sortie


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sortie", type=Path, required=True)
    print(creer(p.parse_args().sortie))
