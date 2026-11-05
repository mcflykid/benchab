"""Atelier synthétique actif/historique ; aucun fichier du dépôt modifié."""

import argparse
import json
from pathlib import Path
from zipfile import ZipFile

import trimesh

from benchab.visualisation.fixtures_inspection import creer as creer_inspection
from benchab.visualisation.fixtures_inspection import valider_sortie
from benchab.visualisation.visionneuse import Visionneuse, translation


def suivi(nom, titre, historique=False):
    return {
        "schema": 1,
        "nom": nom,
        "titre": titre,
        "type": "historique" if historique else "conception",
        "statut": "HISTORIQUE" if historique else "EN_PREPARATION",
        "physique": {
            "imprime": historique,
            "resultat": "reussie" if historique else "inconnu",
            "preuve": "suivi/journal/2026-11/retour-synthetique.md" if historique else None,
        },
        "references_imprimees": [],
        "travail_en_cours": {"fichiers": []},
        "evenements": [],
        "reproductibilite": "Source synthétique, aucune preuve physique réelle",
    }


def creer(sortie):
    """Produit les fixtures synthétiques en mm, sans données de projet."""
    sortie = valider_sortie(sortie)
    root = creer_inspection(sortie)
    p = root / "projets/inspection-ete"
    q = {"suivi": suivi("inspection-ete", "Inspection été — projet actif")}
    q["suivi"].update(preparation_courante="P02", preuves_utiles=["preuves/controle.json"])
    (p / "qualite.json").write_text(json.dumps(q, ensure_ascii=False), encoding="utf-8")
    (p / "preuves/visualisation").mkdir(parents=True)
    (p / "preuves/controle.json").write_text(
        json.dumps(
            {"statut": "WARNING", "limite": "Contre-exemple synthétique, pas une réparation imprimée"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    for ident, hauteur in (("P01", 10), ("P02", 12)):
        f = p / "preparations" / ident / "PREPARATION.json"
        f.parent.mkdir(parents=True)
        f.write_text(
            json.dumps({"id": ident, "statut": "candidate NON imprimée", "parametres": {"hauteur": hauteur}}),
            encoding="utf-8",
        )
    with ZipFile(p / "reference/envoi.gcode.3mf", "w") as z:
        z.write(p / "reference/envoi.gcode", "Metadata/plate_1.gcode")
    v = Visionneuse("Observations été — synthèse", source={"fixture": True})
    v.modele("Bloc", trimesh.creation.box((30, 20, 20)))
    v.piece("Gauche", "Bloc", matrice=translation(-22, 0, 10))
    v.piece("Droite", "Bloc", matrice=translation(22, 0, 10))
    v.ecrire(p / "preuves/visualisation/projet-3d.html")
    ancien = root / "projets/ancienne-lune"
    ancien.mkdir()
    q = {"suivi": suivi("ancienne-lune", "Lune ancienne — historique", True)}
    q["suivi"]["references_imprimees"] = [
        {"id": "ancienne", "preuve": "suivi/journal/2026-11/retour-synthetique.md", "fichiers": []}
    ]
    (ancien / "qualite.json").write_text(json.dumps(q, ensure_ascii=False), encoding="utf-8")
    journal = root / "suivi/journal/2026-11/retour-synthetique.md"
    journal.parent.mkdir(parents=True)
    journal.write_text(
        "# Déclaration humaine synthétique\nFixture seulement, aucune impression réelle.",
        encoding="utf-8",
    )
    data = root / "donnees/connaissances/registre.json"
    data.parent.mkdir(parents=True)
    data.write_text(
        json.dumps(
            {
                "connaissances": [
                    {
                        "id": "K-SYNTH",
                        "titre": "Base théorique et limites",
                        "domaine": "géométrie",
                        "conditions": "Source numérique connue",
                        "exclusions": "Ne valide pas le contact réel",
                        "niveau_preuve": "CALCUL_NUMERIQUE",
                        "validite": "actuelle",
                        "sources": [{"chemin": "suivi/journal/2026-11/retour-synthetique.md"}],
                    }
                ],
                "journaux": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return root


if __name__ == "__main__":
    a = argparse.ArgumentParser(description=__doc__)
    a.add_argument("--sortie", type=Path, required=True)
    print(creer(a.parse_args().sortie))
