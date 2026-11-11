"""Outils géométriques de logements nominaux, sans cote ISO ni calibration implicite."""

import math

import trimesh

_FORMES = {"lamage", "insert_cylindrique", "ecrou_hexagonal", "fraisage"}
_SECTIONS_RONDES = 96


def _dimension(valeur, nom):
    """Valide une cote nominale en mm dans le domaine du maillage."""
    if type(valeur) not in (int, float) or not 0.001 <= valeur <= 10_000:
        raise ValueError(f"{nom} doit être entre 0,001 et 10 000 mm")
    return float(valeur)


def _cylindre(rayon, hauteur, centre, *, sections, angle=0.0):
    """Crée un outil orienté en XY depuis la face d’entrée z=0, en mm."""
    outil = trimesh.creation.cylinder(radius=rayon, height=hauteur, sections=sections)
    if angle:
        outil.apply_transform(trimesh.transformations.rotation_matrix(math.radians(angle), (0, 0, 1)))
    outil.apply_translation((*centre, hauteur / 2))
    return outil


def generer_outil(
    forme: str,
    *,
    largeur_mm: float,
    profondeur_mm: float,
    bloc_xyz_mm: tuple[float, float, float],
    centre_xy_mm: tuple[float, float],
    marge_min_mm: float,
    source_cotes: str,
    passage_mm: float | None = None,
    orientation_deg: float = 0.0,
) -> tuple[trimesh.Trimesh, dict]:
    """Génère un volume à soustraire et les marges nominales dans un bloc rectangulaire.

    Toutes les cotes sont en mm, l’angle dans le plan XY est en degrés.
    Le bloc va de l’origine aux dimensions XYZ ; l’entrée du logement est à z=0.
    La largeur est un diamètre pour les formes rondes et une cote sur plats pour
    l’écrou. L’insert est borgne ; les autres formes exigent un passage traversant.
    Le fraisage relie son diamètre d’entrée à celui du passage à la profondeur
    déclarée ; son angle inclus en degrés est calculé, sans référence ISO.
    PASS concerne uniquement la géométrie, NON VÉRIFIÉ l’ajustement physique.
    Les cotes déclarées ne sont ni normalisées ni compensées. Aucune écriture.
    """
    if not isinstance(forme, str) or forme not in _FORMES:
        raise ValueError("Forme de fixation non prise en charge")
    if not isinstance(source_cotes, str) or not source_cotes.strip():
        raise ValueError("Source des cotes déclarées requise")
    largeur = _dimension(largeur_mm, "Largeur")
    profondeur = _dimension(profondeur_mm, "Profondeur")
    marge_min = _dimension(marge_min_mm, "Marge minimale")
    if not isinstance(bloc_xyz_mm, (list, tuple)) or len(bloc_xyz_mm) != 3:
        raise ValueError("Dimensions XYZ du bloc requises")
    bloc = tuple(_dimension(x, "Dimension du bloc") for x in bloc_xyz_mm)
    if not isinstance(centre_xy_mm, (list, tuple)) or len(centre_xy_mm) != 2 or any(
        type(x) not in (int, float) or not 0 <= x <= cote
        for x, cote in zip(centre_xy_mm, bloc)
    ):
        raise ValueError("Centre XY fini à l’intérieur du bloc requis")
    centre = tuple(map(float, centre_xy_mm))
    if type(orientation_deg) not in (int, float) or not 0 <= orientation_deg < 360:
        raise ValueError("Orientation XY attendue entre 0 inclus et 360 degrés exclus")
    hexagonal = forme == "ecrou_hexagonal"
    conique = forme == "fraisage"
    if not hexagonal and orientation_deg != 0:
        raise ValueError("L’orientation s’applique seulement à l’écrou hexagonal")
    if forme == "insert_cylindrique":
        if passage_mm is not None:
            raise ValueError("Un logement d’insert borgne ne définit pas de passage")
        passage = None
    else:
        passage = _dimension(passage_mm, "Diamètre du passage")
        if passage >= largeur:
            raise ValueError("Le passage doit rester plus étroit que le logement")

    # Sur plats = deux apothèmes ; les six sommets donnent les extrema XY exacts.
    rayon = largeur / (2 * math.cos(math.pi / 6)) if hexagonal else largeur / 2
    if hexagonal:
        angles = [math.radians(orientation_deg) + i * math.tau / 6 for i in range(6)]
        demi_x = max(abs(math.cos(angle)) * rayon for angle in angles)
        demi_y = max(abs(math.sin(angle)) * rayon for angle in angles)
    else:
        demi_x = demi_y = rayon
    marges = {
        "x_min": centre[0] - demi_x,
        "x_max": bloc[0] - centre[0] - demi_x,
        "y_min": centre[1] - demi_y,
        "y_max": bloc[1] - centre[1] - demi_y,
        "fond_hors_passage": bloc[2] - profondeur,
    }
    if passage is not None:
        marges["marge_annulaire_entree" if conique else "epaulement"] = (largeur - passage) / 2
    insuffisantes = [f"{nom}={valeur:.6g} mm" for nom, valeur in marges.items() if valeur < marge_min]
    if insuffisantes:
        raise ValueError(f"Marges sous {marge_min:g} mm : " + ", ".join(insuffisantes))

    angle_inclus = None
    if conique and passage is not None:
        rayon_passage = passage / 2
        angle_inclus = 2 * math.degrees(math.atan2(rayon - rayon_passage, profondeur))
        # Un profil de révolution commun évite deux surfaces jointives au col.
        profil = [(0, 0), (rayon, 0), (rayon_passage, profondeur), (rayon_passage, bloc[2]), (0, bloc[2])]
        outil = trimesh.creation.revolve(profil, sections=_SECTIONS_RONDES)
        outil.apply_translation((*centre, 0))
    else:
        outil = _cylindre(
            rayon, profondeur, centre, sections=6 if hexagonal else _SECTIONS_RONDES, angle=orientation_deg,
        )
        if passage is not None:
            foret = _cylindre(passage / 2, bloc[2], centre, sections=_SECTIONS_RONDES)
            outil = trimesh.boolean.union([outil, foret], engine="manifold")
    if not outil.is_volume or outil.body_count != 1:
        raise ValueError("L’outil n’est pas un volume fermé et connexe")
    return outil, {
        "statut": "PASS",
        "forme": forme,
        "source_cotes": source_cotes,
        "bloc_xyz_mm": list(bloc),
        "centre_xy_mm": list(centre),
        "convention_largeur": "sur plats" if hexagonal else "diamètre nominal",
        "largeur_mm": largeur,
        "profondeur_mm": profondeur,
        "passage_mm": passage,
        "orientation_deg": float(orientation_deg),
        **({"angle_inclus_deg": angle_inclus} if angle_inclus is not None else {}),
        "marge_min_mm": marge_min,
        "marges_mm": marges,
        "validation_physique": "NON VÉRIFIÉ",
        "limite": "Outil à soustraire dans le bloc déclaré ; aucune cote de visserie ni compensation appliquée.",
    }
