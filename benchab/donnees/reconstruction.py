"""Reconstruit une base vide depuis un export complet vérifié, sans effacement."""

import hashlib
import json
from pathlib import Path

from benchab.donnees.base import TABLES
from benchab.qualite.ecritures import _sans_lien


def _json(texte):
    def refuser(valeur):
        raise ValueError("Nombre non fini dans l'export : " + valeur)

    return json.loads(texte, parse_constant=refuser)


def _ligne(donnees):
    return json.dumps(donnees, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"


def reconstruire(base, export):
    """Restaure les treize tables ; refuse une destination contenant déjà des lignes.

    Le manifeste et toutes les tables sont relus avant la transaction. Un export
    en cours de remplacement, une empreinte périmée ou un lien est refusé.
    Les fichiers des projets ne sont pas inclus dans l'export relationnel.
    """
    export = _sans_lien(Path(export).expanduser())
    if export == (base.prive / "export").resolve():
        raise ValueError("L'export source doit être distinct de la destination.")
    manifeste = _sans_lien(export / "manifest.json")
    if manifeste.stat().st_size > 100_000:
        raise ValueError("Manifeste trop volumineux.")
    original = manifeste.read_bytes()
    contrat = _json(original)
    if (
        not isinstance(contrat, dict)
        or contrat.get("schema") != base.connexion.execute("PRAGMA user_version").fetchone()[0]
        or contrat.get("tables") != list(TABLES)
        or not isinstance(contrat.get("sha256"), dict)
        or set(contrat["sha256"]) != set(TABLES)
    ):
        raise ValueError("Schéma ou liste de tables incompatible.")
    contenus = {}
    volume = 0
    for table in TABLES:
        fichier = _sans_lien(export / f"{table}.jsonl")
        if fichier.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("Table trop volumineuse : " + table)
        brut = fichier.read_bytes()
        volume += len(brut)
        if volume > 256 * 1024 * 1024:
            raise ValueError("Export trop volumineux pour une reconstruction en mémoire.")
        if hashlib.sha256(brut).hexdigest() != contrat["sha256"][table]:
            raise ValueError("Empreinte d'export incorrecte : " + table)
        colonnes = [row[1] for row in base.connexion.execute(f'PRAGMA table_info("{table}")')]
        lignes = [_json(ligne) for ligne in brut.decode("utf-8").splitlines()]
        if any(not isinstance(row, dict) or set(row) != set(colonnes) for row in lignes):
            raise ValueError("Colonnes d'export incorrectes : " + table)
        if "".join(_ligne(row) for row in lignes).encode() != brut:
            raise ValueError("Export non canonique : " + table)
        contenus[table] = (colonnes, lignes, brut)
    if manifeste.read_bytes() != original:
        raise ValueError("Le manifeste d'export a changé pendant sa lecture.")
    with base.transaction() as connexion:
        connexion.execute("BEGIN IMMEDIATE")
        if any(connexion.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone() for table in TABLES):
            raise ValueError("Reconstruction refusée : la base de destination n'est pas vide.")
        for table, (colonnes, lignes, brut) in contenus.items():
            champs = ",".join('"' + col + '"' for col in colonnes)
            marqueurs = ",".join("?" for _ in colonnes)
            connexion.executemany(
                f'INSERT INTO "{table}" ({champs}) VALUES ({marqueurs})',
                [tuple(row[col] for col in colonnes) for row in lignes],
            )
            relu = "".join(_ligne(dict(row)) for row in connexion.execute(f'SELECT * FROM "{table}" ORDER BY id'))
            if relu.encode() != brut:
                raise ValueError("Données reconstruites différentes de l'export : " + table)
    return {"statut": "PASS", "lignes": {table: len(contenus[table][1]) for table in TABLES}}
