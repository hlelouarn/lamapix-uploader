"""Point d'entrée : un seul geste pour démarrer (§8 du brief).

Séquence : instance unique → config → mot de passe → moteur → fenêtre.
Le moteur démarre même si la connexion Lamapix est mauvaise : les photos
s'empilent dans le tampon et la mémoire, et partent dès que ça repasse.
"""

from __future__ import annotations

import sys
import threading

from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from . import NOM_APPLICATION, VERSION, paths, secrets_win, updater
from .config import Config
from .engine import Moteur
from .journal import Journal
from .ui import theme
from .ui.icone import charger as charger_icone
from .ui.main_window import FenetrePrincipale
from .ui.settings_dialog import DialogueReglages

CLE_INSTANCE = "lamapix-uploader-instance-unique"


def _deja_lance() -> bool:
    """Un second exemplaire écraserait la mémoire du premier : on l'empêche."""
    sonde = QLocalSocket()
    sonde.connectToServer(CLE_INSTANCE)
    if sonde.waitForConnected(300):
        sonde.disconnectFromServer()
        return True
    QLocalServer.removeServer(CLE_INSTANCE)  # reliquat d'un arrêt brutal
    serveur = QLocalServer()
    serveur.listen(CLE_INSTANCE)
    _garder_en_vie.append(serveur)
    return False


_garder_en_vie: list[object] = []


def _verifier_mises_a_jour(config: Config, fenetre: FenetrePrincipale) -> None:
    """Vérification silencieuse en tâche de fond : jamais bloquante."""

    def travail() -> None:
        trouvee = updater.chercher(config.depot_mises_a_jour)
        if trouvee is None or not trouvee.plus_recente or not paths.est_gele():
            return
        fenetre.moteur.journal.ecrire(
            f"Mise à jour disponible : version {trouvee.version}"
        )

    threading.Thread(target=travail, name="maj", daemon=True).start()


def principal(arguments: list[str] | None = None) -> int:
    application = QApplication(arguments if arguments is not None else sys.argv)
    application.setApplicationName(NOM_APPLICATION)
    application.setApplicationVersion(VERSION)
    application.setQuitOnLastWindowClosed(False)  # la zone de notification prend le relais
    application.setStyleSheet(theme.FEUILLE_DE_STYLE)

    icone = charger_icone()
    application.setWindowIcon(icone)

    if _deja_lance():
        QMessageBox.information(
            None,
            NOM_APPLICATION,
            f"{NOM_APPLICATION} tourne déjà sur ce PC.\n\n"
            "Cliquez sur son icône près de l'horloge pour ouvrir la fenêtre.",
        )
        return 0

    config = Config.charger()
    journal = Journal(paths.racine_journaux() / "demarrage.txt")
    journal.ecrire(f"{NOM_APPLICATION} {VERSION} — démarrage")

    # Premier lancement sur ce PC : ni identifiant ni mot de passe Lamapix.
    # (Rien de tout ça n'est livré dans l'exe : le dépôt est public.)
    if not config.ftp_utilisateur or secrets_win.lire(paths.fichier_secret()) is None:
        QMessageBox.information(
            None,
            NOM_APPLICATION,
            "Premier lancement sur ce PC.\n\n"
            "Renseignez le dossier des événements Kadra, puis l'identifiant et le "
            "mot de passe du compte Lamapix.\n\n"
            "Le mot de passe est mémorisé chiffré, uniquement sur cette machine. "
            "Les autres réglages se recopient d'un PC à l'autre avec le fichier "
            "donnees\\config.json.",
        )
        if not DialogueReglages(config).exec():
            journal.ecrire("Abandon : compte Lamapix non renseigné")
            return 0
        config = Config.charger()

    moteur = Moteur(
        config=config,
        journal=journal,
        fournisseur_mot_de_passe=lambda: secrets_win.lire(paths.fichier_secret()),
    )
    moteur.demarrer()

    fenetre = FenetrePrincipale(config=config, moteur=moteur, icone=icone)
    fenetre.show()

    if config.verifier_mises_a_jour:
        _verifier_mises_a_jour(config, fenetre)

    application.aboutToQuit.connect(moteur.arreter)
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(principal())
