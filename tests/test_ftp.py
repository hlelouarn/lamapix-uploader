"""Client FTPS : détection des fragments d'envoi interrompu et garde-fous.

Aucune connexion réseau ici — on teste la logique de décision, celle qui
autorise la seule suppression que l'outil pratique sur le serveur.
"""

from __future__ import annotations

import pytest

from lamapix_uploader.ftp import ClientFtps, ErreurFtp

EVENEMENT = "2026-08-23BJ22DRESSFESTIVAL"
CAVALIER = "EUWER VICTOR_DIRAJA ALTAIR"
PHOTO = "2026-08-23BJ22DRESSFESTIVAL#EUWER VICTOR#DIRAJA ALTAIR#Z93_6548_b54d9771.jpg"
REL = f"{CAVALIER}/{PHOTO}"

# Message réel renvoyé par Lamapix en production.
MESSAGE_REEL = (
    f"550 /{EVENEMENT}/{CAVALIER}/{PHOTO} : un fichier caché temporaire "
    f"« /{EVENEMENT}/{CAVALIER}/.in.{PHOTO}. » existe déjà"
)


@pytest.fixture
def client():
    return ClientFtps(
        hote="exemple", port=21, utilisateur="u", mot_de_passe="p", racine=EVENEMENT
    )


class TestDetection:
    def test_le_message_de_production_est_reconnu(self, client):
        fragment = client._fragment_bloquant(MESSAGE_REEL, REL)
        assert fragment == f"/{EVENEMENT}/{CAVALIER}/.in.{PHOTO}."

    def test_message_anglais(self, client):
        message = (
            f'550 {PHOTO}: Temporary hidden file '
            f'"/{EVENEMENT}/{CAVALIER}/.in.{PHOTO}." already exists'
        )
        assert client._fragment_bloquant(message, REL) is not None

    def test_repli_si_le_serveur_ne_cite_aucun_chemin(self, client):
        """On reconstruit le nom au format ProFTPD par défaut plutôt qu'abandonner."""
        message = "550 hidden file already exists"
        assert client._fragment_bloquant(message, REL) == (
            f"/{EVENEMENT}/{CAVALIER}/.in.{PHOTO}."
        )

    @pytest.mark.parametrize(
        "message",
        [
            "550 Directory not found",
            "550 Permission denied",
            "553 Could not create file",
            "421 Service not available",
        ],
    )
    def test_les_autres_550_ne_declenchent_rien(self, client, message):
        """Surtout ne pas transformer une panne banale en suppression."""
        assert client._fragment_bloquant(message, REL) is None

    def test_un_chemin_hors_evenement_est_refuse(self, client):
        """La réponse du serveur est une donnée, pas un ordre : on ne la suit pas
        aveuglément vers un DELE."""
        message = (
            "550 un fichier caché temporaire "
            "« /AUTRE_EVENEMENT/QUELQU UN/.in.photo.jpg. » existe déjà"
        )
        fragment = client._fragment_bloquant(message, REL)
        assert fragment.startswith(f"/{EVENEMENT}/")

    def test_un_chemin_non_cache_est_refuse(self, client):
        """Le serveur ne doit pas pouvoir nous faire effacer une vraie photo."""
        message = (
            f"550 un fichier caché temporaire "
            f"« /{EVENEMENT}/{CAVALIER}/{PHOTO} » existe déjà"
        )
        fragment = client._fragment_bloquant(message, REL)
        assert fragment != f"/{EVENEMENT}/{CAVALIER}/{PHOTO}"
        assert ".in." in fragment


class TestGardeFouSuppression:
    @pytest.mark.parametrize(
        "chemin",
        [
            f"/{EVENEMENT}/{CAVALIER}/{PHOTO}",              # une vraie photo
            f"/AUTRE/{CAVALIER}/.in.{PHOTO}.",               # un autre événement
            "/.in.quelque_chose.",                           # hors événement
            f"/{EVENEMENT}/{CAVALIER}",                       # un dossier
        ],
    )
    def test_refus_de_tout_ce_qui_nest_pas_un_fragment(self, client, chemin):
        with pytest.raises(ErreurFtp, match="refus de supprimer"):
            client.supprimer_fragment(chemin)


class TestClassementDesPannes:
    """Une liaison morte et un refus du serveur n'appellent pas la même réponse :
    sur la première, insister sur la même photo ne fait que geler un ouvrier
    pendant tout le délai de transfert."""

    @pytest.mark.parametrize(
        "exception",
        [
            TimeoutError("timed out"),
            ConnectionResetError("[WinError 10054] connexion fermée par l'hôte"),
            ConnectionAbortedError("[WinError 10053]"),
            EOFError(),
            OSError("The read operation timed out"),
        ],
    )
    def test_pannes_de_lien_reconnues(self, exception):
        from lamapix_uploader.ftp import _est_panne_de_lien

        assert _est_panne_de_lien(exception) is True

    @pytest.mark.parametrize(
        "exception",
        [
            Exception("550 Permission denied"),
            Exception("553 Could not create file"),
            Exception("530 Login incorrect"),
        ],
    )
    def test_refus_du_serveur_non_confondus(self, exception):
        from lamapix_uploader.ftp import _est_panne_de_lien

        assert _est_panne_de_lien(exception) is False
