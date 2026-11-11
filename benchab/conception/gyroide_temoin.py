"""Construit un coupon gyroïde borné pour des comparaisons numériques (#87)."""

import hashlib
import json
from io import BytesIO
from math import ceil, cos, isclose, pi, prod, sin, sqrt
from pathlib import Path
from tempfile import TemporaryDirectory

import manifold3d as mf
import trimesh

from benchab.qualite.ecritures import destination_neuve, renommer_exclusif

_BUDGET_GRILLE = 750_000
_MAX_PROTECTIONS = 12


def _reel(valeur, minimum, maximum, nom):
    """Refuse les booléens, valeurs non finies et dimensions hors contrat."""
    if type(valeur) not in (int, float) or not minimum <= valeur <= maximum:
        raise ValueError(f"{nom} hors du domaine [{minimum}, {maximum}].")
    return float(valeur)


def _triplet(valeurs, minimum, maximum, nom):
    """Lit les trois coordonnées déclarées en mm, sans normalisation implicite."""
    if not isinstance(valeurs, (tuple, list)) or len(valeurs) != 3:
        raise ValueError(f"{nom} : trois valeurs explicites en mm attendues.")
    return tuple(_reel(v, minimum, maximum, nom) for v in valeurs)


def _protections(declarations, cotes):
    """Construit les boîtes protégées exactes dans l'enveloppe déclarée."""
    if not isinstance(declarations, (tuple, list)) or len(declarations) > _MAX_PROTECTIONS:
        raise ValueError("Liste de protections explicites trop longue ou invalide.")
    volumes, bornes = [], []
    for box in declarations:
        if not isinstance(box, (tuple, list)) or len(box) != 2:
            raise ValueError("Une protection exige deux coins opposés en mm.")
        a, b = (_triplet(coin, 0, 64, "protection") for coin in box)
        if any(not i < j <= d for i, j, d in zip(a, b, cotes, strict=True)):
            raise ValueError("Boîte de protection vide, inversée ou hors enveloppe.")
        tailles = tuple(j - i for i, j in zip(a, b, strict=True))
        if min(tailles) < 0.2:
            raise ValueError("Chaque côté d’une protection doit atteindre 0,2 mm.")
        volumes.append(mf.Manifold.cube(tailles).translate(a))
        bornes.append([list(a), list(b)])
    return volumes, bornes


def generer_coupon(dimensions_mm, paroi_mm, cellule_mm, seuil, pas_mm, *, protections=()):
    """Génère en mémoire un STL de témoin et son rapport de géométrie.

    Entrées : dimensions XYZ, épaisseur de peau, taille de cellule et pas
    nominal en mm, seuil du champ sans unité, et boîtes à protéger données
    par deux coins en mm. Sortie : octets STL et rapport PASS géométrique
    avec volumes en mm³ et tenue physique NON VÉRIFIÉE. Les domaines hors
    contrat ou un maillage incohérent lèvent ValueError ; aucun résultat
    partiel n'est rendu. Aucun réglage, fichier ni appareil n'est modifié.
    """
    cotes = _triplet(dimensions_mm, 4, 64, "dimensions")
    paroi = _reel(paroi_mm, 0.6, 8, "paroi")
    cellule = _reel(cellule_mm, 2, 24, "cellule")
    seuil = _reel(seuil, 0.30, 0.75, "seuil")
    pas = _reel(pas_mm, 0.15, 1, "résolution")
    interieur = tuple(d - 2 * paroi for d in cotes)
    if min(interieur) < cellule:
        raise ValueError("La zone disponible doit contenir au moins une cellule dans chaque direction.")
    if pas > min(cellule / 8, paroi / 2):
        raise ValueError("La résolution doit échantillonner la cellule et l’ancrage à la paroi.")

    # Évaluer avec du dégagement puis couper par un volume exact. L'enveloppe
    # de la grille implicite ne doit jamais délimiter directement la pièce.
    bas = paroi - 2 * pas
    haut = tuple(d - bas for d in cotes)
    cellules_grille = prod(ceil((d - bas) / pas) + 3 for d in haut)
    if cellules_grille > _BUDGET_GRILLE:
        raise ValueError("Le budget de grille du témoin serait dépassé.")
    protections_solides, protections_mm = _protections(protections, cotes)
    plein = mf.Manifold.cube(cotes)
    cavite = mf.Manifold.cube(interieur).translate((paroi,) * 3)
    peau = (plein - cavite) + mf.Manifold.batch_boolean(protections_solides, mf.OpType.Add)
    volume_plein = plein.volume()
    marge = max(1e-6, volume_plein * 1e-9)
    if volume_plein - peau.volume() <= marge:
        raise ValueError("Les protections occupent toute la zone disponible pour le gyroïde.")

    k = 2 * pi / cellule

    def champ(x, y, z):
        """Valeur positive dans la bande implicite, sans unité de distance."""
        a, b, c = k * x, k * y, k * z
        return seuil - abs(sin(a) * cos(b) + sin(b) * cos(c) + sin(c) * cos(a))

    # Le recouvrement d'un pas dans la peau écarte les contacts seulement tangents.
    ancrage = mf.Manifold.cube(tuple(d + 2 * pas for d in interieur)).translate((paroi - pas,) * 3)
    implicite = mf.Manifold.level_set(champ, (bas, bas, bas, *haut), pas)
    variante = peau + (implicite ^ ancrage)
    if variante.status() != mf.Error.NoError or variante.is_empty():
        raise ValueError("Le moteur n’a pas produit de solide gyroïde exploitable.")
    volume = variante.volume()
    if not peau.volume() + marge < volume < volume_plein - marge:
        raise ValueError("Le gyroïde n’ajoute pas une structure interne distincte dans la zone disponible.")
    perdue = sum((protection - variante).volume() for protection in protections_solides)
    debord = (variante - plein).volume()
    corps = sum(partie.volume() > marge for partie in variante.decompose())
    if abs(perdue) > marge or abs(debord) > marge or corps != 1:
        raise ValueError("Protection, confinement ou connexité géométrique non établis.")

    maillage = variante.to_mesh()
    surface = trimesh.Trimesh(maillage.vert_properties[:, :3], maillage.tri_verts, process=True)
    stl = surface.export(file_type="stl")
    relu = trimesh.load_mesh(BytesIO(stl), file_type="stl")
    if not relu.is_volume or not relu.is_watertight or not isclose(relu.volume, volume, rel_tol=1e-6):
        raise ValueError("La relecture du STL n’établit pas un solide fermé et un volume cohérent.")

    return stl, {
        "statut": "PASS",
        "tenue_physique": "NON VÉRIFIÉ",
        "dimensions_mm": list(cotes),
        "paroi_mm": paroi,
        "protections_mm": protections_mm,
        "cellule_mm": cellule,
        "pas_nominal_mm": pas,
        "seuil_sans_unite": seuil,
        # La norme du gradient est bornée par 2√3 k dans le champ continu.
        # Ce diamètre vaut autour de la surface médiane hors découpe, pas sur le STL.
        "diametre_boule_garantie_champ_infini_mm": seuil / (sqrt(3) * k),
        "volume_mm3": volume,
        "volume_peau_et_protections_mm3": peau.volume(),
        "volume_plein_mm3": volume_plein,
        "zone_protegee_perdue_mm3": perdue,
        "debord_mm3": debord,
        "corps_positifs": corps,
        "triangles_stl": len(relu.faces),
        "stl_sha256": hashlib.sha256(stl).hexdigest(),
    }


def publier_coupon(sortie, dimensions_mm, paroi_mm, cellule_mm, seuil, pas_mm, *, protections=(), prive=None):
    """Publie le STL et son rapport ensemble dans un dossier privé neuf.

    Entrées : destination, atelier privé et mêmes cotes en mm que
    generer_coupon. Sortie : résumé PASS géométrique, tenue NON VÉRIFIÉE
    et chemin du rapport JSON. Une erreur refuse le lot sans écraser
    d'ancienne sortie. Aucune base, configuration ou imprimante n'est ouverte.
    """
    cible = destination_neuve(sortie, prive=prive, temporaire=False)
    stl, rapport = generer_coupon(dimensions_mm, paroi_mm, cellule_mm, seuil, pas_mm, protections=protections)
    with TemporaryDirectory(prefix=".gyroide-temoin-", dir=cible.parent) as temporaire:
        dossier = Path(temporaire)
        (dossier / "gyroide.stl").write_bytes(stl)
        (dossier / "rapport.json").write_text(
            json.dumps(rapport, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        # Comme pour les fiches CAO, publier le dossier complet sans collision.
        renommer_exclusif(dossier, destination_neuve(cible, prive=prive, temporaire=False))
    return {"statut": rapport["statut"], "tenue_physique": rapport["tenue_physique"],
            "rapport": str(cible / "rapport.json")}
