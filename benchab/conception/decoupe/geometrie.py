"""Sections, propositions et coupes complémentaires, sans altérer le modèle.

Une séparation inférée reste une proposition : le maillage ne révèle pas
l'intention de son auteur. Les surfaces courbes sont des surfaces triangulées
passant par une boucle périodique de contrôle, dans un repère déclaré.
"""

import manifold3d as mf
import numpy as np
import trimesh
from shapely.geometry import Polygon, box

from benchab.conception.decoupe import _plan, _repere, _vers_manifold, _vers_trimesh, section
from benchab.controles.orientations_supports import mesure_rapide, pose, zone_etude

TOLERANCE_RELATIVE = 1e-5
TOLERANCE_INTERSECTION_MM3 = 1e-5
MAX_FACES = 3_000_000


def solide(mesh):
    """Convertit un maillage fermé en solide Manifold et refuse les coordonnées invalides ou hors budget."""
    if not isinstance(mesh, trimesh.Trimesh) or mesh.is_empty or len(mesh.faces) > MAX_FACES:
        raise ValueError("Maillage vide ou budget de trois millions de faces dépassé")
    if not np.isfinite(mesh.vertices).all() or not mesh.is_volume:
        raise ValueError("Maillage fermé, orienté, fini et de volume positif exigé ; aucune réparation implicite")
    return _vers_manifold(mesh)


def separer_volumes(mesh):
    """Composantes disjointes réelles ; aucune segmentation sémantique inventée."""
    corps = solide(mesh)
    parties = [_vers_trimesh(p) for p in corps.decompose()]
    parties.sort(key=lambda p: (*p.bounds[0], -p.volume))
    return parties


def orienter(mesh, normale=None, plateau=None):
    """Oriente une copie sur le plateau en mm et retourne la transformation et le bilan numérique."""
    directions = [np.array([0.0, 0.0, -1.0])]
    if normale is not None:
        n = np.asarray(normale, float)
        directions.extend((n, -n))
    # Les grandes facettes sont des faces d'appui candidates, dans le même outil
    # de pose que les études d'orientation ; aucune rotation du modèle original.
    if len(mesh.facets_area):
        directions.extend(mesh.facets_normal[np.argsort(mesh.facets_area)[-8:]])
    choix = []
    for direction in directions:
        matrice, tient = pose(mesh, direction, plateau)
        copie = mesh.copy().apply_transform(matrice)
        bilan = mesure_rapide(copie)
        choix.append(
            ((not tient, bilan["surplomb_mm2"], -bilan["appui_mm2"], copie.extents[2]), copie, matrice, bilan, tient)
        )
    _, copie, matrice, bilan, tient = min(choix, key=lambda p: p[0])
    return copie, matrice, {**bilan, "tient_plateau": bool(tient), "validation_physique": False}


def _boucle(points, normale, fermee=True):
    p = np.asarray(points, float)
    if not fermee or p.ndim != 2 or p.shape[1] != 3 or not 3 <= len(p) <= 128 or not np.isfinite(p).all():
        raise ValueError("Courbe fermée : 3 à 128 points de contrôle finis exigés")
    if np.allclose(p[0], p[-1]):
        p = p[:-1]
    origine = p.mean(axis=0)
    _, n = _plan({"origine": origine, "normale": normale})
    rotation = _repere(n)
    local = (p - origine) @ rotation
    # Catmull–Rom périodique : les points de contrôle sont conservés à t=0.
    echantillons = []
    for i in range(len(local)):
        a, b, c, d = (local[(i + j) % len(local)] for j in (-1, 0, 1, 2))
        for t in np.linspace(0, 1, 4, endpoint=False):
            echantillons.append(
                0.5 * (2 * b + (-a + c) * t + (2 * a - 5 * b + 4 * c - d) * t * t + (-a + 3 * b - 3 * c + d) * t**3)
            )
    local = np.asarray(echantillons)
    polygone = Polygon(local[:, :2])
    if not polygone.is_valid or polygone.area <= 1e-6:
        raise ValueError("Courbe croisée ou dégénérée dans son repère : changer les points ou la normale")
    return origine, rotation, local, polygone


def surface_courbe(mesh, coupe):
    """Construit le volume de coupe courbe à partir des points en mm et retourne ses caractéristiques."""
    points = np.asarray(coupe["points"], float)
    origine, rotation, contour, polygone = _boucle(
        coupe["points"], coupe.get("normale", [0, 0, 1]), coupe.get("fermee", True)
    )
    _, distances, _ = trimesh.proximity.closest_point(mesh, points)
    tolerance_surface = max(0.1, float(np.linalg.norm(mesh.extents)) * 1e-4)
    if max(distances) > tolerance_surface:
        raise ValueError("Points de courbe hors du modèle : les replacer sur sa surface")
    local_modele = (np.asarray(mesh.vertices) - origine) @ rotation
    bas, haut = local_modele.min(axis=0), local_modele.max(axis=0)
    marge = max(float(np.linalg.norm(haut - bas)), 1.0)
    xmin, ymin = np.minimum(bas[:2], contour[:, :2].min(axis=0)) - marge
    xmax, ymax = np.maximum(haut[:2], contour[:, :2].max(axis=0)) + marge
    exterieur = box(xmin, ymin, xmax, ymax)
    # La courbe intérieure impose les hauteurs ; l'anneau prolonge la surface
    # jusqu'au-delà de toute la pièce. Pas de paroi verticale parasite au cou.
    sommets, triangles = [], []
    for region in (polygone, exterieur.difference(polygone)):
        xy, faces = trimesh.creation.triangulate_polygon(region, engine="earcut")
        distances = np.linalg.norm(xy[:, None, :] - contour[None, :, :2], axis=2)
        proches = distances.argmin(axis=1)
        z = np.where(distances[np.arange(len(xy)), proches] < 1e-7, contour[proches, 2], 0.0)
        triangles.extend((faces + len(sommets)).tolist())
        sommets.extend(np.c_[xy, z].tolist())
    dessus = trimesh.Trimesh(sommets, triangles, process=True)
    # Le dessus doit être orienté +Z avant de fermer le volume inférieur.
    faces = np.asarray(dessus.faces).copy()
    faces[dessus.face_normals[:, 2] < 0] = faces[dessus.face_normals[:, 2] < 0][:, ::-1]
    v = np.asarray(dessus.vertices)
    bas_z = min(float(bas[2]), float(contour[:, 2].min())) - marge
    nb = len(v)
    total = np.vstack([v, np.c_[v[:, :2], np.full(nb, bas_z)]])
    fermeture = [*faces.tolist(), *(faces[:, ::-1] + nb).tolist()]
    uniques, comptes = np.unique(
        np.sort(np.vstack([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1), axis=0, return_counts=True
    )
    for a, b in uniques[comptes == 1]:
        fermeture.extend(([a, a + nb, b + nb], [a, b + nb, b]))
    volume = trimesh.Trimesh(total, fermeture, process=True)
    volume.fix_normals()
    volume.apply_transform(np.vstack([np.c_[rotation, origine], [0, 0, 0, 1]]))
    return solide(volume), {
        "type": "courbe",
        "fermee": True,
        "points_controle": len(coupe["points"]),
        "echantillons_boucle": len(contour),
        "surface": "triangulation Catmull–Rom périodique",
        "precision": "Approximation facettée ; examiner la couture avant export",
        "tolerance_points_surface_mm": tolerance_surface,
        "courbe_affichee": "Courbe de contrôle approchée ; le joint réel est le bord des pièces calculées",
    }


def couper(mesh, coupe):
    """Coupe le maillage et retourne les pièces en mm avec le bilan numérique de réassemblage."""
    corps = solide(mesh)
    if coupe.get("type", "plan") == "plan":
        o, n = _plan({k: coupe[k] for k in ("origine", "normale")})
        dessus, dessous = corps.split_by_plane(n.tolist(), float(np.dot(o, n)))
        info = {"type": "plan", "origine": o.tolist(), "normale": n.tolist()}
    elif coupe["type"] == "courbe":
        outil, info = surface_courbe(mesh, coupe)
        dessous, dessus = corps ^ outil, corps - outil
    else:
        raise ValueError("Type de coupe inconnu : plan ou courbe")
    if min(dessous.volume(), dessus.volume()) <= max(1e-6, corps.volume() * 1e-9):
        raise ValueError("La coupe ne sépare pas la pièce en deux volumes significatifs")
    parties = {"dessous": _vers_trimesh(dessous), "dessus": _vers_trimesh(dessus)}
    bilan = reassemblage(mesh, parties)
    if bilan["statut"] != "PASS":
        raise ValueError("Coupe non conforme : " + str(bilan))
    return parties, {**info, "reassemblage": bilan}


def reassemblage(original, parties, *, volume_ajoute=0.0, volume_retire=0.0, tolerance=TOLERANCE_RELATIVE):
    """Vérifie écarts et intersections en mm³ à tolérance relative donnée. Retourne PASS ou FAIL."""
    source = solide(original)
    corps = [(nom, solide(m)) for nom, m in parties.items()]
    if not 0 < tolerance <= 1e-3:
        raise ValueError("Tolérance relative de volume attendue entre 0 et 0,001")
    tol_mm3 = max(1e-5, float(source.volume()) * tolerance)
    chevauchements = []
    for i, (nom, a) in enumerate(corps):
        for nom_b, b in corps[i + 1 :]:
            v = float((a ^ b).volume())
            if v > TOLERANCE_INTERSECTION_MM3:
                chevauchements.append({"a": nom, "b": nom_b, "volume_mm3": v})
    volume = sum(p.volume() for _, p in corps)
    attendu = source.volume() + volume_ajoute - volume_retire
    ecart = float(volume - attendu)
    # Pour une coupe sans connecteur, l'égalité des volumes seule ne suffit pas :
    # les deux différences géométriques doivent aussi être vides à la tolérance.
    union = mf.Manifold.batch_boolean([p for _, p in corps], mf.OpType.Add)
    manquant, excedent = float((source - union).volume()), float((union - source).volume())
    identite = volume_ajoute != 0 or volume_retire != 0 or max(manquant, excedent) <= tol_mm3
    return {
        "statut": "PASS" if abs(ecart) <= tol_mm3 and not chevauchements and identite else "FAIL",
        "volume_original_mm3": float(source.volume()),
        "volume_pieces_mm3": float(volume),
        "volume_ajoute_mm3": volume_ajoute,
        "volume_retire_mm3": volume_retire,
        "ecart_mm3": ecart,
        "tolerance_relative": tolerance,
        "tolerance_mm3": tol_mm3,
        "tolerance_intersection_mm3": TOLERANCE_INTERSECTION_MM3,
        "interpenetrations": chevauchements,
        "manquant_mm3": manquant,
        "excedent_mm3": excedent,
        "transformations_reassemblage": {nom: np.eye(4).tolist() for nom in parties},
        "validation_physique": False,
    }


def boucles_section(mesh, origine, normale, nombre=12):
    """Échantillonne en mm les boucles de section au plan donné par son origine et sa normale."""
    o, n = _plan({"origine": origine, "normale": normale})
    polygones, vers3d = section(mesh, o, n)
    if not polygones:
        raise ValueError("Aucune section à cet emplacement")
    resultat = []
    for poly in sorted(polygones, key=lambda p: (-p.area, p.centroid.x, p.centroid.y)):
        bord = poly.exterior
        xy = np.asarray([bord.interpolate(i / nombre, normalized=True).coords[0] for i in range(nombre)])
        resultat.append(trimesh.transform_points(np.c_[xy, np.zeros(len(xy))], vers3d).tolist())
    return resultat


def boucle_section(mesh, origine, normale, nombre=12):
    """Retourne la première boucle de section échantillonnée dans le repère 3D en mm."""
    return boucles_section(mesh, origine, normale, nombre)[0]


def proposer(mesh, nombre=2, *, plateau=None, faces_visibles=None, echantillons=49):
    """Propose des plans de coupe numériques pour le maillage et le plateau en mm."""
    if type(nombre) is not int or not 1 <= nombre <= 12:
        raise ValueError("Nombre de pièces : entier de 1 à 12")
    if type(echantillons) is not int or not 15 <= echantillons <= 129:
        raise ValueError("Nombre de sections borné entre 15 et 129")
    corps = solide(mesh)
    plateau = zone_etude() if plateau is None else plateau
    composantes = separer_volumes(mesh)
    baseline = mesh.copy().apply_translation(-mesh.bounds[0])
    avant = mesure_rapide(baseline)
    meilleur_monobloc = orienter(mesh, plateau=plateau)[2]
    candidats = []
    # Sections manifold : ne pas construire 3×49 maillages pour mesurer les aires.
    for axe in range(3):
        n = np.eye(3)[axe]
        rotation = _repere(n)
        local = mesh.copy().apply_transform(np.vstack([np.c_[rotation.T, [0, 0, 0]], [0, 0, 0, 1]]))
        mlocal = solide(local)
        zs = np.linspace(local.bounds[0, 2], local.bounds[1, 2], echantillons + 2)[1:-1]
        aires = np.asarray([mlocal.slice(float(z)).area() for z in zs])
        if not aires.max() > 1e-6:
            continue
        indices = {len(zs) // 2}
        for i in range(3, len(zs) - 3):
            gauche, droite = max(aires[:i]), max(aires[i + 1 :])
            if aires[i] > 0 and aires[i] <= min(aires[i - 1], aires[i + 1]) and min(gauche, droite) > 1.15 * aires[i]:
                indices.add(i)
        # Le dépassement du plateau et les branches longues sont évalués aussi
        # aux tiers, même si aucune gorge n'existe.
        indices.update((len(zs) // 3, 2 * len(zs) // 3))
        for i in sorted(indices):
            o = (n * zs[i]).tolist()
            try:
                parties, _ = couper(mesh, {"origine": o, "normale": n.tolist()})
            except ValueError:
                continue
            volumes_coupes = [q for p in parties.values() for q in separer_volumes(p)]
            petite = min(p.volume for p in volumes_coupes) / mesh.volume
            if petite < 0.02:
                continue
            gorge = max(
                0.0, 1 - float(aires[i] / max(min(max(aires[:i], default=0), max(aires[i + 1 :], default=0)), 1e-9))
            )
            bilans = [orienter(p, n, plateau)[2] for p in volumes_coupes]
            support_apres = sum(b["surplomb_mm2"] for b in bilans)
            reduction = (avant["surplomb_mm2"] - support_apres) / max(avant["surplomb_mm2"], 1.0)
            tient = all(b["tient_plateau"] for b in bilans)
            score = 60 * gorge + 30 * max(0.0, reduction) + 10 * petite + (10 if tient else -20)
            candidats.append(
                {
                    "id": f"axe-{axe}-{i}",
                    "type": "plan",
                    "origine": o,
                    "normale": n.tolist(),
                    "score": round(score, 4),
                    "nombre_volumes": len(volumes_coupes),
                    "etranglement": round(gorge, 4),
                    "raison": "Rétrécissement naturel de section"
                    if gorge > 0.15
                    else "Séparation évaluée pour l'appui, les surplombs et le plateau",
                    "comparaison": {
                        "monobloc": avant,
                        "decoupe": {
                            "surplomb_mm2": support_apres,
                            "tient_plateau": tient,
                            "portee": "Pièces nues avant connecteurs ; compléter par poses_export et tranchage",
                        },
                        "monobloc_pose_optimisee": meilleur_monobloc,
                        "temps": "NON VÉRIFIÉ : trancher les variantes",
                        "aspect": "NON VÉRIFIÉ : couture et faces visibles à relire",
                        "solidite": "NON VÉRIFIÉ : assemblage à qualifier physiquement",
                        "reussite": "NON VÉRIFIÉ : aucune probabilité inventée",
                    },
                }
            )
    candidats.sort(key=lambda p: (-p["score"], p["id"]))
    # Une seule surface peut séparer plusieurs branches : un plan sous une
    # jonction à trois bras donne quatre pièces, pas un paquet de trois bras.
    exacts = [c for c in candidats if c["nombre_volumes"] == nombre]
    if exacts:
        candidats = [exacts[0], *[c for c in candidats if c is not exacts[0]]]
    choisis = []
    for c in candidats:
        if len(choisis) >= nombre - 1:
            break
        if all(
            np.linalg.norm(np.asarray(c["origine"]) - d["origine"]) > max(mesh.extents) * 0.08
            or c["normale"] != d["normale"]
            for d in choisis
        ):
            choisis.append(c)
        if len(choisis) == nombre - 1:
            break
        if len(choisis) == 1 and c["nombre_volumes"] == nombre:
            break
    declencheurs = []
    if len(composantes) > 1:
        declencheurs.append("volumes disjoints")
    if any(c["etranglement"] > 0.15 for c in candidats):
        declencheurs.append("rétrécissement de section")
    if avant["surplomb_mm2"] > 1:
        declencheurs.append("surplombs dans la pose source")
    if any(a > b for a, b in zip(mesh.extents, plateau, strict=True)):
        declencheurs.append("dépassement du plateau")
    if faces_visibles:
        declencheurs.append("faces visibles ou fonctionnelles déclarées ; revue de couture requise")
    return {
        "schema": 1,
        "unites": "mm",
        "nombre_demande": nombre,
        "volumes_disjoints": len(composantes),
        "coupes": choisis,
        "alternatives": candidats[:12],
        "declencheurs": declencheurs,
        "validation_physique": False,
        "statut": "WARN" if declencheurs else "NON VÉRIFIÉ",
        "limites": [
            "Une gorge n'est pas une preuve de l'intention de l'auteur.",
            "Volumes fusionnés sans frontière identifiable : séparation sémantique NON VÉRIFIÉE.",
            "Les faces visibles sont déclarées ; aucun détecteur de couture 1.1 n'est activé.",
        ],
        "volume_original_mm3": float(corps.volume()),
    }
