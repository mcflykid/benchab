"""Verrou de séance à jeton privé, compatible avec les ateliers existants."""

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone

DUREE_VERROU = timedelta(hours=24)


def _maintenant():
    return datetime.now(timezone.utc)


def _empreinte(jeton):
    return hashlib.sha256(jeton.encode("utf-8")).hexdigest()


def _active(connexion):
    return connexion.execute("SELECT id, lot, debut FROM seances WHERE fin IS NULL").fetchone()


def _verrou(connexion, identifiant):
    return connexion.execute(
        "SELECT json_extract(donnees_json,'$.empreinte'), json_extract(donnees_json,'$.expire') "
        "FROM decisions WHERE json_extract(donnees_json,'$.type')='verrou_seance' "
        "AND json_extract(donnees_json,'$.seance_id')=? ORDER BY rowid DESC LIMIT 1",
        (identifiant,),
    ).fetchone()


def _valide(verrou):
    return verrou is not None and datetime.fromisoformat(verrou[1]) > _maintenant()


def active_valide(connexion):
    """Renvoie la séance au verrou actif ou None, sans rattacher de mesure à un verrou orphelin."""
    actif = _active(connexion)
    return actif if actif and _valide(_verrou(connexion, actif["id"])) else None


def _trace(connexion, identifiant, action, **details):
    import json

    connexion.execute("INSERT INTO decisions VALUES (?,NULL,?,?,?,?)", (
        uuid.uuid4().hex, _maintenant().isoformat(), f"Verrou de séance : {action}",
        "journal de séance BENCHAB : outil", json.dumps({
            "type": "verrou_seance", "seance_id": identifiant, "action": action, **details,
        }, ensure_ascii=False),
    ))


def ouvrir(base, lot=None, *, reprise=False):
    """Ouvre ou reprend une séance et rend son jeton, valide 24 h. Seule son empreinte est conservée."""
    jeton = secrets.token_urlsafe(32)
    instant = _maintenant()
    with base.transaction() as connexion:
        # Réserver l’écriture avant la lecture évite que deux débuts passent
        # ensemble. Les anciens verrous restent récupérables (cycle-projet.md).
        connexion.execute("BEGIN IMMEDIATE")
        actif = _active(connexion)
        if reprise:
            if actif is None:
                raise ValueError("Aucune séance orpheline à reprendre. Utiliser benchab seance debut.")
            if _valide(_verrou(connexion, actif["id"])):
                raise ValueError("Séance déjà active, verrou valide. Le propriétaire peut la terminer.")
            identifiant, action = actif["id"], "reprise"
        else:
            if actif is not None:
                if _valide(_verrou(connexion, actif["id"])):
                    raise ValueError("Une séance est déjà active. Consulter benchab seance etat.")
                raise ValueError("Séance orpheline. Utiliser benchab seance reprendre.")
            identifiant, action = uuid.uuid4().hex, "début"
            connexion.execute(
                "INSERT INTO seances (id,debut,lot) VALUES (?,?,?)", (identifiant, instant.isoformat(), lot),
            )
        _trace(connexion, identifiant, action, empreinte=_empreinte(jeton),
               expire=(instant + DUREE_VERROU).isoformat())
    return jeton


def fermer(base, jeton, *, forcer=False, candidates=0, temps_vote=None, temps_controles=None):
    """Ferme la séance avec un jeton valide ou une dérogation tracée, durées en secondes."""
    with base.transaction() as connexion:
        connexion.execute("BEGIN IMMEDIATE")
        actif = _active(connexion)
        if actif is None:
            raise ValueError("Aucune séance active")
        verrou = _verrou(connexion, actif["id"])
        if not forcer:
            if not _valide(verrou):
                raise ValueError("Séance orpheline. Utiliser benchab seance reprendre ou fin --forcer.")
            if not jeton or not hmac.compare_digest(_empreinte(jeton), verrou[0]):
                raise ValueError("Jeton de séance requis dans BENCHAB_JETON_SEANCE.")
        if forcer:
            _trace(connexion, actif["id"], "fin forcée")
        connexion.execute(
            "UPDATE seances SET fin=?, candidates=?, temps_vote_s=COALESCE(?,temps_vote_s), "
            "temps_controles_s=COALESCE(?,temps_controles_s) WHERE id=?",
            (_maintenant().isoformat(), candidates, temps_vote, temps_controles, actif["id"]),
        )


def etat_verrou(connexion, identifiant):
    """Indique ACTIF ou ORPHELIN et son expiration, sans divulguer le jeton."""
    verrou = _verrou(connexion, identifiant)
    return {
        "statut": "ACTIF" if _valide(verrou) else "ORPHELIN",
        "expiration": verrou[1] if verrou else None,
    }
