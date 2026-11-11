"""Aperçu du G-code couche par couche (outil commun, Claude, 23/10/2026 ; proposition 9 retenue par
l'utilisateur).
usage : python -I apercu_gcode.py <tranche.3mf | fichier.gcode> <sortie.html> [--defauts
preuves/premiere-couche.json] [--titre "…"]
        [--zones preuves/controle-envoi.json] [--colonnes preuves/colonnes-isolees.json]
Vue 3D (25/10/2026, onglet « 3D ») : trajets de toutes les couches en lignes, rubans en volume sur les
dernières couches affichées,
couleur par type ou par vitesse, déplacements à vide (rouge : longs et peu relevés), alertes des contrôles
placées à leur hauteur.
Page autonome hors ligne (three.js intégré).
Page HTML locale autonome : vue de dessus de chaque couche, trajets colorés par type (comme l'aperçu du
slicer), curseur et flèches pour
changer de couche, couche précédente en grisé, types masquables, zoom à la molette, déplacement à la souris,
plaque au choix.
Un clic donne X, Y et Z (bouton « Copier ») pour signaler un endroit précis à l'assistant. Les défauts du
contrôle de première couche
(outils/controles/premiere_couche.py) sont marqués en rouge. Temps par couche : longueur / vitesse commandée,
sans accélérations
(estimation grossière, le temps total du slicer fait foi). Affichage du fichier tranché, pas une simulation de
l'impression."""

import base64
import html as html_lib
import json
import math
import re
import sys
import zipfile
from importlib.resources import files
from pathlib import Path

import numpy as np

from benchab.chemins import atelier_prive, est_temporaire, sans_lien
from benchab.qualite.ecritures import PublicationNeuve

TYPES = [
    "Outer wall",
    "Inner wall",
    "Overhang wall",
    "Sparse infill",
    "Internal solid infill",
    "Solid infill",
    "Top surface",
    "Bottom surface",
    "Bridge",
    "Internal Bridge",
    "Gap infill",
    "Support",
    "Support interface",
    "Support transition",
    "Brim",
    "Skirt",
    "Custom",
    "Prime tower",
    "Autre",
]
COULEURS = [
    "#ff8c00",
    "#ffd23f",
    "#3b6fe0",
    "#a83a3a",
    "#9b59d0",
    "#9b59d0",
    "#e8314a",
    "#7ac943",
    "#5fd3e8",
    "#2aa8b8",
    "#ffffff",
    "#33b864",
    "#1f8f4a",
    "#1f8f4a",
    "#66d9a0",
    "#66d9a0",
    "#7a7a7a",
    "#a0a0a0",
    "#b0b0b0",
]
Q = 20.0


def gcodes(f):
    f = Path(f)
    if f.suffix == ".gcode":
        return [(1, f.read_text(errors="replace"))]
    with zipfile.ZipFile(f) as z:
        return [
            (int(re.search("plate_(\\d+)", n).group(1)), z.read(n).decode(errors="replace"))
            for n in sorted(
                z.namelist(),
                key=lambda n: int(re.search("(\\d+)", n.split("/")[-1]).group(1)) if re.search("\\d", n) else 0,
            )
            if re.fullmatch("Metadata/plate_\\d+\\.gcode", n)
        ]


def couches(g):
    """[{z, h, t (s), d: Int16 [type, n, x0, y0, …], v: Uint8 vitesse (mm/s) de chaque polyligne, tr: Int16
    [x0, y0, x1, y1, relevage]}].
    Vitesse : celle commandée au début de la polyligne (une polyligne est coupée quand F change).
    Déplacements à vide de 1 mm et plus,
    relevage = Z du déplacement − Z de la couche (unités de 1/Q mm). Vue 3D ajoutée le 25/10/2026."""
    from benchab.controles.gcode_interpreter import Interpreter

    parser = Interpreter()
    out, cur, poly = ([], None, None)
    t, x, y, z, F, h = ("Autre", 0.0, 0.0, 0.0, 3000.0, 0.2)

    def fermer():
        nonlocal poly
        if poly and len(poly) > 5:
            cur["l"].extend([poly[0], (len(poly) - 2) // 2 - 1] + poly[2:])
            cur["v"].append(min(255, round(poly[1] / 60)))
        poly = None

    for ligne in g.splitlines():
        event = parser.feed_line(ligne)
        if ligne.startswith(";Z:"):
            fermer()
            cur = {"z": float(ligne[3:]), "h": h, "t": 0.0, "l": [], "v": [], "tr": []}
            out.append(cur)
        elif ligne.startswith(";HEIGHT:"):
            h = float(ligne[8:])
            cur and cur.update(h=h)
        elif ligne.startswith(";TYPE:"):
            fermer()
            t = ligne[6:].strip()
            t = t if t in TYPES else "Support" if t.startswith("Support") else "Autre"
        if not event:
            continue
        x, y, z = event["start"]
        nx, ny, nz = event["end"]
        F = event["feed"]
        d = math.hypot(nx - x, ny - y)
        if cur is not None and F > 0:
            cur["t"] += event["length"] / (F / 60)
        if event["trajectory"] == "arc_g17_xy":
            parser.error("aperçu géométrique des arcs non implémenté ; aucune corde tracée")
            fermer()
            continue
        if cur is not None and event["deposited"] > 0 and (event["deposited_length"] > 0) and (d > 0):
            sx, sy, _ = event["deposition_start"]
            start = [round(sx * Q), round(sy * Q)]
            if poly is None or poly[0] != TYPES.index(t) or poly[1] != F or (poly[-2:] != start):
                fermer()
                poly = [TYPES.index(t), F, *start]
            poly += [round(nx * Q), round(ny * Q)]
        elif d > 0:
            fermer()
            if cur is not None and d >= 1:
                cur["tr"] += [
                    round(x * Q),
                    round(y * Q),
                    round(nx * Q),
                    round(ny * Q),
                    max(0, min(32767, round((max(z, nz) - cur["z"]) * Q))),
                ]
    if parser.errors:
        raise ValueError("G-code non interprétable : " + "; ".join(parser.errors))
    fermer()

    def b64(a, ty):
        return base64.b64encode(np.array(a, ty).tobytes()).decode()

    return [
        {
            "z": c["z"],
            "h": c["h"],
            "t": round(c["t"], 1),
            "d": b64(c["l"], np.int16),
            "v": b64(c["v"], np.uint8),
            "tr": b64(c["tr"], np.int16),
        }
        for c in out
        if c["l"]
    ]


def alertes(zones=None, colonnes=None):
    """Alertes des contrôles par plaque : zones locales (contrôle avant envoi) et séries de colonnes
    isolées. Fichiers absents : ignorés."""
    a = {}
    if zones and Path(zones).exists():
        zl = json.loads(Path(zones).read_text()).get("zones_locales", {})
        for zo in zl.get("zones", []):
            if "bbox_xy_mm" in zo:
                a.setdefault(zo.get("plaque", 1), []).append(
                    {
                        "type": "zone " + zo["type"],
                        "z": [zo["z_min_mm"], zo["z_max_mm"]],
                        "bbox": zo["bbox_xy_mm"],
                        "texte": f"{zo['type']} z {zo['z_min_mm']} ({zo.get('segments', '?')} segments, "
                        f"{round(zo.get('vitesse_max_mm_s', 0))} mm/s)",
                    }
                )
    if colonnes and Path(colonnes).exists():
        for k, r in json.loads(Path(colonnes).read_text()).get("plaques", {}).items():
            for s in r.get("series", []):
                if s.get("alerte"):
                    a.setdefault(int(k.split()[-1]), []).append(
                        {
                            "type": "colonnes isolées",
                            "z": s["z_mm"],
                            "texte": f"colonnes isolées z {s['z_mm'][0]}–{s['z_mm'][1]} : trajets "
                            f"{s['trajet_max_mm']} mm relevés {s['relevage_mm']} mm",
                        }
                    )
    return a


def page(f, sortie, defauts=None, titre=None, zones=None, colonnes=None):
    """Construit une page locale autonome; G-code inchangé, sortie neuve privée/temporaire."""
    sortie = sans_lien(sortie)
    entrees = [f, *[p for p in (defauts, zones, colonnes) if p and Path(p).is_file()]]
    with PublicationNeuve(entrees, [sortie]) as pub:
        plaques = [{"n": k, "couches": couches(g)} for k, g in gcodes(f)]
        al = alertes(zones, colonnes)
        d = json.loads(Path(defauts).read_text()).get("plaques", {}) if defauts and Path(defauts).exists() else {}
        for p in plaques:
            p["defauts"] = d.get(f"plaque {p['n']}", {}).get("defauts", [])
            p["alertes"] = al.get(p["n"], [])
        donnees = {"titre": titre or Path(f).name, "q": Q, "types": TYPES, "couleurs": COULEURS, "plaques": plaques}
        payload = (
            json.dumps(donnees, separators=(",", ":"), allow_nan=False)
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("&", "\\u0026")
        )
        html = GABARIT.replace("__TITRE__", html_lib.escape(donnees["titre"])).replace("__DONNEES__", payload)
        from benchab.visualisation.hors_ligne import autonome

        html = autonome(html)
        pub.temporaire(sortie).write_text(html, encoding="utf-8")
        pub.publier()
        return {"fichier": str(sortie), "octets": len(html), "plaques": [(p["n"], len(p["couches"])) for p in plaques]}


# Les scripts restent inline dans le document final ; hors_ligne intègre l’import map et ses modules.
_RESSOURCES = files("benchab.visualisation").joinpath("ressources")
GABARIT = (
    Path(__file__).with_suffix(".html")
    .read_text(encoding="utf-8")
    .replace("__SCRIPT_2D__", _RESSOURCES.joinpath("apercu_gcode_2d.js").read_text(encoding="utf-8"))
    .replace("__SCRIPT_3D__", _RESSOURCES.joinpath("apercu_gcode_3d.js").read_text(encoding="utf-8"))
)


def main(argv=None, *, prive=None):
    """Produit une page locale privée; chemins sans unité, G-code et trajectoires inchangés."""
    import argparse

    parser = argparse.ArgumentParser(description="Aperçu du G-code et des risques, sans modification des trajectoires")
    parser.add_argument("source", type=Path)
    parser.add_argument("sortie", type=Path)
    parser.add_argument("--defauts", type=Path)
    parser.add_argument("--titre")
    parser.add_argument("--zones", type=Path)
    parser.add_argument("--colonnes", type=Path)
    args = parser.parse_args(argv)
    racine = atelier_prive(prive)
    sortie = sans_lien(args.sortie.absolute())
    if not sortie.is_relative_to(racine) and not est_temporaire(sortie):
        raise ValueError("Aperçu personnel : sortie privée ou temporaire exigée")
    print(
        json.dumps(page(args.source, sortie, args.defauts, args.titre, args.zones, args.colonnes), ensure_ascii=False)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
