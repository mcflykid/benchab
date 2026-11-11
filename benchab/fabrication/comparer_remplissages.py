"""Compare des variantes de remplissage et leurs trajets G-code."""

import argparse
import json
import math
import zipfile
from pathlib import Path

import trimesh

from benchab import reglages as schema_reglages
from benchab.controles.gcode_interpreter import Interpreter
from benchab.fabrication import anycubic, remplissage_localise
from benchab.fabrication.resistance import cle_classement
from benchab.gcode.lecture import iter_lignes_gcode_3mf
from benchab.qualite.empreintes import sha256_fichier

LOT_PLAQUES = 30
CLES_REMPLISSAGE = ("sparse_infill_pattern", "sparse_infill_density", "wall_loops")
HERITES_REMPLISSAGE = ("sparse_infill_line_width",)


def _variante(motif: str, densite: int, parois: int, **options) -> dict:
    return {
        "sparse_infill_pattern": motif,
        "sparse_infill_density": f"{densite}%",
        "wall_loops": str(parois),
        **options,
    }


FAMILLES = {
    "decoratif": [
        _variante("gyroid", 5, 2),
        _variante("gyroid", 10, 2),
        _variante("lightning", 10, 2),
        _variante("gyroid", 8, 3),
    ],
    "support": [
        _variante("gyroid", 10, 3),
        _variante("gyroid", 15, 2),
        _variante("gyroid", 20, 3),
        _variante("crosshatch", 15, 3),
        _variante("gyroid", 10, 4),
    ],
    "mecanique": [
        _variante("gyroid", 15, 5),
        _variante("gyroid", 25, 4),
        _variante("gyroid", 40, 4),
        _variante("crosshatch", 25, 5),
    ],
    "contenant": [
        _variante("gyroid", 10, 3),
        _variante("gyroid", 15, 4),
        _variante("crosshatch", 10, 3),
    ],
}
JUSTIFICATION = {
    "decoratif": "pièce décorative sans charge",
    "support": "charge modérée, pièce posée",
    "mecanique": "pièce chargée ou à liaison : parois renforcées",
    "contenant": "contenant à parois minces",
}


def construire_pieces_remplissages(plaques, reglages, declarations, *, zones=None, cavites=(), efforts=None):
    """Prépare les variantes déclarées ; unités natives inchangées et aucune géométrie ou réglage source modifié.

    Entrées : plaques, réglages projet, usages, zones, noms de cavités et efforts déclarés.
    Sortie : (pièces d'étude, avertissements) ; les variantes restent des comparaisons, jamais un choix appliqué.
    """
    zones = zones or {}
    efforts = efforts or {}
    noms_cavites = (
        {nom for nom, declaration in cavites.items() if declaration} if isinstance(cavites, dict) else set(cavites)
    )
    pieces, avertissements, vus = [], [], set()
    for plaque in plaques:
        for item in plaque["items"]:
            nom_piece = item["piece"]
            if nom_piece in vus:
                continue
            vus.add(nom_piece)
            declaration = declarations.get(nom_piece, declarations.get("*"))
            if isinstance(declaration, dict):
                usage = declaration.get("usage")
                variantes_libres = declaration.get("variantes", [])
                justification = declaration.get("justification")
            else:
                usage = declaration
                variantes_libres = declarations.get("variantes", [])
                justification = None
            if usage not in FAMILLES:
                avertissements.append(
                    {"piece": nom_piece, "statut": "WARN", "detail": f"usage {usage!r} inconnu ou non déclaré"}
                )
                continue
            reglages_objet = item.get("reglages", {})
            reference = {
                cle: str(valeur)
                for cle, valeur in {**reglages, **reglages_objet}.items()
                if cle in CLES_REMPLISSAGE + HERITES_REMPLISSAGE
            }
            herites = {cle: reference[cle] for cle in HERITES_REMPLISSAGE if cle in reference}
            variantes = []
            for valeur in FAMILLES[usage]:
                if any(valeur[cle] != reference.get(cle) for cle in CLES_REMPLISSAGE):
                    variantes.append(
                        {
                            "nom": (
                                f"{valeur['sparse_infill_pattern']} {valeur['sparse_infill_density']}, "
                                f"{valeur['wall_loops']} parois"
                            ),
                            "reglages": herites | valeur,
                            "acceptable": True,
                            "justification": justification or JUSTIFICATION[usage],
                        }
                    )
            for variante in variantes_libres:
                if not isinstance(variante, dict) or not isinstance(variante.get("reglages"), dict):
                    raise ValueError(f"Variante libre invalide pour {nom_piece!r}.")
                if variante.get("piece", nom_piece) == nom_piece:
                    copie = dict(variante)
                    copie.setdefault("acceptable", True)
                    copie.setdefault("justification", justification or JUSTIFICATION[usage])
                    variantes.append(copie)
            zones_piece = zones.get(nom_piece, [])
            if zones_piece:
                moins_dense = min(
                    FAMILLES[usage], key=lambda valeur: float(valeur["sparse_infill_density"].rstrip("%"))
                )
                variantes.extend([
                    {"nom": "localisé : référence + zones", "reglages": reference, "zones": zones_piece,
                     "acceptable": True, "justification": "zones déclarées dans ZONES_REMPLISSAGE"},
                    {"nom": f"localisé : reste {moins_dense['sparse_infill_density']} + zones",
                     "reglages": dict(reference, sparse_infill_density=moins_dense["sparse_infill_density"]),
                     "zones": zones_piece, "acceptable": True,
                     "justification": "reste de la pièce allégé, zones fonctionnelles conservées"},
                ])
            base = {"nom": nom_piece, "stl": f"../../candidats/{nom_piece}.stl", "extruder": item.get("extruder", 1),
                    "reference": reference, "variantes": variantes, "usage": usage}
            effort = efforts.get(nom_piece, efforts.get("*")) if isinstance(efforts, dict) else None
            if effort is not None:
                base["efforts"] = effort
            if nom_piece in noms_cavites:
                pieces.append(
                    {
                        **base,
                        "nom": nom_piece + " (cavités)",
                        "stl": f"../../candidats/optimise/{nom_piece}.stl",
                        "variantes": [],
                    }
                )
            pieces.append(base)
    return pieces, avertissements


def rapport_remplissages(pieces, comparaison, avertissements=None):
    """Résume les coûts et avertissements sans sélectionner ni appliquer un réglage d'impression.

    Entrées : pièces et résultats du comparateur (temps en heures, masse en grammes, statuts).
    Sortie : rapport PASS/WARN/FAIL ; aucun réglage n'est appliqué et aucun résultat physique n'est inféré.
    """
    rapport = {"statut": "PASS", "pieces": {}}
    for alerte in avertissements or []:
        rapport["pieces"][alerte["piece"]] = dict(alerte)
    lignes = comparaison.get("resultats", [])
    for piece in pieces:
        nom = piece["nom"]
        lignes_piece = [ligne for ligne in lignes if ligne.get("piece") == nom]
        if not lignes_piece:
            rapport["pieces"][nom] = {"statut": "FAIL", "detail": "aucun résultat de comparaison"}
            continue
        reference = lignes_piece[0]
        acceptables = [
            ligne
            for ligne in lignes_piece
            if ligne.get("acceptable") and ligne.get("resistance_calculee", {}).get("statut") != "FAIL"
        ]
        meilleure = (
            # Le classement commun garde sa priorité d'imprimabilité puis résistance avant
            # durée/masse ; ne pas remplacer cet ordre par un tri économique
            # (docs/reference/profils-et-procedes.md).
            min(acceptables, key=cle_classement)
            if acceptables
            else reference
        )
        gain = max(meilleure.get("gain_temps_pct", 0), meilleure.get("gain_matiere_pct", 0))
        famille = FAMILLES[piece["usage"]]
        parois_min = min(int(valeur["wall_loops"]) for valeur in famille)
        densite_min = min(float(valeur["sparse_infill_density"].rstrip("%")) for valeur in famille)
        faible = int(reference["reglages"].get("wall_loops", parois_min)) < parois_min or float(
            str(reference["reglages"].get("sparse_infill_density", f"{densite_min}%")).rstrip("%")
        ) < densite_min
        statut = (
            "FAIL"
            if reference.get("resistance_calculee", {}).get("statut") == "FAIL" or not acceptables
            else "PASS"
        )
        resistance = reference.get("resistance_calculee", {}).get("statut")
        avertissement = (
            (meilleure is not reference and gain > 3)
            or faible
            or reference.get("alerte_imprimabilite")
            or resistance in {"WARN", "NON VERIFIE"}
        )
        if statut != "FAIL" and avertissement:
            statut = "WARN"
        cles_meilleure = ("nom", "reglages", "heures", "grammes", "gain_temps_pct", "gain_matiere_pct")
        resume = {
            "statut": statut,
            "usage": piece["usage"],
            "reference": reference["reglages"],
            "temps_h": reference["heures"],
            "grammes": reference["grammes"],
            "meilleure": {cle: meilleure[cle] for cle in cles_meilleure if cle in meilleure},
            "sous_le_minimum_de_la_famille": faible,
            "remplissage_trajets": reference.get("remplissage"),
            "resistance_orca": reference.get("resistance_orca"),
            "resistance_calculee": reference.get("resistance_calculee"),
        }
        if reference.get("alerte_imprimabilite"):
            resume["alerte_imprimabilite"] = reference["alerte_imprimabilite"]
        rapport["pieces"][nom] = resume
    for nom, resume in rapport["pieces"].items():
        if not nom.endswith(" (cavités)"):
            continue
        reference = rapport["pieces"].get(nom.removesuffix(" (cavités)"))
        if (
            resume.get("statut") != "FAIL"
            and
            reference
            and resume.get("grammes") is not None
            and resume["grammes"] >= reference.get("grammes", float("inf"))
        ):
            resume.update(
                statut="WARN",
                conseil=(
                    "Cavités plus coûteuses en matière que la référence ; conserver la référence "
                    "sauf raison déclarée."
                ),
            )
    statuts = [entree.get("statut") for entree in rapport["pieces"].values()]
    rapport["statut"] = "FAIL" if "FAIL" in statuts else "WARN" if "WARN" in statuts else "PASS"
    rapport["meilleure_acceptable"] = comparaison.get("meilleure_acceptable", {})
    return rapport


def rapport_lissage(pieces, comparaison):
    """Traduit une comparaison A/B en coût numérique du lissage, sans conclure sur l'aspect physique.

    Entrées : pièces déclarant un mode de lissage et résultats du comparateur.
    Sortie : temps sans/avec en minutes et surcoût temporel en pour cent.
    """
    rapport = {"statut": "PASS", "pieces": {}}
    for piece in pieces:
        lignes = [ligne for ligne in comparaison.get("resultats", []) if ligne.get("piece") == piece["nom"]]
        if len(lignes) < 2:
            rapport["statut"] = "FAIL"
            rapport["pieces"][piece["nom"]] = {"statut": "FAIL", "detail": "résultats A/B incomplets"}
            continue
        sans, avec = lignes[0], lignes[1]
        rapport["pieces"][piece["nom"]] = {
            "mode": piece["mode"], "sans_min": round(sans["heures"] * 60),
            "avec_min": round(avec["heures"] * 60), "cout_temps_pct": -avec.get("gain_temps_pct", 0),
        }
    return rapport
PLANCHER_ILOTS = 50
LONG_MM = 5.0
RESISTANCE_ORCA = {
    "rectilinear": (3, 2),
    "grid": (6, 6),
    "crosshatch": (5, 5),
    "cubic": (6, 6),
    "adaptivecubic": (5, 5),
    "gyroid": (6, 6),
    "lightning": (2, 2),
    "honeycomb": (6, 6),
    "3dhoneycomb": (5, 5),
    "tpmsd": (6, 6),
    "tpmsfk": (5, 5),
}


def trajets_remplissage(gcode: str, ilot_court_mm: float = 3.0) -> dict:
    """Mesure les trajets XY de remplissage en mm ; rapport PASS/NON VÉRIFIÉ, aucune commande machine.

    Le lecteur commun conserve modes et dette de rétraction ; les mesures partielles restent explicites.
    """
    limite = float(ilot_court_mm)
    if not math.isfinite(limite) or limite < 0:
        raise ValueError("Le seuil de longueur d'îlot doit être fini et positif ou nul.")
    deplacements = retractions = courts = ilots = longs = 0
    longueur = 0.0
    lecteur = Interpreter(error_limit=128)
    dans_remplissage = False
    ilot = None

    def terminer_ilot():
        nonlocal courts, ilots, ilot
        if ilot is not None:
            ilots += 1
            courts += ilot < limite
        ilot = None

    for ligne in gcode.splitlines():
        dette_avant = lecteur.retracted.get(None, 0.0)
        mouvement = lecteur.feed_line(ligne)
        if ligne.startswith(";TYPE:"):
            terminer_ilot()
            dans_remplissage = ligne[6:].strip() == "Sparse infill"
        if mouvement is None:
            continue
        if mouvement["trajectory"] == "arc_g17_xy":
            lecteur.error("arc exact en longueur ; comparaison géométrique linéaire non vérifiée")
            terminer_ilot()
            continue
        if not dans_remplissage:
            continue
        if mouvement["extrusion"] < 0:
            terminer_ilot()
            retractions += 1
        distance_xy = math.dist(mouvement["start"][:2], mouvement["end"][:2])
        fraction_deposee = mouvement["deposited_length"] / mouvement["length"] if mouvement["length"] else 0.0
        depose = distance_xy * fraction_deposee
        # Une partie du trajet peut seulement rembourser la rétraction ; elle
        # ne constitue pas un îlot déposé (#13, contrat commun XYZ/E).
        distance = distance_xy - depose
        if distance > 0:
            terminer_ilot()
            deplacements += 1
            longueur += distance
            # Le remboursement peut finir pendant ce déplacement : l’absence
            # de dette à l’arrivée ne prouve pas un trajet sans rétraction.
            longs += distance > LONG_MM and dette_avant == 0 and mouvement["extrusion"] >= 0
        if depose > 0:
            ilot = (ilot or 0.0) + depose
    terminer_ilot()
    return {
        "deplacements": deplacements,
        "retractions": retractions,
        "longueur_deplacements_mm": round(longueur),
        "longs_sans_retraction": longs,
        "ilots": ilots,
        "ilots_courts": courts,
        "statut": "NON VÉRIFIÉ" if lecteur.errors else "PASS",
        "couverture_complete": not lecteur.errors,
        "erreurs": list(lecteur.errors),
    }


def estimations(resultat: dict) -> dict:
    """Valide les estimations slicer en précision complète avant affichage arrondi."""
    secondes = float(resultat["secondes"])
    grammes = float(resultat["grammes"])
    if not all(math.isfinite(valeur) and valeur >= 0 for valeur in (secondes, grammes)):
        raise ValueError("Estimations du slicer non finies ou négatives.")
    return {"secondes": secondes, "heures": secondes / 3600, "grammes": grammes}


def _fichier_etude(racine: Path, relatif: str) -> Path:
    chemin = Path(relatif)
    if chemin.is_absolute() or ".." in chemin.parts:
        raise ValueError("Les sources de l’étude doivent être des chemins relatifs privés.")
    candidat = racine / chemin
    if any(element.is_symlink() for element in (candidat, *candidat.parents)):
        raise ValueError("Lien symbolique dans une source d’étude refusé.")
    source = candidat.resolve(strict=True)
    if not source.is_relative_to(racine.resolve()):
        raise ValueError("Une source sort du dossier de l’étude.")
    if not source.is_file():
        raise ValueError("Fichier source ordinaire requis.")
    return source


def _estimation_affichage(resultat: dict) -> dict:
    mesures = estimations(resultat)
    mesures["avertissements"] = resultat.get("avertissements", [])
    return mesures


def main(
    fichier_etude: str | Path,
    *,
    prive: str | Path,
    dossier_sortie_precree: bool = False,
) -> dict:
    """Exécute une étude locale ; les seuls tranchages passent par le lanceur commun. Réglages en
    unités natives (mm, s, °C, %) lus via le dictionnaire typé ; aucun réglage inconnu transformé.

    `dossier_sortie_precree` est réservé au CLI après création de sa preuve INCOMPLET ;
    aucune autre sortie existante n'est acceptée.
    """
    etude = Path(fichier_etude).expanduser().resolve(strict=True)
    racine_privee = Path(prive).expanduser().resolve(strict=True)
    if not etude.is_relative_to(racine_privee):
        raise ValueError("La fiche d’étude doit rester dans base.prive.")
    donnees = json.loads(etude.read_text(encoding="utf-8"))
    if not isinstance(donnees, dict) or not isinstance(donnees.get("pieces"), list) or not donnees["pieces"]:
        raise ValueError("Étude invalide : une liste non vide de pièces est requise.")
    racine = etude.parent
    archive_base = _fichier_etude(racine, donnees["base_3mf"])
    with zipfile.ZipFile(archive_base) as archive:
        noms = archive.namelist()
        membre = "Metadata/project_settings.config"
        if noms.count(membre) != 1 or len(noms) != len(set(noms)) or archive.testzip():
            raise ValueError("Archive source dupliquée, invalide ou corrompue.")
        reglages_base = schema_reglages.decoder(archive.read(membre))
    couleurs = schema_reglages.valeur_brute(reglages_base, "filament_colour")
    if not isinstance(couleurs, list) or not couleurs:
        raise ValueError("L'étude exige un 3MF préparé avec couleurs de filaments explicitement configurées.")

    sorties = []
    index = []
    sources = [etude, archive_base]
    for piece in donnees["pieces"]:
        if not isinstance(piece, dict) or not isinstance(piece.get("variantes"), list):
            raise ValueError("Définition de pièce ou variantes invalide.")
        maillage_path = _fichier_etude(racine, piece["stl"])
        sources.append(maillage_path)
        maillage = trimesh.load_mesh(maillage_path)
        if not isinstance(maillage, trimesh.Trimesh) or maillage.is_empty:
            raise ValueError(f"Maillage invalide pour {piece.get('nom', piece['stl'])}.")
        if piece.get("rot90"):
            maillage.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [0, 0, 1]))
        bornes = maillage.bounds
        maillage.apply_translation(-bornes[0])
        configurations = [
            {
                "nom": "référence",
                "reglages": piece["reference"],
                "acceptable": True,
                "justification": "configuration actuelle",
                "zones": piece.get("reference_zones", []),
            },
            *piece["variantes"],
        ]
        for variante in configurations:
            if not isinstance(variante, dict) or not isinstance(variante.get("reglages"), dict):
                raise ValueError("Variante invalide : réglages objet requis.")
            modificateurs, bilan = remplissage_localise.volumes(maillage, variante.get("zones", []))
            identifiant = len(index) + 1
            nom_piece = str(piece["nom"])
            objet = {
                "name": f"{nom_piece}-{identifiant}",
                "mesh": maillage,
                "w": float(maillage.extents[0]),
                "h": float(maillage.extents[1]),
                "extruder": int(piece["extruder"]),
                "settings": variante["reglages"],
                "volumes": modificateurs,
            }
            sorties.append({"name": objet["name"], "items": [objet]})
            index.append(
                {
                    "piece": nom_piece,
                    **{
                        cle: variante[cle]
                        for cle in ("nom", "reglages", "acceptable", "justification")
                        if cle in variante
                    },
                    **({"zones": bilan} if bilan else {}),
                }
            )

    dossier_sortie = donnees.get("sortie")
    if not isinstance(dossier_sortie, str):
        raise ValueError("Dossier de sortie requis.")
    chemin_sortie = Path(dossier_sortie)
    if chemin_sortie.is_absolute() or ".." in chemin_sortie.parts:
        raise ValueError("La sortie doit être un chemin relatif dans l’étude privée.")
    dossier = (racine / chemin_sortie).resolve()
    if not dossier.is_relative_to(racine.resolve()) or not dossier.is_relative_to(racine_privee):
        raise ValueError("La sortie doit rester dans base.prive.")
    if dossier_sortie_precree:
        preuve = dossier / "provenance.json"
        if (
            not dossier.is_dir()
            or dossier.is_symlink()
            or not preuve.is_file()
            or preuve.is_symlink()
            or json.loads(preuve.read_text(encoding="utf-8")).get("statut") != "INCOMPLET"
            or set(dossier.iterdir()) != {preuve}
        ):
            raise FileExistsError("Dossier précréé sans preuve INCOMPLET exclusive refusé.")
    else:
        dossier.mkdir(parents=True, exist_ok=False)
    duree_tranchage = 0.0
    lots = [list(range(debut, min(debut + LOT_PLAQUES, len(sorties)))) for debut in range(0, len(sorties), LOT_PLAQUES)]
    for numero_lot, lot in enumerate(lots, 1):
        destination_lot = dossier if len(lots) == 1 else dossier / f"lot-{numero_lot}"
        destination_lot.mkdir(exist_ok=True)
        plaques = [sorties[index_plaque] for index_plaque in lot]
        for position, plaque in enumerate(plaques):
            anycubic.ranger(plaque["items"], position, len(plaques), 10.0, cli=True)
        source_tranche = anycubic.ecrire_3mf(
            plaques,
            reglages_base,
            destination_lot / "etude-plaques.3mf",
            cli=True,
            canonique=True,
            nom_source="etude-plaques.3mf",
        )
        tranche, duree = anycubic.trancher(
            source_tranche,
            destination_lot,
            "etude-tranche.3mf",
            usage="impression",
            prive=racine_privee,
        )
        duree_tranchage += float(duree)
        resultats_plaques = sorted(anycubic.resultats(tranche), key=lambda resultat: resultat["index"])
        if len(resultats_plaques) != len(lot):
            raise RuntimeError(f"lot {numero_lot} : nombre de plaques tranchées inattendu")
        membres = set(anycubic.membres_gcode_3mf(tranche))
        for resultat, indice in zip(resultats_plaques, lot):
            ligne = index[indice]
            ligne.update(_estimation_affichage(resultat))
            membre = f"Metadata/plate_{resultat['index']}.gcode"
            if membre not in membres:
                raise ValueError(f"G-code absent pour la plaque {resultat['index']}.")
            gcode = "\n".join(iter_lignes_gcode_3mf(tranche, membre))
            ligne["remplissage"] = trajets_remplissage(gcode)

    alertes = {}
    synthese = {}
    lignes = [
        (
            "| Pièce | Configuration | Temps | Matière | Résistance X-Y/Z (indice Orca) | Déplacements remplissage | "
            "Longs sans rétraction | Îlots < 3 mm | Acceptable | Gain temps | Gain matière |"
        ),
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for piece in donnees["pieces"]:
        resultats_piece = [ligne for ligne in index if ligne["piece"] == piece["nom"]]
        reference = resultats_piece[0]
        for ligne in resultats_piece:
            gain_temps = (1 - ligne["heures"] / reference["heures"]) * 100 if reference["heures"] else 0
            gain_matiere = (1 - ligne["grammes"] / reference["grammes"]) * 100 if reference["grammes"] else 0
            ligne.update(
                gain_temps_pct=round(gain_temps, 1),
                gain_matiere_pct=round(gain_matiere, 1),
                economie_g=round(reference["grammes"] - ligne["grammes"], 1),
                economie_min=round((reference["heures"] - ligne["heures"]) * 60, 1),
            )
            ligne["resistance_calculee"] = remplissage_resistance(piece, reglages_base, ligne)
            motif = str(
                ligne["reglages"].get("sparse_infill_pattern", reglages_base.get("sparse_infill_pattern", ""))
            ).lower()
            ligne["resistance_orca"] = RESISTANCE_ORCA.get(motif)
            trajets = ligne.get("remplissage") or {}
            alertes_ligne = []
            if trajets.get("ilots_courts", 0) >= PLANCHER_ILOTS:
                alertes_ligne.append(
                    f"{trajets['ilots_courts']} îlots courts et {trajets['longs_sans_retraction']} "
                    "longs déplacements sans rétraction : risque de fils et de branches qui penchent"
                )
            if motif == "lightning" and piece.get("usage") not in (None, "decoratif"):
                alertes_ligne.append("Lightning sur une pièce non décorative : aucun rôle de résistance interne")
            if alertes_ligne:
                ligne["alerte_imprimabilite"] = " ; ".join(alertes_ligne)
                alertes.setdefault(piece["nom"], {})[ligne["nom"]] = ligne["alerte_imprimabilite"]
            temps = round(ligne["heures"] * 60)
            indice_orca = "/".join(map(str, ligne["resistance_orca"])) if ligne["resistance_orca"] else "?"
            lignes.append(
                f"| {piece['nom']} | {ligne['nom']} | {temps // 60} h {temps % 60:02d} | {ligne['grammes']} g | "
                f"{indice_orca} | {trajets.get('deplacements', '?')} | {trajets.get('longs_sans_retraction', '?')} | "
                f"{trajets.get('ilots_courts', '?')} | {'oui' if ligne.get('acceptable') else 'non'} | "
                f"{gain_temps:+.1f} % | {gain_matiere:+.1f} % |"
            )
        acceptables = [
            ligne
            for ligne in resultats_piece
            if ligne.get("acceptable") and ligne.get("resistance_calculee", {}).get("statut") != "FAIL"
        ]
        synthese[piece["nom"]] = min(acceptables, key=cle_classement)["nom"] if acceptables else None

    rapport = {
        "etude": etude.name,
        "tranchage_s": duree_tranchage,
        "resultats": index,
        "meilleure_acceptable": synthese,
        "alertes_imprimabilite": alertes,
        "validation_physique": False,
    }
    (dossier / "comparaison.json").write_text(
        json.dumps(rapport, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8"
    )
    texte = "\n".join(lignes) + "\n\nEstimations du slicer ; résistance et imprimabilité non validées physiquement.\n"
    (dossier / "comparaison.md").write_text(texte, encoding="utf-8")
    return rapport


def remplissage_resistance(piece: dict, base: dict, variante: dict) -> dict:
    """Évalue la résistance numérique de réglages projet+variante ; ne modifie rien et ne valide pas physiquement.

    Entrées : pièce, réglages source et ligne de variante. Sortie : rapport PASS/WARN/FAIL/NON VÉRIFIÉ.
    Les surcharges de `variante["reglages"]` sont conservées exactement (docs/reference/profils-et-procedes.md).
    """
    from benchab.fabrication.resistance import evaluer_piece

    return evaluer_piece(
        piece.get("efforts"),
        piece.get("usage", "non_declare"),
        {**base, **variante.get("reglages", {})},
    )


def cli(argv: list[str] | None = None) -> int:
    """Valide les chemins, crée un dossier neuf, puis laisse une preuve INCOMPLET en cas d'échec."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prive", type=Path, required=True)
    parser.add_argument("etude", type=Path)
    args = parser.parse_args(argv)
    try:
        prive = args.prive.expanduser().resolve(strict=True)
        etude = args.etude.expanduser().resolve(strict=True)
        if not etude.is_relative_to(prive):
            raise ValueError("La fiche d’étude doit rester sous base.prive.")
        configuration = json.loads(etude.read_text(encoding="utf-8"))
        if not isinstance(configuration, dict) or not isinstance(configuration.get("pieces"), list):
            raise ValueError("Étude JSON invalide.")
        racine = etude.parent
        fichiers = [etude, _fichier_etude(racine, configuration["base_3mf"])]
        fichiers.extend(_fichier_etude(racine, piece["stl"]) for piece in configuration["pieces"])
        sortie = configuration.get("sortie")
        if not isinstance(sortie, str) or Path(sortie).is_absolute() or ".." in Path(sortie).parts:
            raise ValueError("La sortie doit être un chemin relatif à l’étude.")
        dossier = (racine / sortie).resolve()
        if not dossier.is_relative_to(prive):
            raise ValueError("La sortie de l’étude doit rester sous base.prive.")
        empreintes = {fichier.relative_to(prive).as_posix(): sha256_fichier(fichier) for fichier in fichiers}
        dossier.mkdir(parents=True, exist_ok=False)
        preuve = dossier / "provenance.json"
        preuve.write_text(
            json.dumps({"statut": "INCOMPLET", "sources_sha256": empreintes}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        resultat = main(etude, prive=prive, dossier_sortie_precree=True)
        apres = {fichier.relative_to(prive).as_posix(): sha256_fichier(fichier) for fichier in fichiers}
        if apres != empreintes:
            raise ValueError("Une source a changé pendant la comparaison.")
        # Le nom unique conserve tout voisin et l'ancien état en cas d'échec (#17).
        from benchab.qualite.ecritures import remplacer_texte

        remplacer_texte(
            preuve,
            json.dumps(
                {"statut": "TERMINE", "sources_sha256": empreintes, "validation_physique": False},
                ensure_ascii=False,
                indent=2,
            ),
        )
        return 0 if resultat.get("resultats") else 1
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, zipfile.BadZipFile) as erreur:
        parser.error(str(erreur))
    return 2


__all__ = [
    "CLES_REMPLISSAGE",
    "FAMILLES",
    "HERITES_REMPLISSAGE",
    "JUSTIFICATION",
    "LONG_MM",
    "PLANCHER_ILOTS",
    "RESISTANCE_ORCA",
    "cle_classement",
    "cli",
    "construire_pieces_remplissages",
    "estimations",
    "main",
    "rapport_lissage",
    "rapport_remplissages",
    "trajets_remplissage",
]
