"""Le pipeline complet, avec le faux Lamapix : c'est ici que se vérifient les
promesses du brief (jamais deux fois, jamais bloqué, jamais rien perdu)."""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath

import pytest

from lamapix_uploader import engine as module_moteur

from .conftest import poser_photo

PHOTO_A = "CSO_01_Amateur/1001_DUPONT MARIE_ECLAIR/a.jpg"
PHOTO_B = "CSO_01_Amateur/1001_DUPONT MARIE_ECLAIR/b.jpg"
PHOTO_C = "CSO_04_Pro/2317_MARTIN PAUL_ORAGE/c.jpg"
AMBIANCE = "0_AMBIANCE/0001_AMBIANCE_AMBIANCE/z.jpg"


@pytest.fixture(autouse=True)
def _pas_dattente_entre_essais(monkeypatch):
    """Les reprises attendent quelques secondes en production ; inutile de le
    subir en test. Les DÉLAIS DE REPRISE, eux, restent réels : plusieurs tests
    vérifient qu'une photo écartée n'est pas réessayée tout de suite."""
    monkeypatch.setattr(module_moteur, "DELAIS_ENTRE_ESSAIS", (0.0, 0.0, 0.0))


@pytest.fixture
def source(tmp_path):
    dossier = tmp_path / "redim" / "2026-08-02GRANDPRIX"
    dossier.mkdir(parents=True)
    return dossier


def deposes(serveur) -> set[str]:
    """Chemins distants effectivement reçus par « Lamapix »."""
    return set(serveur.fichiers)


def scanner_sans_envoyer(moteur) -> None:
    """Le geste réel de l'opérateur avant d'initialiser : mettre en pause, laisser
    l'outil repérer les photos, puis décider."""
    moteur.basculer_pause()
    moteur._un_tour()
    moteur.basculer_pause()


class TestTourNominal:
    def test_les_photos_partent_restructurees(self, source, serveur, fabrique_moteur):
        poser_photo(source, PHOTO_A)
        poser_photo(source, PHOTO_C)
        poser_photo(source, AMBIANCE)

        fabrique_moteur(source)._un_tour()

        assert deposes(serveur) == {
            "2026-08-02GRANDPRIX/DUPONT MARIE_ECLAIR/a.jpg",
            "2026-08-02GRANDPRIX/MARTIN PAUL_ORAGE/c.jpg",
            "2026-08-02GRANDPRIX/AMBIANCE/z.jpg",
        }

    def test_la_racine_de_levenement_est_creee(self, source, serveur, fabrique_moteur):
        """Kadra ne la crée plus : sans MKD explicite, c'est 550 garanti."""
        poser_photo(source, PHOTO_A)
        fabrique_moteur(source)._un_tour()
        assert "2026-08-02GRANDPRIX" in serveur.dossiers

    def test_le_tampon_est_le_miroir_du_ftp(self, source, fabrique_moteur, racine_isolee):
        """Plan B du brief : glisser le tampon dans FileZilla doit donner pareil."""
        poser_photo(source, PHOTO_A)
        poser_photo(source, AMBIANCE)
        fabrique_moteur(source)._un_tour()

        tampon = racine_isolee / "donnees" / "tampon" / "2026-08-02GRANDPRIX"
        assert (tampon / "DUPONT MARIE_ECLAIR" / "a.jpg").exists()
        assert (tampon / "AMBIANCE" / "z.jpg").exists()

    def test_la_source_nest_jamais_touchee(self, source, fabrique_moteur):
        photo = poser_photo(source, PHOTO_A)
        fabrique_moteur(source)._un_tour()
        assert photo.exists()

    def test_webp_et_autres_extensions_nagissent_pas(self, source, serveur, fabrique_moteur):
        poser_photo(source, PHOTO_A)
        poser_photo(source, "CSO_01_Amateur/1001_DUPONT MARIE_ECLAIR/webp/a.jpg")
        poser_photo(source, "CSO_01_Amateur/1001_DUPONT MARIE_ECLAIR/a.png")
        fabrique_moteur(source)._un_tour()
        assert len(deposes(serveur)) == 1

    def test_compteurs_de_letat(self, source, fabrique_moteur):
        poser_photo(source, PHOTO_A)
        poser_photo(source, PHOTO_B)
        moteur = fabrique_moteur(source)
        moteur._un_tour()

        etat = moteur.etat()
        assert (etat.detectees, etat.envoyees, etat.en_attente, etat.erreurs) == (2, 2, 0, 0)
        assert etat.pourcentage == 100


class TestJamaisDeuxFois:
    def test_un_second_tour_nenvoie_rien(self, source, serveur, fabrique_moteur):
        poser_photo(source, PHOTO_A)
        moteur = fabrique_moteur(source)
        moteur._un_tour()
        moteur._un_tour()
        assert serveur.stors.count("2026-08-02GRANDPRIX/DUPONT MARIE_ECLAIR/a.jpg") == 1

    def test_apres_redemarrage_rien_nest_renvoye(self, source, serveur, fabrique_moteur):
        """Le cas qui coûte cher : coupure, relance, et tout repart en double."""
        poser_photo(source, PHOTO_A)
        fabrique_moteur(source)._un_tour()
        serveur.stors.clear()

        fabrique_moteur(source)._un_tour()  # nouvelle instance, même dossier
        assert serveur.stors == []

    def test_lamapix_a_tout_aspire_on_ne_renvoie_pas_pour_autant(
        self, source, serveur, fabrique_moteur
    ):
        """Le FTP vide ne prouve rien : la mémoire locale fait seule autorité."""
        poser_photo(source, PHOTO_A)
        moteur = fabrique_moteur(source)
        moteur._un_tour()
        serveur.consommer()
        serveur.stors.clear()

        moteur._un_tour()
        assert serveur.stors == []

    def test_photo_retouchee_repart_sous_le_meme_nom(self, source, serveur, fabrique_moteur):
        photo = poser_photo(source, PHOTO_A, contenu=b"version 1")
        moteur = fabrique_moteur(source)
        moteur._un_tour()

        poser_photo(source, PHOTO_A, contenu=b"version 2 nettement plus longue")
        moteur._un_tour()

        rel = "2026-08-02GRANDPRIX/DUPONT MARIE_ECLAIR/a.jpg"
        assert serveur.stors.count(rel) == 2
        assert serveur.fichiers[rel] == b"version 2 nettement plus longue"
        assert photo.read_bytes() == b"version 2 nettement plus longue"


class TestPannesEtReprises:
    def test_un_echec_passager_est_rattrape(self, source, serveur, fabrique_moteur):
        rel = "2026-08-02GRANDPRIX/DUPONT MARIE_ECLAIR/a.jpg"
        serveur.echecs_pour[rel] = 1  # premier STOR en 550, comme quand Lamapix ingère
        poser_photo(source, PHOTO_A)

        fabrique_moteur(source)._un_tour()
        assert rel in serveur.fichiers

    def test_les_dossiers_consommes_sont_recrees_au_retry(
        self, source, serveur, fabrique_moteur
    ):
        rel = "2026-08-02GRANDPRIX/DUPONT MARIE_ECLAIR/a.jpg"
        serveur.echecs_pour[rel] = 1
        poser_photo(source, PHOTO_A)
        moteur = fabrique_moteur(source)

        # Lamapix a tout aspiré entre-temps : le retry doit refaire l'arborescence.
        serveur.consommer()
        moteur._un_tour()

        assert "2026-08-02GRANDPRIX/DUPONT MARIE_ECLAIR" in serveur.dossiers
        assert rel in serveur.fichiers

    def test_une_photo_en_erreur_ne_bloque_pas_les_autres(
        self, source, serveur, fabrique_moteur
    ):
        """La règle qui compte en concours : la file continue, quoi qu'il arrive."""
        maudite = "2026-08-02GRANDPRIX/DUPONT MARIE_ECLAIR/a.jpg"
        serveur.echecs_pour[maudite] = 99
        poser_photo(source, PHOTO_A)
        poser_photo(source, PHOTO_B)
        poser_photo(source, PHOTO_C)

        moteur = fabrique_moteur(source)
        moteur._un_tour()

        assert "2026-08-02GRANDPRIX/DUPONT MARIE_ECLAIR/b.jpg" in serveur.fichiers
        assert "2026-08-02GRANDPRIX/MARTIN PAUL_ORAGE/c.jpg" in serveur.fichiers
        assert maudite not in serveur.fichiers
        assert moteur.etat().erreurs == 1

    def test_la_photo_en_erreur_est_mise_en_attente_pas_reessayee_en_boucle(
        self, source, serveur, fabrique_moteur
    ):
        maudite = "2026-08-02GRANDPRIX/DUPONT MARIE_ECLAIR/a.jpg"
        serveur.echecs_pour[maudite] = 99
        poser_photo(source, PHOTO_A)

        moteur = fabrique_moteur(source, essais_max=2)
        moteur._un_tour()
        essais_premier_tour = serveur.stors.count(maudite)
        moteur._un_tour()  # le cooldown de 4 min court encore

        assert essais_premier_tour == 2
        assert serveur.stors.count(maudite) == 2

    def test_trois_echecs_mettent_le_dossier_en_attente(
        self, source, serveur, fabrique_moteur
    ):
        prefixe = "2026-08-02GRANDPRIX/DUPONT MARIE_ECLAIR/"
        for nom in ("a", "b", "d"):
            serveur.echecs_pour[f"{prefixe}{nom}.jpg"] = 99
            poser_photo(source, f"CSO_01_Amateur/1001_DUPONT MARIE_ECLAIR/{nom}.jpg")
        poser_photo(source, "CSO_01_Amateur/1001_DUPONT MARIE_ECLAIR/e.jpg")

        moteur = fabrique_moteur(source, essais_max=1)
        moteur._un_tour()

        # Les 3 premières échouent, le dossier passe en cooldown : la 4e attendra.
        assert f"{prefixe}e.jpg" not in serveur.fichiers
        assert moteur._pause_dossier

    def test_identifiants_refuses_arretent_proprement(self, source, serveur, fabrique_moteur):
        serveur.identifiants_invalides = True
        poser_photo(source, PHOTO_A)

        moteur = fabrique_moteur(source)
        moteur._un_tour()

        assert moteur.etat().identifiants_refuses is True
        assert deposes(serveur) == set()

    def test_rien_nest_perdu_apres_une_panne(self, source, serveur, fabrique_moteur):
        """La photo en échec reste en attente : elle repartira, pas question de l'oublier."""
        maudite = "2026-08-02GRANDPRIX/DUPONT MARIE_ECLAIR/a.jpg"
        serveur.echecs_pour[maudite] = 99
        poser_photo(source, PHOTO_A)

        moteur = fabrique_moteur(source, essais_max=1)
        moteur._un_tour()
        assert moteur.etat().en_attente == 1

        serveur.echecs_pour[maudite] = 0
        moteur._pause_fichier.clear()  # on simule la fin des 4 minutes
        moteur._un_tour()
        assert maudite in serveur.fichiers


class TestEnvoisParalleles:
    def test_deux_connexions_envoient_tout(self, source, serveur, fabrique_moteur):
        for index in range(12):
            poser_photo(source, f"CSO_01_Amateur/1001_NOM_CHEVAL/p{index:02d}.jpg")

        fabrique_moteur(source, connexions_paralleles=2)._un_tour()

        assert len(deposes(serveur)) == 12
        assert serveur.connexions >= 2

    def test_pas_de_doublon_en_parallele(self, source, serveur, fabrique_moteur):
        """Deux threads ne doivent jamais se servir la même photo."""
        for index in range(20):
            poser_photo(source, f"CSO_01_Amateur/1001_NOM_CHEVAL/p{index:02d}.jpg")

        fabrique_moteur(source, connexions_paralleles=3)._un_tour()

        assert len(serveur.stors) == len(set(serveur.stors)) == 20


class TestBoutons:
    def test_initialiser_pose_la_frontiere_sans_rien_envoyer(
        self, source, serveur, fabrique_moteur
    ):
        poser_photo(source, PHOTO_A)
        poser_photo(source, PHOTO_B)
        moteur = fabrique_moteur(source)

        assert moteur.initialiser_memoire() == 2
        moteur._un_tour()
        assert serveur.stors == []

        poser_photo(source, PHOTO_C)  # seule la nouveauté doit partir
        moteur._un_tour()
        assert deposes(serveur) == {"2026-08-02GRANDPRIX/MARTIN PAUL_ORAGE/c.jpg"}

    def test_initialiser_peut_poser_une_frontiere_datee(
        self, source, serveur, fabrique_moteur
    ):
        """Le cas qui compte : Kadra s'est arrêté en cours d'événement. On ne
        déclare envoyé que ce qui précède son arrêt, pas tout le dossier."""
        poser_photo(source, PHOTO_A)                      # datée d'il y a 1 h
        recente = poser_photo(source, PHOTO_B)
        os.utime(recente, (time.time() - 60, time.time() - 60))

        moteur = fabrique_moteur(source)
        scanner_sans_envoyer(moteur)

        assert moteur.initialiser_memoire(avant=time.time() - 600) == 1

        moteur._un_tour()
        assert deposes(serveur) == {"2026-08-02GRANDPRIX/DUPONT MARIE_ECLAIR/b.jpg"}

    def test_apercu_annonce_ce_qui_sera_avale(self, source, fabrique_moteur):
        """L'utilisateur doit voir le nombre AVANT de valider, pas après."""
        poser_photo(source, PHOTO_A)
        recente = poser_photo(source, PHOTO_B)
        os.utime(recente, (time.time() - 60, time.time() - 60))

        moteur = fabrique_moteur(source)
        scanner_sans_envoyer(moteur)

        assert moteur.apercu_initialisation() == (2, 2)
        assert moteur.apercu_initialisation(avant=time.time() - 600) == (1, 2)

    def test_initialiser_marche_meme_apres_un_scan(self, source, fabrique_moteur):
        """Une photo déjà repérée mais pas encore partie doit être avalée."""
        poser_photo(source, PHOTO_A)
        moteur = fabrique_moteur(source)
        scanner_sans_envoyer(moteur)

        assert moteur.initialiser_memoire() == 1

    def test_initialiser_est_annulable(self, source, serveur, fabrique_moteur):
        """Le filet : si Kadra n'avait pas fini, les photos enterrées ressortent."""
        poser_photo(source, PHOTO_A)
        poser_photo(source, PHOTO_B)
        moteur = fabrique_moteur(source)
        scanner_sans_envoyer(moteur)
        moteur.initialiser_memoire()
        assert moteur.etat().initialisees == 2

        assert moteur.annuler_initialisation() == 2
        assert moteur.etat().initialisees == 0

        moteur._un_tour()
        assert len(deposes(serveur)) == 2

    def test_annuler_ne_renvoie_pas_les_photos_reellement_parties(
        self, source, serveur, fabrique_moteur
    ):
        """Sinon l'annulation créerait les doublons qu'on cherche à éviter."""
        poser_photo(source, PHOTO_A)
        moteur = fabrique_moteur(source)
        moteur._un_tour()                       # a.jpg part pour de vrai

        poser_photo(source, PHOTO_B)
        scanner_sans_envoyer(moteur)
        moteur.initialiser_memoire()            # n'avale que b.jpg
        serveur.stors.clear()

        assert moteur.annuler_initialisation() == 1
        moteur._un_tour()
        assert serveur.stors == ["2026-08-02GRANDPRIX/DUPONT MARIE_ECLAIR/b.jpg"]

    def test_un_envoi_reel_ne_compte_pas_comme_initialise(
        self, source, fabrique_moteur
    ):
        poser_photo(source, PHOTO_A)
        moteur = fabrique_moteur(source)
        moteur._un_tour()
        assert (moteur.etat().envoyees, moteur.etat().initialisees) == (1, 0)

    def test_le_drapeau_survit_au_redemarrage(self, source, fabrique_moteur):
        """Une déclaration reste annulable demain, pas seulement dans la minute."""
        poser_photo(source, PHOTO_A)
        moteur = fabrique_moteur(source)
        scanner_sans_envoyer(moteur)
        moteur.initialiser_memoire()

        relance = fabrique_moteur(source)
        assert relance.etat().initialisees == 1
        assert relance.annuler_initialisation() == 1

    def test_reinitialiser_renvoie_tout(self, source, serveur, fabrique_moteur):
        poser_photo(source, PHOTO_A)
        moteur = fabrique_moteur(source)
        moteur._un_tour()

        moteur.reinitialiser_memoire()
        moteur._un_tour()
        assert serveur.stors.count("2026-08-02GRANDPRIX/DUPONT MARIE_ECLAIR/a.jpg") == 2

    def test_pause_suspend_les_envois_mais_pas_le_scan(
        self, source, serveur, fabrique_moteur
    ):
        poser_photo(source, PHOTO_A)
        moteur = fabrique_moteur(source)
        moteur.basculer_pause()

        moteur._un_tour()
        assert serveur.stors == []
        assert moteur.etat().detectees == 1  # le scan a bien tourné

        moteur.basculer_pause()
        moteur._un_tour()
        assert len(deposes(serveur)) == 1


class TestPurge:
    def test_le_tampon_est_purge_apres_le_delai(
        self, source, fabrique_moteur, racine_isolee
    ):
        poser_photo(source, PHOTO_A)
        moteur = fabrique_moteur(source, purge_apres_heures=24)
        moteur._un_tour()

        tampon = racine_isolee / "donnees" / "tampon" / "2026-08-02GRANDPRIX"
        copie = tampon / "DUPONT MARIE_ECLAIR" / "a.jpg"
        assert copie.exists()

        # On antidate l'envoi de deux jours et on relance la purge.
        vieux = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        for entree in moteur._memoire.entrees.values():
            entree.envoyee_le = vieux
        moteur._prochaine_purge = 0
        moteur._un_tour()

        assert not copie.exists()

    def test_la_purge_ne_touche_ni_la_source_ni_la_memoire(
        self, source, serveur, fabrique_moteur
    ):
        photo = poser_photo(source, PHOTO_A)
        moteur = fabrique_moteur(source)
        moteur._un_tour()

        vieux = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        for entree in moteur._memoire.entrees.values():
            entree.envoyee_le = vieux
        moteur._prochaine_purge = 0
        serveur.stors.clear()
        moteur._un_tour()

        assert photo.exists()
        assert moteur.etat().envoyees == 1
        assert serveur.stors == []  # purgé ≠ oublié : surtout pas de renvoi

    def test_purge_desactivee_a_zero(self, source, fabrique_moteur, racine_isolee):
        poser_photo(source, PHOTO_A)
        moteur = fabrique_moteur(source, purge_apres_heures=0)
        moteur._un_tour()

        vieux = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
        for entree in moteur._memoire.entrees.values():
            entree.envoyee_le = vieux
        moteur._prochaine_purge = 0
        moteur._un_tour()

        tampon = racine_isolee / "donnees" / "tampon" / "2026-08-02GRANDPRIX"
        assert (tampon / "DUPONT MARIE_ECLAIR" / "a.jpg").exists()

    def test_un_tampon_efface_a_la_main_est_reconstitue(
        self, source, serveur, fabrique_moteur, racine_isolee
    ):
        """Sinon la photo resterait en attente pour toujours, sans jamais partir."""
        maudite = "2026-08-02GRANDPRIX/DUPONT MARIE_ECLAIR/a.jpg"
        serveur.echecs_pour[maudite] = 99
        poser_photo(source, PHOTO_A)
        moteur = fabrique_moteur(source, essais_max=1)
        moteur._un_tour()

        tampon = racine_isolee / "donnees" / "tampon" / "2026-08-02GRANDPRIX"
        (tampon / "DUPONT MARIE_ECLAIR" / "a.jpg").unlink()

        serveur.echecs_pour[maudite] = 0
        moteur._pause_fichier.clear()
        moteur._un_tour()

        assert maudite in serveur.fichiers


class TestSourceInjoignable:
    def test_reseau_coupe_ne_fait_pas_planter(self, source, fabrique_moteur):
        poser_photo(source, PHOTO_A)
        moteur = fabrique_moteur(source)
        moteur._un_tour()

        for fichier in source.rglob("*.jpg"):
            fichier.unlink()
        for dossier in sorted(source.rglob("*"), reverse=True):
            dossier.rmdir()
        source.rmdir()

        assert moteur._un_tour() == 5.0
        assert "introuvable" in moteur.etat().note


class TestFragmentsInterrompus:
    """ProFTPD écrit dans `.in.<nom>.` puis renomme. Un envoi coupé laisse ce
    fragment, et le serveur refuse ensuite la photo en 550 — pour toujours."""

    def test_sans_nettoyage_la_photo_serait_perdue(self, source, serveur, fabrique_moteur):
        """Le scénario vu en production : 3 essais, 3 refus, et ça ne guérit jamais."""
        rel = "2026-08-02GRANDPRIX/DUPONT MARIE_ECLAIR/a.jpg"
        serveur.hidden_stores = True
        serveur.echecs_pour[rel] = 1        # la coupure qui crée le fragment
        poser_photo(source, PHOTO_A)

        moteur = fabrique_moteur(source)
        moteur._un_tour()

        assert rel in serveur.fichiers
        assert serveur.supprimes == [serveur.fragment_de(rel)]
        assert not serveur.fragments      # le serveur est laissé propre

    def test_un_fragment_deja_present_est_nettoye(self, source, serveur, fabrique_moteur):
        """Le cas du redémarrage : le fragment date d'une session précédente."""
        rel = "2026-08-02GRANDPRIX/DUPONT MARIE_ECLAIR/a.jpg"
        serveur.fragments.add(serveur.fragment_de(rel))
        poser_photo(source, PHOTO_A)

        moteur = fabrique_moteur(source)
        moteur._un_tour()

        assert rel in serveur.fichiers
        assert moteur.etat().en_attente == 0

    def test_les_autres_photos_ne_sont_pas_ralenties(self, source, serveur, fabrique_moteur):
        rel_a = "2026-08-02GRANDPRIX/DUPONT MARIE_ECLAIR/a.jpg"
        serveur.fragments.add(serveur.fragment_de(rel_a))
        poser_photo(source, PHOTO_A)
        poser_photo(source, PHOTO_B)
        poser_photo(source, PHOTO_C)

        fabrique_moteur(source)._un_tour()

        assert len(deposes(serveur)) == 3
        assert len(serveur.supprimes) == 1   # on n'efface que ce qui bloquait

    def test_aucune_photo_nest_jamais_supprimee(self, source, serveur, fabrique_moteur):
        """La règle du brief tient : seuls les fragments sont effaçables."""
        rel = "2026-08-02GRANDPRIX/DUPONT MARIE_ECLAIR/a.jpg"
        serveur.fragments.add(serveur.fragment_de(rel))
        poser_photo(source, PHOTO_A)

        fabrique_moteur(source)._un_tour()

        assert all(
            PurePosixPath(chemin).name.startswith(".in.")
            for chemin in serveur.supprimes
        )


class TestLiaisonInstable:
    """Starlink, 4G en limite de couverture : coupures brèves et répétées.

    Le piège est que TOUTES les photos échouent en même temps. Une mise à
    l'écart forfaitaire de 4 minutes viderait la file et figerait l'outil,
    alors que la coupure n'a duré que quelques secondes.
    """

    def test_la_premiere_reprise_est_rapide(self, source, fabrique_moteur):
        moteur = fabrique_moteur(source)
        assert moteur._mise_en_attente(1) == 15

    def test_linsistance_espace_les_reprises(self, source, fabrique_moteur):
        moteur = fabrique_moteur(source)
        attentes = [moteur._mise_en_attente(n) for n in (1, 2, 3, 4, 10)]
        assert attentes == [15, 60, 240, 240, 240]
        assert attentes == sorted(attentes)   # jamais décroissant

    def test_le_plafond_reglable_est_respecte(self, source, fabrique_moteur):
        moteur = fabrique_moteur(source, pause_apres_echec=30)
        assert moteur._mise_en_attente(3) == 30

    def test_une_coupure_breve_ne_coute_que_quinze_secondes(
        self, source, serveur, fabrique_moteur
    ):
        """Avant : 4 minutes d'arrêt complet pour une microcoupure."""
        distant = "2026-08-02GRANDPRIX/DUPONT MARIE_ECLAIR/a.jpg"
        interne = "DUPONT MARIE_ECLAIR/a.jpg"   # le moteur indexe sans l'événement
        serveur.echecs_pour[distant] = 99
        poser_photo(source, PHOTO_A)

        moteur = fabrique_moteur(source, essais_max=1)
        moteur._un_tour()

        restant = moteur._pause_fichier[interne] - time.monotonic()
        assert 10 < restant <= 15

    def test_un_succes_efface_lhistorique_des_echecs(
        self, source, serveur, fabrique_moteur
    ):
        """La liaison est revenue : la photo suivante ne doit pas hériter des
        pénalités accumulées pendant la coupure."""
        distant = "2026-08-02GRANDPRIX/DUPONT MARIE_ECLAIR/a.jpg"
        interne = "DUPONT MARIE_ECLAIR/a.jpg"
        serveur.echecs_pour[distant] = 99
        poser_photo(source, PHOTO_A)
        moteur = fabrique_moteur(source, essais_max=1)

        moteur._un_tour()
        moteur._pause_fichier.clear()
        moteur._un_tour()
        assert moteur._echecs_fichier[interne] == 2   # ça s'aggrave tant que ça rate

        serveur.echecs_pour[distant] = 0
        moteur._pause_fichier.clear()
        moteur._un_tour()

        assert distant in serveur.fichiers
        assert interne not in moteur._echecs_fichier
        assert moteur.etat().erreurs == 0

    def test_les_tentatives_immediates_sont_espacees_progressivement(
        self, source, fabrique_moteur, monkeypatch
    ):
        # On lève la neutralisation posée par la fixture pour lire les vrais délais.
        monkeypatch.setattr(module_moteur, "DELAIS_ENTRE_ESSAIS", (3.0, 10.0, 20.0))
        moteur = fabrique_moteur(source)
        attentes = [moteur._attente_entre_essais(n) for n in (1, 2, 3, 9)]
        assert attentes == [3.0, 10.0, 20.0, 20.0]

    def test_les_timeouts_sont_transmis_au_client(self, source, fabrique_moteur):
        """Ouvrir doit rester rapide ; un transfert doit pouvoir survivre à une
        coupure de plusieurs dizaines de secondes."""
        moteur = fabrique_moteur(source, timeout_connexion=20, timeout_donnees=300)
        client = moteur._client_ftps("secret")
        assert (client.timeout, client.timeout_donnees) == (20, 300)


class TestCoupureNeGelePasLaFile:
    """Le défaut vu en production sur Starlink : une photo touchée par une
    coupure monopolisait un ouvrier pendant tout le délai de transfert. Avec
    deux ouvriers, chaque coupure supprimait la moitié du débit ; le journal
    montrait 2 min 36 sans le moindre envoi, alors que le lien tenait 85
    photos/min entre deux coupures."""

    def test_une_coupure_ne_consomme_quun_seul_essai(
        self, source, serveur, fabrique_moteur
    ):
        distant = "2026-08-02GRANDPRIX/DUPONT MARIE_ECLAIR/a.jpg"
        serveur.pannes_de_lien[distant] = 99
        poser_photo(source, PHOTO_A)

        moteur = fabrique_moteur(source, essais_max=3)
        moteur._un_tour()

        # Sans ça : 3 essais, donc trois fois le délai de transfert à attendre.
        assert serveur.stors.count(distant) == 1

    def test_un_refus_du_serveur_reste_reessaye(self, source, serveur, fabrique_moteur):
        """La distinction doit rester fine : un 550 mérite toujours ses reprises."""
        distant = "2026-08-02GRANDPRIX/DUPONT MARIE_ECLAIR/a.jpg"
        serveur.echecs_pour[distant] = 99
        poser_photo(source, PHOTO_A)

        moteur = fabrique_moteur(source, essais_max=3)
        moteur._un_tour()

        assert serveur.stors.count(distant) == 3

    def test_la_file_continue_pendant_la_coupure(self, source, serveur, fabrique_moteur):
        coupee = "2026-08-02GRANDPRIX/DUPONT MARIE_ECLAIR/a.jpg"
        serveur.pannes_de_lien[coupee] = 99
        poser_photo(source, PHOTO_A)
        poser_photo(source, PHOTO_B)
        poser_photo(source, PHOTO_C)

        moteur = fabrique_moteur(source)
        moteur._un_tour()

        assert len(deposes(serveur)) == 2
        assert coupee not in serveur.fichiers

    def test_la_photo_repart_des_que_le_lien_revient(
        self, source, serveur, fabrique_moteur
    ):
        """Écartée, jamais abandonnée."""
        distant = "2026-08-02GRANDPRIX/DUPONT MARIE_ECLAIR/a.jpg"
        interne = "DUPONT MARIE_ECLAIR/a.jpg"
        serveur.pannes_de_lien[distant] = 1
        poser_photo(source, PHOTO_A)

        moteur = fabrique_moteur(source, essais_max=1)
        moteur._un_tour()
        assert moteur.etat().en_attente == 1

        moteur._pause_fichier.clear()   # les 15 s se sont écoulées
        moteur._un_tour()
        assert distant in serveur.fichiers

    def test_on_ne_dialogue_pas_avec_un_serveur_mort(
        self, source, serveur, fabrique_moteur
    ):
        """Un QUIT poli sur un lien coupé attend une réponse qui ne viendra pas."""
        distant = "2026-08-02GRANDPRIX/DUPONT MARIE_ECLAIR/a.jpg"
        serveur.echecs_pour[distant] = 99
        poser_photo(source, PHOTO_A)

        moteur = fabrique_moteur(source, essais_max=3)
        moteur._un_tour()

        # Les reconnexions entre tentatives ferment sans attendre le serveur.
        assert serveur.fermetures_brutales >= 2
