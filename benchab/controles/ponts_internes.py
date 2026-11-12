"""Ponts internes longs juste sous le sommet de la pièce, dans le G-code tranché (outil commun, Claude, 27/10/2026).

usage : python -I ponts_internes.py <tranche.3mf | fichier.gcode> [--json sortie.json] [--portee 10] [--sous-sommet
2] [--seuil 5]
Constat d'origine (observation de l'utilisateur, 27/10/2026, horloge P11, Lune 3 arrière en Lightning 10 %) : dessus
ouvert près de
l'extrémité coupée, au sommet. À cet endroit, le slicer pose les premières couches pleines du dessus en « Internal
Bridge » de 10 à 16 mm
au-dessus d'un remplissage presque absent, à moins de 2 mm du sommet : les ponts se sont affaissés et le dessus
s'est ouvert.
Méthode, par plaque : longueur de chaque segment extrudé de type « Internal Bridge » ; on compte ceux de plus de
--portee mm situés à moins
de --sous-sommet mm de la couche la plus haute de la plaque. WARN (code 1, non bloquant) à partir de --seuil
segments sur une plaque.
Seuils THÉORIQUES, calés sur deux pièces seulement : Lune 1 avant réussie (0 segment), Lune 3 arrière trouée (13).
Le sommet est celui de
la plaque : sur une plaque à plusieurs objets de hauteurs différentes, les objets plus bas ne sont pas couverts. Ne
prédit ni la flèche
réelle d'un pont, ni l'effet du refroidissement : un PASS ne garantit pas un dessus fermé.
"""

import re
import sys
from pathlib import Path

from benchab.gcode.lecture import lire_texte_gcode_3mf, membres_gcode_3mf
from benchab.qualite.ecritures import destination_neuve, ecrire_json_neuf

NUM = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"


def mesurer(g, portee=10.0, sous_sommet=2.0):
    """Segments « Internal Bridge » (z, longueur) d'un G-code, puis bilan près du sommet."""
    z = zmax = 0.0
    t = ""
    x = y = None
    ponts = []
    for ligne in g.splitlines():
        if ligne.startswith(";Z:"):
            z = float(ligne[3:])
            zmax = max(zmax, z)
            continue
        if ligne.startswith(";TYPE:"):
            t = ligne[6:].strip()
            continue
        if not ligne.startswith(("G1 ", "G0 ")):
            continue
        c = ligne.split(";")[0]
        mx = re.search(r"X(" + NUM + ")", c)
        my = re.search(r"Y(" + NUM + ")", c)
        me = re.search(r"E(" + NUM + ")", c)
        nx = float(mx.group(1)) if mx else x
        ny = float(my.group(1)) if my else y
        if t == "Internal Bridge" and me and float(me.group(1)) > 0 and x is not None and nx is not None:
            ponts.append((z, ((nx - x) ** 2 + (ny - y) ** 2) ** 0.5, (nx + x) / 2, (ny + y) / 2))
        x, y = nx, ny
    pres = [p for p in ponts if p[0] > zmax - sous_sommet - 1e-6 and p[1] > portee]
    return {
        "z_sommet": zmax,
        "ponts_internes": len(ponts),
        "longs_sous_sommet": len(pres),
        "portee_max_sous_sommet_mm": round(max((p[1] for p in pres), default=0.0), 1),
        "positions": [
            [round(p[0], 2), round(p[2], 1), round(p[3], 1), round(p[1], 1)]
            for p in sorted(pres, key=lambda p: -p[1])[:20]
        ],
    }


def main(a):
    """Rapporte les ponts internes en mm et rend le code du contrôle."""
    if not a:
        print(__doc__)
        return 2

    def opt(k, d):
        return float(a[a.index(k) + 1]) if k in a else d

    portee, sous, seuil = opt("--portee", 10.0), opt("--sous-sommet", 2.0), int(opt("--seuil", 5))
    f = Path(a[0])
    try:
        sortie = destination_neuve(a[a.index("--json") + 1]) if "--json" in a else None
    except (OSError, ValueError, IndexError) as erreur:
        print("Sortie JSON refusée : " + str(erreur), file=sys.stderr)
        return 2
    if f.suffix == ".3mf":
        noms = sorted(membres_gcode_3mf(f), key=lambda name: int(re.findall(r"\d+", name)[0]))
        gcodes = {
            "plaque " + re.findall(r"\d+", name)[0]: lire_texte_gcode_3mf(f, name, erreurs="ignore") for name in noms
        }
    else:
        gcodes = {f.name: f.read_text(errors="ignore")}
    res = {"fichier": str(f), "seuils": {"portee_mm": portee, "sous_sommet_mm": sous, "segments": seuil}, "plaques": {}}
    for n, g in gcodes.items():
        m = mesurer(g, portee, sous)
        m["statut"] = "WARN" if m["longs_sous_sommet"] >= seuil else "PASS"
        res["plaques"][n] = m
        print(
            f"{m['statut']:5s} {n} : {m['longs_sous_sommet']} pont(s) interne(s) de plus de {portee:g} mm "
            f"à moins de {sous:g} mm du sommet (max {m['portee_max_sous_sommet_mm']} mm)"
        )
    res["statut"] = "WARN" if any(p["statut"] == "WARN" for p in res["plaques"].values()) else "PASS"
    if sortie is not None:
        try:
            ecrire_json_neuf(sortie, res, entrees=[f])
        except (OSError, ValueError) as erreur:
            print("Sortie JSON refusée : " + str(erreur), file=sys.stderr)
            return 2
    return 0 if res["statut"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
