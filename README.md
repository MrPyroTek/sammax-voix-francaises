# Voix françaises pour Sam & Max Save the World

Le remaster Steam (Skunkape) affiche les sous-titres français, mais les personnages parlent anglais. Ce mod remet l'anciens doublage français sur les six épisodes.

Tout tient dans **un seul fichier** : `SamMax-Voix-Francaises.exe` (environ 1,5 Go). Il contient déjà les voix des épisodes 1 à 6. 

## Télécharger

1. Ouvre la page des releases : [github.com/MrPyroTek/sammax-voix-francaises/releases](https://github.com/MrPyroTek/sammax-voix-francaises/releases)
2. Dans la dernière version, télécharge **SamMax-Voix-Francaises.exe**.

Ne prends pas le « Source code (zip) » : cette archive ne contient que le code, pas les voix. Le seul fichier utile pour jouer est l'exécutable.

Lien direct de la version 1.0.0 :

[SamMax-Voix-Francaises.exe](https://github.com/MrPyroTek/sammax-voix-francaises/releases/download/v1.0.0/SamMax-Voix-Francaises.exe)

## Installer

1. Ferme Sam & Max complètement.
2. Lance `SamMax-Voix-Francaises.exe`.
3. Une fenêtre extrait les voix. Compte une ou deux minutes, et environ 3 Go libres sur le disque Windows.
4. L'installeur s'ouvre. Le dossier du jeu est rempli tout seul quand Steam est installé.
5. Si le dossier est vide, clique sur **Parcourir** et choisis le dossier qui contient `SamMax100.exe`. C'est en général :

   `C:\Program Files (x86)\Steam\steamapps\common\Sam & Max Save the World`

   Tu peux aussi choisir le dossier parent : si `Sam & Max Save the World` est dedans, il est sélectionné.
6. Clique sur **Installer les voix françaises**.
7. Si Windows bloque l'écriture, accepte de relancer l'installeur en administrateur. C'est le cas habituel quand le jeu est dans `Program Files`.
8. Lance le jeu. Dans les options, choisis la langue **Français**, pour que les sous-titres correspondent aux voix.

Avant de remplacer les voix, l'installeur copie les fichiers anglais dans :

`Sam & Max Save the World\Archives\EnglishVoiceBackup`

Cette copie n'est jamais écrasée par les voix françaises. Elle sert à revenir en arrière.

## Revenir aux voix anglaises

1. Ferme le jeu.
2. Relance `SamMax-Voix-Francaises.exe`.
3. Clique sur **Remettre les voix anglaises**.

Vérifier l'intégrité des fichiers dans Steam remet aussi les voix anglaises. Propriétés du jeu, puis **Fichiers installés**, puis **Vérifier l'intégrité des fichiers du jeu**. Cette vérification efface le mod.

## Ce que le mod change

- Les dialogues des six épisodes passent sur le doublage français d'origine.
- Les sous-titres français déjà présents dans le remaster restent ceux du jeu.
- Les bruitages et la musique du remaster ne sont pas touchés.

## Limites
- Certaines phrases onts mal été "trouver" par mon script, donc il y a les voix anglaises.
- Le mouvement des lèvres reste calé sur la durée des voix anglaises.
- Quelques répliques réécrites pour le remaster n'existent pas dans l'ancien doublage. Celles-là restent en anglais.
- Le remaster n'a qu'une seule piste de voix. Après l'installation, les voix françaises jouent quelle que soit la langue des sous-titres.

## Pourquoi l'exécutable n'est pas dans les fichiers du dépôt

GitHub refuse tout fichier de plus de 100 Mo dans le dépôt git. L'installeur dépasse cette limite parce que les six épisodes sont déjà à l'intérieur. Il est donc joint à la release : un seul téléchargement, et toutes les voix y sont.

Cloner le dépôt, ou télécharger le code source, ne donne pas les voix. Pour jouer, il faut l'exécutable de la release.
