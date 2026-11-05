"""Profil maître versionné, lu localement sans accès au slicer ou à l'imprimante.

Les limites configurées ne sont ni des maxima physiques validés ni des choix de
projet. Les profils et registres de projet sont toujours fournis explicitement.
"""

import argparse
import copy
import hashlib
import json
import math
import re
from pathlib import Path

from benchab import reglages as schema_reglages
from benchab.machine.configuration import charger as charger_configuration

RACINE = Path(__file__).resolve().parents[2]
CANON = {
    "vitesse_xy_mm_s": ("machine_max_speed_x", "machine_max_speed_y"),
    "vitesse_z_mm_s": ("machine_max_speed_z",),
    "vitesse_e_mm_s": ("machine_max_speed_e",),
    "acceleration_xy_mm_s2": ("machine_max_acceleration_x", "machine_max_acceleration_y"),
    "acceleration_z_mm_s2": ("machine_max_acceleration_z",),
    "acceleration_e_mm_s2": ("machine_max_acceleration_e",),
    "acceleration_extruding_mm_s2": ("machine_max_acceleration_extruding",),
    "acceleration_travel_mm_s2": ("machine_max_acceleration_travel",),
    "acceleration_retracting_mm_s2": ("machine_max_acceleration_retracting",),
}
MATERIAUX_BUSE = {"brass", "hardened_steel", "stainless_steel"}
TYPES_PREUVE = {
    "constructeur",
    "configuration",
    "mesure",
    "validation",
    "observation",
    "hypothese",
    "declaration_utilisateur",
}


def _json(path):
    with Path(path).open(encoding="utf-8") as f:
        return json.load(f)


def _sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for bloc in iter(lambda: f.read(1048576), b""):
            h.update(bloc)
    return h.hexdigest()


def _nombre(value, label, positif=True):
    if isinstance(value, bool):
        raise ValueError(label + " : booléen interdit.")
    try:
        nombre = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(label + " : nombre requis.") from exc
    if not math.isfinite(nombre) or (positif and nombre <= 0):
        raise ValueError(label + " : nombre positif fini requis.")
    return nombre


def _unite(cle):
    if "acceleration" in cle:
        return "mm/s2"
    if cle.startswith("machine_max_speed") or cle.startswith("machine_max_jerk"):
        return "mm/s"
    if cle in ("nozzle_diameter", "printable_height"):
        return "mm"
    return None


def _preuve(fait, sources, unite=None):
    champs = {
        "value",
        "unit",
        "type",
        "source_ids",
        "date_reference",
        "version_reference",
        "confidence",
        "conditions",
        "verification_status",
    }
    if not isinstance(fait, dict) or not champs <= fait.keys():
        raise ValueError("Donnée sans enveloppe de provenance complète.")
    if fait["type"] not in TYPES_PREUVE or not isinstance(fait["unit"], str):
        raise ValueError("Type ou unité de preuve invalide.")
    if unite and fait["unit"] != unite:
        raise ValueError("Unité contradictoire : " + str(fait["unit"]) + " au lieu de " + unite)
    if not isinstance(fait["source_ids"], list) or any(s not in sources for s in fait["source_ids"]):
        raise ValueError("Source de preuve inconnue.")
    if fait["value"] is not None and not fait["source_ids"]:
        raise ValueError("Une valeur connue exige une source.")
    if not fait["date_reference"] and not fait["version_reference"]:
        raise ValueError("Date ou version de référence requise.")
    if not fait["conditions"] or not fait["confidence"] or not fait["verification_status"]:
        raise ValueError("Conditions, confiance et statut explicites requis.")
    if fait["value"] is None and fait["verification_status"] != "NON_VERIFIE":
        raise ValueError("Une valeur inconnue doit rester NON_VERIFIE.")


def _valider(profil, dossier=None):
    if not isinstance(profil, dict) or profil.get("schema") != 1 or isinstance(profil.get("schema"), bool):
        raise ValueError("Schéma maître non pris en charge.")
    if isinstance(profil.get("version"), bool) or not isinstance(profil.get("version"), int) or profil["version"] < 1:
        raise ValueError("Version maître invalide.")
    if not isinstance(profil.get("configuration_materielle_id"), str) or not profil["configuration_materielle_id"]:
        raise ValueError("Version de configuration matérielle absente.")
    sources = profil.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise ValueError("Sources du maître absentes.")
    for source in sources.values():
        if not isinstance(source, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(source.get("sha256", ""))):
            raise ValueError("Empreinte source invalide.")
        if not source.get("date_reference") or not source.get("origine"):
            raise ValueError("Source sans date ou origine.")
        if source.get("snapshot"):
            rel = Path(source["snapshot"])
            if rel.is_absolute() or ".." in rel.parts:
                raise ValueError("Snapshot hors périmètre maître.")
            if dossier is not None:
                p = dossier / rel
                if (
                    p.is_symlink()
                    or not p.resolve().is_relative_to(dossier.resolve())
                    or not p.is_file()
                    or _sha(p) != source["sha256_snapshot"]
                ):
                    raise ValueError("Snapshot maître absent, symbolique ou empreinte divergente.")
    hardware = profil.get("machine", {})
    for cle, unit in (
        ("modele", "texte"),
        ("cinematique", "texte"),
        ("volume_mm", "mm"),
        ("buse_mm", "mm"),
        ("buse_matiere", "texte"),
        ("chargeur", "texte"),
    ):
        _preuve(hardware.get(cle), sources, unit)
    if hardware["cinematique"]["value"] != "corexy":
        raise ValueError("Cinématique contradictoire avec la Kobra S1.")
    if hardware["modele"]["value"] not in ("Anycubic Kobra S1", "Anycubic Kobra S1 Combo"):
        raise ValueError("Ce maître concerne la Kobra S1 standard, jamais S1 Max/Kobra3.")
    buse = _nombre(hardware["buse_mm"]["value"], "buse_mm")
    matiere = hardware["buse_matiere"]["value"]
    if matiere not in MATERIAUX_BUSE:
        raise ValueError("Matériau de buse non pris en charge.")
    volume = hardware["volume_mm"]["value"]
    if not isinstance(volume, list) or len(volume) != 3:
        raise ValueError("Volume XYZ requis.")
    for n in volume:
        _nombre(n, "volume_mm")
    configs = profil.get("configurations")
    if not isinstance(configs, dict) or not configs or profil.get("configuration_par_defaut") not in configs:
        raise ValueError("Configuration par défaut inconnue.")
    for config in configs.values():
        if not isinstance(config, dict) or not isinstance(config.get("parametres"), dict):
            raise ValueError("Configuration slicer invalide.")
        if config.get("disponible") is not True:
            if config["parametres"]:
                raise ValueError("Configuration absente contenant des valeurs inventées.")
            continue
        for cle, fait in config["parametres"].items():
            _preuve(fait, sources, _unite(cle))
            if fait["type"] != "configuration":
                raise ValueError("Une limite configurée exige un fait de configuration.")
        valeurs = {k: v["value"] for k, v in config["parametres"].items()}
        snapshot_id = config.get("snapshot_source_id")
        if snapshot_id not in sources:
            raise ValueError("Snapshot de configuration absent.")
        if dossier is not None:
            snapshot = _json(dossier / sources[snapshot_id]["snapshot"])
            if snapshot.get("valeurs") != valeurs:
                raise ValueError("Valeurs de configuration contradictoires avec le snapshot.")
        if "printer_model" in valeurs and valeurs["printer_model"] != "Anycubic Kobra S1":
            raise ValueError("Modèle slicer contradictoire.")
        if valeurs.get("nozzle_diameter") is not None:
            diam = valeurs["nozzle_diameter"]
            diam = diam if isinstance(diam, list) else [diam]
            if len(diam) != 1 or not math.isclose(_nombre(diam[0], "nozzle_diameter"), buse, abs_tol=1e-9):
                raise ValueError("Diamètre de buse contradictoire avec la version matérielle.")
        if valeurs.get("printable_height") is not None and not math.isclose(
            _nombre(valeurs["printable_height"], "printable_height"), float(volume[2]), abs_tol=1e-6
        ):
            raise ValueError("Volume Z contradictoire avec le matériel.")
        if valeurs.get("printable_area"):
            try:
                points = [
                    tuple(_nombre(x, "printable_area", False) for x in point.split("x"))
                    for point in valeurs["printable_area"]
                ]
                if len(points) < 3 or any(len(pt) != 2 for pt in points):
                    raise ValueError("Polygone XY invalide.")
                dimensions = [max(pt[a] for pt in points) - min(pt[a] for pt in points) for a in (0, 1)]
                if any(not math.isclose(dimensions[a], float(volume[a]), abs_tol=1e-6) for a in (0, 1)):
                    raise ValueError("Volume XY contradictoire avec le matériel.")
            except (AttributeError, TypeError) as exc:
                raise ValueError("Polygone XY invalide.") from exc
        if valeurs.get("nozzle_type") not in (None, matiere):
            raise ValueError("Matériau de buse contradictoire.")
        for cle, value in valeurs.items():
            if cle.startswith(("machine_max_speed_", "machine_max_acceleration_")):
                if not isinstance(value, list) or not value:
                    raise ValueError("Tableau de modes requis : " + cle)
                for n in value:
                    _nombre(n, cle)
    for nom, fait in profil.get("constructeur", {}).items():
        _preuve(fait, sources, "mm/s2" if "acceleration" in nom else "mm/s" if "vitesse" in nom else "degC")
        if fait["type"] != "constructeur":
            raise ValueError("Annonce constructeur avec type contradictoire.")
        if fait["value"] is not None:
            _nombre(fait["value"], nom)
    for recomm, maximum in (
        ("vitesse_recommandee_mm_s", "vitesse_max_mm_s"),
        ("acceleration_recommandee_mm_s2", "acceleration_max_mm_s2"),
    ):
        a = profil.get("constructeur", {}).get(recomm, {}).get("value")
        b = profil.get("constructeur", {}).get(maximum, {}).get("value")
        if a is not None and b is not None and _nombre(a, recomm) > _nombre(b, maximum):
            raise ValueError("Annonce constructeur recommandée supérieure au maximum.")
    for nom, fait in profil.get("maxima_valides", {}).items():
        _preuve(
            fait,
            sources,
            {"vitesse_xy_mm_s": "mm/s", "acceleration_xy_mm_s2": "mm/s2", "debit_tete_mm3_s": "mm3/s"}.get(nom),
        )
        if fait["value"] is not None:
            _nombre(fait["value"], nom)
            if fait["type"] != "validation" or not any(
                sources[i].get("type") in ("mesure", "validation") and sources[i].get("protocole")
                for i in fait["source_ids"]
            ):
                raise ValueError("Un maximum validé exige une preuve et un protocole de validation dédiés.")
    return profil


def charger_machine(path=None):
    """Charge un maître et ses snapshots figés ; n'interroge jamais les actifs."""
    if isinstance(path, dict):
        copie = copy.deepcopy(path)
        dossier = Path(copie["_dossier_sources"]) if copie.get("_dossier_sources") else None
        return _valider(copie, dossier)
    if path is None:
        config = charger_configuration()
        configured = config.get("machine", {}).get("profil_maitre")
        if not configured:
            raise ValueError("Profil maître absent : configurez machine.profil_maitre ou passez un profil explicite.")
        path = configured
    fichier = Path(path)
    if not fichier.is_absolute():
        fichier = RACINE / fichier
    if fichier.is_symlink():
        raise ValueError("Maître symbolique refusé.")
    data = _json(fichier)
    data["_dossier_sources"] = str(fichier.parent.resolve())
    return _valider(data, fichier.parent)


def limites_configurees(profil, configuration_id=None, mode_index=0):
    """Limites flat canoniques ; indice explicite, jamais max(tableau).

    XY = minimum des plafonds X et Y si les deux sont connus. Cet agrégat
    conservateur n'affirme pas que les axes ont la même limite physique.
    """
    profil = charger_machine(profil)
    if isinstance(mode_index, bool) or not isinstance(mode_index, int) or mode_index < 0:
        raise ValueError("mode_index doit être un entier positif ou nul.")
    ident = configuration_id or profil["configuration_par_defaut"]
    if ident not in profil["configurations"]:
        raise ValueError("configuration_id inconnue : " + str(ident))
    config = profil["configurations"][ident]
    if config.get("disponible") is not True:
        raise ValueError("Configuration indisponible : " + ident)
    params = config["parametres"]
    out = {}
    nv = []
    src = {}
    for canon, cles in CANON.items():
        nombres = []
        ids = []
        for cle in cles:
            fait = params.get(cle)
            if fait is None or fait["value"] is None:
                nv.append(canon + " : " + cle + " absent")
                nombres.append(None)
                continue
            tableau = fait["value"]
            if mode_index >= len(tableau):
                raise ValueError("mode_index absent de " + cle)
            nombres.append(_nombre(tableau[mode_index], cle))
            ids += fait["source_ids"]
        out[canon] = min(nombres) if nombres and None not in nombres else None
        src[canon] = {
            "configuration_id": ident,
            "source_ids": sorted(set(ids)),
            "cles_brutes": list(cles),
            "mode_index": mode_index,
        }
    for cle in ("buse_mm", "buse_matiere", "volume_mm"):
        fait = profil["machine"][cle]
        out[cle] = copy.deepcopy(fait["value"])
        src[cle] = {
            "configuration_id": profil["configuration_materielle_id"],
            "source_ids": fait["source_ids"],
            "cles_brutes": [cle],
            "mode_index": None,
        }
    for canon, nom in (
        ("vitesse_constructeur_max_mm_s", "vitesse_max_mm_s"),
        ("acceleration_constructeur_max_mm_s2", "acceleration_max_mm_s2"),
        ("acceleration_constructeur_recommandee_mm_s2", "acceleration_recommandee_mm_s2"),
    ):
        fait = profil.get("constructeur", {}).get(nom)
        out[canon] = fait["value"] if fait else None
        if out[canon] is None:
            nv.append(canon + " : annonce constructeur inconnue")
        src[canon] = {
            "configuration_id": None,
            "source_ids": fait["source_ids"] if fait else [],
            "cles_brutes": [nom],
            "mode_index": None,
        }
    nv += [
        "Maxima physiques non mesurés ; limites de profil seulement.",
        "Correspondance physique ACE et état firmware actuel non interrogés.",
    ]
    out.update(non_verifie=nv, sources=src)
    return out


def resoudre_preset(nom, categorie="machine", sources=None):
    """Résout uniquement les parents déclarés dans les sources explicitement fournies.

    sources : liste ordonnée de dossiers de JSON ou dict nom -> dict JSON/Path.
    Les premiers dossiers ont priorité ; aucune recherche réseau ou implicite des
    profils installés. Retour valeurs typées (unités natives), origine et parents ;
    aucun réglage inconnu transformé, aucune écriture de profil.
    """
    if categorie not in ("machine", "process", "filament") or not isinstance(nom, str) or not nom:
        raise ValueError("Nom et catégorie de preset invalides.")
    if not isinstance(sources, (dict, list, tuple)):
        raise ValueError("Sources de presets explicites requises (dict ou liste de dossiers).")

    def chercher(name):
        if not isinstance(name, str) or not name or Path(name).name != name or name in (".", "..") or "\\" in name:
            raise ValueError("Nom de preset hors périmètre.")
        if isinstance(sources, dict):
            value = sources.get(name)
            if value is None:
                return None, None, None
            if isinstance(value, dict):
                raw = copy.deepcopy(value)
                return (
                    raw,
                    name,
                    hashlib.sha256(
                        json.dumps(raw, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
                    ).hexdigest(),
                )
            p = Path(value)
            contenu = p.read_bytes()
            return schema_reglages.decoder(contenu), str(p.resolve()), hashlib.sha256(contenu).hexdigest()
        for folder in sources:
            p = Path(folder) / (name + ".json")
            if p.is_file():
                contenu = p.read_bytes()
                return schema_reglages.decoder(contenu), str(p.resolve()), hashlib.sha256(contenu).hexdigest()
        return None, None, None

    def visiter(name, vus):
        if name in vus:
            raise ValueError("Cycle d’héritage : " + " -> ".join([*vus, name]))
        raw, origine, empreinte = chercher(name)
        if raw is None:
            return {"valeurs": {}, "origine_par_cle": {}, "heritages": [], "manquants": [name]}
        if not isinstance(raw, dict) or raw.get("type", categorie) != categorie or raw.get("name", name) != name:
            raise ValueError("Identité/catégorie du preset incohérente : " + name)
        parents = raw.get("inherits") or []
        parents = [parents] if isinstance(parents, str) else parents
        if not isinstance(parents, list) or any(not isinstance(x, str) or not x for x in parents):
            raise ValueError("Héritage invalide : " + name)
        ret = {"valeurs": {}, "origine_par_cle": {}, "heritages": [], "manquants": []}
        for parent in parents:
            r = visiter(parent, [*vus, name])
            for cle in ("valeurs", "origine_par_cle"):
                ret[cle].update(r[cle])
            for cle in ("heritages", "manquants"):
                ret[cle] += r[cle]
        securise = {
            k: (
                "<MASQUE>"
                if any(t in k.lower() for t in ("apikey", "api_key", "password", "token", "secret")) and v
                else v
            )
            for k, v in raw.items()
        }
        ret["valeurs"].update(securise)
        ret["origine_par_cle"].update({k: origine for k in raw})
        ret["heritages"].append(
            {
                "name": name,
                "source": origine,
                "version": raw.get("version"),
                "sha256": empreinte,
                "empreinte_type": "JSON canonique en mémoire"
                if isinstance(sources, dict) and isinstance(sources.get(name), dict)
                else "octets du fichier source",
            }
        )
        ret["valeurs"] = schema_reglages.Reglages(ret["valeurs"])
        return ret

    return visiter(nom, [])


def compatibilite_filament(profil, filament_id, registre=None, ace_declare=None):
    """Lit REFERENCES par id ; aucun débit/température/stock dupliqué dans le maître."""
    profil = charger_machine(profil)
    if registre is None:
        raise ValueError("Registre filaments explicite requis ; aucune donnée de projet n'est chargée implicitement.")
    data = _json(registre) if isinstance(registre, (str, Path)) else copy.deepcopy(registre)
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("filaments"), list)
        or ("schema" in data and (data["schema"] != 1 or isinstance(data["schema"], bool)))
    ):
        raise ValueError("Registre filaments invalide.")
    entries = [f for f in data["filaments"] if isinstance(f, dict) and f.get("id") == filament_id]
    if len(entries) != 1:
        raise ValueError("Identifiant filament absent ou dupliqué : " + str(filament_id))
    f = entries[0]
    alertes = []
    nv = []
    bloque = False
    stock = f.get("stock")
    if isinstance(stock, dict) and (
        stock.get("etat") in ("épuisée", "commandée non reçue") or stock.get("bobines_disponibles") == 0
    ):
        alertes.append("Bobine non disponible selon le stock déclaré.")
        bloque = True
    elif not isinstance(stock, dict):
        nv.append("Disponibilité physique non structurée dans le registre.")
    abrasif = f.get("abrasif")
    if abrasif is not None and not isinstance(abrasif, bool):
        raise ValueError("abrasif doit être booléen si déclaré.")
    if abrasif is True and profil["machine"]["buse_matiere"]["value"] == "brass":
        alertes.append("Abrasivité déclarée : usure de la buse laiton à examiner ; compatibilité physique non validée.")
    elif abrasif is None:
        nv.append("Abrasivité non structurée ; aucune déduction depuis le nom commercial.")
    for regle in profil.get("compatibilite_notices", []):
        if filament_id in regle.get("filament_ids", []) and profil["machine"]["buse_matiere"]["value"] in regle.get(
            "buse_matieres", []
        ):
            alertes.append(regle["message"])
    if ace_declare is not None and (
        isinstance(ace_declare, bool) or not isinstance(ace_declare, int) or not 1 <= ace_declare <= 4
    ):
        raise ValueError("ACE déclaré : entier de1 à4 requis pour cet ACE2Pro.")
    decl = f.get("emplacement_ace_declare", {})
    if ace_declare is not None and decl.get("numero") is not None and decl["numero"] != ace_declare:
        alertes.append("ACE déclaré contradictoire avec le registre.")
        bloque = True
    nv.append("Correspondance physique ACE non lue ; une déclaration n’est pas une mesure.")
    return {
        "statut": "FAIL" if bloque else "WARN" if alertes else "NON VERIFIE" if nv else "PASS",
        "filament_id": filament_id,
        "ace_declare": ace_declare,
        "alertes": alertes,
        "non_verifie": nv,
        "source": {
            "registre": str(registre) if isinstance(registre, (str, Path)) else "registre fourni explicitement",
            "fiche": f.get("fiche"),
            "date_controle": f.get("date_controle"),
        },
    }


def empreinte_contexte(*valeurs, **noms):
    """SHA reproductible de données/versions et, pour Path, du contenu réel."""

    def normaliser(v):
        if isinstance(v, Path):
            return {"chemin": str(v.resolve()), "sha256": _sha(v)}
        if isinstance(v, dict):
            if len({str(k) for k in v}) != len(v):
                raise ValueError("Clés de contexte ambiguës après sérialisation.")
            return {str(k): normaliser(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [normaliser(x) for x in v]
        if isinstance(v, float) and not math.isfinite(v):
            raise ValueError("Contexte non fini.")
        if v is None or isinstance(v, (str, int, float, bool)):
            return v
        raise ValueError("Type de contexte non sérialisable.")

    payload = json.dumps(
        normaliser({"args": valeurs, "noms": noms, "code_profil_maitre_sha256": _sha(Path(__file__))}),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--profil", type=Path)
    p.add_argument("--configuration-id")
    p.add_argument("--mode-index", type=int, default=0)
    args = p.parse_args(argv)
    try:
        result = limites_configurees(charger_machine(args.profil), args.configuration_id, args.mode_index)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        p.exit(2, "ERREUR : " + str(exc) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
