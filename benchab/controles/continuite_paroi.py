"""Contrôle d'envoi spécialisé de continuité des parois extérieures."""

from benchab.gcode.continuite import controler

__all__ = ["controler", "resumer"]


def resumer(rapport, destination):
    """Renvoie verdict, source, compteurs et chemin du rapport complet publié.

    Les compteurs sont sans unité. Les avertissements généraux sont conservés ;
    les détails par couche, en mm, et diagnostics restent dans le rapport.
    Ne relit ni ne modifie aucun fichier ni réglage.
    """
    source = rapport["source"]
    return {
        "statut": rapport["statut"],
        "code": rapport["code"],
        "source": source["fichier"],
        "plaque": source["plaque"],
        "sha256": source["sha256"],
        "rapport": str(destination),
        "couverture_complete": rapport.get("couverture_complete"),
        "couches_analysees": rapport.get("couches_analysees", 0),
        "bandes_z": len(rapport.get("bandes_z", ())),
        "avertissements": rapport.get("avertissements", ()),
    }
