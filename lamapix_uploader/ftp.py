"""Client FTPS explicite pour Lamapix (§6 du brief — pièges appris en production).

Deux particularités qui dictent tout le reste :

1. Lamapix « aspire » ce qu'on dépose. Un dossier créé il y a cinq minutes peut
   avoir disparu. On ne met donc JAMAIS en cache « ce dossier existe » de façon
   définitive : sur échec, on invalide et on recrée l'arborescence.
2. La racine de l'événement n'existe pas tant que personne ne l'a créée (Kadra
   n'uploade plus) — sans MKD explicite on récolte des erreurs 550.

On ne supprime jamais une photo ni un dossier côté serveur. Seule exception,
documentée plus bas : les fragments `.in.` laissés par nos propres envois
interrompus, qui bloqueraient définitivement la photo concernée.
"""

from __future__ import annotations

import ftplib
import re
import ssl
from pathlib import Path, PurePosixPath

TAILLE_BLOC = 65536
TIMEOUT_PAR_DEFAUT = 30


class ErreurFtp(Exception):
    """Échec d'une opération FTP, avec le message serveur d'origine."""


class ErreurIdentifiants(ErreurFtp):
    """Login refusé (530) — il faut redemander le mot de passe."""


class ErreurFragmentBloquant(ErreurFtp):
    """Un envoi interrompu a laissé un fichier caché qui bloque tous les suivants.

    Lamapix tourne sous ProFTPD avec `HiddenStores` : un dépôt s'écrit d'abord
    dans `.in.<nom>.` puis est renommé une fois complet. Si la connexion tombe en
    plein transfert, ce fragment reste — et le serveur refuse ensuite l'envoi de
    la photo en 550, indéfiniment. Aucune reprise ne peut aboutir tant qu'il est
    là : c'est le seul cas où l'outil efface quelque chose sur le serveur.
    """

    def __init__(self, message: str, fragment: str) -> None:
        super().__init__(message)
        self.fragment = fragment


# Le serveur cite le chemin du fragment entre guillemets, dans sa propre langue.
_MOTIF_CHEMIN_CITE = re.compile(r"""[«"'`]\s*(/[^«»"'`]+?)\s*[»"'`]""")
_INDICES_EXISTE_DEJA = ("existe déjà", "already exists")
_INDICES_FRAGMENT = (".in.", "caché", "cache", "hidden", "temporaire", "temporary")


def _contexte_ssl(ignorer_certificat: bool) -> ssl.SSLContext:
    if ignorer_certificat:
        return ssl._create_unverified_context()
    return ssl.create_default_context()


class ClientFtps:
    """Une connexion FTPS explicite (AUTH TLS, port 21, passif, binaire).

    Un client = une connexion. Les envois parallèles utilisent plusieurs clients :
    on ne partage jamais une connexion entre threads.
    """

    def __init__(
        self,
        hote: str,
        port: int,
        utilisateur: str,
        mot_de_passe: str,
        racine: str,
        ignorer_certificat: bool = False,
        timeout: int = TIMEOUT_PAR_DEFAUT,
    ) -> None:
        self.hote = hote
        self.port = port
        self.utilisateur = utilisateur
        self._mot_de_passe = mot_de_passe
        self.racine = racine.strip("/")
        self.ignorer_certificat = ignorer_certificat
        self.timeout = timeout
        self._ftp: ftplib.FTP_TLS | None = None
        self._dossiers_crees: set[str] = set()

    # ------------------------------------------------------------ connexion

    def connecter(self) -> None:
        if self._ftp is not None:
            return
        ftp = ftplib.FTP_TLS(context=_contexte_ssl(self.ignorer_certificat))
        ftp.encoding = "utf-8"
        try:
            ftp.connect(self.hote, self.port, timeout=self.timeout)
            ftp.auth()                    # AUTH TLS : chiffrement explicite
            ftp.login(self.utilisateur, self._mot_de_passe)
            ftp.prot_p()                  # canal de données chiffré aussi
            ftp.set_pasv(True)
        except ftplib.error_perm as exc:
            self._fermer_silencieusement(ftp)
            if str(exc).startswith("530"):
                raise ErreurIdentifiants(str(exc)) from exc
            raise ErreurFtp(str(exc)) from exc
        except ftplib.all_errors as exc:  # type: ignore[misc]
            # all_errors couvre déjà OSError (réseau, DNS, timeout) et EOFError.
            self._fermer_silencieusement(ftp)
            raise ErreurFtp(str(exc)) from exc
        self._ftp = ftp

    def fermer(self) -> None:
        if self._ftp is not None:
            self._fermer_silencieusement(self._ftp)
            self._ftp = None
        self._dossiers_crees.clear()

    @staticmethod
    def _fermer_silencieusement(ftp: ftplib.FTP_TLS) -> None:
        try:
            ftp.quit()
        except Exception:
            try:
                ftp.close()
            except Exception:
                pass

    def __enter__(self) -> "ClientFtps":
        self.connecter()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.fermer()

    # ----------------------------------------------------------- opérations

    def tester(self) -> None:
        """Connexion + login + un listing : valide les identifiants au démarrage."""
        self.connecter()
        assert self._ftp is not None
        try:
            self._ftp.voidcmd("NOOP")
        except ftplib.all_errors as exc:  # type: ignore[misc]
            raise ErreurFtp(str(exc)) from exc

    def _chemin_absolu(self, rel: str) -> str:
        return "/" + str(PurePosixPath(self.racine, rel)) if rel else "/" + self.racine

    def assurer_dossier(self, rel_dossier: str) -> None:
        """Crée l'arborescence distante niveau par niveau, racine événement comprise.

        Un MKD sur un dossier existant renvoie une erreur : c'est le cas normal,
        on l'ignore. Ce qui compte, c'est qu'après l'appel le dossier existe.
        """
        self.connecter()
        assert self._ftp is not None

        niveaux: list[str] = [""]
        if rel_dossier:
            cumul = PurePosixPath()
            for segment in PurePosixPath(rel_dossier).parts:
                cumul = cumul / segment
                niveaux.append(str(cumul))

        for niveau in niveaux:
            if niveau in self._dossiers_crees:
                continue
            try:
                self._ftp.mkd(self._chemin_absolu(niveau))
            except ftplib.error_perm:
                pass  # existe déjà : le cas nominal
            except ftplib.all_errors as exc:  # type: ignore[misc]
                raise ErreurFtp(str(exc)) from exc
            self._dossiers_crees.add(niveau)

    def invalider_cache(self, rel_dossier: str = "") -> None:
        """Oublie ce qu'on croyait savoir : Lamapix a peut-être consommé les dossiers."""
        self._dossiers_crees.discard("")
        if not rel_dossier:
            return
        cumul = PurePosixPath()
        for segment in PurePosixPath(rel_dossier).parts:
            cumul = cumul / segment
            self._dossiers_crees.discard(str(cumul))

    def envoyer(self, fichier_local: Path, rel_distant: str) -> None:
        """Dépose une photo, en créant son dossier au besoin."""
        dossier = str(PurePosixPath(rel_distant).parent)
        if dossier == ".":
            dossier = ""
        self.assurer_dossier(dossier)
        assert self._ftp is not None
        # Ouverture à part : un tampon illisible n'est pas une panne serveur, et le
        # message doit le dire (all_errors avale les OSError sans les distinguer).
        try:
            flux = fichier_local.open("rb")
        except OSError as exc:
            raise ErreurFtp(f"lecture du fichier local : {exc}") from exc
        try:
            with flux:
                self._ftp.storbinary(
                    f"STOR {self._chemin_absolu(rel_distant)}", flux, TAILLE_BLOC
                )
        except ftplib.error_perm as exc:
            # error_perm avant all_errors : c'est une sous-classe.
            fragment = self._fragment_bloquant(str(exc), rel_distant)
            if fragment:
                raise ErreurFragmentBloquant(str(exc), fragment) from exc
            raise ErreurFtp(str(exc)) from exc
        except ftplib.all_errors as exc:  # type: ignore[misc]
            raise ErreurFtp(str(exc)) from exc

    # ------------------------------------------- fragments d'envois interrompus

    def _fragment_bloquant(self, message: str, rel_distant: str) -> str | None:
        """Chemin du fichier caché qui bloque cet envoi, ou None si autre chose.

        On n'accepte le chemin dicté par le serveur qu'après vérification : il
        doit être sous la racine de l'événement, porter un nom caché, et contenir
        le nom de la photo qu'on est en train d'envoyer. Une réponse serveur est
        une donnée, pas un ordre — surtout quand elle débouche sur un DELE.
        """
        minuscules = message.lower()
        if "550" not in message:
            return None
        if not any(indice in minuscules for indice in _INDICES_EXISTE_DEJA):
            return None
        if not any(indice in minuscules for indice in _INDICES_FRAGMENT):
            return None

        cible = PurePosixPath(self._chemin_absolu(rel_distant))
        prefixe = f"/{self.racine}/"

        for candidat in _MOTIF_CHEMIN_CITE.findall(message):
            candidat = candidat.strip()
            nom = PurePosixPath(candidat).name
            if not candidat.startswith(prefixe):
                continue
            if not nom.startswith(".") or cible.name not in nom:
                continue
            return candidat

        # Le serveur n'a rien cité d'exploitable : on retombe sur le format par
        # défaut de ProFTPD (`HiddenStores` = `.in.<nom>.`).
        return f"{cible.parent}/.in.{cible.name}."

    def supprimer_fragment(self, chemin: str) -> None:
        """Efface un fragment d'envoi interrompu. LA SEULE suppression distante.

        Ce n'est jamais une photo : c'est le résidu d'un de nos propres envois
        coupés en route, que le serveur refuse ensuite d'écraser. Le garde-fou
        est revérifié ici, indépendamment de l'appelant.
        """
        nom = PurePosixPath(chemin).name
        if not chemin.startswith(f"/{self.racine}/") or not nom.startswith("."):
            raise ErreurFtp(
                f"refus de supprimer « {chemin} » : ce n'est pas un fragment d'envoi."
            )
        self.connecter()
        assert self._ftp is not None
        try:
            self._ftp.delete(chemin)
        except ftplib.all_errors as exc:  # type: ignore[misc]
            raise ErreurFtp(str(exc)) from exc
