"""Entrées du contrôle machine : profil maître, réglages typés et métadonnées de plaque."""

import collections
import hashlib
import json
import math

SUFFIXES = {"brass": "_BRASS", "hardened_steel": "_HS"}


def _sha(data):
    """Empreinte SHA-256 hexadécimale des octets lus."""
    return hashlib.sha256(data).hexdigest()


def _positif(value):
    """Convertit une limite native strictement positive, sinon None."""
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def _limites(maitre):
    """Résout les limites maître en unités natives et leur provenance SHA, sans machine."""
    # Un retour flat de limites_configurees peut être transmis directement par
    # le moteur : aucune deuxième résolution ni constante machine locale.
    if isinstance(maitre, dict) and "vitesse_xy_mm_s" in maitre:
        for key, value in maitre.items():
            if key.endswith(("_mm_s", "_mm_s2", "_max_c")) and value is not None and _positif(value) is None:
                raise ValueError("limite maître non finie/positive : " + key)
        return dict(maitre), {
            "mode": "limites aplaties fournies",
            "sha256": _sha(json.dumps(maitre, sort_keys=True, default=str).encode()),
        }
    from benchab.machine.profil_maitre import charger_machine, limites_configurees

    profil = charger_machine(maitre)
    limits = limites_configurees(profil)
    for key, target in [
        ("temperature_buse_max_C", "temperature_buse_constructeur_max_c"),
        ("temperature_plateau_max_C", "temperature_plateau_constructeur_max_c"),
    ]:
        fact = profil.get("constructeur", {}).get(key, {})
        if _positif(fact.get("value")):
            limits[target] = float(fact["value"])
    return limits, {
        "mode": "profil maître résolu",
        "sha256": _sha(json.dumps(profil, sort_keys=True, default=str).encode()),
    }


def _valeur(settings, key, index, suffix=""):
    """Lit un réglage natif d’un slot explicite, sans diffusion de vecteur."""
    selected = key + suffix if suffix and key + suffix in settings else key
    value = settings.get(selected)
    if isinstance(value, list):
        value = value[index] if 0 <= index < len(value) else None
    # Une valeur scalaire est globale, un tableau incomplet n'est jamais
    # prolongé avec la première valeur d'un autre filament.
    return value, "project_settings." + selected + (
        "[" + str(index) + "]" if isinstance(settings.get(selected), list) else ""
    )


def _reglages_filaments(settings, machine, limites_filaments, rapport, note):
    """Ajoute provenance, limites et routage déclaré au rapport, sans prouver l’affectation physique."""
    nozzle_type = str(settings.get("nozzle_type", "")).lower()
    suffix = SUFFIXES.get(nozzle_type, "")
    if not suffix and any(k.endswith(("_BRASS", "_HS")) for k in settings):
        note("variante de buse", "NON VÉRIFIÉ", "type de buse inconnu : variantes non attribuables")
    material = {"brass": "laiton", "hardened_steel": "acier_trempe"}.get(nozzle_type)
    expected_material = machine.get("buse_matiere")
    aliases = {"brass": "laiton", "hardened_steel": "acier_trempe", "acier trempé": "acier_trempe"}
    expected_material = aliases.get(expected_material, expected_material)
    if material and expected_material and material != expected_material:
        note("matière buse", "FAIL", f"buse embarquée {material}, maître {expected_material}")
    nozzle, _ = _valeur(settings, "nozzle_diameter", 0)
    if (
        _positif(nozzle)
        and _positif(machine.get("buse_mm"))
        and not math.isclose(float(nozzle), float(machine["buse_mm"]), abs_tol=1e-9)
    ):
        note("diamètre buse", "FAIL", f"{nozzle} mm embarqué, maître {machine['buse_mm']} mm")
    count = max(
        [
            len(v)
            for k, v in settings.items()
            if k in ("filament_type", "filament_settings_id", "filament_max_volumetric_speed")
            and isinstance(v, list)
        ]
        or [0]
    )
    mapping = {str(k): dict(v) for k, v in (limites_filaments or {}).items()}
    rapport["entrees"]["limites_filaments_sha256"] = _sha(json.dumps(mapping, sort_keys=True, default=str).encode())
    filaments = {}
    for ident in set(mapping) | {str(i + 1) for i in range(count)}:
        if not ident.isdigit() or int(ident) < 1:
            raise ValueError("identifiant filament positif 1-based requis")
        idx = int(ident) - 1
        supplied = mapping.get(ident, {})
        cap, cap_source = _valeur(settings, "filament_max_volumetric_speed", idx, suffix)
        diameter, dia_source = _valeur(settings, "filament_diameter", idx)
        temp, temp_source = _valeur(settings, "nozzle_temperature", idx, suffix)
        first, first_source = _valeur(settings, "nozzle_temperature_initial_layer", idx, suffix)
        filaments[ident] = {
            "plafond_mm3_s": _positif(supplied.get("plafond_mm3_s", cap)),
            "diametre_mm": _positif(supplied.get("diametre_mm", diameter)),
            "temperature_c": _positif(supplied.get("temperature_c", temp)),
            "temp_1re_c": _positif(supplied.get("temp_1re_c", first)),
            "id": supplied.get("id"),
            "ace_declare": supplied.get("ace_declare"),
            "source": supplied.get(
                "source",
                {
                    "plafond": cap_source,
                    "diametre": dia_source,
                    "temperature": temp_source,
                    "temp_1re": first_source,
                },
            ),
            "variante": suffix or None,
        }
    rapport["filaments"] = filaments
    rapport["ace"] = {
        "statut": "NON VÉRIFIÉ",
        "declarations": {k: v["ace_declare"] for k, v in filaments.items()},
        "raison": "Routage physique non prouvé par profil, déclaration ou sélection T.",
    }
    return filaments


def _metadonnees_archive(archive):
    """Lit les métadonnées 3MF du contexte courant, sans déduire de trajectoire ni modifier l’archive."""
    painting = any(
        any(token in archive.lire(n) for token in (b"paint_color", b"mmu_segmentation"))
        for n in archive.noms()
        if n.endswith(".model")
    )
    allowed_objects = {}
    plate_objects = collections.defaultdict(set)
    if "Metadata/model_settings.config" in archive.noms():
        model = archive.xml("Metadata/model_settings.config")
        identities = {}
        for obj in model.findall("object"):
            md = {m.get("key"): m.get("value") for m in obj.findall("metadata")}
            if md.get("name"):
                identities[obj.get("id")] = md["name"]
                tools = (
                    {int(md["extruder"]) - 1}
                    if md.get("extruder", "").isdigit() and int(md["extruder"]) > 0
                    else set()
                )
                for part in obj.findall("part"):
                    pm = {m.get("key"): m.get("value") for m in part.findall("metadata")}
                    if pm.get("extruder", "").isdigit() and int(pm["extruder"]) > 0:
                        tools.add(int(pm["extruder"]) - 1)
                allowed_objects[md["name"]] = tools
        for pl in model.findall("plate"):
            md = {m.get("key"): m.get("value") for m in pl.findall("metadata")}
            k = md.get("plater_id")
            if k and k.isdigit():
                for inst in pl.findall("model_instance"):
                    im = {m.get("key"): m.get("value") for m in inst.findall("metadata")}
                    if im.get("object_id") in identities:
                        plate_objects[int(k)].add(identities[im["object_id"]])
    slice_filaments = {}
    if "Metadata/slice_info.config" in archive.noms():
        for pl in archive.xml("Metadata/slice_info.config").findall("plate"):
            md = {m.get("key"): m.get("value") for m in pl.findall("metadata")}
            if str(md.get("index", "")).isdigit():
                slice_filaments[int(md["index"])] = {f.get("id") for f in pl.findall("filament")}
    return painting, allowed_objects, plate_objects, slice_filaments
