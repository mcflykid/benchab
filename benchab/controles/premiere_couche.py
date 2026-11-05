"""Contrôle numérique des deux premières couches.

Usage : benchab controle premiere-couche <tranche.3mf|fichier.gcode> [options]

Méthode : les trajets extrudés des deux premières couches (types de la pièce seulement : parois, surfaces,
remplissages ; ni purge, ni
jupe, ni supports) sont tracés sur une grille (pas 0,1 mm, largeur de ligne lue dans le G-code, 0,42 mm par défaut).
Signale par plaque :
- îlot minuscule : zone extrudée isolée de moins de --ilot-max mm² en 1re couche (accroche faible, risque
d'arrachement) ;
- creux d'une couche : vide fermé de la 1re couche (moins de --creux-max mm²) recouvert à plus de 50 % dès la 2e
couche
  (gravure ou creux trop peu profond : inutile sous la pièce, et souvent des îlots à l'intérieur) ;
Statut WARN (code 1, non bloquant) si un défaut est trouvé, PASS sinon. Contrôle du fichier, pas de l'impression
réelle.

"""

import json
import math
import re
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage

from benchab.controles.gcode_interpreter import Interpreter
from benchab.gcode.lecture import lire_texte_gcode_3mf, membres_gcode_3mf

# Budget conservateur des tableaux NumPy/SciPy : 64 octets/cellule, 256 Mio.
MAX_CELLS = 4_000_000
MAX_SAMPLES = 2_000_000
MAX_SEGMENTS = 250_000
MAX_KERNEL = 4096

PIECE = (
    "Outer wall",
    "Inner wall",
    "Overhang wall",
    "Bottom surface",
    "Top surface",
    "Internal solid infill",
    "Sparse infill",
    "Gap infill",
    "Bridge",
    "Internal Bridge",
    "Solid infill",
)


def gcodes(f):
    f = Path(f)
    if f.suffix == ".gcode":
        return [(1, f.read_text(errors="replace"))]
    return [
        (int(re.search(r"plate_(\d+)", member).group(1)), lire_texte_gcode_3mf(f, member))
        for member in membres_gcode_3mf(f)
    ]


def deux_couches(g):
    """
    Segments extrudés [(x0, y0, x1, y1, largeur)] des deux premières couches de la pièce.
    """
    couches, vus, w = [[], []], [], 0.42
    it = Interpreter()
    for line in g.splitlines():
        if line.startswith(";Z:"):
            z = float(line[3:])
            if not math.isfinite(z) or abs(z) > 1e6:
                raise ValueError("coordonnée Z invalide")
            z = round(z, 3)
            if z not in vus:
                vus.append(z)
            if len(vus) > 2:
                break
        elif line.startswith(";WIDTH:"):
            w = float(line[7:])
            if not math.isfinite(w) or not 0 < w <= 10:
                raise ValueError("largeur de ligne invalide (0 < largeur ≤ 10 mm)")
        move = it.feed_line(line)
        if move and vus and move["type"] in PIECE and move["deposited_length"] > 0:
            a, b = move["deposition_start"], move["end"]
            if sum(map(len, couches)) >= MAX_SEGMENTS:
                raise ValueError("budget de segments dépassé")
            couches[len(vus) - 1].append((a[0], a[1], b[0], b[1], w))
    if it.errors:
        raise ValueError("; ".join(it.errors))
    return couches, vus[:2]


def tracer(segs, origine, forme, pas):
    if not math.isfinite(pas) or pas <= 0:
        raise ValueError("pas fini strictement positif requis")
    if len(forme) != 2 or any(not isinstance(n, int) or n <= 0 for n in forme) or math.prod(forme) > MAX_CELLS:
        raise ValueError("budget de grille dépassé (4 millions de cellules)")
    if segs:
        data = np.asarray(segs, dtype=float)
        if (
            data.ndim != 2
            or data.shape[1] != 5
            or not np.isfinite(data).all()
            or np.any(np.abs(data[:, :4]) > 1e6)
            or np.any(data[:, 4] <= 0)
            or np.any(data[:, 4] > 10)
        ):
            raise ValueError("segments ou largeurs invalides")
        samples = np.maximum(2, np.ceil(np.hypot(data[:, 2] - data[:, 0], data[:, 3] - data[:, 1]) / (pas / 2)))
        if not np.isfinite(samples).all() or samples.sum() > MAX_SAMPLES:
            raise ValueError("budget de points de tracé dépassé")
        radius = max(1, int(round(float(data[:, 4].max()) / pas / 2)))
        if (2 * radius + 1) ** 2 > MAX_KERNEL:
            raise ValueError("budget du noyau de largeur dépassé")
    m = np.zeros(forme, bool)
    if not segs:
        return m
    s = np.array(segs)
    L = np.hypot(s[:, 2] - s[:, 0], s[:, 3] - s[:, 1])
    n = np.maximum(2, np.ceil(L / (pas / 2)).astype(int))
    for k in np.unique(np.round(s[:, 4] / pas).astype(int)):  # une dilatation par largeur de ligne
        sel = np.round(s[:, 4] / pas).astype(int) == k
        c = np.zeros(forme, bool)
        for (x0, y0, x1, y1, _), nn in zip(s[sel], n[sel]):
            t = np.linspace(0, 1, nn)
            i = ((x0 + (x1 - x0) * t - origine[0]) / pas).astype(int)
            j = ((y0 + (y1 - y0) * t - origine[1]) / pas).astype(int)
            c[np.clip(i, 0, forme[0] - 1), np.clip(j, 0, forme[1] - 1)] = True
        r = max(1, int(round(k / 2)))
        yy, xx = np.ogrid[-r : r + 1, -r : r + 1]
        m |= ndimage.binary_dilation(c, structure=xx**2 + yy**2 <= r * r)
    return m


def controler(g, ilot_max=3.0, creux_max=150.0, pas=0.1):
    try:
        for value in (ilot_max, creux_max, pas):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("seuils et pas finis strictement positifs requis")
        return _controler(g, ilot_max, creux_max, pas)
    except (ValueError, OverflowError, MemoryError, IndexError) as exc:
        return {"statut": "NON VÉRIFIÉ", "detail": str(exc) or "budget mémoire dépassé"}


def _controler(g, ilot_max, creux_max, pas):
    try:
        (c1, c2), zs = deux_couches(g)
    except ValueError as ex:
        return {"statut": "NON VÉRIFIÉ", "detail": str(ex)}
    if not c1:
        return {"statut": "NON VÉRIFIÉ", "detail": "aucune extrusion de pièce en 1re couche"}
    a = np.array([s[:4] for s in c1 + c2])
    if not np.isfinite(a).all() or np.any(np.abs(a) > 1e6):
        raise ValueError("coordonnées non finies ou extrêmes")
    o = (a[:, [0, 2]].min() - 2, a[:, [1, 3]].min() - 2)
    forme = (int((a[:, [0, 2]].max() - o[0] + 2) / pas) + 1, int((a[:, [1, 3]].max() - o[1] + 2) / pas) + 1)
    if math.prod(forme) > MAX_CELLS:
        raise ValueError("budget de grille dépassé (4 millions de cellules)")
    m1, m2 = tracer(c1, o, forme, pas), tracer(c2, o, forme, pas)
    px = pas * pas
    defauts = []
    lab, n = ndimage.label(
        ndimage.binary_dilation(m1, iterations=2)
    )  # traces à moins de 0,4 mm l'une de l'autre = même îlot (interstices)
    lab = np.where(m1, lab, 0)
    if n:
        aires = ndimage.sum(m1, lab, range(1, n + 1)) * px
        ctr = ndimage.center_of_mass(m1, lab, range(1, n + 1))
        for ar, (i, j) in zip(aires, ctr):
            if 0 < ar < ilot_max:
                defauts.append(
                    {
                        "type": "îlot minuscule",
                        "aire_mm2": round(float(ar), 2),
                        "xy": [round(o[0] + i * pas, 1), round(o[1] + j * pas, 1)],
                    }
                )
    trous = ndimage.binary_fill_holes(m1) & ~m1
    lab, n = ndimage.label(trous)
    for k in range(1, n + 1):
        z = lab == k
        ar = z.sum() * px
        if ar < 0.3 or ar > creux_max:
            continue
        cov = float((z & m2).sum() / z.sum())
        if cov > 0.5:
            i, j = np.argwhere(z).mean(0)
            defauts.append(
                {
                    "type": "creux d'une seule couche (recouvert dès la 2e)",
                    "aire_mm2": round(float(ar), 1),
                    "recouvert_pct": round(100 * cov),
                    "xy": [round(o[0] + i * pas, 1), round(o[1] + j * pas, 1)],
                }
            )
    return {
        "statut": "WARN" if defauts else "PASS",
        "z_couches": zs,
        "segments": [len(c1), len(c2)],
        "aire_1re_couche_mm2": round(float(m1.sum() * px)),
        "defauts": defauts,
    }


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if not a or a[0].startswith("--"):
        print(__doc__)
        return 2

    def opt(n, d):
        return float(a[a.index(n) + 1]) if n in a else d

    res = {
        f"plaque {k}": controler(g, opt("--ilot-max", 3.0), opt("--creux-max", 150.0), opt("--pas", 0.1))
        for k, g in gcodes(a[0])
    }
    for k, r in res.items():
        print(
            f"{r['statut']:5s} {k} : {len(r.get('defauts', []))} défaut(s) de 1re couche"
            + "".join(
                f"\n      - {d['type']} : {d['aire_mm2']} mm² vers X {d['xy'][0]} Y {d['xy'][1]}"
                for d in r.get("defauts", [])[:12]
            )
        )
    if "--json" in a:
        Path(a[a.index("--json") + 1]).write_text(
            json.dumps({"fichier": a[0], "plaques": res}, indent=1, ensure_ascii=False)
        )
    return (
        2
        if not res or any(r["statut"] == "NON VÉRIFIÉ" for r in res.values())
        else 1
        if any(r["statut"] == "WARN" for r in res.values())
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
