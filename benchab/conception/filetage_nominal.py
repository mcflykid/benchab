"""Maillage d’un profil triangulaire hélicoïdal à cotes déclarées, hors norme ISO."""

import argparse
import hashlib
import io
import json
import math
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import trimesh

from benchab.qualite.ecritures import destination_neuve, renommer_exclusif

_SECTIONS_ANGULAIRES = 64
_SECTIONS_PAR_PAS = 16
_MAX_TOURS = 64


def _cote(valeur, nom, minimum, maximum):
    """Valide une cote en mm avant toute allocation de maillage."""
    if type(valeur) not in (int, float) or not minimum <= valeur <= maximum:
        raise ValueError(f"{nom} : cote finie entre {minimum:g} et {maximum:g} mm requise")
    return float(valeur)


def generer(
    *,
    diametre_mm,
    longueur_mm,
    pas_mm,
    profondeur_radiale_mm,
    source_cotes,
    sens="droite",
    role="exterieur",
    jeu_radial_mm=None,
):
    """Génère un filetage triangulaire nominal ou son outil intérieur à soustraire.

    Diamètre de crête, longueur, pas par tour, profondeur radiale et jeu
    sont en mm. La source des cotes est déclarée ; aucun choix ISO n’est
    déduit. Le maillage est fermé sur Z = 0 et Z = longueur. Pour l’outil
    intérieur, un jeu radial doit être donné et augmente tous les rayons
    nominaux de la même valeur. PASS signifie seulement volume numérique
    fermé ; l’ajustement et la tenue réels restent NON VÉRIFIÉS. Aucune écriture.
    """
    diametre = _cote(diametre_mm, "Diamètre de crête", 0.2, 250)
    longueur = _cote(longueur_mm, "Longueur", 0.1, 500)
    pas = _cote(pas_mm, "Pas", 0.1, 50)
    profondeur = _cote(profondeur_radiale_mm, "profondeur radiale", 0.01, 125)
    if diametre / 2 - profondeur < 0.05:
        raise ValueError("La profondeur radiale doit conserver un rayon de fond d’au moins 0,05 mm")
    if (
        not isinstance(source_cotes, str)
        or not source_cotes.strip()
        or len(source_cotes) > 240
        or not source_cotes.isprintable()
    ):
        raise ValueError("Une source des cotes explicite sur une ligne de 240 caractères au plus est requise")
    if sens not in ("droite", "gauche"):
        raise ValueError("Sens droite ou gauche requis")
    if role not in ("exterieur", "outil-interieur"):
        raise ValueError("Rôle exterieur ou outil-interieur requis")
    if role == "outil-interieur":
        jeu = _cote(jeu_radial_mm, "Le jeu radial", 0, 2)
    else:
        if jeu_radial_mm is not None:
            raise ValueError("Le jeu radial se déclare sur l’outil intérieur seulement")
        jeu = 0.0

    tours = longueur / pas
    if not 1 <= tours <= _MAX_TOURS:
        raise ValueError("Le budget exige entre un et soixante-quatre tours de filetage")
    nb = _SECTIONS_ANGULAIRES
    axiales = math.ceil(tours * _SECTIONS_PAR_PAS)
    z = np.linspace(0.0, longueur, axiales + 1)
    theta = np.arange(nb) * math.tau / nb
    # Le profil est périodique : le dernier secteur se referme sur le premier.
    phase = np.mod(z[:, None] / pas - theta[None, :] / math.tau, 1.0)
    rayon_crete = diametre / 2 + jeu
    rayon = rayon_crete - 2 * profondeur * np.minimum(phase, 1.0 - phase)
    corps = np.stack((rayon * np.cos(theta), rayon * np.sin(theta), np.broadcast_to(z[:, None], rayon.shape)), axis=2)
    sommets = np.vstack((corps.reshape(-1, 3), (0.0, 0.0, 0.0), (0.0, 0.0, longueur)))
    bas, haut = len(sommets) - 2, len(sommets) - 1

    j = np.arange(nb)
    voisin = (j + 1) % nb
    a = np.arange(axiales)[:, None] * nb + j[None, :]
    b = np.arange(axiales)[:, None] * nb + voisin[None, :]
    faces = np.concatenate(
        (
            np.stack((a, b, b + nb), axis=2).reshape(-1, 3),
            np.stack((a, b + nb, a + nb), axis=2).reshape(-1, 3),
            np.column_stack((np.full(nb, bas), voisin, j)),
            np.column_stack((np.full(nb, haut), axiales * nb + j, axiales * nb + voisin)),
        )
    )
    if sens == "gauche":
        # Le miroir conserve aussi les diagonales des facettes, donc le volume.
        sommets[:, 1] *= -1
        faces = faces[:, [0, 2, 1]]
    maillage = trimesh.Trimesh(vertices=sommets, faces=faces, process=False)
    if not maillage.is_volume or maillage.body_count != 1:
        raise ValueError("Le maillage généré n’est pas un volume unique fermé")
    return maillage, {
        "statut": "PASS",
        "role": role,
        "sens": sens,
        "profil": "triangulaire nominal, une hélice, sans troncature ISO",
        "diametre_declare_mm": diametre,
        "longueur_mm": longueur,
        "pas_mm": pas,
        "profondeur_radiale_mm": profondeur,
        "rayon_crete_nominal_mm": rayon_crete,
        "rayon_fond_nominal_mm": rayon_crete - profondeur,
        "jeu_radial_mm": jeu if role == "outil-interieur" else None,
        "source_cotes": source_cotes,
        "tours": tours,
        "secteurs_angulaires": nb,
        "intervalles_axiaux": axiales,
        "volume_maillage_mm3": float(maillage.volume),
        "ajustement_reel": "NON VÉRIFIÉ",
        "limite": "Surface échantillonnée avec bouts plats, sans entrée de filet, résistance ou compensation mesurée.",
    }


def main(argv=None, *, prive=None):
    """Publie STL et rapport numérique dans un dossier neuf ; codes 0 ou 2, aucun tranchage."""
    parser = argparse.ArgumentParser(description="Génère un filetage triangulaire nominal à cotes déclarées.")
    for nom in ("diametre-mm", "longueur-mm", "pas-mm", "profondeur-radiale-mm"):
        parser.add_argument("--" + nom, type=float, required=True)
    parser.add_argument("--source-cotes", required=True)
    parser.add_argument("--sens", choices=("droite", "gauche"), default="droite")
    parser.add_argument("--role", choices=("exterieur", "outil-interieur"), default="exterieur")
    parser.add_argument("--jeu-radial-mm", type=float)
    parser.add_argument("--sortie", required=True, type=Path, help="Dossier privé neuf")
    args = vars(parser.parse_args(argv))
    try:
        cible = destination_neuve(args.pop("sortie"), prive=prive)
        maillage, rapport = generer(**args)
        stl = maillage.export(file_type="stl")
        relu = trimesh.load_mesh(io.BytesIO(stl), file_type="stl")
        if (
            not relu.is_volume
            or relu.body_count != 1
            or not math.isclose(float(relu.volume), rapport["volume_maillage_mm3"], rel_tol=1e-6)
        ):
            raise ValueError("Le STL relu ne conserve pas le volume nominal unique")
        rapport["stl_sha256"] = hashlib.sha256(stl).hexdigest()
        rapport["volume_stl_mm3"] = float(relu.volume)
        with TemporaryDirectory(prefix=".filetage-nominal-", dir=cible.parent) as temporaire:
            dossier = Path(temporaire)
            (dossier / "filetage.stl").write_bytes(stl)
            (dossier / "rapport.json").write_text(
                json.dumps(rapport, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            renommer_exclusif(dossier, cible)
        print(json.dumps({"statut": rapport["statut"], "rapport": str(cible / "rapport.json")}, ensure_ascii=False))
        return 0
    except (OSError, ValueError) as erreur:
        print(f"BENCHAB : filetage nominal refusé : {erreur}", file=sys.stderr)
        return 2
