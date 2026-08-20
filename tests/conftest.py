"""Outillage commun : faux serveur Lamapix et moteur isolé du disque réel."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path, PurePosixPath

import pytest

from lamapix_uploader import paths
from lamapix_uploader.config import Config
from lamapix_uploader.engine import Moteur
from lamapix_uploader.ftp import (
    ErreurFragmentBloquant,
    ErreurFtp,
    ErreurIdentifiants,
    ErreurLiaison,
)
from lamapix_uploader.journal import Journal


class FauxLamapix:
    """Serveur Lamapix simulé, avec ses comportements pénibles du §6 du brief.

    - il « aspire » les dossiers déposés (`consommer`), donc un STOR peut échouer
      en 550 alors qu'on venait de créer le dossier ;
    - on peut programmer des pannes ciblées (`echecs_pour`) pour vérifier les
      reprises, les cooldowns, et que la file ne se bloque jamais.
    """

    def __init__(self) -> None:
        self.verrou = threading.Lock()
        self.dossiers: set[str] = set()
        self.fichiers: dict[str, bytes] = {}
        self.echecs_pour: dict[str, int] = {}   # rel -> nombre d'échecs restants
        self.identifiants_invalides = False
        self.stors: list[str] = []
        self.connexions = 0
        # HiddenStores de ProFTPD : un envoi coupé laisse un `.in.<nom>.` qui
        # bloque ensuite la photo en 550, pour toujours.
        self.hidden_stores = False
        self.fragments: set[str] = set()
        self.supprimes: list[str] = []
        # Coupures de lien (Starlink, 4G faible) : rel -> pannes restantes.
        self.pannes_de_lien: dict[str, int] = {}
        # Panne du LIEN tout entier : les N prochains envois échouent, quelle
        # que soit la photo — c'est le vrai visage d'une coupure Starlink.
        self.panne_globale = 0
        self.fermetures_polies = 0
        self.fermetures_brutales = 0

    @staticmethod
    def fragment_de(chemin_absolu: str) -> str:
        cible = PurePosixPath(chemin_absolu)
        return f"{cible.parent}/.in.{cible.name}."

    def consommer(self, prefixe: str = "") -> None:
        """Ce que fait Lamapix : il ingère et fait disparaître ce qui est déposé."""
        with self.verrou:
            self.dossiers = {d for d in self.dossiers if not d.startswith(prefixe)}
            self.fichiers = {f: v for f, v in self.fichiers.items() if not f.startswith(prefixe)}


class ClientFauxLamapix:
    """Même interface publique que ClientFtps, branché sur FauxLamapix."""

    def __init__(self, serveur: FauxLamapix, racine: str) -> None:
        self.serveur = serveur
        self.racine = racine
        self._connecte = False
        self._dossiers_crees: set[str] = set()

    # -- connexion

    def _connecter(self) -> None:
        if self._connecte:
            return
        if self.serveur.identifiants_invalides:
            raise ErreurIdentifiants("530 Login incorrect")
        with self.serveur.verrou:
            self.serveur.connexions += 1
        self._connecte = True

    def fermer(self, poli: bool = True) -> None:
        # On compte tous les appels : le vrai client garde son objet FTP même
        # après une panne, et c'est précisément là que le QUIT poli coûterait
        # cher. Se caler sur `_connecte` masquerait le cas qu'on veut vérifier.
        with self.serveur.verrou:
            if poli:
                self.serveur.fermetures_polies += 1
            else:
                self.serveur.fermetures_brutales += 1
        self._connecte = False
        self._dossiers_crees.clear()

    # -- opérations

    def _absolu(self, rel: str) -> str:
        return str(PurePosixPath(self.racine, rel)) if rel else self.racine

    def assurer_dossier(self, rel_dossier: str) -> None:
        self._connecter()
        niveaux = [""]
        if rel_dossier:
            cumul = PurePosixPath()
            for segment in PurePosixPath(rel_dossier).parts:
                cumul = cumul / segment
                niveaux.append(str(cumul))
        for niveau in niveaux:
            if niveau in self._dossiers_crees:
                continue
            with self.serveur.verrou:
                self.serveur.dossiers.add(self._absolu(niveau))
            self._dossiers_crees.add(niveau)

    def invalider_cache(self, rel_dossier: str = "") -> None:
        self._dossiers_crees.discard("")
        if not rel_dossier:
            return
        cumul = PurePosixPath()
        for segment in PurePosixPath(rel_dossier).parts:
            cumul = cumul / segment
            self._dossiers_crees.discard(str(cumul))

    def envoyer(self, fichier_local: Path, rel_distant: str) -> None:
        dossier = str(PurePosixPath(rel_distant).parent)
        dossier = "" if dossier == "." else dossier
        self.assurer_dossier(dossier)

        # Tout est indexé en chemin absolu côté serveur (racine événement comprise) :
        # c'est ce que les tests observent, et ce que Lamapix voit réellement.
        absolu = self._absolu(rel_distant)
        fragment = self.serveur.fragment_de(absolu)
        with self.serveur.verrou:
            self.serveur.stors.append(absolu)

            # Le fragment d'un envoi précédent bloque celui-ci, comme en vrai.
            if fragment in self.serveur.fragments:
                raise ErreurFragmentBloquant(
                    f"550 {absolu} : un fichier caché temporaire "
                    f"« {fragment} » existe déjà",
                    fragment,
                )

            if self.serveur.panne_globale > 0:
                self.serveur.panne_globale -= 1
                self._connecte = False
                raise ErreurLiaison(
                    "[WinError 10054] Une connexion existante a dû être fermée "
                    "par l'hôte distant"
                )

            pannes = self.serveur.pannes_de_lien.get(absolu, 0)
            if pannes > 0:
                self.serveur.pannes_de_lien[absolu] = pannes - 1
                self._connecte = False
                raise ErreurLiaison(
                    "[WinError 10054] Une connexion existante a dû être fermée "
                    "par l'hôte distant"
                )

            restants = self.serveur.echecs_pour.get(absolu, 0)
            if restants > 0:
                self.serveur.echecs_pour[absolu] = restants - 1
                self._connecte = False  # le serveur a coupé : comme en vrai
                if self.serveur.hidden_stores:
                    # Transfert interrompu : le fichier caché reste derrière.
                    self.serveur.fragments.add(fragment)
                raise ErreurFtp("550 Requested action not taken")
            if self._absolu(dossier) not in self.serveur.dossiers:
                raise ErreurFtp("550 Directory not found")
            self.serveur.fichiers[absolu] = fichier_local.read_bytes()
            self.serveur.fragments.discard(fragment)   # renommé par le serveur

    def supprimer_fragment(self, chemin: str) -> None:
        """Le vrai client refuse tout ce qui n'est pas un fragment : on rejoue
        ce garde-fou ici, pour qu'un test le vérifie réellement."""
        nom = PurePosixPath(chemin).name
        if not chemin.startswith(f"{self.racine}/") or not nom.startswith("."):
            raise ErreurFtp(f"refus de supprimer « {chemin} »")
        self._connecter()
        with self.serveur.verrou:
            if chemin not in self.serveur.fragments:
                raise ErreurFtp("550 fichier introuvable")
            self.serveur.fragments.discard(chemin)
            self.serveur.supprimes.append(chemin)


@pytest.fixture
def racine_isolee(tmp_path, monkeypatch):
    """Redirige tout ce que l'outil écrit (config, tampon, journaux) vers tmp_path."""
    application = tmp_path / "application"
    application.mkdir()
    monkeypatch.setattr(paths, "racine_application", lambda: application)
    return application


@pytest.fixture
def serveur():
    return FauxLamapix()


@pytest.fixture
def fabrique_moteur(racine_isolee, serveur):
    """Construit un moteur prêt à tourner, branché sur le faux serveur."""

    def construire(dossier_source: Path, **reglages) -> Moteur:
        config = Config(
            base_redim=str(dossier_source.parent),
            delai_stabilite=0,
            intervalle_scan=1,
            essais_max=reglages.pop("essais_max", 3),
            connexions_paralleles=reglages.pop("connexions_paralleles", 1),
            purge_apres_heures=reglages.pop("purge_apres_heures", 24),
        )
        for cle, valeur in reglages.items():
            setattr(config, cle, valeur)

        moteur = Moteur(
            config=config,
            journal=Journal(),
            fournisseur_mot_de_passe=lambda: "secret",
            fabrique_client=lambda _mdp: ClientFauxLamapix(
                serveur, config.evenement or ""
            ),
        )
        moteur.choisir_evenement(str(dossier_source))
        return moteur

    return construire


def poser_photo(racine: Path, chemin_relatif: str, contenu: bytes = b"jpeg") -> Path:
    """Crée une photo source, antidatée pour passer le contrôle de stabilité."""
    fichier = racine / chemin_relatif
    fichier.parent.mkdir(parents=True, exist_ok=True)
    fichier.write_bytes(contenu)
    quand = time.time() - 3600
    os.utime(fichier, (quand, quand))
    return fichier
