"""Chemins communs : les copies de garde divergeaient sur les alias système et l'atelier choisi."""

import os
import tempfile
from pathlib import Path


def _absolu(chemin):
    p = Path(chemin).expanduser()
    return p if p.is_absolute() else Path.cwd() / p


def _alias_systeme(chemin):
    p = _absolu(chemin)
    # Seuls ces alias système attestés sont normalisés avant le contrôle ; aucun
    # lien de l'atelier n'est résolu ici (docs/reference/chemins-et-atelier.md).
    for alias, attendu in ((Path('/tmp'), Path('/private/tmp')), (Path('/var'), Path('/private/var'))):
        if p.is_relative_to(alias) and alias.is_symlink() and alias.resolve() == attendu:
            return attendu / p.relative_to(alias)
    return p


def racine_depot():
    """Renvoie la racine du checkout, sans unité ni écriture ; FAIL si le paquet n'est pas dans un dépôt."""
    racine = Path(__file__).resolve().parents[1]
    if not (racine / 'pyproject.toml').is_file() or not (racine / 'AGENTS.md').is_file():
        raise ValueError('Atelier explicite requis hors checkout : définir BENCHAB_PRIVE ou --prive.')
    return racine


def _racines_implicites():
    racines = [Path(__file__).resolve().parents[1], _alias_systeme('/tmp'), _alias_systeme(tempfile.gettempdir())]
    if os.environ.get('BENCHAB_PRIVE'):
        racines.append(_alias_systeme(os.environ['BENCHAB_PRIVE']))
    return racines


def sans_lien(chemin, *, racine=None, strict=False):
    """Contrôle les composants sous la racine de confiance ; chemin absolu, FAIL sur lien/échappée.

    Entrées chemins sans unité, sortie Path ; ne crée rien et ne suit aucun lien interne, même devant « .. ».
    Les parents système au-dessus de la racine explicite peuvent être des alias légitimes.
    """
    brut = _alias_systeme(chemin)
    if racine is None:
        confiance = next((r for r in _racines_implicites() if brut.is_relative_to(r)), Path(brut.anchor))
    else:
        confiance = _alias_systeme(racine)
    if not brut.is_relative_to(confiance):
        raise ValueError('Chemin hors racine de confiance : ' + str(brut))
    if confiance.is_symlink():
        raise ValueError('Lien symbolique refusé : ' + str(confiance))
    base = confiance.parent.resolve() / confiance.name if confiance != Path('/') else confiance
    courant = base
    for composant in brut.relative_to(confiance).parts:
        if composant == '..':
            if courant == base:
                raise ValueError('Chemin hors racine de confiance : ' + str(brut))
            courant = courant.parent
        else:
            courant = courant / composant
            if courant.is_symlink():
                raise ValueError('Lien symbolique refusé : ' + str(courant))
    if strict:
        return courant.resolve(strict=True)
    return courant


def atelier_prive(explicite=None):
    """Résout l'atelier choisi : argument, BENCHAB_PRIVE, dépôt/prive ; Path sans unité, aucune création.

    Un chemin explicite relatif est choisi par l'appelant ; le défaut ne dépend jamais du dossier courant.
    """
    if explicite is not None and not str(explicite).strip():
        raise ValueError('Le chemin explicite de l’atelier ne peut pas être vide.')
    choix = explicite if explicite is not None else os.environ.get('BENCHAB_PRIVE')
    return sans_lien(choix if choix else racine_depot() / 'prive')


def est_temporaire(chemin):
    """Indique si le chemin est sous un temporaire reconnu ; booléen sans unité, refuse les liens internes."""
    p = sans_lien(chemin)
    return any(p.is_relative_to(sans_lien(r)) for r in (_alias_systeme('/tmp'), _alias_systeme(tempfile.gettempdir())))


def est_temporaire_hors_depot(chemin):
    """Vérifie qu’un chemin est jetable et hors de la partie publique du checkout, sans écrire."""
    cible = sans_lien(chemin)
    try:
        racine = racine_depot()
    except ValueError:
        racine = None
    return est_temporaire(cible) and (racine is None or not cible.is_relative_to(racine))
