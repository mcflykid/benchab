"""Lecture bornée du depsgraph d’une scène .blend; ne sauvegarde jamais le fichier source."""

import argparse
import json
import math
import sys
from pathlib import Path

import bmesh
import bpy


def inspecter(source: Path, mm_par_unite=None) -> dict:
    """Relève bornes/mm, topologie et volume signé/mm³ ; unité inconnue NV, aucune sauvegarde."""
    bpy.ops.wm.open_mainfile(filepath=str(source), load_ui=False)
    scene = bpy.context.scene
    unite = scene.unit_settings.system
    echelle = float(scene.unit_settings.scale_length)
    unite_valide = unite in {"METRIC", "IMPERIAL"} and math.isfinite(echelle) and echelle > 0
    facteur_mm = echelle * 1000.0 if unite_valide else None
    if mm_par_unite is not None:
        if not math.isfinite(mm_par_unite) or mm_par_unite <= 0:
            raise ValueError("Échelle mm par unité : nombre positif fini exigé")
        # Une scène sans unités n'est mesurable qu'avec une déclaration explicite
        # (docs/reference/commandes.md, inspection-scene), jamais par supposition.
        facteur_mm = float(mm_par_unite)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    objets = []
    for original in sorted(scene.objects, key=lambda objet: objet.name):
        if original.type != "MESH":
            continue
        objet = original.evaluated_get(depsgraph)
        maillage = objet.to_mesh(preserve_all_data_layers=False, depsgraph=depsgraph)
        try:
            points = [objet.matrix_world @ sommet.co for sommet in maillage.vertices]
            if not points or any(not all(math.isfinite(float(v)) for v in point) for point in points):
                # Un objet inutilisable ne doit pas masquer les autres mesures
                # (docs/reference/commandes.md, inspection-scene).
                objets.append({"nom": original.name, "statut": "NON VÉRIFIÉ",
                               "erreur": "maillage sans sommets ou coordonnées non finies"})
                continue
            minimum = [min(float(point[i]) for point in points) for i in range(3)]
            maximum = [max(float(point[i]) for point in points) for i in range(3)]
            facteur = facteur_mm or 1.0
            dimensions = [(maximum[i] - minimum[i]) * facteur for i in range(3)]
            bornes_min = [valeur * facteur for valeur in minimum]
            bornes_max = [valeur * facteur for valeur in maximum]
            objet_rapport = {
                "nom": original.name,
                "vertices": len(maillage.vertices),
                "faces": len(maillage.polygons),
                "modificateurs_evalues": [modificateur.name for modificateur in original.modifiers],
            }
            bm = bmesh.new()
            try:
                # Le depsgraph inclut les modificateurs, puis la matrice monde porte les volumes mesurés
                # (docs/reference/commandes.md, inspection-scene) ; le maillage de la scène reste intact.
                bm.from_mesh(maillage)
                bm.transform(objet.matrix_world)
                objet_rapport.update(
                    boundary_edges=sum(e.is_boundary for e in bm.edges),
                    non_manifold_edges=sum(not e.is_manifold for e in bm.edges),
                    degenerate_faces=sum(f.calc_area() < 1e-12 for f in bm.faces),
                )
                volume = float(bm.calc_volume(signed=True)) * facteur**3
                objet_rapport["signed_volume_mm3" if facteur_mm is not None else "signed_volume_unites_scene3"] = volume
            finally:
                bm.free()
            if facteur_mm is None:
                objet_rapport.update(
                    dimensions_unites_scene=dimensions,
                    minimum_unites_scene=bornes_min,
                    maximum_unites_scene=bornes_max,
                )
            else:
                objet_rapport.update(dimensions_mm=dimensions, minimum_mm=bornes_min, maximum_mm=bornes_max)
            objets.append(objet_rapport)
        finally:
            objet.to_mesh_clear()
    return {
        "schema": 1,
        "statut_geometrique": "PASS" if objets and facteur_mm is not None and not any(
            "erreur" in objet for objet in objets
        ) else "NON VÉRIFIÉ" if objets else "WARN",
        "version_blender": bpy.app.version_string,
        "unite_scene": unite,
        "facteur_mm_par_unite": facteur_mm,
        "origine_unites": "déclaration explicite"
        if mm_par_unite is not None
        else "scène"
        if facteur_mm is not None
        else "inconnue",
        "objets": objets,
        "limite": (
            "Mesures de la scène évaluée ; volume algébrique, pas validation de matière, de paroi ou de tenue physique."
        ),
    }


def main() -> None:
    """Écrit le relevé demandé dans un rapport temporaire neuf, sans sauvegarder la scène."""
    arguments = sys.argv[sys.argv.index("--") + 1 :]
    parser = argparse.ArgumentParser()
    parser.add_argument("--mm-par-unite", type=float)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args(arguments)
    rapport = inspecter(args.source, args.mm_par_unite)
    args.destination.write_text(json.dumps(rapport, ensure_ascii=False, allow_nan=False), encoding="utf-8")


main()
