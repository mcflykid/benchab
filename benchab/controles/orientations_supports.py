"""Étude des orientations, des supports et de la direction des efforts.

Usage : benchab controle orientations-supports <piece.stl> --profil <profil.3mf> --sortie <dossier>

Pour chaque pièce : ~600 orientations (sphère de Fibonacci + normales des grandes faces de l'enveloppe convexe) ;
indice rapide
= surface en surplomb (pente < 40° sur l'horizontale, hors plateau) × hauteur ; assise minimale au plateau ; lacet
qui minimise
l'empreinte et encombrement du plateau ; puis volume de support par lancer de rayons (jusqu'à la pièce ou au
plateau) pour les 25
meilleures ; enfin tranchage réel des N meilleures (réglages du 3MF de profil, supports de
outils/controles/supports_requis.py) et
masse de support mesurée dans le G-code. Écrit <dossier>/orientations.json et les STL posés <pièce>-<rang>.stl.
Module : etudier(mesh, …) et pose(mesh, direction_bas) sont réutilisables par un projet. Estimation du slicer : la
qualité des faces
posées, l'aspect sous les supports et la tenue de l'assise sont à juger à part (voir aussi le contrôle « assise » de
controles_piece.py).
Résistance entre couches (option avec avertissement) : seulement
si des efforts sont déclarés (--effort, ou efforts=[…] dans etudier ; directions dans le repère de la pièce, ex.
traction ou flexion
d'une patte). Chaque orientation reçoit risque_couches = |cos| de l'angle entre l'effort et l'axe de fabrication (1
: effort
perpendiculaire aux couches, le cas faible du FDM ; 0 : effort dans le plan des couches). Le classement par supports
reste inchangé ;
s'y ajoutent la meilleure orientation à risque ≤ seuil et un AVERTISSEMENT qui dit que la résistance a été prise en
compte et chiffre
l'écart de supports, pour décider si c'est pertinent. Indice géométrique : aucune contrainte calculée ni résistance
mesurée.

"""

import json
import math
import sys
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import trimesh as tm

from benchab import reglages as schema_reglages
from benchab.controles import supports_requis as S
from benchab.fabrication import anycubic as A
from benchab.gcode.lecture import lire_texte_gcode_3mf
from benchab.machine.configuration import charger as charger_configuration

ICI = Path(__file__).resolve().parent
SEUIL = math.cos(math.radians(50))  # normale à plus de 50° de la verticale vers le bas = pente < 40° sur l'horizontale


def zone_etude():
    """Limite conservative pour l'étude d'orientation, distincte du volume constructeur."""
    dimensions = charger_configuration().get("placement", {}).get("zone_etude_mm")
    if (
        not isinstance(dimensions, list)
        or len(dimensions) != 3
        or any(type(valeur) not in (int, float) or not math.isfinite(valeur) or valeur <= 0 for valeur in dimensions)
    ):
        raise ValueError("placement.zone_etude_mm doit contenir trois dimensions positives et finies.")
    return tuple(float(valeur) for valeur in dimensions)


def rotation_vers_bas(v):
    return tm.geometry.align_vectors(np.asarray(v, float) / np.linalg.norm(v), [0, 0, -1])


def pose(m, direction_bas, plateau=None):
    """
    Matrice 4×4 : direction_bas (repère de la pièce) vers le plateau, lacet minimisant l'empreinte, coin minimal à
    l'origine.
        Renvoie (matrice, tient_sur_le_plateau).
    """
    plateau = zone_etude() if plateau is None else plateau
    T = rotation_vers_bas(direction_bas)
    q = m.copy()
    q.apply_transform(T)
    best = None
    for yaw in range(0, 90, 5):
        Y = tm.transformations.rotation_matrix(math.radians(yaw), [0, 0, 1])
        e = q.copy().apply_transform(Y).extents
        ok = e[0] <= plateau[0] and e[1] <= plateau[1] and e[2] <= plateau[2]
        if best is None or (ok, -e[0] * e[1]) > (best[1], -best[2]):
            best = (Y, ok, e[0] * e[1])
    T = best[0] @ T
    q = m.copy()
    q.apply_transform(T)
    return tm.transformations.translation_matrix(-q.bounds[0]) @ T, best[1]


def directions(m, n=520):
    i = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * i / n)
    th = math.pi * (1 + 5**0.5) * i
    fib = np.c_[np.cos(th) * np.sin(phi), np.sin(th) * np.sin(phi), np.cos(phi)]
    h = m.convex_hull
    return np.vstack([fib, [h.facets_normal[k] for k in np.argsort(h.facets_area)[::-1][:80]]])


def faces_surplomb(q):
    """
    Indices des faces du contrôle existant, dans le repère Z de fabrication.
    """
    return np.flatnonzero((q.face_normals[:, 2] < -SEUIL) & (q.triangles_center[:, 2] > 0.4))


def mesure_rapide(q):
    n, c, a = q.face_normals, q.triangles_center, q.area_faces
    sur = faces_surplomb(q)
    plat = (n[:, 2] < -0.9) & (c[:, 2] < 0.6)
    return {
        "surplomb_mm2": float(a[sur].sum()),
        "indice_support_mm3": float((a[sur] * -n[sur, 2] * c[sur, 2]).sum()),
        "appui_mm2": float(a[plat].sum()),
    }


def support_rayons(q, maxi=3000):
    n, c, a = q.face_normals, q.triangles_center, q.area_faces
    tous = faces_surplomb(q)
    if len(tous) == 0:
        return 0.0
    ids = (
        np.random.default_rng(1).choice(tous, maxi, replace=False, p=a[tous] / a[tous].sum())
        if len(tous) > maxi
        else tous
    )
    loc, ir, _ = q.ray.intersects_location(
        c[ids] - [0, 0, 0.05], np.tile([0, 0, -1.0], (len(ids), 1)), multiple_hits=False
    )
    h = c[ids, 2].copy()
    if len(ir):
        h[ir] = (
            c[ids[ir], 2] - np.asarray(loc).reshape(-1, 3)[:, 2]
        )  # aucun rayon ne touche la pièce : tout descend au plateau (corrigé le 23/10)
    return float((a[ids] * -n[ids, 2] * h).sum() * a[tous].sum() / a[ids].sum())


def risque_couches(direction_bas, efforts):
    """
    |cos| maximal entre les efforts déclarés et l'axe de fabrication (−direction_bas) : 1 = effort perpendiculaire
    aux couches.
    """
    z = -np.asarray(direction_bas, float)
    z /= np.linalg.norm(z)
    return round(max(abs(float(z @ (np.asarray(e, float) / np.linalg.norm(e)))) for e in efforts), 3)


def resistance(fins, efforts, seuil=0.5):
    """
    Avertissement de résistance : meilleure orientation pour les supports contre meilleure à effort dans le plan des
    couches.
    """
    if not fins:
        return None
    a = fins[0]
    b = next((x for x in fins if x["risque_couches"] <= seuil), None)
    t = (
        f"RÉSISTANCE PRISE EN COMPTE (efforts déclarés {efforts}, indice géométrique, rien de mesuré) : "
        "la meilleure orientation pour "
        f"les supports a un risque entre couches de {a['risque_couches']:.2f}"
    )
    if b is None:
        t += (
            f" ; aucune orientation étudiée n'a un risque ≤ {seuil} : "
            "vérifier les efforts déclarés ou renforcer la pièce."
        )
    elif b is a:
        t += f" (≤ {seuil}) : pas de conflit, elle convient aussi pour la résistance."
    else:
        t += (
            f" ; la meilleure à risque ≤ {seuil} (direction {b['direction_bas']}, "
            f"risque {b['risque_couches']:.2f}) demande "
            f"{b['support_rayons_mm3'] - a['support_rayons_mm3']:+.0f} mm³ de supports estimés. "
            "À décider selon la charge réelle de la pièce."
        )
    return {
        "efforts": efforts,
        "seuil": seuil,
        "supports_d_abord": a["direction_bas"],
        "risque_supports_d_abord": a["risque_couches"],
        "compromis_couches": b["direction_bas"] if b else None,
        "avertissement": t,
    }


def etudier(m, appui_min=150.0, plateau=None, garder=25, efforts=None, seuil=0.5):
    """
    Classe les orientations d'une pièce ; renvoie [{direction_bas, matrice, surplomb_mm2, appui_mm2,
    support_rayons_mm3, …}] trié.
        efforts (option) : ajoute risque_couches à chaque orientation et garde aussi les meilleures à risque ≤ seuil
    (voir resistance()).
    """
    plateau = zone_etude() if plateau is None else plateau
    lignes = []
    for d in directions(m):
        q = m.copy()
        q.apply_transform(rotation_vers_bas(d))
        q.apply_translation([0, 0, -q.bounds[0, 2]])
        r = mesure_rapide(q)
        if r["appui_mm2"] >= appui_min:
            lignes.append((r["indice_support_mm3"], d, r))
    lignes.sort(key=lambda x: x[0])
    fins = []
    if efforts:  # en plus des meilleures pour les supports, les meilleures à effort dans le plan des couches
        bons = [ligne for ligne in lignes if risque_couches(ligne[1], efforts) <= seuil][: max(5, garder // 3)]
        lignes = lignes[:garder] + [ligne for ligne in bons if not any(ligne is x for x in lignes[:garder])]
    for _, d, r in lignes:
        if len(fins) >= garder + (max(5, garder // 3) if efforts else 0):
            break
        T, ok = pose(m, d, plateau)
        if not ok:
            continue
        q = m.copy()
        q.apply_transform(T)
        fins.append(
            dict(
                r,
                direction_bas=[round(float(x), 4) for x in d],
                matrice=T.tolist(),
                dimensions_mm=[round(float(x), 1) for x in q.extents],
                support_rayons_mm3=round(support_rayons(q)),
            )
        )
        if efforts:
            fins[-1]["risque_couches"] = risque_couches(d, efforts)
    fins.sort(key=lambda x: (x["support_rayons_mm3"], x.get("risque_couches", 0)))
    return fins  # à supports égaux, effort dans le plan des couches d'abord


def trancher(cands, profil, reglages_support):
    """cands : [(nom, mesh posé)] ; profil : 3MF dont on reprend project_settings ; renvoie la masse,
    le temps et la masse de support par plaque. Réglages en unités natives (mm, s, °C, %) lus via le
    dictionnaire typé ; aucun réglage inconnu transformé. Le 3MF neuf porte ce profil explicite comme provenance."""
    st = schema_reglages.decoder(ZipFile(profil).read("Metadata/project_settings.config"))
    st.update(reglages_support)
    # U25 : la masse dépend du diamètre déclaré, jamais d'une constante implicite.
    densite = float(schema_reglages.valeur_unique(st, "filament_density"))
    diametre = float(schema_reglages.valeur_unique(st, "filament_diameter"))
    if not all(math.isfinite(v) and v > 0 for v in (densite, diametre)):
        raise ValueError("Densité ou diamètre filament NON VÉRIFIÉ")
    aire = math.pi * (diametre / 2) ** 2
    plates = []
    for k, (nom, q) in enumerate(cands):
        it = {
            "name": nom,
            "mesh": q,
            "w": float(q.extents[0]) + 10,
            "h": float(q.extents[1]) + 10,
            "extruder": 1,
            "settings": dict(reglages_support),
        }
        A.ranger([it], k, len(cands), 10, cli=True)
        it["x"] += 5
        it["y"] += 5
        plates.append({"name": "", "items": [it]})
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        cible = Path(d) / "orientations.3mf"
        # Le profil choisi doit rester vérifiable dans le 3MF produit (docs/reference/integrite-3mf.md).
        src = A.ecrire_3mf(plates, st, cible, cli=True, canonique=True, nom_source=cible.name)
        t, _ = A.trancher(src, d, "orientations-tranche.3mf")
        res = A.resultats(t)
        out = []
        for r in res:
            g = lire_texte_gcode_3mf(t, f"Metadata/plate_{r['index']}.gcode")
            sup = sum(S.masse_support(g, aire, densite)[0].values())  # réamorçages exclus
            out.append(
                {"grammes_total": r["grammes"], "minutes": round(r["secondes"] / 60), "support_g": round(sup, 2)}
            )
    return out


def main(argv=None):
    """Rapporte les orientations candidates et besoins en supports, sans imprimer."""
    a = list(sys.argv[1:] if argv is None else argv)
    if "--profil" not in a or "--sortie" not in a:
        print(__doc__)
        return 2
    stls = [x for x in a[: min(a.index("--profil"), a.index("--sortie"))] if not x.startswith("--")]
    sortie = Path(a[a.index("--sortie") + 1])
    sortie.mkdir(parents=True, exist_ok=True)
    profil = a[a.index("--profil") + 1]
    n = int(a[a.index("--tranches") + 1]) if "--tranches" in a else 4
    amin = float(a[a.index("--appui-min") + 1]) if "--appui-min" in a else 150.0
    plat = (
        tuple(float(x) for x in a[a.index("--plateau") + 1 : a.index("--plateau") + 4])
        if "--plateau" in a
        else zone_etude()
    )
    rs = S.reglages(
        a[a.index("--type") + 1] if "--type" in a else "arbre",
        a[a.index("--style") + 1] if "--style" in a else "organique",
        partout="--partout" in a,
    )
    efforts = [[float(v) for v in a[i + 1].split(",")] for i, x in enumerate(a) if x == "--effort"] or None
    seuil = float(a[a.index("--seuil-couches") + 1]) if "--seuil-couches" in a else 0.5
    rapport, cands = {"methode": __doc__.split("\n")[0], "seuil_pente_deg": 40, "supports": rs, "pieces": {}}, []
    for f in stls:
        m = tm.load(f, force="mesh")
        nom = Path(f).stem
        fins = etudier(m, amin, plat, efforts=efforts, seuil=seuil)
        rapport["pieces"][nom] = {
            "meilleures": [{k: v for k, v in x.items() if k != "matrice"} | {"matrice": x["matrice"]} for x in fins[:8]]
        }
        if efforts:
            res = resistance(fins, efforts, seuil)
            rapport["pieces"][nom]["resistance"] = res
            print(nom, ":", res["avertissement"])
            c = next((x for x in fins if x["direction_bas"] == res["compromis_couches"]), None)
            if c is not None and c not in fins[:8]:
                rapport["pieces"][nom]["meilleures"].append(dict(c, rang="compromis couches"))
        for i, x in enumerate(fins[:n]):
            q = m.copy()
            q.apply_transform(np.array(x["matrice"]))
            q.export(sortie / f"{nom}-{i + 1}.stl")
            cands.append((f"{nom} #{i + 1}", q))
        print(
            nom,
            ":",
            len(fins),
            "orientations sur le plateau ; meilleure estimation",
            fins[0]["support_rayons_mm3"] if fins else None,
            "mm³",
        )
    for (lib, _), t in zip(cands, trancher(cands, profil, rs) if cands else []):
        nom, rang = lib.rsplit(" #", 1)
        rapport["pieces"][nom]["meilleures"][int(rang) - 1]["tranchage"] = t
        print(lib, t)
    (sortie / "orientations.json").write_text(
        json.dumps(rapport, indent=1, ensure_ascii=False, default=float),
        encoding="utf-8",
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
