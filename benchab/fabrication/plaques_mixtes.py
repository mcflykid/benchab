"""Prépare des plaques regroupant des pièces compatibles de projets privés."""

import argparse
import json
import math
import re
import zipfile
from pathlib import Path

import trimesh

from benchab import reglages as schema_reglages
from benchab.archives_3mf import Archive3MF
from benchab.fabrication import anycubic
from benchab.fabrication.planning import plaques_pretes
from benchab.projets.protections import protections
from benchab.qualite.empreintes import sha256_fichier

FILAMENT = (
    "filament_settings_id",
    "nozzle_temperature",
    "nozzle_temperature_initial_layer",
    "textured_plate_temp",
    "layer_height",
    "filament_flow_ratio",
)
PAR_OBJET = (
    "wall_loops",
    "sparse_infill_density",
    "sparse_infill_pattern",
    "top_shell_layers",
    "bottom_shell_layers",
    "enable_support",
    "support_type",
    "support_style",
    "support_on_build_plate_only",
    "support_threshold_angle",
    "brim_type",
    "brim_width",
    "seam_position",
    "ironing_type",
    "reduce_crossing_wall",
    "outer_wall_speed",
    "inner_wall_speed",
    "sparse_infill_speed",
    "slow_down_layer_time",
)
_ID_PREPARATION = re.compile(r"P[0-9]+\Z")


def duree_positive(valeur):
    """Renvoie une durée positive et finie, sans interpréter bool comme nombre."""
    if isinstance(valeur, bool):
        return None
    try:
        nombre = float(valeur)
    except (TypeError, ValueError, OverflowError):
        return None
    return nombre if math.isfinite(nombre) and nombre > 0 else None


def _fichier_prive(prive: Path, chemin: str | Path) -> Path:
    brut = Path(chemin).expanduser()
    if brut.is_absolute():
        cible = brut
    else:
        cible = prive / brut
    if any(element.is_symlink() for element in (cible, *cible.parents)):
        raise ValueError("Lien symbolique dans un chemin privé refusé.")
    resolu = cible.resolve(strict=True)
    if not resolu.is_relative_to(prive) or not resolu.is_file():
        raise ValueError("Fichier source ordinaire sous base.prive requis.")
    return resolu


def _preparation_courante(projet: Path) -> Path | None:
    dossier = projet / "preparations"
    if not dossier.is_dir() or dossier.is_symlink():
        return None
    fiches = []
    for fiche in dossier.glob("P*/PREPARATION.json"):
        identifiant = fiche.parent.name
        if _ID_PREPARATION.fullmatch(identifiant) is None or fiche.parent.is_symlink() or fiche.is_symlink():
            continue
        fiches.append((int(identifiant[1:]), fiche))
    return max(fiches, key=lambda item: item[0])[1] if fiches else None


def pieces_projet(prive: str | Path, nom: str) -> tuple[dict | None, str | None]:
    """Charge la dernière préparation privée et filtre les pièces protégées. Réglages en unités natives
    (mm, s, °C, %) lus via le dictionnaire typé ; aucun réglage inconnu transformé."""
    racine = Path(prive).expanduser().resolve(strict=True)
    if Path(nom).name != nom or nom in (".", ".."):
        raise ValueError("Nom de projet invalide.")
    projet = racine / "projets" / nom
    if projet.is_symlink() or not projet.is_dir():
        return None, "projet absent"
    fiche_path = _preparation_courante(projet)
    if fiche_path is None:
        return None, "aucune préparation"
    fiche_path = _fichier_prive(racine, fiche_path)
    fiche = json.loads(fiche_path.read_text(encoding="utf-8"))
    if not isinstance(fiche, dict):
        raise ValueError("Fiche de préparation invalide.")
    if fiche.get("envoi"):
        return None, f"{fiche.get('id', fiche_path.parent.name)} déjà envoyée"
    plaques_path = _fichier_prive(racine, fiche["plaques_3mf"]["fichier"])
    tranche_path = _fichier_prive(racine, fiche["tranche_3mf"]["fichier"])
    with Archive3MF(plaques_path) as archive:
        membre = "Metadata/project_settings.config"
        noms = archive.noms()
        if membre not in noms:
            raise ValueError("Archive de réglages absente, dupliquée ou corrompue.")
        reglages = archive.reglages(membre)
        # U36 conserve la preuve du paquet complet ; chaque membre ne consomme son budget qu’une fois.
        archive.verifier(membres=(nom for nom in noms if nom != membre))
        archive.verifier_source(plaques_path)
    placement_path = _fichier_prive(racine, projet / "preuves/placement.json")
    placement = json.loads(placement_path.read_text(encoding="utf-8"))
    if not isinstance(placement, dict) or not isinstance(placement.get("objects"), list):
        raise ValueError("Placement projet invalide.")
    estimations = anycubic.resultats(tranche_path)
    if any((projet / nom).is_symlink() for nom in ("qualite.json", "LIVRABLES.json", "VERROU-REIMPRESSION.json")):
        raise ValueError("Fichier de protection lié symboliquement refusé.")
    proteges = protections(projet)
    chemins_proteges = set(proteges)
    empreintes_protegees = {
        empreinte
        for empreinte in proteges.values()
        if isinstance(empreinte, str) and re.fullmatch(r"[0-9a-f]{64}", empreinte)
    }
    qualite_path = projet / "qualite.json"
    if qualite_path.is_file() and not qualite_path.is_symlink():
        qualite = json.loads(qualite_path.read_text(encoding="utf-8"))
        chemins_proteges.update(qualite.get("protegees", {}))
    pieces = []
    exclues = {}
    for objet in placement["objects"]:
        if not isinstance(objet, dict) or not isinstance(objet.get("name"), str):
            raise ValueError("Objet de placement invalide.")
        modele = objet.get("model", objet["name"])
        if not isinstance(modele, str) or Path(modele).name != modele:
            raise ValueError("Nom de modèle invalide.")
        chemin_maillage = projet / "candidats" / f"{modele}.stl"
        relatif = chemin_maillage.relative_to(projet).as_posix()
        if objet["name"] in chemins_proteges or modele in chemins_proteges or relatif in chemins_proteges:
            exclues[objet["name"]] = "pièce protégée : référence figée"
            continue
        maillage_path = _fichier_prive(racine, chemin_maillage)
        if empreintes_protegees and sha256_fichier(maillage_path) in empreintes_protegees:
            exclues[objet["name"]] = "pièce protégée : empreinte d’une référence figée"
            continue
        maillage = trimesh.load_mesh(maillage_path)
        if not isinstance(maillage, trimesh.Trimesh) or maillage.is_empty:
            raise ValueError(f"Maillage invalide : {objet['name']}.")
        maillage.apply_translation(-maillage.bounds[0])
        pieces.append(
            {
                "projet": nom,
                "name": f"{nom}:{objet['name']}",
                "mesh": maillage,
                "plaque": int(objet["plate"]),
                "reglages": objet.get("reglages") or {},
            }
        )
    for plaque in {piece["plaque"] for piece in pieces}:
        groupe = [piece for piece in pieces if piece["plaque"] == plaque]
        volumes = [abs(float(piece["mesh"].volume)) if piece["mesh"].is_volume else None for piece in groupe]
        volume_total = sum(volume for volume in volumes if volume is not None)
        resultat_plaque = [item for item in estimations if item["index"] == plaque]
        secondes = duree_positive(resultat_plaque[0].get("secondes")) if len(resultat_plaque) == 1 else None
        for piece, volume in zip(groupe, volumes):
            piece["heures"] = (
                secondes / 3600 * volume / volume_total
                if secondes is not None
                and volume is not None
                and volume_total > 0
                and all(v is not None for v in volumes)
                else None
            )
    return {
        "preparation": fiche.get("id", fiche_path.parent.name),
        "reglages": reglages,
        "pieces": pieces,
        "pieces_protegees": exclues,
    }, None


def _liste(arguments: list[str], cle: str) -> list[str]:
    if cle not in arguments:
        return []
    position = arguments.index(cle) + 1
    resultat = []
    while position < len(arguments) and not arguments[position].startswith("--"):
        resultat.append(arguments[position])
        position += 1
    return resultat


def main(argv: list[str] | None = None, *, prive: str | Path) -> int:
    """Génère des plaques privées ; seuil en h et codes PASS/WARN/FAIL.

    Réglages inchangés ; export géométrique sans profil explicite déclaré NON VÉRIFIÉ.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projets", nargs="*")
    parser.add_argument("--ajouter", nargs="*")
    parser.add_argument("--duree-max", default="3")
    parser.add_argument("--sortie", type=Path)
    parser.add_argument("--trancher", action="store_true")
    arguments = parser.parse_args(argv)
    racine = Path(prive).expanduser().resolve(strict=True)
    maximum = duree_positive(arguments.duree_max)
    if maximum is None:
        parser.error("--duree-max : nombre fini strictement positif requis")
    noms = arguments.projets or sorted({plaque["projet"] for plaque in plaques_pretes(racine / "projets")})
    if not noms:
        print("aucun projet utilisable", {"projets": "aucune préparation candidate"})
        return 1
    rapport = {"exclus": {}, "groupes": []}
    projets = {}
    try:
        for nom in noms:
            projet, erreur = pieces_projet(racine, nom)
            if erreur:
                rapport["exclus"][nom] = erreur
            else:
                projets[nom] = projet
                rapport["exclus"].update(
                    {f"{nom}:{piece}": raison for piece, raison in projet["pieces_protegees"].items()}
                )
        if not projets:
            print("aucun projet utilisable", rapport["exclus"])
            return 1

        def cle(reglages):
            return tuple(str(schema_reglages.valeur_unique(reglages, champ)) for champ in FILAMENT)

        groupes = {}
        for nom, projet in projets.items():
            groupes.setdefault(cle(projet["reglages"]), []).append(nom)
        base_nom = noms[0] if noms[0] in projets else next(iter(projets))
        reglages_base = projets[base_nom]["reglages"]
        groupes_compatibles = groupes[cle(reglages_base)]
        for cle_groupe, noms_groupe in groupes.items():
            if cle_groupe != cle(reglages_base):
                rapport["exclus"].update(
                    {nom: f"filament ou couche différents ({dict(zip(FILAMENT, cle_groupe))})" for nom in noms_groupe}
                )
        pieces = []
        for nom in groupes_compatibles:
            reglages_projet = projets[nom]["reglages"]
            differences = {
                champ: str(schema_reglages.valeur_unique(reglages_projet, champ))
                for champ in PAR_OBJET
                if champ in reglages_projet
                and str(schema_reglages.valeur_unique(reglages_projet, champ))
                != str(schema_reglages.valeur_unique(reglages_base, champ))
            }
            for piece in projets[nom]["pieces"]:
                pieces.append(
                    dict(piece, settings={**differences, **{k: str(v) for k, v in piece["reglages"].items()}})
                )

        if arguments.ajouter:
            chemins_proteges, empreintes_protegees = _protections_globales(racine)
            for chemin_brut in arguments.ajouter:
                chemin = _fichier_prive(racine, chemin_brut)
                if chemin in chemins_proteges or sha256_fichier(chemin) in empreintes_protegees:
                    rapport["exclus"][f"ajout:{chemin.name}"] = "pièce protégée : référence figée, ajout refusé"
                    continue
                maillage = trimesh.load_mesh(chemin)
                if not isinstance(maillage, trimesh.Trimesh) or maillage.is_empty:
                    raise ValueError(f"Maillage invalide : {chemin.name}.")
                maillage.apply_translation(-maillage.bounds[0])
                pieces.append(
                    {"projet": "ajout", "name": chemin.stem, "mesh": maillage, "settings": {}, "heures": None}
                )

        plaques = []
        for piece in sorted(pieces, key=lambda item: -(item["heures"] or 0)):
            piece.update(w=float(piece["mesh"].extents[0]) + 6, h=float(piece["mesh"].extents[1]) + 6, extruder=1)
            for plaque in plaques:
                if piece["heures"] is None or any(item["heures"] is None for item in plaque):
                    continue
                if sum(item["heures"] for item in plaque) + piece["heures"] > maximum:
                    continue
                try:
                    anycubic.ranger([dict(item) for item in plaque + [piece]], 0, 1, 6.0, cli=True)
                    plaque.append(piece)
                    break
                except ValueError:
                    continue
            else:
                plaques.append([piece])
        for numero, plaque in enumerate(plaques):
            anycubic.ranger(plaque, numero, len(plaques), 6.0, cli=True)
        dossier_sortie = arguments.sortie or racine / "plaques-mixtes" / "lot"
        dossier_sortie = _sortie_privee(racine, dossier_sortie)
        dossier_sortie.mkdir(parents=True, exist_ok=False)
        archive_sortie = dossier_sortie / "plaques-mixtes.3mf"
        if not plaques:
            rapport.update(
                base=base_nom,
                duree_max_h=maximum,
                conforme=False,
                statut="NON VÉRIFIÉ",
            )
            (dossier_sortie / "rapport.json").write_text(
                json.dumps(rapport, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8"
            )
            return 1
        couleurs = reglages_base.get("filament_colour")
        canonique = isinstance(couleurs, list) and bool(couleurs)
        options_ecriture = {"cli": True}
        if canonique:
            # Le profil explicite garde sa provenance (docs/reference/integrite-3mf.md).
            options_ecriture.update(canonique=True, nom_source=archive_sortie.name)
            rapport["export_3mf"] = {"statut": "PASS", "provenance": "profil explicite de la préparation"}
        else:
            rapport["export_3mf"] = {
                "statut": "NON VÉRIFIÉ",
                "raison": "aucune couleur de filament explicite; export géométrique non prêt au tranchage",
            }
        anycubic.ecrire_3mf(
            [{"name": "", "items": [dict(piece) for piece in plaque]} for plaque in plaques],
            dict(reglages_base),
            archive_sortie,
            **options_ecriture,
        )
        rapport.update(base=base_nom, duree_max_h=maximum)
        for numero, plaque in enumerate(plaques, 1):
            heures = (
                sum(piece["heures"] for piece in plaque)
                if all(piece["heures"] is not None for piece in plaque)
                else None
            )
            rapport["groupes"].append(
                {
                    "plaque": numero,
                    "heures_estimees": round(heures, 2) if heures is not None else None,
                    "depasse": heures > maximum if heures is not None else None,
                    "statut": "NON VÉRIFIÉ" if heures is None else "FAIL" if heures > maximum else "PASS",
                    "source_duree": "part de la plaque source au prorata du volume ; estimation",
                    "pieces": [{"nom": piece["name"], "reglages_par_objet": piece["settings"]} for piece in plaque],
                }
            )
        if arguments.trancher:
            tranche, _duree = anycubic.trancher(
                archive_sortie,
                dossier_sortie,
                "tranche-mixtes.3mf",
                usage="impression",
                prive=racine,
            )
            resultats = anycubic.resultats(tranche)
            attendus = {groupe["plaque"] for groupe in rapport["groupes"]}
            observes = [resultat.get("index") for resultat in resultats]
            rapport["erreurs_tranchage"] = []
            if set(observes) != attendus or len(observes) != len(set(observes)):
                rapport["erreurs_tranchage"].append("plaques tranchées absentes, supplémentaires ou dupliquées")
            for groupe in rapport["groupes"]:
                correspondant = [resultat for resultat in resultats if resultat.get("index") == groupe["plaque"]]
                secondes = duree_positive(correspondant[0].get("secondes")) if len(correspondant) == 1 else None
                if secondes is None:
                    groupe.update(
                        statut="NON VÉRIFIÉ",
                        depasse=None,
                        source_duree="résultat de tranchage absent ou durée invalide",
                    )
                else:
                    heures_tranchees = secondes / 3600
                    groupe.update(
                        heures_tranchees=round(heures_tranchees, 2),
                        grammes=correspondant[0].get("grammes"),
                        depasse=heures_tranchees > maximum,
                        statut="FAIL" if heures_tranchees > maximum else "PASS",
                        source_duree="durée estimée du G-code tranché ; pas une mesure physique",
                    )
        rapport["conforme"] = (
            bool(rapport["groupes"])
            and not rapport.get("erreurs_tranchage")
            and all(groupe["statut"] == "PASS" for groupe in rapport["groupes"])
        )
        rapport["statut"] = (
            "PASS"
            if rapport["conforme"]
            else "FAIL"
            if rapport.get("erreurs_tranchage") or any(groupe["statut"] == "FAIL" for groupe in rapport["groupes"])
            else "NON VÉRIFIÉ"
        )
        (dossier_sortie / "rapport.json").write_text(
            json.dumps(rapport, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8"
        )
        for groupe in rapport["groupes"]:
            print(f"plaque {groupe['plaque']} : {len(groupe['pieces'])} pièce(s), ≈ {groupe['heures_estimees']} h")
        print("exclus :", rapport["exclus"] or "aucun")
        print("écrit :", archive_sortie)
        print("durée :", rapport["statut"])
        return 0 if rapport["conforme"] else 1
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as erreur:
        parser.error(str(erreur))
    return 2


def _protections_globales(prive: Path) -> tuple[set[Path], set[str]]:
    chemins = set()
    empreintes = set()
    dossier_projets = prive / "projets"
    if dossier_projets.is_dir():
        for projet in dossier_projets.iterdir():
            if projet.is_symlink() or not projet.is_dir():
                continue
            if any(
                (projet / nom).is_symlink() for nom in ("qualite.json", "LIVRABLES.json", "VERROU-REIMPRESSION.json")
            ):
                raise ValueError("Fichier de protection lié symboliquement refusé.")
            for relatif, empreinte in protections(projet).items():
                chemins.add((projet / relatif).resolve())
                if isinstance(empreinte, str) and re.fullmatch(r"[0-9a-f]{64}", empreinte):
                    empreintes.add(empreinte)
    return chemins, empreintes


def _sortie_privee(prive: Path, chemin: str | Path) -> Path:
    """Valide une sortie neuve sous l'atelier ; FAIL sur lien/existant, sans création ni réglage changé."""
    from benchab.qualite.ecritures import destination_neuve

    brut = Path(chemin).expanduser()
    candidat = brut if brut.is_absolute() else prive / brut
    try:
        return destination_neuve(candidat, prive=prive, temporaire=False)
    except FileExistsError as erreur:
        raise ValueError("La sortie doit être neuve et rester sous base.prive.") from erreur


__all__ = ["FILAMENT", "PAR_OBJET", "duree_positive", "main", "pieces_projet"]
