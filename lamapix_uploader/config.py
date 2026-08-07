"""Configuration de l'outil — un JSON portable à côté de l'exécutable.

Toutes les valeurs du §8 de l'ancien script (variables en tête de fichier) sont
ici, éditables depuis l'écran de réglages. Les valeurs par défaut correspondent
au parc actuel : seul le mot de passe reste à saisir sur un nouveau PC.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import paths
from .mapping import EXTENSIONS_PAR_DEFAUT


@dataclass
class Config:
    """Réglages persistants. Les défauts valent pour un PC neuf du parc."""

    # --- Source
    base_redim: str = r"C:\Kadra\redim"
    evenement: str | None = None          # nom de l'événement surveillé
    dossier_source: str | None = None     # chemin complet (local ou UNC)

    # --- Destination
    # Le dépôt est public : ni identifiant ni nom de serveur interne en dur ici.
    # Ils se saisissent au premier lancement et vivent dans le config.json local,
    # qu'on peut copier d'un PC à l'autre (cf. README). Seul le mot de passe est
    # forcément re-saisi par machine : DPAPI le rend illisible ailleurs.
    ftp_hote: str = "www.lamapix.com"
    ftp_port: int = 21
    ftp_utilisateur: str = ""
    ignorer_certificat: bool = False

    # --- Rythme et robustesse
    intervalle_scan: int = 30             # secondes entre deux scans
    delai_stabilite: int = 15             # un fichier plus récent est ignoré
    essais_max: int = 3                   # tentatives par photo
    pause_apres_echec: int = 240          # secondes de mise en attente (fichier/dossier)
    echecs_avant_pause_dossier: int = 3
    rescan_max: int = 300                 # on recoupe la file pour re-scanner
    connexions_paralleles: int = 2        # 1 = séquentiel ; 3 max raisonnable
    purge_apres_heures: int = 24          # tampon ; 0 = ne jamais purger

    # --- Divers
    extensions: list[str] = field(default_factory=lambda: list(EXTENSIONS_PAR_DEFAUT))
    demarrer_avec_windows: bool = False
    reduire_dans_zone_notification: bool = True
    verifier_mises_a_jour: bool = True
    depot_mises_a_jour: str = "hlelouarn/lamapix-uploader"

    # ------------------------------------------------------------ (dé)sérialisation

    @classmethod
    def charger(cls, fichier: Path | None = None) -> "Config":
        """Config du disque. Fichier absent ou abîmé → valeurs par défaut."""
        cible = fichier or paths.fichier_config()
        if not cible.exists():
            return cls()
        try:
            brut = json.loads(cible.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(brut, dict):
            return cls()

        connus = {f for f in cls().__dict__}
        retenus = {k: v for k, v in brut.items() if k in connus}
        try:
            return cls(**retenus)
        except TypeError:
            return cls()

    def sauver(self, fichier: Path | None = None) -> None:
        """Écriture atomique : une coupure ne laisse pas un JSON tronqué."""
        cible = fichier or paths.fichier_config()
        cible.parent.mkdir(parents=True, exist_ok=True)
        temporaire = cible.with_suffix(".tmp")
        temporaire.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporaire.replace(cible)

    # ------------------------------------------------------------------ confort

    @property
    def extensions_tuple(self) -> tuple[str, ...]:
        return tuple(e.lower() for e in self.extensions)

    def dossier_tampon_evenement(self) -> Path | None:
        if not self.evenement:
            return None
        return paths.racine_tampon() / self.evenement

    def fichier_memoire(self) -> Path | None:
        tampon = self.dossier_tampon_evenement()
        return None if tampon is None else tampon / "_memoire.json"

    def resoudre_source(self, saisie: str) -> tuple[str, str]:
        """(nom_evenement, dossier_source) depuis un nom de dossier OU un chemin complet.

        L'utilisateur peut cliquer un événement de la liste, ou coller
        `\\\\serveur\\Kadra\\redim\\MON_EVENEMENT` : les deux doivent marcher.
        """
        valeur = saisie.strip().strip('"').rstrip("\\/")
        if not valeur:
            raise ValueError("Aucun dossier indiqué.")
        chemin = Path(valeur)
        if chemin.is_absolute() or valeur.startswith("\\\\"):
            return chemin.name, str(chemin)
        return valeur, str(Path(self.base_redim) / valeur)
