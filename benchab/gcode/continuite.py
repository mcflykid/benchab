"""Mesure en flux la continuité des parois extérieures avec l’interpréteur partagé.

Les compteurs restent par couche et les diagnostics du lecteur sont bornés.
Les heuristiques et seuils configurables ne sont pas des spécifications fabricant.
"""

from __future__ import annotations

import hashlib
import inspect
import math
import re
from collections import Counter
from pathlib import Path
from zipfile import BadZipFile

from benchab import reglages
from benchab.archives_3mf import Archive3MF
from benchab.controles.gcode_interpreter import Interpreter, axes_homing
from benchab.gcode.geometrie_arc import point_debut_deposee_xyz
from benchab.gcode.lecture import analyser_membre_gcode_3mf, plaques_gcode_archive

SEUILS_DEFAUT = {
    "morceaux_par_couche": 10,
    "fraction_microsegments": 0.30,
    "acceleration_exterieure_mm_s2": 4000.0,
}
LONGUEUR_MICRO_MM = 0.3
RELATIONS_REFERENCE = {
    "meme_source_modele",
    "seulement_meme_machine_filament",
    "inconnue",
}
_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)")
_CONFIG = re.compile(r"^;\s*outer_wall_acceleration\s*=\s*(" + _NUMBER.pattern + r")\s*$", re.I)
_Z_META = re.compile(r"^;\s*Z\s*:\s*(" + _NUMBER.pattern + r")\s*$", re.I)


class _Etat:
    """Cumule les mesures par couche ; aucun état modal dupliqué ni commande machine."""

    def __init__(self, intervalle_z_mm, longueur_micro_mm, mode_vase=False):
        """Initialise compteurs et lecteur borné ; longueurs en mm, 128 diagnostics au plus puis résumé."""
        self.lecteur = Interpreter(tool=0, error_limit=128)
        self.role = ""
        self.z_meta = None
        self.acceleration = None
        self.acceleration_configuree = None
        self.incomplet = False
        self.homing_non_verifie = False
        self.derniere_fin = None
        self.stats = {}
        self.intervalle_z_mm = intervalle_z_mm
        self.longueur_micro_mm = longueur_micro_mm
        self.mode_vase = mode_vase
        self.tour_sans_repere = False

    def _couche_stats(self, z):
        """Renvoie les compteurs de la couche à Z en mm, sans réinterpréter les trajectoires."""
        if z not in self.stats:
            self.stats[z] = {"morceaux": 0, "segments": 0, "microsegments": 0, "accelerations": Counter()}
        return self.stats[z]

    def _evenement(self, evenement, ancien_outil):
        """Applique les seules décisions métier du contrôle ; accélérations en mm/s², sans réinterprétation modale."""
        op = evenement.get("command")
        mots = evenement.get("parameters", {})
        if self.lecteur.tool != ancien_outil or evenement.get("kind") == "homing":
            self.derniere_fin = None
        if evenement.get("kind") == "homing" and "Z" in mots:
            self.z_meta = None
        # G92 ne déplace pas la trajectoire continue ; ses coordonnées de commande
        # restent distinctes du repère spatial (docs/reference/continuite-paroi.md).
        if evenement.get("homing_unverified"):
            self.homing_non_verifie = True
        if op == "M204":
            valeur = mots.get("S", mots.get("P"))
            if valeur is not None and math.isfinite(valeur) and valeur >= 0:
                self.acceleration = valeur
            else:
                self.incomplet = True

    def ligne(self, ligne):
        """Consomme une ligne et cumule les dépôts ; mm et mm/s², aucune commande exécutée."""
        ligne = ligne.strip()
        config = _CONFIG.match(ligne)
        if config:
            self.acceleration_configuree = float(config.group(1))
        zmatch = _Z_META.match(ligne)
        if zmatch:
            self.z_meta = float(zmatch.group(1))
        if ligne.upper().startswith(";TYPE:"):
            self.role = ligne[6:].strip().lower()
        ancien_outil = self.lecteur.tool
        mouvement = self.lecteur.feed_line(ligne)
        evenement = self.lecteur.last_event or {}
        self._evenement(evenement, ancien_outil)
        if self.lecteur.errors:
            self.incomplet = True
            self.derniere_fin = None
        if mouvement is None:
            return
        debut_depot = mouvement["deposition_start"]
        if mouvement["op"] in {"G2", "G3"}:
            # Seul G17 à Z strictement constant et marqué par le slicer comme
            # paroi extérieure entre dans le calcul de continuité (#123).
            if (
                self.role not in {"outer wall", "outerwall", "external perimeter"}
                or mouvement["start"][2] != mouvement["end"][2]
            ):
                self.incomplet = True
                self.derniere_fin = None
                return
            if mouvement["deposited_length"] <= 0:
                self.derniere_fin = None
                return
            try:
                debut_depot = point_debut_deposee_xyz(mouvement)
            except ValueError:
                self.incomplet = True
                self.derniere_fin = None
                return
        # Un changement F seul n’interrompt pas le dépôt précédent ; les modes,
        # rétractions et trajectoires sont déjà résolus par le lecteur commun.
        if not set(evenement.get("parameters", {})) & {"X", "Y", "Z", "E"}:
            return
        if mouvement["deposited_length"] <= 0:
            self.derniere_fin = None
            return
        fin = mouvement["spatial_end"]
        z = fin[2] if "Z" in evenement.get("parameters", {}) else self.z_meta
        z = fin[2] if z is None else z
        if self.intervalle_z_mm is not None and not self.intervalle_z_mm[0] <= z <= self.intervalle_z_mm[1]:
            self.derniere_fin = None
            return
        if self.role not in {"outer wall", "outerwall", "external perimeter"}:
            self.derniere_fin = None
            return
        if self.mode_vase:
            # Le mouvement en spirale change de Z à chaque segment sans créer une nouvelle couche (#246).
            if self.lecteur.layer < 0 or self.z_meta is None:
                self.tour_sans_repere = True
                self.incomplet = True
                self.derniere_fin = None
                return
            cle = (self.lecteur.layer, round(self.z_meta, 3))
        else:
            cle = round(z, 3)
        stats = self._couche_stats(cle)
        contigu = self.derniere_fin is not None and math.dist(self.derniere_fin, debut_depot) <= 0.02
        if not contigu:
            stats["morceaux"] += 1
        stats["segments"] += 1
        if mouvement["deposited_length"] < self.longueur_micro_mm:
            stats["microsegments"] += 1
        if self.acceleration is not None:
            stats["accelerations"][self.acceleration] += 1
        self.derniere_fin = fin


class _PlaqueAmbigue(ValueError):
    """Un 3MF multi-plaques doit être sélectionné explicitement."""


def _options(intervalle_z_mm, seuils_absolus, longueur_micro_mm, reference):
    if intervalle_z_mm is not None:
        if not isinstance(intervalle_z_mm, (tuple, list)) or len(intervalle_z_mm) != 2:
            raise ValueError("intervalle_z_mm doit être None ou une paire de bornes")
        bas, haut = intervalle_z_mm
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (bas, haut)):
            raise ValueError("bornes Z doivent être des nombres finis")
        if bas >= haut:
            raise ValueError("intervalle Z incohérent : borne basse doit être inférieure à la borne haute")
        intervalle_z_mm = (float(bas), float(haut))

    seuils = dict(SEUILS_DEFAUT)
    if seuils_absolus is not None:
        if not isinstance(seuils_absolus, dict):
            raise ValueError("seuils_absolus doit être un dictionnaire")
        inconnus = set(seuils_absolus) - set(SEUILS_DEFAUT)
        if inconnus:
            raise ValueError("seuil absolu inconnu : " + ", ".join(sorted(inconnus)))
        seuils.update(seuils_absolus)
    fragments = seuils["morceaux_par_couche"]
    if isinstance(fragments, bool) or not isinstance(fragments, int) or fragments < 1:
        raise ValueError("morceaux_par_couche doit être un entier positif")
    for cle in ("fraction_microsegments", "acceleration_exterieure_mm_s2"):
        valeur = seuils[cle]
        if isinstance(valeur, bool) or not isinstance(valeur, (int, float)) or not math.isfinite(valeur) or valeur <= 0:
            raise ValueError(f"{cle} doit être un nombre fini positif")
    if seuils["fraction_microsegments"] > 1:
        raise ValueError("fraction_microsegments doit être au plus 1")
    if (
        isinstance(longueur_micro_mm, bool)
        or not isinstance(longueur_micro_mm, (int, float))
        or not math.isfinite(longueur_micro_mm)
        or longueur_micro_mm <= 0
    ):
        raise ValueError("longueur_micro_mm doit être un nombre fini positif")

    relation_reference = None
    if reference is not None:
        relation = reference.get("relation") if isinstance(reference, dict) else None
        if not isinstance(relation, str) or relation not in RELATIONS_REFERENCE:
            raise ValueError("reference doit déclarer explicitement une relation reconnue")
        relation_reference = relation
    return intervalle_z_mm, seuils, float(longueur_micro_mm), relation_reference


def _analyser_lignes(lignes, intervalle_z_mm=None, seuils_absolus=None, longueur_micro_mm=LONGUEUR_MICRO_MM,
                     reference=None, mode_vase=False):
    """Interprète les lignes partagées, en gardant O(couches) en mémoire."""
    intervalle_z_mm, seuils, longueur_micro_mm, relation_reference = _options(
        intervalle_z_mm, seuils_absolus, longueur_micro_mm, reference
    )
    etat = _Etat(intervalle_z_mm, longueur_micro_mm, mode_vase)
    for brut in lignes:
        etat.ligne(brut.decode("utf-8", "replace") if isinstance(brut, bytes) else brut)

    couches = []
    max_morceaux = 0
    max_fraction_micro = 0.0
    accelerations = Counter()
    for cle, stats in sorted(etat.stats.items()):
        z = cle[1] if mode_vase else cle
        fraction = stats["microsegments"] / stats["segments"] if stats["segments"] else 0.0
        max_morceaux = max(max_morceaux, stats["morceaux"])
        max_fraction_micro = max(max_fraction_micro, fraction)
        accelerations.update(stats["accelerations"])
        couches.append(
            {
                "z_mm": z,
                "morceaux": stats["morceaux"],
                "segments": stats["segments"],
                "microsegments": stats["microsegments"],
                "fraction_microsegments": round(fraction, 6),
                "accelerations_modal_mm_s2": {str(cle): valeur for cle, valeur in stats["accelerations"].items()},
            }
        )
    accel_modal_max = max(accelerations, default=None)
    accel_config = etat.acceleration_configuree
    avertissements = []
    if etat.incomplet:
        statut = "NON VÉRIFIÉ"
        if etat.tour_sans_repere:
            avertissements.append("Mode vase : repères de couche ou d’altitude absents.")
        elif etat.homing_non_verifie:
            avertissements.append("Segment d’extrusion immédiatement après G28 exclu.")
        else:
            avertissements.append("Commande d'extrusion ou mouvement non pris en charge ; couverture incomplète.")
    elif not couches:
        statut = "NON VÉRIFIÉ"
        avertissements.append("Aucune extrusion de paroi extérieure exploitable dans l'intervalle sélectionné.")
    else:
        if max_morceaux > seuils["morceaux_par_couche"]:
            avertissements.append("Nombre de morceaux par couche au-dessus du seuil absolu.")
        if max_fraction_micro > seuils["fraction_microsegments"]:
            avertissements.append("Fraction de microsegments au-dessus du seuil absolu.")
        if accel_config is not None and accel_config > seuils["acceleration_exterieure_mm_s2"]:
            avertissements.append("Accélération extérieure configurée au-dessus du seuil absolu.")
        if accel_modal_max is not None and accel_modal_max > seuils["acceleration_exterieure_mm_s2"]:
            avertissements.append("Accélération M204 observée sur la paroi au-dessus du seuil absolu.")
        statut = "WARN" if avertissements else "PASS"

    return {
        "controle": "continuité paroi extérieure",
        "statut": statut,
        "couverture_complete": not etat.incomplet,
        "intervalle_z_mm": list(intervalle_z_mm) if intervalle_z_mm is not None else None,
        "seuils_absolus": seuils,
        "longueur_micro_mm": longueur_micro_mm,
        "heuristiques": "WARN au-delà des seuils ; repères de triage, non normes fabricant ni prédiction physique.",
        "couches_analysees": len(couches),
        "mode_vase": mode_vase,
        "max_morceaux_par_couche": max_morceaux,
        "max_fraction_microsegments": round(max_fraction_micro, 6),
        "acceleration_exterieure_configuree_mm_s2": accel_config,
        "acceleration_m204_max_observee_paroi_mm_s2": accel_modal_max,
        "accelerations_m204_observees_paroi_mm_s2": {str(cle): valeur for cle, valeur in accelerations.items()},
        "couches": couches,
        "avertissements": avertissements,
        "validation_physique": False,
        "reference": None if relation_reference is None else {
            "relation": relation_reference,
            "comparaison_geometrique_autorisee": relation_reference == "meme_source_modele",
            "identifiant": reference.get("identifiant"),
        },
    }


def analyser(chemin, plaque=None, intervalle_z_mm=None, seuils_absolus=None, longueur_micro_mm=LONGUEUR_MICRO_MM,
             reference=None):
    """Analyse en flux la continuité nominale (longueurs en mm, accélérations en mm/s²).

    Reçoit un G-code ou un 3MF et, si nécessaire, un numéro de plaque. Le
    cache lie les résultats à la version du lecteur et aux octets de la source.
    Le mode vase d’un 3MF regroupe les incréments Z par couche déclarée.
    Un numéro ambigu est refusé ; aucune écriture ou validation physique.
    """
    intervalle, seuils, longueur, _relation = _options(
        intervalle_z_mm, seuils_absolus, longueur_micro_mm, reference,
    )
    if reference is not None:
        reference = dict(reference)
        if isinstance(reference.get("fichier"), Path):
            reference["fichier"] = str(reference["fichier"])
    options = {
        "intervalle_z_mm": intervalle, "seuils_absolus": seuils,
        "longueur_micro_mm": longueur, "reference": reference,
    }

    def calculer(lignes):
        return _analyser_lignes(lignes, **options)

    if Path(chemin).suffix.lower() == ".3mf":
        with Archive3MF(chemin) as archive:
            if "Metadata/project_settings.config" in archive.noms():
                config = archive.reglages()
                options["mode_vase"] = (
                    "spiral_mode" in config and reglages.valeur_brute(config, "spiral_mode") == "1"
                )
            try:
                membres = dict(plaques_gcode_archive(archive))
            except ValueError as erreur:
                raise _PlaqueAmbigue("numéro de plaque ambigu") from erreur
            if plaque is not None:
                numero = int(plaque)
                membres = {numero: membres[numero]} if numero in membres else {}
            if len(membres) != 1:
                if not membres:
                    raise _PlaqueAmbigue("aucun G-code pour la plaque sélectionnée")
                raise _PlaqueAmbigue("plusieurs plaques G-code sans sélection")
            version = hashlib.sha256(
                Path(__file__).read_bytes()
                + inspect.getsource(Interpreter).encode()
                + inspect.getsource(axes_homing).encode()
            ).hexdigest()
            return analyser_membre_gcode_3mf(
                chemin, next(iter(membres.values())), options, calculer, version=version, archive=archive,
            )
    entree = Path(chemin).open("r", encoding="utf-8", errors="replace")
    try:
        return calculer(entree)
    finally:
        entree.close()


def controler(chemin, plaque=None, intervalle_z_mm=None, seuils_absolus=None,
              longueur_micro_mm=LONGUEUR_MICRO_MM, reference=None):
    """Retourne PASS, FAIL ou NON VÉRIFIÉ pour la continuité nominale de la paroi.

    Les mesures spatiales sont en mm et les options suivent analyser.
    Une plaque ambiguë n’est jamais analysée ; aucune écriture ni preuve physique.
    """
    _options(intervalle_z_mm, seuils_absolus, longueur_micro_mm, reference)
    if not Path(chemin).is_file():
        return {
            "controle": "continuité paroi extérieure",
            "statut": "NON VÉRIFIÉ",
            "avertissements": ["Fichier G-code absent."],
            "validation_physique": False,
        }
    try:
        rapport = analyser(chemin, plaque, intervalle_z_mm, seuils_absolus, longueur_micro_mm, reference)
        if reference and reference.get("fichier"):
            compare = controler(
                reference["fichier"], reference.get("plaque"), intervalle_z_mm,
                seuils_absolus, longueur_micro_mm,
            )
            mesures = (
                "couches_analysees", "max_morceaux_par_couche", "max_fraction_microsegments",
                "acceleration_exterieure_configuree_mm_s2", "acceleration_m204_max_observee_paroi_mm_s2",
            )
            rapport["reference"]["statut_analyse"] = compare["statut"]
            rapport["reference"]["mesures"] = {cle: compare.get(cle) for cle in mesures}
            if reference["relation"] == "meme_source_modele" and compare["statut"] != "NON VÉRIFIÉ":
                rapport["reference"]["ecarts"] = {
                    cle: rapport[cle] - compare[cle]
                    for cle in mesures if cle != "couches_analysees"
                    and rapport.get(cle) is not None and compare.get(cle) is not None
                }
            else:
                rapport["reference"]["limite"] = (
                    "Mesures de géométries distinctes présentées séparément, sans écart géométrique ni causalité."
                    if reference["relation"] != "meme_source_modele"
                    else "Référence incomplètement analysée, aucun écart calculé."
                )
        return rapport
    except _PlaqueAmbigue as erreur:
        if "plusieurs" in str(erreur):
            message = "3MF avec plusieurs plaques : préciser la plaque; aucune n'a été analysée."
        elif "ambigu" in str(erreur):
            message = "Numéro de plaque ambigu : aucun G-code n’a été analysé."
        else:
            message = "Aucun G-code pour la plaque sélectionnée."
        return {
            "controle": "continuité paroi extérieure",
            "statut": "NON VÉRIFIÉ",
            "avertissements": [message],
            "validation_physique": False,
        }
    except (OSError, ValueError, KeyError, BadZipFile):
        return {
            "controle": "continuité paroi extérieure",
            "statut": "NON VÉRIFIÉ",
            "avertissements": ["G-code indisponible ou plaque ambiguë."],
            "validation_physique": False,
        }
