# 🎯 Guide Détaillé: Fly.io + Render (Preview + Production) - Europe API

## 📌 Vue d'ensemble de l'architecture

```
Ton ordinateur (local)
    ↓
    git push origin main
    ↓
GitHub (ton repo)
    ↓
    └─→ GitHub Actions (workflow automatique)
         ↓
         ├─→ Teste le build
         └─→ Envoie vers Fly.io
              ↓
              Fly.io (Preview/Staging)
              https://trainnomad-europe-preview.fly.dev
              (Tu testes chaque changement ici)
              ↓
         Une fois par semaine tu fais un
         PUSH MANUEL vers Render (Production)
         ↓
         Render (Production)
         https://api.onrender.com (ou ton URL)
```

---

# 🚀 ÉTAPE PAR ÉTAPE (TRÈS DÉTAILLÉ)

## ❶ INSTALLER FLYCTL (CLI Fly.io)

### Sur Windows PowerShell:

```powershell
# Ouvre PowerShell comme administrateur
# Appuie sur Windows + X, puis sélectionne "Windows PowerShell (Admin)"

# Exécute cette commande:
iwr https://fly.io/install.ps1 -useb | iex

# Attends que ça finisse (quelques secondes)
# Tu dois voir: "Flyctl is installed!"
```

### Vérifier que c'est installé:

```powershell
fly --version

# Tu dois voir quelque chose comme:
# flyctl version 0.1.234
```

---

## ❷ CRÉER UN COMPTE FLY.IO (si pas déjà fait)

1. Va sur https://fly.io
2. Click **"Sign Up"**
3. Entre ton email
4. Vérifie ton email
5. Crée un mot de passe

L'app gratuite incluse suffit pour toi!

---

## ❸ CONNEXION FLY.IO DEPUIS POWERSHELL

```powershell
fly auth login

# Tu vas être redirigé vers un navigateur
# Une page s'ouvre: https://api.fly.io/app/auth/cli/...
# Click "Authorize"
# Reviens à PowerShell

# PowerShell affiche:
# "Logged in successfully as: ton@email.com"
```

**Vérifier:**
```powershell
fly auth whoami
# Doit afficher ton email
```

---

## ❹ CRÉER L'APP FLY.IO

```powershell
# Change vers le dossier Backend/Europe
cd "C:\Users\PC\Desktop\TrainNomad V2\Backend\Europe"

# Démarre la création
fly launch

# Fly.io va te poser des questions:
```

**Question 1: App name?**
```
? App Name: trainnomad-europe-preview
```

**Question 2: Select Organization**
```
? Select Organization: [ta-personne] (personal)
(appuie sur Entrée)
```

**Question 3: Region**
```
? Region for app: cdg

(cdg = Paris, rapide pour l'Europe)
```

**Question 4: PostgreSQL?**
```
? Would you like to set up a PostgreSQL database now? No
(appuie sur Entrée)
```

**Question 5: Redis?**
```
? Would you like to set up a Redis now? No
(appuie sur Entrée)
```

**Question 6: Deploy now?**
```
? Would you like to deploy now? Yes
(appuie sur Entrée)
```

Fly.io va construire et déployer. Attends 2-3 minutes.

Tu dois voir:
```
Visit your newly deployed app at https://trainnomad-europe-preview.fly.dev/
```

---

## ❺ GÉNÉRER LE TOKEN FLY.IO

```powershell
fly tokens create deploy

# Fly.io affiche:
# Fly API token xxxxxxxxxxxxxxxxxxxxxxxx
```

**IMPORTANT:** Copie ce token complètement. Ne le montre à personne!

---

## ❻ AJOUTER LE TOKEN À GITHUB

### Étape 1: Aller sur GitHub

1. Va sur: https://github.com/ton-username/ton-repo
2. Click sur **"Settings"**

### Étape 2: Aller aux Secrets

3. Dans le menu gauche: click **"Secrets and variables"**
4. Puis click **"Actions"**

### Étape 3: Créer un nouveau secret

5. Click le bouton vert **"New repository secret"**

Tu vas voir un formulaire:
```
Name: 
Secret: 
```

6. **Name:** tape exactement `FLY_API_TOKEN`
7. **Secret:** Colle le token que tu as copié

8. Click **"Add secret"**

---

## ❼ CONFIGURER RENDER (Déploiement MANUEL)

### Sur ton dashboard Render:

1. Va sur https://dashboard.render.com
2. Sélectionne le service **"europe"** ou **"api"**
3. Click sur **"Settings"**
4. Scroll jusqu'à **"Build & Deploy"** ou **"Auto-deploy"**
5. Sélectionne **"No"** pour désactiver l'auto-deploy
6. Click **"Save"**

### Pour redéployer une fois par semaine:

1. Va sur le dashboard Render
2. Sélectionne ton service
3. Click le bouton **"Deploy latest"** ou **"Redeploy"**

---

## ❽ TESTER LE WORKFLOW

### Faire un commit test:

```powershell
cd "C:\Users\PC\Desktop\TrainNomad V2\Backend\Europe"

git status

# Tu dois voir:
# - fly.toml (nouveau)
# - FLY_SETUP.md (nouveau)
# - FLY_SETUP_DETAILED.md (nouveau)
# - .github/workflows/deploy-fly.yml (nouveau)
```

### Ajouter et commit:

```powershell
git add fly.toml .github/workflows/deploy-fly.yml FLY_SETUP.md FLY_SETUP_DETAILED.md

git status
# Doit montrer les fichiers en vert

git commit -m "Setup: Configure Fly.io auto-deployment for preview environment"

git push origin main
```

---

## ❾ VÉRIFIER QUE LE WORKFLOW FONCTIONNE

### Sur GitHub:

1. Va sur ton repo: https://github.com/ton-username/ton-repo
2. Click sur **"Actions"**
3. Tu dois voir le workflow **"Deploy to Fly.io (Preview)"** en cours

Attends 2-3 minutes.

### Vérifier l'app:

```powershell
fly status

# Doit afficher:
# App: trainnomad-europe-preview
# Status: Running
```

Ou visite dans le navigateur:
```
https://trainnomad-europe-preview.fly.dev/
```

---

## ❿ APRÈS LE TEST: Utilisation quotidienne

### Chaque jour (pendant le développement):

```powershell
# Fais ton travail normal
# Édite les fichiers

git add .
git commit -m "Ma description du changement"
git push origin main

# GitHub Actions détecte le push
# ↓
# Fly.io redéploie AUTOMATIQUEMENT
# ↓
# Tu peux voir le changement sur:
# https://trainnomad-europe-preview.fly.dev
```

### Une fois par semaine (production):

```powershell
# Quand tu es satisfait des changements
# Va sur le dashboard Render
# Click "Redeploy"
```

---

## 🆘 TROUBLESHOOTING

### Erreur: "GitHub Actions workflow failed"

```powershell
# 1. Vérife le token GitHub Secrets
# Va sur GitHub → Settings → Secrets
# Tu dois voir FLY_API_TOKEN

# 2. Vérife le fichier deploy-fly.yml
git ls-files | grep deploy-fly
# Doit afficher: .github/workflows/deploy-fly.yml

# 3. Redéploie le workflow
# Sur GitHub Actions, click "Re-run failed jobs"
```

### L'app Fly ne répond pas

```powershell
# Voir les logs
fly logs

# Vérifier la machine
fly machines list

# Redémarrer si nécessaire
fly machines restart <machine-id>
```

---

## ✅ CHECKLIST FINALE

- [ ] Flyctl installé (`fly --version` fonctionne)
- [ ] Compte Fly.io créé
- [ ] `fly auth login` réussi
- [ ] `fly launch` exécuté
- [ ] Token généré avec `fly tokens create deploy`
- [ ] Token ajouté à GitHub Secrets (`FLY_API_TOKEN`)
- [ ] Render configuré en **déploiement manuel**
- [ ] Commit test poussé
- [ ] GitHub Actions workflow réussi
- [ ] App accessible sur Fly.io

---

## 🎉 Bravo!

Tu as maintenant:

✅ **Preview/Staging** (Fly.io) = Chaque commit test automatiquement
✅ **Production** (Render) = Tu contrôles quand déployer
✅ **GitHub Actions** = Automatisation complète
