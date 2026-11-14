"""Hypothèse de pont limitée aux plafonds à bords opposés observables."""

from collections import defaultdict

import numpy as np

_TOL_MM = 1e-6
_MAX_FACES = 4096
_MAX_COORDONNEE_MM = 1e6


def _inconnu(raison):
    """Retourne un diagnostic NON VÉRIFIÉ sans déduire une absence de pont."""
    return {"statut": "NON VÉRIFIÉ", "raison": raison}


def _couvre(intervalles, bas, haut):
    """Vérifie un côté complet, sans lacune ni chevauchement au-delà de 1 µm."""
    position = bas
    for debut, fin in sorted(intervalles):
        if abs(debut - position) > _TOL_MM or fin - debut <= _TOL_MM:
            return False
        position = fin
    return bool(intervalles) and abs(position - haut) <= _TOL_MM


def _aretes_groupe(triangles, faces):
    """Recense les arêtes exactes du groupe et les faces qui les touchent."""
    aretes = defaultdict(list)
    for face in faces:
        points = [tuple(float(v) for v in point) for point in triangles[face]]
        for a, b in zip(points, points[1:] + points[:1], strict=True):
            aretes[tuple(sorted((a, b)))].append(face)
    return aretes


def _cote(a, b, minima, maxima):
    """Associe une arête à un unique côté du rectangle XY et son intervalle en mm."""
    limites = (minima, maxima)
    resultat = []
    for axe in (0, 1):
        orthogonal = 1 - axe
        if abs(a[orthogonal] - b[orthogonal]) <= _TOL_MM:
            continue
        for sens in (0, 1):
            coord = limites[sens][axe]
            if max(abs(a[axe] - coord), abs(b[axe] - coord)) <= _TOL_MM:
                resultat.append(((axe, sens), tuple(sorted((a[orthogonal], b[orthogonal])))))
    return resultat[0] if len(resultat) == 1 else None


def _voisine_descend(triangles, voisins, groupe, face, arete, z):
    """Cherche une face adjacente non retenue dont le troisième sommet est sous Z."""
    a, b = arete
    for numero in voisins[face]:
        if numero in groupe:
            continue
        points = {tuple(float(v) for v in p) for p in triangles[numero]}
        if a in points and b in points and any(p[2] < z - _TOL_MM for p in points - {a, b}):
            return True
    return False


def evaluer(triangles, faces, voisins, topologie, portee_max_mm):
    """Repère un plafond horizontal avec deux côtés opposés complets en matière.

    Les triangles et la portée déclarée sont en mm ; faces et voisins
    contiennent des indices sans unité issus de l’adjacence exacte.
    HYPOTHÈSE indique seulement une portée géométrique sous le maximum
    déclaré ; sinon NON VÉRIFIÉ. Aucun réglage ou verdict physique.
    """
    if any(topologie.values()):
        return _inconnu("Topologie ouverte ou ambiguë ; les bords ne sont pas qualifiés.")
    if not faces or len(faces) > _MAX_FACES:
        return _inconnu("Le plafond est absent ou dépasse le budget de 4 096 faces.")
    sommets = np.asarray(triangles[faces]).reshape((-1, 3))
    minima, maxima = sommets.min(axis=0), sommets.max(axis=0)
    if (
        not np.isfinite(sommets).all()
        or np.abs(sommets).max() > _MAX_COORDONNEE_MM
        or maxima[2] - minima[2] > _TOL_MM
        or min(maxima[0] - minima[0], maxima[1] - minima[1]) <= _TOL_MM
    ):
        return _inconnu("Plafond horizontal dans le domaine géométrique non établi.")

    groupe = set(faces)
    bords, bords_descendants = defaultdict(list), defaultdict(list)
    for arete, voisines in _aretes_groupe(triangles, faces).items():
        if len(voisines) == 2:
            continue
        cote = _cote(*arete, minima, maxima)
        if len(voisines) != 1 or cote is None:
            return _inconnu("Contour rectangulaire aligné et complet non établi.")
        direction, intervalle = cote
        bords[direction].append(intervalle)
        if _voisine_descend(triangles, voisins, groupe, voisines[0], arete, minima[2]):
            bords_descendants[direction].append(intervalle)

    axes = []
    for axe in (0, 1):
        orthogonal = 1 - axe
        bornes = (minima[orthogonal], maxima[orthogonal])
        if not all(_couvre(bords[axe, cote], *bornes) for cote in (0, 1)):
            return _inconnu("Contour rectangulaire aligné et complet non établi.")
        if all(_couvre(bords_descendants[axe, cote], *bornes) for cote in (0, 1)):
            axes.append((float(maxima[axe] - minima[axe]), "XY"[axe]))
    if not axes:
        return _inconnu("Deux côtés opposés entiers ne sont pas établis sous le plafond.")
    portee, axe = min(axes)
    if portee > portee_max_mm:
        return {
            **_inconnu("La portée géométrique dépasse le maximum déclaré."),
            "axe": axe, "portee_mm": round(portee, 6),
        }
    return {
        "statut": "HYPOTHÈSE", "axe": axe, "portee_mm": round(portee, 6),
        "franchissabilite": "NON VÉRIFIÉE",
        "raison": "Deux côtés opposés entiers descendent sous le plafond.",
        "limite": "Ni trajet du slicer, ni adhésion, ni appui physique établis.",
    }
