"""

Revue locale anti-boudinage par critères déclarés (chantier K, lot 2 ; Claude, 27/10/2026).

Industrialise la revue faite à la main pour l'horloge P07 (26/10/2026, 4 421 zones). Pour chaque zone de
`zones_locales` (sortie --json de controle_envoi.py), mesure dans le G-code :
- le temps de couche commandé le plus court sur les couches de la zone (distance / F, accélérations ignorées,
  donc temps réel un peu plus long) ;
- la portée (diagonale de la boîte XY) ;
- la présence d'extrusion de support sous la zone, à la couche inférieure (boîte élargie de 1 mm).
Une zone est JUSTIFIÉE seulement si elle remplit un critère de CRITERES ; sinon elle reste à revoir (IA ou
utilisateur). Zones géométriques (controles_piece.py) : justifiées seulement si toutes les plaques qui portent
l'objet ont leur temps de couche minimal au-dessus du seuil et, pour les surplombs, des supports.
Les décisions déjà présentes dans REVUE-ZONES-LOCALES.json et encore liées aux mêmes empreintes sont conservées
telles quelles (une décision humaine n'est jamais écrasée). Sans --appliquer : simulation, rien n'est écrit.
Ce contrôle réduit le risque ; il ne garantit pas l'absence de défaut physique.

Usage : revue_locale.py candidats/tranche.3mf --envoi preuves/controle-envoi.json
        [--revue REVUE-ZONES-LOCALES.json] [--faits preuves/revue-locale-faits.json] [--appliquer]
Puis relancer controle_envoi.py (make revue le fait).

"""

import collections
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from benchab.archives_3mf import Archive3MF
from benchab.controles.gcode_interpreter import Interpreter
from benchab.gcode.lecture import lire_texte_gcode_3mf

# Critères de la revue P07 (26/10/2026), seuils théoriques jusqu'aux retours d'impression ; les réviser avec date.
CRITERES = {
    "date": "26/10/2026",
    "temps_couche_min_s": 8.0,  # sous ce temps : boudinage constaté (tirelire, JBL) ; jamais justifié ici
    "portee_courte_mm": 10.0,  # zone courte
    "pont_bas": {"portee_max_mm": 25.0, "temps_couche_min_s": 20.0, "z_max_mm": 10.0},
    "marge_support_mm": 1.0,
}
NUM = re.compile(r"-?[\d.]+$")


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def mesurer_plaque(g):
    """
    Temps de couche par Z, extrusions de support par Z, première couche.
    """
    tc = collections.Counter()
    sup = collections.defaultdict(list)
    z = 0.0
    it = Interpreter()
    for line in g.splitlines():
        if line.startswith(";Z:"):
            z = round(float(line[3:]), 3)
        move = it.feed_line(line)
        if not move:
            continue
        if move["trajectory"] == "arc_g17_xy":
            it.error("arc exact en longueur ; trajectoire de support non représentée par une corde")
            continue
        if move["feed"] > 0:
            tc[z] += move["length"] / (move["feed"] / 60)
        if (move["type"] or "").startswith("Support") and move["deposited_length"] > 0:
            a, b = move["deposition_start"], move["end"]
            sup[z].append((a[0], a[1], b[0], b[1], move["type"]))
    zs = sorted(k for k in tc if k > 0)
    premiere = zs[0] if zs else 0
    hors_premiere = [tc[k] for k in zs if k > premiere]
    return {
        "tc": tc,
        "zs": zs,
        "sup": sup,
        "erreurs": it.errors,
        "min_s": round(min(hors_premiere), 2) if hors_premiere else None,
        "derniere_z": zs[-1] if zs else None,
        "derniere_s": round(tc[zs[-1]], 2) if zs else None,
        "support": any(sup.values()),
    }


def faits_zone(zo, m):
    x0, y0, x1, y1 = zo["bbox_xy_mm"]
    marge = CRITERES["marge_support_mm"]
    couches = [k for k in m["zs"] if zo["z_min_mm"] - 1e-6 <= k <= zo["z_max_mm"] + 1e-6] or [zo["z_min_mm"]]
    dessous = max([k for k in m["zs"] if k < zo["z_min_mm"] - 1e-6], default=None)

    def croise(segment):
        # Segment/rectangle (Liang-Barsky) : un support traversant la zone
        # est pertinent même lorsque ses deux extrémités sont dehors.
        ax, ay, bx, by = segment[:4]
        lo, hi = 0.0, 1.0
        for origin, delta, lower, upper in (
            (ax, bx - ax, x0 - marge, x1 + marge),
            (ay, by - ay, y0 - marge, y1 + marge),
        ):
            if abs(delta) < 1e-12:
                if not lower <= origin <= upper:
                    return False
            else:
                u, v = sorted(((lower - origin) / delta, (upper - origin) / delta))
                lo = max(lo, u)
                hi = min(hi, v)
                if lo > hi:
                    return False
        return True

    # La couche immédiatement inférieure doit être voisine, pas un support
    # arbitrairement bas. Sans séparation déclarée : pas d'acquittement support.
    gap = zo.get("separation_support_max_mm")
    soutenu = (
        not m.get("erreurs")
        and dessous is not None
        and isinstance(gap, (int, float))
        and 0 < zo["z_min_mm"] - dessous <= gap + 1e-6
        and any(croise(s) for s in m["sup"].get(dessous, []))
    )
    return {
        "plaque": zo["plaque"],
        "type": zo["type"],
        "z": [zo["z_min_mm"], zo["z_max_mm"]],
        "portee_mm": round(((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5, 1),
        "temps_couche_min_s": round(min(m["tc"].get(k, 0) for k in couches), 1),
        "support_dessous": soutenu,
        "vitesse_max": zo.get("vitesse_max_mm_s"),
    }


def juger(v):
    """
    Justification si un critère est rempli, sinon None.
    """
    c = CRITERES
    t = v["temps_couche_min_s"]
    if t < c["temps_couche_min_s"]:
        return None
    if v["support_dessous"]:
        return (
            f"{v['type']} posé sur support/interface à la couche inférieure ; "
            f"couche {t} s ≥ {c['temps_couche_min_s']:g} s"
        )
    if v["portee_mm"] <= c["portee_courte_mm"]:
        return f"{v['type']} court ({v['portee_mm']} mm) ; couche {t} s ≥ {c['temps_couche_min_s']:g} s"
    b = c["pont_bas"]
    if v["portee_mm"] <= b["portee_max_mm"] and t >= b["temps_couche_min_s"] and v["z"][1] <= b["z_max_mm"]:
        return (
            f"{v['type']} de {v['portee_mm']} mm près du plateau (Z {v['z'][1]}), grande couche {t} s, "
            f"vitesse ≤ {v['vitesse_max']} mm/s : pont court courant en PLA"
        )
    return None


def juger_geometrie(zo, plaques, gcodes):
    stem = Path(zo.get("fichier", "")).stem
    porteuses = [
        pl
        for pl, g in gcodes.items()
        if stem
        and re.search(r"^EXCLUDE_OBJECT_START\s+NAME=" + re.escape(stem) + r"(?:_id_\d+_copy_\d+)?(?:\s|$)", g, re.M)
    ]
    if not porteuses:
        return None
    mins = {pl: plaques[pl]["min_s"] for pl in porteuses}
    if any(plaques[pl].get("erreurs") for pl in porteuses):
        return None
    if any(v is None or v < CRITERES["temps_couche_min_s"] for v in mins.values()):
        return None
    if zo["type"] == "surplombs":
        # Une alerte géométrique sans bbox/repère plateau ne permet aucune
        # correspondance locale. La présence globale de supports est insuffisante.
        if not all(k in zo for k in ("bbox_xy_mm", "z_min_mm", "z_max_mm", "separation_support_max_mm")):
            return None
        if not all(faits_zone(dict(zo, plaque=pl), plaques[pl])["support_dessous"] for pl in porteuses):
            return None
        return (
            f"Surplombs de {zo['fichier']} : supports localisés sous la zone des plaques {porteuses} ; "
            f"temps minimal {mins}"
        )
    return (
        f"{zo['type']} de {zo['fichier']} : aucune couche des plaques {porteuses} "
        f"sous {CRITERES['temps_couche_min_s']:g} s (minimum {mins})"
    )


def revoir(tranche, envoi, revue_p, faits_p):
    env = json.loads(Path(envoi).read_text())["zones_locales"]
    with Archive3MF(tranche) as zf:
        noms = {int(m.group(1)): n for n in zf.noms() if (m := re.match(r"Metadata/plate_(\d+)\.gcode$", n))}
    gcodes = {pl: lire_texte_gcode_3mf(tranche, n, erreurs="ignore") for pl, n in noms.items()}
    plaques = {pl: mesurer_plaque(g) for pl, g in gcodes.items()}
    if env.get("tranche_sha256") != sha(tranche):
        print(
            "ARRÊT : controle-envoi.json ne correspond pas à ce tranchage ; relancer controle_envoi.py", file=sys.stderr
        )
        sys.exit(2)
    ancien = {}
    if Path(revue_p).is_file():
        r = json.loads(Path(revue_p).read_text())
        if r.get("tranche_sha256") == env["tranche_sha256"] and r.get("geometrie_sha256") == env.get(
            "geometrie_sha256"
        ):
            ancien = {k: v for k, v in r.get("zones", {}).items() if not v.get("automatique")}
    faits, auto, gardees, reste = {}, {}, {}, []
    for zo in env["zones"]:
        i = zo["id"]
        if i in ancien:
            gardees[i] = ancien[i]
            continue
        if i.startswith("geometrie:"):
            j = juger_geometrie(zo, plaques, gcodes)
        elif zo.get("plaque") in plaques and not plaques[zo["plaque"]].get("erreurs"):
            v = faits[i] = faits_zone(zo, plaques[zo["plaque"]])
            j = juger(v)
        else:
            j = None
        (auto.__setitem__(i, j) if j else reste.append(i))
    resume_pl = {pl: {k: m[k] for k in ("min_s", "derniere_z", "derniere_s", "support")} for pl, m in plaques.items()}
    return {"env": env, "faits": faits, "auto": auto, "gardees": gardees, "reste": reste, "plaques": resume_pl}


def ecrire(res, revue_p, faits_p):
    """Publie les preuves de revue liées aux zones, sans valider physiquement la pièce."""
    fp = Path(faits_p)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(
        json.dumps(
            {
                "outil": "outils/controles/revue_locale.py",
                "criteres": CRITERES,
                "zones": res["faits"],
                "temps_couche_par_plaque": res["plaques"],
                "methode": (
                    "distance / F par couche (accélérations ignorées) ; support = extrusion Support* "
                    "à la couche inférieure, boîte élargie"
                ),
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    preuve = [
        {"fichier": Path(os.path.relpath(fp.resolve(), Path(revue_p).resolve().parent)).as_posix(), "sha256": sha(fp)}
    ]  # relatif à la revue, comme le lit zones_locales.evaluer
    zones = dict(res["gardees"])
    for i, j in res["auto"].items():
        zones[i] = {
            "decision": "justifie",
            "justification": j,
            "derniere_couche_verifiee": True,
            "preuves": preuve,
            "automatique": f"revue_locale.py, critères du {CRITERES['date']}",
        }
    doc = {
        "tranche_sha256": res["env"]["tranche_sha256"],
        "geometrie_sha256": res["env"].get("geometrie_sha256"),
        "auteur": "revue_locale.py (critères déclarés) ; décisions humaines conservées",
        "zones": dict(sorted(zones.items())),
    }
    Path(revue_p).write_text(json.dumps(doc, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")


def main(argv):
    if not argv or "--envoi" not in argv:
        print(__doc__)
        return 64

    def opt(n, d=None):
        return argv[argv.index(n) + 1] if n in argv else d

    tranche = Path(argv[0])
    revue_p = opt("--revue", "REVUE-ZONES-LOCALES.json")
    faits_p = opt("--faits", "preuves/revue-locale-faits.json")
    res = revoir(tranche, opt("--envoi"), revue_p, faits_p)
    sortie = {
        "zones": len(res["env"]["zones"]),
        "justifiees_automatiquement": len(res["auto"]),
        "decisions_conservees": len(res["gardees"]),
        "a_revoir": len(res["reste"]),
        "a_revoir_exemples": res["reste"][:15],
        "temps_couche_par_plaque": res["plaques"],
        "ecrit": "--appliquer" in argv,
    }
    if "--appliquer" in argv:
        ecrire(res, revue_p, faits_p)
    print(json.dumps(sortie, ensure_ascii=False, indent=1))
    return 0 if not res["reste"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
