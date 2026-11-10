"""Parcours natif sans moteur en CI ; les réceptions réelles du slicer restent des critères distincts."""

import json
import os
import subprocess
import sys

from benchab.controles.envoi import controler
from benchab.donnees.base import Base
from benchab.projets.nouveau import creer
from benchab.qualite import coherence


def test_nouveau_projet_jusqu_au_controle_envoi_sans_fausse_validation(tmp_path):
    """Construit un modèle 12 × 8 × 2 mm ; sans tranchage, l'envoi reste bloqué et aucune impression n'est déduite."""
    prive = tmp_path / 'prive'
    base = Base(prive)
    try:
        projet = creer(prive, 'fixture-cycle', base=base)
        parametres = projet / 'src/parametres.py'
        parametres.write_text('''"""Entrées synthétiques en mm et °C ; aucune configuration personnelle."""
P={'titre':'Fixture','longueur':12,'largeur':8,'epaisseur':2}
FILAMENTS={'1':{'type':'PLA','color':'#ffffff','id':'fixture-matiere','settings_id':'fixture-profil',
               'temp':210,'temp_1re':210,'plateau_c':50,'debit_max':10}}
REGLAGES={'layer_height':'0.2','wall_loops':'3','sparse_infill_density':'20%',
          'sparse_infill_pattern':'gyroid','top_shell_layers':'5'}
PLAQUES=[{'name':'Plaque fixture','items':[{'piece':'Piece','copies':1,'extruder':1,
                                        'reglages':{'top_shell_layers':'8'}}]}]
VUE={}; REMPLISSAGE={}; LISSAGE={}; EFFORTS={}
''')
        env = {**os.environ, 'BENCHAB_PRIVE': str(prive), 'PYTHONDONTWRITEBYTECODE': '1', 'TMPDIR': '/private/tmp'}
        for commande in ('src.construire', 'src.preparer'):
            resultat = subprocess.run([sys.executable, '-B', '-m', commande], cwd=projet,
                                      env=env, capture_output=True, text=True, check=False)
            assert resultat.returncode == 0, resultat.stdout + resultat.stderr
        archive = projet / 'candidats/plaques.3mf'
        original = archive.read_bytes()
        rapport = controler(archive)
        assert rapport['statut'] in {'FAIL', 'NON VÉRIFIÉ'}
        assert rapport['code'] == 2 and rapport['validation_physique'] is False
        assert archive.read_bytes() == original
        assert not (projet / 'candidats/tranche.3mf').exists()
        donnees = json.loads(base.connexion.execute('SELECT donnees_json FROM projets').fetchone()[0])
        assert donnees['publication_autorisee'] is False
        assert donnees['suivi']['physique']['imprime'] is False
        assert coherence.verifier(prive) == []
    finally:
        base.fermer()
