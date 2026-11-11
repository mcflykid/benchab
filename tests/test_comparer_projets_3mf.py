"""Comparaison préparatoire de palettes entre archives synthétiques."""

import json
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from benchab.cli import main as cli_main
from benchab.controles.comparer_projets_3mf import comparer


def _archive(path, settings):
    """Écrit uniquement les réglages fictifs utiles au contrat de comparaison."""
    with ZipFile(path, "w") as archive:
        archive.writestr("Metadata/project_settings.config", json.dumps(settings))


def _archive_peinte(
    path, *, dialect="paint_color", couleurs=("1", "2"), triangles=((0, 1, 2), (0, 2, 3)),
    x1="1", composant=False, relation_ambigue=False, objet_supplementaire=False,
    type_relation=None, cible_relation="/3D/3dmodel.model", mode_relation=None,
    qname_relation=False, proprietes_objet=None,
):
    """Écrit un modèle OPC synthétique direct, avec deux facettes et un dialecte explicite."""
    from benchab.imports3d.lecture_3mf import NS, NS_REL, REL_MODELE

    modele = ET.Element(NS + "model", unit="millimeter")
    ressources = ET.SubElement(modele, NS + "resources")
    objet = ET.SubElement(ressources, NS + "object", id="1", type="model")
    for cle, valeur in (proprietes_objet or {}).items():
        objet.set(cle, valeur)
    if composant:
        ET.SubElement(objet, NS + "components")
    maillage = ET.SubElement(objet, NS + "mesh")
    sommets = ET.SubElement(maillage, NS + "vertices")
    for x, y, z in (("0", "0", "0"), (x1, "0", "0"), ("1", "1", "0"), ("0", "1", "0")):
        ET.SubElement(sommets, NS + "vertex", x=x, y=y, z=z)
    faces = ET.SubElement(maillage, NS + "triangles")
    for index, face in enumerate(triangles):
        attributs = dict(zip(("v1", "v2", "v3"), map(str, face), strict=True))
        if couleurs[index] is not None:
            nom = (
                "paint_color" if dialect == "paint_color"
                else "{http://schemas.slic3r.org/3mf/2017/06}mmu_segmentation"
            )
            attributs[nom] = couleurs[index]
        ET.SubElement(faces, NS + "triangle", **attributs)
    if objet_supplementaire:
        ET.SubElement(ressources, NS + "object", id="2", type="model")
    construction = ET.SubElement(modele, NS + "build")
    ET.SubElement(construction, NS + "item", objectid="1")
    relations = ET.Element(NS_REL + "Relationships")
    attributs_relation = {"Id": "rModel", "Type": type_relation or REL_MODELE, "Target": cible_relation}
    if mode_relation is not None:
        attributs_relation["TargetMode"] = mode_relation
    qname = "{urn:autre}Relationship" if qname_relation else NS_REL + "Relationship"
    ET.SubElement(relations, qname, attributs_relation)
    if relation_ambigue:
        ET.SubElement(
            relations,
            NS_REL + "Relationship",
            Id="rAlias",
            Type=REL_MODELE,
            Target="/3D/3dmodel.model",
        )
    with ZipFile(path, "w") as archive:
        archive.writestr("_rels/.rels", ET.tostring(relations))
        archive.writestr("3D/3dmodel.model", ET.tostring(modele))
        archive.writestr("Metadata/project_settings.config", json.dumps({
            "filament_settings_id": ["Profil A", "Profil B"],
            "filament_colour": ["#112233", "#AABBCC"],
        }))


def test_palette_comparee_par_identite_et_ordre_sans_affirmer_peinture(tmp_path):
    """Compare des couleurs par slot seulement si les identités et leur ordre concordent."""
    source = tmp_path / "source.3mf"
    candidate = tmp_path / "candidate.3mf"
    slots = ["Filament A", "Filament B"]
    _archive(source, {
        "filament_settings_id": slots,
        "filament_colour": ["#112233", "#AABBCC"],
        "top_shell_layers": "4",
    })
    _archive(candidate, {
        "filament_settings_id": slots,
        "filament_colour": ["#112233", "#AABBCC"],
        "top_shell_layers": "4",
    })

    rapport = comparer(source, candidate)

    assert rapport["palette"]["statut"] == "PASS"
    assert [slot["identite_profil"] for slot in rapport["palette"]["associations_profils"]] == slots
    assert rapport["palette"]["attribution_physique_piece"]["statut"] == "NON VÉRIFIÉ"
    assert rapport["reglages"]["statut"] == "PASS"
    assert rapport["peinture"]["statut"] == "NON VÉRIFIÉ"
    assert rapport["statut"] == "NON VÉRIFIÉ"


def test_palette_ordre_different_est_signale_et_attribution_reste_non_verifiee(tmp_path):
    """Une même couleur sous des identités réordonnées ne prouve pas la conservation du slot."""
    source = tmp_path / "source.3mf"
    candidate = tmp_path / "candidate.3mf"
    _archive(source, {
        "filament_settings_id": ["Filament A", "Filament B"],
        "filament_colour": ["#112233", "#AABBCC"],
    })
    _archive(candidate, {
        "filament_settings_id": ["Filament B", "Filament A"],
        "filament_colour": ["#112233", "#AABBCC"],
    })

    rapport = comparer(source, candidate)

    assert rapport["palette"]["statut"] == "NON VÉRIFIÉ"
    assert rapport["palette"]["associations_profils"] == []
    assert rapport["palette"]["association_profil_couleur"]["statut"] == "NON VÉRIFIÉ"
    assert {d["cle"] for d in rapport["reglages"]["differences"]} == {"filament_settings_id"}


def test_absence_de_reglage_est_signalee_et_sources_restent_intactes(tmp_path):
    """Une clé absente du candidat est visible et les deux fichiers restent identiques."""
    source = tmp_path / "source.3mf"
    candidate = tmp_path / "candidate.3mf"
    _archive(source, {"top_shell_layers": "4"})
    _archive(candidate, {})
    octets_source, octets_candidate = source.read_bytes(), candidate.read_bytes()

    rapport = comparer(source, candidate)

    assert any(d["cle"] == "top_shell_layers" for d in rapport["reglages"]["absences"])
    assert len(rapport["sha256"]["source"]) == 64
    assert len(rapport["sha256"]["candidat"]) == 64
    assert source.read_bytes() == octets_source
    assert candidate.read_bytes() == octets_candidate


def test_cli_est_un_diagnostic_json_sans_ouvrir_un_atelier(tmp_path, capsys, monkeypatch):
    """Le contrôle compare deux fixtures sans Base, mesure, sortie fichier ou réglage modifié."""
    source = tmp_path / "source.3mf"
    candidate = tmp_path / "candidate.3mf"
    _archive(source, {"filament_settings_id": ["Filament A"], "filament_colour": ["#112233"]})
    _archive(candidate, {"filament_settings_id": ["Filament A"], "filament_colour": ["#112233"]})
    monkeypatch.setattr("benchab.cli.Base", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Base")))

    code = cli_main(["controle", "comparer-projets-3mf", str(source), str(candidate)])

    sortie = capsys.readouterr()
    assert code == 2  # Les propriétés Core et la compatibilité restent NON VÉRIFIÉES.
    assert sortie.err == ""
    rapport = json.loads(sortie.out)
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["palette"]["statut"] == "PASS"


def test_palette_signale_couleur_litteralement_differente(tmp_path):
    """Une couleur modifiée au même slot est signalée sans conversion ou normalisation."""
    source = tmp_path / "source.3mf"
    candidate = tmp_path / "candidate.3mf"
    _archive(source, {"filament_settings_id": ["Filament A"], "filament_colour": ["#112233"]})
    _archive(candidate, {"filament_settings_id": ["Filament A"], "filament_colour": ["#112234"]})

    rapport = comparer(source, candidate)

    assert rapport["palette"]["statut"] == "WARN"
    assert rapport["palette"]["association_profil_couleur"]["statut"] == "PASS"
    assert rapport["palette"]["differences_litterales"][0]["statut"] == "DIFFÉRENT"
    assert rapport["reglages"]["statut"] == "WARN"


def test_profils_repetes_n_interpretent_pas_les_couleurs_comme_slots(tmp_path):
    """Des identifiants de profil répétés ne désignent pas des slots physiques distincts."""
    source = tmp_path / "source.3mf"
    candidate = tmp_path / "candidate.3mf"
    ids = ["Profil partagé", "Profil partagé"]
    _archive(source, {"filament_settings_id": ids, "filament_colour": ["#112233", "#AABBCC"]})
    _archive(candidate, {"filament_settings_id": ids, "filament_colour": ["#112234", "#AABBCC"]})

    rapport = comparer(source, candidate)

    assert rapport["palette"]["association_profil_couleur"]["statut"] == "NON VÉRIFIÉ"
    assert rapport["palette"]["associations_profils"] == []
    assert rapport["palette"]["differences_litterales"][0]["index_litteral"] == 1
    assert rapport["palette"]["differences_litterales"][0]["statut"] == "DIFFÉRENT"
    assert rapport["palette"]["attribution_physique_piece"]["statut"] == "NON VÉRIFIÉ"


def test_couleur_vide_ou_mal_formee_reste_visible_sans_association(tmp_path):
    """Une chaîne vide ou qui n’est pas un RGB hexadécimal n’est pas un slot coloré qualifié."""
    source = tmp_path / "source.3mf"
    candidate = tmp_path / "candidate.3mf"
    _archive(source, {"filament_settings_id": ["Profil A"], "filament_colour": [""]})
    _archive(candidate, {"filament_settings_id": ["Profil A"], "filament_colour": ["#GGGGGG"]})

    rapport = comparer(source, candidate)

    assert rapport["palette"]["association_profil_couleur"]["statut"] == "NON VÉRIFIÉ"
    assert rapport["palette"]["associations_profils"] == []
    assert rapport["palette"]["differences_litterales"] == [{
        "index_litteral": 1, "couleur_source": "", "couleur_candidat": "#GGGGGG",
        "present_source": True, "present_candidat": True, "statut": "DIFFÉRENT",
    }]


def test_couleur_residuelle_est_signalee_sans_devenir_un_slot(tmp_path):
    """Une couleur en trop reste un résidu littéral, jamais un filament actif inventé."""
    source = tmp_path / "source.3mf"
    candidate = tmp_path / "candidate.3mf"
    _archive(source, {"filament_settings_id": ["Profil A"], "filament_colour": ["#112233", "#AABBCC"]})
    _archive(candidate, {"filament_settings_id": ["Profil A"], "filament_colour": ["#112233"]})

    rapport = comparer(source, candidate)

    assert rapport["palette"]["association_profil_couleur"]["statut"] == "NON VÉRIFIÉ"
    assert rapport["palette"]["associations_profils"] == []
    assert rapport["palette"]["couleurs_residuelles"]["source"] == [{
        "index_litteral": 2, "valeur": "#AABBCC", "statut": "NON VÉRIFIÉ",
    }]
    assert rapport["palette"]["differences_litterales"][0]["statut"] == "DIFFÉRENT"


def test_absence_commune_de_palette_ne_devient_pas_une_difference(tmp_path):
    """Deux palettes absentes ne sont ni égales ni différentes, et restent NON VÉRIFIÉES."""
    source = tmp_path / "source.3mf"
    candidate = tmp_path / "candidate.3mf"
    _archive(source, {})
    _archive(candidate, {})

    rapport = comparer(source, candidate)

    assert rapport["palette"]["statut"] == "NON VÉRIFIÉ"
    assert rapport["palette"]["differences_litterales"] == []
    assert rapport["palette"]["association_profil_couleur"]["statut"] == "NON VÉRIFIÉ"


def test_peinture_litterale_signale_une_couleur_facette_retiree(tmp_path):
    """Une propriété retirée d’une facette est visible si l’identité et l’ordre restent établis."""
    source, candidate = tmp_path / "source.3mf", tmp_path / "candidate.3mf"
    _archive_peinte(source)
    _archive_peinte(candidate, couleurs=("1", None))

    rapport = comparer(source, candidate)

    peinture = rapport["peinture"]
    assert peinture["extensions_asn_prusa"]["statut"] == "WARN"
    assert peinture["extensions_asn_prusa"]["dialecte"] == "paint_color"
    assert peinture["extensions_asn_prusa"]["differences_litterales"] == [{
        "objet_id": "1", "facette": 2, "attribut": "paint_color",
        "source": "2", "candidat": None, "present_source": True, "present_candidat": False,
        "statut": "DIFFÉRENT",
    }]


def test_peinture_non_comparee_si_ordre_des_facettes_change(tmp_path):
    """La même couleur au même index ne suffit pas lorsque les facettes ont été permutées."""
    source, candidate = tmp_path / "source.3mf", tmp_path / "candidate.3mf"
    _archive_peinte(source)
    _archive_peinte(candidate, triangles=((0, 2, 3), (0, 1, 2)), couleurs=("1", "2"))

    rapport = comparer(source, candidate)

    assert rapport["peinture"]["extensions_asn_prusa"]["statut"] == "NON VÉRIFIÉ"
    assert "ordre" in rapport["peinture"]["extensions_asn_prusa"]["raison"].lower()
    assert rapport["peinture"]["extensions_asn_prusa"]["differences_litterales"] == []


def test_peinture_non_comparee_entre_qnames_de_dialectes_differents(tmp_path):
    """Les valeurs ASN paint_color et Prusa mmu_segmentation ne sont jamais transcodées."""
    source, candidate = tmp_path / "source.3mf", tmp_path / "candidate.3mf"
    _archive_peinte(source, dialect="paint_color")
    _archive_peinte(candidate, dialect="mmu_segmentation")

    rapport = comparer(source, candidate)

    assert rapport["peinture"]["extensions_asn_prusa"]["statut"] == "NON VÉRIFIÉ"
    assert "dialecte" in rapport["peinture"]["extensions_asn_prusa"]["raison"].lower()
    assert rapport["peinture"]["extensions_asn_prusa"]["differences_litterales"] == []


def test_peinture_non_comparee_si_coordonnees_changees(tmp_path):
    """Un maillage qui a bougé n’est pas pris pour la même surface peinte."""
    source, candidate = tmp_path / "source.3mf", tmp_path / "candidate.3mf"
    _archive_peinte(source)
    _archive_peinte(candidate, x1="1.0")

    rapport = comparer(source, candidate)

    assert rapport["peinture"]["extensions_asn_prusa"]["statut"] == "NON VÉRIFIÉ"
    assert "coordonnées" in rapport["peinture"]["extensions_asn_prusa"]["raison"].lower()


def test_peinture_non_verifiee_si_composant_objet_ou_relation_ambigue(tmp_path):
    """Composants, objets multiples et StartPart aliasés ne sont jamais choisis arbitrairement."""
    for nom, options in (
        ("composant", {"composant": True}),
        ("objet", {"objet_supplementaire": True}),
        ("relation", {"relation_ambigue": True}),
    ):
        source, candidate = tmp_path / f"{nom}-source.3mf", tmp_path / f"{nom}-candidate.3mf"
        _archive_peinte(source, **options)
        _archive_peinte(candidate)

        rapport = comparer(source, candidate)

        assert rapport["peinture"]["extensions_asn_prusa"]["statut"] == "NON VÉRIFIÉ"


def test_perte_ou_ajout_integral_de_peinture_est_warn(tmp_path):
    """Un dialecte unique d’un seul côté qualifie la perte ou l’ajout littéral intégral."""
    for nom, couleurs_source, couleurs_candidat, attendu in (
        ("perte", ("1", "2"), (None, None), "peinture absente du candidat"),
        ("ajout", (None, None), ("1", "2"), "peinture absente de la source"),
    ):
        source, candidate = tmp_path / f"{nom}-source.3mf", tmp_path / f"{nom}-candidate.3mf"
        _archive_peinte(source, couleurs=couleurs_source)
        _archive_peinte(candidate, couleurs=couleurs_candidat)

        peinture = comparer(source, candidate)["peinture"]["extensions_asn_prusa"]

        assert peinture["statut"] == "WARN"
        assert peinture["cause"] == attendu
        assert len(peinture["differences_litterales"]) == 2


def test_proprietes_core_vides_ne_sont_pas_signalees_presentes(tmp_path):
    """Les dictionnaires de facette vides ne prouvent aucune propriété Core."""
    source, candidate = tmp_path / "source.3mf", tmp_path / "candidate.3mf"
    _archive_peinte(source)
    _archive_peinte(candidate, proprietes_objet={"pid": "2", "pindex": "0"})

    peinture = comparer(source, candidate)["peinture"]["proprietes_standard_3mf"]

    assert peinture["present_source"] is False
    assert peinture["present_candidat"] is True
    assert peinture["statut"] == "NON VÉRIFIÉ"


def test_relation_opc_non_qualifiee_ou_cible_dangereuse_reste_non_verifiee(tmp_path):
    """La résolution directe refuse QName, type, chemin et mode externe non qualifiés."""
    for nom, options in (
        ("qname", {"qname_relation": True}),
        ("type", {"type_relation": "urn:other/3dmodel"}),
        ("parent", {"cible_relation": "/3D/../3D/3dmodel.model"}),
        ("backslash", {"cible_relation": "3D\\3dmodel.model"}),
        ("uri", {"cible_relation": "https://example.invalid/model"}),
        ("external", {"mode_relation": "External"}),
    ):
        source, candidate = tmp_path / f"{nom}-source.3mf", tmp_path / f"{nom}-candidate.3mf"
        _archive_peinte(source, **options)
        _archive_peinte(candidate)

        assert comparer(source, candidate)["peinture"]["extensions_asn_prusa"]["statut"] == "NON VÉRIFIÉ"


def test_facette_aux_indices_repetes_reste_non_verifiee(tmp_path):
    """Une triangle dégénéré répété n’établit pas une identité de facette qualifiée."""
    source, candidate = tmp_path / "source.3mf", tmp_path / "candidate.3mf"
    _archive_peinte(source, triangles=((0, 0, 2), (0, 2, 3)))
    _archive_peinte(candidate, triangles=((0, 0, 2), (0, 2, 3)))

    assert comparer(source, candidate)["peinture"]["extensions_asn_prusa"]["statut"] == "NON VÉRIFIÉ"


def test_cle_inconnue_reste_non_verifiee_meme_identique(tmp_path):
    """Une clé hors dictionnaire n’est pas présentée comme préservée par égalité brute."""
    source = tmp_path / "source.3mf"
    candidate = tmp_path / "candidate.3mf"
    _archive(source, {"vendor_painted_faces": ["01", "02"]})
    _archive(candidate, {"vendor_painted_faces": ["01", "02"]})

    rapport = comparer(source, candidate)

    assert rapport["reglages"]["statut"] == "NON VÉRIFIÉ"
    assert rapport["reglages"]["non_verifies"][0]["cle"] == "vendor_painted_faces"
    assert rapport["peinture"]["extensions_asn_prusa"]["statut"] == "NON VÉRIFIÉ"


def test_absence_de_config_projet_reste_non_verifiee(tmp_path):
    """Un 3MF sans réglages projet n’est pas traité comme un projet vide équivalent."""
    source = tmp_path / "source.3mf"
    candidate = tmp_path / "candidate.3mf"
    with ZipFile(source, "w") as archive:
        archive.writestr("3D/3dmodel.model", "<model/>")
    _archive(candidate, {"filament_settings_id": ["Filament A"], "filament_colour": ["#112233"]})

    rapport = comparer(source, candidate)

    assert rapport["reglages"]["statut"] == "NON VÉRIFIÉ"
    assert rapport["palette"]["statut"] == "NON VÉRIFIÉ"
    assert rapport["peinture"]["statut"] == "NON VÉRIFIÉ"


def test_cli_archive_illisible_est_refuse_sans_traceback(tmp_path, capsys):
    """Une archive qui n’est pas un ZIP retourne le code technique 2 sans traceback."""
    source = tmp_path / "source.3mf"
    candidate = tmp_path / "candidate.3mf"
    source.write_bytes(b"not a zip")
    _archive(candidate, {})

    code = cli_main(["controle", "comparer-projets-3mf", str(source), str(candidate)])

    sortie = capsys.readouterr()
    assert code == 2
    assert sortie.out == ""
    assert "comparaison 3MF refusée" in sortie.err
    assert "Traceback" not in sortie.err


def test_cli_resume_conserve_le_rapport_complet_et_les_sources(tmp_path, capsys):
    """Une sortie courte garde le chemin et les écarts sans perdre le rapport détaillé."""
    from benchab import cli

    source, candidat = (tmp_path / f"{nom}.3mf" for nom in ("source", "candidate"))
    _archive(source, {"top_shell_layers": "3", "vendor_inconnu": "A"})
    _archive(candidat, {"top_shell_layers": "5", "vendor_inconnu": "B"})
    avant = (source.read_bytes(), candidat.read_bytes())
    prive = tmp_path / "prive"
    prive.mkdir()
    sortie = prive / "rapport.json"
    commande = ["--prive", str(prive), "controle", "comparer-projets-3mf", str(source), str(candidat)]
    assert cli.main([*commande, "--resume", "--sortie", str(sortie)]) == 2
    resultat = capsys.readouterr()
    assert not resultat.err
    court = json.loads(resultat.out)
    complet = json.loads(sortie.read_bytes())
    assert court["rapport"] == str(sortie)
    assert court["reglages"]["differences"] == len(complet["reglages"]["differences"])
    assert court["reglages"]["non_verifies"] == len(complet["reglages"]["non_verifies"])
    assert court["statut"] == complet["statut"] == "NON VÉRIFIÉ"
    assert "vendor_inconnu" not in resultat.out and "vendor_inconnu" in sortie.read_text()
    assert (source.read_bytes(), candidat.read_bytes()) == avant
    empreinte = sortie.read_bytes()
    assert cli.main([*commande, "--resume", "--sortie", str(sortie)]) == 2
    assert capsys.readouterr().err and sortie.read_bytes() == empreinte
