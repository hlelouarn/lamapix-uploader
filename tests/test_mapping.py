"""Règles §3 du brief, sur les chemins réels de production cités dans le brief."""

from __future__ import annotations

import pytest

from lamapix_uploader.mapping import (
    chemin_distant,
    est_photo_eligible,
    rendre_unique,
)

FICHIER = "2026-07-19GRANDPRIX#DUPONT MARIE#ECLAIR DU VALLON#H92_4832_50ffb8c2.jpg"


class TestCheminDistant:
    def test_epreuve_et_dossard_disparaissent(self):
        source = rf"CSO_01_Critérium Amateur Elite - 1.25m\1001_DUPONT MARIE_ECLAIR DU VALLON\{FICHIER}"
        assert chemin_distant(source) == f"DUPONT MARIE_ECLAIR DU VALLON/{FICHIER}"

    def test_meme_cavalier_dans_deux_epreuves_fusionne(self):
        """Le point clé : deux épreuves, deux dossards, un seul dossier distant."""
        premier = chemin_distant(rf"CSO_01_Amateur\1001_DUPONT MARIE_ECLAIR\{FICHIER}")
        second = chemin_distant(rf"CSO_04_Pro\2317_DUPONT MARIE_ECLAIR\autre.jpg")
        assert premier is not None and second is not None
        assert premier.split("/")[0] == second.split("/")[0] == "DUPONT MARIE_ECLAIR"

    def test_ambiance_est_mise_a_plat(self):
        source = r"0_AMBIANCE\0001_AMBIANCE_AMBIANCE\photo#Z99_0014_76300f6e.jpg"
        assert chemin_distant(source) == "AMBIANCE/photo#Z99_0014_76300f6e.jpg"

    @pytest.mark.parametrize("dossier", ["0_AMBIANCE", "AMBIANCE", "12_ambiance"])
    def test_variantes_du_dossier_ambiance(self, dossier):
        assert chemin_distant(rf"{dossier}\x.jpg") == "AMBIANCE/x.jpg"

    def test_ambiance_tres_profonde_reste_a_plat(self):
        assert chemin_distant(r"0_AMBIANCE\a\b\c\x.jpg") == "AMBIANCE/x.jpg"

    def test_photo_a_la_racine_reste_a_la_racine(self):
        assert chemin_distant("x.jpg") == "x.jpg"

    def test_sous_dossier_sous_le_cavalier_est_conserve(self):
        source = r"CSO_01\1001_NOM_CHEVAL\detail\x.jpg"
        assert chemin_distant(source) == "NOM_CHEVAL/detail/x.jpg"

    def test_photo_sans_dossier_cavalier_est_ignoree(self):
        """On préfère ne pas envoyer que ranger la photo chez le mauvais cavalier."""
        assert chemin_distant(r"CSO_01_Amateur\x.jpg") is None

    def test_dossier_cavalier_reduit_a_un_dossard_est_ignore(self):
        assert chemin_distant(r"CSO_01\1001_\x.jpg") is None


class TestEligibilite:
    @pytest.mark.parametrize("nom", ["a.jpg", "a.JPG", "a.jpeg", r"dossier\a.Jpg"])
    def test_jpeg_acceptes(self, nom):
        assert est_photo_eligible(nom)

    @pytest.mark.parametrize("nom", ["a.png", "a.webp", "a.cr2", "a.txt"])
    def test_autres_extensions_refusees(self, nom):
        assert not est_photo_eligible(nom)

    @pytest.mark.parametrize(
        "chemin",
        [r"webp\a.jpg", r"CSO_01\1001_NOM_CHEVAL\webp\a.jpg", r"a\WEBP\b\c.jpg"],
    )
    def test_dossier_webp_exclu_a_toute_profondeur(self, chemin):
        assert not est_photo_eligible(chemin)

    def test_fichier_nomme_webp_reste_accepte(self):
        """C'est le DOSSIER webp qu'on exclut, pas un fichier qui s'appelle ainsi."""
        assert est_photo_eligible(r"CSO_01\1001_NOM_CHEVAL\webp.jpg")


class TestUnicite:
    def test_chemin_libre_est_rendu_tel_quel(self):
        assert rendre_unique("NOM/x.jpg", "C:/src/a.jpg", {}) == "NOM/x.jpg"

    def test_meme_source_garde_son_chemin(self):
        """Sinon une photo retouchée changerait de nom à chaque passage."""
        utilises = {"NOM/x.jpg": "C:/src/a.jpg"}
        assert rendre_unique("NOM/x.jpg", "C:/src/a.jpg", utilises) == "NOM/x.jpg"

    def test_collision_recoit_un_suffixe(self):
        utilises = {"NOM/x.jpg": "C:/src/a.jpg"}
        assert rendre_unique("NOM/x.jpg", "C:/src/b.jpg", utilises) == "NOM/x_2.jpg"

    def test_collisions_successives(self):
        utilises = {"NOM/x.jpg": "C:/a.jpg", "NOM/x_2.jpg": "C:/b.jpg"}
        assert rendre_unique("NOM/x.jpg", "C:/c.jpg", utilises) == "NOM/x_3.jpg"

    def test_collision_a_la_racine(self):
        utilises = {"x.jpg": "C:/a.jpg"}
        assert rendre_unique("x.jpg", "C:/b.jpg", utilises) == "x_2.jpg"

    def test_suffixe_stable_dans_le_temps(self):
        """La mémoire rejoue les mêmes affectations : `b.jpg` retrouve SON `_2`,
        au lieu de se voir attribuer un `_3` à chaque redémarrage."""
        utilises = {"NOM/x.jpg": "C:/a.jpg", "NOM/x_2.jpg": "C:/b.jpg"}
        assert rendre_unique("NOM/x.jpg", "C:/b.jpg", utilises) == "NOM/x_2.jpg"
