"""Inventaire et archivage d'exports locaux 3MF, depuis une source explicite.

La présence d'un fichier prouve son contenu, pas une transmission, un démarrage
ou une impression physique. Les dates tirées du nom restent des métadonnées.
Aucun accès réseau, commande d'imprimante ou recherche dans un compte installé.
"""

import hashlib
import io
import json
import math
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from benchab.archives_3mf import Archive3MF
from benchab.gcode.lecture import plaques_gcode_archive
from benchab.qualite.ecritures import PublicationNeuve, valider_chemins

CLES = (
    "filament_settings_id",
    "filament_type",
    "nozzle_temperature_initial_layer",
    "nozzle_temperature",
    "nozzle_temperature_initial_layer_HS",
    "nozzle_temperature_HS",
    "nozzle_temperature_initial_layer_BRASS",
    "nozzle_temperature_BRASS",
    "filament_flow_ratio",
    "filament_max_volumetric_speed",
    "textured_plate_temp_initial_layer",
    "textured_plate_temp",
    "layer_height",
    "sparse_infill_density",
    "wall_loops",
    "print_settings_id",
    "nozzle_type",
)
SUFFIXE = {"brass": "_BRASS", "hardened_steel": "_HS"}
_REGLES_PROJET = "Metadata/project_settings.config"
_MAX_LIGNE_GCODE = 1024 * 1024
_MASSE = re.compile(r"^; (?:total )?filament used \[g\] = ([^\n]+)")
_OUTIL = re.compile(r"^\s*T(\d+)(?=\s|;|$)")


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _lire_plaque(archive, membre):
    """Lit masse déclarée en g et indices d’outils par plaque, sans charger le G-code entier."""
    masses, outils = None, set()
    with archive.ouvrir_flux(membre) as flux, io.TextIOWrapper(
        flux, encoding="utf-8", errors="ignore", newline="\n",
    ) as texte:
        while ligne := texte.readline(_MAX_LIGNE_GCODE + 1):
            if len(ligne) > _MAX_LIGNE_GCODE:
                raise ValueError("ligne G-code hors budget : " + membre)
            if masses is None and (mesure := _MASSE.match(ligne)):
                masses = [float(x) for x in mesure[1].split(",")]
                if any(not math.isfinite(x) or x < 0 for x in masses):
                    raise ValueError("masse de filament non finie ou négative : " + membre)
            if selection := _OUTIL.match(ligne):
                outils.add(int(selection[1]))
    # Une masse positive peut être publiée sans commande T explicite.
    outils.update(i for i, x in enumerate(masses or []) if x > 0)
    return {"gcode": membre, "filament_utilise_g": masses, "filaments_utilises": sorted(outils)}


def _verifier_reste(archive, deja_lus):
    """Contrôle le CRC de chaque autre membre sans le charger en mémoire."""
    for membre in archive.noms():
        if membre in deja_lus or membre.endswith("/"):
            continue
        with archive.ouvrir_flux(membre) as flux:
            while flux.read(64 * 1024):
                pass


def lire(p):
    """Inventorie un 3MF tranché sans écriture ; masses en g, réglages typés, refus si archive ambiguë."""
    p = Path(p)
    try:
        with Archive3MF(p) as archive:
            initial = sha(p)
            archive.verifier_source(p)
            if _REGLES_PROJET not in archive.noms():
                raise ValueError(f"{p.name} : réglages absents")
            st = archive.reglages(_REGLES_PROJET)
            plaques = plaques_gcode_archive(archive)
            if not plaques:
                raise ValueError(f"{p.name} : aucun G-code de plaque")
            details = [_lire_plaque(archive, nom) for _, nom in plaques]
            _verifier_reste(archive, {_REGLES_PROJET, *(nom for _, nom in plaques)})
            # Une plaque non renseignée interdit de présenter un total partiel
            # comme la consommation de l'archive entière.
            utilise = None
            if all(d["filament_utilise_g"] is not None for d in details):
                utilise = [
                    math.fsum(d["filament_utilise_g"][i] for d in details if i < len(d["filament_utilise_g"]))
                    for i in range(max(len(d["filament_utilise_g"]) for d in details))
                ]
            m = re.match(r"(\d{2})(\d{2})-(\d{2})(\d{2})-(.*)\.gcode\.3mf$", p.name)
            rapport = {
                "fichier": p.name,
                "envoye_le": f"{m[2]}/{m[1]} {m[3]}:{m[4]}" if m else None,
                "nom": m[5] if m else p.stem,
                "sha256": initial,
                "octets": p.stat().st_size,
                "plaques": len(plaques),
                "filament_utilise_g": utilise,
                "filaments_utilises": sorted({outil for detail in details for outil in detail["filaments_utilises"]}),
                "details_plaques": details,
                "reglages": {k: st.get(k) for k in CLES if k in st},
                **temperature_appliquee(st),
            }
            if sha(p) != initial:
                raise ValueError("source modifiée pendant lecture")
            archive.verifier_source(p)
            return rapport
    except (zipfile.BadZipFile, json.JSONDecodeError, KeyError, TypeError, AttributeError, UnicodeError) as e:
        raise ValueError(f"{p.name} : archive 3MF illisible ({e})") from e


def temperature_appliquee(st):
    """Variante de température du type déclaré : _BRASS pour laiton, _HS pour acier trempé.
    Ambiguë seulement si le type est inconnu et que les variantes diffèrent de la base."""
    s = SUFFIXE.get(str(st.get("nozzle_type", "")).lower())
    if s:
        return {
            "buse_declaree": st.get("nozzle_type"),
            "temperature_appliquee": {
                "premiere_couche": st.get(
                    f"nozzle_temperature_initial_layer{s}", st.get("nozzle_temperature_initial_layer")
                ),
                "suite": st.get(f"nozzle_temperature{s}", st.get("nozzle_temperature")),
            },
            "temperature_ambigue": False,
        }
    return {
        "buse_declaree": st.get("nozzle_type"),
        "temperature_appliquee": None,
        "temperature_ambigue": any(
            st.get(f"nozzle_temperature{x}") not in (None, st.get("nozzle_temperature")) for x in ("_HS", "_BRASS")
        ),
    }


def envois(source):
    return sorted(source.glob("*/*.gcode.3mf")) + sorted(source.glob("*.gcode.3mf")) if source.is_dir() else []


def archiver(source, archive):
    """Vérifie les fichiers réels, conserve l'historique et publie sans collision.

    Le verrou local empêche deux archivages concurrents. Une copie complète peut
    rester sans entrée JSON si l'écriture du registre échoue ; elle sera retrouvée
    au prochain inventaire. Aucun fichier utilisateur n'est supprimé ni écrasé.
    Le registre, seul fichier mutable, est remplacé après relecture JSON.
    """
    source, archive = Path(source), Path(archive)
    fiche = archive / "ENVOIS.json"
    valider_chemins([source, archive, fiche], [])
    archive.mkdir(parents=True, exist_ok=True)
    verrou = archive / ".archivage.lock"
    verrou.mkdir()  # exclusif, jamais forcé, même après arrêt brutal
    try:
        initial = sha(fiche) if fiche.exists() else None
        try:
            connus = json.loads(fiche.read_text(encoding="utf-8")) if fiche.exists() else []
        except (ValueError, UnicodeError) as exc:
            raise ValueError("ENVOIS.json illisible : historique conservé, archivage refusé.") from exc
        if not isinstance(connus, list) or any(
            not isinstance(e, dict)
            or not re.fullmatch(r"[0-9a-f]{64}", str(e.get("sha256", "")))
            or not isinstance(e.get("archive"), str)
            or not e["archive"]
            or Path(e["archive"]).name != e["archive"]
            or ("historique_etats" in e and not isinstance(e["historique_etats"], list))
            for e in connus
        ):
            raise ValueError("ENVOIS.json invalide : historique conservé, archivage refusé.")
        copies, deja, erreurs = [], 0, []
        physiques = {}
        for p in sorted(archive.glob("*.gcode.3mf")):
            try:
                valider_chemins([p], [])
                physiques[p.name] = lire(p)
            except (ValueError, OSError) as exc:
                erreurs.append(f"archive corrompue ou illisible : {p.name} ({exc})")
        for e in connus:
            p = archive / e["archive"]
            reel = physiques.get(e["archive"])
            etat = (
                "verifiee"
                if reel and reel["sha256"] == e["sha256"]
                else ("absente" if not p.exists() and not p.is_symlink() else "corrompue")
            )
            if etat != e.get("etat_archive") and e.get("etat_archive"):
                e.setdefault("historique_etats", []).append(e["etat_archive"])
            e["etat_archive"] = etat
            if etat != "verifiee":
                erreurs.append(f"archive {etat} : {e['archive']} (empreinte attendue {e['sha256']})")
        enregistres = {(e["archive"], e["sha256"]) for e in connus}
        for nom, e in physiques.items():
            if (nom, e["sha256"]) not in enregistres:
                connus.append(
                    dict(e, archive=nom, etat_archive="verifiee", origine_registre="inventaire des fichiers presents")
                )
        vus = {e["sha256"] for e in physiques.values()}
        for p in envois(source):
            try:
                valider_chemins([p], [])
                e = lire(p)
            except (ValueError, OSError) as exc:
                erreurs.append(str(exc))
                continue
            if e["sha256"] in vus:
                deja += 1
                continue
            cible = archive / p.name
            base = p.stem.removesuffix(".gcode")
            variantes = [
                cible,
                archive / f"{base}-{e['sha256'][:8]}.gcode.3mf",
                archive / f"{base}-{e['sha256']}.gcode.3mf",
            ]
            compteur = 0
            while True:
                cible = variantes.pop(0) if variantes else archive / f"{base}-{e['sha256']}-{compteur}.gcode.3mf"
                compteur += 1
                if not cible.exists() and not cible.is_symlink():
                    break
            with PublicationNeuve([p], [cible]) as publication:
                temp = publication.temporaire(cible)
                shutil.copy2(p, temp)
                if sha(temp) != e["sha256"] or sha(p) != e["sha256"]:
                    raise ValueError(f"{p.name} : copie ou source modifiée, publication refusée")
                # Le SHA identique à la source dont tous les CRC ont été
                # relus prouve la copie, sans décompresser une seconde fois.
                publication.publier()
            e.update(archive=cible.name, etat_archive="verifiee")
            ancienne = next((a for a in connus if a["archive"] == e["archive"] and a["sha256"] == e["sha256"]), None)
            if ancienne is None:
                connus.append(e)
            else:
                if ancienne.get("etat_archive") != "verifiee":
                    ancienne.setdefault("historique_etats", []).append(ancienne.get("etat_archive", "non_verifiee"))
                ancienne["etat_archive"] = "verifiee"
            physiques[cible.name] = e
            vus.add(e["sha256"])
            copies.append(e)
        # Une restauration peut réoccuper le nom d'une ancienne entrée d'une
        # autre empreinte : refléter l'état final, sans perdre l'état observé.
        for e in connus:
            reel = physiques.get(e["archive"])
            p = archive / e["archive"]
            etat = (
                "verifiee"
                if reel and reel["sha256"] == e["sha256"]
                else ("absente" if not p.exists() and not p.is_symlink() else "corrompue")
            )
            if etat != e["etat_archive"]:
                e.setdefault("historique_etats", []).append(e["etat_archive"])
                e["etat_archive"] = etat
        with tempfile.TemporaryDirectory(prefix=".registre-", dir=archive) as tmp:
            temp = Path(tmp) / "ENVOIS.json"
            temp.write_text(json.dumps(connus, indent=1, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            json.loads(temp.read_text(encoding="utf-8"))
            os.chmod(temp, (fiche.stat().st_mode & 0o777) if fiche.exists() else 0o600)
            with temp.open("rb") as f:
                os.fsync(f.fileno())
            valider_chemins([archive, fiche], [])
            if (sha(fiche) if fiche.exists() else None) != initial:
                raise ValueError("ENVOIS.json modifié pendant archivage : remplacement refusé.")
            os.replace(temp, fiche)
        return copies, deja, erreurs
    finally:
        verrou.rmdir()


def main(argv=None, *, prive=None):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["lister", "archiver"])
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--prive", type=Path)
    arguments = parser.parse_args(argv)
    if arguments.action == "lister":
        print(json.dumps([lire(p) for p in envois(arguments.source)], ensure_ascii=False, indent=2))
        return 0
    racine_privee = (
        Path(prive or arguments.prive).expanduser().resolve(strict=True) if prive or arguments.prive else None
    )
    if racine_privee is None:
        parser.error("archiver exige un atelier privé via --prive ou le CLI BENCHAB")
    archive = arguments.archive or racine_privee / "archives/slicer"
    brute = Path(archive).expanduser()
    if not brute.is_absolute():
        brute = racine_privee / brute
    if any(element.is_symlink() for element in (brute, *brute.parents)):
        parser.error("Un lien symbolique dans le chemin d’archive est refusé")
    archive = brute.resolve()
    if not archive.is_relative_to(racine_privee):
        parser.error("L’archive doit rester sous base.prive")
    copies, deja, erreurs = archiver(arguments.source, archive)
    print(
        json.dumps(
            {"copies": [c["archive"] for c in copies], "deja_archives": deja, "illisibles": erreurs}, ensure_ascii=False
        )
    )
    return 1 if erreurs else 0


if __name__ == "__main__":
    raise SystemExit(main())
