"""Audit local des déclarations ACE : aucun accès machine, réseau ou profil actif."""

import argparse
import copy
import hashlib
import json
import math
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from benchab import reglages
from benchab.machine import profil_maitre as PM
from benchab.machine.configuration import charger
from benchab.qualite.ecritures import PublicationNeuve, valider_chemins


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _path(path):
    p = Path(path)
    if ".." in p.parts:
        raise ValueError("Traversée de chemin refusée.")
    valider_chemins([p], [])
    return p.absolute()


def _liste(settings, key):
    value = settings.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(key + " : liste requise, aucun repli implicite.")
    return value


def _nombre(value):
    if isinstance(value, bool):
        raise ValueError("Nombre positif requis, booléen refusé.")
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise ValueError("Nombre positif requis.")
    if not math.isfinite(n) or n <= 0:
        raise ValueError("Nombre positif fini requis.")
    return n


def _attestation(att, contexte):
    if att is None:
        return {"statut": "NON VERIFIE", "raison": "Aucune attestation physique explicite."}
    if not isinstance(att, dict) or att.get("contexte_sha256") != contexte:
        raise ValueError("Attestation physique liée à un autre contexte.")
    if (
        att.get("confirme") is not True
        or not isinstance(att.get("auteur"), str)
        or not att["auteur"].strip()
        or not isinstance(att.get("preuve"), str)
        or not att["preuve"].strip()
    ):
        raise ValueError("Attestation explicite, auteur et preuve requis.")
    try:
        date = datetime.fromisoformat(att["date"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError("Date ISO avec fuseau requise pour attestation.")
    if date.tzinfo is None or date > datetime.now(timezone.utc):
        raise ValueError("Attestation sans fuseau ou future refusée.")
    return {
        "statut": "ATTESTATION_HUMAINE_DATEE",
        "attestation": copy.deepcopy(att),
        "limite": "Déclaration humaine liée au contexte, aucune lecture physique automatique.",
    }


def auditer_affectations(source3mf, declarations, maitre=None, registre=None):
    """Audite les déclarations et un 3MF local (mm, °C, mm³/s) ; rapport DECLARATIONS_ATTESTEES, FAIL ou NON VÉRIFIÉ.

    Les cases ACE sont exactes ; aucune confirmation physique ni commande machine n'est produite.
    """
    source = _path(source3mf)
    if source.suffix.lower() != ".3mf":
        raise ValueError("Projet3MF requis.")
    sha = _sha(source)
    with zipfile.ZipFile(source) as z:
        infos = z.infolist()
        names = [i.filename for i in infos]
        if len(set(names)) != len(names) or any(
            n.startswith("/") or ".." in PurePosixPath(n).parts or "\\" in n for n in names
        ):
            raise ValueError("Archive ambiguë ou chemins non confinés.")
        raw = reglages.lire_membre_projet(z)
    settings = reglages.decoder(raw)
    declaration_path = _path(declarations) if isinstance(declarations, (str, Path)) else None
    decl_sha = _sha(declaration_path) if declaration_path else None
    dec = _json(declaration_path) if declaration_path else copy.deepcopy(declarations)
    if (
        not isinstance(dec, dict)
        or set(dec) - {"schema", "outils"}
        or dec.get("schema") != 1
        or isinstance(dec.get("schema"), bool)
        or not isinstance(dec.get("outils"), list)
    ):
        raise ValueError("Déclarations schema1/outils requises.")
    master = PM.charger_machine(maitre)
    regpath = _path(registre) if isinstance(registre, (str, Path)) else None
    reg = _json(regpath) if regpath else copy.deepcopy(registre) if registre is not None else None
    if not isinstance(reg, dict) or not isinstance(reg.get("filaments"), list):
        raise ValueError("Registre filaments invalide.")
    ids = [f.get("id") for f in reg["filaments"]]
    if len(set(ids)) != len(ids):
        raise ValueError("Identifiants filaments dupliqués.")
    entries = {f["id"]: f for f in reg["filaments"]}
    profiles = _liste(settings, "filament_settings_id")
    if not 1 <= len(profiles) <= 4 or any(not isinstance(x, str) or not x.strip() for x in profiles):
        raise ValueError("Un à quatre profils embarqués explicites requis.")
    n = len(profiles)
    errors, nv = [], []

    def depasse_constructeur(valeur, cle):
        limite = master.get("constructeur", {}).get(cle, {}).get("value")
        if limite is None:
            raison = "Limite constructeur non vérifiée : " + cle
            if raison not in nv:
                nv.append(raison)
            return False
        return valeur > _nombre(limite)

    model = settings.get("printer_model")
    if model not in charger()["machine"]["modeles_slicer"]:
        errors.append("Modèle projet absent ou différent de la S1 du maître.")
    nozzle = _liste(settings, "nozzle_diameter")
    if not nozzle or any(_nombre(x) != master["machine"]["buse_mm"]["value"] for x in nozzle):
        errors.append("Diamètre de buse absent ou contradictoire au maître.")
    if settings.get("nozzle_type") != master["machine"]["buse_matiere"]["value"]:
        errors.append("Matière de buse absente ou contradictoire au maître.")
    seen, slots, bytool = set(), set(), {}
    for d in dec["outils"]:
        if not isinstance(d, dict) or set(d) - {
            "outil",
            "filament_id",
            "slot_declare",
            "confirmation_physique",
            "source_declaration",
            "date_declaration",
            "changements_attendus",
        }:
            raise ValueError("Champs déclaration inconnus ou invalides.")
        tool = d.get("outil")
        if not isinstance(tool, str) or re.fullmatch(r"T[0-3]", tool) is None or int(tool[1]) >= n or tool in seen:
            raise ValueError("OutilT0..T3 absent du projet ou dupliqué.")
        seen.add(tool)
        slot = d.get("slot_declare")
        if type(slot) is not int or not 1 <= slot <= 4 or slot in slots:
            raise ValueError("Slot déclaré1..4 entier unique requis.")
        slots.add(slot)
        if d.get("filament_id") not in entries:
            raise ValueError("Identifiant filament absent du registre unique.")
        bytool[tool] = d
    suffix = "_BRASS" if settings.get("nozzle_type") == "brass" else "_HS"
    numeric_keys = ("nozzle_temperature", "nozzle_temperature_initial_layer", "filament_max_volumetric_speed")
    for key in (
        "filament_type",
        "filament_colour",
        *numeric_keys,
        *(k + suffix for k in numeric_keys),
        "textured_plate_temp",
        "textured_plate_temp_initial_layer",
    ):
        arr = _liste(settings, key)
        if arr and len(arr) != n:
            raise ValueError("Longueur ambiguë des valeurs " + key)
        if key not in ("filament_type", "filament_colour"):
            for value in arr:
                _nombre(value)
        elif any(not isinstance(value, str) for value in arr):
            raise ValueError(key + " : valeurs texte requises.")
    # La validité des réglages projet ne dépend pas de l'existence d'une déclaration ACE.
    for key, limit in [
        ("nozzle_temperature", "temperature_buse_max_C"),
        ("nozzle_temperature_initial_layer", "temperature_buse_max_C"),
        ("textured_plate_temp", "temperature_plateau_max_C"),
        ("textured_plate_temp_initial_layer", "temperature_plateau_max_C"),
    ]:
        selected_key = key + suffix if key + suffix in settings else key
        if any(depasse_constructeur(_nombre(value), limit) for value in _liste(settings, selected_key)):
            errors.append(selected_key + " dépasse le constructeur.")
    # Le contexte exclut uniquement les attestations pour permettre la signature humaine en deux temps.
    declared = [{k: v for k, v in d.items() if k != "confirmation_physique"} for d in dec["outils"]]
    contexte = PM.empreinte_contexte(
        projet_sha256=sha,
        profils=settings,
        declarations=declared,
        maitre=master,
        registre=reg,
        code_sha256=_sha(__file__),
    )
    results = []
    for i, profile in enumerate(profiles):
        tool = "T" + str(i)
        d = bytool.get(tool)
        item = {
            "outil": tool,
            "profil_nom": profile,
            "profil_embarque_sha256": PM.empreinte_contexte(
                {
                    k: (
                        reglages.valeur_brute_indice(settings, k, i, lecteur="index_exact")
                        if reglages.definition(k)["type"] == "liste_filament" and isinstance(v, list) and len(v) == n
                        else v
                    )
                    for k, v in settings.items()
                    if k.startswith(("filament_", "nozzle_temperature"))
                }
            ),
            "matiere_projet": reglages.valeur_brute_indice(settings, "filament_type", i, lecteur="index_exact"),
            "couleur_projet": reglages.valeur_brute_indice(settings, "filament_colour", i, lecteur="index_exact"),
            "declaration": copy.deepcopy(d),
            "confirmation_physique": {"statut": "NON VERIFIE"},
            "erreurs": [],
            "non_verifie": [],
        }
        if not d:
            item["non_verifie"].append("Affectation absente ; aucun slot inféré.")
        else:
            f = entries[d["filament_id"]]
            item["compatibilite"] = PM.compatibilite_filament(master, d["filament_id"], reg, d["slot_declare"])
            if item["compatibilite"]["statut"] == "FAIL":
                item["erreurs"].extend(item["compatibilite"]["alertes"])
            elif item["compatibilite"]["alertes"]:
                item["non_verifie"].extend(item["compatibilite"]["alertes"])
            if not d.get("source_declaration") or not d.get("date_declaration"):
                item["non_verifie"].append("Source/date déclaration manquante.")
            expected_type = f.get("profil", {}).get("filament_type")
            if (
                item["matiere_projet"]
                and isinstance(expected_type, list)
                and len(expected_type) == 1
                and item["matiere_projet"] != expected_type[0]
            ):
                item["erreurs"].append("Matière embarquée différente du registre.")
            elif not expected_type:
                item["non_verifie"].append("Matière du registre non structurée ; aucune déduction depuis le nom.")
            for key, maxkey in [
                ("nozzle_temperature", "temperature_buse_max_C"),
                ("nozzle_temperature_initial_layer", "temperature_buse_max_C"),
                ("filament_max_volumetric_speed", None),
            ]:
                selected_key = key + suffix if key + suffix in settings else key
                arr = _liste(settings, selected_key)
                if not arr:
                    item["non_verifie"].append(key + " absent ; aucun défaut inventé.")
                    continue
                value = _nombre(reglages.valeur_indice(settings, selected_key, i, lecteur="index_exact"))
                item[key] = value
                item[key + "_source"] = selected_key
                if maxkey and depasse_constructeur(value, maxkey):
                    item["erreurs"].append(key + " dépasse le constructeur.")
                ref = f.get("profil", {}).get(key + suffix, f.get("profil", {}).get(key))
                if isinstance(ref, list) and len(ref) == 1:
                    refval = _nombre(ref[0])
                    if value > refval:
                        item["erreurs"].append(
                            key + " dépasse la référence configurée du filament ; pas un maximum physique."
                        )
                else:
                    item["non_verifie"].append("Limite configurée registre absente pour " + key)
            for key in ("textured_plate_temp", "textured_plate_temp_initial_layer"):
                arr = _liste(settings, key)
                if arr:
                    temperature = _nombre(reglages.valeur_indice(settings, key, i, lecteur="index_exact"))
                    if depasse_constructeur(temperature, "temperature_plateau_max_C"):
                        item["erreurs"].append(key + " dépasse le constructeur.")
            item["confirmation_physique"] = _attestation(d.get("confirmation_physique"), contexte)
        if not item["matiere_projet"]:
            item["non_verifie"].append("Matière projet absente.")
        if not item["couleur_projet"]:
            item["non_verifie"].append("Couleur projet absente ; aucune identité déduite.")
        errors.extend(tool + ": " + x for x in item["erreurs"])
        nv.extend(tool + ": " + x for x in item["non_verifie"])
        results.append(item)
    if _sha(source) != sha or (declaration_path and _sha(declaration_path) != decl_sha):
        raise ValueError("Source ou déclarations modifiées pendant audit.")
    physical = all(r["confirmation_physique"]["statut"] == "ATTESTATION_HUMAINE_DATEE" for r in results)
    return {
        "schema": 1,
        "type": "affectations-ace-declarees",
        "source": str(source),
        "source_sha256": sha,
        "configuration_sha256": hashlib.sha256(raw).hexdigest(),
        "contexte_sha256": contexte,
        "maitre_sha256": PM.empreinte_contexte(master),
        "registre_sha256": PM.empreinte_contexte(reg),
        "outils": results,
        "erreurs": errors,
        "non_verifie": nv,
        "statut": "FAIL" if errors else "NON VERIFIE" if nv or not physical else "DECLARATIONS_ATTESTEES",
        "physique": "ATTESTATION_HUMAINE_DATEE" if physical else "NON VERIFIE",
        "compatibilite_lan_s1": "NON VERIFIE",
        "portee": (
            "Outils configurés du projet ; usage réel par plaque non déduit. "
            "Déclaration et attestation humaine, jamais lecture matérielle ou GO impression."
        ),
        "limites": [
            "Aucun slot, matière, couleur, température ou débit inventé.",
            "Une attestation n’est pas une interrogation physique ; son contexte exact doit rester identique.",
            "Aucune commande ni synchronisation machine.",
        ],
    }


def main(argv=None):
    """Rapporte les affectations déclarées, sans modifier les positions ACE."""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source")
    p.add_argument("--declarations", required=True)
    p.add_argument("--json", required=True)
    p.add_argument("--registre", type=Path, required=True)
    p.add_argument("--maitre", type=Path, required=True)
    a = p.parse_args(argv)
    try:
        out = _path(a.json)
        with PublicationNeuve([a.source, a.declarations, a.maitre, a.registre], [out]) as pub:
            r = auditer_affectations(a.source, a.declarations, maitre=a.maitre, registre=a.registre)
            pub.temporaire(out).write_text(
                json.dumps(r, ensure_ascii=False, indent=2, allow_nan=False),
                encoding="utf-8",
            )
            _json(pub.temporaire(out))
            pub.publier()
        print(json.dumps(r, ensure_ascii=False, allow_nan=False))
        return 1 if r["statut"] == "FAIL" else 0
    except (ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile) as e:
        print(str(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
