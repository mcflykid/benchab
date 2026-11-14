"""Indices de dégagement autour des dépôts de support, sur une grille nominale."""

from contextlib import closing
from functools import lru_cache
from itertools import product
from math import ceil, isfinite
from pathlib import Path

import numpy as np
from scipy import ndimage

from benchab.archives_3mf import Archive3MF
from benchab.controles.contact_support import PIECE
from benchab.controles.gcode_interpreter import Interpreter
from benchab.gcode.lecture import iter_lignes_gcode_3mf, plaques_gcode_archive

_ROLES_SUPPORT = {"support", "support interface", "support transition"}
_AUXILIAIRES = {"wipe tower", "prime tower", "skirt", "brim", "custom"}
_MAX_CELLS = 2_000_000
_MAX_TRAVAIL = 3_000_000
_MAX_REGIONS = 12
_PAS_MM = 0.4
_RAYON_MM = 1.2
_VOISINS = ndimage.generate_binary_structure(3, 1)


def _exterieur(libres):
    """Marque les cellules accessibles depuis le bord de la grille."""
    graines = np.zeros_like(libres)
    for axe in range(3):
        bord = np.moveaxis(graines, axe, 0)
        bord[[0, -1]] = True
    return ndimage.binary_propagation(graines & libres, structure=_VOISINS, mask=libres)


@lru_cache(maxsize=6)
def _voisins(rayon):
    """Réutilise le voisinage borné des échantillons XY."""
    return np.asarray(list(product(range(-rayon, rayon + 1), repeat=2)))


def classifier(piece, support, *, pas_mm, rayon_mm):
    """Classe les composantes de support par dégagement nominal, selon le pas et le rayon en mm.

    ENCLAVE, ETROIT et OUVERT décrivent la connectivité de la grille. Aucun verdict
    sur le retrait physique ou la trajectoire du support entier n’en est déduit.
    """
    if (piece.ndim != 3 or piece.shape != support.shape or piece.size > _MAX_CELLS
            or min(piece.shape) < 3 or not isfinite(pas_mm) or not isfinite(rayon_mm)
            or not 0 < pas_mm <= rayon_mm <= 10):
        raise ValueError("Grille ou dégagement hors budget.")
    libres = ~np.asarray(piece, dtype=bool)
    support = np.asarray(support, dtype=bool) & libres
    ouvert = _exterieur(libres)
    # L’outil nominal doit passer dans les cellules libres jusqu’à la branche.
    distance = ndimage.distance_transform_edt(libres, sampling=pas_mm)
    large = _exterieur(libres & (distance >= rayon_mm))
    regions = []
    # Une branche ouverte à sa base peut avoir une portion dans une fente.
    for acces, masque in (("ENCLAVE", ~ouvert), ("ETROIT", ouvert & ~large), ("OUVERT", large)):
        etiquettes, _ = ndimage.label(support & masque, structure=_VOISINS)
        for i, boite in enumerate(ndimage.find_objects(etiquettes), 1):
            zone = etiquettes[boite] == i
            point = np.argwhere(zone)[0] + np.array([a.start for a in boite])
            regions.append({"acces": acces, "cellules": int(zone.sum()), "indice": point.tolist()})
    return regions


def _grille(lignes, verifier):
    """Projette les dépôts horizontaux dans des cellules de 0,4 mm, avec budgets bornés."""
    interprete = Interpreter(error_limit=12)
    cellules = [set(), set()]
    incomplets = 0
    travail = 0
    for i, ligne in enumerate(lignes):
        if i % 1024 == 0 and verifier is not None:
            verifier()
        m = interprete.feed_line(ligne)
        if not m or m["deposited_length"] <= 0:
            continue
        role = (m["type"] or "").strip().lower()
        if role in _AUXILIAIRES:
            continue
        a, b = m["deposition_start"], m["end"]
        largeur, hauteur = (interprete.metadata.get(c) for c in ("WIDTH", "HEIGHT"))
        if (role not in PIECE | _ROLES_SUPPORT or a is None or a[2] != b[2] or largeur is None
                or hauteur is None or not 0 < largeur <= 2 or not 0 < hauteur <= 1
                or any(abs(v) > 1000 for v in (*a, *b))):
            incomplets += 1
            continue
        debut, fin = np.asarray(a), np.asarray(b)
        pas = _PAS_MM
        n = max(1, ceil(m["deposited_length"] / (pas / 2)))
        rayon = ceil(largeur / (2 * pas))
        travail += (n + 1) * (2 * rayon + 1) ** 2
        if travail > _MAX_TRAVAIL:
            return None, "Budget de dépôts dépassé."
        sens = fin[:2] - debut[:2]
        cellule_z = range(int(np.floor((b[2] - hauteur) / pas)), int(np.floor((b[2] - 1e-8) / pas)) + 1)
        xy = np.unique(np.floor(np.linspace(debut[:2], fin[:2], n + 1) / pas).astype(int), axis=0)
        cases = np.unique((xy[:, None, :] + _voisins(rayon)[None, :, :]).reshape(-1, 2), axis=0)
        centres = (cases + 0.5) * pas
        delta = centres - debut[:2]
        t = np.clip(delta @ sens / (sens @ sens), 0, 1)
        retenues = cases[np.sum((delta - t[:, None] * sens) ** 2, axis=1) <= (largeur / 2) ** 2]
        if not len(retenues):
            incomplets += 1
        cellules[role in _ROLES_SUPPORT].update((int(x), int(y), z) for x, y in retenues for z in cellule_z)
    return cellules, "Dépôts partiellement couverts." if incomplets or interprete.errors else None


def analyser(source: str | Path, plaques, *, verifier=None):
    """Repère les supports enclavés ou étroits par plaque et en coordonnées mm.

    Le bilan reste NON VÉRIFIÉ pour le retrait physique. Sans donnée géométrique
    suffisante, aucun repère n’est présenté comme une preuve de dégagement.
    """
    resultats = []
    with Archive3MF(source) as archive:
        empreinte = archive.sha256()
        membres = dict(plaques_gcode_archive(archive))
        for plaque in plaques:
            ligne = {"plaque": plaque, "statut": "NON VÉRIFIÉ", "regions": []}
            resultats.append(ligne)
            if verifier is not None:
                verifier()
            membre = membres.get(plaque)
            if membre is None or archive.taille_membre(membre) > 8 * 1024 * 1024:
                ligne["raison"] = "G-code absent ou hors budget."
                continue
            with closing(iter_lignes_gcode_3mf(source, membre, archive=archive)) as lignes:
                cellules, raison = _grille(lignes, verifier)
            if cellules is None or not cellules[1]:
                ligne["raison"] = raison or "Aucun support représentable à cette résolution."
                continue
            points = [np.array(sorted(c), dtype=int).reshape(-1, 3) for c in cellules]
            if not len(points[0]):
                ligne["raison"] = "Aucun dépôt de pièce représentable à cette résolution."
                continue
            ensemble = np.concatenate(points)
            origine = ensemble.min(axis=0) - 5
            forme = ensemble.max(axis=0) - origine + 6
            if np.prod(forme, dtype=np.int64) > _MAX_CELLS:
                ligne["raison"] = "Grille hors budget."
                continue
            grille = [np.zeros(tuple(forme), dtype=bool) for _ in points]
            for g, p in zip(grille, points, strict=True):
                g[tuple((p - origine).T)] = True
            # Les contacts partagent parfois une cellule à cette résolution.
            if np.any(grille[0] & grille[1]):
                raison = "Dépôts partiellement couverts ou contacts confondus dans la grille."
            if verifier is not None:
                verifier()
            zones = classifier(*grille, pas_mm=_PAS_MM, rayon_mm=_RAYON_MM)
            if verifier is not None:
                verifier()
            ordre = {"ENCLAVE": 0, "ETROIT": 1, "OUVERT": 2}
            zones.sort(key=lambda z: (ordre[z["acces"]], -z["cellules"]))
            for zone in zones[:_MAX_REGIONS]:
                position = (origine + zone.pop("indice") + 0.5) * _PAS_MM
                zone["position_mm"] = position.round(3).tolist()
            ligne.update(
                regions=zones[:_MAX_REGIONS], regions_omises=max(0, len(zones) - _MAX_REGIONS),
                couverture="PARTIELLE" if raison else "ESTIMATION",
                raison=raison or "Dégagement local estimé autour des dépôts.",
            )
        archive.verifier_source(source)
    return {
        "statut": "NON VÉRIFIÉ", "source_sha256": empreinte,
        "pas_mm": _PAS_MM, "rayon_mm": _RAYON_MM, "plaques": resultats,
    }
