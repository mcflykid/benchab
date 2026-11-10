"""Collecte directe des mesures : idempotence, unités et rattachement séance."""

from types import SimpleNamespace

from benchab.donnees import seance
from benchab.donnees.base import Base
from benchab.mesure.collecte import Mesure


def test_rejouer_identifiant_change_code_sans_doubler_mesure_ni_duree(tmp_path, monkeypatch):
    """Une reprise corrige le code, sans incrémenter deux fois la durée de contrôle."""
    base = Base(tmp_path / "prive", creer=True)
    try:
        seance.ouvrir(base)
        identifiant = "mesure-rejouee"
        mesure = Mesure()
        mesure.identifiant = identifiant
        monkeypatch.setattr("benchab.mesure.collecte.time.perf_counter", lambda: mesure.debut + 2.5)

        mesure.enregistrer(base, "controle plaques", 1)
        mesure.enregistrer(base, "controle plaques", 0)

        ligne = base.connexion.execute(
            "SELECT seance_id, code, duree_s FROM mesures WHERE id = ?", (identifiant,),
        ).fetchone()
        temps = base.connexion.execute(
            "SELECT temps_controles_s FROM seances WHERE id = ?", (ligne["seance_id"],),
        ).fetchone()[0]
        assert base.connexion.execute("SELECT count(*) FROM mesures WHERE id = ?", (identifiant,)).fetchone()[0] == 1
        assert ligne["code"] == 0
        assert ligne["duree_s"] == 2.5
        assert temps == 2.5
    finally:
        base.fermer()


def test_mesure_stocke_unites_et_seance_valide(tmp_path, monkeypatch):
    """La mesure conserve des secondes, octets et le rattachement à la séance ouverte."""
    base = Base(tmp_path / "prive", creer=True)
    try:
        seance.ouvrir(base)
        monkeypatch.setattr(
            "benchab.mesure.collecte.resource.getrusage",
            lambda _cible: SimpleNamespace(ru_utime=0.0, ru_stime=0.0, ru_maxrss=64),
        )
        monkeypatch.setattr("benchab.mesure.collecte.sys.platform", "darwin")
        mesure = Mesure()
        monkeypatch.setattr("benchab.mesure.collecte.time.perf_counter", lambda: mesure.debut + 1.25)
        mesure.enregistrer(base, "vote preparation", 0)

        ligne = base.connexion.execute("SELECT * FROM mesures WHERE id = ?", (mesure.identifiant,)).fetchone()
        assert ligne["seance_id"] is not None
        assert base.connexion.execute("SELECT id FROM seances WHERE id = ?", (ligne["seance_id"],)).fetchone()
        assert ligne["duree_s"] == 1.25
        assert ligne["cpu_utilisateur_s"] >= 0
        assert ligne["cpu_systeme_s"] >= 0
        assert ligne["memoire_max_octets"] == 64
        assert base.connexion.execute(
            "SELECT temps_vote_s FROM seances WHERE id = ?", (ligne["seance_id"],),
        ).fetchone()[0] == 1.25
    finally:
        base.fermer()
