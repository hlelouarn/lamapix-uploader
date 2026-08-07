"""Icône de l'application.

On charge `assets/lamapix.ico` s'il est là ; sinon on la dessine. L'outil ne doit
jamais refuser de démarrer parce qu'un fichier d'icône manque.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPixmap

from .. import paths
from . import theme

NOM_FICHIER = "lamapix.ico"


def charger() -> QIcon:
    fichier = paths.dossier_ressources() / NOM_FICHIER
    if fichier.exists():
        icone = QIcon(str(fichier))
        if not icone.isNull():
            return icone
    return QIcon(dessiner(256))


def dessiner(taille: int) -> QPixmap:
    """Pastille bleue avec une flèche montante : « ça part vers Lamapix »."""
    pixmap = QPixmap(taille, taille)
    pixmap.fill(Qt.GlobalColor.transparent)

    peintre = QPainter(pixmap)
    peintre.setRenderHint(QPainter.RenderHint.Antialiasing)
    peintre.setPen(Qt.PenStyle.NoPen)
    peintre.setBrush(QBrush(QColor(theme.FOND)))
    peintre.drawRoundedRect(
        QRectF(0, 0, taille, taille), taille * 0.22, taille * 0.22
    )

    peintre.setBrush(QBrush(QColor(theme.BLEU)))
    police = QFont("Segoe UI Symbol")
    police.setPixelSize(int(taille * 0.62))
    police.setBold(True)
    peintre.setFont(police)
    peintre.setPen(QColor(theme.BLEU))
    peintre.drawText(
        QRectF(0, 0, taille, taille), Qt.AlignmentFlag.AlignCenter, "↑"
    )
    peintre.end()
    return pixmap
