"""
Outils communs de préparation/tranchage (nom historique conservé pour les imports) : écrire un 3MF multi-plaques,
le trancher avec le moteur externe déclaré (Anycubic Slicer Next, retour décidé le 30/10/2026),
lire les résultats. Repris du coffre V4 (preparer_projet.py / trancher_projet.py), rendu générique.

Compatibilité conservée depuis Anycubic (docs/guides/slicer-anycubic.md) :
  - pas des plaques : interface 314,4 mm, ligne de commande 300 mm -> positions écrites dans le repère de
l'interface,
    chaque plaque rangée dans une zone valable avec les deux pas ;
  - colonnes de la grille : sqrt(n) arrondi, +1 s'il est dépassé ;
  - ancien plantage sur les plaques nommées -> noms écrits après tranchage ;
  - les variantes de températures _BRASS/_HS et le plafond par objet sont des précautions historiques Anycubic,
    pas des comportements établis d'Orca. Les réglages effectifs restent à contrôler dans le G-code exporté.
Le 30/10/2026, deux plaques nommées ont été tranchées par Orca 2.5.0-beta aux deux pas ;
ce contrôle de format et de placement ne valide pas l'impression physique.
Aucune impression n'est lancée, aucun profil installé n'est modifié.
"""

import copy
import csv
import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path
from xml.etree import ElementTree as E
from zipfile import ZIP_DEFLATED, ZipFile

from benchab import integrite_3mf
from benchab import reglages as schema_reglages
from benchab.fabrication.reglages import normaliser_heritages
from benchab.fabrication.trancher import restaurer_noms, trancher_fichier
from benchab.gcode.lecture import lire_texte_gcode_3mf, membres_gcode_3mf
from benchab.machine.configuration import charger
from benchab.qualite.empreintes import sha256_fichier

_PLACEMENT = charger()["placement"]
_ZONE_MIN_X, _ZONE_MIN_Y = _PLACEMENT["zone_min_mm"]
_ZONE_MAX_X, _ZONE_MAX_Y = _PLACEMENT["zone_max_mm"]
PAS_INTERFACE = float(_PLACEMENT["pas_interface_mm"])
PAS_CLI = float(_PLACEMENT["pas_cli_mm"])
NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
PROD = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"


def colonnes(n):
    r = math.sqrt(n)
    return int(round(r)) + (1 if r > round(r) else 0)


def zone(k, n, cli=False):
    """
    Zone utile (x0, x1, y0, y1) de la plaque k (0-based), valable avec les deux pas (ou seulement la ligne de
    commande : cli).
    """
    if cli:
        return _ZONE_MIN_X, _ZONE_MAX_X, _ZONE_MIN_Y, _ZONE_MAX_Y
    c = colonnes(n)
    col, row = k % c, k // c
    d = PAS_INTERFACE - PAS_CLI
    return _ZONE_MIN_X, _ZONE_MAX_X - d * col, _ZONE_MIN_Y + d * row, _ZONE_MAX_Y


def ranger(items, k, n, gap, cli=False):
    """
    Rangement en lignes (plus hautes d'abord). items : dicts avec 'w', 'h' (mm). Ajoute x, y. Lève si ça ne tient
    pas.
    """
    x0, x1, y0, y1 = zone(k, n, cli)
    rows = []
    for q in sorted(items, key=lambda i: (-i["h"], -i["w"], i["name"])):
        for r in rows:
            if q["h"] <= r["height"] and r["x"] + q["w"] <= x1:
                q.update(x=r["x"], y=r["y"])
                r["x"] += q["w"] + gap
                break
        else:
            y = max((r["y"] + r["height"] + gap for r in rows), default=y0)
            if y + q["h"] > y1 or x0 + q["w"] > x1:
                raise ValueError(f"plaque {k + 1} : {q['name']} ne tient pas")
            rows.append({"y": y, "height": q["h"], "x": x0 + q["w"] + gap})
            q.update(x=x0, y=y)
    return items


def empreinte_geometrie(mesh, max_faces=3_000_000):
    """
    Identité des triangles orientés en mm, quantifiée à 0,00001 mm.

        Invariante à l'ordre des sommets/faces, pas à la triangulation ni au sens
        des faces. Ce n'est pas une preuve d'identité exacte sous le quantum.

    """
    import numpy as np

    vertices = np.asarray(mesh.vertices, dtype=float)
    faces = np.asarray(mesh.faces)
    if (
        vertices.ndim != 2
        or vertices.shape[1] != 3
        or not np.isfinite(vertices).all()
        or np.abs(vertices).max(initial=0) > 1e7
    ):
        raise ValueError("Coordonnées géométriques invalides ou hors budget")
    if (
        faces.ndim != 2
        or faces.shape[1] != 3
        or not np.issubdtype(faces.dtype, np.integer)
        or not len(faces)
        or faces.min() < 0
        or faces.max() >= len(vertices)
    ):
        raise ValueError("Triangles géométriques invalides")
    if max_faces < 1:
        raise ValueError("Budget de triangulation invalide")
    if len(faces) > max_faces:
        return {
            "sha256_triangles": None,
            "triangles": len(faces),
            "unite": "millimeter",
            "quantum_mm": 1e-5,
            "statut": "NON_CALCULEE_BUDGET",
            "methode": "Archive exportable ; identité non calculée au-delà du budget de faces",
        }
    # Le 3MF écrit .9g : comparer les coordonnées réellement sérialisées,
    # opération idempotente, avant le quantum (sinon frontière demi-quantum).
    vertices = np.fromiter(
        (float(format(float(x), ".9g")) for x in vertices.ravel()), dtype=np.float64, count=vertices.size
    ).reshape(vertices.shape)
    triangles = np.rint(vertices[faces] / 1e-5).astype(np.int64)
    first = np.lexsort((triangles[:, :, 2], triangles[:, :, 1], triangles[:, :, 0]), axis=1)[:, 0]
    cycles = (first[:, None] + np.arange(3)) % 3
    canonical = np.take_along_axis(triangles, cycles[:, :, None], axis=1).reshape(-1, 9)
    order = np.lexsort(tuple(canonical[:, i] for i in range(8, -1, -1)))
    digest = hashlib.sha256(
        b"BENCHAB triangles orientes mm coords .9g quantum 1e-5\n" + canonical[order].astype("<i8").tobytes()
    ).hexdigest()
    return {
        "sha256_triangles": digest,
        "triangles": len(faces),
        "unite": "millimeter",
        "quantum_mm": 1e-5,
        "methode": (
            "Triangles orientés, permutation cyclique et ordre des faces normalisés ; "
            "coordonnées locales sérialisées .9g puis quantifiées"
        ),
    }


def charger_reglages_reference(path):
    """Lit les réglages plats d'un 3MF explicitement choisi, sans profil installé. Réglages en unités
    natives (mm, s, °C, %) lus via le dictionnaire typé ; aucun réglage inconnu transformé."""
    path = Path(path)
    avant = sha256_fichier(path)
    with ZipFile(path) as archive:
        cle = "Metadata/project_settings.config"
        if archive.namelist().count(cle) != 1 or archive.getinfo(cle).file_size > 8 * 1024**2:
            raise ValueError("Un unique fichier de réglages 3MF, au plus 8 Mio, est requis")
        valeurs = schema_reglages.decoder(archive.read(cle))
    if not isinstance(valeurs, dict) or not valeurs:
        raise ValueError("Les réglages du profil 3MF doivent être un objet non vide")
    if sha256_fichier(path) != avant:
        raise ValueError("Profil de référence modifié pendant sa lecture")
    return valeurs, {"source_sha256": avant, "source_modifiee": False}


def ecrire_3mf(plaques, settings, out, cli=False, *, nommer_source=False, canonique=False, nom_source=None,
               source_3mf=None, rapport_integrite=None):
    """Écrit un 3MF multi-plaques et renvoie ``out`` ; coordonnées mm, réglages en unités natives.

    Lève ``ValueError``/``OSError`` au contrôle FAIL. Le remplacement est atomique ; les valeurs inconnues restent
    intactes, les différences historiques de réglages sont déclarées dans les métadonnées, et aucun profil installé
    ni géométrie d'entrée n'est modifié. ``cli=True`` sélectionne le pas de 300 mm.
    ``canonique=True`` déclare la provenance du nouveau fichier nommé ``nom_source``
    et ses métadonnées de plaque. Si source_3mf est fournie, chaque différence exacte
    est planifiée avant sérialisation et vérifiée avant livraison ; rapport_integrite reçoit le détail.
    """
    maximum = min(30, int(charger()["slicer"]["plaques_max"]))
    if not 1 <= len(plaques) <= maximum:
        raise ValueError(
            f"Un 3MF doit contenir entre 1 et {maximum} plaques ; répartir les suivantes dans un autre fichier"
        )
    types_volume = schema_reglages.definition("subtype", portee="structure_part")["valeurs"]
    for plaque in plaques:
        for item in plaque["items"]:
            for volume in item.get("volumes", []):
                if volume.get("subtype") not in types_volume:
                    # Le moteur transforme un rôle inconnu en corps imprimé : refuser plutôt que changer l'intention
                    # (docs/reference/integrite-3mf.md, rôle des volumes).
                    raise ValueError(
                        f"Type de volume inconnu : {volume.get('subtype')!r} ; "
                        "aucune conversion silencieuse en corps imprimé n'est permise."
                    )
    if canonique:
        if not isinstance(nom_source, str) or Path(nom_source).name != nom_source or not nom_source.endswith(".3mf"):
            raise ValueError("Le producteur canonique exige le nom réel du nouveau 3MF source")
        couleurs = schema_reglages.valeur_brute(settings, "filament_colour")
        if not isinstance(couleurs, list) or not couleurs:
            raise ValueError("Le producteur canonique exige la liste explicite des couleurs de filament")
    E.register_namespace("", NS)
    E.register_namespace("p", PROD)

    def tag(n):
        return "{" + NS + "}" + n

    def md(n, k, v):
        E.SubElement(n, "metadata", key=k, value=str(v))

    def repere_volume(maillage, centrer):
        """Décrit le repère du volume neuf en mm ; aucun réglage d'impression modifié."""
        centre = maillage.bounds.mean(axis=0)
        xyz = [format(float(v), ".17g") for v in centre]
        composant = "1 0 0 0 1 0 0 0 1 " + " ".join(xyz) if centrer else "1 0 0 0 1 0 0 0 1 0 0 0"
        matrice = (
            f"1 0 0 {xyz[0]} 0 1 0 {xyz[1]} 0 0 1 {xyz[2]} 0 0 0 1"
            if centrer else "1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"
        )
        return centre, composant, matrice

    def origine_volume(noeud, centre, indice):
        """Écrit la provenance du volume neuf ; décalages mm, indices sans unité, jamais de réglage de secours."""
        md(noeud, "source_file", nom_source)
        md(noeud, "source_object_id", len(identites))
        md(noeud, "source_volume_id", indice)
        for axe, valeur in zip("xyz", centre, strict=True):
            md(noeud, "source_offset_" + axe, format(float(valeur), ".17g"))

    root = E.Element(tag("model"), unit="millimeter", requiredextensions="p")
    E.SubElement(root, tag("metadata"), name="Application").text = "BambuStudio-2.0.0.3"
    E.SubElement(root, tag("metadata"), name="BambuStudio:3mfVersion").text = "1"
    res = E.SubElement(root, tag("resources"))
    build = E.SubElement(root, tag("build"))
    extroot = E.Element(tag("model"), unit="millimeter")
    er = E.SubElement(extroot, tag("resources"))
    config = E.Element("config")
    oid = 0
    n = len(plaques)
    c = colonnes(n)
    identites = []
    for k, p in enumerate(plaques):
        pr = E.SubElement(config, "plate")
        md(pr, "plater_id", k + 1)
        md(pr, "plater_name", p["name"] if nommer_source else "")
        if canonique:
            # Métadonnées déterministes du format moteur, pas un réglage de secours
            # (docs/reference/integrite-3mf.md, contrat du producteur neuf).
            md(pr, "locked", "false")
            md(pr, "filament_map_mode", "Auto For Flush")
            md(pr, "filament_maps", " ".join("1" for _ in couleurs))
            md(pr, "gcode_file", f"Metadata/plate_{k + 1}.gcode")
        pas = PAS_CLI if cli else PAS_INTERFACE
        px, py = (k % c) * pas, -(k // c) * pas
        for it in p["items"]:
            # Le moteur centre séparément les maillages multi-volumes et restitue leurs transformations.
            # Les écrire dès la création garde les positions mondiales : docs/reference/integrite-3mf.md.
            centrer_volumes = canonique and bool(it.get("volumes"))
            centre, transformation, matrice = repere_volume(it["mesh"], centrer_volumes)
            oid += 1
            pid = oid
            obj = E.SubElement(res, tag("object"), id=str(oid), type="model")
            cs = E.SubElement(obj, tag("components"))
            oc = E.SubElement(config, "object", id=str(oid))
            md(oc, "name", it["name"])
            md(oc, "extruder", it["extruder"])
            for kk, vv in it.get("settings", {}).items():
                md(oc, kk, vv)
            part = E.SubElement(er, tag("object"), id=str(pid), type="model")
            mesh = E.SubElement(part, tag("mesh"))
            vs = E.SubElement(mesh, tag("vertices"))
            fs = E.SubElement(mesh, tag("triangles"))
            for v in it["mesh"].vertices:
                coordonnees = v - centre if centrer_volumes else v
                E.SubElement(
                    vs,
                    tag("vertex"),
                    x=format(float(coordonnees[0]), ".9g"),
                    y=format(float(coordonnees[1]), ".9g"),
                    z=format(float(coordonnees[2]), ".9g"),
                )
            for f in it["mesh"].faces:
                E.SubElement(fs, tag("triangle"), v1=str(f[0]), v2=str(f[1]), v3=str(f[2]))
            E.SubElement(
                cs,
                tag("component"),
                objectid=str(pid),
                attrib={"{" + PROD + "}path": "/3D/Objects/pieces.model"},
                transform=transformation,
            )
            pc = E.SubElement(oc, "part", id=str(pid), subtype="normal_part")
            md(pc, "name", it["name"])
            if not canonique or it.get("volumes"):
                md(pc, "extruder", it["extruder"])
            md(pc, "matrix", matrice)
            if canonique:
                # Le volume unique hérite de l'objet ; les multivolumes gardent
                # leurs affectations distinctes (docs/reference/integrite-3mf.md).
                origine_volume(pc, centre, 0)
            # Volumes de support, forceurs ou modificateurs conservés à leur repère d'origine.
            for indice_volume, vol in enumerate(it.get("volumes", []), 1):
                centre_volume, transformation_volume, matrice_volume = repere_volume(vol["mesh"], centrer_volumes)
                oid += 1
                part = E.SubElement(er, tag("object"), id=str(oid), type="model")
                mesh = E.SubElement(part, tag("mesh"))
                vs = E.SubElement(mesh, tag("vertices"))
                fs = E.SubElement(mesh, tag("triangles"))
                for v in vol["mesh"].vertices:
                    coordonnees = v - centre_volume if centrer_volumes else v
                    E.SubElement(
                        vs,
                        tag("vertex"),
                        x=format(float(coordonnees[0]), ".9g"),
                        y=format(float(coordonnees[1]), ".9g"),
                        z=format(float(coordonnees[2]), ".9g"),
                    )
                for f in vol["mesh"].faces:
                    E.SubElement(fs, tag("triangle"), v1=str(f[0]), v2=str(f[1]), v3=str(f[2]))
                E.SubElement(
                    cs,
                    tag("component"),
                    objectid=str(oid),
                    attrib={"{" + PROD + "}path": "/3D/Objects/pieces.model"},
                    transform=transformation_volume,
                )
                pv = E.SubElement(oc, "part", id=str(oid), subtype=vol["subtype"])
                md(pv, "name", vol["name"])
                md(pv, "matrix", matrice_volume)
                if canonique:
                    origine_volume(pv, centre_volume, indice_volume)
                for kk, vv in vol.get("settings", {}).items():
                    md(pv, kk, vv)  # réglages propres au volume (modificateur), 25/10/2026 : remplissage_localise.py
            E.SubElement(
                build,
                tag("item"),
                objectid=str(pid),
                transform=f"1 0 0 0 1 0 0 0 1 {it['x'] + px} {it['y'] + py} 0",
                printable="1",
            )
            identites.append(
                {
                    "object_id": pid,
                    "piece": it["name"],
                    "plaque": k + 1,
                    "geometrie": empreinte_geometrie(it["mesh"]),
                    "transform_3mf": [1, 0, 0, 0, 1, 0, 0, 0, 1, float(it["x"] + px), float(it["y"] + py), 0],
                    "xy_plaque_mm": [float(it["x"]), float(it["y"])],
                    "modificateurs": [
                        {"type": v["subtype"], "geometrie": empreinte_geometrie(v["mesh"])}
                        for v in it.get("volumes", [])
                    ],
                }
            )
            mi = E.SubElement(pr, "model_instance")
            md(mi, "object_id", pid)
            md(mi, "instance_id", 0)
            md(mi, "identify_id", pid)
    contexte = {**getattr(settings, "contexte", {}), "nombre_plaques_declares": n,
                "consommateur_reglages": "cli"}
    settings = schema_reglages.Reglages(settings, contexte=contexte)
    settings, normalisations = normaliser_heritages(settings)
    settings = schema_reglages.Reglages(settings, contexte=contexte)
    transformations = copy.deepcopy(normalisations)
    for cle, valeur in (("wipe_tower_x", ["200"] * n), ("wipe_tower_y", ["10"] * n)):
        avant_present = cle in settings
        avant = copy.deepcopy(settings.get(cle))
        settings[cle] = valeur
        transformations.append(
            {
                "reglage": cle,
                "avant_present": avant_present,
                "avant": avant,
                "apres": copy.deepcopy(valeur),
                "unite": "mm",
                "raison": "Coordonnée d’essuyage définie pour chaque plaque déclarée.",
                "reference": "docs/guides/slicer-anycubic.md",
            }
        )
    attendus_reglages = copy.deepcopy(dict(settings))
    attendus_model = E.tostring(config, encoding="utf-8", xml_declaration=True)
    modele_3mf = E.tostring(root, encoding="utf-8", xml_declaration=True)
    geometrie_3mf = E.tostring(extroot, encoding="utf-8", xml_declaration=True)
    contexte_validation = copy.deepcopy(contexte)
    sortie = Path(out)
    permis_source = None
    sha_source = None
    if source_3mf is not None:
        sha_source = sha256_fichier(source_3mf)
        # La découpe change le modèle : déclarer son profil et ses retraits AVANT l'écriture,
        # jamais déduire une permission d'une sortie déjà produite (docs/reference/integrite-3mf.md).
        etat_source = integrite_3mf.instantane(source_3mf)
        etat_prevu = integrite_3mf.instantane_config(attendus_reglages, attendus_model)
        permis_source = integrite_3mf.difference(etat_source, etat_prevu)
    temporaire = None
    try:
        # Le temporaire voisin garantit qu'un échec de sérialisation garde la cible préexistante intacte
        # (docs/reference/regles.md : les transformations 3MF sont vérifiées avant livraison).
        fd, nom_temporaire = tempfile.mkstemp(prefix=f".{sortie.name}.", suffix=".tmp", dir=sortie.parent)
        os.close(fd)
        temporaire = Path(nom_temporaire)
        with ZipFile(temporaire, "w", ZIP_DEFLATED) as z:
            z.writestr("3D/3dmodel.model", modele_3mf)
            z.writestr("3D/Objects/pieces.model", geometrie_3mf)
            z.writestr("Metadata/model_settings.config", attendus_model)
            z.writestr(
                "Metadata/project_settings.config",
                schema_reglages.encoder(settings, ensure_ascii=False, allow_nan=False),
            )
            z.writestr(
                "Metadata/benchab_geometry.json",
                json.dumps(
                    {
                        "schema": 1,
                        "unite": "millimeter",
                        "pas_plaques_mm": PAS_CLI if cli else PAS_INTERFACE,
                        "objets": identites,
                        "transformations_reglages": transformations,
                        "limite": (
                            "Provenance du producteur du 3MF source ; archive à lier par SHA-256. "
                            "Distinct du G-code et de la validation physique."
                        ),
                    },
                    ensure_ascii=False,
                ),
            )
            z.writestr(
                "[Content_Types].xml",
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
                "</Types>",
            )
            for f, target in [
                ("_rels/.rels", "/3D/3dmodel.model"),
                ("3D/_rels/3dmodel.model.rels", "/3D/Objects/pieces.model"),
            ]:
                z.writestr(
                    f,
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    f'<Relationship Target="{target}" Id="rel-1" '
                    'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
                    "</Relationships>",
                )
        with ZipFile(temporaire) as archive:
            if archive.testzip() is not None:
                raise ValueError("Archive 3MF temporaire corrompue.")
            projet_relu = schema_reglages.decoder(
                archive.read("Metadata/project_settings.config"), contexte=contexte_validation
            )
            modele_relu = archive.read("Metadata/model_settings.config")
            if dict(projet_relu) != attendus_reglages or modele_relu != attendus_model:
                raise ValueError("Réglages 3MF modifiés pendant la sérialisation ; publication refusée.")
        # Le différentiel vérifie l'absence de clés ambiguës dans projet, objets, parties et plaques.
        instantane = integrite_3mf.instantane(temporaire)
        if not instantane:
            raise ValueError("Instantané de réglages 3MF vide ; publication refusée.")
        if permis_source is not None:
            controle_source = integrite_3mf.verifier(source_3mf, temporaire, permis_source)
            if sha256_fichier(source_3mf) != sha_source:
                raise ValueError("Source 3MF changée pendant sa transformation")
            if rapport_integrite is not None:
                rapport_integrite.update(controle_source, source_sha256=sha_source,
                                         declaration_avant_serialisation=True)
        os.replace(temporaire, sortie)
        temporaire = None
    finally:
        if temporaire is not None:
            temporaire.unlink(missing_ok=True)
    return out


def noms_plaques(path):
    with ZipFile(path) as z:
        cle = "Metadata/model_settings.config"
        if z.namelist().count(cle) != 1:
            raise ValueError("modèle de plaques absent ou dupliqué")
        plates = E.fromstring(z.read(cle)).findall("plate")
        if not plates:
            raise ValueError("aucune plaque source déclarée")
        names = []
        for plate in plates:
            fields = [m.get("value") for m in plate.findall("metadata") if m.get("key") == "plater_name"]
            if len(fields) != 1 or fields[0] is None:
                raise ValueError("nom de plaque absent ou dupliqué")
            names.append(fields[0])
        return names


def _signature_sortie(path):
    """
    Identité et contenu : détecte aussi un remplacement à contenu identique.
    """
    if path.is_symlink():
        raise ValueError("sortie symbolique refusée")
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    if not path.is_file() or st.st_nlink != 1:
        raise ValueError("sortie non régulière ou liée refusée")
    return (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size, hashlib.sha256(path.read_bytes()).hexdigest())


def _valider_tranchage(path, noms, attendu=None):
    mesures = resultats(path)
    with ZipFile(path) as z:
        membres = z.namelist()
        if len(membres) != len(set(membres)) or z.testzip() is not None:
            raise ValueError("ZIP dupliqué ou corrompu")
        plaques = {
            int(re.fullmatch(r"Metadata/plate_(\d+)\.gcode", n).group(1)): n
            for n in membres
            if re.fullmatch(r"Metadata/plate_\d+\.gcode", n)
        }
        indices = {p["index"] for p in mesures}
        attendues = set(range(1, len(noms) + 1)) if noms else indices
        if not plaques or set(plaques) != indices or indices != attendues:
            raise ValueError("plaques tranchées absentes ou incohérentes avec les résultats/source")
        for numero, membre in plaques.items():
            gcode = lire_texte_gcode_3mf(path, membre, erreurs="strict")
            if not any(ligne.split(";")[0].strip() for ligne in gcode.splitlines()):
                raise ValueError(f"plaque {numero} sans commandes")
    if attendu and reglages_embarques(path, attendu):
        raise ValueError("réglages embarqués absents ou différents des réglages requis")
    return mesures


def trancher(src, outdir, nom, log=None, timeout=None, attendu=None, *, usage="impression", prive=None):
    """
    Délègue au lanceur unique ; garde le contrat historique (chemin, secondes).
    """
    timeout = 1800.0 if timeout is None else float(timeout)
    if not math.isfinite(timeout) or timeout <= 0 or timeout > 1800:
        raise ValueError("délai de tranchage fini entre 0 et 1800 secondes requis")
    journal = Path(log) if log is not None else Path(outdir) / (Path(nom).name + ".log")
    destination, rapport = trancher_fichier(
        src,
        outdir,
        nom,
        usage=usage,
        timeout_s=timeout,
        journal=journal,
        prive=prive,
        attendu=attendu,
    )
    return destination, float(rapport["duree_s"])


def resultats(t3mf):
    with ZipFile(t3mf) as z:
        si = E.fromstring(z.read("Metadata/slice_info.config"))
    out = []
    for p in si.findall("plate"):
        md = {m.get("key"): m.get("value") for m in p.findall("metadata")}
        out.append(
            {
                "index": int(md["index"]),
                "secondes": float(md.get("prediction", 0)),
                "grammes": float(md.get("weight", 0)),
                "supports": md.get("support_used"),
                "avertissements": [w.get("msg") for w in p.findall("warning")],
            }
        )
    if not out or len({p["index"] for p in out}) != len(out):
        raise ValueError("résultats de plaques absents ou dupliqués")
    if any(p["index"] < 1 or any(not math.isfinite(p[k]) or p[k] <= 0 for k in ("secondes", "grammes")) for p in out):
        raise ValueError("durée ou masse du slicer absente/non exploitable")
    return out


def reglages_embarques(t3mf, attendu):
    """Compare chaque commentaire G-code aux attentes typées, en unités natives.

    Retourne les écarts FAIL/NON VÉRIFIÉ ; liste vide si conforme. Ne modifie jamais
    le 3MF ni ses réglages ; une liste par mode machine n'est jamais assimilée aux filaments.
    """
    ecarts = []
    plaques = membres_gcode_3mf(t3mf)
    if not plaques:
        return [{"plaque": None, "reglage": "G-code", "attendu": "au moins une plaque", "embarque": None}]
    for n in plaques:
        g = lire_texte_gcode_3mf(t3mf, n)
        for k, v in attendu.items():
            definition = schema_reglages.definition(k, valeur=v)
            if definition["type"] == "inconnu":
                ecarts.append({"plaque": n, "reglage": k, "statut": "NON VÉRIFIÉ",
                               "detail": "Clé de réglage inconnue : commentaire non certifié"})
                continue
            if definition["type"].startswith("liste_") and not isinstance(v, (list, tuple)):
                # Une attente scalaire ne doit pas masquer la cardinalité d'une liste
                # (docs/reference/reglages-et-integrite.md).
                ecarts.append({"plaque": n, "reglage": k, "statut": "FAIL",
                               "detail": "Attente explicite sous forme de liste exigée"})
                continue
            m = re.search(r"^; " + re.escape(k) + r" = (.*)$", g, re.M)
            if m is None:
                ecarts.append({"plaque": n, "reglage": k, "attendu": v, "embarque": None, "statut": "NON VÉRIFIÉ"})
                continue
            values = [x.strip() for x in next(csv.reader([m.group(1)], skipinitialspace=True))]
            expected = [str(x) for x in v] if isinstance(v, (list, tuple)) else [str(v)]
            if len(expected) != len(values):
                ecarts.append(
                    {
                        "plaque": n,
                        "reglage": k,
                        "attendu": expected,
                        "embarque": values,
                        "statut": "FAIL",
                        "detail": "cardinalité différente",
                    }
                )
                continue
            for index, (want, actual) in enumerate(zip(expected, values), 1):
                if actual != want:
                    ecarts.append(
                        {
                            "plaque": n,
                            "reglage": k,
                            "mode_machine" if definition["type"] == "liste_mode_machine" else
                            "filament" if definition["type"] == "liste_filament" else "indice": index,
                            "attendu": want,
                            "embarque": actual,
                            "statut": "FAIL",
                        }
                    )
    return ecarts


def nommer_plaques(path, noms):
    """Restaure les noms d'un 3MF privé ; retourne PASS ou lève FAIL, sans toucher aux plaques protégées.

    Entrées : chemin privé et noms (sans unité) ; sortie : bilan de réouverture. Aucun G-code ni réglage
    d'impression n'est modifié (docs/reference/integrite-3mf.md).
    """
    import stat

    from benchab.projets.protections import protections
    from benchab.qualite.ecritures import acquerir_verrou, ouvrir_verrou, sortie_privee

    cible = sortie_privee(path, prive=Path(__file__).resolve().parents[2] / "prive")
    if cible.is_symlink():
        raise ValueError("3MF lié refusé pour la restauration des noms.")
    parent_fd = os.open(cible.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        verrou_fd = ouvrir_verrou(f".{cible.name}.nommer.lock", parent_fd)
        with os.fdopen(verrou_fd, "a") as verrou:
            acquerir_verrou(verrou)
            fd = os.open(cible.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
            with os.fdopen(fd, "rb") as fichier:
                mode = os.fstat(fichier.fileno()).st_mode
                if not stat.S_ISREG(mode) or os.fstat(fichier.fileno()).st_nlink != 1:
                    raise ValueError("3MF non régulier ou lié refusé.")
                empreinte = hashlib.file_digest(fichier, "sha256").hexdigest()
            for projet in cible.parents:
                if any((projet / marqueur).is_file() for marqueur in
                       ("LIVRABLES.json", "VERROU-REIMPRESSION.json", "qualite.json")) or (
                           projet / "preparations"
                       ).is_dir():
                    relat = cible.relative_to(projet).as_posix()
                    proteges = protections(projet)
                    if relat in proteges or empreinte in proteges.values():
                        raise ValueError("3MF protégé : restauration de noms refusée.")
            # Le verrou stable sérialise les appels BENCHAB ; restaurer_noms valide les membres
            # et l'intégrité avant son remplacement atomique (docs/reference/integrite-3mf.md).
            resultat = restaurer_noms(cible, noms)
            return resultat
    finally:
        os.close(parent_fd)
