"""Codes publics des verdicts numériques, décrits dans docs/reference/controles.md."""

_CODES = {
    "PASS": 0,
    "WARN": 1,
    "WARNING": 1,
    "FAIL": 2,
    "NON VÉRIFIÉ": 2,
    "INCONNU": 2,
    "BLOQUE": 2,
    "ATTENTE HUMAINE": 2,
}


def code_statut(statut: str) -> int:
    """Convertit un verdict en code de sortie sans unité, sans changer son statut ni sa portée."""
    return _CODES[statut]
