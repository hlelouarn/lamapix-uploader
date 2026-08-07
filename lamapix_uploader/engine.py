"""Moteur d'envoi : scan → tampon → FTPS, avec mémoire, reprises et cooldowns.

Il tourne dans son propre thread et ne connaît rien de l'interface : celle-ci
lui pousse des commandes et lit `etat()`. Fermer la fenêtre n'arrête donc rien.

Le tampon local est structuré EXACTEMENT comme le FTP : c'est le plan B du brief
(glisser son contenu dans FileZilla doit donner le même résultat).
"""

from __future__ import annotations

import shutil
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Callable

from . import paths
from .config import Config
from .ftp import ClientFtps, ErreurFtp, ErreurIdentifiants
from .journal import Journal
from .mapping import chemin_distant, rendre_unique
from .memory import MemoireEvenement
from .scanner import PhotoTrouvee, lister_evenements, scanner

PAUSE_ENTRE_ESSAIS = 3.0
INTERVALLE_PURGE_MINUTES = 10


@dataclass
class Etat:
    """Photo instantanée pour l'interface. Immuable, lisible sans verrou."""

    evenement: str | None = None
    source: str | None = None
    evenements_disponibles: list[str] = field(default_factory=list)
    detectees: int = 0
    envoyees: int = 0
    en_attente: int = 0
    erreurs: int = 0
    derniere_erreur: str = ""
    en_cours: str = ""
    note: str = ""
    en_pause: bool = False
    debit_par_minute: int = 0
    dernier_envoi: str = ""
    journal: list[str] = field(default_factory=list)
    identifiants_refuses: bool = False

    @property
    def total_connu(self) -> int:
        return self.envoyees + self.en_attente

    @property
    def pourcentage(self) -> int:
        total = self.total_connu
        return round(100 * self.envoyees / total) if total else 0


class Moteur:
    """Orchestre tout le pipeline. Une instance = un événement surveillé à la fois."""

    def __init__(
        self,
        config: Config,
        journal: Journal,
        fournisseur_mot_de_passe: Callable[[], str | None],
        fabrique_client: Callable[[str], ClientFtps] | None = None,
    ) -> None:
        self.config = config
        self.journal = journal
        self._mot_de_passe = fournisseur_mot_de_passe
        # Injectable : les tests rejouent les pannes Lamapix sans toucher au vrai serveur.
        self._fabrique_client = fabrique_client or self._client_ftps

        self._verrou = threading.RLock()
        self._arret = threading.Event()
        self._reveil = threading.Event()
        self._thread: threading.Thread | None = None

        self._memoire: MemoireEvenement | None = None
        self._dossier_source: Path | None = None
        self._dossier_tampon: Path | None = None

        self._en_pause = False
        self._detectees = 0
        self._erreurs: dict[str, str] = {}
        self._derniere_erreur = ""
        self._en_cours = ""
        self._note = ""
        self._identifiants_refuses = False
        self._pause_fichier: dict[str, float] = {}
        self._pause_dossier: dict[str, float] = {}
        self._echecs_dossier: dict[str, int] = {}
        self._envois_recents: deque[float] = deque()
        self._dernier_envoi: datetime | None = None
        self._prochaine_purge = 0.0
        self._evenements_disponibles: list[str] = []
        self._prochaine_liste = 0.0
        self._epreuves_signalees: set[str] = set()
        self._a_reprendre: str | None = None

    # ================================================================ cycle de vie

    def demarrer(self) -> None:
        """Démarre le moteur sans jamais toucher au disque depuis l'appelant.

        La reprise de l'événement précédent se fait DANS le thread : un partage
        réseau éteint fait attendre le timeout SMB (plusieurs dizaines de
        secondes), et l'interface doit rester utilisable pendant ce temps.
        """
        if self._thread is not None:
            return
        self._a_reprendre = self.config.dossier_source
        self._thread = threading.Thread(target=self._boucle, name="moteur", daemon=True)
        self._thread.start()

    def arreter(self, delai: float = 5.0) -> None:
        self._arret.set()
        self._reveil.set()
        if self._thread is not None:
            self._thread.join(timeout=delai)
            self._thread = None
        with self._verrou:
            if self._memoire is not None:
                self._memoire.sauver()

    # =================================================================== commandes

    def choisir_evenement(self, saisie: str) -> None:
        try:
            _, source = self.config.resoudre_source(saisie)
        except ValueError as exc:
            self._noter_erreur(str(exc))
            return
        self._ouvrir_evenement(source, memoriser=True)
        self._reveil.set()

    def basculer_pause(self) -> bool:
        with self._verrou:
            self._en_pause = not self._en_pause
            etat = self._en_pause
        self.journal.ecrire("Pause des envois" if etat else "Reprise des envois")
        self._reveil.set()
        return etat

    def initialiser_memoire(self) -> int:
        """Marque tout l'existant comme déjà envoyé, SANS rien envoyer."""
        with self._verrou:
            memoire = self._memoire
            source = self._dossier_source
        if memoire is None or source is None:
            return 0

        photos = []
        rels = dict(memoire.rels_utilises)
        for photo in self._scanner(source):
            rel = chemin_distant(photo.relatif)
            if rel is None:
                continue
            rel = rendre_unique(rel, str(photo.chemin), rels)
            rels[rel] = str(photo.chemin)
            photos.append((str(photo.chemin), rel, photo.taille))

        with self._verrou:
            nombre = memoire.marquer_tout_envoye(photos)
        self.journal.ecrire(
            f"Initialisation : {nombre} photo(s) existante(s) marquée(s) comme déjà "
            "envoyée(s) — rien n'a été envoyé"
        )
        return nombre

    def reinitialiser_memoire(self) -> None:
        """Efface la mémoire : tout l'événement sera renvoyé."""
        with self._verrou:
            if self._memoire is None:
                return
            self._memoire.effacer()
            self._erreurs.clear()
            self._pause_fichier.clear()
            self._pause_dossier.clear()
            self._echecs_dossier.clear()
        self.journal.ecrire("Mémoire effacée : renvoi complet de l'événement")
        self._reveil.set()

    def recharger_config(self) -> None:
        """Après l'écran de réglages : les nouveaux paramètres prennent au tour suivant."""
        self._identifiants_refuses = False
        self._reveil.set()

    # ======================================================================= état

    def etat(self) -> Etat:
        with self._verrou:
            memoire = self._memoire
            envoyees = memoire.nombre_envoyees if memoire else 0
            en_attente = memoire.nombre_en_attente if memoire else 0
            self._elaguer_debit()
            return Etat(
                evenement=self.config.evenement,
                source=str(self._dossier_source) if self._dossier_source else None,
                evenements_disponibles=list(self._evenements_disponibles),
                detectees=self._detectees,
                envoyees=envoyees,
                en_attente=en_attente,
                erreurs=len(self._erreurs),
                derniere_erreur=self._derniere_erreur,
                en_cours=self._en_cours,
                note=self._note,
                en_pause=self._en_pause,
                debit_par_minute=len(self._envois_recents),
                dernier_envoi=(
                    self._dernier_envoi.strftime("%H:%M:%S") if self._dernier_envoi else ""
                ),
                journal=self.journal.dernieres(),
                identifiants_refuses=self._identifiants_refuses,
            )

    # =============================================================== boucle privée

    def _boucle(self) -> None:
        if self._a_reprendre:
            self._noter("Reprise de l'événement précédent…")
            self._ouvrir_evenement(self._a_reprendre, memoriser=False)
            self._a_reprendre = None
        while not self._arret.is_set():
            try:
                attente = self._un_tour()
            except Exception as exc:  # un imprévu ne doit jamais tuer le moteur
                self.journal.erreur(f"Incident interne : {exc}")
                self._noter_erreur(str(exc))
                attente = 5.0
            self._attendre_avec_decompte(attente)

    def _un_tour(self) -> float:
        """Un cycle complet. Retourne le nombre de secondes à attendre ensuite.

        C'est le point d'entrée testable du moteur : il ne dort jamais lui-même.
        """
        with self._verrou:
            source = self._dossier_source
            memoire = self._memoire

        if source is None or memoire is None:
            self._noter("Choisissez un dossier d'événement à surveiller.")
            self._rafraichir_evenements()
            return 2.0

        if not source.exists():
            self._noter(f"Dossier introuvable (réseau coupé ?) : {source}")
            return 5.0

        self._noter("Scan du dossier…")
        self._preparer_tampon(source, memoire)
        self._purger_tampon(memoire)

        if self._en_pause:
            self._noter("EN PAUSE — les envois sont suspendus, le scan continue.")
        else:
            self._envoyer_la_file(memoire)

        # En dernier : ce n'est qu'un confort d'affichage, et lire un partage
        # réseau éteint peut coûter très cher en temps.
        self._rafraichir_evenements()
        return float(self.config.intervalle_scan)

    # ------------------------------------------------------- 1. scan + tampon

    def _scanner(self, source: Path) -> list[PhotoTrouvee]:
        return scanner(
            source,
            extensions=self.config.extensions_tuple,
            delai_stabilite=self.config.delai_stabilite,
        )

    def _preparer_tampon(self, source: Path, memoire: MemoireEvenement) -> None:
        """Copie les nouveautés dans le tampon, structuré comme le FTP."""
        photos = self._scanner(source)
        with self._verrou:
            self._detectees = len(photos)
        tampon = self._dossier_tampon
        if tampon is None:
            return

        modifiee = False
        for photo in photos:
            cle = str(photo.chemin)
            with self._verrou:
                entree = memoire.entrees.get(cle)
            deja_a_jour = entree is not None and entree.taille == photo.taille

            # Cas nominal : connue, inchangée, et son tampon est bien là.
            if deja_a_jour and (entree.envoyee or (tampon / entree.rel).exists()):
                continue

            if entree is not None:
                rel = entree.rel  # une photo retouchée garde son nom distant
            else:
                calcule = chemin_distant(photo.relatif)
                if calcule is None:
                    self._signaler_photo_non_rangeable(photo)
                    continue
                with self._verrou:
                    rel = rendre_unique(calcule, cle, memoire.rels_utilises)

            cible = tampon / rel
            try:
                cible.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(photo.chemin, cible)
            except OSError as exc:
                self.journal.erreur(f"copie vers le tampon — {rel} : {exc}")
                continue

            with self._verrou:
                memoire.enregistrer(cle, rel, photo.taille)
            modifiee = True

        if modifiee:
            with self._verrou:
                memoire.sauver()

    def _signaler_photo_non_rangeable(self, photo: PhotoTrouvee) -> None:
        """Photo dans une épreuve sans dossier cavalier : on prévient une fois."""
        epreuve = Path(photo.relatif).parts[0]
        if epreuve in self._epreuves_signalees:
            return
        self._epreuves_signalees.add(epreuve)
        self.journal.ecrire(f"Ignoré : photo sans dossier cavalier dans « {epreuve} »")

    # --------------------------------------------------------------- 2. purge

    def _purger_tampon(self, memoire: MemoireEvenement) -> None:
        """Supprime du TAMPON les photos envoyées il y a longtemps.

        Jamais la source, jamais le FTP, et jamais la mémoire — c'est elle qui
        garantit qu'on ne renverra pas ces photos.
        """
        heures = self.config.purge_apres_heures
        tampon = self._dossier_tampon
        if heures <= 0 or tampon is None:
            return
        if time.monotonic() < self._prochaine_purge:
            return
        self._prochaine_purge = time.monotonic() + INTERVALLE_PURGE_MINUTES * 60

        limite = datetime.now(timezone.utc) - timedelta(hours=heures)
        supprimees = 0
        with self._verrou:
            entrees = list(memoire.entrees.values())
        for entree in entrees:
            if not entree.envoyee or not entree.envoyee_le:
                continue
            try:
                envoyee_le = datetime.fromisoformat(entree.envoyee_le)
            except ValueError:
                continue
            if envoyee_le.tzinfo is None:
                envoyee_le = envoyee_le.replace(tzinfo=timezone.utc)
            if envoyee_le >= limite:
                continue
            fichier = tampon / entree.rel
            try:
                if fichier.exists():
                    fichier.unlink()
                    supprimees += 1
            except OSError:
                continue

        if supprimees:
            self._nettoyer_dossiers_vides(tampon)
            self.journal.ecrire(
                f"Purge du tampon : {supprimees} photo(s) nettoyée(s) (mémoire conservée)"
            )

    @staticmethod
    def _nettoyer_dossiers_vides(racine: Path) -> None:
        try:
            dossiers = sorted(
                (d for d in racine.rglob("*") if d.is_dir()),
                key=lambda d: len(d.parts),
                reverse=True,
            )
        except OSError:
            return
        for dossier in dossiers:
            try:
                next(dossier.iterdir())
            except StopIteration:
                try:
                    dossier.rmdir()
                except OSError:
                    pass
            except OSError:
                pass

    # ---------------------------------------------------------------- 3. envoi

    def _envoyer_la_file(self, memoire: MemoireEvenement) -> None:
        mot_de_passe = self._mot_de_passe()
        if not mot_de_passe:
            self._noter("Mot de passe Lamapix non renseigné — ouvrez les réglages.")
            return

        with self._verrou:
            file = deque(memoire.en_attente())
        if not file:
            self._noter("À jour : toutes les photos détectées sont parties.")
            return

        self._noter(f"Envoi en cours — {len(file)} photo(s) en attente…")
        echeance = time.monotonic() + self.config.rescan_max
        verrou_file = threading.Lock()

        def prochaine() -> str | None:
            """Sert la file en sautant ce qui est en cooldown (sans jamais bloquer)."""
            with verrou_file:
                maintenant = time.monotonic()
                reportees: list[str] = []
                choisie = None
                while file:
                    source = file.popleft()
                    with self._verrou:
                        entree = memoire.entrees.get(source)
                    if entree is None or entree.envoyee:
                        continue
                    if self._en_cooldown(entree.rel, maintenant):
                        reportees.append(source)
                        continue
                    choisie = source
                    break
                file.extend(reportees)  # réexaminées au prochain tour de boucle
                return choisie

        nombre = max(1, min(3, self.config.connexions_paralleles))
        travailleurs = [
            threading.Thread(
                target=self._travailleur,
                args=(memoire, prochaine, mot_de_passe, echeance),
                name=f"envoi-{index + 1}",
                daemon=True,
            )
            for index in range(nombre)
        ]
        for travailleur in travailleurs:
            travailleur.start()
        for travailleur in travailleurs:
            travailleur.join()

        with self._verrou:
            memoire.sauver()
            self._en_cours = ""
            restantes = memoire.nombre_en_attente
        self._noter(
            "À jour : toutes les photos détectées sont parties."
            if not restantes
            else f"{restantes} photo(s) encore en attente (reprise au prochain scan)."
        )

    def _travailleur(
        self,
        memoire: MemoireEvenement,
        prochaine: Callable[[], str | None],
        mot_de_passe: str,
        echeance: float,
    ) -> None:
        """Un thread = une connexion FTPS, gardée ouverte tant que ça passe."""
        client = self._fabrique_client(mot_de_passe)
        try:
            while not self._arret.is_set() and not self._en_pause:
                if time.monotonic() > echeance:
                    break  # on recoupe pour re-scanner : les nouveautés n'attendent pas
                source = prochaine()
                if source is None:
                    break
                if not self._envoyer_une(client, memoire, source):
                    if self._identifiants_refuses:
                        break
        finally:
            client.fermer()

    def _client_ftps(self, mot_de_passe: str) -> ClientFtps:
        return ClientFtps(
            hote=self.config.ftp_hote,
            port=self.config.ftp_port,
            utilisateur=self.config.ftp_utilisateur,
            mot_de_passe=mot_de_passe,
            racine=self.config.evenement or "",
            ignorer_certificat=self.config.ignorer_certificat,
        )

    def _envoyer_une(
        self, client: ClientFtps, memoire: MemoireEvenement, source: str
    ) -> bool:
        with self._verrou:
            entree = memoire.entrees.get(source)
        if entree is None or entree.envoyee:
            return True

        rel = entree.rel
        tampon = self._dossier_tampon
        if tampon is None:
            return False
        fichier = tampon / rel
        if not fichier.exists():
            # Tampon disparu : on le reconstituera au prochain scan.
            return True

        parent = str(PurePosixPath(rel).parent)
        parent = "" if parent == "." else parent

        with self._verrou:
            self._en_cours = rel

        derniere: str = ""
        for essai in range(1, self.config.essais_max + 1):
            try:
                if essai > 1:
                    # Connexion neuve : Lamapix a pu consommer les dossiers, et une
                    # session keep-alive peut être dans un état bancal.
                    client.fermer()
                    client.invalider_cache(parent)
                client.envoyer(fichier, rel)
                self._noter_succes(memoire, source, rel)
                return True
            except ErreurIdentifiants as exc:
                derniere = str(exc)
                self._identifiants_refuses = True
                self.journal.erreur(f"identifiants refusés par Lamapix : {exc}")
                self._noter_erreur("Identifiants refusés (530) — vérifiez le mot de passe.")
                return False
            except ErreurFtp as exc:
                derniere = str(exc)
                self.journal.ecrire(f"Essai {essai}/{self.config.essais_max} — {rel} : {exc}")
                if essai < self.config.essais_max and not self._arret.is_set():
                    time.sleep(PAUSE_ENTRE_ESSAIS)

        self._noter_echec(rel, parent, derniere)
        return False

    # ------------------------------------------------------------- cooldowns

    def _en_cooldown(self, rel: str, maintenant: float) -> bool:
        parent = str(PurePosixPath(rel).parent)
        parent = "" if parent == "." else parent
        with self._verrou:
            if self._pause_dossier.get(parent, 0.0) > maintenant:
                return True
            return self._pause_fichier.get(rel, 0.0) > maintenant

    def _noter_succes(self, memoire: MemoireEvenement, source: str, rel: str) -> None:
        maintenant = datetime.now()
        parent = str(PurePosixPath(rel).parent)
        parent = "" if parent == "." else parent
        with self._verrou:
            memoire.marquer_envoyee(source)
            memoire.sauver_si_necessaire()
            self._erreurs.pop(rel, None)
            self._pause_fichier.pop(rel, None)
            self._echecs_dossier[parent] = 0
            self._dernier_envoi = maintenant
            self._envois_recents.append(time.monotonic())
            self._elaguer_debit()
        self.journal.succes(rel)

    def _noter_echec(self, rel: str, parent: str, message: str) -> None:
        """Un fichier en erreur ne doit JAMAIS bloquer les autres : on le met de
        côté quelques minutes et la file continue."""
        attente = self.config.pause_apres_echec
        with self._verrou:
            self._erreurs[rel] = message
            self._derniere_erreur = f"{rel} : {message}"
            self._pause_fichier[rel] = time.monotonic() + attente
            self._echecs_dossier[parent] = self._echecs_dossier.get(parent, 0) + 1
            trop = self._echecs_dossier[parent] >= self.config.echecs_avant_pause_dossier
            if trop:
                self._pause_dossier[parent] = time.monotonic() + attente
                self._echecs_dossier[parent] = 0
        self.journal.erreur(f"{rel} : {message}")
        if trop:
            self.journal.ecrire(
                f"Dossier « {parent or '(racine)'} » mis en attente "
                f"{attente // 60} min après échecs répétés"
            )

    def _elaguer_debit(self) -> None:
        limite = time.monotonic() - 60
        while self._envois_recents and self._envois_recents[0] < limite:
            self._envois_recents.popleft()

    # ---------------------------------------------------------------- divers

    def _ouvrir_evenement(self, source_saisie: str, memoriser: bool) -> None:
        try:
            nom, source = self.config.resoudre_source(source_saisie)
        except ValueError as exc:
            self._noter_erreur(str(exc))
            return
        chemin = Path(source)
        if not chemin.exists():
            self._noter_erreur(f"Dossier introuvable : {source}")
            return

        with self._verrou:
            if self._memoire is not None:
                self._memoire.sauver()

            self.config.evenement = nom
            self.config.dossier_source = source
            tampon = paths.racine_tampon() / nom
            tampon.mkdir(parents=True, exist_ok=True)

            self._dossier_source = chemin
            self._dossier_tampon = tampon
            self._memoire = MemoireEvenement.charger(tampon / "_memoire.json")
            self._erreurs.clear()
            self._pause_fichier.clear()
            self._pause_dossier.clear()
            self._echecs_dossier.clear()
            self._epreuves_signalees.clear()
            self._detectees = 0
            self._derniere_erreur = ""
            connues = len(self._memoire.entrees)

        self.journal.rediriger(paths.racine_journaux() / f"{nom}.txt")
        self.journal.ecrire(
            f"Événement surveillé : {nom} | dossier : {source} "
            f"| mémoire : {connues} photo(s) connue(s)"
        )
        if memoriser:
            self.config.sauver()

    def _rafraichir_evenements(self) -> None:
        if time.monotonic() < self._prochaine_liste:
            return
        self._prochaine_liste = time.monotonic() + 30
        liste = lister_evenements(Path(self.config.base_redim))
        with self._verrou:
            self._evenements_disponibles = liste

    def _noter(self, texte: str) -> None:
        with self._verrou:
            self._note = texte

    def _noter_erreur(self, texte: str) -> None:
        with self._verrou:
            self._derniere_erreur = texte

    def _attendre_avec_decompte(self, secondes: float) -> None:
        """Attente interruptible : une commande de l'interface réveille aussitôt."""
        restant = secondes
        while restant > 0 and not self._arret.is_set():
            if not self._en_pause and secondes >= 5:
                self._noter(f"Prochain scan dans {int(restant)} s…")
            pas = min(2.0, restant)
            if self._reveil.wait(timeout=pas):
                self._reveil.clear()
                return
            restant -= pas
