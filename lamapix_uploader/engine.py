"""Moteur d'envoi : scan → tampon → FTPS, avec mémoire, reprises et cooldowns.

Il tourne dans son propre thread et ne connaît rien de l'interface : celle-ci
lui pousse des commandes et lit `etat()`. Fermer la fenêtre n'arrête donc rien.

Depuis la v2.0, le moteur surveille une LISTE d'événements (0, 1 ou plusieurs) :
un seul pool de connexions et un seul disjoncteur pour tous — la ressource rare
est le lien, pas les événements — et une alternance équitable entre eux dans la
file d'envoi. Mémoire, tampon et boutons Initialiser/Réinitialiser restent par
événement. Tout dossier hors de la liste n'existe pas pour l'outil.

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
from .ftp import (
    ClientFtps,
    ErreurFragmentBloquant,
    ErreurFtp,
    ErreurIdentifiants,
    ErreurLiaison,
)
from .journal import Diagnostic, Journal
from .mapping import chemin_distant, rendre_unique
from .memory import MemoireEvenement
from .scanner import PhotoTrouvee, lister_evenements, scanner

# Reprises progressives. Un lien instable (satellite, 4G en limite de couverture)
# produit des coupures brèves : réessayer plus tard, mais pas toutes les 3 s, et
# surtout pas en mettant d'emblée la photo de côté pour 4 minutes.
DELAIS_ENTRE_ESSAIS = (3.0, 10.0, 20.0)
DELAIS_REPRISE = (15, 60, 240)
INTERVALLE_PURGE_MINUTES = 10

# Disjoncteur de liaison. Quand le LIEN tombe (Starlink qui change de satellite,
# 4G qui décroche), toutes les photos échoueraient une par une : au lieu de ça,
# après SEUIL pannes consécutives on cesse de consommer la file, une seule sonde
# réessaie à intervalles courts, et le premier succès rouvre tout.
SEUIL_LIAISON_COUPEE = 2
DELAIS_SONDE = (2.0, 5.0, 10.0, 20.0, 30.0)
# Une photo qui provoque N pannes de lien à elle seule est traitée en échec
# normal : sans ce garde-fou, une photo pathologique monopoliserait la sonde.
MAX_LIAISONS_PAR_PHOTO = 5


@dataclass
class ContexteEvenement:
    """Tout ce qui appartient en propre à un événement surveillé."""

    nom: str
    source: Path
    tampon: Path
    memoire: MemoireEvenement


@dataclass
class Etat:
    """Photo instantanée pour l'interface. Immuable, lisible sans verrou."""

    evenement: str | None = None            # nom si UN SEUL événement surveillé
    source: str | None = None               # idem
    surveilles: list[str] = field(default_factory=list)
    # (nom, détectées, envoyées, en attente) — dans l'ordre d'ajout
    par_evenement: list[tuple[str, int, int, int]] = field(default_factory=list)
    evenements_disponibles: list[str] = field(default_factory=list)
    detectees: int = 0
    envoyees: int = 0
    en_attente: int = 0
    erreurs: int = 0
    initialisees: int = 0   # déclarées envoyées, jamais réellement envoyées
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
    """Orchestre tout le pipeline pour l'ensemble des événements surveillés."""

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

        # nom -> contexte, dans l'ordre d'ajout (l'alternance suit cet ordre).
        self._contextes: dict[str, ContexteEvenement] = {}

        self._en_pause = False
        self._detectees: dict[str, int] = {}
        # Toutes les tables ci-dessous sont indexées par le chemin AFFICHÉ
        # (« ÉVÉNEMENT/CAVALIER/photo.jpg ») : unique entre événements, et c'est
        # aussi le chemin réellement parlé au serveur depuis la v2.0.
        self._erreurs: dict[str, str] = {}
        self._derniere_erreur = ""
        self._en_cours = ""
        self._note = ""
        self._identifiants_refuses = False
        self._pause_fichier: dict[str, float] = {}
        self._pause_dossier: dict[str, float] = {}
        self._echecs_dossier: dict[str, int] = {}
        self._echecs_fichier: dict[str, int] = {}
        self._echecs_dossier_suite: dict[str, int] = {}
        self._liaisons_fichier: dict[str, int] = {}
        self._pannes_liaison = 0        # consécutives, tous événements confondus
        self._prochaine_sonde = 0.0
        self._sonde_prise = False
        self._envois_recents: deque[float] = deque()
        self._dernier_envoi: datetime | None = None
        self._prochaine_purge = 0.0
        self._evenements_disponibles: list[str] = []
        self._prochaine_liste = 0.0
        self._epreuves_signalees: set[str] = set()
        self._a_reprendre: list[str] = []
        self._dernier_scan: dict[str, list[PhotoTrouvee]] = {}
        self._dernier_scan_a = float("-inf")   # monotonic du dernier scan réel
        self.diagnostic = Diagnostic()

    # ------------------------------------------------------- compat & accès ctx

    @property
    def _memoire(self) -> MemoireEvenement | None:
        """La mémoire quand UN SEUL événement est surveillé (tests, dialogues)."""
        with self._verrou:
            if len(self._contextes) == 1:
                return next(iter(self._contextes.values())).memoire
        return None

    def _contexte(self, nom: str | None) -> ContexteEvenement | None:
        """Le contexte demandé — ou l'unique surveillé quand `nom` est None."""
        with self._verrou:
            if nom is not None:
                return self._contextes.get(nom)
            if len(self._contextes) == 1:
                return next(iter(self._contextes.values()))
        return None

    @staticmethod
    def _afficher(nom: str, rel: str) -> str:
        """Chemin unique inter-événements — et chemin distant réel (racine FTP =
        la racine du compte, l'événement est le premier dossier)."""
        return f"{nom}/{rel}"

    # ================================================================ cycle de vie

    def demarrer(self) -> None:
        """Démarre le moteur sans jamais toucher au disque depuis l'appelant.

        La reprise des événements précédents se fait DANS le thread : un partage
        réseau éteint fait attendre le timeout SMB (plusieurs dizaines de
        secondes), et l'interface doit rester utilisable pendant ce temps.
        """
        if self._thread is not None:
            return
        self._a_reprendre = list(self.config.dossiers_surveilles)
        self._thread = threading.Thread(target=self._boucle, name="moteur", daemon=True)
        self._thread.start()

    def arreter(self, delai: float = 5.0) -> None:
        self._arret.set()
        self._reveil.set()
        if self._thread is not None:
            self._thread.join(timeout=delai)
            self._thread = None
        with self._verrou:
            for ctx in self._contextes.values():
                ctx.memoire.sauver()

    # =================================================================== commandes

    def ajouter_evenement(self, saisie: str) -> None:
        """Ajoute un événement à la liste de surveillance."""
        self._ouvrir_evenement(saisie, memoriser=True)
        self._reveil.set()

    def retirer_evenement(self, nom: str) -> None:
        """Retire un événement de la surveillance. RIEN n'est effacé : mémoire et
        tampon restent sur disque, le re-surveiller reprend où il en était."""
        with self._verrou:
            ctx = self._contextes.pop(nom, None)
            if ctx is None:
                return
            ctx.memoire.sauver()
            self._detectees.pop(nom, None)
            self._dernier_scan.pop(nom, None)
            prefixe = f"{nom}/"
            for table in (
                self._erreurs,
                self._pause_fichier,
                self._pause_dossier,
                self._echecs_dossier,
                self._echecs_fichier,
                self._echecs_dossier_suite,
                self._liaisons_fichier,
            ):
                for cle in [c for c in table if c.startswith(prefixe)]:
                    del table[cle]
        self.config.dossiers_surveilles = [
            c for c in self.config.dossiers_surveilles
            if Path(c).name != nom
        ]
        self.config.sauver()
        self.journal.ecrire(f"Événement retiré de la surveillance : {nom} (mémoire conservée)")
        self._reveil.set()

    def choisir_evenement(self, saisie: str) -> None:
        """Remplace TOUTE la surveillance par cet unique événement.

        C'est l'ancien geste mono-événement ; l'interface v2 passe par
        ajouter/retirer, mais ce raccourci reste le bon outil quand on veut
        « juste surveiller ça »."""
        with self._verrou:
            noms = list(self._contextes)
        for nom in noms:
            self.retirer_evenement(nom)
        self.ajouter_evenement(saisie)

    def basculer_pause(self) -> bool:
        with self._verrou:
            self._en_pause = not self._en_pause
            etat = self._en_pause
        self.journal.ecrire("Pause des envois" if etat else "Reprise des envois")
        self._reveil.set()
        return etat

    def photos_connues(self, nom: str | None = None) -> list[PhotoTrouvee]:
        """Le dernier scan d'un événement. Sert aux aperçus de l'interface sans
        relancer une lecture disque (coûteuse sur un partage réseau)."""
        ctx = self._contexte(nom)
        if ctx is None:
            return []
        with self._verrou:
            return list(self._dernier_scan.get(ctx.nom, []))

    def apercu_initialisation(
        self, avant: float | None = None, nom: str | None = None
    ) -> tuple[int, int]:
        """(nombre concerné, nombre total présent) pour une frontière donnée.

        `avant` est un timestamp : seules les photos modifiées avant lui seraient
        marquées. None = toutes.
        """
        photos = self.photos_connues(nom)
        if avant is None:
            return len(photos), len(photos)
        return sum(1 for p in photos if p.modifie_le < avant), len(photos)

    def initialiser_memoire(
        self, avant: float | None = None, nom: str | None = None
    ) -> int:
        """Déclare l'existant d'UN événement comme déjà envoyé, SANS rien envoyer.

        Ce n'est pas une constatation : Lamapix aspirant ce qu'on y dépose, aucune
        vérification n'est possible côté serveur. C'est le pari « Kadra avait fini
        d'uploader ». `avant` permet de poser la frontière à l'heure où Kadra s'est
        réellement arrêté plutôt que d'avaler tout le dossier ; et
        `annuler_initialisation()` permet d'en revenir.
        """
        ctx = self._contexte(nom)
        if ctx is None:
            return 0

        # On repart du dernier scan quand il existe : l'aperçu montré à
        # l'utilisateur et ce qu'on marque portent alors sur exactement le
        # même ensemble de photos.
        photos_sources = self.photos_connues(ctx.nom) or self._scanner(ctx.source)

        photos = []
        rels = dict(ctx.memoire.rels_utilises)
        for photo in photos_sources:
            if avant is not None and photo.modifie_le >= avant:
                continue
            rel = chemin_distant(photo.relatif)
            if rel is None:
                continue
            rel = rendre_unique(rel, str(photo.chemin), rels)
            rels[rel] = str(photo.chemin)
            photos.append((str(photo.chemin), rel, photo.taille))

        with self._verrou:
            nombre = ctx.memoire.marquer_tout_envoye(photos)
        frontiere = (
            "tout le dossier"
            if avant is None
            else f"photos antérieures au {datetime.fromtimestamp(avant):%d/%m/%Y %H:%M}"
        )
        self.journal.ecrire(
            f"Initialisation de {ctx.nom} ({frontiere}) : {nombre} photo(s) "
            "déclarée(s) déjà envoyée(s) — rien n'a été envoyé, geste annulable"
        )
        return nombre

    def annuler_initialisation(self, nom: str | None = None) -> int:
        """Renvoie dans la file ce qu'une initialisation avait mis de côté."""
        ctx = self._contexte(nom)
        if ctx is None:
            return 0
        with self._verrou:
            nombre = ctx.memoire.annuler_initialisation()
        if nombre:
            self.journal.ecrire(
                f"Initialisation de {ctx.nom} annulée : {nombre} photo(s) "
                "remise(s) en file d'attente"
            )
            self._reveil.set()
        return nombre

    def reinitialiser_memoire(self, nom: str | None = None) -> None:
        """Efface la mémoire d'UN événement : il sera intégralement renvoyé."""
        ctx = self._contexte(nom)
        if ctx is None:
            return
        prefixe = f"{ctx.nom}/"
        with self._verrou:
            ctx.memoire.effacer()
            for table in (self._erreurs, self._pause_fichier, self._pause_dossier,
                          self._echecs_dossier, self._echecs_fichier):
                for cle in [c for c in table if c.startswith(prefixe)]:
                    del table[cle]
        self.journal.ecrire(f"Mémoire de {ctx.nom} effacée : renvoi complet de l'événement")
        self._reveil.set()

    def recharger_config(self) -> None:
        """Après l'écran de réglages : les nouveaux paramètres prennent au tour suivant."""
        self._identifiants_refuses = False
        self._reveil.set()

    # ======================================================================= état

    def etat(self) -> Etat:
        with self._verrou:
            contextes = list(self._contextes.values())
            par_evenement = [
                (
                    ctx.nom,
                    self._detectees.get(ctx.nom, 0),
                    ctx.memoire.nombre_envoyees,
                    ctx.memoire.nombre_en_attente,
                )
                for ctx in contextes
            ]
            self._elaguer_debit()
            unique = contextes[0] if len(contextes) == 1 else None
            return Etat(
                evenement=unique.nom if unique else None,
                source=str(unique.source) if unique else None,
                surveilles=[ctx.nom for ctx in contextes],
                par_evenement=par_evenement,
                evenements_disponibles=list(self._evenements_disponibles),
                detectees=sum(n for _, n, _, _ in par_evenement),
                envoyees=sum(n for _, _, n, _ in par_evenement),
                en_attente=sum(n for _, _, _, n in par_evenement),
                erreurs=len(self._erreurs),
                initialisees=sum(
                    ctx.memoire.nombre_initialisees for ctx in contextes
                ),
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
        for source in self._a_reprendre:
            self._noter("Reprise des événements surveillés…")
            self._ouvrir_evenement(source, memoriser=False)
        self._a_reprendre = []
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
            contextes = list(self._contextes.values())

        if not contextes:
            self._noter("Ajoutez un dossier d'événement à surveiller.")
            self._rafraichir_evenements()
            return 2.0

        # Un partage réseau éteint sur UN événement ne doit pas priver les
        # autres : on continue avec ce qui est joignable.
        joignables = []
        for ctx in contextes:
            if ctx.source.exists():
                joignables.append(ctx)
            else:
                self._noter(f"Dossier introuvable (réseau coupé ?) : {ctx.source}")
        if not joignables:
            return 5.0

        debut_tour = time.monotonic()

        # Tant qu'il reste un arriéré à envoyer, on ne re-scanne qu'une fois par
        # intervalle : sinon chaque fenêtre d'envoi se termine par un scan qui
        # suspend les transferts, et l'utilisateur voit l'outil « s'arrêter pour
        # scanner » toutes les cinq minutes.
        with self._verrou:
            arriere = any(ctx.memoire.nombre_en_attente > 0 for ctx in joignables)
        scan_recent = time.monotonic() - self._dernier_scan_a < self.config.intervalle_scan
        if arriere and scan_recent:
            self.diagnostic.tracer(
                "SCAN_SAUTE",
                en_attente=sum(ctx.memoire.nombre_en_attente for ctx in joignables),
            )
        else:
            self._noter("Scan des dossiers…")
            for ctx in joignables:
                self._preparer_tampon(ctx)
            self._dernier_scan_a = time.monotonic()
        self._purger_tampons(joignables)

        if self._en_pause:
            self._noter("EN PAUSE — les envois sont suspendus, le scan continue.")
        else:
            self._envoyer_la_file(joignables)

        # En dernier : ce n'est qu'un confort d'affichage, et lire un partage
        # réseau éteint peut coûter très cher en temps.
        self._rafraichir_evenements()

        delai = self._prochain_delai(joignables)
        with self._verrou:
            restantes = sum(ctx.memoire.nombre_en_attente for ctx in joignables)
        self.diagnostic.tracer(
            "TOUR",
            duree_ms=int((time.monotonic() - debut_tour) * 1000),
            en_attente=restantes,
            prochain_s=int(delai),
        )
        return delai

    def _prochain_delai(self, contextes: list[ContexteEvenement]) -> float:
        """1 s s'il reste des photos prêtes à partir, 5 s si tout attend une
        reprise, sinon le rythme de scan normal. Les fenêtres d'envoi
        s'enchaînent au lieu d'être entrecoupées de 30 s de vide."""
        if self._en_pause:
            return float(self.config.intervalle_scan)
        with self._verrou:
            en_attente = [
                self._afficher(ctx.nom, e.rel)
                for ctx in contextes
                for e in ctx.memoire.entrees.values()
                if not e.envoyee
            ]
        if not en_attente:
            return float(self.config.intervalle_scan)
        maintenant = time.monotonic()
        if any(not self._en_cooldown(aff, maintenant) for aff in en_attente):
            return 1.0
        return 5.0

    # ------------------------------------------------------- 1. scan + tampon

    def _scanner(self, source: Path) -> list[PhotoTrouvee]:
        return scanner(
            source,
            extensions=self.config.extensions_tuple,
            delai_stabilite=self.config.delai_stabilite,
        )

    def _preparer_tampon(self, ctx: ContexteEvenement) -> None:
        """Copie les nouveautés d'un événement dans son tampon, structuré comme le FTP."""
        debut_scan = time.monotonic()
        photos = self._scanner(ctx.source)
        self.diagnostic.tracer(
            "SCAN",
            evt=ctx.nom,
            detectees=len(photos),
            duree_ms=int((time.monotonic() - debut_scan) * 1000),
        )
        with self._verrou:
            self._detectees[ctx.nom] = len(photos)
            self._dernier_scan[ctx.nom] = photos

        memoire = ctx.memoire
        modifiee = False
        for photo in photos:
            cle = str(photo.chemin)
            with self._verrou:
                entree = memoire.entrees.get(cle)
            deja_a_jour = entree is not None and entree.taille == photo.taille

            # Cas nominal : connue, inchangée, et son tampon est bien là.
            if deja_a_jour and (entree.envoyee or (ctx.tampon / entree.rel).exists()):
                continue

            if entree is not None:
                rel = entree.rel  # une photo retouchée garde son nom distant
            else:
                calcule = chemin_distant(photo.relatif)
                if calcule is None:
                    self._signaler_photo_non_rangeable(ctx.nom, photo)
                    continue
                with self._verrou:
                    rel = rendre_unique(calcule, cle, memoire.rels_utilises)

            cible = ctx.tampon / rel
            try:
                cible.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(photo.chemin, cible)
            except OSError as exc:
                self.journal.erreur(f"copie vers le tampon — {ctx.nom}/{rel} : {exc}")
                continue

            with self._verrou:
                memoire.enregistrer(cle, rel, photo.taille)
            modifiee = True

        if modifiee:
            self.diagnostic.tracer(
                "COPIE",
                evt=ctx.nom,
                duree_ms=int((time.monotonic() - debut_scan) * 1000),
            )
            with self._verrou:
                memoire.sauver()

    def _signaler_photo_non_rangeable(self, nom: str, photo: PhotoTrouvee) -> None:
        """Photo dans une épreuve sans dossier cavalier : on prévient une fois."""
        epreuve = f"{nom}:{Path(photo.relatif).parts[0]}"
        if epreuve in self._epreuves_signalees:
            return
        self._epreuves_signalees.add(epreuve)
        self.journal.ecrire(
            f"Ignoré : photo sans dossier cavalier dans « {epreuve} »"
        )

    # --------------------------------------------------------------- 2. purge

    def _purger_tampons(self, contextes: list[ContexteEvenement]) -> None:
        """Supprime des TAMPONS les photos envoyées il y a longtemps.

        Jamais la source, jamais le FTP, et jamais la mémoire — c'est elle qui
        garantit qu'on ne renverra pas ces photos.
        """
        heures = self.config.purge_apres_heures
        if heures <= 0:
            return
        if time.monotonic() < self._prochaine_purge:
            return
        self._prochaine_purge = time.monotonic() + INTERVALLE_PURGE_MINUTES * 60

        limite = datetime.now(timezone.utc) - timedelta(hours=heures)
        for ctx in contextes:
            supprimees = 0
            with self._verrou:
                entrees = list(ctx.memoire.entrees.values())
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
                fichier = ctx.tampon / entree.rel
                try:
                    if fichier.exists():
                        fichier.unlink()
                        supprimees += 1
                except OSError:
                    continue

            if supprimees:
                self._nettoyer_dossiers_vides(ctx.tampon)
                self.journal.ecrire(
                    f"Purge du tampon de {ctx.nom} : {supprimees} photo(s) "
                    "nettoyée(s) (mémoire conservée)"
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

    def _construire_file(
        self, contextes: list[ContexteEvenement]
    ) -> deque[tuple[str, str]]:
        """File (nom_evt, source) en ALTERNANCE équitable entre événements :
        une photo de l'un, une photo de l'autre — un gros arriéré n'affame
        jamais les photos fraîches de l'événement d'à côté."""
        with self._verrou:
            listes = [
                deque((ctx.nom, source) for source in ctx.memoire.en_attente())
                for ctx in contextes
            ]
        file: deque[tuple[str, str]] = deque()
        while any(listes):
            for liste in listes:
                if liste:
                    file.append(liste.popleft())
        return file

    def _envoyer_la_file(self, contextes: list[ContexteEvenement]) -> None:
        mot_de_passe = self._mot_de_passe()
        if not mot_de_passe:
            self._noter("Mot de passe Lamapix non renseigné — ouvrez les réglages.")
            return

        file = self._construire_file(contextes)
        if not file:
            self._noter("À jour : toutes les photos détectées sont parties.")
            return

        self._noter(f"Envoi en cours — {len(file)} photo(s) en attente…")
        self.diagnostic.tracer("FENETRE", file=len(file))
        echeance = time.monotonic() + self.config.rescan_max
        verrou_file = threading.Lock()
        par_nom = {ctx.nom: ctx for ctx in contextes}

        def prochaine() -> tuple[str, str] | None:
            """Sert la file. Liaison coupée : une seule sonde à la fois."""
            while not self._arret.is_set() and not self._en_pause:
                if time.monotonic() > echeance:
                    return None
                with self._verrou:
                    coupee = self._pannes_liaison >= SEUIL_LIAISON_COUPEE
                    if coupee:
                        if self._sonde_prise or time.monotonic() < self._prochaine_sonde:
                            patienter = True
                        else:
                            self._sonde_prise = True  # cet ouvrier porte la sonde
                            patienter = False
                    else:
                        patienter = False
                if patienter:
                    # Les autres ouvriers attendent le verdict de la sonde au
                    # lieu d'aller chacun mourir sur la liaison coupée.
                    time.sleep(0.25)
                    continue
                with verrou_file:
                    maintenant = time.monotonic()
                    reportees: list[tuple[str, str]] = []
                    choisie = None
                    while file:
                        nom, source = file.popleft()
                        ctx = par_nom.get(nom)
                        if ctx is None:
                            continue  # retiré de la surveillance entre-temps
                        with self._verrou:
                            entree = ctx.memoire.entrees.get(source)
                        if entree is None or entree.envoyee:
                            continue
                        if self._en_cooldown(
                            self._afficher(nom, entree.rel), maintenant
                        ):
                            reportees.append((nom, source))
                            continue
                        choisie = (nom, source)
                        break
                    file.extend(reportees)  # réexaminées au prochain appel
                if choisie is None:
                    with self._verrou:
                        self._sonde_prise = False
                    return None
                return choisie
            return None

        def remettre(element: tuple[str, str]) -> None:
            """Photo victime du lien : elle repart EN TÊTE, la sonde la retente."""
            with verrou_file:
                file.appendleft(element)

        nombre = max(1, min(3, self.config.connexions_paralleles))
        travailleurs = [
            threading.Thread(
                target=self._travailleur,
                args=(par_nom, prochaine, remettre, mot_de_passe, echeance),
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
            for ctx in contextes:
                ctx.memoire.sauver()
            self._en_cours = ""
            restantes = sum(ctx.memoire.nombre_en_attente for ctx in contextes)
        self._noter(
            "À jour : toutes les photos détectées sont parties."
            if not restantes
            else f"{restantes} photo(s) encore en attente (reprise au prochain scan)."
        )

    def _travailleur(
        self,
        par_nom: dict[str, ContexteEvenement],
        prochaine: Callable[[], tuple[str, str] | None],
        remettre: Callable[[tuple[str, str]], None],
        mot_de_passe: str,
        echeance: float,
    ) -> None:
        """Un thread = une connexion FTPS, gardée ouverte tant que ça passe."""
        client = self._fabrique_client(mot_de_passe)
        try:
            while not self._arret.is_set() and not self._en_pause:
                if time.monotonic() > echeance:
                    break  # on recoupe pour re-scanner : les nouveautés n'attendent pas
                element = prochaine()
                if element is None:
                    break
                nom, source = element
                ctx = par_nom[nom]
                statut = self._envoyer_une(client, ctx, source)
                with self._verrou:
                    self._sonde_prise = False
                if statut == "liaison":
                    remettre(element)  # rien à reprocher à la photo : elle repassera
                elif self._identifiants_refuses:
                    break
        finally:
            client.fermer()

    def _client_ftps(self, mot_de_passe: str) -> ClientFtps:
        # racine="" : les chemins distants portent l'événement en premier
        # segment, ce qui permet à un même pool de connexions de servir tous
        # les événements surveillés.
        return ClientFtps(
            hote=self.config.ftp_hote,
            port=self.config.ftp_port,
            utilisateur=self.config.ftp_utilisateur,
            mot_de_passe=mot_de_passe,
            racine="",
            ignorer_certificat=self.config.ignorer_certificat,
            timeout=self.config.timeout_connexion,
            timeout_donnees=self.config.timeout_donnees,
        )

    def _envoyer_une(
        self, client: ClientFtps, ctx: ContexteEvenement, source: str
    ) -> str:
        """Tente une photo. Retourne "ok", "echec" ou "liaison".

        "liaison" signifie : la photo n'a rien fait de mal, c'est le lien qui
        est tombé — l'appelant la remet en tête de file, sans aucune pénalité.
        """
        memoire = ctx.memoire
        with self._verrou:
            entree = memoire.entrees.get(source)
        if entree is None or entree.envoyee:
            return "ok"

        rel = entree.rel
        aff = self._afficher(ctx.nom, rel)
        fichier = ctx.tampon / rel
        if not fichier.exists():
            # Tampon disparu : on le reconstituera au prochain scan.
            return "ok"

        parent = str(PurePosixPath(aff).parent)
        parent = "" if parent == "." else parent

        with self._verrou:
            self._en_cours = aff

        try:
            octets = fichier.stat().st_size
        except OSError:
            octets = -1

        derniere: str = ""
        for essai in range(1, self.config.essais_max + 1):
            debut_essai = time.monotonic()
            try:
                if essai > 1:
                    # Connexion neuve : Lamapix a pu consommer les dossiers, et une
                    # session keep-alive peut être dans un état bancal.
                    client.fermer(poli=False)
                    client.invalider_cache(parent)
                client.envoyer(fichier, aff)
                self.diagnostic.tracer(
                    "ENVOI_OK",
                    duree_ms=int((time.monotonic() - debut_essai) * 1000),
                    octets=octets,
                    essai=essai,
                    rel=aff,
                )
                self._noter_succes(memoire, source, aff, parent)
                return "ok"
            except ErreurIdentifiants as exc:
                derniere = str(exc)
                self._identifiants_refuses = True
                self.journal.erreur(f"identifiants refusés par Lamapix : {exc}")
                self._noter_erreur("Identifiants refusés (530) — vérifiez le mot de passe.")
                return "echec"
            except ErreurFragmentBloquant as exc:
                # Sans ce nettoyage, la photo est perdue pour toujours : le serveur
                # refusera tous les envois suivants, y compris aux prochains scans.
                derniere = str(exc)
                self.journal.ecrire(
                    f"Envoi interrompu détecté sur {aff} — nettoyage du fragment "
                    f"« {exc.fragment} »"
                )
                try:
                    # `sous` réancre le garde-fou : seul un fragment DE CET
                    # événement est supprimable depuis ce contexte.
                    client.supprimer_fragment(exc.fragment, sous=ctx.nom)
                except ErreurFtp as echec:
                    self.journal.erreur(f"fragment non supprimable — {aff} : {echec}")
                if essai < self.config.essais_max and not self._arret.is_set():
                    time.sleep(self._attente_entre_essais(essai))
            except ErreurLiaison as exc:
                # Le LIEN est tombé, pas la photo. Le client a déjà jeté sa
                # session morte ; le disjoncteur prend le relais : la photo
                # repart en tête de file, aucun compteur d'échec ne bouge.
                self.diagnostic.tracer(
                    "ENVOI_LIEN",
                    duree_ms=int((time.monotonic() - debut_essai) * 1000),
                    rel=aff,
                )
                if self._noter_liaison(aff, str(exc)):
                    derniere = str(exc)
                    break        # photo pathologique : échec normal
                return "liaison"
            except ErreurFtp as exc:
                derniere = str(exc)
                self.diagnostic.tracer(
                    "ENVOI_KO",
                    duree_ms=int((time.monotonic() - debut_essai) * 1000),
                    essai=essai,
                    rel=aff,
                )
                self.journal.ecrire(f"Essai {essai}/{self.config.essais_max} — {aff} : {exc}")
                if essai < self.config.essais_max and not self._arret.is_set():
                    time.sleep(self._attente_entre_essais(essai))

        self._noter_echec(aff, parent, derniere)
        return "echec"

    # ------------------------------------------------------------- cooldowns

    def _en_cooldown(self, aff: str, maintenant: float) -> bool:
        parent = str(PurePosixPath(aff).parent)
        parent = "" if parent == "." else parent
        with self._verrou:
            if self._pause_dossier.get(parent, 0.0) > maintenant:
                return True
            return self._pause_fichier.get(aff, 0.0) > maintenant

    def _noter_succes(
        self, memoire: MemoireEvenement, source: str, aff: str, parent: str
    ) -> None:
        maintenant = datetime.now()
        with self._verrou:
            memoire.marquer_envoyee(source)
            memoire.sauver_si_necessaire()
            self._erreurs.pop(aff, None)
            self._pause_fichier.pop(aff, None)
            # Un succès efface l'historique : la liaison est revenue.
            self._echecs_fichier.pop(aff, None)
            self._echecs_dossier[parent] = 0
            self._echecs_dossier_suite.pop(parent, None)
            # La liaison répond : disjoncteur refermé, ardoise effacée.
            self._pannes_liaison = 0
            self._prochaine_sonde = 0.0
            self._liaisons_fichier.pop(aff, None)
            self._dernier_envoi = maintenant
            self._envois_recents.append(time.monotonic())
            self._elaguer_debit()
            restantes = sum(
                ctx.memoire.nombre_en_attente for ctx in self._contextes.values()
            )
            self._note = f"Envoi en cours — {restantes} photo(s) en attente…"
        self.journal.succes(aff)

    def _attente_entre_essais(self, essai: int) -> float:
        """Délai avant la tentative suivante, croissant."""
        return DELAIS_ENTRE_ESSAIS[min(essai, len(DELAIS_ENTRE_ESSAIS)) - 1]

    def _mise_en_attente(self, echecs: int) -> float:
        """Secondes de mise à l'écart, selon le nombre d'échecs consécutifs.

        Progressif et non forfaitaire : sur une liaison instable, TOUTES les
        photos échouent en même temps. Les écarter d'emblée pour 4 minutes vide
        la file et fige l'outil pendant ce temps, alors que la coupure n'a duré
        que quelques secondes. On réessaie vite d'abord, on n'insiste que si ça
        dure.
        """
        base = DELAIS_REPRISE[min(echecs, len(DELAIS_REPRISE)) - 1]
        return float(min(base, self.config.pause_apres_echec))

    def _noter_liaison(self, aff: str, message: str) -> bool:
        """Comptabilise une panne de LIEN. True si cette photo doit être
        traitée en échec normal (elle seule fait tomber la liaison).

        Aucun compteur d'échec fichier/dossier ne bouge ici : au retour du
        lien, tout doit repartir à pleine vitesse immédiatement.
        """
        with self._verrou:
            self._pannes_liaison += 1
            self._liaisons_fichier[aff] = self._liaisons_fichier.get(aff, 0) + 1
            pathologique = self._liaisons_fichier[aff] >= MAX_LIAISONS_PAR_PHOTO
            rang = max(0, self._pannes_liaison - SEUIL_LIAISON_COUPEE)
            delai = DELAIS_SONDE[min(rang, len(DELAIS_SONDE) - 1)]
            self._prochaine_sonde = time.monotonic() + delai
            self._derniere_erreur = f"{aff} : {message}"
            self._en_cours = ""
            if self._pannes_liaison >= SEUIL_LIAISON_COUPEE:
                self._note = (
                    f"Liaison interrompue — nouvelle tentative dans {int(delai)} s "
                    "(reprise automatique dès que ça répond)…"
                )
            if pathologique:
                self._liaisons_fichier.pop(aff, None)
        self.journal.ecrire(f"Liaison interrompue — {aff} : {message}")
        self.diagnostic.tracer(
            "LIAISON", pannes=self._pannes_liaison, sonde_dans_s=int(delai)
        )
        return pathologique

    def _noter_echec(self, aff: str, parent: str, message: str) -> None:
        """Un fichier en erreur ne doit JAMAIS bloquer les autres : on l'écarte
        un moment et la file continue."""
        with self._verrou:
            self._erreurs[aff] = message
            self._derniere_erreur = f"{aff} : {message}"
            self._echecs_fichier[aff] = self._echecs_fichier.get(aff, 0) + 1
            attente = self._mise_en_attente(self._echecs_fichier[aff])
            self._pause_fichier[aff] = time.monotonic() + attente

            self._echecs_dossier[parent] = self._echecs_dossier.get(parent, 0) + 1
            trop = self._echecs_dossier[parent] >= self.config.echecs_avant_pause_dossier
            attente_dossier = 0.0
            if trop:
                self._echecs_dossier_suite[parent] = (
                    self._echecs_dossier_suite.get(parent, 0) + 1
                )
                attente_dossier = self._mise_en_attente(
                    self._echecs_dossier_suite[parent]
                )
                self._pause_dossier[parent] = time.monotonic() + attente_dossier
                self._echecs_dossier[parent] = 0
        self.journal.erreur(f"{aff} : {message} (nouvel essai dans {int(attente)} s)")
        if trop:
            self.journal.ecrire(
                f"Dossier « {parent or '(racine)'} » mis en attente "
                f"{int(attente_dossier)} s après échecs répétés"
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
            if nom in self._contextes:
                return  # déjà surveillé

            tampon = paths.racine_tampon() / nom
            tampon.mkdir(parents=True, exist_ok=True)
            memoire = MemoireEvenement.charger(tampon / "_memoire.json")
            self._contextes[nom] = ContexteEvenement(
                nom=nom, source=chemin, tampon=tampon, memoire=memoire
            )
            self._detectees[nom] = 0
            self._dernier_scan[nom] = []
            self._derniere_erreur = ""
            connues = len(memoire.entrees)

        # Les événements changent en cours de route : un scan complet s'impose.
        self._dernier_scan_a = float("-inf")
        # Journal et diagnostic communs à toute la surveillance : les lignes
        # portent l'événement en tête de chemin, un seul fichier à consulter.
        self.journal.rediriger(paths.racine_journaux() / "journal.txt")
        self.diagnostic.rediriger(paths.racine_journaux() / "diagnostic.txt")
        self.journal.ecrire(
            f"Événement surveillé : {nom} | dossier : {source} "
            f"| mémoire : {connues} photo(s) connue(s)"
        )
        if memoriser:
            if source not in self.config.dossiers_surveilles:
                self.config.dossiers_surveilles.append(source)
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
