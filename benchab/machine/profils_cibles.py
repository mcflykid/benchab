"""Catalogue filtré sur la machine et la buse configurées, sans modifier aucun profil."""

import argparse
import hashlib
import json
from pathlib import Path

from benchab import reglages as schema_reglages
from benchab.machine.configuration import charger
from benchab.machine.profil_maitre import resoudre_preset


def cible():
    machine = charger()["machine"]
    return set(machine["modeles_slicer"]), float(machine["buse_mm"])


def selectionner(valeurs, categorie, machines):
    """Filtre les profils lus via le dictionnaire (buse en mm) ; renvoie booléen/raison, sans changer de réglage."""
    valeurs = schema_reglages.Reglages(valeurs)
    modeles, buse = cible()
    if valeurs.get("instantiation") in ("false", False):
        return False, "parent abstrait"
    if valeurs.get("printer_model") is not None and valeurs["printer_model"] not in modeles:
        return False, "autre modèle"
    if categorie == "machine":
        diametres = valeurs.get("nozzle_diameter")
        try:
            exact = isinstance(diametres, list) and len(diametres) == 1 and float(diametres[0]) == buse
        except (TypeError, ValueError):
            exact = False
        ok = valeurs.get("printer_model") in modeles and exact
        return ok, "modèle et buse exacts" if ok else "modèle ou buse absent/différent"
    compat = valeurs.get("compatible_printers")
    if not isinstance(compat, list) or not compat:
        return False, "compatibilité explicite absente"
    ok = bool(set(x for x in compat if isinstance(x, str)) & set(machines))
    return ok, "compatible avec la S1/0,4 déclarée" if ok else "aucune machine cible explicitement compatible"


def inventorier(source):
    """Inventorie les profils explicites ; rapport de compatibilité, unités mm, sans écriture ni validation physique."""
    source = Path(source).resolve(strict=True)
    rows = []
    acceptes = []
    for categorie in ("machine", "process", "filament"):
        folder = source / categorie
        if not folder.is_dir():
            raise ValueError("Dossier catégorie absent : " + categorie)
        for p in sorted(folder.glob("*.json")):
            # Résolution bornée au dossier fourni : pas de compte ou de réseau.
            raw = schema_reglages.decoder(p.read_text())
            if (
                not isinstance(raw, dict)
                or raw.get("type", categorie) != categorie
                or raw.get("name", p.stem) != p.stem
            ):
                rows.append(
                    {
                        "categorie": categorie,
                        "nom": p.stem,
                        "retenu": False,
                        "raison": "définition de modèle ou identité hors catégorie preset",
                        "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                    }
                )
                continue
            result = resoudre_preset(p.stem, categorie, [folder])
            ok, why = selectionner(result["valeurs"], categorie, acceptes)
            if result["manquants"]:
                ok = False
                why = "héritage incomplet"
            if ok and categorie == "machine":
                acceptes.append(result["valeurs"]["name"])
            rows.append(
                {
                    "categorie": categorie,
                    "nom": p.stem,
                    "retenu": ok,
                    "raison": why,
                    "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                    "heritages": result["heritages"],
                    "manquants": result["manquants"],
                }
            )
    return {
        "modele": charger()["machine"]["nom"],
        "buse_mm": cible()[1],
        "profils": rows,
        "machines": acceptes,
        "retenus": sum(x["retenu"] for x in rows),
        "archives_conservees": True,
        "limites": [
            "Compatibilité déclarée, pas qualification du matériau, de la buse laiton ou validation physique.",
            "Profil sans compatibilité explicite exclu, à examiner manuellement.",
        ],
        "profils_modifies": False,
        "reseau": False,
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", required=True, type=Path)
    p.add_argument("--json", type=Path)
    a = p.parse_args(argv)
    try:
        report = inventorier(a.source)
        if a.json:
            with a.json.open("x") as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
                f.write("\n")
        print(
            json.dumps(
                {
                    "examines": len(report["profils"]),
                    "retenus": report["retenus"],
                    "machines": report["machines"],
                    "profils_modifies": False,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except (OSError, ValueError, KeyError) as e:
        print(str(e))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
