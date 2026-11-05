"""Planifie les plaques prêtes à partir des seules données privées fournies."""

import argparse
import json
import math
import re
import zipfile
from pathlib import Path

from benchab.donnees.base import Base
from benchab.fabrication.anycubic import resultats
from benchab.projets.envois import lire_fiche

_ID_PREPARATION = re.compile(r"P[0-9]+\Z")
_NOMS_PLATE = re.compile(
    r'<metadata key="plater_id" value="([0-9]+)"\s*/>\s*'
    r'<metadata key="plater_name" value="([^"]*)"\s*/>'
)


def _fichier_dans(racine: Path, relatif: str) -> Path | None:
    if not isinstance(relatif, str) or not relatif:
        return None
    chemin = Path(relatif)
    if chemin.is_absolute() or ".." in chemin.parts:
        return None
    cible = racine / chemin
    for element in (cible, *cible.parents):
        if element.is_symlink():
            return None
    try:
        resolu = cible.resolve(strict=True)
    except FileNotFoundError:
        return None
    if not resolu.is_relative_to(racine.resolve()):
        return None
    return resolu if resolu.is_file() else None


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


def _noms_plaques(archive: Path) -> dict[str, str]:
    with zipfile.ZipFile(archive) as paquet:
        contenu = paquet.read("Metadata/model_settings.config").decode("utf-8")
    return dict(_NOMS_PLATE.findall(contenu))


def plaques_pretes(projets_root: str | Path, base: Base | None = None) -> list[dict]:
    """Retourne les plaques sans envoi enregistré, avec estimations du trancheur."""
    racine = Path(projets_root).expanduser().resolve(strict=True)
    if not racine.is_dir():
        raise ValueError("La racine des projets doit être un dossier privé existant.")
    sorties = []
    for projet in sorted(racine.iterdir(), key=lambda chemin: chemin.name):
        if projet.is_symlink() or not projet.is_dir():
            continue
        fiche = _preparation_courante(projet)
        if fiche is None:
            continue
        donnees = json.loads(fiche.read_text(encoding="utf-8"))
        if base is not None:
            donnees = lire_fiche(base, projet, donnees.get("id", fiche.parent.name))
        if not isinstance(donnees, dict) or donnees.get("envoi"):
            continue
        tranche = donnees.get("tranche_3mf")
        if not isinstance(tranche, dict):
            continue
        archive = _fichier_dans(projet, tranche.get("fichier"))
        if archive is None or archive.suffix.lower() != ".3mf":
            continue
        declaration_path = projet / "DECLARATION-ENVOI.json"
        declaration = {}
        if declaration_path.is_file() and not declaration_path.is_symlink():
            declaration = json.loads(declaration_path.read_text(encoding="utf-8"))
        filaments = declaration.get("filaments", {}) if isinstance(declaration, dict) else {}
        if not isinstance(filaments, dict):
            filaments = {}
        filament = (
            " + ".join(str(valeur.get("matiere", "?")) for valeur in filaments.values() if isinstance(valeur, dict))
            or "?"
        )
        noms = _noms_plaques(archive)
        for resultat in resultats(archive):
            secondes = float(resultat["secondes"])
            grammes = float(resultat["grammes"])
            if not math.isfinite(secondes) or secondes <= 0 or not math.isfinite(grammes) or grammes < 0:
                raise ValueError(f"Estimation de plaque invalide : {projet.name}/{donnees['id']}.")
            sorties.append(
                {
                    "projet": projet.name,
                    "preparation": donnees.get("id", fiche.parent.name),
                    "plaque": int(resultat["index"]),
                    "nom": noms.get(str(resultat["index"]), ""),
                    "heures": round(secondes / 3600, 2),
                    "grammes": round(grammes, 1),
                    "filament": filament,
                    "fichier": archive.relative_to(racine).as_posix(),
                }
            )
    return sorties


def creneaux(plaques: list[dict], heures_max: float) -> list[list[dict]]:
    """Regroupe gloutonnement les plaques par filament et par durée maximale."""
    limite = float(heures_max)
    if not math.isfinite(limite) or limite <= 0:
        raise ValueError("La durée maximale doit être un nombre positif et fini.")
    resultats_creneaux = []
    for filament in sorted({plaque["filament"] for plaque in plaques}):
        groupes = []
        candidates = sorted(
            (plaque for plaque in plaques if plaque["filament"] == filament),
            key=lambda plaque: -plaque["heures"],
        )
        for plaque in candidates:
            if not math.isfinite(float(plaque["heures"])) or plaque["heures"] < 0:
                raise ValueError("Durée de plaque invalide.")
            creneau = next(
                (groupe for groupe in groupes if sum(item["heures"] for item in groupe) + plaque["heures"] <= limite),
                None,
            )
            if creneau is None:
                groupes.append([plaque])
            else:
                creneau.append(plaque)
        resultats_creneaux.extend(groupes)
    return resultats_creneaux


def _heure(valeur: float) -> str:
    return f"{int(valeur)} h {round(valeur % 1 * 60):02d}"


def rendre(plaques: list[dict], heures_max: float) -> str:
    """Produit le rapport Markdown en mémoire, sans effectuer d’écriture."""
    creneaux_planifies = creneaux(plaques, heures_max)
    lignes = [
        "# Planning des plaques prêtes",
        "",
        f"Durée maximale par créneau : {heures_max:g} h.",
        "Durées et masses estimées par le slicer ; quantité restante des bobines non vérifiée.",
        "Aucune impression n’est lancée par cet outil.",
        "",
        "| Projet | Préparation | Plaque | Durée | Masse | Filament | Fichier |",
        "|---|---|---|---|---|---|---|",
    ]
    for plaque in plaques:
        projet = str(plaque["projet"]).replace("|", "\\|").replace("\n", " ")
        nom = str(plaque["nom"]).replace("|", "\\|").replace("\n", " ")
        filament = str(plaque["filament"]).replace("|", "\\|").replace("\n", " ")
        lignes.append(
            f"| {projet} | {plaque['preparation']} | {plaque['plaque']} {nom} | "
            f"{_heure(plaque['heures'])} | {plaque['grammes']} g | {filament} | `{plaque['fichier']}` |"
        )
    lignes.extend(["", "## Besoin par filament", ""])
    for filament in sorted({plaque["filament"] for plaque in plaques}):
        groupe = [plaque for plaque in plaques if plaque["filament"] == filament]
        total_grammes = sum(plaque["grammes"] for plaque in groupe)
        total_heures = sum(plaque["heures"] for plaque in groupe)
        lignes.append(f"- {filament} : {total_grammes:.0f} g, {_heure(total_heures)}")
    lignes.extend(["", f"## Ordre proposé (créneaux de {heures_max:g} h, regroupés par filament)", ""])
    for index, groupe in enumerate(creneaux_planifies, 1):
        duree = _heure(sum(plaque["heures"] for plaque in groupe))
        items = " ; ".join(
            f"{plaque['projet']} {plaque['preparation']} plaque {plaque['plaque']} "
            f"({_heure(plaque['heures'])}, {plaque['filament']})"
            for plaque in groupe
        )
        lignes.append(f"{index}. {duree} — {items}")
    return "\n".join(lignes) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Affiche un planning privé (s) ; aucune machine ni base initialisée implicitement, estimations sans
    essai physique."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prive", type=Path, required=True, help="Racine privée BENCHAB")
    parser.add_argument("--projets", type=Path, required=True, help="Dossier projets dans l’espace privé BENCHAB")
    parser.add_argument("--sortie", type=Path, required=True, help="Rapport dans prive/ ou /private/tmp")
    parser.add_argument("--creneau", type=float, default=3.0, help="Durée maximale d’un créneau, en heures")
    args = parser.parse_args(argv)
    prive = args.prive.expanduser().resolve(strict=True)
    projets = args.projets.expanduser().resolve(strict=True)
    sortie = args.sortie.expanduser()
    for element in (sortie, *sortie.parents):
        if element.is_symlink():
            parser.error("Un composant lié dans le chemin de sortie est refusé.")
    sortie_parent = sortie.parent.resolve(strict=True)
    sortie = sortie_parent / sortie.name
    if sortie.exists() or sortie.is_symlink():
        parser.error("La sortie existe déjà ou est liée.")
    if not sortie_parent.is_dir():
        parser.error("Le dossier de sortie doit exister et ne pas être un lien.")
    if not projets.is_relative_to(prive / "projets"):
        parser.error("Les projets doivent rester sous prive/projets.")
    temporaire = Path("/private/tmp").resolve()
    if not sortie.is_relative_to(prive) and not sortie.is_relative_to(temporaire):
        parser.error("La sortie doit rester dans prive/ ou /private/tmp.")
    base = Base(prive, creer=False)
    try:
        rapport = rendre(plaques_pretes(projets, base), args.creneau)
        with sortie.open("x", encoding="utf-8") as flux:
            flux.write(rapport)
    except (OSError, ValueError, KeyError, json.JSONDecodeError, zipfile.BadZipFile) as erreur:
        parser.error(str(erreur))
    finally:
        base.fermer()
    print(rapport, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
