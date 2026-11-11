"""

Revue locale des ponts et surplombs : faits G-code, pas diagnostic thermique.

Les ponts extérieurs et parois en surplomb, même courts et sur la dernière
couche, nécessitent une revue documentée. Internal Bridge n'est pas assimilé
à un pont extérieur. Les petits îlots sont fournis par controles_piece.py.
Une revue est liée aux empreintes du tranchage, du contrôle géométrique et de
ses preuves : modifier un de ces fichiers invalide la décision précédente.


"""

import hashlib
import json
import math
from pathlib import Path

from benchab.controles.gcode_interpreter import Interpreter
from benchab.controles.mesures_zones import verifier_mesures_declarees
from benchab.gcode.geometrie_arc import enveloppe_deposee_xyz

NUM = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
PIECE = {
    "Outer wall",
    "Inner wall",
    "Overhang wall",
    "Bridge",
    "Internal Bridge",
    "Top surface",
    "Bottom surface",
    "Sparse infill",
    "Internal solid infill",
    "Solid infill",
    "Gap infill",
}


def sha(path):
    """Calcule le SHA-256 du fichier local en lecture seule pour identifier la preuve."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def analyser_gcode(g, plaque=1):
    """Détecte les zones Bridge/Overhang et leurs bbox XY/Z en mm depuis le G-code.

    Renvoie les mouvements, leur plaque et objet déclaré, ainsi que les erreurs.
    Sur les zones déclarées, les arcs G17 qualifiés bornent la portion déposée
    en mm ; ils ne prouvent ni la portée libre ni l’appui réel. Toute géométrie
    ou attribution non couverte reste bloquante. Aucun fichier ni réglage modifié.
    """
    it = Interpreter()
    x = y = z = 0.0
    feed = 0.0
    typ = None
    active = False
    purge = False
    zone = None
    zone_arc = False
    objets_zone = set()
    zones = []
    erreurs = []
    depots = 0

    def finir():
        nonlocal zone, zone_arc
        if zone:
            zone["bbox_xy_mm"] = [round(v, 3) for v in zone["bbox_xy_mm"]]
            # L'identifiant historique rattache des avis humains figés. Ajouter
            # le contexte de mesure après son calcul, sans redécouper la zone (#111).
            zone["id"] = hashlib.sha256(json.dumps(zone, sort_keys=True).encode()).hexdigest()[:16]
            zone["objet"] = next(iter(objets_zone)) if len(objets_zone) == 1 else None
            if len(objets_zone) > 1:
                zone["objet_ambigu"] = True
            if zone_arc and (len(objets_zone) != 1 or None in objets_zone):
                erreurs.append("attribution objet non vérifiée sur une zone avec arc G17")
            zones.append(zone)
            zone = None
            zone_arc = False
            objets_zone.clear()

    for line in g.splitlines():
        move = it.feed_line(line)
        courbe = None
        if move and move["trajectory"] == "arc_g17_xy":
            # Le contrôle qualifie ici une enveloppe locale, jamais la corde ni la couverture (#123).
            if (
                active and not purge and move["layer"] >= 0 and move["deposited"] > 0
                and typ in {"Bridge", "Overhang wall"} and move["type"] == typ
            ):
                try:
                    courbe = enveloppe_deposee_xyz(move)
                except ValueError as erreur:
                    erreurs.append(f"arc G17 ligne {move['line']} : {erreur}")
                    move = None
            else:
                erreurs.append("arc analytique non pris en charge par l'approximation géométrique des zones")
                it.error(erreurs[-1])
                move = None
        if line.strip() == "; PURGE LINE":
            purge = True
        if line.startswith("; AFTER_LAYER_CHANGE") or line.startswith("EXCLUDE_OBJECT_START"):
            purge = False
        if line.startswith(";LAYER_CHANGE"):
            active = True
            finir()
        if line.startswith(";TYPE:"):
            finir()
            typ = line[6:].strip()
        c = line.split(";", 1)[0].strip()
        if not c:
            continue
        if move:
            x, y, z = move["start"]
            nx, ny, nz = move["end"]
            if courbe is None and move["deposited"] > 0:
                x, y, z = move["deposition_start"]
            de = move["deposited"]
            feed = move["feed"]
            if active and not purge and de > 0 and (courbe is not None or math.hypot(nx - x, ny - y) > 1e-8):
                if typ not in PIECE and typ not in {
                    "Support",
                    "Support interface",
                    "Skirt",
                    "Brim",
                    "Prime tower",
                    "Wipe tower",
                }:
                    erreurs.append("type de trajet absent ou inconnu : " + str(typ))
                if typ in PIECE:
                    depots += 1
                if typ in {"Bridge", "Overhang wall"}:
                    if feed <= 0:
                        erreurs.append("vitesse inconnue sur une zone à risque")
                    if courbe is None:
                        bbox = [min(x, nx), min(y, ny), max(x, nx), max(y, ny)]
                        bas_z = haut_z = nz
                    else:
                        (xmin, xmax), (ymin, ymax), (bas_z, haut_z) = courbe
                        bbox = [xmin, ymin, xmax, ymax]
                        zone_arc = True
                    if zone is None:
                        zone = {
                            "plaque": plaque,
                            "type": typ,
                            "z_min_mm": bas_z,
                            "z_max_mm": haut_z,
                            "bbox_xy_mm": bbox,
                            "segments": 0,
                            "vitesse_min_mm_s": feed / 60,
                            "vitesse_max_mm_s": feed / 60,
                        }
                    b = zone["bbox_xy_mm"]
                    objets_zone.add(move["object"])
                    zone["bbox_xy_mm"] = [
                        min(b[0], bbox[0]), min(b[1], bbox[1]), max(b[2], bbox[2]), max(b[3], bbox[3])
                    ]
                    zone["z_min_mm"] = min(zone["z_min_mm"], bas_z)
                    zone["z_max_mm"] = max(zone["z_max_mm"], haut_z)
                    zone["segments"] += 1
                    zone["vitesse_min_mm_s"] = min(zone["vitesse_min_mm_s"], feed / 60)
                    zone["vitesse_max_mm_s"] = max(zone["vitesse_max_mm_s"], feed / 60)
            x, y = nx, ny
    finir()
    erreurs.extend(it.errors)
    if not depots:
        erreurs.append("aucun dépôt de pièce analysable")
    return {
        "zones": zones,
        "erreurs": sorted(set(erreurs)),
        "limites": (
            "Types déclarés par le slicer ; bbox inclut ancrages. Ni portée libre, ni température réelle, "
            "ni refroidissement mesurés. Détails reliés à une grande section possibles."
        ),
    }


def evaluer(zones, erreurs, tranche, geometrie=None, revue=None):
    """Évalue l’acquittement des zones et des preuves liées aux empreintes.

    Les distances sont en mm, les sources sont identifiées par SHA-256 et les
    statuts globaux sont PASS ou FAIL. Toute preuve manquante ou mesure NON
    VÉRIFIÉE laisse la zone non revue; une contradiction mesurée reste bloquante.
    Les décisions humaines sont lues sans les modifier et aucun résultat ne
    constitue une validation physique.
    """
    zones = list(zones)
    erreurs = list(erreurs)
    gh = None
    if geometrie:
        try:
            gh = sha(geometrie)
            geo = json.loads(Path(geometrie).read_text())
            if not isinstance(geo, dict) or not isinstance(geo.get("fichiers"), dict) or not geo["fichiers"]:
                raise ValueError("aucun fichier contrôlé")
            for nom, controles in geo["fichiers"].items():
                if not isinstance(controles, list) or not controles:
                    raise ValueError("aucun contrôle pour " + nom)
                for c in controles:
                    if not isinstance(c, dict) or c.get("statut") not in {
                        "PASS",
                        "WARN",
                        "FAIL",
                        "NON APPLICABLE",
                        "NON VÉRIFIÉ",
                    }:
                        raise ValueError("statut géométrique absent ou inconnu pour " + nom)
                    if c.get("statut") == "FAIL":
                        erreurs.append("FAIL géométrique bloquant : " + nom + " : " + str(c.get("controle")))
                    if c.get("controle") in {"couches courtes", "surplombs"} and c.get("statut") != "PASS":
                        zones.append(
                            {
                                "id": "geometrie:" + nom + ":" + c["controle"],
                                "type": c["controle"],
                                "fichier": nom,
                                "detail": c.get("detail", ""),
                            }
                        )
        except (OSError, ValueError, KeyError, TypeError) as ex:
            erreurs.append("contrôle géométrique absent/invalide : " + str(ex))
    signature = {"tranche_sha256": sha(tranche), "geometrie_sha256": gh}
    doc = {}
    base = Path(revue).parent if revue else Path(".")
    if revue and Path(revue).exists():
        try:
            doc = json.loads(Path(revue).read_text())
            if not isinstance(doc, dict) or not isinstance(doc.get("zones", {}), dict):
                raise ValueError("Objet de revue et dictionnaire des zones requis")
        except (OSError, ValueError):
            doc = {}
            erreurs.append("revue locale illisible")
    valide = all(doc.get(k) == v for k, v in signature.items())
    declarations = doc.get("mesures_recul_recouvrement", []) if valide else []
    mesures = (
        verifier_mesures_declarees(tranche, zones, declarations)
        if valide and "mesures_recul_recouvrement" in doc
        else []
    )
    erreurs.extend(
        f"mesure recul/recouvrement {r.get('zone_id', '?')} : contradiction"
        for r in mesures
        if r.get("statut") == "FAIL"
    )
    non_mesurees = [r.get("zone_id", "?") for r in mesures if r.get("statut") == "NON VÉRIFIÉ"]
    ids_zones = {zone.get("id") for zone in zones}
    if any(r.get("statut") == "NON VÉRIFIÉ" and r.get("zone_id") not in ids_zones for r in mesures):
        non_mesurees.extend(ids_zones)
    erreurs.extend(
        f"mesure recul/recouvrement sans zone valide : {r.get('erreur', 'déclaration invalide')}"
        for r in mesures
        if r.get("statut") == "NON VÉRIFIÉ" and r.get("zone_id") not in ids_zones
    )
    non_revues = []
    for zone in zones:
        avis = doc.get("zones", {}).get(zone["id"], {}) if valide else {}
        if not isinstance(avis, dict):
            avis = {}
        preuves = avis.get("preuves", [])
        preuves_ok = isinstance(preuves, list) and bool(preuves)
        for preuve in preuves if preuves_ok else ():
            try:
                preuves_ok &= sha(base / preuve["fichier"]) == preuve["sha256"]
            except (OSError, KeyError, TypeError):
                preuves_ok = False
        if not (
            avis.get("decision") in {"corrige", "justifie"}
            and str(avis.get("justification", "")).strip()
            and avis.get("derniere_couche_verifiee") is True
            and preuves_ok
            and zone["id"] not in non_mesurees
        ):
            non_revues.append(zone["id"])
    return {
        "statut": "FAIL" if erreurs or non_revues else "PASS",
        **signature,
        "zones": zones,
        "mesures_recul_recouvrement": mesures,
        "non_revues": non_revues,
        "erreurs": erreurs,
        "limite": (
            "Revue documentaire liée aux fichiers, pas une validation physique. Examiner appuis, parcours, vitesses "
            "et refroidissement ; corriger puis retrancher si nécessaire."
        ),
    }
