"""Modifie la géométrie interne sous contraintes de paroi et de zones protégées.

Les résultats sont géométriques : ils ne prouvent ni résistance, ni tenue
physique, ni comportement optique ou phosphorescent.
"""

import argparse
import json
import math
import sys
from datetime import date
from pathlib import Path
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

import numpy as np
import trimesh

from benchab.archives_3mf import Archive3MF
from benchab.fabrication import anycubic as A
from benchab.fabrication import remplissage_localise as RL
from benchab.gcode.lecture import plaques_gcode_archive

R = Path(__file__).resolve().parents[2]

TYPES_PROTEGES = (
    "geometrie",
    "dimension",
    "interface",
    "appui",
    "fixation",
    "trou",
    "logement",
    "esthetique",
    "collage",
    "acces",
    "mecanique",
    "optique",
    "phosphorescent",
    "thermique",
    "fonction",
    "cable",
    "mecanisme",
    "etancheite",
)
OP = dict(engine="manifold")
RENFORTS = ("nervure", "gousset")
EPAISSEUR_MIN = 0.8  # deux lignes de 0,4 mm : en dessous, le slicer peut ne rien déposer
DIRECTIONS = {"+x": (1, 0), "-x": (-1, 0), "+y": (0, 1), "-y": (0, -1)}


def _vol(m):
    return 0.0 if m is None or m.is_empty else abs(float(m.volume))


def cavite(c):
    """Construit une cavité à toit autoportant et valide ses dimensions."""
    if c.get("forme") != "toit":
        raise ValueError(f"cavité {c.get('nom')} : forme {c.get('forme')!r} inconnue (attendu : toit)")
    a = np.array(c["min"], float)
    b = np.array(c["max"], float)
    ang = float(c.get("angle", 45))
    if a.shape != (3,) or b.shape != (3,) or np.any(b <= a):
        raise ValueError(f"cavité {c.get('nom')} : boîte vide ou mal orientée")
    if not 45 <= ang < 90:
        raise ValueError(f"cavité {c.get('nom')} : angle {ang}° hors de [45, 90[ (plafonds autoportants seulement)")
    ax = {"x": 0, "y": 1}.get(c.get("faitage", "x"))
    if ax is None:
        raise ValueError("faitage : x ou y")
    large = 1 - ax
    w = b[large] - a[large]
    hr = w / 2 * math.tan(math.radians(ang))
    zt = b[2] - hr
    if zt <= a[2] + 1e-6:
        raise ValueError(
            f"cavité {c.get('nom')} : hauteur {b[2] - a[2]:.1f} mm insuffisante pour un toit de {hr:.1f} mm"
        )
    # profil dans le plan (largeur, z), extrudé le long du faîtage
    p2 = [(a[large], a[2]), (b[large], a[2]), (b[large], zt), ((a[large] + b[large]) / 2, b[2]), (a[large], zt)]
    v, f = [], []
    for s in (a[ax], b[ax]):
        for u, z in p2:
            q = [0.0, 0.0, z]
            q[ax] = s
            q[large] = u
            v.append(q)
    n = len(p2)
    f += [[0, 2, 1], [0, 3, 2], [0, 4, 3]] + [[n, n + 1, n + 2], [n, n + 2, n + 3], [n, n + 3, n + 4]]
    for i in range(n):
        j = (i + 1) % n
        f += [[i, j, n + j], [i, n + j, n + i]]
    m = trimesh.Trimesh(v, f, process=True)
    trimesh.repair.fix_normals(m)
    if not m.is_volume:
        raise ValueError("cavité : maillage non fermé")
    return m


def renfort(r):
    """Construit une nervure ou un gousset et valide ses dimensions."""
    f = r.get("forme")
    e = float(r.get("epaisseur", 0))
    h = float(r.get("hauteur", 0))
    if f not in RENFORTS:
        raise ValueError(f"renfort {r.get('nom')} : forme {f!r} inconnue (attendu : {', '.join(RENFORTS)})")
    if e <= 0 or h <= 0:
        raise ValueError(f"renfort {r.get('nom')} : épaisseur et hauteur doivent être positives")
    if f == "nervure":
        a = np.array(r["debut"], float)
        b = np.array(list(r["fin"])[:2], float)
        d = b - a[:2]
        L = float(np.hypot(*d))
        if a.shape != (3,) or L <= 0:
            raise ValueError(f"nervure {r.get('nom')} : debut [x, y, z] et fin [x, y] distincts attendus")
        m = trimesh.creation.box(extents=(L, e, h))
        m.apply_transform(trimesh.transformations.rotation_matrix(math.atan2(d[1], d[0]), (0, 0, 1)))
        m.apply_translation((*((a[:2] + b) / 2), a[2] + h / 2))
        return m
    c = np.array(r["coin"], float)
    u = DIRECTIONS.get(r.get("direction"))
    B = float(r.get("base", 0))
    if c.shape != (3,) or u is None or B <= 0:
        raise ValueError(f"gousset {r.get('nom')} : coin [x, y, z], direction ±x/±y et base positive attendus")
    w = (-u[1], u[0])
    v = []
    for s in (-e / 2, e / 2):
        for du, z in ((0, 0), (B, 0), (0, h)):
            v.append([c[0] + u[0] * du + w[0] * s, c[1] + u[1] * du + w[1] * s, c[2] + z])
    m = trimesh.Trimesh(
        v, [[0, 2, 1], [3, 4, 5], [0, 1, 4], [0, 4, 3], [1, 2, 5], [1, 5, 4], [2, 0, 3], [2, 3, 5]], process=True
    )
    trimesh.repair.fix_normals(m)
    if not m.is_volume:
        raise ValueError("gousset : maillage non fermé")
    return m


def _agrandi(z, marge):
    z = dict(z)
    if z["forme"] == "boite":
        z["min"] = [x - marge for x in z["min"]]
        z["max"] = [x + marge for x in z["max"]]
    else:
        z["rayon"] = z["rayon"] + marge
        z["hauteur"] = z["hauteur"] + 2 * marge
    return z


def appliquer(piece, cavites, contraintes=None, echantillons=4000):
    """Construit la variante interne et son rapport de contrôles géométriques."""
    C = contraintes or {}
    pmin = float(C.get("paroi_min", 2.4))
    ctl = []
    var = piece.copy()
    outils = []
    renf = []
    for c in cavites:
        if c.get("forme") in RENFORTS:
            renf.append((c, renfort(c)))
        else:
            m = cavite(c)
            outils.append((c, m))
    if outils:
        var = trimesh.boolean.difference([piece] + [m for _, m in outils], **OP)
    evide = var
    for c, m in renf:
        dans = trimesh.boolean.intersection([m, piece], **OP)
        avant = _vol(var)
        var = trimesh.boolean.union([var, dans], **OP) if _vol(dans) > 0 else var
        ok_e = float(c["epaisseur"]) >= EPAISSEUR_MIN - 1e-9
        ctl.append(
            {
                "controle": f"épaisseur de {c['nom']}",
                "statut": "PASS" if ok_e else "FAIL",
                "detail": f"{c['epaisseur']} mm (minimum {EPAISSEUR_MIN} mm, deux lignes de 0,4)",
            }
        )
        bas = dans.triangles_center[dans.face_normals[:, 2] < -0.7] if _vol(dans) > 0 else np.zeros((0, 3))
        sous = bas - [0, 0, 0.3]
        appui = (
            (sous[:, 2] <= 0.3) | (trimesh.proximity.signed_distance(evide, sous) >= -1e-3)
            if len(sous)
            else np.ones(0, bool)
        )
        ctl.append(
            {
                "controle": f"appui de {c['nom']}",
                "statut": "PASS" if appui.all() else "FAIL",
                "detail": f"{int(appui.sum())}/{len(appui)} facettes inférieures posées sur la matière ou le plateau"
                + ("" if appui.all() else " : renfort flottant, surplomb"),
            }
        )
        ajout = _vol(var) - avant
        ctl.append(
            {
                "controle": f"effet de {c['nom']}",
                "statut": "PASS" if ajout > 1 else "WARN",
                "detail": f"{ajout:.1f} mm³ ajoutés"
                + ("" if ajout > 1 else " : renfort entièrement dans la matière, sans effet"),
            }
        )

    def ok(condition):
        return "PASS" if condition else "FAIL"

    coques = var.split(only_watertight=False)
    corps = sum(1 for q in coques if q.volume > 0)  # une cavité interne est une coque de volume négatif
    ctl.append(
        {
            "controle": "solide valide",
            "statut": ok(var.is_watertight and var.is_volume and corps == 1),
            "detail": (
                f"fermé {var.is_watertight}, volume {var.is_volume}, corps {corps}, "
                f"cavités internes {len(coques) - corps}"
            ),
        }
    )
    # enveloppe : chaque point de la surface de référence doit rester sur la surface de la variante
    pts = trimesh.sample.sample_surface_even(piece, echantillons, seed=7)[0]
    d = np.abs(trimesh.proximity.signed_distance(var, pts))
    ctl.append(
        {
            "controle": "enveloppe extérieure conservée",
            "statut": ok(float(d.max()) < 0.01),
            "detail": f"écart max {d.max():.4f} mm sur {len(pts)} points",
        }
    )
    for c, m in outils:
        q = trimesh.sample.sample_surface_even(m, 1500, seed=11)[0]
        sd = trimesh.proximity.signed_distance(piece, q)  # > 0 : dans la pièce
        ctl.append(
            {
                "controle": f"paroi autour de {c['nom']}",
                "statut": ok(float(sd.min()) >= pmin - 1e-3),
                "detail": f"matière min {sd.min():.2f} mm (exigé {pmin} mm)",
            }
        )
        nz = m.face_normals[:, 2]
        plafonds = m.area_faces[nz > math.cos(math.radians(45)) + 1e-6].sum()
        ctl.append(
            {
                "controle": f"plafonds autoportants de {c['nom']}",
                "statut": ok(plafonds < 1e-6),
                "detail": f"{plafonds:.1f} mm² de plafond à moins de 45° de l’horizontale",
            }
        )
        ctl.append(
            {
                "controle": f"cavité captive {c['nom']}",
                "statut": "WARN",
                "detail": (
                    "fermée : aucun support ne doit y être généré (supports depuis le plateau seulement) ; "
                    "à vérifier dans le G-code"
                ),
            }
        )
    for z in C.get("protege", []):
        if z.get("type") not in TYPES_PROTEGES:
            raise ValueError(f"région protégée {z.get('nom')} : type {z.get('type')!r} inconnu")
        P = RL.volume(_agrandi(z, float(z.get("marge", 0))))
        dv = (
            _vol(trimesh.boolean.intersection([trimesh.boolean.difference([piece, var], **OP), P], **OP))
            if outils
            else 0.0
        )
        ctl.append(
            {
                "controle": f"région protégée {z['nom']} ({z['type']})",
                "statut": ok(dv < 1e-3),
                "detail": f"matière modifiée dans la région et sa marge : {dv:.4f} mm³",
            }
        )
    statut = (
        "FAIL"
        if any(x["statut"] == "FAIL" for x in ctl)
        else ("WARN" if any(x["statut"] == "WARN" for x in ctl) else "PASS")
    )
    return var, {
        "statut": statut,
        "volume_reference_mm3": round(_vol(piece), 1),
        "volume_variante_mm3": round(_vol(var), 1),
        "volume_retire_mm3": round(_vol(piece) - _vol(var), 1),
        "volume_renforts_mm3": round(_vol(var) - _vol(evide), 1),
        "controles": ctl,
        "efforts_declares": C.get("efforts", []),
        "limite": "Contrôles géométriques ; ni résistance, ni lumière, ni phosphorescence évaluées.",
    }


NERVURES_EPROUVETTE = [
    {
        "nom": "nervure longitudinale",
        "forme": "nervure",
        "debut": [20, 20, 3],
        "fin": [60, 20],
        "hauteur": 18.5,
        "epaisseur": 1.2,
    }
] + [
    {
        "nom": f"nervure transversale {i + 1}",
        "forme": "nervure",
        "debut": [x, 3, 3],
        "fin": [x, 37],
        "hauteur": 18.5,
        "epaisseur": 1.2,
    }
    for i, x in enumerate((100 / 3, 140 / 3))
]


def seuil(lignes, prefixe, cle="ecart_pct_masse"):
    """Interpole la densité où l'écart à la référence change de signe."""
    pts = sorted(
        (float(ligne["remplissage"].rstrip("%")), ligne[cle])
        for ligne in lignes
        if ligne["variante"].startswith(prefixe)
    )
    for d, e in pts:
        if e == 0:
            return d
    for (d0, e0), (d1, e1) in zip(pts, pts[1:]):
        if e0 * e1 < 0:
            return round(d0 + (d1 - d0) * e0 / (e0 - e1), 1)
    return None


def _verifier_resultats(resultats, nombre):
    indices = [r.get("index") for r in resultats]
    if len(indices) != nombre or any(type(i) is not int for i in indices) or set(indices) != set(range(1, nombre + 1)):
        raise ValueError("résultats de tranchage incomplets, dupliqués ou plaques inattendues")


def eprouvette(sortie, tranches, base_3mf, densites=(15, 20, 25, 30, 35, 40)):
    """Compare des densités et variantes géométriques dans un seul tranchage.

    Réglages en unités
    natives (mm, s, °C, %) lus via le dictionnaire typé ; aucun réglage inconnu transformé. Le profil
    explicite reste la provenance du fichier neuf (docs/reference/integrite-3mf.md).
    """
    from benchab.chemins import atelier_prive, est_temporaire, sans_lien

    out, tr = sans_lien(sortie), sans_lien(tranches)
    for dossier in (out, tr):
        if not dossier.is_relative_to(atelier_prive()) and not est_temporaire(dossier):
            raise ValueError("Les sorties dépendantes d'un projet doivent rester dans prive/ ou /private/tmp")
    for dossier in (out, tr):
        if dossier.exists() and (not dossier.is_dir() or any(dossier.iterdir())):
            raise FileExistsError(f"{dossier} existe et contient déjà des données : sortie neuve requise")
    with Archive3MF(base_3mf) as archive:
        st = archive.reglages()
        archive.verifier_source(base_3mf)
    bloc = trimesh.creation.box(extents=(80, 40, 24))
    bloc.apply_translation((40, 20, 12))
    trous = [trimesh.creation.cylinder(radius=2.2, height=26, sections=48) for _ in range(2)]
    for t, x in zip(trous, (10, 70)):
        t.apply_translation((x, 20, 12))
    ref = trimesh.boolean.difference([bloc] + trous, **OP)
    contraintes = {
        "paroi_min": 2.4,
        "protege": [
            {
                "nom": f"vis {i + 1}",
                "type": "fixation",
                "forme": "cylindre",
                "centre": [x, 20, 12],
                "rayon": 6,
                "hauteur": 24,
                "marge": 1.5,
            }
            for i, x in enumerate((10, 70))
        ]
        + [{"nom": "appui inférieur", "type": "appui", "forme": "boite", "min": [0, 0, 0], "max": [80, 40, 2.4]}],
        "efforts": [{"nom": "vissage des deux vis", "type": "vissage", "note": "déclaré pour l’essai, non chiffré"}],
    }
    cav = [
        {
            "nom": "évidement central",
            "forme": "toit",
            "min": [20, 3, 3],
            "max": [60, 37, 21],
            "faitage": "x",
            "angle": 45,
        }
    ]
    varB, rapB = appliquer(ref, cav, contraintes)
    varD, rapD = appliquer(ref, cav + NERVURES_EPROUVETTE, contraintes)
    zones = [
        {
            "nom": f"vis {i + 1}",
            "role": "fixation",
            "forme": "cylindre",
            "centre": [x, 20, 12],
            "rayon": 6,
            "hauteur": 24,
        }
        for i, x in enumerate((10, 70))
    ]
    reg = {
        "sparse_infill_density": "15%",
        "sparse_infill_pattern": "gyroid",
        "wall_loops": "3",
        "top_shell_layers": "4",
        "bottom_shell_layers": "3",
        "enable_support": "1",
        "support_on_build_plate_only": "1",
        "support_type": "tree(auto)",
    }

    def rg(densite):
        return dict(reg, sparse_infill_density=f"{densite}%")

    variantes = [v for d in densites for v in ((f"A{d}-reference", ref, [], rg(d)), (f"B{d}-cavite", varB, [], rg(d)))]
    variantes += [("C15-cavite-zones", varB, RL.volumes(varB, zones)[0], rg(15))] + [
        (f"D{d}-cavite-nervures", varD, [], rg(d)) for d in (15, 40) if d in densites
    ]
    plaques = []
    for k, (nom, m, vols, rg) in enumerate(variantes):
        it = {"name": nom, "mesh": m, "w": 80.0, "h": 40.0, "extruder": 1, "settings": rg, "volumes": vols}
        A.ranger([it], k, len(variantes), 10.0, cli=True)
        plaques.append({"name": nom, "items": [it]})
    out = Path(sortie)
    out.mkdir(parents=True, exist_ok=True)
    tr = Path(tranches)
    tr.mkdir(parents=True, exist_ok=True)
    for nom, m in (("A-reference", ref), ("B-cavite", varB), ("D-cavite-nervures", varD)):
        m.export(out / f"{nom}.stl")
    source_3mf = out / "eprouvette-plaques.3mf"
    # Le profil explicitement fourni est conservé comme provenance, sans réécriture de réglages.
    src = A.ecrire_3mf(plaques, st, source_3mf, cli=True, canonique=True, nom_source=source_3mf.name)
    t3, sec = A.trancher(src, tr, "eprouvette-tranche.3mf")
    lignes = []
    with Archive3MF(t3) as archive:
        # Lire ensemble les résultats et le membre de chaque plaque, sans reconstruire son nom (#169).
        xml = archive.lire("Metadata/slice_info.config", limite_octets=Archive3MF.MAX_XML_OCTETS)
        res = sorted(A.resultats_xml(xml), key=lambda r: r["index"])
        _verifier_resultats(res, len(variantes))
        membres = dict(plaques_gcode_archive(archive))
        if set(membres) != {r["index"] for r in res}:
            raise ValueError("Les numéros des G-code ne correspondent pas aux résultats de plaques")
        for (nom, m, vols, rg), r, pl in zip(variantes, res, plaques):
            it = pl["items"][0]
            x0, y0 = it["x"], it["y"]
            g = archive.lire(membres[r["index"]]).decode(errors="replace")
            a = RL.analyser_gcode(
                g,
                {
                    "emprise de la cavité": [x0 + 20, y0 + 3, x0 + 60, y0 + 37, 0, 30],
                    "bossages des vis": [x0 + 4, y0 + 14, x0 + 16, y0 + 26, 0, 30],
                },
                types=(
                    "Sparse infill",
                    "Internal solid infill",
                    "Inner wall",
                    "Outer wall",
                    "Top surface",
                    "Bottom surface",
                    "Bridge",
                    "Overhang wall",
                    "Support",
                ),
            )
            lignes.append(
                {
                    "variante": nom,
                    "remplissage": rg["sparse_infill_density"],
                    "volume_cao_mm3": round(_vol(m), 1),
                    "grammes": round(r["grammes"], 2),
                    "secondes": r["secondes"],
                    "supports_slicer": r["supports"],
                    "zones_remplissage": [v["name"] for v in vols],
                    "longueurs_mm": a,
                }
            )
        archive.verifier_source(t3)
    for ligne in lignes:  # écarts par rapport à la référence de même densité
        ref_g, ref_s = next(
            (x["grammes"], x["secondes"])
            for x in lignes
            if x["variante"].startswith("A") and x["remplissage"] == ligne["remplissage"]
        )
        ligne.update(
            ecart_g=round(ligne["grammes"] - ref_g, 2),
            ecart_pct_masse=round((ligne["grammes"] / ref_g - 1) * 100, 1),
            ecart_min=round((ligne["secondes"] - ref_s) / 60, 1),
            ecart_pct_temps=round((ligne["secondes"] / ref_s - 1) * 100, 1),
        )
    rapport = {
        "tranchage_s": sec,
        "reglages": reg,
        "contraintes": contraintes,
        "cavites": cav,
        "controles_B": rapB,
        "nervures": NERVURES_EPROUVETTE,
        "controles_D": rapD,
        "seuil_cavite_masse_pct": seuil(lignes, "B"),
        "seuil_cavite_temps_pct": seuil(lignes, "B", "ecart_pct_temps"),
        "limite_seuil": (
            "estimation du slicer sur cette éprouvette (gyroïde, 3 parois, 0,2 mm) ; ni résistance ni autre géométrie"
        ),
        "variantes": lignes,
        "tranche": Path(t3).name,
        "source_3mf": "eprouvette-plaques.3mf",
    }
    (out / "eprouvette.json").write_text(json.dumps(rapport, indent=1, ensure_ascii=False), encoding="utf-8")
    return rapport


def main(argv=None):
    """Lance l'étude géométrique et son tranchage via le client BENCHAB."""
    parser = argparse.ArgumentParser(description=__doc__)
    commandes = parser.add_subparsers(dest="commande", required=True)
    etude = commandes.add_parser("eprouvette")
    etude.add_argument("--base", type=Path, required=True)
    etude.add_argument(
        "--sortie", type=Path, default=R / f"prive/mesures/{date.today().isoformat()}-optimisation-interne"
    )
    etude.add_argument(
        "--tranches", type=Path, default=Path(f"/private/tmp/optimisation-interne-tranches-{date.today().isoformat()}")
    )
    etude.add_argument("--densites", default="15,20,25,30,35,40")
    args = parser.parse_args(argv)
    try:
        densites = tuple(int(valeur) for valeur in args.densites.split(","))
        rapport = eprouvette(args.sortie, args.tranches, args.base, densites)
    except (OSError, ValueError, KeyError, BadZipFile, ParseError, RuntimeError) as erreur:
        print(f"Éprouvette refusée : {erreur}", file=sys.stderr)
        return 2
    resume = {
        "controles_B": rapport["controles_B"]["statut"],
        "controles_D": rapport["controles_D"]["statut"],
        "seuil_cavite_masse_pct": rapport["seuil_cavite_masse_pct"],
        "seuil_cavite_temps_pct": rapport["seuil_cavite_temps_pct"],
        "variantes": [
            {cle: valeur for cle, valeur in ligne.items() if cle != "longueurs_mm"} for ligne in rapport["variantes"]
        ],
    }
    print(json.dumps(resume, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
