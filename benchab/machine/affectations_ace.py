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


def _charger_contexte(source3mf, declarations, maitre, registre):
    """Charge et qualifie les sources 3MF, maître et registre sans les modifier."""
    source = _path(source3mf)
    if source.suffix.lower() != ".3mf":
        raise ValueError("Projet3MF requis.")
    sha_source = _sha(source)
    with zipfile.ZipFile(source) as archive:
        infos = archive.infolist()
        noms = [info.filename for info in infos]
        if len(set(noms)) != len(noms) or any(
            nom.startswith("/") or ".." in PurePosixPath(nom).parts or "\\" in nom for nom in noms
        ):
            raise ValueError("Archive ambiguë ou chemins non confinés.")
        brut = reglages.lire_membre_projet(archive)
    settings = reglages.decoder(brut)
    declaration_path = _path(declarations) if isinstance(declarations, (str, Path)) else None
    sha_declarations = _sha(declaration_path) if declaration_path else None
    donnees = _json(declaration_path) if declaration_path else copy.deepcopy(declarations)
    if (
        not isinstance(donnees, dict)
        or set(donnees) - {"schema", "outils"}
        or donnees.get("schema") != 1
        or isinstance(donnees.get("schema"), bool)
        or not isinstance(donnees.get("outils"), list)
    ):
        raise ValueError("Déclarations schema1/outils requises.")
    maitre_charge = PM.charger_machine(maitre)
    registre_path = _path(registre) if isinstance(registre, (str, Path)) else None
    registre_charge = (
        _json(registre_path)
        if registre_path
        else copy.deepcopy(registre) if registre is not None else None
    )
    if not isinstance(registre_charge, dict) or not isinstance(registre_charge.get("filaments"), list):
        raise ValueError("Registre filaments invalide.")
    ids = [filament.get("id") for filament in registre_charge["filaments"]]
    if len(set(ids)) != len(ids):
        raise ValueError("Identifiants filaments dupliqués.")
    entrees = {filament["id"]: filament for filament in registre_charge["filaments"]}
    return (
        source, sha_source, brut, settings, declaration_path, sha_declarations, donnees,
        maitre_charge, registre_path, registre_charge, entrees,
    )


def _valider_declarations(outils, nombre_profils, entrees):
    """Valide les associations explicites outil/slot/filament et refuse les doublons."""
    vus, slots, par_outil = set(), set(), {}
    for declaration in outils:
        if not isinstance(declaration, dict) or set(declaration) - {
            "outil", "filament_id", "slot_declare", "confirmation_physique",
            "source_declaration", "date_declaration", "changements_attendus",
        }:
            raise ValueError("Champs déclaration inconnus ou invalides.")
        outil = declaration.get("outil")
        if (
            not isinstance(outil, str)
            or re.fullmatch(r"T[0-3]", outil) is None
            or int(outil[1]) >= nombre_profils
            or outil in vus
        ):
            raise ValueError("OutilT0..T3 absent du projet ou dupliqué.")
        vus.add(outil)
        slot = declaration.get("slot_declare")
        if type(slot) is not int or not 1 <= slot <= 4 or slot in slots:
            raise ValueError("Slot déclaré1..4 entier unique requis.")
        slots.add(slot)
        if declaration.get("filament_id") not in entrees:
            raise ValueError("Identifiant filament absent du registre unique.")
        par_outil[outil] = declaration
    return par_outil


def _depasse_constructeur(master, cle, valeur, non_verifie):
    """Compare à une limite constructeur déclarée ; une limite absente reste inconnue."""
    limite = master.get("constructeur", {}).get(cle, {}).get("value")
    if limite is None:
        raison = "Limite constructeur non vérifiée : " + cle
        if raison not in non_verifie:
            non_verifie.append(raison)
        return False
    return valeur > _nombre(limite)


def _valider_machine(settings, master, erreurs):
    """Contrôle d'abord modèle et buse, avant les déclarations outil/slot."""
    if settings.get("printer_model") not in charger()["machine"]["modeles_slicer"]:
        erreurs.append("Modèle projet absent ou différent de la S1 du maître.")
    buses = _liste(settings, "nozzle_diameter")
    if not buses or any(_nombre(x) != master["machine"]["buse_mm"]["value"] for x in buses):
        erreurs.append("Diamètre de buse absent ou contradictoire au maître.")
    if settings.get("nozzle_type") != master["machine"]["buse_matiere"]["value"]:
        erreurs.append("Matière de buse absente ou contradictoire au maître.")


def _valider_listes_reglages(settings, nombre_profils, suffixe):
    """Valide longueurs et types des listes projet après les déclarations explicites."""
    numeric_keys = ("nozzle_temperature", "nozzle_temperature_initial_layer", "filament_max_volumetric_speed")
    for key in (
        "filament_type", "filament_colour", *numeric_keys,
        *(cle + suffixe for cle in numeric_keys),
        "textured_plate_temp", "textured_plate_temp_initial_layer",
    ):
        valeurs = _liste(settings, key)
        if valeurs and len(valeurs) != nombre_profils:
            raise ValueError("Longueur ambiguë des valeurs " + key)
        if key not in ("filament_type", "filament_colour"):
            for valeur in valeurs:
                _nombre(valeur)
        elif any(not isinstance(valeur, str) for valeur in valeurs):
            raise ValueError(key + " : valeurs texte requises.")


def _valider_limites_globales(settings, master, suffixe, erreurs, non_verifie):
    """Compare les températures projet globales aux limites constructeur disponibles."""
    for key, limit in [
        ("nozzle_temperature", "temperature_buse_max_C"),
        ("nozzle_temperature_initial_layer", "temperature_buse_max_C"),
        ("textured_plate_temp", "temperature_plateau_max_C"),
        ("textured_plate_temp_initial_layer", "temperature_plateau_max_C"),
    ]:
        choisi = key + suffixe if key + suffixe in settings else key
        for valeur in _liste(settings, choisi):
            if _depasse_constructeur(master, limit, _nombre(valeur), non_verifie):
                erreurs.append(choisi + " dépasse le constructeur.")


def _empreinte_profil(settings, index, nombre_profils):
    """Calcule la signature des réglages embarqués pour un outil sans déduire d'affectation."""
    valeurs = {}
    for cle, valeur in settings.items():
        if cle.startswith(("filament_", "nozzle_temperature")):
            if (
                reglages.definition(cle)["type"] == "liste_filament"
                and isinstance(valeur, list)
                and len(valeur) == nombre_profils
            ):
                valeurs[cle] = reglages.valeur_brute_indice(settings, cle, index, lecteur="index_exact")
            else:
                valeurs[cle] = valeur
    return PM.empreinte_contexte(valeurs)


def _auditer_parametres_outil(item, settings, filament, index, suffixe, master, non_verifie):
    """Ajoute les limites de température et de débit du filament pour un outil déclaré."""
    for key, maxkey in [
        ("nozzle_temperature", "temperature_buse_max_C"),
        ("nozzle_temperature_initial_layer", "temperature_buse_max_C"),
        ("filament_max_volumetric_speed", None),
    ]:
        choisi = key + suffixe if key + suffixe in settings else key
        if not _liste(settings, choisi):
            item["non_verifie"].append(key + " absent ; aucun défaut inventé.")
            continue
        value = _nombre(reglages.valeur_indice(settings, choisi, index, lecteur="index_exact"))
        item[key] = value
        item[key + "_source"] = choisi
        if maxkey and _depasse_constructeur(master, maxkey, value, non_verifie):
            item["erreurs"].append(key + " dépasse le constructeur.")
        ref = filament.get("profil", {}).get(key + suffixe, filament.get("profil", {}).get(key))
        if isinstance(ref, list) and len(ref) == 1:
            if value > _nombre(ref[0]):
                item["erreurs"].append(key + " dépasse la référence configurée du filament ; pas un maximum physique.")
        else:
            item["non_verifie"].append("Limite configurée registre absente pour " + key)
    for key in ("textured_plate_temp", "textured_plate_temp_initial_layer"):
        if _liste(settings, key):
            temperature = _nombre(reglages.valeur_indice(settings, key, index, lecteur="index_exact"))
            if _depasse_constructeur(master, "temperature_plateau_max_C", temperature, non_verifie):
                item["erreurs"].append(key + " dépasse le constructeur.")


def _auditer_outil(index, profiles, bytool, settings, entrees, master, registre, suffixe, contexte, non_verifie):
    """Construit le rapport d'un outil déclaré, en conservant l'inconnu sans inventer de valeur."""
    outil = "T" + str(index)
    declaration = bytool.get(outil)
    item = {
        "outil": outil,
        "profil_nom": profiles[index],
        "profil_embarque_sha256": _empreinte_profil(settings, index, len(profiles)),
        "matiere_projet": reglages.valeur_brute_indice(settings, "filament_type", index, lecteur="index_exact"),
        "couleur_projet": reglages.valeur_brute_indice(settings, "filament_colour", index, lecteur="index_exact"),
        "declaration": copy.deepcopy(declaration),
        "confirmation_physique": {"statut": "NON VERIFIE"},
        "erreurs": [],
        "non_verifie": [],
    }
    if not declaration:
        item["non_verifie"].append("Affectation absente ; aucun slot inféré.")
    else:
        _auditer_declaration_outil(
            item, declaration, settings, entrees, master, registre, suffixe, index, contexte=contexte,
            non_verifie=non_verifie,
        )
    if not item["matiere_projet"]:
        item["non_verifie"].append("Matière projet absente.")
    if not item["couleur_projet"]:
        item["non_verifie"].append("Couleur projet absente ; aucune identité déduite.")
    return item


def _auditer_declaration_outil(
    item, declaration, settings, entrees, master, registre, suffixe, index, *, contexte, non_verifie,
):
    """Contrôle compatibilité, provenance et réglages du filament explicitement relié."""
    filament = entrees[declaration["filament_id"]]
    item["compatibilite"] = PM.compatibilite_filament(
        master, declaration["filament_id"], registre, declaration["slot_declare"]
    )
    if item["compatibilite"]["statut"] == "FAIL":
        item["erreurs"].extend(item["compatibilite"]["alertes"])
    elif item["compatibilite"]["alertes"]:
        item["non_verifie"].extend(item["compatibilite"]["alertes"])
    if not declaration.get("source_declaration") or not declaration.get("date_declaration"):
        item["non_verifie"].append("Source/date déclaration manquante.")
    expected_type = filament.get("profil", {}).get("filament_type")
    if (
        item["matiere_projet"]
        and isinstance(expected_type, list)
        and len(expected_type) == 1
        and item["matiere_projet"] != expected_type[0]
    ):
        item["erreurs"].append("Matière embarquée différente du registre.")
    elif not expected_type:
        item["non_verifie"].append("Matière du registre non structurée ; aucune déduction depuis le nom.")
    _auditer_parametres_outil(item, settings, filament, index, suffixe, master, non_verifie)
    item["confirmation_physique"] = _attestation(declaration.get("confirmation_physique"), contexte)


def auditer_affectations(source3mf, declarations, maitre=None, registre=None):
    """Audite les déclarations et un 3MF local (mm, °C, mm³/s) ; statut DECLARATIONS_ATTESTEES, FAIL ou NON VERIFIE.

    Les cases ACE sont exactes ; aucune confirmation physique ni commande machine n'est produite.
    """
    (
        source, sha_source, brut, settings, declaration_path, sha_declarations, dec,
        master, registre_path, reg, entries,
    ) = _charger_contexte(source3mf, declarations, maitre, registre)
    profiles = _liste(settings, "filament_settings_id")
    if not 1 <= len(profiles) <= 4 or any(not isinstance(x, str) or not x.strip() for x in profiles):
        raise ValueError("Un à quatre profils embarqués explicites requis.")
    nombre_profils = len(profiles)
    suffixe = "_BRASS" if settings.get("nozzle_type") == "brass" else "_HS"
    erreurs, non_verifie = [], []
    _valider_machine(settings, master, erreurs)
    bytool = _valider_declarations(dec["outils"], nombre_profils, entries)
    _valider_listes_reglages(settings, nombre_profils, suffixe)
    _valider_limites_globales(settings, master, suffixe, erreurs, non_verifie)
    declared = [{k: v for k, v in d.items() if k != "confirmation_physique"} for d in dec["outils"]]
    contexte = PM.empreinte_contexte(
        projet_sha256=sha_source,
        profils=settings,
        declarations=declared,
        maitre=master,
        registre=reg,
        code_sha256=_sha(__file__),
    )
    results = []
    for index in range(nombre_profils):
        item = _auditer_outil(
            index, profiles, bytool, settings, entries, master, reg, suffixe, contexte, non_verifie,
        )
        erreurs.extend(item["outil"] + ": " + erreur for erreur in item["erreurs"])
        non_verifie.extend(item["outil"] + ": " + detail for detail in item["non_verifie"])
        results.append(item)
    if _sha(source) != sha_source or (declaration_path and _sha(declaration_path) != sha_declarations):
        raise ValueError("Source ou déclarations modifiées pendant audit.")
    physique = all(r["confirmation_physique"]["statut"] == "ATTESTATION_HUMAINE_DATEE" for r in results)
    return {
        "schema": 1,
        "type": "affectations-ace-declarees",
        "source": str(source),
        "source_sha256": sha_source,
        "configuration_sha256": hashlib.sha256(brut).hexdigest(),
        "contexte_sha256": contexte,
        "maitre_sha256": PM.empreinte_contexte(master),
        "registre_sha256": PM.empreinte_contexte(reg),
        "outils": results,
        "erreurs": erreurs,
        "non_verifie": non_verifie,
        "statut": "FAIL" if erreurs else "NON VERIFIE" if non_verifie or not physique else "DECLARATIONS_ATTESTEES",
        "physique": "ATTESTATION_HUMAINE_DATEE" if physique else "NON VERIFIE",
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
