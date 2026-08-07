"""Thème sombre de l'outil — reprend les couleurs de la page web de référence.

Un seul endroit pour les couleurs : l'interface est un tableau de bord qu'on
regarde de loin dans une salle de presse, la lisibilité prime sur la finesse.
"""

from __future__ import annotations

FOND = "#0f172a"
SURFACE = "#1e293b"
SURFACE_HAUTE = "#334155"
TEXTE = "#e2e8f0"
TEXTE_DOUX = "#94a3b8"

BLEU = "#7dd3fc"
VERT = "#4ade80"
JAUNE = "#facc15"
ROUGE = "#f87171"
ORANGE = "#f59e0b"

FEUILLE_DE_STYLE = f"""
QWidget {{
    background: {FOND};
    color: {TEXTE};
    font-family: "Segoe UI", Arial, sans-serif;
    font-size: 13px;
}}
QLabel#titre {{
    color: {BLEU};
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 1px;
}}
QLabel#evenement {{ color: {JAUNE}; font-size: 15px; font-weight: 600; }}
QLabel#source    {{ color: {TEXTE_DOUX}; font-size: 11px; }}
QLabel#note      {{ color: {TEXTE_DOUX}; font-size: 12px; }}
QLabel#erreur    {{ color: {ROUGE}; font-size: 12px; }}
QLabel#badgePause {{
    background: {ORANGE}; color: #111827;
    border-radius: 9px; padding: 3px 10px;
    font-size: 11px; font-weight: 700;
}}

QFrame.carte {{ background: {SURFACE}; border-radius: 12px; }}
QLabel.chiffre {{ font-size: 30px; font-weight: 700; }}
QLabel.libelle {{ color: {TEXTE_DOUX}; font-size: 10px; letter-spacing: 1px; }}

QProgressBar {{
    background: {SURFACE}; border: 0; border-radius: 7px;
    height: 14px; text-align: center; color: transparent;
}}
QProgressBar::chunk {{ background: {VERT}; border-radius: 7px; }}

QPushButton {{
    background: {SURFACE_HAUTE}; color: {TEXTE};
    border: 0; border-radius: 8px; padding: 9px 16px; font-weight: 600;
}}
QPushButton:hover  {{ background: #475569; }}
QPushButton:disabled {{ color: {TEXTE_DOUX}; background: {SURFACE}; }}
QPushButton#pause  {{ background: {ORANGE}; color: #111827; }}
QPushButton#danger {{ background: #7f1d1d; color: #fecaca; }}

QComboBox, QLineEdit, QSpinBox {{
    background: {FOND}; border: 1px solid {SURFACE_HAUTE};
    border-radius: 8px; padding: 7px 10px; selection-background-color: {SURFACE_HAUTE};
}}
QComboBox QAbstractItemView {{
    background: {SURFACE}; border: 1px solid {SURFACE_HAUTE};
    selection-background-color: {SURFACE_HAUTE};
}}

QListWidget {{
    background: {SURFACE}; border: 0; border-radius: 12px;
    color: {TEXTE_DOUX};
    font-family: Consolas, "Courier New", monospace; font-size: 11px;
}}
/* Pas de `color` sur ::item : la feuille de style l'emporterait sur le vert et
   le rouge posés ligne par ligne (succès / erreur) par la fenêtre. */
QListWidget::item {{ padding: 3px 8px; }}

QGroupBox {{
    border: 1px solid {SURFACE_HAUTE}; border-radius: 10px;
    margin-top: 14px; padding-top: 10px; font-weight: 600;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {BLEU}; }}

QCheckBox {{ spacing: 8px; }}
QScrollBar:vertical {{ background: {FOND}; width: 10px; }}
QScrollBar::handle:vertical {{ background: {SURFACE_HAUTE}; border-radius: 5px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
"""
