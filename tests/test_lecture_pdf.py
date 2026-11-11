"""Contrat de lecture PDF bornée, sans rendu, OCR ni accès machine."""

import builtins
import hashlib
import importlib.util
import json

import pytest

from benchab.cli import main as main_benchab
from benchab.connaissances import pdf

_LECTEUR_PDF_PRESENT = importlib.util.find_spec("pypdf") is not None
pytestmark_pdf = pytest.mark.skipif(not _LECTEUR_PDF_PRESENT, reason="extra optionnel pdf non installé")


def _construire_pdf(chemin, pages):
    """Écrit un PDF texte minimal à plusieurs pages, sans générateur tiers."""
    objets = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 7 0 R >> >> /Contents 4 0 R >>",
        None,
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 7 0 R >> >> /Contents 6 0 R >>",
        None,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 "
        b"/ColorSpace /DeviceGray /BitsPerComponent 8 /Length 1 >>\nstream\n\xff\nendstream",
    ]
    for index, texte in enumerate(pages):
        if texte is None:
            page_id = 3 + index * 2
            contenu = b"q 100 0 0 100 0 0 cm /Im0 Do Q"
            objets[page_id - 1] = (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                b"/Resources << /XObject << /Im0 8 0 R >> >> /Contents "
                + str(page_id + 1).encode()
                + b" 0 R >>"
            )
            pixels = b"\xff"
            objets[7] = (
                b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 "
                b"/ColorSpace /DeviceGray /BitsPerComponent 8 /Length 1 >>\nstream\n"
                + pixels
                + b"\nendstream"
            )
        else:
            contenu = f"BT /F1 12 Tf 72 720 Td ({texte}) Tj ET".encode("ascii")
        objets[3 + index * 2] = (
            b"<< /Length " + str(len(contenu)).encode() + b" >>\nstream\n" + contenu + b"\nendstream"
        )
    donnees = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, objet in enumerate(objets, 1):
        offsets.append(len(donnees))
        donnees.extend(f"{index} 0 obj\n".encode() + objet + b"\nendobj\n")
    position_xref = len(donnees)
    donnees.extend(f"xref\n0 {len(objets) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        donnees.extend(f"{offset:010d} 00000 n \n".encode())
    donnees.extend(
        f"trailer\n<< /Root 1 0 R /Size {len(objets) + 1} >>\nstartxref\n{position_xref}\n%%EOF\n".encode()
    )
    chemin.write_bytes(donnees)
    return chemin


@pytestmark_pdf
def test_extrait_chaque_page_avec_provenance_sha_et_sans_rendu(tmp_path):
    source = _construire_pdf(tmp_path / "guide.pdf", ["PAGE ONE", "PAGE TWO"])
    avant = source.read_bytes()

    rapport = pdf.analyser(source)

    assert rapport["statut"] == "PASS"
    assert rapport["source"]["chemin"] == str(source.resolve())
    assert rapport["source"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert [page["numero"] for page in rapport["pages"]] == [1, 2]
    assert [page["texte"].strip() for page in rapport["pages"]] == ["PAGE ONE", "PAGE TWO"]
    assert rapport["restitution"] == {"mode": "texte", "format": "JSON stdout avec un numéro par page"}
    assert rapport["ocr"] == {"effectue": False, "limite": "Aucun OCR n'est lancé."}
    assert rapport["rendu"]["effectue"] is False
    assert source.read_bytes() == avant


@pytestmark_pdf
def test_selection_de_pages_ne_retourne_que_les_pages_demandees(tmp_path):
    source = _construire_pdf(tmp_path / "guide.pdf", ["PAGE ONE", "PAGE TWO"])

    rapport = pdf.analyser(source, pages=[2])

    assert rapport["statut"] == "PASS"
    assert [page["numero"] for page in rapport["pages"]] == [2]
    assert rapport["pages"][0]["texte"].strip() == "PAGE TWO"


@pytestmark_pdf
def test_commande_benchab_extrait_une_page_sur_demande(atelier_initialise, tmp_path, capsys):
    source = _construire_pdf(tmp_path / "guide.pdf", ["PAGE ONE", "PAGE TWO"])

    code = main_benchab([
        "--prive", str(atelier_initialise), "connaissances", "pdf", str(source), "--page", "2",
    ])

    rapport = json.loads(capsys.readouterr().out)
    assert code == rapport["code"] == 0
    assert [page["numero"] for page in rapport["pages"]] == [2]
    assert source.exists()


@pytestmark_pdf
def test_pdf_sans_couche_texte_reste_non_verifie_et_signale_ocr(tmp_path):
    source = _construire_pdf(tmp_path / "image-seule.pdf", [None, "PAGE TWO"])

    rapport = pdf.analyser(source)

    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert rapport["pages"][0]["statut"] == "NON VÉRIFIÉ"
    assert "OCR" in rapport["pages"][0]["limite"]
    assert rapport["pages"][1]["statut"] == "PASS"
    assert rapport["ocr"]["effectue"] is False


@pytestmark_pdf
def test_nombre_et_identifiants_de_pages_sont_bornes(tmp_path):
    source = _construire_pdf(tmp_path / "guide.pdf", ["PAGE ONE", "PAGE TWO"])

    try:
        pdf.analyser(source, pages=[3])
    except ValueError as erreur:
        assert "page" in str(erreur).lower()
    else:
        raise AssertionError("Une page hors du document doit être refusée")

    try:
        pdf.analyser(source, max_pages=1)
    except ValueError as erreur:
        assert "limite" in str(erreur).lower()
    else:
        raise AssertionError("Un document qui dépasse la limite de pages doit être refusé")


@pytestmark_pdf
def test_taille_et_liens_symboliques_sont_refuses(tmp_path):
    source = _construire_pdf(tmp_path / "guide.pdf", ["PAGE ONE", "PAGE TWO"])
    try:
        pdf.analyser(source, max_octets=32)
    except ValueError as erreur:
        assert "octets" in str(erreur).lower()
    else:
        raise AssertionError("Un PDF trop grand doit être refusé avant parsing")

    lien = tmp_path / "guide-lien.pdf"
    lien.symlink_to(source)
    try:
        pdf.analyser(lien)
    except ValueError as erreur:
        assert "lien" in str(erreur).lower()
    else:
        raise AssertionError("Un PDF lié doit être refusé")


@pytestmark_pdf
def test_pdf_malforme_est_non_verifie_sans_traceback(tmp_path, capsys):
    source = tmp_path / "malforme.pdf"
    source.write_bytes(b"ceci n'est pas un PDF")

    code = pdf.main([str(source)])

    rapport = json.loads(capsys.readouterr().out)
    assert code == 2
    assert rapport["statut"] == "NON VÉRIFIÉ"
    assert "PDF" in rapport["erreur"]


def test_dependance_absente_donne_un_message_actionnable(tmp_path, monkeypatch, capsys):
    source = _construire_pdf(tmp_path / "guide.pdf", ["PAGE ONE", "PAGE TWO"])
    import_original = builtins.__import__

    def import_sans_pypdf(nom, *args, **kwargs):
        if nom == "pypdf":
            raise ModuleNotFoundError("optional module unavailable")
        return import_original(nom, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_sans_pypdf)
    code = pdf.main([str(source)])
    sortie = json.loads(capsys.readouterr().out)
    assert code == 2
    assert sortie["statut"] == "NON VÉRIFIÉ"
    assert "--extra pdf" in sortie["erreur"]
