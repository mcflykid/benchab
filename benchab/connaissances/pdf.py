"""Extrait le texte d’un PDF privé, avec pages et empreinte, sans OCR ni rendu."""

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path

from benchab.chemins import sans_lien
from benchab.qualite.statuts import code_statut

_MAX_OCTETS = 16 * 1024 * 1024
_MAX_PAGES = 200
_MAX_TEXTE_PAGE = 200_000
_MAX_TEXTE_TOTAL = 1_000_000


def _lecteur_optionnel():
    """Charge pypdf uniquement pour cette capacité ; renvoie son API et sa version."""
    try:
        import pypdf
    except ImportError as erreur:
        message = "Lecteur PDF absent ; installer l’extra optionnel avec `uv sync --locked --extra pdf`."
        raise RuntimeError(message) from erreur
    return pypdf


def _pages_demandee(pages, nombre):
    """Valide les numéros de page à partir de 1 ; renvoie leur ordre sans doublons."""
    selection = list(range(1, nombre + 1)) if pages is None else list(pages)
    if not selection or any(type(numero) is not int or not 1 <= numero <= nombre for numero in selection):
        raise ValueError(f"Numéro de page hors limites ; le document contient {nombre} page(s).")
    if len(selection) != len(set(selection)):
        raise ValueError("Numéro de page répété ; chaque page demandée doit être unique.")
    return selection


def _empreinte_fichier(stream, attendu):
    """Calcule le SHA-256 par blocs, sous la taille annoncée, puis restaure le curseur."""
    stream.seek(0)
    empreinte = hashlib.sha256()
    total = 0
    while bloc := stream.read(1024 * 1024):
        total += len(bloc)
        if total > attendu:
            raise ValueError("Le PDF a grandi au-delà de sa taille validée pendant la lecture.")
        empreinte.update(bloc)
    stream.seek(0)
    if total != attendu:
        raise ValueError("Le PDF a changé de taille pendant la lecture.")
    return empreinte.hexdigest()


def analyser(source, pages=None, *, max_octets=_MAX_OCTETS, max_pages=_MAX_PAGES):
    """Extrait les pages PDF choisies ; octets/caractères bornés, PASS/NON VÉRIFIÉ, source intacte."""
    if type(max_octets) is not int or max_octets <= 0 or type(max_pages) is not int or max_pages <= 0:
        raise ValueError("Les limites de taille en octets et de pages doivent être des entiers positifs.")
    chemin = sans_lien(Path(source), strict=True)
    lecteur_pdf = _lecteur_optionnel()
    try:
        descripteur = os.open(chemin, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as erreur:
        if chemin.is_symlink():
            raise ValueError("Lien symbolique PDF refusé.") from erreur
        raise
    with os.fdopen(descripteur, "rb") as flux:
        avant = os.fstat(flux.fileno())
        if not stat.S_ISREG(avant.st_mode):
            raise ValueError("Le PDF doit être un fichier ordinaire, pas un lien ou un périphérique.")
        if avant.st_size > max_octets:
            raise ValueError(f"PDF trop volumineux : {avant.st_size} octets, limite {max_octets} octets.")
        try:
            lecteur = lecteur_pdf.PdfReader(flux, strict=True)
        except Exception as erreur:
            raise ValueError(f"PDF illisible ou structure invalide ({type(erreur).__name__}).") from erreur
        if lecteur.is_encrypted:
            raise ValueError("PDF chiffré refusé ; aucun mot de passe n’est demandé ni mémorisé.")
        nombre_pages = len(lecteur.pages)
        if nombre_pages == 0:
            raise ValueError("PDF vide : aucune page disponible.")
        if nombre_pages > max_pages:
            raise ValueError(f"PDF trop long : {nombre_pages} pages, limite {max_pages} pages.")
        numeros = _pages_demandee(pages, nombre_pages)
        resultats = []
        caracteres_total = 0
        for numero in numeros:
            page = lecteur.pages[numero - 1]
            try:
                texte = page.extract_text() or ""
            except Exception as erreur:
                raise ValueError(f"Extraction PDF impossible à la page {numero} ({type(erreur).__name__}).") from erreur
            if len(texte) > _MAX_TEXTE_PAGE:
                raise ValueError(
                    f"Page {numero} trop volumineuse : {len(texte)} caractères, limite {_MAX_TEXTE_PAGE}."
                )
            caracteres_total += len(texte)
            if caracteres_total > _MAX_TEXTE_TOTAL:
                raise ValueError(f"Texte extrait trop volumineux : limite {_MAX_TEXTE_TOTAL} caractères.")
            page_sans_texte = not texte.strip()
            resultats.append({
                "numero": numero,
                "statut": "NON VÉRIFIÉ" if page_sans_texte else "PASS",
                "texte": texte,
                **({
                    "limite": "Aucune couche texte détectée ; la page peut être une image. OCR requis mais non exécuté."
                } if page_sans_texte else {}),
            })
        signature_avant = (avant.st_dev, avant.st_ino, avant.st_size, avant.st_mtime_ns, avant.st_ctime_ns)
        empreinte = _empreinte_fichier(flux, avant.st_size)
        final = os.fstat(flux.fileno())
        signature_finale = (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns, final.st_ctime_ns)
        if signature_avant != signature_finale:
            raise ValueError("Le PDF a été modifié pendant la lecture.")
        try:
            chemin_apres = os.stat(chemin, follow_symlinks=False)
        except OSError as erreur:
            raise ValueError("Le chemin du PDF a changé pendant la lecture.") from erreur
        if not stat.S_ISREG(chemin_apres.st_mode) or not os.path.samestat(final, chemin_apres):
            raise ValueError("Le chemin du PDF ne désigne plus le fichier contrôlé.")
        non_verifie = any(page["statut"] == "NON VÉRIFIÉ" for page in resultats)
        statut = "NON VÉRIFIÉ" if non_verifie else "PASS"
        return {
            "schema": 1,
            "statut": statut,
            "code": code_statut(statut),
            "source": {"chemin": str(chemin.resolve()), "sha256": empreinte, "taille_octets": avant.st_size},
            "lecteur": {"nom": "pypdf", "version": lecteur_pdf.__version__},
            "pages": resultats,
            "restitution": {"mode": "texte", "format": "JSON stdout avec un numéro par page"},
            "ocr": {"effectue": False, "limite": "Aucun OCR n'est lancé."},
            "rendu": {"effectue": False, "limite": "Aucune image de page n'est rendue."},
            "limites": {
                "taille_max_octets": max_octets,
                "pages_max": max_pages,
                "caracteres_max_page": _MAX_TEXTE_PAGE,
                "caracteres_max_total": _MAX_TEXTE_TOTAL,
            },
            "validation_physique": False,
            "modifie_source": False,
        }


def main(argv=None, *, racine=None):
    """Commande de lecture PDF ; JSON page/provenance, code 0 PASS ou 2 NON VÉRIFIÉ, lecture seule."""
    del racine  # Le rapport reste sur stdout ; aucun document privé n’est enregistré.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Guide PDF privé à lire sans modification")
    parser.add_argument(
        "--page", type=int, action="append", help="Numéro de page à extraire, à partir de 1 ; répétable"
    )
    arguments = parser.parse_args(argv)
    try:
        rapport = analyser(arguments.source, pages=arguments.page)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as erreur:
        rapport = {
            "schema": 1,
            "statut": "NON VÉRIFIÉ",
            "code": code_statut("NON VÉRIFIÉ"),
            "erreur": str(erreur),
            "restitution": {"mode": "texte", "format": "JSON stdout avec un numéro par page"},
            "ocr": {"effectue": False, "limite": "Aucun OCR n'est lancé."},
            "rendu": {"effectue": False, "limite": "Aucune image de page n'est rendue."},
            "modifie_source": False,
        }
    print(json.dumps(rapport, ensure_ascii=False, indent=2, allow_nan=False))
    return rapport["code"]


if __name__ == "__main__":
    raise SystemExit(main())
