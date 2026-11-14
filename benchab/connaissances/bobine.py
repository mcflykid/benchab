"""Importe une fiche de bobine depuis les données documentées par l’assistant.

L’import ne consulte pas le réseau et ne modifie aucun profil machine.
Commande : benchab connaissances bobine importer DONNEES.json
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from benchab.qualite.ecritures import _sans_lien, publier_texte_repris, remplacer_texte, verrou_publication


def reference_fiche(donnees):
    """Rend la référence privée canonique (unités natives) ; aucun réglage adopté ni validation physique."""
    from benchab import reglages

    lignes = ["## Référence contrôlée — " + donnees["date_controle"], "",
              "Valeurs de profil enregistrées, pas une sélection active ni une calibration.", "",
              "| Paramètre | Valeur |", "|---|---|"]
    # La fiche cite les valeurs brutes, sans transformer leur type ni les adopter
    # pour le slicer (docs/reference/donnees.md, représentations générées).
    profil = reglages.Reglages(donnees["profil"])
    for cle, valeur in profil.items():
        citation = ", ".join(map(str, valeur)) if isinstance(valeur, list) else str(valeur)
        lignes.append(f"| `{cle}` | {citation} |")
    stock = donnees.get("stock")
    if stock:
        lignes += ["", f"**Stock** : {stock['etat']} le {stock['date']} ({stock['source']})"]
    lignes += ["", "**Fabricant / étiquette** : " + donnees["fabricant"], "",
               "**Retour et limite** : " + donnees["observation"], "",
               "Sources de profils (empreintes vérifiées par le contrôle automatique) :"]
    lignes.extend(f"- `{source['chemin']}` — `{source['sha256'][:12]}`" for source in donnees["sources_profils"])
    return "\n".join(lignes)


def texte_fiche(donnees):
    """Rend la fiche de recherche depuis SQLite, valeurs/unités citées ; aucun réglage adopté ni fichier écrit."""
    texte = ("# " + donnees["nom"] + "\n\nPossession : " + donnees["possession"]
             + "\n\n## Recommandations et provenance\n")
    for cle, source in donnees["sources_recherche"].items():
        texte += ("\n- " + cle + " : " + source["statut"] + " ; " + source.get("url", "") + " ; "
                  + source.get("date", "") + "\n  Valeurs de source (pas une calibration) : "
                  + json.dumps(source.get("valeurs", {}), ensure_ascii=False) + "\n")
    texte += ("\n## À compléter\nLot, étiquette, quantité restante, affectation ACE, état de buse, séchage, "
              "résultats d’essai. Aucune validation physique présumée.\n")
    return (texte + "\n<!-- AUTO:" + donnees["id"] + ":DEBUT -->\n" + reference_fiche(donnees)
            + "\n<!-- AUTO:" + donnees["id"] + ":FIN -->\n")


def importer(root, donnees):
    """Crée une fiche privée sourcée (unités fabricant), renvoie son chemin ou FAIL ; aucun réglage adopté."""
    root = _sans_lien(root)
    d = donnees
    for k in ("id", "marque"):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", d.get(k, "")):
            raise ValueError("identifiant ou marque invalide")
    if not d.get("possession") or not d.get("nom"):
        raise ValueError("identité et confirmation de possession requises")
    sources = d.get("sources", {})
    if set(sources) != {"fabricant", "orca", "3dfilamentprofiles"}:
        raise ValueError("trois sources requises, éventuellement NON VÉRIFIÉES")
    for s in sources.values():
        if s.get("statut") not in ("CONSULTÉ", "NON VÉRIFIÉ", "BLOQUÉ", "NON TROUVÉ"):
            raise ValueError("statut de source invalide")
        if s["statut"] == "CONSULTÉ" and (not s.get("url", "").startswith("https://") or not s.get("date")):
            raise ValueError("source consultée sans URL/date")
    from benchab.connaissances.sqlite import native
    from benchab.donnees.base import Base

    natif = native(root)
    p = _sans_lien(root / "donnees/filaments/references.json")
    rel = "docs/reference/filaments/" + d["marque"] + "/" + d["id"] + ".md"
    fiche = _sans_lien(root / rel)
    entry = {
        "id": d["id"],
        "nom": d["nom"],
        "fiche": rel,
        "date_controle": "identification à confirmer si incomplète",
        "type_profil": "non établi",
        "profil": {},
        "sources_profils": [],
        "fabricant": "Voir les recommandations sourcées ; aucun réglage adopté automatiquement.",
        "observation": "Aucun retour physique documenté.",
        "sources_recherche": sources,
        "possession": d["possession"],
        "preuves_empreintes": {},
    }
    entry["fiche_contrat"] = "recherche-sources-v1"
    matiere = d.get("matiere", "inconnue")
    empreinte = hashlib.sha256(
        json.dumps([matiere, entry], sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).hexdigest()
    dossier = root / "cache" if natif else p.parent
    with verrou_publication(dossier, "bobines.lock"):
        recu = _sans_lien(dossier / f"bobine-{d['id']}.en-cours")
        reprise = recu.exists()
        if reprise and recu.read_text(encoding="utf-8").strip() != empreinte:
            raise ValueError("Référence en cours avec des données différentes")
        if natif:
            base = Base(root, creer=False)
            try:
                _inscrire_native(base, entry, matiere, fiche, recu, reprise, empreinte)
            finally:
                base.fermer()
        else:
            _inscrire_historique(p, entry, fiche, recu, reprise, empreinte)
        # La fiche est une vue des données canoniques déjà persistées.
        # Voir docs/reference/donnees.md.
        publier_texte_repris(fiche, texte_fiche(entry))
        recu.unlink()
    return rel


def _inscrire_native(base, entry, matiere, fiche, recu, reprise, empreinte):
    with base.transaction() as connexion:
        connexion.execute("BEGIN IMMEDIATE")
        anciens = connexion.execute("SELECT id,nom,matiere,donnees_json FROM filaments").fetchall()
        double = next((r for r in anciens if r[0] == entry["id"] or r[1].casefold() == entry["nom"].casefold()), None)
        if double:
            if not reprise or double[0] != entry["id"] or double[2] != matiere or json.loads(double[3]) != entry:
                raise ValueError("référence déjà présente")
            return
        if fiche.exists():
            raise ValueError("référence déjà présente")
        if not reprise:
            remplacer_texte(recu, empreinte + "\n")
        connexion.execute("INSERT INTO filaments VALUES (?,?,?,?,?)", (
            entry["id"], entry["nom"], matiere, fiche.parent.name,
            json.dumps(entry, ensure_ascii=False, allow_nan=False),
        ))
        connexion.execute("INSERT INTO bobines VALUES (?,?,NULL,NULL,?)", (
            "bobine:" + entry["id"], entry["id"],
            json.dumps({"possession": entry["possession"], "quantites": "non mesurées"}, ensure_ascii=False),
        ))


def _inscrire_historique(p, entry, fiche, recu, reprise, empreinte):
    reg = json.loads(p.read_text(encoding="utf-8"))
    double = next((r for r in reg["filaments"] if r["id"] == entry["id"]
                   or r["nom"].casefold() == entry["nom"].casefold()), None)
    if double:
        if not reprise or double != entry:
            raise ValueError("référence déjà présente")
        return
    if fiche.exists():
        raise ValueError("référence déjà présente")
    if not reprise:
        remplacer_texte(recu, empreinte + "\n")
    reg["filaments"].append(entry)
    remplacer_texte(p, json.dumps(reg, indent=2, ensure_ascii=False) + "\n")


def main(argv=None, *, racine=None):
    """Importe une fiche JSON de bobine dans l’atelier privé. Renvoie 0 ou 2 si refus."""
    a = argparse.ArgumentParser(description=__doc__)
    a.add_argument("commande", choices=["importer"])
    a.add_argument("donnees", type=Path)
    args = a.parse_args(argv)
    try:
        print(importer(Path(racine or "prive"), json.loads(args.donnees.read_text())))
    except (ValueError, OSError) as e:
        print("REFUSÉ :", e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
