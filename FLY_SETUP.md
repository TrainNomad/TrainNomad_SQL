# 🚀 Migration vers Fly.io + Render (Architecture Preview/Prod)

## Architecture
```
GitHub main branch
    ↓
    ├→ Fly.io (auto-deploy) → https://trainnomad-europe-preview.fly.dev
    └→ Render (manuel/1x par semaine) → Production
```

---

## ✅ ÉTAPE 1: Créer l'app Fly.io

```bash
cd Backend/Europe
fly auth login  # Si pas déjà connecté

fly launch
# Répondre:
# - App name: trainnomad-europe-preview
# - Region: cdg (Paris) ou autre
# - Postgresql: non
# - Redis: non
# - Deploy now: yes
```

---

## ✅ ÉTAPE 2: Générer le token Fly.io pour GitHub

```bash
fly tokens create deploy
```

Copie le token!

---

## ✅ ÉTAPE 3: Ajouter le token à GitHub Secrets

1. Va sur ton repo GitHub
2. **Settings** → **Secrets and variables** → **Actions**
3. Click **"New repository secret"**
4. Nom: `FLY_API_TOKEN`
5. Valeur: (colle le token)
6. **Add secret**

---

## ✅ ÉTAPE 4: Configurer Render (déploiement MANUEL)

Sur Render:

1. Va sur https://dashboard.render.com
2. Sélectionne ton service `europe` ou `api`
3. **Settings** → **Build & Deploy**
4. **Auto-Deploy**: mets sur **OFF**
5. Save

---

## ✅ ÉTAPE 5: Tester le workflow

```bash
git add fly.toml .github/workflows/deploy-fly.yml FLY_SETUP.md FLY_SETUP_DETAILED.md
git commit -m "Setup Fly.io preview deployment for Europe API"
git push origin main
```

Va vérifier sur GitHub → **Actions** tab

---

## 📋 Checklist

- [ ] Compte Fly.io créé
- [ ] `fly launch` exécuté
- [ ] Token Fly créé et copié
- [ ] `FLY_API_TOKEN` ajouté aux GitHub Secrets
- [ ] Render configuré en **déploiement manuel**
- [ ] Premier test commit poussé
- [ ] Preview app visible sur Fly.io

---

## 🎉 Résultat

✅ **Preview** (Fly.io) = Chaque commit test automatiquement
✅ **Production** (Render) = Tu contrôles quand déployer
