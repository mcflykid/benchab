"""Mesure les dépôts nominaux étiquetés Prime tower, sans attribution de purge."""

import math

from benchab.gcode.debit import mesurer, positif
from benchab.reglages import valeur_indice


class ObservationTour:
    """Observe une plaque ; outils indexés à zéro, dépôt nominal en mm³."""

    def __init__(self, reglages, nombre_slots):
        """Reçoit les réglages typés du projet et le nombre de slots déclaré."""
        self.reglages = reglages
        self.nombre_slots = nombre_slots
        self.outils = {}
        self.diametres = {}
        self.omis = 0
        self.macro_opaque = False

    def ajouter(self, mouvement, evenement):
        """Cumule le dépôt en mm³ et sa durée nominale en s, en gardant les macros opaques."""
        if evenement and evenement.get("opaque"):
            self.macro_opaque = True
        # ASN ExtrusionEntity.cpp : erWipeTower est étiqueté « Prime tower ».
        # Ni Custom, ni la ligne de démarrage ne désignent une tour (docs/reference/purge-changements.md).
        if (
            not mouvement or mouvement["type"] != "Prime tower" or mouvement["deposited"] <= 0
            or mouvement["deposited_length"] <= 0
        ):
            return
        outil = mouvement["tool"]
        if outil is None or self.nombre_slots is None or not 0 <= outil < self.nombre_slots:
            self.omis += 1
            return
        if outil not in self.diametres:
            self.diametres[outil] = valeur_indice(self.reglages, "filament_diameter", outil, lecteur="index_exact")
        mesure = mesurer(mouvement, self.diametres[outil])
        volume, duree = mesure["volume_mm3"], mesure["duree_depot_s"]
        segments, precedent, secondes = self.outils.get(outil, (0, 0.0, 0.0))
        if volume is None or not math.isfinite(precedent + volume) or not math.isfinite(secondes + duree):
            self.omis += 1
            return
        self.outils[outil] = segments + 1, precedent + volume, secondes + duree

    def rapport(self, lecteur):
        """Rapporte le dépôt en mm³, la durée nominale en s et la masse en g.

        PASS s’applique au dépôt couvert. Une densité absente garde la masse
        NON VÉRIFIÉE ; la durée réelle et la purge physique restent inconnues.
        """
        outils, masses, durees = [], [], []
        for outil, (n, volume, duree) in sorted(self.outils.items()):
            densite = positif(valeur_indice(self.reglages, "filament_density", outil, lecteur="index_exact"))
            masse = volume / 1000 * densite if densite is not None else None
            masse = masse if masse is not None and math.isfinite(masse) and masse > 0 else None
            outils.append({
                "outil": outil, "segments": n, "volume_nominal_mm3": volume, "temps_depot_nominal_s": duree,
                "masse_nominale_g": masse, "densite_embarquee_g_cm3": densite,
            })
            masses.append(masse)
            durees.append(duree)
        raisons = []
        if not outils:
            raisons.append("Aucun déplacement déposé de rôle Prime tower exploitable.")
        if self.omis:
            raisons.append("Outil, diamètre ou dépôt nominal incomplet.")
        if self.macro_opaque:
            raisons.append("Une macro opaque empêche de qualifier la couverture.")
        if lecteur.errors or lecteur.errors_truncated:
            raisons.append("Le lecteur signale une couverture G-code incomplète.")
        volume_total = temps_total = masse_totale = None
        if not raisons:
            try:
                volume_total = math.fsum(x["volume_nominal_mm3"] for x in outils)
                temps_total = math.fsum(durees)
            except OverflowError:
                volume_total = temps_total = None
                raisons.append("Somme nominale hors limites.")
        if not raisons and all(masse is not None for masse in masses):
            try:
                masse_totale = math.fsum(masses)
            except OverflowError:
                pass
        return {
            "statut": "NON VÉRIFIÉ" if raisons else "PASS",
            "role": "Prime tower",
            "volume_nominal_mm3": volume_total,
            "temps_depot_nominal_s": temps_total,
            "temps_reel_s": None,
            "masse_nominale_g": masse_totale,
            "statut_masse": "PASS" if masse_totale is not None else "NON VÉRIFIÉ",
            "source_densite": "Metadata/project_settings.config / filament_density, index exact",
            "outils": outils,
            "segments_non_qualifies": self.omis,
            "raisons": raisons,
            "diagnostics_lecture": list(lecteur.errors),
            "limite": "Dépôt nominal étiqueté ; aucune attribution à une paire, ni purge physique mesurée.",
        }
