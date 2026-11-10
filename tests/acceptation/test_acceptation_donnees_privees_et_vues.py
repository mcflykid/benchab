"""Données privées et export restaurable d'un atelier synthétique autonome."""

import json

from benchab.donnees.base import TABLES, Base
from benchab.donnees.reconstruction import reconstruire
from benchab.donnees.requetes import impressions
from benchab.donnees.vues import generer


def test_projets_observations_recherche_et_reconstruction_sans_publication(tmp_path):
    base, copie = Base(tmp_path / "atelier"), Base(tmp_path / "copie")
    try:
        with base.transaction() as c:
            for numero in range(3):
                ident = f"projets/exemple-{numero}"
                c.execute("INSERT INTO projets VALUES (?,?,?,?,?)", (
                    ident, f"Exemple {numero}", "fixture", "inconnu", "{}",
                ))
                c.execute("INSERT INTO preparations VALUES (?,?,?,?,?,?)", (
                    f"p{numero}", ident, "P01", None, str(numero) * 64, "{}",
                ))
            c.execute("INSERT INTO impressions VALUES (?,?,?,?,?)", (
                "observation", "p0", "2026-01-01", "reussie", json.dumps({
                    "filaments": [{"matiere": "PLA+"}],
                    "ponts": {"present": True, "source": "observation synthétique confirmée"},
                    "portee": "ENSEMBLE",
                }),
            ))
        assert len(impressions(base, resultat="reussie", matiere="PLA+", ponts=True)) == 1
        assert generer(base, racine_publique=tmp_path / "public")["projets_publics"] == 0
        assert "Exemple" not in (tmp_path / "public/README.md").read_text()
        assert reconstruire(copie, base.prive / "export")["lignes"]["projets"] == 3
        for table in TABLES:
            assert (copie.prive / f"export/{table}.jsonl").read_bytes() == (
                base.prive / f"export/{table}.jsonl"
            ).read_bytes()
    finally:
        base.fermer()
        copie.fermer()
