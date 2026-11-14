"""Échange NPZ de géométrie indexée : sommets en mm et faces, sans fusion."""

from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile

import numpy as np
import trimesh

from benchab.archives_3mf import Archive3MF
from benchab.chemins import sans_lien
from benchab.qualite.ecritures import PublicationNeuve, destination_neuve

_MAX_LIGNES = 3_000_000
_MAX_MEMBRE = _MAX_LIGNES * 3 * 8 + 10_000
_SOMMETS = "vertices.npy"
_FACES = "faces.npy"


def _valider(sommets, faces):
    """Contrôle tailles, finitude et indices sans réordonner, fusionner ou réparer."""
    for tableau in (sommets, faces):
        if tableau.ndim != 2 or tableau.shape[1] != 3 or not 0 < len(tableau) <= _MAX_LIGNES:
            raise ValueError("Taille de tableau de maillage indexé non qualifiée.")
    if not np.issubdtype(sommets.dtype, np.floating) or sommets.dtype.itemsize > 8 or not np.isfinite(sommets).all():
        raise ValueError("Sommets en mm non numériques ou non finis.")
    if not np.issubdtype(faces.dtype, np.integer) or faces.min() < 0 or faces.max() >= len(sommets):
        raise ValueError("Faces avec indices absents ou invalides.")


def _tableau(archive, membre, attendu):
    """Lit un tableau NPY v1.0 de taille attestée, sans allocation issue de l’en-tête."""
    with BytesIO(archive.lire(membre)) as flux:
        if np.lib.format.read_magic(flux) != (1, 0):
            raise ValueError("Version NPY non qualifiée : version 1.0 requise.")
        forme, fortran, dtype = np.lib.format.read_array_header_1_0(flux)
        if dtype.str != attendu or fortran:
            raise ValueError("Type ou ordre de données NPY non qualifié.")
        if len(forme) != 2 or forme[1] != 3 or not 0 < forme[0] <= _MAX_LIGNES:
            raise ValueError("Taille NPY déclarée non qualifiée.")
        brut = flux.read()
    if len(brut) != forme[0] * 3 * dtype.itemsize:
        raise ValueError("Taille NPY réelle incompatible avec son en-tête.")
    return np.frombuffer(brut, dtype=dtype).reshape(forme)


def lire(fichier):
    """Rouvre un NPZ BENCHAB en mm et retourne un maillage sans traitement des indices.

    Le conteneur ZIP est borné à deux membres, chacun vérifié en taille et CRC.
    Les archives inconnues et indices invalides lèvent ValueError ; aucune écriture.
    """
    try:
        fichier = sans_lien(Path(fichier), strict=True)
        if fichier.stat().st_size > 2 * _MAX_MEMBRE + 4096:
            raise ValueError("Taille du conteneur NPZ hors budget.")
        # Le conteneur NPZ réutilise l’index ZIP partagé ; aucun réglage ni membre 3MF n’est lu.
        with Archive3MF(
            fichier, nombre_max_membres=2, taille_max_membre=_MAX_MEMBRE, taille_max_totale=2 * _MAX_MEMBRE,
        ) as archive:
            if set(archive.noms()) != {_SOMMETS, _FACES}:
                raise ValueError("NPZ de maillage : tableaux vertices et faces requis.")
            sommets = _tableau(archive, _SOMMETS, "<f8")
            faces = _tableau(archive, _FACES, "<i8")
    except (BadZipFile, EOFError, SyntaxError, OSError, RuntimeError) as erreur:
        raise ValueError("NPZ de maillage illisible.") from erreur
    _valider(sommets, faces)
    return trimesh.Trimesh(sommets, faces, process=False)


def sauvegarder(fichier, maillage, *, prive=None):
    """Écrit un NPZ neuf, sommets float64 en mm et indices int64 ; aucune fusion ni écrasement."""
    sommets, faces = np.asarray(maillage.vertices), np.asarray(maillage.faces)
    _valider(sommets, faces)
    fichier = destination_neuve(Path(fichier), prive=prive)
    with PublicationNeuve([], [fichier]) as publication:
        with publication.temporaire(fichier).open("wb") as flux:
            np.savez(flux, vertices=sommets.astype("<f8", copy=False), faces=faces.astype("<i8", copy=False))
        publication.publier()
    return fichier
