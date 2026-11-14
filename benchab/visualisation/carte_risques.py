"""Mesures localisées avant tranchage, dans le repère affiché en millimètres."""

import numpy as np
from shapely import Point, Polygon, union_all
from trimesh.sample import sample_surface

from benchab.controles import surplombs_connexes
from benchab.controles.orientations_supports import faces_surplomb
from benchab.visualisation.anomalies_geometriques import diagnostiquer

MAX_FACES_RAYONS = 50_000
MAX_FACES_ASSISE = 100_000
NB_RAYONS = 512
TOLERANCE_PLAN_MM = 0.02


def _repere(contexte, categorie, indice, point, valeur, seuil, unite, methode, limite, surface=()):
    """Construit un repère et sa surface facultative en mm, dans le format de la visionneuse."""
    return {
        **contexte,
        "id": f"{categorie}:{contexte['piece']}:{indice}",
        "type": categorie,
        "point_modele": list(point),
        "valeur": valeur,
        "seuil": seuil,
        "unite_mesure": unite,
        "condition": methode,
        "precision": "Coordonnées calculées sur le modèle source.",
        "limite": limite,
        "surface_mm": list(surface),
    }


class AnalyseAnnulee(ValueError):
    """Une analyse annulée ne doit livrer aucun verdict."""


def verifier_annulation(annule):
    """Interrompt la mesure au prochain lot si l’appelant a demandé l’arrêt."""
    if annule is not None and annule():
        raise AnalyseAnnulee("Analyse annulée.")


def _epaisseur(mesh, contexte, seuil, annule=None):
    """Échantillonne des distances internes en mm ; signale explicitement les exclusions."""
    limite = "Échantillonnage directionnel ; un minimum peut échapper et la résistance reste non mesurée."
    if len(mesh.faces) > MAX_FACES_RAYONS or not mesh.is_volume:
        return {
            "statut": "NON VÉRIFIÉ",
            "detail": "Maillage ouvert, orientation incertaine ou budget de rayons dépassé.",
        }, []
    points, indices = sample_surface(mesh, NB_RAYONS, seed=0)
    normales = -mesh.face_normals[indices]
    # Un décalage interne court élimine l’intersection avec la face de départ.
    try:
        impacts, rayons = [], []
        for debut in range(0, NB_RAYONS, 32):
            verifier_annulation(annule)
            fin = debut + 32
            p, n = points[debut:fin], normales[debut:fin]
            positions, indices_rayons, _ = mesh.ray.intersects_location(p + n * 0.001, n, multiple_hits=False)
            impacts.append(positions.reshape(-1, 3))
            rayons.append(indices_rayons + debut)
        impacts, rayons = np.concatenate(impacts), np.concatenate(rayons)
    except ImportError:
        return {"statut": "NON VÉRIFIÉ", "detail": "Moteur de lancer de rayons indisponible."}, []
    distances = np.einsum("ij,ij->i", impacts - points[rayons], normales[rayons])
    valides = np.isfinite(distances) & (distances > 0.001)
    observations = sorted(zip(distances[valides], rayons[valides], strict=True))
    alertes = {}
    for distance, rayon in observations:
        indice = int(indices[rayon])
        if distance >= seuil or len(alertes) >= 24:
            break
        if indice not in alertes:
            alertes[indice] = _repere(
                contexte,
                "epaisseur_locale",
                indice,
                points[rayon].tolist(),
                float(distance),
                seuil,
                "mm",
                "Distance jusqu’à la première intersection dans la direction normale intérieure.",
                limite,
                mesh.triangles[indice].tolist(),
            )
    return {
        "statut": "WARN" if alertes else "PASS" if len(observations) == NB_RAYONS else "NON VÉRIFIÉ",
        "minimum_echantillonne_mm": float(observations[0][0]) if observations else None,
        "rayons_valides": len(observations),
        "rayons_lances": NB_RAYONS,
        "seuil_mm": seuil,
        "detail": f"{len(observations)}/{NB_RAYONS} rayons exploitables. {limite}",
    }, list(alertes.values())


def _assise(mesh, contexte, plan):
    """Mesure aire et marge au polygone d’appui en mm² et mm, avec centre homogène conditionnel."""
    if plan is None or len(mesh.faces) > MAX_FACES_ASSISE or mesh.bounds[0, 2] < plan - TOLERANCE_PLAN_MM:
        return {"statut": "NON VÉRIFIÉ", "detail": "Plan non déclaré, franchi ou budget d’assise dépassé."}, []
    triangles = mesh.triangles
    ids = np.flatnonzero(
        (mesh.face_normals[:, 2] < -0.99) & (np.abs(triangles[:, :, 2] - plan).max(axis=1) <= TOLERANCE_PLAN_MM)
    )
    appui = union_all([Polygon(triangles[i, :, :2]) for i in ids])
    aire = float(appui.area)
    marge = None
    if aire and mesh.is_volume:
        centre = Point(mesh.center_mass[:2])
        enveloppe = appui.convex_hull
        marge = (1 if enveloppe.covers(centre) else -1) * float(enveloppe.boundary.distance(centre))
    limite = "Centre de masse d’un solide homogène et enveloppe des contacts ; adhérence physique non mesurée."
    statut = (
        "WARN" if not aire or (marge is not None and marge <= 0) else "PASS" if marge is not None else "NON VÉRIFIÉ"
    )
    if aire:
        point = appui.representative_point()
        xyz = [float(point.x), float(point.y), plan]
    else:
        xyz = [*mesh.centroid[:2].tolist(), plan]
    repere = _repere(
        contexte,
        "assise_locale",
        0,
        xyz,
        aire,
        0.0,
        "mm²",
        f"Union des faces au plan déclaré, à ±{TOLERANCE_PLAN_MM} mm. "
        + (
            f"Marge signée du centre de masse projeté : {marge:.2f} mm."
            if marge is not None
            else "Marge du centre de masse non vérifiée."
        ),
        limite,
    )
    # La surbrillance borne ses triangles, le polygone d’assise est mesuré sur tous.
    repere["surfaces_mm"] = triangles[ids[:128]].tolist()
    return {"statut": statut, "aire_mm2": aire, "marge_centre_mm": marge, "detail": limite}, [repere]


def orientation_indicative(mesh):
    """Compare la pose source et une pose sur une face en mm², sans déplacer la pièce."""
    from benchab.controles.orientations_supports import mesure_rapide, pose

    if len(mesh.faces) > 20_000 or not mesh.is_volume:
        return None
    faces = np.argsort(-mesh.area_faces, kind="stable")
    face = next((i for i in faces if abs(mesh.face_normals[i, 2]) < 0.95), None)
    if face is None:
        return None
    transformation, tient = pose(mesh, mesh.face_normals[face])
    if not tient:
        return None
    alternative = mesh.copy().apply_transform(transformation)
    return {
        "triangle_source": int(face),
        "surface_surplomb_source_mm2": float(mesh.area_faces[faces_surplomb(mesh)].sum()),
        "surface_surplomb_alternative_mm2": float(alternative.area_faces[faces_surplomb(alternative)].sum()),
        "assise_alternative_mm2": mesure_rapide(alternative)["appui_mm2"],
        "transformation_mm": transformation.tolist(),
    }


def mesurer(mesh, piece, source, sha256, *, plan_mm=0.0, seuil_epaisseur_mm=0.84, annule=None):
    """Mesure surplomb, épaisseur et assise en mm ; renvoie calques PASS/WARN/NON VÉRIFIÉ et repères.

    Les calculs préservent la pose et les réglages. La soudure de sommets identiques
    sert seulement à tester la fermeture du solide, notamment en STL.
    """
    contexte = {"piece": piece, "source": source, "sha256": sha256, "repere": "repère source transformé en mm"}
    if not np.isfinite(mesh.vertices).all() or not np.isfinite(seuil_epaisseur_mm) or seuil_epaisseur_mm <= 0:
        raise ValueError("Coordonnées et seuil d’épaisseur finis requis.")
    topo = mesh.copy()
    topo.merge_vertices(digits_vertex=12)
    ferme = bool(topo.is_watertight)
    verifier_annulation(annule)
    if plan_mm is None:
        sur = {"faces_detectees": 0, "anomalies": []}
        mesure_sur = {"statut": "NON VÉRIFIÉ", "detail": "Plan de fabrication inconnu."}
    else:
        pose = mesh.copy()
        pose.apply_translation([0, 0, -plan_mm])
        sur = diagnostiquer(pose, piece, source, sha256)
        for a in sur["anomalies"]:
            indice = a["triangle_source"]
            a.update(
                contexte,
                point_modele=mesh.triangles_center[indice].tolist(),
                surface_mm=mesh.triangles[indice].tolist(),
                valeur=float(np.degrees(np.arccos(-np.clip(mesh.face_normals[indice, 2], -1, 1)))),
                seuil=50.0,
                unite_mesure="°",
                condition="Pente sur l’horizontale et centre > 0,4 mm du plan.",
            )
        groupes = None
        if len(mesh.faces) <= surplombs_connexes.MAX_FACES and np.all(mesh.area_faces > 0):
            groupes = len(
                surplombs_connexes.cartographier(pose, source=source, unite="mm", angle_max_deg=50)["groupes"]
            )
        orientation_etablie = ferme and topo.is_winding_consistent and topo.volume > 0
        # Des normales cohérentes sur une surface ouverte ne prouvent pas son extérieur.
        reserve = "" if orientation_etablie else "Maillage ouvert ou orientation incertaine. "
        mesure_sur = {
            "statut": (
                "WARN" if sur["faces_detectees"] else "PASS" if orientation_etablie else "NON VÉRIFIÉ"
            ),
            "surface_surplomb_mm2": float(pose.area_faces[faces_surplomb(pose)].sum()),
            "detail": f"{sur['faces_detectees']} faces au-delà du plan ; {len(sur['anomalies'])} repères affichés. "
            f"Groupes descendants (assise comprise) : {groupes if groupes is not None else 'non vérifiés'}. "
            f"{reserve}Portée de pont et trajectoires de support non vérifiées.",
        }
    verifier_annulation(annule)
    epaisseur, reperes = _epaisseur(topo, contexte, seuil_epaisseur_mm, annule)
    verifier_annulation(annule)
    assise, appuis = _assise(topo, contexte, plan_mm)
    return {
        **contexte,
        "faces_detectees": sur["faces_detectees"],
        "anomalies": sur["anomalies"] + reperes + appuis,
        "calques": {"surplomb": mesure_sur, "epaisseur": epaisseur, "assise": assise},
        "maillage_ferme": ferme,
    }
