"""Prépare une vitrine HTML statique à partir du corpus public BENCHAB versionné."""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from html import escape
from pathlib import Path

from benchab.chemins import sans_lien
from benchab.donnees.reference import CHAMPS
from benchab.qualite.ecritures import PublicationNeuve, destination_neuve
from benchab.qualite.frontiere import verifier as verifier_frontiere

_CORPUS = Path("benchab/reference/connaissances.jsonl")
_MAX_CORPUS = 8 * 1024**2
_THEMES = {
    "matieres": ("Matières", r"\b(?:filaments?|bobines?|matières?|pla|petg|abs|asa|tpu)\b"),
    "reglages": ("Réglages", r"\b(?:profils?|réglages?|paramètres?|tranchage|consignes?)\b"),
    "defauts": ("Défauts", r"\b(?:défauts?|coutures?|marques?|surplombs?|décoll\w*)\b"),
    "entretien": ("Entretien", r"\b(?:entretien|nettoy\w*|lubrifi\w*|maintenance)\b"),
    "codes": ("Codes", r"\b(?:[gmt]\d{1,4}|codes? (?:d['’]erreur|machine|slicer))\b"),
    "autres": ("Autres repères", ""),
}
_PAGES = {
    "docs/reference/filaments.md": "matieres",
    "docs/reference/profils-et-procedes.md": "reglages",
    "docs/explications/retours-d-experience.md": "defauts",
    "docs/explications/surplombs-ponts-supports.md": "defauts",
    "docs/explications/coutures.md": "defauts",
}


def _lire_versionne(racine):
    """Compare les octets du seul corpus admis à son blob dans HEAD, sous une limite en octets."""
    source = sans_lien(racine / _CORPUS, racine=racine, strict=True)
    if not source.is_file() or source.stat().st_size > _MAX_CORPUS:
        raise ValueError("Le corpus doit être un fichier ordinaire de 8 Mio au plus.")
    objet = f"HEAD:{_CORPUS.as_posix()}"
    commande = ["git", "-C", str(racine), "cat-file"]
    try:
        taille = subprocess.run([*commande, "-s", objet], check=True, capture_output=True, timeout=15)
        if not 0 < int(taille.stdout) <= _MAX_CORPUS:
            raise ValueError("Corpus versionné vide ou supérieur à 8 Mio.")
        blob = subprocess.run([*commande, "blob", objet], check=True, capture_output=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError) as erreur:
        raise ValueError("Corpus BENCHAB versionné dans HEAD introuvable.") from erreur
    with source.open("rb") as fichier:
        lu = fichier.read(_MAX_CORPUS + 1)
    if lu != blob:
        raise ValueError("Le corpus local ne correspond pas au corpus versionné dans HEAD.")
    return source, blob


def _fiches(corpus):
    """Valide les champs publics et le niveau de preuve, sans rendre les données imbriquées."""
    fiches = {}
    for numero, brut in enumerate(corpus.splitlines(), 1):
        try:
            ligne = json.loads(brut)
            if not isinstance(ligne, dict) or set(ligne) != set(CHAMPS):
                raise ValueError("format de fiche inconnu")
            if type(ligne["public"]) is not int or ligne["public"] != 1:
                raise ValueError("fiche non publique")
            donnees = json.loads(ligne["donnees_json"])
            if not isinstance(donnees, dict) or {"projet", "projet_id", "preparation_id"} & donnees.keys():
                raise ValueError("données de projet ou format inconnu")
            champs = ("id", "texte", "source", "categorie")
            valeurs = {cle: ligne[cle] for cle in champs} | {
                cle: donnees.get(cle) for cle in ("statut", "portee")
            }
            if any(not isinstance(v, str) or not v.strip() for v in valeurs.values()):
                raise ValueError("texte, provenance ou portée manquants")
            identifiant = valeurs["id"]
            if identifiant in fiches:
                raise ValueError("identifiant dupliqué")
            date = ligne["date_source"]
            if date is not None and not isinstance(date, str):
                raise ValueError("date de source illisible")
            page = donnees.get("page")
            if page is not None and not isinstance(page, str):
                raise ValueError("référence documentaire illisible")
            fiches[identifiant] = {**valeurs, "date": date, "page": page}
        except (TypeError, ValueError) as erreur:
            raise ValueError(f"Corpus public invalide à la ligne {numero} : {erreur}.") from erreur
    return [fiches[identifiant] for identifiant in sorted(fiches)]


def _rendre(fiches, empreinte):
    """Produit des octets UTF-8 déterministes ; les données restent du texte HTML échappé."""
    index = {cle: [] for cle in _THEMES}
    articles = []
    for numero, fiche in enumerate(fiches, 1):
        ancre = f"fiche-{numero}"
        titre = escape(fiche["texte"][:96] + ("…" if len(fiche["texte"]) > 96 else ""))
        themes = {cle for cle, (_, motif) in _THEMES.items()
                  if motif and re.search(motif, fiche["texte"], re.IGNORECASE)}
        if fiche["page"] in _PAGES:
            themes.add(_PAGES[fiche["page"]])
        for cle in themes or {"autres"}:
            index[cle].append(f'<li><a href="#{ancre}">{titre}</a></li>')
        articles.append(
            f'<article id="{ancre}"><h3>{escape(fiche["id"])}</h3>'
            f'<p>{escape(fiche["texte"])}</p><dl><dt>Statut</dt><dd>{escape(fiche["statut"])}</dd>'
            f'<dt>Portée</dt><dd>{escape(fiche["portee"])}</dd>'
            f'<dt>Source</dt><dd>{escape(fiche["source"])}</dd>'
            f'<dt>Date de source</dt><dd>{escape(fiche["date"] or "Non renseignée")}</dd></dl>'
            '<p><a href="#sujets">Revenir aux sujets</a></p></article>'
        )
    nav = " · ".join(f'<a href="#{cle}">{libelle}</a>' for cle, (libelle, _) in _THEMES.items())
    sujets = "\n".join(
        f'<details><summary id="{cle}">{libelle} ({len(index[cle])})</summary>'
        f'<ul>{"".join(index[cle])}</ul></details>'
        for cle, (libelle, _) in _THEMES.items()
    )
    corps = "\n".join(articles)
    return (
        '<!doctype html>\n<html lang="fr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>Savoir public BENCHAB</title><style>'
        ':root{color-scheme:light dark}main{max-width:52rem;margin:2rem auto;padding:0 1rem;'
        'font:1rem/1.55 system-ui,sans-serif}summary{cursor:pointer;font-weight:600}'
        'details,article{margin:1rem 0;padding:.75rem;border:1px solid;border-radius:.4rem}'
        'dt{font-weight:600}dd{margin:0 0 .5rem}code{overflow-wrap:anywhere}'
        '</style></head><body><main><h1>Savoir public BENCHAB</h1>'
        '<p>Index par sujet : une fiche peut apparaître dans plusieurs rubriques. '
        'Lire son statut et sa portée avant de reprendre un réglage.</p>'
        f'<p>SHA-256 du corpus : <code>{empreinte}</code>.</p>'
        f'<nav id="sujets" aria-label="Sujets">{nav}</nav>{sujets}'
        f'<h2>Fiches et sources</h2>{corps}</main></body></html>\n'
    ).encode("utf-8")


def generer(racine, sortie, *, prive=None):
    """Écrit un HTML neuf et retourne ses SHA et le nombre de fiches, sans lire de contenu privé à publier."""
    racine = sans_lien(Path(racine), strict=True)
    sortie = destination_neuve(sortie, prive=prive)
    source, corpus = _lire_versionne(racine)
    fiches = _fiches(corpus)
    # La frontière existante vérifie aussi les motifs privés et les archives
    # publiques. La vitrine ne reçoit ni leurs octets ni leur détail.
    if verifier_frontiere(racine, prive=prive):
        raise ValueError("Frontière publique non validée ; aucun HTML généré.")
    empreinte = hashlib.sha256(corpus).hexdigest()
    html = _rendre(fiches, empreinte)
    with PublicationNeuve([source], [sortie]) as publication:
        publication.temporaire(sortie).write_bytes(html)
        publication.publier()
    return {"nombre_fiches": len(fiches), "sha256_corpus": empreinte, "sha256_html": hashlib.sha256(html).hexdigest()}


def main(argv=None):
    """Génère un aperçu hors ligne ; code 0 en succès ou 2 au refus, sans publication distante."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--racine", type=Path, default=Path.cwd(), help="Checkout du corpus public versionné")
    parser.add_argument("--prive", type=Path, help="Atelier de référence pour le contrôle de frontière")
    parser.add_argument("--sortie", type=Path, required=True, help="Fichier HTML neuf, privé ou temporaire")
    arguments = parser.parse_args(argv)
    try:
        resultat = generer(arguments.racine, arguments.sortie, prive=arguments.prive)
    except (OSError, ValueError) as erreur:
        print(f"BENCHAB : {erreur}", file=sys.stderr)
        return 2
    print(json.dumps(resultat, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
