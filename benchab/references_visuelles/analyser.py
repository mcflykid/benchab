"""Analyse locale de silhouettes, repères et cohérence multivue. Ne modifie aucune entrée."""

import argparse
import json
import math
from pathlib import Path

import numpy as np
from scipy import ndimage

from benchab.references_visuelles import references as _helpers

NON_VERIFIE = "NON VÉRIFIÉ"
AXES = {"face": ("X", "Z"), "profil": ("Y", "Z"), "dessus": ("X", "Y"), "dos": ("X", "Z")}


def nombre(value, name, positif=False):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or (value <= 0 if positif else value < 0)
    ):
        raise ValueError(name + " : nombre fini " + ("positif" if positif else "non négatif") + " requis")
    return value


def composantes(mask):
    """Composantes 8-connexes, ordre stable de balayage ; aucune séparation sémantique inventée."""
    labels, count = ndimage.label(mask, structure=np.ones((3, 3), dtype=int))
    result = []
    for identifiant, slices in enumerate(ndimage.find_objects(labels), 1):
        if slices is None:
            continue
        ys, xs = slices
        local_y, local_x = np.where(labels[ys, xs] == identifiant)
        result.append(
            {
                "id": identifiant,
                "aire_px": int(len(local_x)),
                "bbox_px": [xs.start, ys.start, xs.stop, ys.stop],
                "centroide_px": [float(local_x.mean() + xs.start), float(local_y.mean() + ys.start)],
            }
        )
    return labels, result


def ecart_relatif(a, b):
    return abs(a - b) / max(abs(a), abs(b))


def diagnostic_rendu(reference, rendu, tolerance):
    """Écarts image signés : x vers droite, y vers bas ; correction attendue = référence − rendu."""
    if reference.shape != rendu.shape:
        return {"statut": "FAIL", "raison": "dimensions image différentes ; aucun recalage automatique"}
    if not rendu.any():
        return {"statut": "FAIL", "raison": "silhouette du rendu vide"}
    br, cr = _helpers.caracteristiques(reference)
    bg, cg = _helpers.caracteristiques(rendu)
    largeur_r, hauteur_r = br[2] - br[0], br[3] - br[1]
    largeur_g, hauteur_g = bg[2] - bg[0], bg[3] - bg[1]
    dx, dy = cr - cg
    differences = {
        "dx_px": float(dx),
        "dy_px": float(dy),
        "largeur_delta_px": int(largeur_r - largeur_g),
        "hauteur_delta_px": int(hauteur_r - hauteur_g),
        "largeur_facteur": float(largeur_r / largeur_g),
        "hauteur_facteur": float(hauteur_r / hauteur_g),
    }
    erreurs = [
        abs(float(dx)) / largeur_r,
        abs(float(dy)) / hauteur_r,
        ecart_relatif(float(largeur_r), float(largeur_g)),
        ecart_relatif(float(hauteur_r), float(hauteur_g)),
    ]
    return {
        "statut": "PASS" if max(erreurs) <= tolerance else "FAIL",
        "correction_image_reference_moins_rendu": differences,
        "erreur_relative_max": max(erreurs),
        "limites": (
            "Diagnostic projeté uniquement. Corriger les paramètres géométriques concernés ; "
            "ne pas déplacer la caméra pour masquer un défaut. Aucune modification automatique."
        ),
    }


def analyser(specification, sortie=None):
    specification = Path(specification).resolve()
    spec = json.loads(specification.read_text(encoding="utf-8"))
    tolerance = nombre(spec.get("tolerance_relative"), "tolerance_relative")
    if tolerance >= 1:
        raise ValueError("tolerance_relative doit être inférieure à 1")
    vues = spec.get("vues")
    if not isinstance(vues, dict) or not vues:
        raise ValueError("vues : dictionnaire non vide requis")
    if set(vues) - set(AXES):
        raise ValueError("vues autorisées : face, profil, dessus, dos")
    result = {
        "schema": 1,
        "statut": "PASS",
        "spec_sha256": _helpers.empreinte(specification),
        "tolerance_relative": tolerance,
        "vues": {},
        "entrees_sha256": {str(specification): _helpers.empreinte(specification)},
        "ratios": {},
        "coherence_ratios": {"statut": NON_VERIFIE},
        "coherence_cotes_mm": {},
        "limites": (
            "Composantes de silhouette 2D, pas reconnaissance de pièces 3D. "
            "Aucun calibrage déduit d’une perspective, aucune preuve mécanique ni fabrication."
        ),
    }
    dimensions = {}
    for nom, vue in vues.items():
        image = (specification.parent / vue["image"]).resolve()
        mask = _helpers.masque(image, vue.get("mode"), vue.get("seuil_masque"))
        if not mask.any():
            raise ValueError("silhouette vide : " + nom)
        projection = vue.get("projection")
        if projection not in ("orthographique", "concept"):
            raise ValueError("projection explicite requise : " + nom)
        labels, components = composantes(mask)
        box, centroid = _helpers.caracteristiques(mask)
        largeur, hauteur = int(box[2] - box[0]), int(box[3] - box[1])
        ligne = {
            "statut": "PASS",
            "projection": projection,
            "image": str(image),
            "sha256": _helpers.empreinte(image),
            "taille_image_px": [mask.shape[1], mask.shape[0]],
            "bbox_px": box.tolist(),
            "centroide_px": centroid.tolist(),
            "largeur_px": largeur,
            "hauteur_px": hauteur,
            "composantes": components,
            "reperes": {},
            "parties": {},
        }
        result["vues"][nom] = ligne
        result["entrees_sha256"][str(image)] = ligne["sha256"]
        for champ in ("reperes", "parties"):
            mapping = vue.get(champ, {})
            if not isinstance(mapping, dict):
                raise ValueError(champ + " : dictionnaire nom → [x,y] requis")
            for label, position in mapping.items():
                if (
                    not isinstance(label, str)
                    or not label
                    or not isinstance(position, list)
                    or len(position) != 2
                    or any(type(v) is not int for v in position)
                ):
                    raise ValueError("repère/partie nommé avec coordonnées entières [x,y] requis")
                x, y = position
                valid = 0 <= x < mask.shape[1] and 0 <= y < mask.shape[0] and bool(mask[y, x])
                ligne[champ][label] = {
                    "position_px": position,
                    "statut": "PASS" if valid else "FAIL",
                    "composante_id": int(labels[y, x]) if valid else None,
                }
                if not valid:
                    ligne["statut"] = "FAIL"
        if "nombre_composantes_attendu" in vue:
            attendu = vue["nombre_composantes_attendu"]
            if type(attendu) is not int or attendu < 1:
                raise ValueError("nombre_composantes_attendu : entier positif requis")
            ligne["nombre_composantes_attendu"] = attendu
            if attendu != len(components):
                ligne["statut"] = "FAIL"
        scale = vue.get("mm_par_pixel")
        if scale is not None:
            nombre(scale, nom + ".mm_par_pixel", positif=True)
        if projection == "orthographique":
            dimensions[nom] = {AXES[nom][0]: largeur, AXES[nom][1]: hauteur}
            ratio_name = AXES[nom][0] + "/" + AXES[nom][1]
            result["ratios"][nom] = {"statut": "PASS", "ratio": ratio_name, "valeur": largeur / hauteur}
            if scale is not None:
                ligne["cotes_mm"] = {axe: pixels * scale for axe, pixels in dimensions[nom].items()}
                ligne["mm_par_pixel"] = scale
            else:
                ligne["cotes_mm"] = {"statut": NON_VERIFIE, "raison": "échelle mm_par_pixel absente"}
        else:
            result["ratios"][nom] = {
                "statut": NON_VERIFIE,
                "raison": "concept/perspective : proportions physiques non mesurables",
            }
            ligne["cotes_mm"] = {"statut": NON_VERIFIE, "raison": "échelle non exploitable sur une perspective"}
        if vue.get("rendu"):
            rendu = (specification.parent / vue["rendu"]).resolve()
            result["entrees_sha256"][str(rendu)] = _helpers.empreinte(rendu)
            if projection == "orthographique":
                ligne["diagnostic_rendu"] = diagnostic_rendu(
                    mask, _helpers.masque(rendu, vue["mode"], vue["seuil_masque"]), tolerance
                )
            else:
                ligne["diagnostic_rendu"] = {
                    "statut": NON_VERIFIE,
                    "raison": "concept : aucune correction métrique déduite",
                }
    manquantes = [nom for nom in ("face", "profil", "dessus") if nom not in dimensions]
    if manquantes:
        result["coherence_ratios"].update(raison="vues orthographiques manquantes", vues=manquantes)
    else:
        wh = result["ratios"]["face"]["valeur"]
        dh = result["ratios"]["profil"]["valeur"]
        wd = result["ratios"]["dessus"]["valeur"]
        attendu = wh / dh
        erreur = ecart_relatif(attendu, wd)
        result["coherence_ratios"] = {
            "statut": "PASS" if erreur <= tolerance else "FAIL",
            "W_sur_H": wh,
            "D_sur_H": dh,
            "W_sur_D": wd,
            "W_sur_D_attendu": attendu,
            "erreur_relative": erreur,
            "tolerance_relative": tolerance,
        }
    result["coherence_dos"] = {"statut": NON_VERIFIE, "raison": "face et dos orthographiques nécessaires"}
    if "face" in dimensions and "dos" in dimensions:
        erreur = ecart_relatif(result["ratios"]["face"]["valeur"], result["ratios"]["dos"]["valeur"])
        result["coherence_dos"] = {
            "statut": "PASS" if erreur <= tolerance else "FAIL",
            "erreur_relative": erreur,
            "tolerance_relative": tolerance,
        }
    for axe in ("X", "Y", "Z"):
        mesures = {nom: v["cotes_mm"][axe] for nom, v in result["vues"].items() if axe in v["cotes_mm"]}
        if len(mesures) < 2:
            result["coherence_cotes_mm"][axe] = {
                "statut": NON_VERIFIE,
                "raison": "deux vues calibrées nécessaires",
                "mesures": mesures,
            }
        else:
            erreur = ecart_relatif(min(mesures.values()), max(mesures.values()))
            result["coherence_cotes_mm"][axe] = {
                "statut": "PASS" if erreur <= tolerance else "FAIL",
                "mesures": mesures,
                "erreur_relative": erreur,
                "tolerance_relative": tolerance,
            }
    statuts = [v["statut"] for v in result["vues"].values()] + [result["coherence_ratios"]["statut"]]
    statuts += [v["statut"] for v in result["coherence_cotes_mm"].values() if v["statut"] != NON_VERIFIE]
    if result["coherence_dos"]["statut"] != NON_VERIFIE:
        statuts.append(result["coherence_dos"]["statut"])
    statuts += [v["diagnostic_rendu"]["statut"] for v in result["vues"].values() if "diagnostic_rendu" in v]
    result["statut"] = "FAIL" if "FAIL" in statuts else NON_VERIFIE if NON_VERIFIE in statuts else "PASS"
    result["portee"] = "cohérence des silhouettes ; voir séparément les cotes non vérifiées"
    if sortie is not None:
        _helpers.ecrire_json(sortie, result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("specification")
    parser.add_argument("--sortie", required=True)
    args = parser.parse_args(argv)
    try:
        rapport = analyser(args.specification, args.sortie)
        print(rapport["statut"])
        return {"PASS": 0, NON_VERIFIE: 1, "FAIL": 2}[rapport["statut"]]
    except (OSError, ValueError, TypeError, KeyError) as erreur:
        print("FAIL :", erreur)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
