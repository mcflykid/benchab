"""Connecteurs géométriques du lot 6B ; aucun n'est validé physiquement.

Le jeu est radial dans le catalogue historique et latéral par face pour les clips.
Sans mesure applicable, le haut de la plage du témoin de jeux est une hypothèse.
"""

import math

import manifold3d as mf
import numpy as np
import trimesh
from shapely.geometry import Point, Polygon, box

from benchab.conception.decoupe import _plan, _repere, _vers_trimesh, section
from benchab.conception.decoupe.geometrie import reassemblage, solide
from benchab.conception.eprouvette_jeux import JEUX

CATALOGUE = {
    "cheville-rond": "Cheville conique ronde",
    "cheville-rectangle": "Cheville conique rectangulaire",
    "cheville-d": "Cheville conique en D, sens de montage unique",
    "cheville-queue-aronde": "Cheville à profil queue d'aronde",
    "rotule-simple": "Rotule simple : sphère et douille",
    "rotule-double": "Rotule double : deux douilles et axe à deux sphères séparé",
    "douille": "Douilles et goupille imprimée séparément",
    "canal-tige": "Canal pour tige de renfort",
    "clip-languette": "Clip à une languette et rampe nominale",
    "clip-double": "Clip à deux languettes opposées",
    "aucun": "Joint plat, collage ou pose",
}
_CLIPS = {"clip-languette", "clip-double"}


def politique_jeu(options):
    """Renvoie le jeu nominal en mm et sa provenance ; aucun ajustement physique n’est déduit.

    Pour un clip, une mesure déclarée de même famille et bobine prévaut.
    Sans mesure, le haut de la plage sourcée reste NON MESURÉ.
    """
    type_ = options.get("type")
    clip = isinstance(type_, str) and type_ in _CLIPS
    nature = "lateral_par_face" if clip else "radial"
    mesure = options.get("mesure_jeu")
    if mesure is not None:
        if not isinstance(mesure, dict) or not mesure.get("reference") or mesure.get("nature") != nature:
            raise ValueError(f"Une mesure exige nature={nature} et une référence physique explicite")
        if clip and (
            mesure.get("connecteur") != type_
            or not isinstance(mesure.get("reference"), str) or not mesure["reference"].strip()
            or not isinstance(mesure.get("bobine"), str) or not mesure["bobine"].strip()
        ):
            raise ValueError("La mesure d’un clip exige le même connecteur et une bobine explicite")
        if mesure.get("bobine") and mesure["bobine"] != options.get("bobine"):
            raise ValueError("La mesure de jeu ne concerne pas la bobine déclarée")
        valeur = mesure.get("valeur_mm")
        provenance = {"statut": "MESURE DÉCLARÉE", "reference": mesure["reference"], "bobine": mesure.get("bobine")}
    else:
        plage = options.get("plage_jeu_mm", [min(JEUX), max(JEUX)])
        if (
            not isinstance(plage, list)
            or len(plage) != 2
            or any(type(v) not in (float, int) or not math.isfinite(v) or v <= 0 for v in plage)
            or plage[0] > plage[1]
        ):
            raise ValueError("Plage de jeu : deux bornes positives finies et ordonnées")
        if "plage_jeu_mm" in options and not options.get("source_plage"):
            raise ValueError("Une plage personnalisée exige sa source documentaire")
        valeur = plage[1]
        provenance = {
            "statut": "NON MESURÉ",
            "plage_mm": plage,
            "source": options.get("source_plage", "benchab.conception.eprouvette_jeux.JEUX : plage d'essai"),
            "limite": "Haut de plage U30, aucune tenue physique déduite ; imprimer le témoin de jeu",
        }
    if type(valeur) not in (int, float) or not math.isfinite(valeur) or not 0 < valeur <= 2:
        raise ValueError("Jeu fini entre 0 et 2 mm exigé" if clip else "Jeu radial fini entre 0 et 2 mm exigé")
    return float(valeur), provenance


def conseiller(*, mobile=False, renfort=False, section_mm2=None):
    choix = "rotule-double" if mobile else "canal-tige" if renfort else "douille"
    alternatives = {
        "douille": ("Goupille remplaçable et impression séparée", "Pièce supplémentaire, collage éventuel à qualifier"),
        "cheville-d": ("Détrompage et deux pièces seulement", "Tenon solidaire plus fragile à reprendre"),
        "aucun": ("Aucun logement ne fragilise une petite section", "Alignement et maintien à organiser"),
        "rotule-simple": ("Moins de pièces pour une articulation", "Montage, rétention et usure non validés U24"),
        "canal-tige": ("Renfort continu possible", "Tige et longueur réelle à vérifier avant assemblage"),
    }
    if section_mm2 is not None and section_mm2 < 40 and not mobile and not renfort:
        choix = "aucun"
    autres = [k for k in ("douille", "cheville-d", "aucun", "rotule-simple", "canal-tige") if k != choix][:3]
    return {
        "type": choix,
        "raison": "Articulation demandée"
        if mobile
        else "Renfort demandé"
        if renfort
        else "Section faible : conserver la matière"
        if choix == "aucun"
        else "Goupille séparée, remplaçable, dont la pose d'impression peut être choisie indépendamment",
        "alternatives": [
            {"type": k, "avantage": alternatives[k][0], "inconvenient": alternatives[k][1]} for k in autres
        ],
    }


def _cylindre(r, longueur, z=0.0):
    return mf.Manifold.cylinder(longueur, r, r, 64, True).translate([0, 0, z])


def _boite_clip(x0, x1, demi_y, z0, z1):
    """Crée un volume rectangulaire local en mm, centré sur Y."""
    return mf.Manifold.cube([x1 - x0, 2 * demi_y, z1 - z0]).translate([x0, -demi_y, z0])


def _clip_nominal(type_, rayon, longueur, jeu, paroi):
    """Construit deux volumes complémentaires et leur garde, sans calcul de déformation."""
    double = type_ == "clip-double"
    branches = [(-0.55 * rayon, -1), (0.55 * rayon, 1)] if double else [(0.0, 1)]
    epaisseur = 0.30 * rayon if double else 0.40 * rayon
    demi_y = 0.65 * rayon
    debord = (0.28 if double else 0.40) * rayon
    haut = longueur / 2
    epaule = haut - min(0.65 * rayon, longueur / 4)
    base = min(haut, max(0.5, epaisseur))
    if debord - jeu <= 1e-6 or epaule - jeu <= 1e-6:
        raise ValueError("Le clip exige un épaulement supérieur au jeu et une gorge utile.")
    if double and branches[1][0] - branches[0][0] - epaisseur - 2 * debord <= jeu + 1e-6:
        raise ValueError("La course nominale des deux languettes se recouvre.")

    males, logements = [], []
    bord_x = 0.0
    for centre, signe in branches:
        demi_x = epaisseur / 2
        tige = _boite_clip(centre - demi_x, centre + demi_x, demi_y, -base, haut)
        # Section XZ de la rampe : l’épaulement reste large derrière la gorge.
        contour = [(0.0, epaule), (signe * (demi_x + debord), epaule), (signe * demi_x, haut), (0.0, haut)]
        if signe < 0:
            contour.reverse()
        tete = mf.Manifold.extrude(mf.CrossSection([contour]), 2 * demi_y)
        tete = tete.rotate([90, 0, 0]).translate([centre, demi_y, 0])
        males.append(tige + tete)
        # L’entrée réserve la course vers l’intérieur, mais pas le dépassement
        # extérieur ; ce dernier est logé seulement au-delà de l’épaulement.
        gorge_x = sorted((centre - signe * (demi_x + debord + jeu), centre + signe * (demi_x + jeu)))
        tete_x = sorted((centre - signe * (demi_x + jeu), centre + signe * (demi_x + debord + jeu)))
        gorge = _boite_clip(*gorge_x, demi_y + jeu, -1e-4, haut + jeu)
        poche = _boite_clip(*tete_x, demi_y + jeu, epaule - jeu, haut + jeu)
        logements.append(gorge + poche)
        bord_x = max(bord_x, *(abs(x) for x in (*gorge_x, *tete_x)))
    male = mf.Manifold.batch_boolean(males, mf.OpType.Add)
    femelle = mf.Manifold.batch_boolean(logements, mf.OpType.Add)
    garde = _boite_clip(-bord_x - paroi, bord_x + paroi, demi_y + jeu + paroi, -base - paroi, haut + jeu + paroi)
    mesures = {
        "nombre_languettes": len(branches),
        "epaisseur_nominale_mm": epaisseur,
        "course_laterale_nominale_mm": debord,
        "epaulement_nominal_mm": debord - jeu,
        "insertion_reelle": "NON VÉRIFIÉ",
        "limite": "Une déformation est nécessaire à l’insertion ; aucune force, tenue ou fatigue ne sont calculées.",
    }
    return male, femelle, garde, math.hypot(bord_x, demi_y + jeu), mesures


def _profil(type_, rayon):
    if type_ == "cheville-rond":
        return Point(0, 0).buffer(rayon, quad_segs=16)
    if type_ == "cheville-rectangle":
        return box(-rayon, -0.65 * rayon, rayon, 0.65 * rayon)
    if type_ == "cheville-d":
        return Point(0, 0).buffer(rayon, quad_segs=16).intersection(box(-0.3 * rayon, -2 * rayon, 2 * rayon, 2 * rayon))
    return Polygon(
        [(-rayon, rayon * 0.65), (rayon, rayon * 0.65), (0.6 * rayon, -rayon * 0.65), (-0.6 * rayon, -rayon * 0.65)]
    )


def _extrusion(polygone, longueur, facteur=0.85):
    points = np.asarray(polygone.exterior.coords)[:-1]
    if not polygone.exterior.is_ccw:
        points = points[::-1]
    section_ = mf.CrossSection([points.tolist()])
    return mf.Manifold.extrude(section_, longueur, scale_top=[facteur, facteur]).translate([0, 0, -longueur / 2])


def _position(parties, original, coupe, options, rayon, paroi):
    n = coupe.get("normale", [0, 0, 1])
    o = coupe.get(
        "origine", np.asarray(coupe.get("points", []), float).mean(axis=0) if coupe.get("points") else [0, 0, 0]
    )
    o, n = _plan({"origine": o, "normale": n})
    if "position" in options:
        point = np.asarray(options["position"], float)
        if point.shape != (3,) or not np.isfinite(point).all():
            raise ValueError("Position du connecteur : trois coordonnées finies")
    else:
        polygones, repere = section(original, o, n)
        zones = [p.buffer(-(rayon + paroi)) for p in polygones]
        zones = [p for p in zones if not p.is_empty]
        if not zones:
            raise ValueError("Section insuffisante pour ce diamètre, le jeu et la paroi demandés")
        xy = max(zones, key=lambda p: p.area).representative_point()
        point = trimesh.transform_points([[xy.x, xy.y, 0]], repere)[0]
    # Une courbe peut se trouver au-dessus ou au-dessous de son plan moyen.
    # Le point est replacé sur le vrai joint par une paire d'impacts communs.
    marge = max(float(np.linalg.norm(original.extents)) * 2, 10.0)
    impacts = []
    for nom in ("dessous", "dessus"):
        lieux, _, _ = parties[nom].ray.intersects_location([point - n * marge], [n])
        impacts.append(lieux)
    if not all(len(p) for p in impacts):
        raise ValueError("Le connecteur ne traverse pas les deux faces correspondantes")
    distances = np.linalg.norm(impacts[0][:, None, :] - impacts[1][None, :, :], axis=2)
    i, j = np.unravel_index(np.argmin(distances), distances.shape)
    if distances[i, j] > 1e-4:
        raise ValueError("Aucun joint commun à cette position ; replacer le connecteur")
    point = (impacts[0][i] + impacts[1][j]) / 2
    return point, n, np.vstack([np.c_[_repere(n), point], [0, 0, 0, 1]])


def appliquer(original, parties, coupe, options=None):
    """Ajoute un connecteur nominal à deux maillages complémentaires et rend leur contrôle.

    Les dimensions et jeux sont en mm ; sorties : corps, pièces séparées et
    rapport WARN ou FAIL (NON VÉRIFIÉ sans connecteur). Aucun montage réel n’est validé.
    """
    options = dict(options or {})
    type_ = options.get("type", "douille")
    if not isinstance(type_, str) or type_ not in CATALOGUE:
        raise ValueError("Connecteur inconnu ; utiliser uniquement le catalogue de découpe")
    clip = type_ in _CLIPS
    if clip and coupe.get("type") != "plan":
        raise ValueError("Les clips nominaux exigent une coupe plane.")
    if set(parties) != {"dessous", "dessus"}:
        raise ValueError("Deux faces complémentaires dessous/dessus exigées")
    if reassemblage(original, parties)["statut"] != "PASS":
        raise ValueError("Les pièces initiales ne réassemblent pas l'original")
    a, b = (solide(parties[k]) for k in ("dessous", "dessus"))
    source = solide(original)
    if type_ == "aucun":
        return (
            dict(parties),
            {},
            {
                "type": type_,
                "statut": "NON VÉRIFIÉ",
                "controles": [{"controle": "Maintien par collage ou pose", "statut": "NON VÉRIFIÉ"}],
                "reassemblage": reassemblage(original, parties),
                "validation_physique": False,
            },
        )
    jeu, provenance = politique_jeu(options)
    efforts = options.get("efforts", [])
    if not isinstance(efforts, list) or len(efforts) > 8:
        raise ValueError("Efforts : liste d'au plus huit directions")
    for effort in efforts:
        v = np.asarray(effort, float)
        if v.shape != (3,) or not np.isfinite(v).all() or np.linalg.norm(v) < 1e-9:
            raise ValueError("Chaque effort doit être une direction finie non nulle")
    dimensions = {}
    for cle, defaut in (("diametre", 6.0), ("longueur", 12.0), ("paroi_min", 1.2)):
        v = options.get(cle, defaut)
        if type(v) not in (int, float) or not math.isfinite(v) or not 0.2 <= v <= 500:
            raise ValueError(f"{cle} : nombre fini entre 0,2 et 500 mm")
        dimensions[cle] = float(v)
    r, longueur, paroi = dimensions["diametre"] / 2, dimensions["longueur"], dimensions["paroi_min"]
    outils_clip = _clip_nominal(type_, r, longueur, jeu, paroi) if clip else None
    rayon_position = outils_clip[3] if outils_clip else r + jeu / 0.85
    point, n, transformation = _position(parties, original, coupe, options, rayon_position, paroi)

    def place(outil):
        return outil.transform(transformation[:3])

    extras, tige = {}, None
    if outils_clip:
        male, femelle, garde, _, _ = outils_clip
        aa, bb = a + place(male), b - place(femelle)
    elif type_.startswith("cheville-"):
        profil = _profil(type_, r)
        male = _extrusion(profil, longueur)
        # Dilatation au bas puis conicité : le minimum au bout reste égal au jeu
        # demandé. La plage réelle [jeu, jeu/.85] est déclarée, pas dissimulée.
        femelle = _extrusion(profil.buffer(jeu / 0.85, join_style="mitre"), longueur + 2 * jeu)
        garde = _extrusion(profil.buffer(jeu / 0.85 + paroi, join_style="mitre"), longueur + 2 * paroi)
        aa, bb = a + place(male), b - place(femelle)
    elif type_ == "douille":
        male = _cylindre(r, longueur)
        femelle = _cylindre(r + jeu, longueur + 2 * jeu)
        garde = _cylindre(r + jeu + paroi, longueur + 2 * jeu + 2 * paroi)
        aa, bb = a - place(femelle), b - place(femelle)
        extras["goupille"] = place(male)
    elif type_ == "canal-tige":
        distance = max(float(np.linalg.norm(original.extents)) * 2, 10.0)
        impacts, _, _ = original.ray.intersects_location([point - n * distance], [n])
        if len(impacts) < 2:
            raise ValueError("Entrée et sortie du canal non établies sur son axe")
        hauteurs = (impacts - point) @ n
        zmin, zmax = hauteurs.min(), hauteurs.max()
        tige = {
            "diametre_mm": dimensions["diametre"],
            "longueur_a_couper_mm": float(zmax - zmin),
            "extremites_mm": [(point + n * zmin).tolist(), (point + n * zmax).tolist()],
            "statut": "NON VÉRIFIÉ : longueur nominale à contrôler avant débit de la tige",
            "mesure": "Première entrée et dernière sortie du maillage sur l'axe réel du canal",
        }
        outil = _cylindre(r + jeu, float(zmax - zmin + 2), float((zmin + zmax) / 2))
        # Ne pas masquer un étranglement éloigné du joint. Seules les deux
        # faces de sortie sont exclues numériquement, jamais un col entier.
        epsilon_sortie = min(max(1e-6, float(zmax - zmin) * 1e-8), float(zmax - zmin) / 4)
        longueur_garde = float(zmax - zmin) - 2 * epsilon_sortie
        garde = _cylindre(r + jeu + paroi, longueur_garde, float((zmin + zmax) / 2))
        tige["verification_paroi"] = {
            "longueur_analysee_mm": longueur_garde,
            "exclusion_faces_sortie_mm": epsilon_sortie,
            "rayon_garde_mm": r + jeu + paroi,
        }
        aa, bb = a - place(outil), b - place(outil)
    elif type_ == "rotule-simple":
        z = r * 1.2
        male = mf.Manifold.sphere(r, 48).translate([0, 0, z]) + _cylindre(
            r * 0.55, z + longueur / 2, (z - longueur / 2) / 2
        )
        femelle = mf.Manifold.sphere(r + jeu, 48).translate([0, 0, z]) + _cylindre(
            r * 0.55 + jeu, z + longueur / 2, (z - longueur / 2) / 2
        )
        garde = mf.Manifold.sphere(r + jeu + paroi, 48).translate([0, 0, z]) + _cylindre(
            r * 0.55 + jeu + paroi, longueur, 0
        )
        aa, bb = a + place(male), b - place(femelle)
    else:
        z = max(longueur / 2, 2 * r)
        male = _cylindre(r * 0.55, 2 * z)
        femelle = _cylindre(r * 0.55 + jeu, 2 * z)
        garde = _cylindre(r * 0.55 + jeu + paroi, 2 * z)
        for signe in (-1, 1):
            male += mf.Manifold.sphere(r, 48).translate([0, 0, signe * z])
            femelle += mf.Manifold.sphere(r + jeu, 48).translate([0, 0, signe * z])
            garde += mf.Manifold.sphere(r + jeu + paroi, 48).translate([0, 0, signe * z])
        aa, bb = a - place(femelle), b - place(femelle)
        extras["axe-double"] = place(male)
    resultats = {"dessous": _vers_trimesh(aa), "dessus": _vers_trimesh(bb)}
    separes = {nom: _vers_trimesh(m) for nom, m in extras.items()}
    tout = {**resultats, **separes}
    union = mf.Manifold.batch_boolean([solide(m) for m in tout.values()], mf.OpType.Add)
    ajoute, retire = float((union - source).volume()), float((source - union).volume())
    bilan = reassemblage(original, tout, volume_ajoute=ajoute, volume_retire=retire)
    manque_garde = float((place(garde) - source).volume())
    trou_ouvert = type_ == "canal-tige"
    controles = [
        {
            "controle": "Volumes fermés et valides",
            "statut": "PASS" if all(m.is_volume for m in tout.values()) else "FAIL",
        },
        {
            "controle": "Paroi et logement dans la matière",
            "statut": "FAIL" if manque_garde >= 1e-5 else "NON VÉRIFIÉ" if coupe.get("type") == "courbe" else "PASS",
            "volume_garde_hors_matiere_mm3": manque_garde,
            "paroi_min_mm": paroi,
            "portee": "Garde tubulaire sur toute la traversée ; seules les faces de sortie sont exclues"
            if type_ == "canal-tige"
            else "Enveloppe nominale dans l'original ; paroi résiduelle près d'une couture courbe à vérifier"
            if coupe.get("type") == "courbe"
            else "Enveloppe nominale de logement et fond autour du joint plan",
        },
        {"controle": "Interférences au réassemblage", "statut": bilan["statut"]},
        {
            "controle": "Jeu latéral de conception par face" if clip else "Jeu radial de conception",
            "statut": "PASS",
            "minimum_mm": jeu,
            "maximum_mm": jeu / 0.85 if type_.startswith("cheville-") else jeu,
        },
        {
            "controle": "Qualification du jeu",
            "statut": "WARN" if provenance["statut"] == "NON MESURÉ" else "PASS",
            "provenance": provenance,
        },
        {
            "controle": "Orientation du connecteur",
            "statut": "NON VÉRIFIÉ",
            "detail": "À relier à la pose d'export et aux directions d'efforts ; résistance physique inconnue",
        },
        {
            "controle": "Montage, tenue et usage réel U24",
            "statut": "NON VÉRIFIÉ",
            "detail": "Rotation/rétention/insertion à essayer"
            if type_.startswith("rotule")
            else "Ajustement et maintien à essayer",
        },
    ]
    if trou_ouvert:
        controles.append(
            {
                "controle": "Sorties de canal",
                "statut": "WARN",
                "detail": "Débouchantes par conception ; inspecter les bords et l'accès de la tige",
            }
        )
    rapport = {
        "type": type_,
        "libelle": CATALOGUE[type_],
        **dimensions,
        **({"jeu_lateral_par_face_mm": jeu, "clip": outils_clip[4]} if outils_clip else {"jeu_radial_mm": jeu}),
        "position_mm": point.tolist(),
        "axe": n.tolist(),
        "efforts": efforts,
        "tige": tige,
        "statut": "FAIL" if any(c["statut"] == "FAIL" for c in controles) else "WARN",
        "controles": controles,
        "reassemblage": bilan,
        "validation_physique": False,
    }
    return resultats, separes, rapport


def appliquer_joints(original, parties, coupe, options=None):
    """Un connecteur par îlot de section, pour les coupes de plusieurs branches."""
    options = dict(options or {})
    if options.get("type") == "aucun":
        return appliquer(original, parties, coupe, options)
    origine = coupe.get(
        "origine", np.asarray(coupe.get("points", []), float).mean(axis=0) if coupe.get("points") else [0, 0, 0]
    )
    o, n = _plan({"origine": origine, "normale": coupe.get("normale", [0, 0, 1])})
    polygones, vers3d = section(original, o, n)
    if len(polygones) <= 1:
        return appliquer(original, parties, coupe, options)
    if "position" in options:
        raise ValueError("Plusieurs joints : fournir la liste positions ou laisser le placement automatique")
    positions = options.pop("positions", None)
    if positions is not None and len(positions) != len(polygones):
        raise ValueError("Une position par îlot de joint est requise")
    jeu, _ = politique_jeu(options)
    marge = options.get("diametre", 6.0) / 2 + jeu / 0.85 + options.get("paroi_min", 1.2)
    polygones.sort(key=lambda p: (p.centroid.x, p.centroid.y))
    modifiees, extras, rapports = dict(parties), {}, []
    for i, poly in enumerate(polygones):
        if positions is None:
            zone = poly.buffer(-marge)
            if zone.is_empty:
                raise ValueError(f"Joint {i + 1} : section insuffisante pour connecteur, jeu et paroi")
            p = zone.representative_point()
            point = trimesh.transform_points([[p.x, p.y, 0]], vers3d)[0].tolist()
        else:
            point = positions[i]
        courant = _vers_trimesh(solide(modifiees["dessous"]) + solide(modifiees["dessus"]))
        modifiees, ajouts, rapport = appliquer(courant, modifiees, coupe, {**options, "position": point})
        extras.update({f"joint{i + 1}_{nom}": p for nom, p in ajouts.items()})
        rapports.append(rapport)
    tout = modifiees | extras
    source = solide(original)
    union = mf.Manifold.batch_boolean([solide(p) for p in tout.values()], mf.OpType.Add)
    bilan = reassemblage(
        original, tout, volume_ajoute=float((union - source).volume()), volume_retire=float((source - union).volume())
    )
    controles = [{**c, "joint": i + 1} for i, r in enumerate(rapports) for c in r["controles"]]
    return (
        modifiees,
        extras,
        {
            "type": options.get("type", "douille"),
            "nombre_joints": len(rapports),
            "joints": rapports,
            "controles": controles,
            "reassemblage": bilan,
            "statut": "FAIL" if bilan["statut"] == "FAIL" or any(c["statut"] == "FAIL" for c in controles) else "WARN",
            "validation_physique": False,
        },
    )
