"""Colonnes isolées et longs trajets au-dessus de supports hauts.

Usage : benchab controle colonnes-isolees <tranche.3mf|fichier.gcode> [options]

Méthode, par plaque et par couche (grille de 1 mm) :
- « colonnes » : la pièce n'a dans la couche que des îlots dont aucun ne dépasse --colonne-max mm (pas de gros
volume qui raidit l'ensemble) ;
- « supports hauts » : hauteur continue de support sous chaque case, depuis son apparition ;
- couche à risque : colonnes + au moins un déplacement de plus de --trajet-min mm relevé de moins de --relevage-min
mm, qui passe au-dessus
  d'un support haut (≥ --support-haut mm) ou d'une autre colonne de la pièce.
Les couches à risque consécutives forment une série. WARN (code 1, non bloquant) si une série dépasse --hauteur-min
mm et si sa hauteur
vaut au moins --elancement fois la plus petite largeur de ses îlots (colonne élancée) ; PASS sinon. Les seuils sont
théoriques (un seul
cas réel) : contrôle du fichier, pas une prédiction de collision. Ni courbure des bords, ni flexion, ni vibrations
ne sont calculées.

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
SUPPORT = ("Support", "Support interface", "Support transition")
NUM = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"


def gcodes(f):
    f = Path(f)
    if f.suffix == ".gcode":
        return [(1, f.read_text(errors="replace"))]
    return [
        (int(re.search(r"plate_(\d+)", member).group(1)), lire_texte_gcode_3mf(f, member))
        for member in membres_gcode_3mf(f)
    ]


def couches(g):
    """
    [{z, piece: [(x0,y0,x1,y1)], support: [...], trajets: [(x0,y0,x1,y1,relevage)]}] ; relevage = Z du trajet − Z de
    la couche.
    """
    out, cur, zl = [], None, 0.0
    it = Interpreter()
    for line in g.splitlines():
        move = it.feed_line(line)
        if move and move["trajectory"] == "arc_g17_xy":
            it.error("arc exact en longueur ; analyse de colonne linéaire non prise en charge")
            move = None
        if line.startswith(";LAYER_CHANGE"):
            cur = {"z": None, "piece": [], "support": [], "trajets": [], "erreurs": it.errors}
            out.append(cur)
        if line.startswith(";Z:") and cur is not None:
            zl = float(line[3:])
            cur["z"] = zl
        if not move or cur is None:
            continue
        x, y, z = move["start"]
        nx, ny, nz = move["end"]
        d = math.hypot(nx - x, ny - y)
        if d <= 1e-6:
            continue
        if move["deposited_length"] > 0:
            ax, ay, _ = move["deposition_start"]
            segment = (ax, ay, nx, ny)
            if move["type"] in PIECE:
                cur["piece"].append(segment)
            elif move["type"] in SUPPORT:
                cur["support"].append(segment)
        elif move["extrusion"] <= 0 and d >= 1:
            cur["trajets"].append((x, y, nx, ny, round(max(z, nz) - (cur["z"] if cur["z"] is not None else zl), 3)))
    return [c for c in out if c["z"] is not None]


def cases(segs, o, forme, pas, paquet=1_000_000):
    """
    Cases (forme) touchées par les segments (x0, y0, x1, y1) : même échantillonnage que l'ancienne boucle (n points
    par segment,
        n = max(2, longueur / (pas / 2) + 1), répartis comme np.linspace), en un passage NumPy par paquet d'au plus
        `paquet` points
        (mémoire bornée). Vectorisé sur des grilles
    identiques.
    """
    if not isinstance(pas, (int, float)) or isinstance(pas, bool) or not math.isfinite(pas) or pas <= 0:
        raise ValueError("pas fini positif requis")
    if type(paquet) is not int or paquet <= 0:
        raise ValueError("paquet entier positif requis")
    m = np.zeros(forme, bool)
    if not len(segs):
        return m
    s = np.asarray(segs, float)
    if s.ndim != 2 or s.shape[1] != 4 or not np.isfinite(s).all():
        raise ValueError("segments : lignes (x0, y0, x1, y1) finies attendues")
    n = np.maximum(2, (np.hypot(s[:, 2] - s[:, 0], s[:, 3] - s[:, 1]) / (pas / 2)).astype(int) + 1)
    fin = np.cumsum(n)
    debut = 0
    while debut < len(s):
        if n[debut] > paquet:
            # Un segment individuel peut dépasser le paquet : le découper sans
            # changer son échantillonnage (même pas que linspace historique).
            q = s[debut]
            total = int(n[debut])
            step = 1.0 / (total - 1)
            for start in range(0, total, paquet):
                stop_point = min(start + paquet, total)
                t = np.arange(start, stop_point) * step
                if stop_point == total:
                    t[-1] = 1.0
                i = np.clip(((q[0] + (q[2] - q[0]) * t - o[0]) / pas).astype(int), 0, forme[0] - 1)
                j = np.clip(((q[1] + (q[3] - q[1]) * t - o[1]) / pas).astype(int), 0, forme[1] - 1)
                m[i, j] = True
            debut += 1
            continue
        stop = max(int(np.searchsorted(fin, (fin[debut - 1] if debut else 0) + paquet, "right")), debut + 1)
        q, k = s[debut:stop], n[debut:stop]
        idx = np.repeat(np.arange(len(q)), k)
        rang = np.arange(k.sum()) - np.repeat(np.cumsum(k) - k, k)
        t = rang * np.repeat(1.0 / (k - 1), k)
        t[np.cumsum(k) - 1] = 1.0  # formule exacte de np.linspace(0, 1, n)
        i = np.clip(((q[idx, 0] + (q[idx, 2] - q[idx, 0]) * t - o[0]) / pas).astype(int), 0, forme[0] - 1)
        j = np.clip(((q[idx, 1] + (q[idx, 3] - q[idx, 1]) * t - o[1]) / pas).astype(int), 0, forme[1] - 1)
        m[i, j] = True
        debut = stop
    return m


def controler(
    g, colonne_max=30.0, trajet_min=40.0, relevage_min=1.0, support_haut=20.0, hauteur_min=5.0, elancement=2.0, pas=1.0
):
    C = couches(g)
    if C and C[0]["erreurs"]:
        return {"statut": "NON VÉRIFIÉ", "detail": "; ".join(C[0]["erreurs"])}
    pts = [p for c in C for s in c["piece"] + c["support"] for p in ((s[0], s[1]), (s[2], s[3]))]
    if not pts:
        return {"statut": "NON VÉRIFIÉ", "detail": "aucune extrusion analysable"}
    a = np.array(pts)
    o = a.min(0) - 3
    forme = tuple((np.ceil((a.max(0) - o + 3) / pas)).astype(int) + 1)
    debut = np.full(forme, np.nan)
    couche_risque = []
    for c in C:
        sup = ndimage.binary_dilation(cases(c["support"], o, forme, pas))
        # Une branche organique se décale d'une couche à l'autre : la hauteur continue suit le support à 3 mm près.
        suivi = ndimage.binary_dilation(sup, iterations=3)
        heritee = ndimage.grey_erosion(np.where(np.isnan(debut), np.inf, debut), size=(7, 7))
        debut = np.where(suivi, np.where(np.isfinite(heritee), heritee, c["z"]), np.nan)
        haut = np.where(sup, c["z"] - debut, 0.0)
        piece = ndimage.binary_dilation(cases(c["piece"], o, forme, pas))
        lab, n = ndimage.label(piece, structure=np.ones((3, 3)))
        if not n:
            continue
        boites = ndimage.find_objects(lab)
        dims = [((b[0].stop - b[0].start) * pas, (b[1].stop - b[1].start) * pas) for b in boites]
        if max(max(d) for d in dims) > colonne_max:
            continue
        risques = []
        for x0, y0, x1, y1, lev in c["trajets"]:
            L = math.hypot(x1 - x0, y1 - y0)
            if L < trajet_min or lev >= relevage_min:
                continue
            k = max(2, int(L / (pas / 2)))
            t = np.linspace(0, 1, k)
            i = np.clip(((x0 + (x1 - x0) * t - o[0]) / pas).astype(int), 0, forme[0] - 1)
            j = np.clip(((y0 + (y1 - y0) * t - o[1]) / pas).astype(int), 0, forme[1] - 1)
            hs = float(haut[i, j].max())
            dessus = set(lab[i, j][3:-3].tolist()) - {0}  # hors départ et arrivée
            if hs >= support_haut or dessus:
                risques.append(
                    {
                        "longueur_mm": round(L, 1),
                        "relevage_mm": lev,
                        "support_survole_mm": round(hs, 1),
                        "colonnes_survolees": len(dessus),
                    }
                )
        if risques:
            couche_risque.append(
                {
                    "z": c["z"],
                    "ilots": n,
                    "largeur_min_mm": min(min(d) for d in dims),
                    "dimension_max_mm": max(max(d) for d in dims),
                    "trajets": len(risques),
                    "trajet_max_mm": max(r["longueur_mm"] for r in risques),
                    "relevage_mm": sorted({r["relevage_mm"] for r in risques}),
                    "support_survole_max_mm": max(r["support_survole_mm"] for r in risques),
                }
            )
    series, zs = [], [c["z"] for c in C]
    for r in couche_risque:
        k = zs.index(r["z"])
        if series and series[-1]["_k"] == k - 1:
            s = series[-1]
            s["couches"].append(r)
            s["_k"] = k
        else:
            series.append({"_k": k, "couches": [r], "_z0": zs[k - 1] if k else 0.0})
    sortie = []
    for s in series:
        L = s["couches"]
        h = round(L[-1]["z"] - s["_z0"], 2)
        lmin = min(r["largeur_min_mm"] for r in L)
        sortie.append(
            {
                "z_mm": [L[0]["z"], L[-1]["z"]],
                "hauteur_mm": h,
                "couches": len(L),
                "ilots_max": max(r["ilots"] for r in L),
                "largeur_min_ilot_mm": lmin,
                "trajet_max_mm": max(r["trajet_max_mm"] for r in L),
                "relevage_mm": sorted({v for r in L for v in r["relevage_mm"]}),
                "support_survole_max_mm": max(r["support_survole_max_mm"] for r in L),
                "alerte": bool(h >= hauteur_min and h >= elancement * lmin),
            }
        )
    return {
        "statut": "WARN" if any(s["alerte"] for s in sortie) else "PASS",
        "couches": len(C),
        "series": sortie,
        "seuils": {
            "colonne_max_mm": colonne_max,
            "trajet_min_mm": trajet_min,
            "relevage_min_mm": relevage_min,
            "support_haut_mm": support_haut,
            "hauteur_min_mm": hauteur_min,
            "elancement": elancement,
        },
        "limite": (
            "Faits du G-code ; ni flexion, ni bords relevés, ni oscillation des supports calculés. "
            "Seuils issus d'un seul cas réel (arrosoir P09)."
        ),
    }


def main(argv=None):
    """Contrôle les colonnes en mm et retourne le code de contrôle, sans modifier le G-code."""
    a = list(sys.argv[1:] if argv is None else argv)
    if not a or a[0].startswith("--"):
        print(__doc__)
        return 2

    def opt(n, d):
        return float(a[a.index(n) + 1]) if n in a else d

    res = {
        f"plaque {k}": controler(
            g,
            opt("--colonne-max", 30.0),
            opt("--trajet-min", 40.0),
            opt("--relevage-min", 1.0),
            opt("--support-haut", 20.0),
            opt("--hauteur-min", 5.0),
            opt("--elancement", 2.0),
        )
        for k, g in gcodes(a[0])
    }
    for k, r in res.items():
        al = [s for s in r.get("series", []) if s["alerte"]]
        print(
            f"{r['statut']:5s} {k} : "
            + (r.get("detail") or f"{len(al)} série(s) de colonnes isolées survolées par de longs trajets peu relevés")
            + "".join(
                f"\n      - z {s['z_mm'][0]}–{s['z_mm'][1]} mm ({s['hauteur_mm']} mm, {s['couches']} couches) : "
                f"{s['ilots_max']} îlot(s), largeur min {s['largeur_min_ilot_mm']} mm, "
                f"trajets jusqu'à {s['trajet_max_mm']} mm relevés de {s['relevage_mm']} mm, "
                f"supports survolés jusqu'à {s['support_survole_max_mm']} mm"
                for s in al[:8]
            )
        )
    if "--json" in a:
        Path(a[a.index("--json") + 1]).write_text(
            json.dumps({"fichier": a[0], "plaques": res}, indent=1, ensure_ascii=False),
            encoding="utf-8",
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
