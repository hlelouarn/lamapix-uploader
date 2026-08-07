# Lamapix Uploader

Envoi automatique des photos produites par **Kadra** vers la plateforme de vente
**Lamapix**, en FTPS. Un exécutable portable, une fenêtre de contrôle, et une
mémoire locale qui garantit que rien ne part deux fois — ni ne se perd.

Reprend la logique d'un script PowerShell validé sur deux événements réels. Le
cahier des charges d'origine et ce script restent hors du dépôt (`reference/`,
non versionné) : ils citent des noms de cavaliers et une topologie réseau
interne qui n'ont rien à faire dans un dépôt public.

---

## Installer sur un PC (utilisateur)

1. Copier **`LamapixUploader.exe`** où vous voulez (Bureau, `C:\Outils\`, une clé…).
2. Double-cliquer. Au premier lancement, il demande le **dossier des événements
   Kadra**, puis l'**identifiant et le mot de passe Lamapix**. Le mot de passe est
   mémorisé **chiffré** sur cette machine (DPAPI), jamais en clair.
3. Choisir l'événement à surveiller, puis laisser tourner.

### Déployer sur les autres PC sans tout retaper

Configurer **un** poste, puis copier `LamapixUploader.exe` **et**
`donnees\config.json` sur les suivants : hôte, identifiant, dossier Kadra et
réglages arrivent déjà remplis. Seul le mot de passe est redemandé — DPAPI le
rend volontairement illisible sur une autre machine.

Rien de tout cela n'est en dur dans l'exécutable : le dépôt étant public, il ne
contient **ni identifiant, ni nom de serveur interne, ni secret**.

Pas d'installation, pas de droits administrateur. L'outil crée un dossier
**`donnees\`** à côté de l'exe :

```
LamapixUploader.exe
donnees\
├── config.json         réglages de ce PC
├── motdepasse.bin      mot de passe chiffré (illisible ailleurs)
├── tampon\<EVENEMENT>\ copie locale, structurée EXACTEMENT comme le FTP
│   └── _memoire.json   ce qui est parti, et quand
└── journaux\           journal horodaté, avec rotation
```

> **Un événement = une machine = un chemin.** La mémoire est locale et liée au
> chemin exact du dossier surveillé. Deux PC qui envoient le même événement ne se
> connaissent pas et enverront chacun leur lot.

### Le tampon est le plan B

Il reproduit à l'identique l'arborescence attendue par Lamapix. Si l'outil ne
peut plus rien envoyer, on glisse le contenu de `tampon\<EVENEMENT>\` dans
FileZilla et on obtient exactement le même résultat.

## Les trois boutons qui comptent

| Bouton | Effet |
|---|---|
| **Pause** | Suspend les envois. Le scan continue, rien n'est perdu. |
| **Initialiser la mémoire** | Marque tout l'existant comme déjà envoyé, **sans rien envoyer**. À utiliser quand Kadra a déjà uploadé une partie de l'événement : ça pose la frontière. |
| **Réinitialiser** | Efface la mémoire : **tout repart** sur Lamapix. Avec confirmation. |

Fermer la fenêtre **n'arrête pas les envois** : l'outil continue près de
l'horloge. Pour l'arrêter vraiment : clic droit sur l'icône → *Quitter*.

---

## Ce que fait l'outil, dans l'ordre

1. **Scan** du dossier événement (toutes les 30 s par défaut). Les fichiers
   modifiés il y a moins de 15 s sont ignorés — Kadra est peut-être encore en
   train de les écrire.
2. **Restructuration** vers l'arborescence Lamapix :

   | Source (Kadra) | Destination (Lamapix) |
   |---|---|
   | `<EPREUVE>\<NUM>_<NOM>_<CHEVAL>\x.jpg` | `/<EVENEMENT>/<NOM>_<CHEVAL>/x.jpg` |
   | `0_AMBIANCE\**\x.jpg` | `/<EVENEMENT>/AMBIANCE/x.jpg` (à plat) |
   | `x.jpg` (racine) | `/<EVENEMENT>/x.jpg` |

   L'épreuve et le dossard disparaissent : un cavalier engagé sur plusieurs
   épreuves **fusionne** dans un seul dossier distant. Les JPEG seulement ; tout
   chemin passant par un dossier `webp` est ignoré.
3. **Copie dans le tampon**, puis **envoi FTPS** (1 à 3 connexions parallèles).
4. **Purge du tampon** des photos envoyées depuis plus de 24 h. Jamais la source,
   jamais le FTP, jamais la mémoire.

## Pourquoi la mémoire locale fait seule autorité

Lamapix **aspire** ce qu'on dépose : fichiers et dossiers disparaissent après
ingestion. Un FTP vide ne prouve donc rien. Conséquences, toutes implémentées :

- ce qui a été envoyé est su **localement**, dans `_memoire.json` (écriture
  atomique, sauvegarde tous les 20 envois → un crash ne fait pas tout renvoyer) ;
- « ce dossier existe » n'est **jamais** mis en cache définitivement : sur échec
  (typiquement **550**), on invalide, on recrée l'arborescence, et on réessaie
  sur une **connexion neuve** ;
- la racine de l'événement est **créée par l'outil** — Kadra ne le fait plus ;
- 3 tentatives par photo, puis mise en attente 4 min et **la file continue** :
  un fichier en erreur ne bloque jamais les autres. 3 échecs d'affilée dans un
  dossier → tout le dossier attend 4 min ;
- **rien n'est jamais supprimé côté FTP**.

---

## Développement

```powershell
cd C:\dev\lamapix-uploader
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pytest pyinstaller pillow
```

### Tests

```powershell
$env:PYTHONUTF8 = 1
.\.venv\Scripts\python.exe -m pytest
```

82 tests. Le moteur est testé de bout en bout contre un **faux serveur Lamapix**
(`tests/conftest.py`) qui rejoue les pièges du terrain : dossiers consommés en
cours de route, 550 passagers, pannes durables, identifiants refusés. Aucun test
ne touche le vrai serveur.

### Lancer depuis les sources

```powershell
.\.venv\Scripts\python.exe app.py
```

### Construire l'exe

```powershell
.\packaging\construire.ps1            # tests + icône + PyInstaller → dist\
.\packaging\construire.ps1 -Publier   # + release GitHub (updater)
```

Le script refuse de construire si les tests ne passent pas. La version publiée
est celle de `lamapix_uploader/__init__.py` — seule source de vérité, c'est elle
que l'updater compare au tag de la release.

### Architecture

Le cœur ne connaît **ni Qt ni le réseau**, ce qui le rend testable :

| Module | Rôle |
|---|---|
| `mapping.py` | Règles Kadra → Lamapix, unicité des noms distants |
| `memory.py` | Mémoire persistante par événement |
| `scanner.py` | Scan, stabilité, exclusions |
| `ftp.py` | Client FTPS explicite, MKD par niveaux, cache invalidable |
| `engine.py` | Pipeline, parallélisme, reprises, cooldowns, purge |
| `journal.py` / `config.py` / `secrets_win.py` / `paths.py` | Journal tournant, réglages, DPAPI, portabilité |
| `ui/` | Fenêtre Qt, réglages, zone de notification |

`engine._un_tour()` exécute un cycle complet **sans jamais dormir** : c'est le
point d'entrée des tests.

### Sécurité

Aucun secret dans le code ni dans Git. Le mot de passe vit uniquement dans
`donnees\motdepasse.bin`, chiffré par DPAPI, et `donnees/` est dans le
`.gitignore`.
