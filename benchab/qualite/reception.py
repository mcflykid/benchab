"""Frontière commune aux six réceptions UI, absente des anciens scripts autonomes."""

from pathlib import Path
from urllib.parse import unquote, urlsplit

from benchab.chemins import atelier_prive, est_temporaire, sans_lien


def dossier(sortie):
    """Valide un chemin privé/temporaire et crée sa sortie neuve ; renvoie Path, sans statut ni écrasement."""
    cible = sans_lien(sortie)
    prive = atelier_prive()
    if not cible.is_relative_to(prive) and not est_temporaire(cible):
        raise ValueError("La réception doit rester dans l'atelier privé ou un temporaire reconnu")
    cible.mkdir(parents=True, exist_ok=False)
    return cible


def url_locale(url):
    """Valide HTTP local ou fichier HTML privé/temporaire ; renvoie URL ou FAIL, sans requête ni validation physique."""
    # Les tests peuvent écrire une observation ; ils ciblent seulement une
    # fixture locale, jamais un service tiers (docs/guides/developpement.md).
    parsed = urlsplit(url)
    if parsed.scheme == "file":
        # Les paquets autonomes se contrôlent aussi sans serveur, tout en restant
        # dans une fixture explicite (docs/reference/controles.md).
        if parsed.netloc not in {"", "localhost"}:
            raise ValueError("Le fichier de réception doit être local, sans identifiants")
        fichier = sans_lien(Path(unquote(parsed.path)), strict=True)
        prive = atelier_prive()
        if (
            not fichier.is_file() or fichier.suffix.lower() not in {".html", ".htm"}
            or not (fichier.is_relative_to(prive) or est_temporaire(fichier))
        ):
            raise ValueError("Le fichier HTML de réception doit rester privé ou temporaire")
        return fichier.as_uri() + ("?" + parsed.query if parsed.query else "") + (
            "#" + parsed.fragment if parsed.fragment else ""
        )
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Une URL de fixture locale est requise")
    if parsed.username or parsed.password:
        raise ValueError("Identifiants dans une URL de réception interdits")
    return url
