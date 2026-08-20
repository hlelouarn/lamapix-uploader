"""Config : persistance et migrations.

La migration du délai de transfert vient du diagnostic terrain : les anciens
défauts (60, 180, 300 s) laissaient chaque socket morte geler un ouvrier 20 à
45 s, alors qu'une photo part en 0,6 s. Les configs déployées portent ces
valeurs : il faut les rattraper sans écraser un vrai choix de l'utilisateur.
"""

from __future__ import annotations

import pytest

from lamapix_uploader.config import Config


class TestMigrationTimeoutDonnees:
    @pytest.mark.parametrize("herite", [12, 60, 180, 300])
    def test_les_anciens_defauts_sont_rattrapes(self, tmp_path, herite):
        fichier = tmp_path / "config.json"
        Config(ftp_utilisateur="x", timeout_donnees=herite).sauver(fichier)

        assert Config.charger(fichier).timeout_donnees == Config().timeout_donnees

    @pytest.mark.parametrize("choisi", [5, 8, 20, 45, 90])
    def test_un_choix_delibere_est_respecte(self, tmp_path, choisi):
        fichier = tmp_path / "config.json"
        Config(ftp_utilisateur="x", timeout_donnees=choisi).sauver(fichier)

        assert Config.charger(fichier).timeout_donnees == choisi

    def test_le_reste_de_la_config_survit_a_la_migration(self, tmp_path):
        fichier = tmp_path / "config.json"
        Config(
            ftp_utilisateur="compte",
            base_redim=r"D:\Kadra\redim",
            timeout_donnees=180,
            connexions_paralleles=3,
        ).sauver(fichier)

        relue = Config.charger(fichier)
        assert relue.ftp_utilisateur == "compte"
        assert relue.base_redim == r"D:\Kadra\redim"
        assert relue.connexions_paralleles == 3


class TestAllerRetour:
    def test_fichier_absent_donne_les_defauts(self, tmp_path):
        config = Config.charger(tmp_path / "absent.json")
        assert config.timeout_donnees == 8
        assert config.ftp_utilisateur == ""

    def test_json_corrompu_donne_les_defauts(self, tmp_path):
        fichier = tmp_path / "config.json"
        fichier.write_text("{pas du json", encoding="utf-8")
        assert Config.charger(fichier).ftp_hote == "www.lamapix.com"
