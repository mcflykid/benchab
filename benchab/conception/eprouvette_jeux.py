"""Génère un témoin géométrique pour mesurer les jeux d'ajustement après impression."""

import argparse
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import trimesh

from benchab.archives_3mf import Archive3MF
from benchab.chemins import atelier_prive, est_temporaire, sans_lien
from benchab.fabrication import anycubic
from benchab.fabrication.trancher import trancher_fichier
from benchab.qualite.ecritures import PublicationNeuve, valider_chemins

JEUX = [0.2, 0.3, 0.35, 0.4, 0.5, 0.6]
NOMINAL, PAS, EP = 10.0, 16.0, 4.0
_BOOLEENS = {"engine": "manifold"}


def _autoriser_sortie(path):
    """Valide une sortie privée ou temporaire; chemin sans unité, FAIL sur lien interne."""
    path = sans_lien(path)
    prive = atelier_prive()
    if not path.is_relative_to(prive) and not est_temporaire(path):
        raise ValueError("Sortie autorisée uniquement dans le dossier privé BENCHAB ou /private/tmp")
    return path


def plaque(nominal=NOMINAL, borgne=False):
    """Construit une plaque percée, les cotes sont nominales sans compensation."""
    pas = max(PAS, nominal + 6)
    epaisseur = 6.0 if borgne else EP
    profondeur = 4.5 if borgne else 3 * epaisseur
    largeur = 2 * (pas / 2 + 2)
    longueur = 8 + pas * len(JEUX)
    corps = trimesh.creation.box(extents=(longueur, largeur, epaisseur))
    corps.apply_translation((longueur / 2, largeur / 2, epaisseur / 2))
    coin = trimesh.creation.box(extents=(8, 8, 3 * EP))
    coin.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 4, (0, 0, 1)))
    outils, cotes = [coin], []
    for index, jeu in enumerate(JEUX):
        x = 4 + pas / 2 + pas * index
        cote = nominal + 2 * jeu
        z = epaisseur - profondeur / 2 if borgne else epaisseur / 2
        rond = trimesh.creation.cylinder(radius=cote / 2, height=profondeur, sections=128)
        rond.apply_translation((x, largeur / 4, z))
        carre = trimesh.creation.box(extents=(cote, cote, profondeur))
        carre.apply_translation((x, 3 * largeur / 4, z))
        outils.extend((rond, carre))
        cotes.extend(
            (
                {"forme": "rond", "jeu": jeu, "cote_mm": round(cote, 2), "x": x},
                {"forme": "carre", "jeu": jeu, "cote_mm": round(cote, 2), "x": x},
            )
        )
    return trimesh.boolean.difference([corps, *outils], **_BOOLEENS), cotes


def pion(forme, nominal=NOMINAL):
    """Construit un pion rond ou carré debout sur sa collerette."""
    if forme not in ("rond", "carre"):
        raise ValueError("Forme de pion attendue : rond ou carre")
    collerette = trimesh.creation.cylinder(radius=nominal / 2 + 4, height=2, sections=96)
    collerette.apply_translation((0, 0, 1))
    if forme == "rond":
        corps = trimesh.creation.cylinder(radius=nominal / 2, height=12, sections=128)
    else:
        corps = trimesh.creation.box(extents=(nominal, nominal, 12))
    corps.apply_translation((0, 0, 8))
    return trimesh.boolean.union([collerette, corps], **_BOOLEENS)


def generer(sortie, nominal=NOMINAL, borgne=False):
    """Publie la plaque, quatre pions et un rapport dans un nouveau dossier privé."""
    out = _autoriser_sortie(sortie)
    noms = ["plaque", "pion-rond-1", "pion-rond-2", "pion-carre-1", "pion-carre-2"]
    destinations = [out / f"{name}.stl" for name in noms] + [out / "eprouvette-jeux.json"]
    if any(path.exists() or path.is_symlink() for path in destinations):
        raise FileExistsError("sortie existante : choisir un nouveau dossier")
    valider_chemins([], destinations)
    mesh_plaque, cotes = plaque(nominal, borgne)
    pieces = {"plaque": mesh_plaque}
    for index, forme in enumerate(("rond", "rond", "carre", "carre")):
        pieces[f"pion-{forme}-{index % 2 + 1}"] = pion(forme, nominal)
    for nom, mesh in pieces.items():
        if not (mesh.is_watertight and mesh.is_volume):
            raise ValueError(f"{nom} : maillage non fermé")
    rapport = {
        "jeux_par_cote_mm": JEUX,
        "nominal_mm": nominal,
        "trous": "borgnes de 4,5 mm, plaque de 6 mm" if borgne else "traversants",
        "cotes": cotes,
        "volumes_cm3": {nom: round(mesh.volume / 1000, 2) for nom, mesh in pieces.items()},
        "limite": "cotes nominales sans compensation ; résultat valable pour une bobine, une orientation et une couche",
    }
    with PublicationNeuve([], destinations) as publication:
        for nom, mesh in pieces.items():
            mesh.export(publication.temporaire(out / f"{nom}.stl"))
        publication.temporaire(out / "eprouvette-jeux.json").write_text(
            json.dumps(rapport, indent=1, ensure_ascii=False, allow_nan=False), encoding="utf-8"
        )
        publication.publier()
    return pieces, rapport


def trancher(sortie, base_3mf, filament=1, nominal=NOMINAL, borgne=False):
    """Prépare et tranche le témoin avec l'unique lanceur BENCHAB, en usage témoin. Réglages en unités
    natives (mm, s, °C, %) lus via le dictionnaire typé ; aucun réglage inconnu transformé. Le résultat
    conserve le profil explicite comme provenance du fichier source neuf (docs/reference/integrite-3mf.md).
    """
    out = _autoriser_sortie(sortie)
    with Archive3MF(base_3mf) as archive:
        # Lire le profil exact avant de créer le témoin ou de lancer le moteur (#122).
        settings = archive.reglages()
        archive.verifier_source(base_3mf)
    if not isinstance(filament, int) or isinstance(filament, bool) or filament < 1:
        raise ValueError("Numéro de filament positif requis")
    pieces, rapport = generer(out, nominal, borgne)
    reglages = {"sparse_infill_density": "15%", "wall_loops": "3", "enable_support": "0"}
    items = [
        {
            "name": nom,
            "mesh": mesh,
            "w": float(mesh.extents[0]) + 6,
            "h": float(mesh.extents[1]) + 6,
            "extruder": filament,
            "settings": reglages,
        }
        for nom, mesh in pieces.items()
    ]
    anycubic.ranger(items, 0, 1, 10.0, cli=True)
    source_3mf = out / "eprouvette-jeux-plaques.3mf"
    # Le profil fourni déclare les réglages du témoin; le nom final garde leur provenance traçable.
    source = anycubic.ecrire_3mf(
        [{"name": "Jeux", "items": items}],
        settings,
        source_3mf,
        cli=True,
        canonique=True,
        nom_source=source_3mf.name,
    )
    tranchage, execution = trancher_fichier(
        source,
        out,
        "eprouvette-jeux-tranche.3mf",
        usage="temoin",
    )
    resultat = anycubic.resultats(tranchage)[0]
    profils = settings.get("filament_settings_id", [])
    nom_filament = profils[filament - 1] if isinstance(profils, list) and len(profils) >= filament else filament
    rapport.update(
        filament=nom_filament,
        grammes=round(resultat["grammes"], 2),
        minutes=round(resultat["secondes"] / 60, 1),
        supports_slicer=resultat["supports"],
        reglages=reglages,
        tranche=tranchage.name,
        source_3mf=source.name,
        tranchage_s=execution["duree_s"],
    )
    from benchab.qualite.ecritures import modifier_json

    modifier_json(out / "eprouvette-jeux.json", lambda actuel: (actuel.clear(), actuel.update(rapport)))
    return rapport


def main(argv=None):
    """Lance génération ou tranchage vers une sortie privée/temporaire; ne modifie pas l'atelier."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="commande", required=True)
    generer_parser = subparsers.add_parser("generer")
    generer_parser.add_argument(
        "--sortie", type=Path, default=atelier_prive() / "temoins/eprouvette-jeux"
    )
    trancher_parser = subparsers.add_parser("trancher")
    trancher_parser.add_argument(
        "--sortie", type=Path, default=atelier_prive() / "temoins/eprouvette-jeux"
    )
    trancher_parser.add_argument("--base", type=Path, required=True)
    trancher_parser.add_argument("--filament", type=int, default=1)
    trancher_parser.add_argument("--nominal", type=float, default=NOMINAL)
    trancher_parser.add_argument("--borgne", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.commande == "generer":
            _, rapport = generer(args.sortie)
        else:
            rapport = trancher(args.sortie, args.base, args.filament, args.nominal, args.borgne)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, RuntimeError) as error:
        print(f"Éprouvette refusée : {error}", file=sys.stderr)
        return 2
    print(json.dumps(rapport, indent=1, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
