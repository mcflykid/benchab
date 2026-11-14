"""Le point d'entrée CI réutilise le contrôle de frontière sans atelier configuré."""

from benchab.qualite.frontiere import main


def test_frontiere_passe_sans_atelier_prive(tmp_path, capsys):
    """Un arbre public propre passe avec uniquement le privé temporaire du scanner."""
    (tmp_path / "README.md").write_text("Documentation publique sans données privées.\n", encoding="utf-8")

    assert main(["--racine", str(tmp_path)]) == 0
    assert "PASS" in capsys.readouterr().out
    assert not (tmp_path / "prive").exists()


def test_jeton_synthetique_est_refuse_sans_etre_restitue(tmp_path, capsys):
    """Le motif synthétique est détecté, mais aucune valeur sensible ne sort du rapport."""
    jeton = "".join(("ghp_", "X" * 24))
    (tmp_path / "fixture.txt").write_text(jeton, encoding="utf-8")

    assert main(["--racine", str(tmp_path)]) == 2
    rapport = capsys.readouterr()
    assert "fixture.txt" in rapport.err
    assert "jeton" in rapport.err
    assert jeton not in rapport.err
    assert jeton not in rapport.out
