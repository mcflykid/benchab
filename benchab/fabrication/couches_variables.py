"""Calcule et écrit des plages de hauteur de couche dans une copie de 3MF."""

import argparse
import json
import math
import os
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import trimesh

from benchab.archives_3mf import Archive3MF
from benchab.fabrication import anycubic
from benchab.gcode.lecture import iter_lignes_gcode_3mf, plaques_gcode_archive
from benchab.projets.protections import protections
from benchab.qualite.ecritures import acquerir_verrou, ouvrir_verrou
from benchab.qualite.empreintes import sha256_fichier

MODES = {"qualite": (0.12, 0.28, 0.1), "vitesse": (0.16, 0.28, 0.2)}


def _lire_reglages_source(chemin: str | Path) -> dict:
    """Lit les réglages typés et vérifie les CRC du 3MF, sans modifier la source."""
    with Archive3MF(chemin) as archive:
        membre = "Metadata/project_settings.config"
        noms = archive.noms()
        if membre not in noms:
            raise ValueError("Configuration de projet absente du 3MF source.")
        reglages = archive.reglages()
        archive.verifier(membres=[nom for nom in noms if nom != membre])
        archive.verifier_source(chemin)
        return reglages


def profil(maillage, base=0.2, hmin=0.12, hmax=0.28, marche=0.1, pas=0.5):
    """Construit `[z0, h0, z1, h1, …]` selon la pente des faces à chaque hauteur."""
    valeurs = (base, hmin, hmax, marche, pas)
    if not all(math.isfinite(float(valeur)) for valeur in valeurs):
        raise ValueError("Paramètres de profil finis requis.")
    if hmin <= 0 or hmax < hmin or marche <= 0 or pas <= 0:
        raise ValueError("Bornes de hauteur et pas positifs requis.")
    normales = np.asarray(maillage.face_normals)
    centres = np.asarray(maillage.triangles_center)
    bornes = np.asarray(maillage.bounds)
    if (
        normales.ndim != 2
        or normales.shape[1] != 3
        or centres.shape != normales.shape
        or bornes.shape != (2, 3)
        or not np.isfinite(normales).all()
        or not np.isfinite(centres).all()
        or not np.isfinite(bornes).all()
    ):
        raise ValueError("Maillage invalide pour calculer le profil.")
    z_min, z_max = bornes[:, 2]
    az = np.abs(normales[:, 2])
    pente = (az > 0.05) & (az < 0.985)
    plats = az >= 0.985
    points = []
    for z in np.arange(z_min, z_max, pas):
        selection = (centres[:, 2] >= z - pas) & (centres[:, 2] < z + pas)
        inclinees = selection & pente
        horizontales = selection & plats
        if inclinees.any():
            hauteur = float(np.clip(marche / az[inclinees].max(), hmin, hmax))
        elif horizontales.any():
            hauteur = float(base)
        else:
            hauteur = float(hmax)
        points.extend((round(float(z - z_min), 3), round(hauteur, 3)))
    if not points:
        raise ValueError("Maillage sans hauteur exploitable.")
    return points + [round(float(z_max - z_min), 4), points[-1]]


def plages(points, quantum=0.04):
    """Convertit un profil en plages contiguës, hauteurs arrondies au quantum donné."""
    pas = float(quantum)
    if not math.isfinite(pas) or pas <= 0 or len(points) < 4 or len(points) % 2:
        raise ValueError("Profil ou quantum invalide.")
    points_hauteur = [
        (float(points[index]), round(round(float(points[index + 1]) / pas) * pas, 3))
        for index in range(0, len(points) - 2, 2)
    ]
    z_max = float(points[-2])
    if not all(math.isfinite(z) and math.isfinite(h) for z, h in points_hauteur) or not math.isfinite(z_max):
        raise ValueError("Profil non fini.")
    resultat = []
    for (z_debut, hauteur), (z_fin, _) in zip(
        points_hauteur,
        points_hauteur[1:] + [(z_max, None)],
    ):
        if z_fin <= z_debut:
            raise ValueError("Plages de hauteur non croissantes.")
        if resultat and resultat[-1][2] == hauteur:
            resultat[-1][1] = z_fin
        else:
            resultat.append([z_debut, z_fin, hauteur])
    return resultat


def _signature_sortie(chemin: Path):
    if chemin.is_symlink():
        raise ValueError("Sortie symbolique refusée.")
    try:
        avant = chemin.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(avant.st_mode) or avant.st_nlink != 1:
        raise ValueError("Sortie non régulière ou liée refusée.")
    empreinte = sha256_fichier(chemin)
    apres = chemin.lstat()
    signature_avant = (avant.st_dev, avant.st_ino, avant.st_size, avant.st_mtime_ns, avant.st_ctime_ns)
    signature_apres = (apres.st_dev, apres.st_ino, apres.st_size, apres.st_mtime_ns, apres.st_ctime_ns)
    if signature_avant != signature_apres:
        raise ValueError("Sortie modifiée pendant sa vérification.")
    return (*signature_apres, empreinte)


def _verifier_destination(chemin: Path, initial) -> None:
    for parent in chemin.parents:
        marqueurs = any(
            (parent / marqueur).is_file() for marqueur in ("qualite.json", "LIVRABLES.json", "VERROU-REIMPRESSION.json")
        )
        if marqueurs or (parent / "preparations").is_dir():
            chemins_figes = protections(parent)
            relatif = chemin.relative_to(parent).as_posix()
            if relatif in chemins_figes or (initial and initial[-1] in chemins_figes.values()):
                raise ValueError("Fichier figé protégé : choisir une nouvelle sortie.")
    if initial is not None and not chemin.stat().st_mode & 0o222:
        raise ValueError("Fichier sans permission d’écriture : choisir une nouvelle sortie.")


def _chemin_autorise(chemin: str | Path) -> Path:
    """Valide une copie privée/temporaire ; Path sans unité, aucun lien interne ni réglage modifié."""
    from benchab.chemins import atelier_prive, est_temporaire, sans_lien

    cible = sans_lien(chemin)
    cible.parent.resolve(strict=True)
    if not cible.is_relative_to(atelier_prive()) and not est_temporaire(cible):
        raise ValueError("Écriture autorisée seulement dans prive/ ou le temporaire système.")
    return cible


def ecrire_profils(source: str | Path, destination: str | Path, profils: dict) -> None:
    """Publie les plages Z/hauteurs en mm ; refuse tout autre écart et protège les sorties figées."""
    from benchab import integrite_3mf

    source = Path(source).expanduser()
    if source.is_symlink():
        raise ValueError("Source 3MF liée refusée.")
    source = source.resolve(strict=True)
    destination = _chemin_autorise(destination)
    if not source.is_file() or source.is_symlink():
        raise ValueError("Source 3MF ordinaire requise.")
    initial = _signature_sortie(destination)
    _verifier_destination(destination, initial)
    source_sha = sha256_fichier(source)
    lignes = ['<?xml version="1.0" encoding="utf-8"?>', "<objects>"]
    for identifiant, points in profils.items():
        if not str(identifiant).isdigit() or int(identifiant) < 1:
            raise ValueError("Identifiant d’objet invalide.")
        lignes.append(f' <object id="{int(identifiant)}">')
        for debut, fin, hauteur in plages(points):
            lignes.extend(
                [
                    f'  <range min_z="{debut:.3f}" max_z="{fin:.3f}">',
                    f'   <option opt_key="layer_height">{hauteur:.2f}</option>',
                    "  </range>",
                ]
            )
        lignes.append(" </object>")
    xml = "\n".join(lignes + ["</objects>", ""]).encode("utf-8")
    avant = integrite_3mf.instantane(source)
    autres = [cle for cle in avant if cle.startswith("couches/")
              and cle.rsplit("/", 1)[-1] not in {"@present", "@id", "@min_z", "@max_z", "layer_height"}]
    if autres:
        # Changer les hauteurs ne permet pas d'effacer une autre intention d'impression
        # (docs/reference/integrite-3mf.md, profils de couches).
        raise ValueError("Réglage de plage différent ou inconnu : transformation des seules hauteurs impossible : "
                         + ", ".join(autres))
    prevu = {cle: valeur for cle, valeur in avant.items() if not cle.startswith("couches/")}
    prevu.update(integrite_3mf.instantane_config({}, plages_couches=xml))
    # Les hauteurs sont proposées explicitement ; ne jamais autoriser une différence lue après écriture
    # (docs/reference/integrite-3mf.md, profils de couches).
    permis = integrite_3mf.difference(avant, prevu)
    parent_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        verrou_fd = ouvrir_verrou(".couches-variables.lock", parent_fd)
        with os.fdopen(verrou_fd, "a") as verrou:
            acquerir_verrou(verrou)
            if _signature_sortie(destination) != initial:
                raise ValueError("Destination modifiée avant écriture.")
            _verifier_destination(destination, initial)
            with tempfile.TemporaryDirectory(prefix=".couches-variables-", dir=destination.parent) as temporaire:
                temporaire = Path(temporaire) / "profils.3mf"
                cle = "Metadata/layer_config_ranges.xml"
                with Archive3MF(source) as entree, zipfile.ZipFile(temporaire, "w") as sortie:
                    noms = entree.noms()
                    sortie.comment = entree.commentaire
                    for nom in noms:
                        information = entree.information(nom)
                        if nom == cle:
                            # Les anciennes plages doivent être lisibles, même si elles sont remplacées.
                            entree.verifier(membres=[nom])
                            sortie.writestr(information, xml)
                        else:
                            with entree.ouvrir_flux(nom) as lecteur, sortie.open(information, "w") as ecrivain:
                                shutil.copyfileobj(lecteur, ecrivain, 1024 * 1024)
                    if cle not in noms:
                        sortie.writestr(cle, xml)
                    entree.verifier_source(source)
                with Archive3MF(temporaire) as relecture:
                    if relecture.lire(cle, limite_octets=relecture.taille_max_xml) != xml:
                        raise ValueError("Profils 3MF relus non conformes.")
                    relecture.verifier(membres=[nom for nom in relecture.noms() if nom != cle])
                # Seul le membre de plages calculé ci-dessus change ; tous
                # les réglages projet/objet/plaque restent identiques
                # (docs/reference/integrite-3mf.md).
                integrite_3mf.verifier(source, temporaire, permis)
                if sha256_fichier(source) != source_sha:
                    raise ValueError("Source modifiée pendant l’écriture.")
                if _signature_sortie(destination) != initial:
                    raise ValueError("Destination modifiée pendant l’écriture.")
                _verifier_destination(destination, initial)
                if initial is not None:
                    os.chmod(temporaire, destination.stat().st_mode & 0o777)
                with temporaire.open("rb") as fichier:
                    os.fsync(fichier.fileno())
                if initial is None:
                    os.link(temporaire, destination, follow_symlinks=False)
                else:
                    os.replace(temporaire, destination)
                os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def hauteurs_gcode(archive: str | Path) -> dict:
    """Résume les hauteurs G-code en mm, en flux depuis un seul 3MF ; aucune écriture."""
    with Archive3MF(archive) as source:
        hauteurs = [
            # Le G-code peut écrire 0,199999 pour une couche nominale de 0,20 mm.
            round(float(correspondance.group(1)), 3)
            for _, membre in plaques_gcode_archive(source)
            for ligne in iter_lignes_gcode_3mf(archive, membre, archive=source)
            if (correspondance := re.match(r"^;HEIGHT:([\d.]+)", ligne))
        ]
        source.verifier_source(archive)
    return {
        "couches": len(hauteurs),
        "min": min(hauteurs) if hauteurs else None,
        "max": max(hauteurs) if hauteurs else None,
        "distinctes": len(set(hauteurs)),
    }


def _sous_prive(chemin: str | Path, prive: Path) -> Path:
    brut = Path(chemin).expanduser()
    if any(element.is_symlink() for element in (brut, *brut.parents)):
        raise ValueError("Lien symbolique dans un chemin privé refusé.")
    resolu = brut.resolve(strict=True)
    if not resolu.is_relative_to(prive) or not resolu.is_file() and not resolu.is_dir():
        raise ValueError("La source doit rester dans base.prive.")
    return resolu


def main(
    source: str | Path,
    *,
    prive: str | Path,
    placement: str | Path,
    candidats: str | Path,
    sortie: str | Path,
    mode: str | None = None,
    hmin: float = 0.12,
    hmax: float = 0.28,
    marche: float = 0.1,
    appliquer: bool = False,
    personnalise: bool = False,
) -> dict:
    """Compare les couches variables aux temps (min) et masses (g) fixes déclarés, puis retourne PASS/NON VÉRIFIÉ.

    Entrées : chemins privés, hauteurs en mm et modes ; sortie : rapports numériques et validation_physique=False.
    Les réglages passent par le dictionnaire typé ; aucune source ni réglage d'impression n'est modifié sauf demande.
    """
    racine_privee = Path(prive).expanduser().resolve(strict=True)
    archive_source = _sous_prive(source, racine_privee)
    placement_path = _sous_prive(placement, racine_privee)
    dossier_maillages = _sous_prive(candidats, racine_privee)
    if not dossier_maillages.is_dir():
        raise ValueError("Le dossier des maillages doit exister.")
    projet = dossier_maillages.parent
    reference_fixe_brute = projet / "preuves" / "tranchage.json"
    reference_fixe = None
    if reference_fixe_brute.exists():
        reference_fixe = _sous_prive(reference_fixe_brute, racine_privee)
    cible_sortie = Path(sortie).expanduser()
    if any(element.is_symlink() for element in (cible_sortie, *cible_sortie.parents)):
        raise ValueError("Lien symbolique dans la sortie refusé.")
    parent_sortie = cible_sortie.parent.resolve(strict=True)
    cible_sortie = parent_sortie / cible_sortie.name
    if not cible_sortie.is_relative_to(racine_privee) or cible_sortie.exists():
        raise ValueError("La sortie doit être neuve et rester sous base.prive.")
    if mode is not None and mode not in MODES:
        raise ValueError("Mode inconnu ; choisir qualite ou vitesse.")
    modes = (
        {"personnalise": (hmin, hmax, marche)}
        if mode is None and personnalise
        else dict(MODES)
        if mode is None
        else {mode: MODES[mode]}
    )
    if appliquer and len(modes) != 1:
        raise ValueError("L’application exige un mode unique.")
    if not all(math.isfinite(float(valeur)) for bornes in modes.values() for valeur in bornes):
        raise ValueError("Paramètres de modes finis requis.")

    reglages = _lire_reglages_source(archive_source)
    hauteur_base = reglages.get("layer_height", ".2")
    base = float(hauteur_base[0] if isinstance(hauteur_base, list) else hauteur_base)
    donnees_placement = json.loads(placement_path.read_text(encoding="utf-8"))
    objets = donnees_placement.get("objects") if isinstance(donnees_placement, dict) else None
    if not isinstance(objets, list) or not objets:
        raise ValueError("Le placement ne contient pas d’objet exploitable.")
    maillages = []
    empreintes_initiales = {chemin: sha256_fichier(chemin) for chemin in (archive_source, placement_path)}
    if reference_fixe is not None:
        empreintes_initiales[reference_fixe] = sha256_fichier(reference_fixe)
        reference_donnees = json.loads(reference_fixe.read_text(encoding="utf-8"))
        plaques_fixes = reference_donnees.get("plaques") if isinstance(reference_donnees, dict) else None
        if not isinstance(plaques_fixes, list):
            raise ValueError("Référence fixe sans liste de plaques exploitable.")
        fixes = {}
        for ligne in plaques_fixes:
            if not isinstance(ligne, dict) or type(ligne.get("index")) is not int or ligne["index"] < 1:
                raise ValueError("Index invalide dans la référence de tranchage fixe.")
            index_fixe = ligne["index"]
            secondes = ligne.get("secondes")
            grammes = ligne.get("grammes")
            if (index_fixe in fixes or isinstance(secondes, bool) or not isinstance(secondes, (int, float))
                    or not math.isfinite(secondes) or secondes <= 0 or isinstance(grammes, bool)
                    or not isinstance(grammes, (int, float)) or not math.isfinite(grammes) or grammes <= 0):
                raise ValueError("Temps ou masse fixe invalide/dupliqué dans la référence.")
            fixes[index_fixe] = {"secondes": float(secondes), "grammes": float(grammes)}
    else:
        fixes = {}
    for objet in objets:
        if not isinstance(objet, dict) or not isinstance(objet.get("name"), str) or not objet["name"]:
            raise ValueError("Objet de placement invalide.")
        nom_maillage = objet.get("model", objet["name"])
        if not isinstance(nom_maillage, str) or Path(nom_maillage).name != nom_maillage:
            raise ValueError("Nom de maillage invalide.")
        chemin_maillage = _sous_prive(dossier_maillages / f"{nom_maillage}.stl", racine_privee)
        empreintes_initiales[chemin_maillage] = sha256_fichier(chemin_maillage)
        maillage = trimesh.load_mesh(chemin_maillage)
        if not isinstance(maillage, trimesh.Trimesh) or maillage.is_empty:
            raise ValueError(f"Maillage invalide : {nom_maillage}.")
        maillage.apply_translation([0, 0, -maillage.bounds[0, 2]])
        maillages.append(maillage)

    cible_sortie.mkdir(parents=False, exist_ok=False)
    rapport = {
        "base": base,
        "reference_fixe": {
            "statut": "PASS" if reference_fixe is not None else "NON VÉRIFIÉ",
            "source": reference_fixe.relative_to(racine_privee).as_posix() if reference_fixe else None,
            "sha256": sha256_fichier(reference_fixe) if reference_fixe else None,
        },
        "modes": {},
        "validation_physique": False,
    }
    for nom_mode, (minimum, maximum, pas_visible) in modes.items():
        profils = {
            indice: profil(maillage, base, minimum, maximum, pas_visible)
            for indice, maillage in enumerate(maillages, 1)
        }
        profil_3mf = cible_sortie / f"plaques-{nom_mode}.3mf"
        ecrire_profils(archive_source, profil_3mf, profils)
        try:
            tranche, _duree = anycubic.trancher(
                profil_3mf,
                cible_sortie,
                f"tranche-{nom_mode}.3mf",
                usage="impression",
                prive=racine_privee,
            )
        except RuntimeError as erreur:
            rapport["modes"][nom_mode] = {"statut": "INCOMPATIBLE", "detail": str(erreur)}
            continue
        estimations_variables = anycubic.resultats(tranche)
        lignes = []
        for resultat in estimations_variables:
            gcode = hauteurs_gcode(tranche)
            indice_plaque = resultat["index"]
            fixe = fixes.get(indice_plaque)
            ligne = {
                "plaque": indice_plaque,
                "variable_min": round(resultat["secondes"] / 60),
                "variable_g": round(resultat["grammes"], 1),
                "hauteurs_tranchees": gcode,
                "avertissements": resultat["avertissements"],
                "comparaison_fixe_statut": "NON VÉRIFIÉ",
                "fixe_min": None,
                "fixe_g": None,
                "gain_temps_pct": None,
            }
            if fixe is not None:
                ligne.update(
                    comparaison_fixe_statut="PASS",
                    fixe_min=round(fixe["secondes"] / 60),
                    fixe_g=round(fixe["grammes"], 1),
                    gain_temps_pct=round(100 * (1 - float(resultat["secondes"]) / fixe["secondes"]), 1),
                )
            lignes.append(ligne)
        rapport["modes"][nom_mode] = {
            "min": minimum,
            "max": maximum,
            "marche": pas_visible,
            "plaques": lignes,
        }
        if appliquer:
            if any(
                sha256_fichier(chemin) != empreinte
                for chemin, empreinte in empreintes_initiales.items()
                if chemin != archive_source
            ):
                raise ValueError("Une source a changé pendant l’étude ; application refusée.")
            ecrire_profils(archive_source, archive_source, profils)
            rapport["applique"] = nom_mode
    if not appliquer and any(sha256_fichier(chemin) != empreinte for chemin, empreinte in empreintes_initiales.items()):
        raise ValueError("Une source a changé pendant l’étude.")
    if appliquer and any(
        sha256_fichier(chemin) != empreinte
        for chemin, empreinte in empreintes_initiales.items()
        if chemin != archive_source
    ):
        raise ValueError("Une source a changé pendant l’étude.")
    rapport["note"] = "Estimations du slicer ; aspect et tenue restent à évaluer physiquement."
    (cible_sortie / "comparaison.json").write_text(
        json.dumps(rapport, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )
    return rapport


def cli(argv: list[str] | None = None) -> int:
    """Interface en ligne de commande privée, sans modification des profils installés."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prive", type=Path, required=True)
    parser.add_argument("source", type=Path)
    parser.add_argument("--placement", type=Path)
    parser.add_argument("--candidats", type=Path)
    parser.add_argument("--mode", choices=sorted(MODES))
    parser.add_argument("--min", type=float, default=0.12)
    parser.add_argument("--max", type=float, default=0.28)
    parser.add_argument("--marche", type=float)
    parser.add_argument("--sortie", type=Path)
    parser.add_argument("--appliquer", action="store_true")
    args = parser.parse_args(argv)
    try:
        prive = args.prive.expanduser().resolve(strict=True)
        source = _sous_prive(args.source, prive)
        projet = source.parent.parent
        placement = args.placement or projet / "preuves/placement.json"
        candidats = args.candidats or projet / "candidats"
        sortie = args.sortie or projet / "preuves/couches-variables"
        rapport = main(
            source,
            prive=prive,
            placement=placement,
            candidats=candidats,
            sortie=sortie,
            mode=args.mode,
            hmin=args.min,
            hmax=args.max,
            marche=0.1 if args.marche is None else args.marche,
            appliquer=args.appliquer,
            personnalise=args.marche is not None,
        )
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, zipfile.BadZipFile) as erreur:
        parser.error(str(erreur))
    print(json.dumps(rapport, ensure_ascii=False, indent=2))
    return 0
