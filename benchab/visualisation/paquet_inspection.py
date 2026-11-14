"""Paquet de consultation hors ligne, en mémoire, sans préparation de fabrication."""

import hashlib
import io
import json
import re
from zipfile import ZIP_DEFLATED, ZipFile

from benchab.visualisation.catalogue_visuel import couches, projet_courant, scene
from benchab.visualisation.hors_ligne import VENDOR


def securiser_couches(html):
    """Renvoie le HTML des couches avec une politique hors ligne ; refuse un document sans en-tête."""
    csp = (
        '<meta http-equiv="Content-Security-Policy" content="default-src &'
        "#39;none&#39;; script-src &#39;unsafe-inline&#39; &#39;self&#39; "
        "data: blob:; style-src &#39;unsafe-inline&#39;; img-src data: blo"
        'b:; font-src data:; connect-src &#39;none&#39;">'
    )
    html, n = re.subn("<head(?:\\s[^>]*)?>", lambda m: m.group(0) + csp, html, count=1, flags=re.I)
    if n != 1:
        raise ValueError("entête du visualiseur de couches absente")
    return html


def paquet(racine, nom):
    """Renvoie le ZIP de consultation en octets avec manifeste SHA ; ne lance ni tranchage ni impression."""
    projet_courant(racine, nom)
    html = scene(racine, nom).decode()
    match = re.search('(<script[^>]*id="donnees"[^>]*>)(.*?)(</script>)', html, re.S)
    if not match:
        raise ValueError("données de scène absentes")
    data = json.loads(match.group(2))
    data["source"].pop("paquet_url", None)
    contenu = {}
    manque = None
    if data["source"].get("couches_url"):
        contenu["couches.html"] = securiser_couches(couches(racine, nom).decode()).encode()
        data["source"]["couches_url"] = "couches.html"
    else:
        manque = "Aucune couche conservée ; non exportée, aucune fabrication lancée."
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    html = html[: match.start(2)] + payload + html[match.end(2) :]
    contenu["inspection.html"] = html.encode()
    contenu["licence-three.txt"] = (VENDOR / "LICENSE").read_bytes()
    contenu["lire-moi.txt"] = (
        (
            "Ouvrir inspection.html. Ressources graphiques intégrées ; aucune "
            "connexion ou fabrication nécessaire.\nMesures source et risques nu"
            "mériques, pas validation physique. Repères modèle/G-code non reli"
            "és sans preuve.\n"
        )
        + (manque or "Couches conservées dans couches.html ; provenance et limites visibles dans la page.")
        + "\n"
    ).encode()
    manifeste = {
        "schema": 1,
        "projet": nom,
        "fichiers": {n: hashlib.sha256(b).hexdigest() for n, b in contenu.items()},
        "limites": manque or "Correspondance modèle et préparation non attestée par cet export.",
    }
    contenu["manifeste.json"] = json.dumps(manifeste, ensure_ascii=False, indent=2).encode()
    out = io.BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as z:
        for n, b in contenu.items():
            z.writestr(n, b)
    return out.getvalue()
