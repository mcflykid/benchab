"""Mesure en flux la continuité des parois extérieures.

L'interpréteur partagé d'Impression-3D n'est pas repris tel quel : il conserve
des événements et erreurs par fichier, alors que ce contrôle doit borner sa
mémoire et ne retenir que des compteurs par couche. Les règles modales
d'extrusion (M82/M83/G92), de rétraction et de mouvement sont reprises ici.
Les heuristiques et seuils sont absolus, configurables et ne sont pas des
spécifications fabricant.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from benchab.gcode.lecture import analyser_membre_gcode_3mf

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
_MOTS = re.compile(r"([A-Z])\s*(" + _NUMBER.pattern + r")", re.I)
_CONFIG = re.compile(r"^;\s*outer_wall_acceleration\s*=\s*(" + _NUMBER.pattern + r")\s*$", re.I)
_Z_META = re.compile(r"^;\s*Z\s*:\s*(" + _NUMBER.pattern + r")\s*$", re.I)


class _Etat:
    def __init__(self, intervalle_z_mm, longueur_micro_mm):
        self.xyz = [0.0, 0.0, 0.0]
        self.xyz_relatif = False
        self.e_relatif = None
        self.e = 0.0
        self.retraction = 0.0
        self.outil = 0
        self.extrudeurs = {}
        self.role = ""
        self.z_meta = None
        self.acceleration = None
        self.acceleration_configuree = None
        self.incomplet = False
        self.derniere_fin = None
        self.stats = {}
        self.intervalle_z_mm = intervalle_z_mm
        self.longueur_micro_mm = longueur_micro_mm

    def _couche_stats(self, z):
        if z not in self.stats:
            self.stats[z] = {
                "morceaux": 0,
                "segments": 0,
                "microsegments": 0,
                "accelerations": Counter(),
            }
        return self.stats[z]

    def ligne(self, ligne):
        ligne = ligne.strip()
        if not ligne:
            return
        config = _CONFIG.match(ligne)
        if config:
            self.acceleration_configuree = float(config.group(1))
        zmatch = _Z_META.match(ligne)
        if zmatch:
            self.z_meta = float(zmatch.group(1))
        if ligne.upper().startswith(";TYPE:"):
            self.role = ligne[6:].strip().lower()

        commande = ligne.split(";", 1)[0].strip()
        commande = re.sub(r"\([^)]*\)", "", commande).strip()
        commande = re.sub(r"^N\d+\s*", "", commande, flags=re.I).split("*", 1)[0]
        if not commande:
            return
        op = commande.split()[0].upper()
        if re.match(r"^T(?:[-+]?\d|$)", op):
            selection = re.fullmatch(r"T([0-3])", commande, re.I)
            if selection is None:
                self.incomplet = True
                self.derniere_fin = None
                return
            outil = int(selection.group(1))
            if outil != self.outil:
                self.extrudeurs[self.outil] = (self.e, self.retraction)
                self.e, self.retraction = self.extrudeurs.get(outil, (0.0, 0.0))
                self.outil = outil
                self.derniere_fin = None
            return
        if op == "M82":
            self.e_relatif = False
            return
        if op == "M83":
            self.e_relatif = True
            return
        if op == "G90":
            self.xyz_relatif = False
            return
        if op == "G91":
            self.xyz_relatif = True
            return
        if op == "G92":
            mots = {k.upper(): float(v) for k, v in _MOTS.findall(commande[len(op) :])}
            if not mots or any(k not in "XYZE" for k in mots):
                self.incomplet = True
                self.derniere_fin = None
                return
            if set(mots) & {"X", "Y", "Z"}:
                self.incomplet = True
                self.derniere_fin = None
                return
            if "E" in mots:
                self.e = mots["E"]
            self.derniere_fin = None
            return
        if op == "M204":
            mots = {k.upper(): float(v) for k, v in _MOTS.findall(commande[len(op) :])}
            valeur = mots.get("S", mots.get("P"))
            if valeur is not None and math.isfinite(valeur) and valeur >= 0:
                self.acceleration = valeur
            else:
                self.incomplet = True
            return
        if op not in {"G0", "G00", "G1", "G01"}:
            if re.match(r"^G0*[01235][XYZEFIJKR]", op):
                self.incomplet = True  # Format compact non interprété par ce lecteur borné.
                self.derniere_fin = None
            if op in {"G2", "G3", "G20", "G5", "G10", "G11", "M200", "M220", "M221"}:
                self.incomplet = True
                self.derniere_fin = None
            return

        mots_liste = _MOTS.findall(commande[len(op) :])
        mots = {k.upper(): float(v) for k, v in mots_liste}
        if len(mots) != len(mots_liste) or any(k not in "XYZEF" for k in mots):
            self.incomplet = True
            self.derniere_fin = None
            return
        if not (set(mots) & {"X", "Y", "Z", "E"}):
            return  # Les changements de débit seuls ne coupent pas une trajectoire.
        depart = self.xyz.copy()
        fin = [
            depart[i] + mots.get(axe, 0.0) if self.xyz_relatif else mots.get(axe, depart[i])
            for i, axe in enumerate("XYZ")
        ]
        self.xyz = fin
        z = fin[2] if "Z" in mots else (self.z_meta if self.z_meta is not None else fin[2])

        de = 0.0
        if "E" in mots:
            if self.e_relatif is None:
                self.incomplet = True
                self.derniere_fin = None
                return
            de = mots["E"] if self.e_relatif else mots["E"] - self.e
            self.e = self.e + de if self.e_relatif else mots["E"]
        if de < 0:
            self.retraction += -de
            self.derniere_fin = None
            return
        depose = max(0.0, de - self.retraction)
        self.retraction = max(0.0, self.retraction - de)
        longueur = math.dist(depart, fin)
        if depose <= 0 or longueur <= 0:
            self.derniere_fin = None
            return
        if self.intervalle_z_mm is not None and not (self.intervalle_z_mm[0] <= z <= self.intervalle_z_mm[1]):
            self.derniere_fin = None
            return
        role_exterieur = self.role in {"outer wall", "outerwall", "external perimeter"}
        if not role_exterieur:
            self.derniere_fin = None
            return

        stats = self._couche_stats(round(z, 3))
        fraction_deposee = min(1.0, depose / de)
        longueur_deposee = longueur * fraction_deposee
        debut_depose = [
            depart[i] + (fin[i] - depart[i]) * (1.0 - fraction_deposee)
            for i in range(3)
        ]
        contigu = self.derniere_fin is not None and math.dist(self.derniere_fin, debut_depose) <= 0.02
        if not contigu:
            stats["morceaux"] += 1
        stats["segments"] += 1
        if longueur_deposee < self.longueur_micro_mm:
            stats["microsegments"] += 1
        if self.acceleration is not None:
            stats["accelerations"][self.acceleration] += 1
        self.derniere_fin = fin


class _PlaqueAmbigue(ValueError):
    """Un 3MF multi-plaques doit être sélectionné explicitement."""


def _ouvrir_gcode(chemin, plaque=None):
    """Retourne un flux texte et son propriétaire ZIP éventuel."""
    chemin = Path(chemin)
    if chemin.suffix.lower() != ".3mf":
        return chemin.open("r", encoding="utf-8", errors="replace"), None
    archive = ZipFile(chemin)
    membres = [n for n in archive.namelist() if re.fullmatch(r"Metadata/plate_\d+\.gcode", n)]
    if plaque is not None:
        membres = [n for n in membres if n == f"Metadata/plate_{int(plaque)}.gcode"]
    if len(membres) != 1:
        archive.close()
        if not membres:
            raise _PlaqueAmbigue("aucun G-code pour la plaque sélectionnée")
        raise _PlaqueAmbigue("plusieurs plaques G-code sans sélection")
    return archive.open(membres[0]), archive


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
                     reference=None):
    """Interprète les lignes partagées, en gardant O(couches) en mémoire."""
    intervalle_z_mm, seuils, longueur_micro_mm, relation_reference = _options(
        intervalle_z_mm, seuils_absolus, longueur_micro_mm, reference
    )
    etat = _Etat(intervalle_z_mm, longueur_micro_mm)
    for brut in lignes:
        etat.ligne(brut.decode("utf-8", "replace") if isinstance(brut, bytes) else brut)

    couches = []
    max_morceaux = 0
    max_fraction_micro = 0.0
    accelerations = Counter()
    for z, stats in sorted(etat.stats.items()):
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
    """Analyse en flux et réutilise les résultats 3MF par empreinte, plaque, options et version du lecteur."""
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

    entree, archive = _ouvrir_gcode(chemin, plaque)
    if archive is not None:
        membre = entree.name
        entree.close()
        archive.close()
        version = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        return analyser_membre_gcode_3mf(chemin, membre, options, calculer, version=version)
    try:
        return calculer(entree)
    finally:
        entree.close()


def controler(chemin, plaque=None, intervalle_z_mm=None, seuils_absolus=None,
              longueur_micro_mm=LONGUEUR_MICRO_MM, reference=None):
    """Contrôle envoi; les options d'analyse sont validées et les plaques ambiguës restent non vérifiées."""
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
        return {
            "controle": "continuité paroi extérieure",
            "statut": "NON VÉRIFIÉ",
            "avertissements": [
                "3MF avec plusieurs plaques : préciser la plaque; aucune n'a été analysée."
                if "plusieurs" in str(erreur)
                else "Aucun G-code pour la plaque sélectionnée."
            ],
            "validation_physique": False,
        }
    except (OSError, KeyError, BadZipFile):
        return {
            "controle": "continuité paroi extérieure",
            "statut": "NON VÉRIFIÉ",
            "avertissements": ["G-code indisponible ou plaque ambiguë."],
            "validation_physique": False,
        }
