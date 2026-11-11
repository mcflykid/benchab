"""

Projection XY estimée des trajets extrudés, jamais contact physique.

Cellules dont le centre traverse le ruban nominal de largeur WIDTH ; les bouts
sont arrondis. Z du trajet est le dessus, Z-HEIGHT le dessous. On cherche le
premier dessous de pièce situé entre 0 et max_ecart au-dessus d'un support.
Ni déformation, écrasement, affaissement ni retrait des supports n'est prédit.


"""

import math

PIECE = {
    "outer wall",
    "inner wall",
    "overhang wall",
    "gap infill",
    "sparse infill",
    "internal solid infill",
    "bridge",
    "internal bridge",
    "top surface",
    "bottom surface",
}


class ContactSupport:
    """Accumule une projection nominale des interfaces et des écarts Z en mm, sans mesure physique."""

    def __init__(self, cellule=0.5, max_ecart=2.0, max_cellules=100000, max_echantillons=2000000, max_niveaux=200000):
        """Initialise la grille et l’écart maximal en mm, avec des budgets de comptage sans unité."""
        if (
            not all(math.isfinite(x) and x > 0 for x in (cellule, max_ecart))
            or min(max_cellules, max_echantillons, max_niveaux) < 1
        ):
            raise ValueError("Budgets positifs requis")
        self.pas = cellule
        self.max_ecart = max_ecart
        self.max_cellules = max_cellules
        self.max_echantillons = max_echantillons
        self.max_niveaux = max_niveaux
        self.cols = {}
        self.niveaux = 0
        self.echantillons = 0
        self.segments = 0
        self.mesures = 0
        self.absents = 0
        self.arret = None

    def ajouter(self, move, metadata):
        """Ajoute un dépôt horizontal aux cellules XY ; dimensions WIDTH et HEIGHT en mm."""
        if not move or move.get("deposited", 0) <= 0:
            return
        self.segments += 1
        if self.arret:
            return
        try:
            w = float(metadata["WIDTH"])
            h = float(metadata["HEIGHT"])
            a, b = move["deposition_start"], move["end"]
            if (
                not all(math.isfinite(float(x)) for x in (*a, *b, w, h))
                or max(abs(float(x)) for x in (*a, *b)) > 1e7
                or not 0 < w <= 1000
                or not 0 < h <= 100
                or abs(a[2] - b[2]) > 1e-6
            ):
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            self.absents += 1
            return
        support = (move.get("type") or "").lower().startswith("support")
        if not support and (move.get("type") or "").lower() not in PIECE:
            self.absents += 1
            return
        x0, y0 = a[:2]
        dx = b[0] - x0
        dy = b[1] - y0
        l2 = dx * dx + dy * dy
        if l2 <= 0:
            return
        # Visite bornée le long du trait, puis centres voisins testés exactement.
        steps = max(1, math.ceil(math.sqrt(l2) / (self.pas / 2)))
        radius = math.ceil(w / (2 * self.pas)) + 1
        cout = (steps + 1) * (2 * radius + 1) ** 2
        if cout + self.echantillons > self.max_echantillons:
            self.arret = "budget_echantillons"
            return
        self.echantillons += cout
        cells = set()
        for n in range(steps + 1):
            cx = math.floor((x0 + dx * n / steps) / self.pas)
            cy = math.floor((y0 + dy * n / steps) / self.pas)
            for i in range(cx - radius, cx + radius + 1):
                for j in range(cy - radius, cy + radius + 1):
                    x = (i + 0.5) * self.pas
                    y = (j + 0.5) * self.pas
                    t = max(0, min(1, ((x - x0) * dx + (y - y0) * dy) / l2))
                    if (x - x0 - t * dx) ** 2 + (y - y0 - t * dy) ** 2 <= (w / 2) ** 2:
                        cells.add((i, j))
        for cell in cells:
            if cell not in self.cols:
                if len(self.cols) >= self.max_cellules:
                    self.arret = "budget_cellules"
                    return
                self.cols[cell] = [set(), set()]
            levels = self.cols[cell][0 if support else 1]
            z = round(b[2] if support else b[2] - h, 6)
            if z not in levels:
                if self.niveaux >= self.max_niveaux:
                    self.arret = "budget_niveaux"
                    return
                levels.add(z)
                self.niveaux += 1
        self.mesures += 1

    def resultat(self, erreurs=()):
        """Rend ESTIMATION, PARTIEL ou NON_MESURABLE, avec projection en mm² et écarts en mm."""
        gaps = []
        support_cells = sum(bool(s) for s, p in self.cols.values())
        if not self.arret and not erreurs:
            import bisect

            for supports, pieces in self.cols.values():
                if not supports or not pieces:
                    continue
                zs = sorted(pieces)
                local = []
                for top in supports:
                    i = bisect.bisect_left(zs, top - 1e-6)
                    if i < len(zs) and zs[i] - top <= self.max_ecart:
                        local.append(max(0, zs[i] - top))
                if local:
                    gaps.append(min(local))
        gaps.sort()

        def q(f):
            return round(gaps[round((len(gaps) - 1) * f)], 6) if gaps else None

        return {
            "statut": "PARTIEL" if self.arret or erreurs or self.absents else "ESTIMATION" if gaps else "NON_MESURABLE",
            "surface_contact_physique_mm2": None,
            "projection_interface_estimee_mm2": round(len(gaps) * self.pas**2, 6) if gaps else None,
            "ecart_z_local_mm": {"minimum": q(0), "q25": q(0.25), "mediane": q(0.5), "q75": q(0.75), "maximum": q(1)},
            "couverture": {
                "segments_extrudes": self.segments,
                "segments_mesures": self.mesures,
                "segments_sans_metadonnees_exploitables": self.absents,
                "cellules_support": support_cells,
                "cellules_interface": len(gaps),
                "fraction_cellules_support": len(gaps) / support_cells if support_cells and gaps else None,
            },
            "budgets": {
                "cellule_mm": self.pas,
                "recherche_ecart_max_mm": self.max_ecart,
                "cellules_max": self.max_cellules,
                "echantillons_max": self.max_echantillons,
                "niveaux_max": self.max_niveaux,
                "arret": self.arret,
            },
            "methode": (
                "Centres de cellules XY dans les rubans WIDTH ; dessus Z, dessous Z-HEIGHT ; "
                "minimum local par cellule. "
                "Quantiles spatiaux non pondérés."
            ),
            "limite": (
                "Projection nominale discrétisée, pas aire physique ni preuve de retrait. "
                "Les courbes et pentes peuvent être sous-échantillonnées ; HEIGHT/WIDTH absents exclus. "
                "Erreur du parseur ou budget atteint : mesures "
                "globales inconnues."
            ),
        }
