"""Le cache suit le code empaqueté et la configuration machine réellement lue."""

from benchab.qualite import tout


def test_module_et_import_du_paquet_invalident_un_cache_explicite(tmp_path, monkeypatch):
    paquet = tmp_path / "code/benchab"
    (paquet / "qualite").mkdir(parents=True)
    module = paquet / "controle.py"
    module.write_text("from benchab import voisin\n")
    voisin = paquet / "voisin.py"
    voisin.write_text("VALEUR=1\n")
    monkeypatch.setattr(tout, "__file__", str(paquet / "qualite/tout.py"))
    projet = tmp_path / "projet"
    projet.mkdir()
    (projet / "input").write_text("fixture")
    stage = {
        "entrees": ["input"], "commande": ["{python}", "-m", "benchab.controle"],
        "cache": {"dependances": [], "paquets": [], "environnement": []},
    }
    a = tout.empreinte(projet, stage)
    voisin.write_text("VALEUR=2\n")
    b = tout.empreinte(projet, stage)
    assert a != b
    module.write_text("from benchab import voisin\nVALEUR=3\n")
    assert b != tout.empreinte(projet, stage)
    c = tout.empreinte(projet, stage)
    (paquet / "sans_rapport.py").write_text("VALEUR=4\n")
    assert c == tout.empreinte(projet, stage)


def test_machine_changee_au_meme_chemin_invalide_le_cache(tmp_path, monkeypatch):
    machine = tmp_path / "machine.toml"
    machine.write_text('[machine]\nnom="fixture A"\n')
    monkeypatch.setenv("BENCHAB_MACHINE", str(machine))
    stage = {
        "entrees": [], "commande": ["{python}", "-c", "pass"],
        "cache": {"dependances": [], "paquets": [], "environnement": ["BENCHAB_MACHINE"]},
    }
    a = tout.empreinte(tmp_path, stage)
    machine.write_text('[machine]\nnom="fixture B"\n')
    assert a != tout.empreinte(tmp_path, stage)
