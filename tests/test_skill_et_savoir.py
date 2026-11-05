"""Le point d’entrée et les connaissances doivent rester utilisables hors chantier."""
import json
import re
from pathlib import Path

from benchab.donnees.reference import _lignes
from benchab.qualite.edition import documentation

ROOT = Path(__file__).resolve().parents[1]
PAGES = (
    'reference/machine-kobra-s1.md', 'reference/slicer-anycubic.md',
    'reference/filaments.md', 'reference/profils-et-procedes.md',
    'explications/fiabilite-fdm.md', 'explications/surplombs-ponts-supports.md',
    'explications/coutures.md', 'explications/lithophanes.md',
    'explications/retours-d-experience.md', 'guides/cycle-projet.md',
    'guides/retour-impression.md',
)


def test_skill_racine_court_et_structure():
    texte = (ROOT / 'SKILL.md').read_text()
    assert texte.startswith('---\nname: benchab\ndescription: ')
    assert texte.split('---', 2)[1].count('description:') == 1
    assert 'AGENTS.md' in texte and 'benchab --help' in texte
    assert 'docs/guides/cycle-projet.md' in texte
    assert len(texte.splitlines()) < 60


def test_guides_techniques_et_regle_permanente():
    for nom in PAGES:
        texte = (ROOT / 'docs' / nom).read_text()
        assert len(texte.split()) > 250, nom
        assert '## Sources' in texte, nom
    agents = (ROOT / 'AGENTS.md').read_text()
    assert 'lot 8' not in agents and 'prive/chantier/' not in agents
    assert 'journal de séance' in agents and 'fin de séance' in agents
    assert documentation(ROOT, lecteur_url=lambda _: None) == []


def test_savoir_public_contextualise_et_sources_existantes():
    lignes = _lignes()
    assert len(lignes) > 2
    assert len({x['id'] for x in lignes}) == len(lignes)
    sources = (ROOT / 'benchab/reference/sources-savoir.jsonl').read_text().splitlines()
    catalogue = {x['id'] for x in map(json.loads, sources)}
    for ligne in lignes:
        assert ligne['public'] == 1 and ligne['texte'].strip()
        contexte = json.loads(ligne['donnees_json'])
        assert contexte['portee'] and contexte['source']
        assert set(contexte['conditions']) >= {'filament', 'reglages', 'date'}
        assert 'nombre_occurrences' in contexte
        assert contexte['statut'] in {'mesuré', 'observé une fois', 'hypothèse', 'validé'}
        assert all(s in catalogue for s in contexte.get('sources', []))
        assert not re.search(r'/Users/|192\.168\.|user/\d|[\w.+-]+@[\w.-]+\.[a-z]{2,}', ligne['texte'])
        assert not any(k in contexte for k in ('projet', 'projet_id', 'preparation_id'))


def test_index_de_provenance_invalide_sur_changement_de_contenu(monkeypatch):
    from benchab.donnees import reference

    original = _lignes()[0]
    texte = [json.dumps(original)]

    class Ressource:
        def joinpath(self, _chemin):
            return self

        def read_text(self, **_options):
            return texte[0]

    monkeypatch.setattr(reference, 'files', lambda _: Ressource())
    preuve = reference.source_livree(original)
    assert preuve is not None and reference.verifier_source(preuve)
    change = {**original, 'texte': original['texte'] + ' Correction.'}
    texte[0] = json.dumps(change)
    assert reference.source_livree(original) is None
    assert not reference.verifier_source(preuve)
    nouvelle = reference.source_livree(change)
    assert nouvelle is not None and nouvelle != preuve
