"""Chanfrein de base et congés intérieurs sur des solides CAO.

Le chanfrein concerne les arêtes au Z minimal. Éviter une face d'appui fonctionnelle.
Les congés ciblent les angles rentrants ; axe='Z' limite aux arêtes verticales.
En cas d'échec du noyau, le solide original est rendu avec WARN, sans résultat partiel.
La compensation du slicer et la résistance physique restent des vérifications distinctes.
"""


def chanfrein_base(solide, c=0.5):
    """Applique un chanfrein de largeur c en mm aux arêtes au Z minimal.

    Entrée : solide CAO en mm. Sortie : solide et rapport, PASS si le noyau
    réussit, NON APPLICABLE sans arête admissible, WARN en cas d'échec.
    Les deux derniers cas rendent le solide initial, sans résultat partiel.
    """
    from build123d import GeomType, chamfer

    z0 = solide.bounding_box().min.Z
    ar = [
        e
        for e in solide.edges()
        if e.geom_type in (GeomType.LINE, GeomType.CIRCLE, GeomType.BSPLINE, GeomType.ELLIPSE)
        and abs(e.bounding_box().min.Z - z0) < 1e-4
        and abs(e.bounding_box().max.Z - z0) < 1e-4
    ]
    if not ar:
        return solide, {"statut": "NON APPLICABLE", "detail": "aucune arête au plateau"}
    try:
        return chamfer(ar, c), {"statut": "PASS", "aretes": len(ar), "chanfrein_mm": c}
    except Exception as e:
        return solide, {"statut": "WARN", "detail": f"chanfrein impossible ({type(e).__name__}) : pièce inchangée"}


def aretes_rentrantes(solide, axe=None):
    """Liste les arêtes rentrantes entre deux faces d'un solide en mm.

    L'axe Z ne retient que les arêtes quasi verticales. Le classement se fait
    sur les normales des faces ; il ne modifie ni le solide ni ses réglages.
    """
    out = []
    for e in solide.edges():
        fs = [f for f in solide.faces() if any(e.is_same(x) for x in f.edges())]
        if len(fs) != 2:
            continue
        m = e.center()
        if axe == "Z":
            d = e.end_point() - e.start_point()
            if abs(d.Z) < 0.99 * d.length:
                continue
        t = (e.end_point() - e.start_point()).normalized()
        n1, n2 = fs[0].normal_at(m), fs[1].normal_at(m)
        u = n2.cross(t).normalized()  # direction dans la face 2, perpendiculaire à l'arête
        if fs[1].distance_to(m + u * 0.05) > 1e-4:
            u = -u  # du côté où la face 2 existe (faces non convexes comprises)
        if n1.dot(u) > 1e-3:
            out.append(e)  # la face 2 part « devant » la face 1 : angle rentrant
    return out


def conges_interieurs(solide, r=1.0, axe=None):
    """Arrondit les arêtes rentrantes avec un rayon r en mm.

    Entrées : solide CAO en mm et axe éventuel, Z pour les seules arêtes
    quasi verticales. Sortie : solide et rapport PASS si le noyau réussit,
    NON APPLICABLE sans arête admissible, WARN avec le solide initial en cas d'échec.
    """
    from build123d import fillet

    ar = aretes_rentrantes(solide, axe)
    if not ar:
        return solide, {"statut": "NON APPLICABLE", "detail": "aucune arête rentrante"}
    try:
        return fillet(ar, r), {"statut": "PASS", "aretes": len(ar), "rayon_mm": r}
    except Exception as e:
        return solide, {"statut": "WARN", "detail": f"congé impossible ({type(e).__name__}) : pièce inchangée"}
