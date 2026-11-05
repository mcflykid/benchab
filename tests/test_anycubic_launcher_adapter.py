from benchab.fabrication import anycubic


def test_anycubic_trancher_delegates_to_unique_launcher(tmp_path, monkeypatch):
    source = tmp_path / "source.3mf"
    source.write_bytes("entrée 3MF simulée".encode())
    dossier = tmp_path / "sorties"
    sortie = dossier / "tranche.3mf"
    journal = dossier / "tranche.log"
    appels = []

    def faux_lanceur(entree, destination, nom, **options):
        appels.append((entree, destination, nom, options))
        return sortie, {"duree_s": 12.5}

    monkeypatch.setattr(anycubic, "trancher_fichier", faux_lanceur)
    resultat, duree = anycubic.trancher(
        source,
        dossier,
        "tranche.3mf",
        journal,
        timeout=90,
        usage="temoin",
        prive=tmp_path / "prive",
    )

    assert (resultat, duree) == (sortie, 12.5)
    assert appels == [
        (
            source,
            dossier,
            "tranche.3mf",
            {
                "usage": "temoin",
                "timeout_s": 90.0,
                "journal": journal,
                "prive": tmp_path / "prive",
                "attendu": None,
            },
        )
    ]


def test_anycubic_forwards_expected_settings_to_unique_launcher(tmp_path, monkeypatch):
    source = tmp_path / "source.3mf"
    source.write_bytes("entrée".encode())
    sortie = tmp_path / "resultat.3mf"
    appels = []

    def faux_lanceur(*args, **kwargs):
        appels.append((args, kwargs))
        return sortie, {"duree_s": 1.0}

    monkeypatch.setattr(
        anycubic,
        "trancher_fichier",
        faux_lanceur,
    )
    anycubic.trancher(source, tmp_path, "resultat.3mf", attendu={"layer_height": "0.2"})

    assert appels[0][1]["attendu"] == {"layer_height": "0.2"}
