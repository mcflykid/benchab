"""

Repère les petits îlots et sommets à revoir dans l'orientation d'impression.

Usage : benchab controle couches-courtes <stl> [hauteur_couche=.2] [aire_max=40] [hauteur_min=2]
Code 0 : aucun îlot signalé ; 2 : revue locale nécessaire.

Une section fermée d'aire <= aire_max est suivie sur des couches consécutives,
quelle que soit l'aire des autres sections. Elle est signalée si sa hauteur
échantillonnée atteint hauteur_min, ou si elle forme un sommet terminal même
plus court. Les coordonnées sont celles du STL, en mm.

Ces seuils sont des heuristiques géométriques, pas un modèle thermique ni un
diagnostic de boudinage. Les détails reliés à une grande section et ceux plus
fins que l'échantillonnage peuvent échapper au contrôle. Les branches sont
suivies par recouvrement XY entre couches ; leur segmentation reste indicative.
Revoir appuis, parcours réels, débit et refroidissement dans le tranchage,
jusqu'aux dernières couches. L'absence d'alerte ne dispense pas de cette revue.


"""

import json
import math
import sys

import numpy as np
import trimesh
from shapely.affinity import affine_transform


def _sections_xy(mesh, z):
    section = mesh.section(plane_normal=[0, 0, 1], plane_origin=[0, 0, z])
    if section is None:
        return []
    planar, to_world = section.to_2D()
    # to_2D recentre/oriente son repère : ne jamais publier ses XY directement.
    transform = [to_world[0, 0], to_world[0, 1], to_world[1, 0], to_world[1, 1], to_world[0, 3], to_world[1, 3]]
    return [affine_transform(polygon, transform) for polygon in planar.polygons_full if polygon.area > 0]


def _rapport(track, terminal, hmin):
    samples = track["samples"]
    height = samples[-1]["haut"] - samples[0]["bas"]
    if height + 1e-8 < hmin and not terminal:
        return None
    weight = sum(sample["aire"] for sample in samples)
    xy = [sum(sample["centre"][axis] * sample["aire"] for sample in samples) / weight for axis in (0, 1)]
    motifs = []
    if height + 1e-8 >= hmin:
        motifs.append("petit_ilot_continu")
    if terminal:
        motifs.append("petit_sommet_terminal")
    return {
        "centre_xy_mm": tuple(round(value, 3) for value in xy),
        "de_z": round(samples[0]["z"], 3),
        "a_z": round(samples[-1]["z"], 3),
        "hauteur_mm": round(height, 3),
        "aire_max_mm2": round(max(sample["aire"] for sample in samples), 3),
        "aire_totale_max_mm2": round(max(sample["aire_totale"] for sample in samples), 3),
        "couches": len(samples),
        "sommet_terminal": terminal,
        "motifs": motifs,
        "repere": "STL oriente impression",
    }


def analyse(path, lh=0.2, amax=40.0, hmin=2.0):
    """
    Liste compatible avec l'ancien contrôle ; hauteurs issues des sections.

        Une fin locale signifie que l'îlot n'a plus de section qui le recouvre en XY
        à l'échantillon suivant. Ce constat géométrique ne mesure pas le temps local
        de retour de la buse, le refroidissement ou les appuis du parcours tranché.

    """
    if not all(math.isfinite(value) and value > 0 for value in (lh, amax, hmin)):
        raise ValueError("hauteur de couche, aire et hauteur minimale doivent être positives et finies")
    mesh = trimesh.load_mesh(path)
    if mesh.is_empty or not np.isfinite(mesh.bounds).all():
        raise ValueError("maillage vide ou limites non finies")
    z0, z1 = map(float, mesh.bounds[:, 2])
    active, reports = [], []
    count = max(0, int(math.ceil((z1 - z0) / lh - 1e-9)))

    def finish(track, terminal):
        report = _rapport(track, terminal, hmin)
        if report is not None:
            reports.append(report)

    for layer in range(count):
        bottom, top = z0 + layer * lh, min(z0 + (layer + 1) * lh, z1)
        z = (bottom + top) / 2
        polygons = _sections_xy(mesh, z)
        total = sum(polygon.area for polygon in polygons)
        small = [polygon for polygon in polygons if polygon.area <= amax + 1e-8]
        # Une correspondance par recouvrement, jamais par centre arrondi. Un
        # trou vertical ou une couche vide termine la piste immédiatement.
        pairs = []
        for old_index, track in enumerate(active):
            for new_index, polygon in enumerate(small):
                overlap = track["polygon"].intersection(polygon).area
                if overlap > 1e-9:
                    pairs.append((overlap, old_index, new_index))
        old_used, new_to_old = set(), {}
        for _, old_index, new_index in sorted(pairs, reverse=True):
            if old_index not in old_used and new_index not in new_to_old:
                old_used.add(old_index)
                new_to_old[new_index] = old_index
        for old_index, track in enumerate(active):
            if old_index not in old_used:
                terminal = not any(track["polygon"].intersection(polygon).area > 1e-9 for polygon in polygons)
                finish(track, terminal)
        following = []
        for new_index, polygon in enumerate(small):
            track = active[new_to_old[new_index]] if new_index in new_to_old else {"samples": []}
            centre = polygon.centroid
            track["samples"].append(
                {
                    "z": z,
                    "bas": bottom,
                    "haut": top,
                    "aire": float(polygon.area),
                    "aire_totale": float(total),
                    "centre": (float(centre.x), float(centre.y)),
                }
            )
            track["polygon"] = polygon
            following.append(track)
        active = following
    for track in active:
        finish(track, True)
    return sorted(reports, key=lambda report: (report["de_z"], report["centre_xy_mm"]))


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(__doc__)
        return 2
    result = analyse(args[0], *(float(value) for value in args[1:4]))
    print(json.dumps({"fichier": args[0], "ilots_isoles": result}, indent=1, ensure_ascii=False))
    return 2 if result else 0


if __name__ == "__main__":
    raise SystemExit(main())
