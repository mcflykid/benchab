"""Sécurité du lanceur et normalisation des copies, sans lancer le slicer."""

import fcntl
import json
import shutil
import struct
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile, ZipInfo

import pytest

from benchab.fabrication import trancher


def test_appel_direct_verrouille_et_conserve_rapport_sans_lancer_slicer(tmp_path, monkeypatch):
    monkeypatch.setattr(trancher, "ECHANGE", tmp_path / "echange")
    monkeypatch.setenv("BENCHAB_PRIVE", str(tmp_path / "prive"))

    def executer_factice(demande):
        with (trancher.ECHANGE / "execution.lock").open("a") as verrou:
            with pytest.raises(BlockingIOError):
                fcntl.flock(verrou, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return {"code": 0, "modifications": [], "source_sha256": "fixture"}

    monkeypatch.setattr(trancher, "_executer", executer_factice)
    assert trancher.trancher({})["code"] == 0
    rapports = list((tmp_path / "prive/mesures/tranchages").glob("*.json"))
    assert len(rapports) == 1
    assert json.loads(rapports[0].read_text())["source_sha256"] == "fixture"


@pytest.mark.parametrize(
    "causes",
    [
        ["ConfigOptionVector"] * 3,
        ["expand_plate_extruders", "filaments_cnt", "expand_plate_extruders"],
    ],
)
def test_trois_plantages_meme_cause_interdisent_tout_nouvel_essai(tmp_path, monkeypatch, causes):
    monkeypatch.setattr(trancher, "ECHANGE", tmp_path / "echange")
    monkeypatch.setenv("BENCHAB_PRIVE", str(tmp_path / "prive"))
    rapports = tmp_path / "prive/mesures/tranchages"
    rapports.mkdir(parents=True)
    for index, cause in enumerate(causes):
        (rapports / f"{index}.json").write_text(json.dumps({"diagnostic": {"cause": cause}}))

    def lancement_interdit(demande):
        raise AssertionError("Ne doit jamais lancer un quatrième essai")

    monkeypatch.setattr(trancher, "_executer", lancement_interdit)
    with pytest.raises(ValueError, match="Trois essais"):
        trancher.trancher({})


@pytest.mark.parametrize("delai", [0, -1, 1801, float("nan"), float("inf"), True])
def test_delai_invalide_refuse_avant_processus(delai):
    with pytest.raises(ValueError, match="Délai"):
        trancher._executer({"timeout_s": delai})


def test_journal_survit_echec_et_delai_transmis(tmp_path, monkeypatch):
    monkeypatch.setattr(trancher, "ECHANGE", tmp_path / "echange")
    prive = tmp_path / "prive"
    entree = tmp_path / "source.3mf"
    entree.write_bytes(b"entree factice")
    journal = prive / "fabrication/journal.log"

    def executer_factice(demande):
        assert demande["timeout_s"] == 2.5
        natif = tmp_path / "journal-natif.log"
        natif.write_text("diagnostic conservé malgré échec")
        return {"code": 1, "modifications": [], "journal_natif": str(natif)}

    monkeypatch.setattr(trancher, "_executer", executer_factice)
    with pytest.raises(RuntimeError, match="Slicer en échec"):
        trancher.trancher_fichier(entree, prive / "fabrication", prive=prive, timeout_s=2.5, journal=journal)
    assert journal.read_text() == "diagnostic conservé malgré échec"
    assert entree.read_bytes() == b"entree factice"
    assert not (prive / "fabrication/source.gcode.3mf").exists()
    rapport = json.loads(next((prive / "mesures/tranchages").glob("*.json")).read_text())
    assert rapport["journal_conserve"] == str(journal)


def test_incident_bridge_archive_pile_et_ne_relance_pas(tmp_path, monkeypatch):
    monkeypatch.setattr(trancher, "ECHANGE", tmp_path / "echange")
    source = entree_3mf(tmp_path / "source.3mf", tops=("8",))
    appels = []

    def echec(demande):
        appels.append(demande)
        return {
            "code": -11,
            "modifications": [],
            "diagnostic": {
                "cause": "bridge_over_infill",
                "fonctions": ["bridge_over_infill", "appelant"],
            },
        }

    monkeypatch.setattr(trancher, "_executer", echec)
    resultat = trancher.trancher({"source": str(source)}, prive=tmp_path / "prive")
    assert len(appels) == 1
    dossier = Path(resultat["incident"]["dossier"])
    assert (dossier / "entree-originale.3mf").read_bytes() == source.read_bytes()
    assert (dossier / "pile.txt").read_text().splitlines() == ["bridge_over_infill", "appelant"]
    assert resultat["incident"]["cause_etablie"] is False
    assert resultat["incident"]["relance_automatique"] is False


def test_ids_existants_explicites_ne_creent_pas_de_valeur_temporaire(tmp_path):
    source = entree_3mf(
        tmp_path / "source.3mf",
        reglages={
            "filament_type": ["PLA"],
            "filament_colour": ["#FFFFFF"],
            "filament_settings_id": ["Profil déclaré"],
            "filament_ids": [""],
            "top_shell_layers": "5",
        },
        tops=("8",),
    )
    sortie = tmp_path / "copie.3mf"
    rapport = trancher.normaliser(
        source,
        sortie,
        identifiants={
            "Profil déclaré": {"id": "identifiant-existant", "sources": [{"chemin": "profil-fixe.json"}]},
        },
    )
    with ZipFile(sortie) as archive:
        reglages = json.loads(archive.read("Metadata/project_settings.config"))
        assert reglages["filament_ids"] == ["identifiant-existant"]
        assert reglages["top_shell_layers"] == "5"
    assert any(m.get("sources_identifiants") for m in rapport["modifications"])


def entree_3mf(chemin, plaques=1, reglages=None, tops=("5",), modele="Anycubic Kobra S1", buse=("0.4",)):
    configuration = ET.Element("config")
    for i in range(plaques):
        plaque = ET.SubElement(configuration, "plate")
        ET.SubElement(plaque, "metadata", key="plater_name", value=f"Plaque {i + 1}")
    for i, top in enumerate(tops):
        objet = ET.SubElement(configuration, "object", id=str(i + 1))
        ET.SubElement(objet, "metadata", key="top_shell_layers", value=top)
    if reglages is None:
        reglages = {
            "filament_type": ["PLA", "PLA"],
            "filament_colour": ["#FFFFFF", "#000000"],
            "filament_settings_id": ["Profil temporaire 1", "Profil temporaire 2"],
            "filament_ids": ["profil-1", "profil-2"],
            "filament_id": "profil-1",
            "top_shell_layers": "5",
            "inherits_group": ["", "", ""],
            "printer_model": modele,
            "nozzle_diameter": list(buse),
        }
    with ZipFile(chemin, "w") as archive:
        archive.writestr("Metadata/model_settings.config", ET.tostring(configuration))
        archive.writestr("Metadata/project_settings.config", json.dumps(reglages))
    return chemin


def test_normalisation_preserve_source_et_corrige_causes_connues(tmp_path):
    source = entree_3mf(tmp_path / "source.3mf")
    empreinte = trancher.sha256(source)
    destination = tmp_path / "nouveau.3mf"
    rapport = trancher.normaliser(source, destination)
    assert trancher.sha256(source) == empreinte
    assert rapport["plaques"] == 1
    assert rapport["filaments"] == 2
    with ZipFile(destination) as archive:
        modele = ET.fromstring(archive.read("Metadata/model_settings.config"))
        reglages = json.loads(archive.read("Metadata/project_settings.config"))
    assert modele.find("plate/metadata").get("value") == ""
    assert modele.find("object/metadata").get("value") == "5"
    assert reglages["top_shell_layers"] == "5"
    for cle in ("filament_type", "filament_colour", "filament_ids", "filament_settings_id"):
        assert len(reglages[cle]) == 2
        assert all(reglages[cle])
    assert isinstance(reglages["filament_id"], str)
    assert reglages["filament_id"] == reglages["filament_ids"][0]
    assert len(reglages["inherits_group"]) == 4


def test_identifiant_scalaire_optionnel_absent_reste_absent(tmp_path):
    """Conserve l'absence du scalaire optionnel quand la liste explicite suffit ; aucune métadonnée inventée."""
    reglages = {
        "filament_type": ["PLA"],
        "filament_colour": ["#FFFFFF"],
        "filament_settings_id": ["Profil"],
        "filament_ids": ["stable"],
        "top_shell_layers": "5",
    }
    source = entree_3mf(tmp_path / "source.3mf", reglages=reglages)
    destination = tmp_path / "copie.3mf"
    rapport = trancher.normaliser(source, destination)
    with ZipFile(destination) as z:
        sortie = json.loads(z.read("Metadata/project_settings.config"))
    assert "filament_id" not in sortie
    assert sortie["filament_ids"] == ["stable"]
    assert not any(m["champ"] == "filament_id" for m in rapport["modifications"])


def test_plus_de_30_plaques_refuse_avant_tout_lancement(tmp_path):
    source = entree_3mf(tmp_path / "source.3mf", plaques=31)
    with pytest.raises(ValueError, match="30 plaques"):
        trancher.normaliser(source, tmp_path / "nouveau.3mf")
    assert not (tmp_path / "nouveau.3mf").exists()


def test_top_divergents_conserves_et_matiere_inconnue_refusee(tmp_path):
    source = entree_3mf(tmp_path / "source.3mf", tops=("3", "5"))
    trancher.normaliser(source, tmp_path / "nouveau.3mf")
    with ZipFile(tmp_path / "nouveau.3mf") as archive:
        modele = ET.fromstring(archive.read("Metadata/model_settings.config"))
        assert [m.get("value") for m in modele.findall("object/metadata")] == ["3", "5"]
    source = entree_3mf(tmp_path / "sans-matiere.3mf", reglages={})
    with pytest.raises(ValueError, match="Type de filament absent"):
        trancher.normaliser(source, tmp_path / "nouveau.3mf")


def test_diagnostic_lit_uniquement_la_pile_du_fil_en_faute(tmp_path, monkeypatch):
    dossier = tmp_path / "Library/Logs/DiagnosticReports"
    dossier.mkdir(parents=True)
    contenu = {
        "faultingThread": 1,
        "threads": [
            {"frames": [{"symbol": "generate_plate_name_texture"}]},
            {"triggered": True, "frames": [{"symbol": "ConfigOptionVector::get_at"}]},
        ],
    }
    (dossier / "AnycubicSlicerNext-test.ips").write_text("{}\n" + json.dumps(contenu))
    monkeypatch.setattr(trancher.Path, "home", lambda: tmp_path)
    rapport = trancher.diagnostic(time.time())
    assert rapport["cause"] == "ConfigOptionVector"
    assert rapport["fonctions"] == ["ConfigOptionVector::get_at"]


def test_entree_hors_tmp_refusee():
    with pytest.raises(ValueError, match="copie sous"):
        trancher.chemin_temporaire("/Applications/AnycubicSlicerNext.app")


def test_couleurs_residuelles_ne_creent_pas_de_filaments(tmp_path):
    reglages = {
        "filament_type": ["PLA", "PLA", "PLA"],
        "filament_colour": ["#FFFFFF", "#000000", "#0000FF"],
        "filament_settings_id": ["Profil actif unique"],
        "inherits_group": ["", "", "", "", ""],
        "top_shell_layers": "5",
        "filament_ids": ["profil-1"],
        "filament_id": "profil-1",
    }
    source = entree_3mf(tmp_path / "source.3mf", reglages=reglages)
    destination = tmp_path / "nouveau.3mf"
    rapport = trancher.normaliser(source, destination)
    assert rapport["filaments"] == 1
    with ZipFile(destination) as archive:
        normalise = json.loads(archive.read("Metadata/project_settings.config"))
    assert normalise["filament_type"] == reglages["filament_type"]
    assert normalise["filament_colour"] == reglages["filament_colour"]
    assert len(normalise["inherits_group"]) == 3
    assert len(normalise["filament_ids"]) == 1
    assert isinstance(normalise["filament_id"], str)


def test_post_traitement_externe_refuse_avant_tout_lancement(tmp_path):
    source = entree_3mf(tmp_path / "source.3mf", reglages={"post_process": ["programme-externe-interdit"]})
    with pytest.raises(ValueError, match="post_process externe interdit"):
        trancher.normaliser(source, tmp_path / "nouveau.3mf")
    assert not (tmp_path / "nouveau.3mf").exists()


@pytest.mark.parametrize(
    ("modele", "buse"),
    [("Anycubic Kobra S1", ("0.4",)), ("Anycubic Kobra S1 Combo", (0.4,))],
)
def test_top_objet_8_et_projet_5_conserves_dans_sortie_tranchee(tmp_path, monkeypatch, modele, buse):
    source = entree_3mf(tmp_path / "source.3mf", tops=("8",), modele=modele, buse=buse)
    original = source.read_bytes()
    monkeypatch.setattr(trancher, "ECHANGE", tmp_path / "echange")
    natifs = {}

    class SlicerSimule:
        def __init__(self, commande, **options):
            assert options["start_new_session"] is True
            entree = Path(commande[-1])
            sortie = Path(commande[commande.index("--outputdir") + 1]) / commande[commande.index("--export-3mf") + 1]
            shutil.copyfile(entree, sortie)
            with ZipFile(sortie, "a") as archive:
                archive.writestr("Metadata/plate_1.gcode", "M83\nG1 X10 Y10 E1\n")
                archive.writestr(
                    "Metadata/slice_info.config",
                    (
                        '<config><plate><metadata key="index" value="1"/>'
                        '<metadata key="prediction" value="1"/><metadata key="weight" value="1"/></plate></config>'
                    ),
                )
            natifs[sortie] = sortie.read_bytes()
            self.pid = 12345
            self.returncode = None

    publier = trancher._publier_sortie

    def publier_copie(fichier, destination):
        natif, = natifs
        assert fichier != natif
        assert natif.read_bytes() == natifs[natif]
        return publier(fichier, destination)

    monkeypatch.setattr(trancher, "_publier_sortie", publier_copie)
    monkeypatch.setattr(trancher.subprocess, "Popen", SlicerSimule)
    ressources = SimpleNamespace(ru_utime=0, ru_stime=0, ru_maxrss=1)
    monkeypatch.setattr(trancher.os, "wait4", lambda pid, options: (pid, 0, ressources))
    prive = tmp_path / "prive"
    dossier_sortie = prive / "sorties"
    sortie, rapport = trancher.trancher_fichier(source, dossier_sortie, "sortie.3mf", prive=prive)
    assert source.read_bytes() == original
    assert rapport["code"] == 0
    with ZipFile(sortie) as archive:
        modele = ET.fromstring(archive.read("Metadata/model_settings.config"))
        projet = json.loads(archive.read("Metadata/project_settings.config"))
        assert modele.find("object/metadata[@key='top_shell_layers']").get("value") == "8"
        assert projet["top_shell_layers"] == "5"
    assert not any("top_shell_layers" in m["champ"] for m in rapport["modifications"])


@pytest.mark.parametrize(
    ("modele", "buse"),
    [
        ("Anycubic Kobra S1 Max", ["0.4"]),
        (["Anycubic Kobra S1"], ["0.4"]),
        ("Anycubic Kobra S1", ["0.6"]),
        ("Anycubic Kobra S1", None),
        (None, ["0.4"]),
    ],
)
def test_executer_refuse_modele_ou_buse_non_conformes_avant_popen(tmp_path, monkeypatch, modele, buse):
    """Le vrai parcours _executer refuse machine absente/différente avant tout Popen natif."""
    with tempfile.TemporaryDirectory(prefix="benchab-garde-machine-", dir="/private/tmp") as racine:
        dossier = Path(racine)
        reglages = {
            "printer_model": modele,
            "filament_type": ["PLA", "PLA"],
            "filament_colour": ["#FFFFFF", "#000000"],
            "filament_settings_id": ["Profil 1", "Profil 2"],
            "filament_ids": ["profil-1", "profil-2"],
            "filament_id": "profil-1",
            "top_shell_layers": "5",
            "inherits_group": ["", "", ""],
        }
        if buse is not None:
            reglages["nozzle_diameter"] = buse
        source = entree_3mf(dossier / "source.3mf", reglages=reglages)
        destination = dossier / "sortie.3mf"

        def lancement_interdit(*args, **kwargs):
            raise AssertionError("Le précontrôle doit refuser avant Popen")

        monkeypatch.setattr(trancher.subprocess, "Popen", lancement_interdit)
        with pytest.raises(ValueError, match="Modèle embarqué|Buse embarquée"):
            trancher._executer({"source": str(source), "destination": str(destination)})
        assert not destination.exists()


@pytest.mark.parametrize("defaut", ["doublon", "trop_grand"])
def test_validation_machine_refuse_membre_projet_ambigu_ou_trop_grand(tmp_path, defaut):
    """Le précontrôle partagé refuse l'index ou le budget avant lecture des réglages."""
    source = tmp_path / "source.3mf"
    membre = "Metadata/project_settings.config"
    reglages = json.dumps({"printer_model": "Anycubic Kobra S1", "nozzle_diameter": ["0.4"]})
    with ZipFile(source, "w", compression=ZIP_DEFLATED) as archive:
        if defaut == "doublon":
            archive.writestr(membre, reglages)
            with pytest.warns(UserWarning, match="Duplicate name"):
                archive.writestr(membre, reglages)
        else:
            archive.writestr(membre, reglages + " " * (8 * 1024 * 1024))
    avant = source.read_bytes()
    with pytest.raises(ValueError, match="Machine embarquée absente ou illisible"):
        trancher._verifier_machine_embarquee(source)
    assert source.read_bytes() == avant


def test_delai_tue_le_groupe_cree_sans_toucher_parent(tmp_path, monkeypatch):
    """Le timeout signale le seul PGID créé par le slicer et conserve wait4/CPU."""
    with tempfile.TemporaryDirectory(prefix="benchab-timeout-groupe-", dir="/private/tmp") as racine:
        dossier = Path(racine)
        source = entree_3mf(dossier / "source.3mf")
        destination = dossier / "sortie.3mf"
        signaux = []
        tue = False
        groupe_actif = True

        class SlicerSimule:
            def __init__(self, commande, **options):
                assert options["start_new_session"] is True
                self.pid = 424242
                self.returncode = None

        def killpg(groupe, signal_):
            nonlocal tue, groupe_actif
            signaux.append((groupe, signal_))
            if signal_ == trancher.signal.SIGKILL:
                tue = True
                groupe_actif = False

        def actif(groupe):
            assert groupe == 424242
            return groupe_actif

        def wait4(pid, options):
            ressources = SimpleNamespace(ru_utime=0.1, ru_stime=0.2, ru_maxrss=3)
            return (pid, 9, ressources) if tue else (0, 0, ressources)

        monkeypatch.setattr(trancher.subprocess, "Popen", SlicerSimule)
        monkeypatch.setattr(trancher.os, "killpg", killpg)
        monkeypatch.setattr(trancher.os, "wait4", wait4)
        monkeypatch.setattr("benchab.processus._actif", actif)
        resultat = trancher._executer(
            {"source": str(source), "destination": str(destination), "timeout_s": 0.01}
        )
        assert resultat["delai_depasse"] is True
        assert resultat["code"] == 124
        assert signaux == [
            (424242, trancher.signal.SIGTERM),
            (424242, trancher.signal.SIGKILL),
        ]
        assert resultat["cpu_utilisateur_s"] == 0.1


def test_delai_arrete_un_descendant_simule_du_moteur(tmp_path, monkeypatch):
    """Le vrai wait4 du lanceur expire et arrête un enfant Python, sans slicer ni réseau."""
    with tempfile.TemporaryDirectory(prefix="benchab-descendant-", dir="/private/tmp") as racine:
        dossier = Path(racine)
        source = entree_3mf(dossier / "source.3mf")
        destination = dossier / "sortie.3mf"
        pid_enfant = dossier / "enfant.pid"
        executable = dossier / "moteur-simule"
        executable.write_text(
            f"#!{sys.executable}\n"
            "import os, subprocess, time\n"
            f"enfant = subprocess.Popen([{sys.executable!r}, '-c', 'import time; time.sleep(60)'])\n"
            f"open({str(pid_enfant)!r}, 'w').write(f'{{os.getpid()}} {{enfant.pid}}')\n"
            "time.sleep(60)\n"
        )
        executable.chmod(0o700)
        configuration = trancher.charger()
        configuration["slicer"]["executable"] = str(executable)
        monkeypatch.setattr(trancher, "charger", lambda: configuration)

        resultat = trancher._executer(
            {"source": str(source), "destination": str(destination), "timeout_s": 1.5}
        )

        assert resultat["delai_depasse"] is True
        assert resultat["cpu_utilisateur_s"] >= 0
        assert pid_enfant.is_file()
        parent, enfant = map(int, pid_enfant.read_text().split())
        from benchab.processus import _actif

        assert enfant > 0
        assert _actif(parent) is False


def test_moteur_change_reglage_non_declare_sortie_non_livree(tmp_path, monkeypatch):
    """Simule un moteur modifiant cinq couches en six ; FAIL archivé, source intacte et aucune sortie livrée."""
    source = entree_3mf(tmp_path / "source.3mf", tops=("8",))
    original = source.read_bytes()
    monkeypatch.setattr(trancher, "ECHANGE", tmp_path / "echange")

    class MoteurMutant:
        """Double sans processus réel ; change volontairement un réglage pour vérifier la barrière de livraison."""

        def __init__(self, commande, **options):
            entree = Path(commande[-1])
            sortie = Path(commande[commande.index("--outputdir") + 1]) / commande[commande.index("--export-3mf") + 1]
            with ZipFile(entree) as avant, ZipFile(sortie, "w") as apres:
                for info in avant.infolist():
                    contenu = avant.read(info)
                    if info.filename == "Metadata/project_settings.config":
                        reglages = json.loads(contenu)
                        reglages["top_shell_layers"] = "6"
                        contenu = json.dumps(reglages).encode()
                    apres.writestr(info, contenu)
            self.pid = 12345
            self.returncode = None

    monkeypatch.setattr(trancher.subprocess, "Popen", MoteurMutant)
    ressources = SimpleNamespace(ru_utime=0, ru_stime=0, ru_maxrss=1)
    monkeypatch.setattr(trancher.os, "wait4", lambda pid, options: (pid, 0, ressources))
    prive = tmp_path / "prive"
    destination = prive / "sorties/refuse.3mf"
    with pytest.raises(RuntimeError, match="échec"):
        trancher.trancher_fichier(source, destination.parent, destination.name, prive=prive)
    assert not destination.exists()
    assert source.read_bytes() == original
    rapports = list((prive / "mesures/tranchages").glob("*.json"))
    assert len(rapports) == 1
    rapport = json.loads(rapports[0].read_text())
    assert rapport["validation_sortie"]["statut"] == "FAIL"
    assert "Intégrité" in rapport["validation_sortie"]["detail"]
    assert (Path(rapport["incident"]["dossier"]) / "sortie-refusee.3mf").is_file()


def test_noms_identiques_avant_apres_et_gcode_inchange(tmp_path):
    source = entree_3mf(tmp_path / "source.3mf", plaques=2)
    sortie = tmp_path / "sortie.3mf"
    rapport = trancher.normaliser(source, sortie)
    gcode = b"; couche temoin\nG1 X10 Y12 E1\n"
    with ZipFile(sortie, "a") as archive:
        archive.writestr("Metadata/plate_1.gcode", gcode)
    restauration = trancher.restaurer_noms(sortie, rapport["noms_origine"])
    assert restauration["reouverture"] == "PASS"
    with ZipFile(source) as avant, ZipFile(sortie) as apres:
        noms_avant = ET.fromstring(avant.read("Metadata/model_settings.config"))
        noms_apres = ET.fromstring(apres.read("Metadata/model_settings.config"))

        def lire(modele):
            return [p.find("metadata[@key='plater_name']").get("value") for p in modele.findall("plate")]

        assert lire(noms_avant) == lire(noms_apres) == ["Plaque 1", "Plaque 2"]
        assert apres.read("Metadata/plate_1.gcode") == gcode


def test_identifiants_temporaires_uniquement_temoin_et_fail_envoi(tmp_path):
    from benchab.controles.envoi import controler

    reglages = {
        "filament_type": ["PLA"],
        "filament_colour": ["#FFFFFF"],
        "filament_settings_id": ["Profil réel"],
        "top_shell_layers": "5",
    }
    source = entree_3mf(tmp_path / "source.3mf", reglages=reglages)
    with pytest.raises(ValueError, match="temporaires interdits"):
        trancher.normaliser(source, tmp_path / "impression.3mf")
    temoin = tmp_path / "temoin.3mf"
    rapport = trancher.normaliser(source, temoin, usage="temoin")
    assert rapport["usage"] == "temoin"
    assert controler(temoin)["statut"] == "FAIL"
    with pytest.raises(ValueError, match="BENCHAB-temporaire interdit"):
        trancher.normaliser(temoin, tmp_path / "impression.3mf")


def test_inspection_options_utilise_le_lanceur_isole(tmp_path, monkeypatch):
    """L'aide emploie un datadir neuf et HOME/cache sous /tmp ; aucun argument de tranchage ni profil modifié."""
    from types import SimpleNamespace

    from benchab import processus
    from benchab.fabrication import trancher as lanceur

    appels = []

    def faux(commande, **options):
        appels.append((commande, options))
        return SimpleNamespace(code_controle=0, stdout="options FFF fixture\n", diagnostic=None)

    monkeypatch.setattr(processus, "executer", faux)
    rapport = lanceur.inspecter_options(prive=tmp_path)
    assert rapport["statut"] == "PASS"
    assert rapport["mode"] == "aide"
    cmd, options = appels[0]
    assert cmd[-1] == "--help" and "--slice" not in cmd
    assert options["delai"] == 30 and options["conserver_capture"]
    assert Path(rapport["journal"]).read_text() == "options FFF fixture\n"
    assert Path(cmd[cmd.index("--datadir") + 1]).is_dir()
    assert all(Path(options["env"][cle]).is_relative_to("/private/tmp") for cle in ("HOME", "TMPDIR", "XDG_CACHE_HOME"))


def test_plafond_inconnus_separe_sha_source_et_mode_inspection(tmp_path):
    """Les inconnues ne se regroupent qu’avec une même opération et un même contexte explicite."""
    from benchab.fabrication.trancher import _verifier_limite_causes

    prive = tmp_path / "prive"
    dossiers = [prive / "mesures/tranchages", prive / "mesures/options-slicer"]
    for dossier in dossiers:
        dossier.mkdir(parents=True)

    def incident(dossier, nom, *, code, source=None, mode=None):
        rapport = {"code": code, "diagnostic": {"cause": "inconnue"}}
        if source:
            rapport["source_sha256"] = source
        if mode:
            rapport["mode"] = mode
        (dossier / nom).write_text(json.dumps(rapport), encoding="utf-8")

    source_a, source_b = "a" * 64, "b" * 64
    for indice, code in enumerate((250, -11)):
        incident(dossiers[0], f"a-{indice}.json", code=code, source=source_a)
    incident(dossiers[1], "aide-0.json", code=1, mode="aide")
    _verifier_limite_causes(prive, operation="tranchage", source_sha256=source_a)
    _verifier_limite_causes(prive, operation="tranchage", source_sha256=source_b)
    _verifier_limite_causes(prive, operation="options-slicer", mode="aide")

    incident(dossiers[0], "a-2.json", code=250, source=source_a)
    with pytest.raises(ValueError, match="Trois essais"):
        _verifier_limite_causes(prive, operation="tranchage", source_sha256=source_a)
    for indice in range(1, 3):
        incident(dossiers[1], f"aide-{indice}.json", code=1, mode="aide")
    historiques = {chemin: chemin.read_bytes() for dossier in dossiers for chemin in dossier.glob("*.json")}
    with pytest.raises(ValueError, match="Trois essais"):
        _verifier_limite_causes(prive, operation="options-slicer", mode="aide")
    _verifier_limite_causes(prive, operation="options-slicer", mode="defauts")
    assert all(chemin.read_bytes() == octets for chemin, octets in historiques.items())


def test_plafond_conserve_groupes_connus_et_ignore_inconnues_sans_contexte(tmp_path):
    """Les groupes de causes établies restent communs ; une vieille inconnue sans contexte n’est pas agrégée."""
    from benchab.fabrication.trancher import _verifier_limite_causes

    prive = tmp_path / "prive"
    dossier = prive / "mesures/tranchages"
    dossier.mkdir(parents=True)
    inconnue = {"code": 250, "diagnostic": {"cause": "inconnue"}}
    for indice in range(3):
        (dossier / f"ancien-{indice}.json").write_text(json.dumps(inconnue), encoding="utf-8")
    _verifier_limite_causes(prive, operation="tranchage", source_sha256="c" * 64)

    for indice, cause in enumerate(("filaments_cnt", "expand_plate_extruders", "filaments_cnt")):
        (dossier / f"cause-{indice}.json").write_text(
            json.dumps({"diagnostic": {"cause": cause}}), encoding="utf-8"
        )
    historiques = {chemin: chemin.read_bytes() for chemin in dossier.glob("*.json")}
    with pytest.raises(ValueError, match="Trois essais"):
        _verifier_limite_causes(prive, operation="tranchage", source_sha256="c" * 64)
    assert all(chemin.read_bytes() == octets for chemin, octets in historiques.items())


@pytest.mark.parametrize("forme", ["lien", "volumineux"])
def test_garde_refuse_historique_non_ordinaire_ou_hors_borne(tmp_path, forme):
    """La garde refuse un lien ou un rapport supérieur à 8 Mio avant de l’analyser."""
    from benchab.fabrication.trancher import _verifier_limite_causes

    dossier = tmp_path / "prive/mesures/tranchages"
    dossier.mkdir(parents=True)
    rapport = dossier / "incident.json"
    if forme == "lien":
        cible = tmp_path / "cible.json"
        cible.write_text('{"diagnostic":{"cause":"autre"}}', encoding="utf-8")
        rapport.symlink_to(cible)
    else:
        rapport.touch()
        with rapport.open("r+b") as fichier:
            fichier.truncate(8 * 1024**2 + 1)

    with pytest.raises(ValueError, match="Rapport d'incident invalide"):
        _verifier_limite_causes(tmp_path / "prive", operation="tranchage", source_sha256="c" * 64)


@pytest.mark.parametrize("destination_existante", [False, True])
def test_zip_partiel_ne_remplace_pas_la_sortie(tmp_path, monkeypatch, destination_existante):
    """Une panne d'écriture conserve l'original et la sortie précédente, sans temporaire résiduel."""
    source = entree_3mf(tmp_path / "source.3mf")
    entree = source.read_bytes()
    sortie = tmp_path / "sortie.3mf"
    if destination_existante:
        sortie.write_bytes(b"version precedente")

    def echec(*_args, **_options):
        raise OSError("écriture ZIP injectée")

    monkeypatch.setattr(ZipFile, "writestr", echec)
    with pytest.raises(OSError, match="écriture ZIP injectée"):
        trancher.normaliser(source, sortie)
    assert source.read_bytes() == entree
    if destination_existante:
        assert sortie.read_bytes() == b"version precedente"
    else:
        assert not sortie.exists()
    assert not list(tmp_path.glob(".normalisation-*"))


def test_restauration_nom_absent_ne_laisse_aucun_zip(tmp_path):
    """Une plaque sans nom empêche la restauration et laisse seulement le 3MF d'origine."""
    source = tmp_path / "source.3mf"
    with ZipFile(source, "w") as archive:
        archive.writestr("Metadata/model_settings.config", "<config><plate/></config>")
    avant = source.read_bytes()
    with pytest.raises(ValueError, match="Nom de plaque absent"):
        trancher.restaurer_noms(source, ["Fixture"])
    assert source.read_bytes() == avant
    assert list(tmp_path.iterdir()) == [source]


def test_trancher_fichier_transmet_le_catalogue_sans_slicer(tmp_path, monkeypatch):
    """La demande publique transporte le catalogue vers le moteur sans lancer le slicer."""
    source = tmp_path / "source.3mf"
    source.write_bytes("source synthétique".encode())
    prive = tmp_path / "prive"
    catalogue = {"Profil PLA": {"id": "profil-permanent", "sources": [{"chemin": "catalogue.json"}]}}
    captures = []

    def faux_trancher(demande, *, prive=None, destination_finale=None):
        captures.append(demande)
        Path(demande["destination"]).write_bytes("sortie synthétique".encode())
        destination_finale.parent.mkdir(parents=True, exist_ok=True)
        destination_finale.write_bytes("sortie publiée".encode())
        rapport = prive / "rapport.json"
        rapport.parent.mkdir(parents=True, exist_ok=True)
        rapport.write_text("{}", encoding="utf-8")
        return {"code": 0, "rapport": str(rapport), "journal_natif": str(rapport)}

    monkeypatch.setattr(trancher, "trancher", faux_trancher)
    trancher.trancher_fichier(source, prive / "fabrication", prive=prive, identifiants=catalogue)
    assert captures[0]["identifiants"] == catalogue


def test_normaliser_refuse_catalogue_ambigue_ou_absent(tmp_path):
    """Une correspondance vide ne peut laisser un identifiant de production incomplet."""
    source = entree_3mf(
        tmp_path / "source.3mf",
        reglages={
            "filament_type": ["PLA"], "filament_colour": ["#FFFFFF"],
            "filament_settings_id": ["Profil déclaré"], "filament_ids": [""],
            "top_shell_layers": "5",
        },
        tops=("8",),
    )
    with pytest.raises(ValueError, match="Correspondance"):
        trancher.normaliser(source, tmp_path / "sortie.3mf", identifiants={"Profil déclaré": []})


def _corrompre_crc_archive(chemin, cible):
    """Altère le CRC central d’un membre sans modifier ses octets compressés."""
    contenu = bytearray(chemin.read_bytes())
    position = 0
    while (position := contenu.find(b"PK\x01\x02", position)) >= 0:
        longueur_nom, longueur_extra, longueur_commentaire = struct.unpack_from("<HHH", contenu, position + 28)
        debut_nom = position + 46
        nom = bytes(contenu[debut_nom:debut_nom + longueur_nom]).decode("utf-8")
        if nom == cible:
            crc = struct.unpack_from("<I", contenu, position + 16)[0]
            struct.pack_into("<I", contenu, position + 16, crc ^ 1)
            chemin.write_bytes(contenu)
            return
        position = debut_nom + longueur_nom + longueur_extra + longueur_commentaire
    raise AssertionError(f"Membre ZIP absent : {cible}")


def test_normaliser_refuse_un_chemin_zip_non_confine(tmp_path):
    """Le contrôle du lecteur commun refuse un membre de traversée avant copie."""
    source = entree_3mf(tmp_path / "source.3mf")
    with ZipFile(source, "a") as archive:
        archive.writestr("../hors-archive.bin", b"interdit")
    destination = tmp_path / "copie.3mf"

    with pytest.raises(ValueError, match="non sûr|traversant"):
        trancher.normaliser(source, destination)

    assert not destination.exists()


def test_normaliser_borne_xml_avant_parse_et_publication(tmp_path, monkeypatch):
    """Le budget XML de normalisation s’applique au membre avant sa construction ET."""
    source = entree_3mf(tmp_path / "source.3mf")
    with ZipFile(source) as archive:
        membres = [(nom, archive.read(nom)) for nom in archive.namelist()]
    modele = b'<config><!--' + b'x' * (8 * 1024 * 1024 + 1) + b'-->' \
             b'<plate><metadata key="plater_name" value="Plaque 1"/></plate></config>'
    with ZipFile(source, "w") as archive:
        for nom, contenu in membres:
            archive.writestr(nom, modele if nom == "Metadata/model_settings.config" else contenu)
    destination = tmp_path / "copie.3mf"
    from benchab.fabrication import tranchage_copies

    monkeypatch.setattr(
        tranchage_copies.ET,
        "fromstring",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("XML analysé avant vérification du budget")),
    )

    with pytest.raises(ValueError, match="budget XML"):
        trancher.normaliser(source, destination)

    assert not destination.exists()


def test_normaliser_borne_reglages_avant_decodage(tmp_path, monkeypatch):
    """Le budget JSON de réglages s’applique avant le décodeur typé."""
    source = entree_3mf(tmp_path / "source.3mf")
    with ZipFile(source) as archive:
        membres = [(nom, archive.read(nom)) for nom in archive.namelist()]
    reglages = b'{"filament_type":["PLA"],"note":"' + b'x' * (8 * 1024 * 1024 + 1) + b'"}'
    with ZipFile(source, "w") as archive:
        for nom, contenu in membres:
            archive.writestr(nom, reglages if nom == "Metadata/project_settings.config" else contenu)
    destination = tmp_path / "copie.3mf"
    monkeypatch.setattr(
        trancher.schema_reglages,
        "decoder",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("JSON décodé avant le budget")),
    )

    with pytest.raises(ValueError, match="budget réglages"):
        trancher.normaliser(source, destination)

    assert not destination.exists()


@pytest.mark.parametrize(
    "membre",
    ["Metadata/model_settings.config", "Metadata/project_settings.config", "Metadata/extra.bin"],
)
def test_normaliser_refuse_crc_invalide_sur_remplacement_et_copie(tmp_path, membre):
    """Le CRC source est vérifié même pour les deux membres qui seront remplacés."""
    source = entree_3mf(tmp_path / "source.3mf")
    if membre.endswith("extra.bin"):
        with ZipFile(source, "a") as archive:
            archive.writestr(membre, b"copie protegee")
    _corrompre_crc_archive(source, membre)
    avant = source.read_bytes()
    destination = tmp_path / "copie.3mf"

    with pytest.raises(BadZipFile, match="CRC"):
        trancher.normaliser(source, destination)

    assert source.read_bytes() == avant
    assert not destination.exists()


def test_normaliser_preserve_commentaires_pi_ordre_infos_et_commentaire_zip(tmp_path):
    """La migration vers Archive3MF garde les lexèmes XML suivis et les métadonnées ZIP copiées."""
    source = entree_3mf(tmp_path / "source.3mf")
    with ZipFile(source) as archive:
        membres = [(archive.getinfo(nom), archive.read(nom)) for nom in archive.namelist()]
    commentaire = b"commentaire-zip-de-test"
    modele = b'<config><!-- garder --><?avant garder?>' \
             b'<plate><metadata key="plater_name" value="Plaque 1"/></plate>' \
             b'<?apres garder?></config>'
    extra_info = ZipInfo("Metadata/extra.bin", (2024, 1, 2, 3, 4, 6))
    extra_info.compress_type = 8
    extra_info.external_attr = 0o100640 << 16
    with ZipFile(source, "w") as archive:
        archive.comment = commentaire
        for info, donnees in membres:
            if info.filename == "Metadata/model_settings.config":
                archive.writestr(info.filename, modele)
            else:
                archive.writestr(info, donnees)
        archive.writestr(extra_info, b"copie binaire")
    destination = tmp_path / "copie.3mf"

    trancher.normaliser(source, destination)

    with ZipFile(source) as avant, ZipFile(destination) as apres:
        assert apres.namelist() == avant.namelist()
        assert apres.comment == commentaire
        assert apres.getinfo("Metadata/extra.bin").date_time == avant.getinfo("Metadata/extra.bin").date_time
        assert apres.getinfo("Metadata/extra.bin").external_attr == avant.getinfo("Metadata/extra.bin").external_attr
        assert apres.getinfo("Metadata/extra.bin").compress_type == avant.getinfo("Metadata/extra.bin").compress_type
        contenu_modele = apres.read("Metadata/model_settings.config")
        assert b"<?avant garder?>" in contenu_modele
        assert b"<!-- garder -->" in contenu_modele
        assert b"<?apres garder?>" in contenu_modele
        assert apres.read("Metadata/extra.bin") == b"copie binaire"


def test_restauration_refuse_la_substitution_apres_integrite(tmp_path, monkeypatch):
    """Une version arrivée après le contrôle ne peut pas être écrasée."""
    from benchab.fabrication import tranchage_copies

    source = entree_3mf(tmp_path / "source.3mf")
    nouvelle = b"autre version de la fixture"
    verifier = tranchage_copies.integrite_3mf.verifier

    def verifier_puis_remplacer(chemin, copie, permis):
        resultat = verifier(chemin, copie, permis)
        autre = tmp_path / "autre.3mf"
        autre.write_bytes(nouvelle)
        autre.replace(source)
        return resultat

    monkeypatch.setattr(tranchage_copies.integrite_3mf, "verifier", verifier_puis_remplacer)
    with pytest.raises(ValueError, match="(?i)source|modifi"):
        trancher.restaurer_noms(source, ["Autre nom"])
    assert source.read_bytes() == nouvelle
    assert not list(tmp_path.glob(".noms-*"))


def test_restauration_noms_dans_copie_preserve_native_et_membres(tmp_path):
    """La nouvelle destination est exclusive et laisse le G-code natif intact."""
    source = entree_3mf(tmp_path / "native.3mf")
    with ZipFile(source, "a") as archive:
        archive.comment = b"commentaire fixture"
        archive.writestr("Metadata/plate_1.gcode", b"G90\r\n")
        archive.writestr("Metadata/annexe.txt", b"preuve")
    avant = source.read_bytes()
    sortie = tmp_path / "restauree.3mf"
    rapport = trancher.restaurer_noms(source, ["Nom restauré"], destination=sortie)
    assert rapport["reouverture"] == "PASS" and rapport["noms"] == ["Nom restauré"]
    assert source.read_bytes() == avant
    with ZipFile(source) as natif, ZipFile(sortie) as apres:
        assert natif.namelist() == apres.namelist() and natif.comment == apres.comment
        assert all(natif.read(nom) == apres.read(nom) for nom in natif.namelist()
                   if nom != "Metadata/model_settings.config")
    preuve = sortie.read_bytes()
    with pytest.raises(ValueError, match="empreinte"):
        trancher.restaurer_noms(source, ["Nom restauré"], empreinte_source="0" * 64)
    with pytest.raises(FileExistsError):
        trancher.restaurer_noms(source, ["Autre nom"], destination=sortie)
    assert sortie.read_bytes() == preuve and source.read_bytes() == avant


@pytest.mark.parametrize("copie_neuve", [False, True])
def test_restauration_refuse_octet_altere_apres_publication(tmp_path, monkeypatch, copie_neuve):
    """Même avec une taille et un mtime conservés, l’empreinte finale doit être exacte."""
    import os

    from benchab.qualite import ecritures

    source = entree_3mf(tmp_path / "native.3mf")
    with ZipFile(source, "a") as archive:
        archive.comment = b"natif"
    cible = tmp_path / "restauree.3mf" if copie_neuve else source

    def alterer(path):
        info = path.stat()
        donnees = path.read_bytes()
        assert donnees.endswith(b"natif")
        path.write_bytes(donnees[:-1] + b"x")
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))

    if copie_neuve:
        renommer = ecritures.renommer_exclusif

        def publication(temporaire, destination):
            renommer(temporaire, destination)
            alterer(Path(destination))

        monkeypatch.setattr(ecritures, "renommer_exclusif", publication)
    else:
        remplacer = Path.replace

        def remplacement(temporaire, destination):
            resultat = remplacer(temporaire, destination)
            if Path(destination) == source:
                alterer(source)
            return resultat

        monkeypatch.setattr(Path, "replace", remplacement)

    with pytest.raises(ValueError, match="(?i)copie publi|empreinte"):
        trancher.restaurer_noms(source, ["Nom restauré"], **({"destination": cible} if copie_neuve else {}))
    assert cible.read_bytes().endswith(b"natix")
