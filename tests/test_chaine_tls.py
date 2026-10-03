"""Chaîne de certificats incomplète : la compléter sans affaiblir la vérification.

Contexte réel (03/10/2026) : le FTP de Lamapix n'envoie que son certificat,
renouvelé le 18/09 sous un intermédiaire Let's Encrypt tout neuf (« YE1 »).
Plus aucun PC ne pouvait vérifier ; la seule issue était de tout désactiver.

Les fixtures sont les intermédiaires PUBLICS de Let's Encrypt (pas le certificat
du site). Rien ici ne touche le réseau, ni ne dépend de la date du jour : on
teste la remontée et les verrous, pas la validité des certificats.
"""

from __future__ import annotations

import ssl
from pathlib import Path

import certifi
import pytest

from lamapix_uploader import chaine_tls
from lamapix_uploader.ftp import _contextes_ssl

FIXTURES = Path(__file__).parent / "fixtures"
YE1 = (FIXTURES / "le_ye1.der").read_bytes()
ROOT_YE = (FIXTURES / "le_root_ye_par_x2.der").read_bytes()
X2_PAR_X1 = (FIXTURES / "le_x2_par_x1.der").read_bytes()


def _nom(der: bytes, champ: str = "subject") -> str:
    # Certaines racines anciennes n'ont pas de nom commun : chaîne vide alors.
    return dict(x[0] for x in chaine_tls.decoder(der)[champ]).get("commonName", "")


def _racine_certifi(nom_commun: str) -> bytes:
    """Une vraie racine autosignée, prise dans le magasin embarqué."""
    contexte = ssl.create_default_context(cafile=certifi.where())
    for der in contexte.get_ca_certs(binary_form=True):
        if _nom(der) == nom_commun:
            return der
    raise AssertionError(f"{nom_commun} absent de certifi")


X1_RACINE = _racine_certifi("ISRG Root X1")

# Ce que servirait Internet : URL AIA -> certificat de l'émetteur.
TOILE = {
    "http://ye.i.lencr.org/": ROOT_YE,
    "http://x2.i.lencr.org/": X2_PAR_X1,
    "http://x1.i.lencr.org/": X1_RACINE,
}


@pytest.fixture(autouse=True)
def _cache_vierge(racine_isolee):
    """Disque isolé + mémoire vidée : aucun test n'hérite du précédent."""
    chaine_tls._memoire.clear()
    yield
    chaine_tls._memoire.clear()


class TestDecodage:
    def test_le_decodeur_interne_existe_toujours(self):
        """Il s'appuie sur une fonction interne de `ssl`. Si une future version
        de Python la retire, ce test casse AVANT qu'un exe ne soit construit
        (construire.ps1 lance la suite d'abord)."""
        infos = chaine_tls.decoder(YE1)
        assert _nom(YE1) == "YE1"
        assert infos["caIssuers"] == ("http://ye.i.lencr.org/",)

    def test_autosigne(self):
        assert chaine_tls.est_autosigne(chaine_tls.decoder(X1_RACINE)) is True
        assert chaine_tls.est_autosigne(chaine_tls.decoder(YE1)) is False


class TestRemontee:
    def test_remonte_jusqua_la_racine_sans_la_retenir(self):
        """YE1 → Root YE → ISRG Root X2 (croisé) → ISRG Root X1 : la racine
        arrête la remontée et n'est PAS retenue — une racine ne se télécharge
        pas, elle doit déjà être dans le magasin."""
        recuperes = chaine_tls.remonter(YE1, TOILE.get)
        assert [_nom(d) for d in recuperes] == ["Root YE", "ISRG Root X2"]
        assert X1_RACINE not in recuperes

    def test_un_certificat_qui_nest_pas_lemetteur_annonce_est_ecarte(self):
        """L'URL AIA répond autre chose que l'émetteur attendu (erreur ou
        tromperie) : on ne le retient pas."""
        toile_trompeuse = {"http://ye.i.lencr.org/": X2_PAR_X1}
        assert chaine_tls.remonter(YE1, toile_trompeuse.get) == []

    def test_une_racine_servie_a_la_place_dun_intermediaire_est_ecartee(self):
        toile = {"http://ye.i.lencr.org/": X1_RACINE}
        assert chaine_tls.remonter(YE1, toile.get) == []

    def test_telechargement_impossible(self):
        """Hors ligne : rien de récupéré, et surtout pas d'exception."""
        assert chaine_tls.remonter(YE1, lambda url: None) == []

    def test_reponse_illisible(self):
        assert chaine_tls.remonter(YE1, lambda url: b"<html>404</html>") == []

    def test_la_profondeur_est_bornee(self):
        """Une boucle A→B→A ne doit pas tourner sans fin."""
        appels = []

        def toile(url):
            appels.append(url)
            return TOILE.get(url)

        chaine_tls.remonter(YE1, toile)
        assert len(appels) <= chaine_tls.PROFONDEUR_MAX


class TestCompleter:
    def test_du_nouveau_est_signale_et_memorise(self):
        assert chaine_tls.completer("hote.test", 21, lambda: YE1, TOILE.get) is True
        assert [_nom(d) for d in chaine_tls.connus("hote.test", 21)] == [
            "Root YE",
            "ISRG Root X2",
        ]

    def test_rien_de_nouveau_ninvite_pas_a_reessayer(self):
        """Sinon la connexion bouclerait : compléter, échouer, compléter…"""
        chaine_tls.completer("hote.test", 21, lambda: YE1, TOILE.get)
        assert chaine_tls.completer("hote.test", 21, lambda: YE1, TOILE.get) is False

    def test_serveur_injoignable(self):
        def tombe():
            raise OSError("timed out")

        assert chaine_tls.completer("hote.test", 21, tombe, TOILE.get) is False

    def test_le_cache_survit_au_redemarrage(self):
        """Sur Starlink on se reconnecte sans cesse : pas question de
        retélécharger à chaque fois, ni de dépendre du web au lancement."""
        chaine_tls.completer("hote.test", 21, lambda: YE1, TOILE.get)
        chaine_tls._memoire.clear()                      # « redémarrage »
        assert len(chaine_tls.connus("hote.test", 21)) == 2

    def test_un_cache_disque_trafique_ne_fait_pas_entrer_de_racine(self):
        """Défense en profondeur : même relu du disque, rien d'autosigné."""
        chaine_tls.memoriser("hote.test", 21, [ROOT_YE, X1_RACINE])
        chaine_tls._memoire.clear()
        assert [_nom(d) for d in chaine_tls.connus("hote.test", 21)] == ["Root YE"]

    def test_les_serveurs_ne_se_melangent_pas(self):
        chaine_tls.completer("a.test", 21, lambda: YE1, TOILE.get)
        assert chaine_tls.connus("b.test", 21) == []
        assert chaine_tls.connus("a.test", 990) == []


class TestVerificationToujoursStricte:
    """Le point de sécurité. Vérifié aussi en conditions réelles contre un faux
    serveur et une fausse autorité : l'attaquant est refusé — et il serait
    ACCEPTÉ si PARTIAL_CHAIN restait actif."""

    def test_sans_intermediaire_rien_ne_change(self):
        for contexte in _contextes_ssl(False):
            assert contexte.verify_mode == ssl.CERT_REQUIRED
            assert contexte.check_hostname is True

    def test_avec_intermediaires_la_chaine_doit_finir_sur_une_vraie_racine(self):
        for contexte in _contextes_ssl(False, [ROOT_YE, X2_PAR_X1]):
            assert contexte.verify_mode == ssl.CERT_REQUIRED
            assert contexte.check_hostname is True
            assert not (contexte.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN)

    def test_les_intermediaires_sont_bien_charges(self):
        contexte = _contextes_ssl(False, [ROOT_YE])[-1]
        assert ROOT_YE in contexte.get_ca_certs(binary_form=True)

    def test_un_intermediaire_illisible_ne_casse_pas_la_connexion(self):
        contextes = _contextes_ssl(False, [b"n'importe quoi", ROOT_YE])
        assert len(contextes) == 2

    def test_ignorer_reste_un_choix_explicite(self):
        (contexte,) = _contextes_ssl(True, [ROOT_YE])
        assert contexte.verify_mode == ssl.CERT_NONE
