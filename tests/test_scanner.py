"""Scan du dossier événement (§4.1, §4.2)."""

from __future__ import annotations

import os
import time

from lamapix_uploader.scanner import lister_evenements, scanner


def _photo(racine, chemin_relatif, contenu=b"jpeg", age_secondes=3600):
    fichier = racine / chemin_relatif
    fichier.parent.mkdir(parents=True, exist_ok=True)
    fichier.write_bytes(contenu)
    quand = time.time() - age_secondes
    os.utime(fichier, (quand, quand))
    return fichier


class TestScan:
    def test_trouve_les_jpeg_en_profondeur(self, tmp_path):
        _photo(tmp_path, "CSO_01/1001_NOM_CHEVAL/a.jpg")
        _photo(tmp_path, "0_AMBIANCE/b.jpeg")
        trouvees = scanner(tmp_path)
        assert {p.chemin.name for p in trouvees} == {"a.jpg", "b.jpeg"}

    def test_ignore_le_dossier_webp(self, tmp_path):
        _photo(tmp_path, "CSO_01/1001_NOM_CHEVAL/a.jpg")
        _photo(tmp_path, "CSO_01/1001_NOM_CHEVAL/webp/a.jpg")
        assert len(scanner(tmp_path)) == 1

    def test_ignore_les_autres_extensions(self, tmp_path):
        _photo(tmp_path, "CSO_01/1001_NOM_CHEVAL/a.png")
        _photo(tmp_path, "CSO_01/1001_NOM_CHEVAL/a.cr2")
        assert scanner(tmp_path) == []

    def test_fichier_encore_en_ecriture_est_ecarte(self, tmp_path):
        """Kadra ou une synchro écrit peut-être encore : on l'enverrait tronqué."""
        _photo(tmp_path, "CSO_01/1001_NOM_CHEVAL/frais.jpg", age_secondes=2)
        assert scanner(tmp_path, delai_stabilite=15) == []

    def test_le_meme_fichier_passe_une_fois_stabilise(self, tmp_path):
        _photo(tmp_path, "CSO_01/1001_NOM_CHEVAL/frais.jpg", age_secondes=2)
        assert len(scanner(tmp_path, delai_stabilite=1)) == 1

    def test_taille_et_chemin_relatif_remontes(self, tmp_path):
        _photo(tmp_path, "CSO_01/1001_NOM_CHEVAL/a.jpg", contenu=b"x" * 42)
        (photo,) = scanner(tmp_path)
        assert photo.taille == 42
        assert photo.relatif == os.path.join("CSO_01", "1001_NOM_CHEVAL", "a.jpg")

    def test_dossier_inexistant_ne_leve_pas(self, tmp_path):
        """Un partage réseau qui tombe ne doit pas arrêter l'outil."""
        assert scanner(tmp_path / "absent") == []


class TestListeEvenements:
    def test_les_plus_recents_dabord(self, tmp_path):
        for nom, age in (("ancien", 10_000), ("recent", 10)):
            dossier = tmp_path / nom
            dossier.mkdir()
            quand = time.time() - age
            os.utime(dossier, (quand, quand))
        assert lister_evenements(tmp_path) == ["recent", "ancien"]

    def test_les_fichiers_ne_sont_pas_listes(self, tmp_path):
        (tmp_path / "un_evenement").mkdir()
        (tmp_path / "note.txt").write_text("x", encoding="utf-8")
        assert lister_evenements(tmp_path) == ["un_evenement"]

    def test_base_injoignable_donne_une_liste_vide(self, tmp_path):
        assert lister_evenements(tmp_path / "absent") == []
