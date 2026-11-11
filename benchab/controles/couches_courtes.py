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
qualification_sections décrit uniquement les recouvrements entre coupes XY
échantillonnées ; son statut reste NON VÉRIFIÉ pour tout appui réel après tranchage.
L’option --qualifier-position-enveloppes classe en supplément la section par rapport
aux autres enveloppes de la même coupe ; les changements de classe restent ambigus.
Voir docs/reference/couches-courtes.md.


"""

import json
import math
import sys

import numpy as np
import trimesh
from shapely.affinity import affine_transform
from shapely.errors import GEOSException
from shapely.ops import unary_union
from shapely.strtree import STRtree

_AIRE_INTERSECTION_MIN_MM2 = 1e-9


def _sections_xy(mesh, z):
    section = mesh.section(plane_normal=[0, 0, 1], plane_origin=[0, 0, z])
    if section is None:
        return []
    planar, to_world = section.to_2D()
    # to_2D recentre/oriente son repère : ne jamais publier ses XY directement.
    transform = [to_world[0, 0], to_world[0, 1], to_world[1, 0], to_world[1, 1], to_world[0, 3], to_world[1, 3]]
    return [affine_transform(polygon, transform) for polygon in planar.polygons_full if polygon.area > 0]


def _mesurer_recouvrement_xy(polygon, polygones_precedents, *, couche, union_precedente=None):
    """Retourne la relation et l'aire (mm²) de recouvrement entre coupes XY.

    Les entrées sont des polygones exprimés en mm et l'indice de couche est sans
    unité. La fraction est relative à l'aire du polygone courant. Les premières
    coupes et intersections sous la précision numérique n'ont pas de fraction ;
    aucune valeur ne constitue une preuve d'appui réel ou de contact plateau.
    """
    if not polygones_precedents:
        if couche == 0:
            return "premier_echantillon_du_maillage", None, None
        return "sans_recouvrement_xy_echantillonne", 0.0, 0.0
    union = union_precedente if union_precedente is not None else unary_union(polygones_precedents)
    aire = float(polygon.intersection(union).area)
    fraction = min(1.0, max(0.0, aire / polygon.area)) if polygon.area > 0 else None
    if polygon.area <= _AIRE_INTERSECTION_MIN_MM2 or 0 < aire <= _AIRE_INTERSECTION_MIN_MM2:
        return "recouvrement_non_qualifie_precision_numerique", aire, None
    if aire == 0:
        return "sans_recouvrement_xy_echantillonne", aire, fraction
    if polygon.area - aire <= _AIRE_INTERSECTION_MIN_MM2:
        return "recouvrement_complet_xy_echantillonne", aire, fraction
    return "recouvrement_partiel_xy_echantillonne", aire, fraction


def _relations_projection_xy(polygones_precedents, polygones_courants):
    """Indexe les relations entre deux coupes et signale les branches projetées.

    Retourne, pour chaque polygone courant, ses indices parents précédents et
    un booléen de branche. Les intersections sont confirmées par une aire en
    mm² strictement supérieure à ``_AIRE_INTERSECTION_MIN_MM2``.
    """
    parents_par_courant = [[] for _ in polygones_courants]
    branches = [False] * len(polygones_courants)
    if not polygones_precedents or not polygones_courants:
        return parents_par_courant, branches

    arbre = STRtree(polygones_courants)
    enfants_par_precedent = [[] for _ in polygones_precedents]
    for indice_precedent, precedent in enumerate(polygones_precedents):
        candidats = arbre.query(precedent, predicate="intersects")
        for indice_courant_brut in candidats:
            indice_courant = int(indice_courant_brut)
            courant = polygones_courants[indice_courant]
            if precedent.intersection(courant).area > _AIRE_INTERSECTION_MIN_MM2:
                parents_par_courant[indice_courant].append(indice_precedent)
                enfants_par_precedent[indice_precedent].append(indice_courant)
    for enfants in enfants_par_precedent:
        if len(enfants) > 1:
            for indice_courant in enfants:
                branches[indice_courant] = True
    return parents_par_courant, branches


def _position_enveloppes_xy(polygon, enveloppes):
    """Classe strictement une section face aux enveloppes XY de sa même couche.

    Entrées : polygones Shapely en mm ; sortie `RENSEIGNÉE` pour inclusion stricte
    dans un trou ou extériorité stricte, sinon `NON VÉRIFIÉ`. Aucune inférence 3D.
    """
    if polygon is None or polygon.geom_type != "Polygon" or not polygon.is_valid or polygon.area <= 0:
        return {"statut": "NON VÉRIFIÉ", "classe": "ambigue", "cause": "geometrie_invalide"}
    trous = []
    try:
        for enveloppe in enveloppes:
            if enveloppe is None or enveloppe.geom_type != "Polygon" or not enveloppe.is_valid:
                return {"statut": "NON VÉRIFIÉ", "classe": "ambigue", "cause": "geometrie_invalide"}
            for anneau in enveloppe.interiors:
                trou = type(enveloppe)(anneau)
                if polygon.boundary.intersects(trou.boundary):
                    return {"statut": "NON VÉRIFIÉ", "classe": "ambigue", "cause": "frontiere_touchee"}
                if trou.contains(polygon):
                    trous.append(trou)
            if polygon.boundary.intersects(enveloppe.boundary):
                return {"statut": "NON VÉRIFIÉ", "classe": "ambigue", "cause": "frontiere_touchee"}
            if polygon.intersection(enveloppe).area > 0:
                return {"statut": "NON VÉRIFIÉ", "classe": "ambigue", "cause": "chevauchement_enveloppe"}
    except (AttributeError, GEOSException, TypeError, ValueError):
        return {"statut": "NON VÉRIFIÉ", "classe": "ambigue", "cause": "geometrie_invalide"}
    if len(trous) > 1:
        return {"statut": "NON VÉRIFIÉ", "classe": "ambigue", "cause": "trous_multiples"}
    if trous:
        return {"statut": "RENSEIGNÉE", "classe": "interieur_d_un_trou", "cause": None}
    return {"statut": "RENSEIGNÉE", "classe": "exterieur_aux_enveloppes", "cause": None}


def _rapport(track, terminal, lh, amax, hmin, qualifier_positions=False):
    """Construit le rapport historique et ses observations de coupes échantillonnées.

    ``lh``, ``amax`` et ``hmin`` sont en mm et mm² selon leur nom. Le champ de
    qualification conserve le statut NON VÉRIFIÉ pour les appuis après tranchage.
    """
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
    qualifications = [sample["qualification_xy"] for sample in samples]
    fractions = [
        detail["fraction_recouvrement_xy"]
        for detail in qualifications
        if detail["fraction_recouvrement_xy"] is not None
    ]
    relations = {detail["relation_appui_xy"] for detail in qualifications}
    ambiguites = []
    if any(detail["branche_projection_xy"] or detail["fusion_projection_xy"] for detail in qualifications):
        ambiguites.append("association_un_a_un_non_prouvee_branche_ou_fusion")
    if "recouvrement_non_qualifie_precision_numerique" in relations:
        ambiguites.append("recouvrement_sous_precision_numerique")
    qualification_sections = {
        "statut": "NON VÉRIFIÉ",
        "candidat_pose_au_z_min_du_maillage": (
            qualifications[0]["relation_appui_xy"] == "premier_echantillon_du_maillage"
        ),
        "premiere_relation_appui_xy": qualifications[0]["relation_appui_xy"],
        "relations_appui_xy_observees": sorted({detail["relation_appui_xy"] for detail in qualifications}),
        "fraction_recouvrement_xy_min": round(min(fractions), 6) if fractions else None,
        "aire_recouvrement_xy_min_mm2": round(
            min(
                detail["aire_recouvrement_xy_mm2"]
                for detail in qualifications
                if detail["aire_recouvrement_xy_mm2"] is not None
            ),
            6,
        ) if any(detail["aire_recouvrement_xy_mm2"] is not None for detail in qualifications) else None,
        "branche_projection_xy": any(detail["branche_projection_xy"] for detail in qualifications),
        "fusion_projection_xy": any(detail["fusion_projection_xy"] for detail in qualifications),
        "association_pistes_ambigue": any(
            detail["branche_projection_xy"] or detail["fusion_projection_xy"]
            for detail in qualifications
        ),
        "ambiguites": ambiguites,
        "cavite_section_detectee": any(detail["cavites_section"] > 0 for detail in qualifications),
        "cavites_section_max": max(detail["cavites_section"] for detail in qualifications),
        "sommet_terminal_echantillonne": terminal,
        "contact_plateau_prouve": False,
        "pas_echantillonnage_z_mm": lh,
        "seuils_heuristiques": {
            "aire_max_mm2": amax,
            "hauteur_min_mm": hmin,
            "origine": "paramètres explicites de la commande ; aucune calibration physique",
        },
        "aire_intersection_numerique_min_mm2": _AIRE_INTERSECTION_MIN_MM2,
        "portee": (
            "Sections XY aux milieux d'intervalles Z ; projection géométrique seulement. "
            "Ne prouve ni contact plateau, ni appui réel après tranchage, ni cause physique."
        ),
    }
    if qualifier_positions:
        positions = [sample["qualification_xy"]["position_enveloppe_xy"] for sample in samples]
        classes = {position["classe"] for position in positions}
        causes = [position["cause"] for position in positions if position["cause"]]
        if any(position["statut"] != "RENSEIGNÉE" for position in positions):
            resume_position = {
                "statut": "NON VÉRIFIÉ", "classe": "ambigue",
                "cause": causes[0] if causes else "position_ambigue",
            }
        elif len(classes) > 1:
            resume_position = {
                "statut": "NON VÉRIFIÉ", "classe": "ambigue",
                "cause": "position_change_au_fil_des_couches",
            }
        else:
            resume_position = {"statut": "RENSEIGNÉE", "classe": next(iter(classes)), "cause": None}
        qualification_sections["position_enveloppe_xy"] = {
            **resume_position,
            "par_couche": [
                {"couche": sample["qualification_xy"]["couche"], **position}
                for sample, position in zip(samples, positions, strict=True)
            ],
            "portee": (
                "Position de coupe XY face aux enveloppes de la même coupe ; "
                "aucune preuve de cavité 3D ou d'appui."
            ),
        }
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
        "qualification_sections": qualification_sections,
    }


def analyse(path, lh=0.2, amax=40.0, hmin=2.0, *, qualifier_positions=False):
    """
    Retourne les îlots géométriques et leurs observations de sections XY.

    ``path`` désigne un maillage STL ; ``lh`` et ``hmin`` sont en mm, ``amax``
    en mm². Le rapport conserve les champs historiques et ajoute
    ``qualification_sections`` au statut NON VÉRIFIÉ pour tout appui après
    tranchage. ``qualifier_positions`` ajoute optativement la position XY face
    aux enveloppes de la même coupe. La fonction ne modifie ni maillage ni réglages.

    Une fin locale signifie que l'îlot n'a plus de section qui le recouvre en XY
    à l'échantillon suivant. Ce constat ne mesure pas le temps local de retour de
    la buse, le refroidissement ou les appuis du parcours tranché.

    """
    if not all(math.isfinite(value) and value > 0 for value in (lh, amax, hmin)):
        raise ValueError("hauteur de couche, aire et hauteur minimale doivent être positives et finies")
    mesh = trimesh.load_mesh(path)
    if mesh.is_empty or not np.isfinite(mesh.bounds).all():
        raise ValueError("maillage vide ou limites non finies")
    z0, z1 = map(float, mesh.bounds[:, 2])
    active, reports = [], []
    polygones_precedents = []
    count = max(0, int(math.ceil((z1 - z0) / lh - 1e-9)))

    def finish(track, terminal):
        report = _rapport(track, terminal, lh, amax, hmin, qualifier_positions)
        if report is not None:
            reports.append(report)

    for layer in range(count):
        bottom, top = z0 + layer * lh, min(z0 + (layer + 1) * lh, z1)
        z = (bottom + top) / 2
        polygons = _sections_xy(mesh, z)
        total = sum(polygon.area for polygon in polygons)
        arbre_position = STRtree(polygons) if qualifier_positions and polygons else None
        small = [polygon for polygon in polygons if polygon.area <= amax + 1e-8]
        union_precedente = unary_union(polygones_precedents) if polygones_precedents else None
        parents_par_polygone, branches_par_polygone = _relations_projection_xy(
            polygones_precedents, polygons
        )
        indices_petites = [
            index for index, polygon in enumerate(polygons) if polygon.area <= amax + 1e-8
        ]
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
            relation_appui, aire_recouverte, fraction_recouverte = _mesurer_recouvrement_xy(
                polygon, polygones_precedents, couche=layer, union_precedente=union_precedente
            )
            parents = parents_par_polygone[indices_petites[new_index]]
            branche = branches_par_polygone[indices_petites[new_index]]
            position = None
            if qualifier_positions:
                # Recherche par boîtes : une île dans un trou ne coupe pas
                # l’anneau, mais ses boîtes XY se recouvrent.
                enveloppes = [
                    polygons[int(index)]
                    for index in arbre_position.query(polygon)
                    if int(index) != indices_petites[new_index]
                ]
                position = _position_enveloppes_xy(
                    polygon,
                    enveloppes,
                )
            track["samples"].append(
                {
                    "z": z,
                    "bas": bottom,
                    "haut": top,
                    "aire": float(polygon.area),
                    "aire_totale": float(total),
                    "centre": (float(centre.x), float(centre.y)),
                    "qualification_xy": {
                        "relation_appui_xy": relation_appui,
                        "aire_recouvrement_xy_mm2": aire_recouverte,
                        "fraction_recouvrement_xy": fraction_recouverte,
                        "branche_projection_xy": branche,
                        "fusion_projection_xy": len(parents) > 1,
                        "cavites_section": len(polygon.interiors),
                        "couche": layer,
                        **({"position_enveloppe_xy": position} if position is not None else {}),
                    },
                }
            )
            track["polygon"] = polygon
            following.append(track)
        active = following
        polygones_precedents = polygons
    for track in active:
        finish(track, True)
    return sorted(reports, key=lambda report: (report["de_z"], report["centre_xy_mm"]))


def main(argv=None):
    """Analyse un STL en mm et affiche les îlots et leurs observations en JSON.

    Reçoit le chemin source puis les seuils de couche et hauteur (mm) et
    d’aire (mm²), et une option de position XY. Code 0 sans îlot, 2 si des
    îlots sont signalés ou si la source manque. Aucun maillage modifié.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    qualifier_positions = "--qualifier-position-enveloppes" in args
    args = [arg for arg in args if arg != "--qualifier-position-enveloppes"]
    if not args:
        print(__doc__)
        return 2
    result = analyse(args[0], *(float(value) for value in args[1:4]), qualifier_positions=qualifier_positions)
    print(json.dumps({"fichier": args[0], "ilots_isoles": result}, indent=1, ensure_ascii=False))
    return 2 if result else 0


if __name__ == "__main__":
    raise SystemExit(main())
