"""Génère `assets/lamapix.ico` (multi-tailles) — à relancer seulement si le
visuel change. Pillow n'est nécessaire que pour ce script, pas pour l'outil.

    python packaging/generer_icone.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

FOND = (15, 23, 42, 255)      # slate 900 — le fond de l'interface
ACCENT = (125, 211, 252, 255)  # sky 300 — la couleur du titre
TAILLES = [16, 24, 32, 48, 64, 128, 256]
COTE = 256


def dessiner(cote: int = COTE) -> Image.Image:
    image = Image.new("RGBA", (cote, cote), (0, 0, 0, 0))
    dessin = ImageDraw.Draw(image)
    dessin.rounded_rectangle(
        [(0, 0), (cote - 1, cote - 1)], radius=int(cote * 0.22), fill=FOND
    )

    # Flèche montante : « ça part vers Lamapix ».
    milieu = cote / 2
    largeur_hampe = cote * 0.13
    dessin.polygon(
        [
            (milieu, cote * 0.20),
            (milieu + cote * 0.24, cote * 0.48),
            (milieu + largeur_hampe / 2, cote * 0.48),
            (milieu + largeur_hampe / 2, cote * 0.74),
            (milieu - largeur_hampe / 2, cote * 0.74),
            (milieu - largeur_hampe / 2, cote * 0.48),
            (milieu - cote * 0.24, cote * 0.48),
        ],
        fill=ACCENT,
    )
    return image


def main() -> None:
    destination = Path(__file__).resolve().parent.parent / "assets" / "lamapix.ico"
    destination.parent.mkdir(parents=True, exist_ok=True)
    dessiner().save(destination, format="ICO", sizes=[(t, t) for t in TAILLES])
    print(f"Icône écrite : {destination}")


if __name__ == "__main__":
    main()
