"""Contrôle des contrats de chemins publics, sans parcours des données privées."""

import hashlib
import json
from pathlib import Path

from benchab.qualite import structure
from benchab.qualite.edition import verifier as verifier_edition
from benchab.qualite.structure import verifier


def _contrat(racine, *, standards=(), interdits=("projets", "donnees", "outils", "suivi", "profils", "sauvegardes")):
    """Écrit un contrat minimal privé à la fixture ; les tests ne touchent jamais le contrat du dépôt."""
    config = racine / "config/architecture.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    noms = ", ".join(json.dumps(nom) for nom in standards)
    racines = ", ".join(json.dumps(nom) for nom in interdits)
    config.write_text(
        f"schema = 1\nnoms_standards = [{noms}]\nracines_interdites = [{racines}]\n",
        encoding="utf-8",
    )
    return config


def test_nommage_modules_standards_et_prive_exclu(tmp_path):
    """Accepte standards et modules privés snake_case, sans inspecter les chemins sous prive/."""
    root = tmp_path / "repo"
    root.mkdir()
    _contrat(root, standards=("README.md", "LICENSE"))
    (root / "README.md").write_text("fixture", encoding="utf-8")
    package = root / "benchab"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "_module_prive.py").write_text("", encoding="utf-8")
    (root / "prive").mkdir()
    (root / "prive/Mauvais Nom.py").write_text("secret fixture", encoding="utf-8")
    assert verifier(root) == []


def test_noms_invalides_et_racines_publiques_interdites(tmp_path):
    """Signale noms non conformes et données projet placées à la racine publique."""
    root = tmp_path / "repo"
    root.mkdir()
    _contrat(root)
    (root / "dossier Invalide").mkdir()
    (root / "script-Maj.py").write_text("", encoding="utf-8")
    (root / "projets").mkdir()
    erreurs = verifier(root)
    assert any("Dossier mal nommé : dossier Invalide" in erreur for erreur in erreurs)
    assert any("Fichier mal nommé : script-Maj.py" in erreur for erreur in erreurs)
    assert any("Racine publique interdite : projets" in erreur for erreur in erreurs)


def test_collisions_nfc_casefold_detectees_sur_entrees_injectees(tmp_path):
    """Détecte casse et normalisation Unicode même si le système de fichiers les confond."""
    root = tmp_path / "repo"
    root.mkdir()
    _contrat(root)
    entrees = [("Nom.md", False), ("nom.md", False), ("cafe\u0301.md", False), ("café.md", False)]
    erreurs = verifier(root, entrees=entrees)
    assert sum("Collision macOS" in erreur for erreur in erreurs) == 2


def test_exception_exige_preuve_sha_exacte(tmp_path):
    """Autorise un nom dérogatoire seulement avec justification et fichier de preuve intact."""
    root = tmp_path / "repo"
    root.mkdir()
    evidence = root / "preuve.md"
    evidence.write_text("contrat versionné", encoding="utf-8")
    target = root / "Fichier externe.dat"
    target.write_text("contenu de fixture", encoding="utf-8")
    sha = hashlib.sha256(evidence.read_bytes()).hexdigest()
    config = root / "config/architecture.toml"
    config.parent.mkdir()
    config.write_text(
        f'''schema = 1
noms_standards = []
racines_interdites = []

[[exceptions]]
chemin = "Fichier externe.dat"
regle = "Nom imposé par un contrat externe"
raison = "Fixture de test"
contrainte = "Le nom est fixé par l'outil consommateur"
duree = "Durée du contrat"
preuve_fichier = "preuve.md"
preuve_sha256 = "{sha}"
''',
        encoding="utf-8",
    )
    assert verifier(root) == []
    evidence.write_text("preuve altérée", encoding="utf-8")
    assert any("empreinte de preuve" in erreur for erreur in verifier(root))


def test_exception_nouvre_jamais_preuve_dans_racine_privee_casefold(tmp_path, monkeypatch):
    """Refuse PRIVE/ avant hash ou lecture ; la variante de casse ne devient pas un chemin public."""
    root = tmp_path / "repo"
    root.mkdir()
    secret = root / "PRIVE/secret.md"
    secret.parent.mkdir()
    secret.write_text("fixture secrète", encoding="utf-8")
    (root / "Nom externe.md").write_text("contenu", encoding="utf-8")
    config = _contrat(root)
    with config.open("a", encoding="utf-8") as fichier:
        fichier.write(
            '\n[[exceptions]]\nchemin = "Nom externe.md"\nregle = "contrat"\n'
            'raison = "fixture"\ncontrainte = "nom fixe"\nduree = "fixture"\n'
            'preuve_fichier = "PRIVE/secret.md"\npreuve_sha256 = "' + "0" * 64 + '"\n'
        )
    lus = []
    empreinte_originale = structure.sha256_fichier

    def surveiller(chemin):
        if Path(chemin).name == "secret.md":
            lus.append(Path(chemin))
            raise AssertionError("preuve privée lue")
        return empreinte_originale(chemin)

    monkeypatch.setattr(structure, "sha256_fichier", surveiller)
    erreurs = verifier(root)
    assert lus == []
    assert any("preuve de nommage absente ou invalide" in erreur for erreur in erreurs)
    assert not any("PRIVE" in erreur or "secret.md" in erreur for erreur in erreurs)


def test_contrat_ressource_tierce_ne_masque_pas_collision(tmp_path):
    """Lie le nom upstream à TIERS et son consommateur, mais garde la détection de collisions active."""
    root = tmp_path / "repo"
    root.mkdir()
    tiers = root / "TIERS.md"
    tiers.write_text("Paquet amont fixture", encoding="utf-8")
    ressource = root / "vendor/paquet-1.0/PROVENANCE.json"
    ressource.parent.mkdir(parents=True)
    ressource.write_text("{}", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src/loader.py").write_text("", encoding="utf-8")
    preuve_sha = hashlib.sha256(tiers.read_bytes()).hexdigest()
    source_sha = hashlib.sha256(ressource.read_bytes()).hexdigest()
    config = root / "config/architecture.toml"
    config.parent.mkdir()
    config.write_text(
        f'''schema = 1
noms_standards = ["TIERS.md"]
racines_interdites = []

[[ressources]]
racine = "vendor/paquet-1.0"
regle = "Noms amont conservés"
raison = "Ressource de fixture consommée localement"
contrainte = "Version et noms de fichiers contractuels"
duree = "Durée de la fixture"
consommateur = "src/loader.py"
preuve_fichier = "TIERS.md"
preuve_sha256 = "{preuve_sha}"
source_fichier = "vendor/paquet-1.0/PROVENANCE.json"
source_sha256 = "{source_sha}"
''',
        encoding="utf-8",
    )
    assert verifier(root) == []
    erreurs = verifier(
        root,
        entrees=[
            ("vendor/paquet-1.0/ChargeurUpper.js", False),
            ("vendor/paquet-1.0/chargeurupper.js", False),
        ],
    )
    assert any("Collision macOS" in erreur for erreur in erreurs)


def test_edition_exige_contrat_sur_checkout_benchab(tmp_path):
    """Branche le contrôle structurel aux checks d’édition seulement pour un checkout BENCHAB reconnu."""
    root = tmp_path / "repo"
    (root / "benchab").mkdir(parents=True)
    (root / "benchab/__init__.py").write_text("", encoding="utf-8")
    erreurs = verifier_edition(root, lecteur_url=lambda _url: None)
    assert any("Contrat de structure absent ou illisible" in erreur for erreur in erreurs)
