"""Mesure des appels CLI ; l'outil historique dépendait de ps, interdit dans le bac à sable."""

import resource
import sys
import time
import uuid
from datetime import datetime


class Mesure:
    def __init__(self):
        self.identifiant = uuid.uuid4().hex
        self.debut = time.perf_counter()
        self.propre = resource.getrusage(resource.RUSAGE_SELF)
        self.enfants = resource.getrusage(resource.RUSAGE_CHILDREN)

    def enregistrer(self, base, commande, code, seance_id=None):
        propre = resource.getrusage(resource.RUSAGE_SELF)
        enfants = resource.getrusage(resource.RUSAGE_CHILDREN)
        unite = 1 if sys.platform == "darwin" else 1024
        if seance_id:
            seance = base.connexion.execute("SELECT id FROM seances WHERE id = ?", (seance_id,)).fetchone()
        else:
            seance = base.connexion.execute("SELECT id FROM seances WHERE fin IS NULL").fetchone()
        duree = time.perf_counter() - self.debut
        with base.transaction() as connexion:
            connexion.execute("INSERT INTO mesures VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                self.identifiant,
                seance[0] if seance else None,
                datetime.now().astimezone().isoformat(),
                commande,
                duree,
                propre.ru_utime - self.propre.ru_utime + enfants.ru_utime - self.enfants.ru_utime,
                propre.ru_stime - self.propre.ru_stime + enfants.ru_stime - self.enfants.ru_stime,
                int(max(propre.ru_maxrss, enfants.ru_maxrss) * unite),
                code,
            ))
            nom = commande.split(maxsplit=1)[0] if commande else ""
            if seance and nom in {"controle", "vote"}:
                colonne = "temps_vote_s" if nom == "vote" else "temps_controles_s"
                connexion.execute(f"UPDATE seances SET {colonne} = {colonne} + ? WHERE id = ?", (duree, seance[0]))
