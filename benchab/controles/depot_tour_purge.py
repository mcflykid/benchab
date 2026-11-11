"""Mesure les dépôts nominaux étiquetés Prime tower, sans attribution de purge."""

import math

from benchab.gcode.debit import mesurer
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
        """Cumule le dépôt en mm³ et garde la présence d’un événement opaque."""
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
        volume = mesurer(mouvement, self.diametres[outil])["volume_mm3"]
        segments, precedent = self.outils.get(outil, (0, 0.0))
        if volume is None or not math.isfinite(precedent + volume):
            self.omis += 1
            return
        self.outils[outil] = segments + 1, precedent + volume

    def rapport(self, lecteur):
        """Renvoie PASS pour le sous-cas couvert, sinon NON VÉRIFIÉ ; aucun volume physique."""
        outils = [
            {"outil": outil, "segments": n, "volume_nominal_mm3": volume}
            for outil, (n, volume) in sorted(self.outils.items())
        ]
        raisons = []
        if not outils:
            raisons.append("Aucun déplacement déposé de rôle Prime tower exploitable.")
        if self.omis:
            raisons.append("Outil, diamètre ou dépôt nominal incomplet.")
        if self.macro_opaque:
            raisons.append("Une macro opaque empêche de qualifier la couverture.")
        if lecteur.errors or lecteur.errors_truncated:
            raisons.append("Le lecteur signale une couverture G-code incomplète.")
        volume_total = None
        if not raisons:
            try:
                volume_total = math.fsum(x["volume_nominal_mm3"] for x in outils)
            except OverflowError:
                raisons.append("Somme nominale hors limites.")
        return {
            "statut": "NON VÉRIFIÉ" if raisons else "PASS",
            "role": "Prime tower",
            "volume_nominal_mm3": volume_total,
            "outils": outils,
            "segments_non_qualifies": self.omis,
            "raisons": raisons,
            "diagnostics_lecture": list(lecteur.errors),
            "limite": "Dépôt nominal étiqueté ; aucune attribution à une paire, ni purge physique mesurée.",
        }
