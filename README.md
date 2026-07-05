# Portfolio Valmy Mabika — Back-end

API FastAPI pour le portfolio : comptes utilisateurs, blog, formations payantes,
panier, paiements (Stripe, cash, mobile money) et emails transactionnels.

- **Base de données & Auth** : Supabase (PostgreSQL + Supabase Auth + RLS)
- **Back-end** : Python / FastAPI
- **Emailing** : Brevo (API transactionnelle)
- **Paiements** : Stripe Checkout (carte), cash validé par admin, Flutterwave (mobile money)

## Démarrage local

```bash
cd portfolio_back
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # puis remplir les clés (voir .env.example, tout y est documenté)
.venv/bin/uvicorn app.main:app --reload
```

- Swagger : http://localhost:8000/api/docs
- Santé : http://localhost:8000/api/health
- Tests : `.venv/bin/python -m pytest tests/ -q`

### Base de données (à exécuter une fois, dans le SQL Editor Supabase)

1. `supabase/schema.sql` — tables, triggers, politiques RLS.
2. `supabase/seed_blog.sql` — les 6 articles du blog.
3. `supabase/seed_courses.sql` — les 12 formations (prix EUR à ajuster).
4. Créer votre compte via `/auth` du site, puis vous promouvoir admin :
   ```sql
   update public.profiles set role = 'admin'
   where id = (select id from auth.users where email = 'valmymabika@gmail.com');
   ```
5. Supabase Dashboard → Authentication → URL Configuration : renseigner la
   Site URL (URL du front) et ajouter `<front>/reset-password` aux Redirect URLs.

## Structure

```
portfolio_back/
├── api/index.py            # point d'entrée Vercel (expose app.main:app)
├── app/
│   ├── main.py             # FastAPI, CORS, erreurs centralisées
│   ├── core/               # config (.env), JWT Supabase, rate limiting
│   ├── db/                 # clients Supabase (anon + service_role)
│   ├── dependencies.py     # get_current_user, require_admin
│   ├── routers/            # auth, blog, courses, cart, enrollments, payments, admin
│   ├── schemas/            # modèles Pydantic
│   └── services/           # email (Brevo), stripe, flutterwave
├── supabase/               # schema.sql + seeds
└── tests/                  # pytest (49+ tests, sans dépendance réseau)
```

## API — résumé des endpoints

Swagger généré automatiquement sur `/api/docs`. Niveaux d'accès :
**Public** / **Connecté** (JWT Supabase en `Authorization: Bearer`) / **Admin** (rôle `admin`).

### Auth — `/api/auth` (rate-limité)

| Méthode | Path | Description | Accès |
|---|---|---|---|
| POST | `/register` | Inscription (email de bienvenue ; confirmation email Supabase si activée) | Public |
| POST | `/login` | Connexion → tokens de session + user (avec rôle) | Public |
| POST | `/refresh` | Renouvelle la session avec le refresh token | Public |
| POST | `/logout` | Révoque la session côté serveur | Connecté |
| POST | `/forgot-password` | Envoie l'email de réinitialisation (Supabase) | Public |
| POST | `/reset-password` | Fixe le nouveau mot de passe (token du lien email) | Public |
| POST | `/change-password` | Change le mot de passe (vérifie l'actuel) | Connecté |
| GET | `/me` | Utilisateur courant + profil | Connecté |
| PUT | `/me/profile` | Met à jour le profil | Connecté |

### Blog — `/api/blog`

| Méthode | Path | Description | Accès |
|---|---|---|---|
| GET | `/` | Articles publiés (pagination, filtre catégorie) | Public |
| GET | `/{slug}` | Article complet | Public |

### Formations — `/api/courses`

| Méthode | Path | Description | Accès |
|---|---|---|---|
| GET | `/` | Catalogue publié (filtres theme/level) | Public |
| GET | `/{slug}` | Détail + sommaire des leçons | Public |
| GET | `/{slug}/lessons` | Contenu complet des leçons | Inscrit (enrollment actif) ou Admin |
| GET | `/{slug}/lessons/{id}` | Une leçon (les aperçus gratuits passent sans enrollment) | Connecté |

### Panier — `/api/cart` (Connecté)

`GET /` · `POST /items {course_slug}` · `DELETE /items/{course_slug}` · `DELETE /`
Chaque réponse renvoie le panier enrichi (formations + sous-total).

### Mes données — `/api/me` (Connecté)

`GET /enrollments` (formations débloquées) · `GET /orders` · `GET /orders/{id}`

### Paiements — `/api/payments`

| Méthode | Path | Description | Accès |
|---|---|---|---|
| POST | `/checkout` | Crée la commande depuis le panier serveur. `card` → URL Stripe Checkout ; `cash` → `pending_validation` + email ; `mobile_money` → lien de paiement Flutterwave | Connecté |
| POST | `/webhooks/stripe` | Signature vérifiée. Paiement OK → commande `paid` + enrollments + email ; remboursement → accès révoqués | Stripe |
| POST | `/webhooks/flutterwave` | Hash `verif-hash` vérifié + re-vérification de la transaction côté API Flutterwave | Flutterwave |

### Admin — `/api/admin` (Admin uniquement)

- **Blog** : `GET /blog` (brouillons inclus), `POST /blog`, `PUT /blog/{id}`, `DELETE /blog/{id}`
- **Formations** : `GET/POST /courses`, `PUT/DELETE /courses/{id}`
- **Leçons** : `GET/POST /courses/{id}/lessons`, `PUT/DELETE /lessons/{id}`
- **Commandes** : `GET /orders?status=...`, `POST /orders/{id}/validate-cash`
  (passe en `paid`, crée les enrollments, envoie l'email), `POST /orders/{id}/cancel`

## Déploiement

### Back-end sur Vercel

1. Pousser `portfolio_back/` dans un repo GitHub dédié (ou en sous-dossier avec
   *Root Directory* configuré dans Vercel).
2. Vercel → New Project → importer le repo. Le `vercel.json` route tout vers
   l'app ASGI (`api/index.py`).
3. Renseigner **toutes** les variables de `.env.example` dans
   Settings → Environment Variables (jamais de clé en dur dans le code).
   - `ENVIRONMENT=production`
   - `FRONTEND_URL=https://<votre-front>.vercel.app`
   - `CORS_ORIGINS=https://<votre-front>.vercel.app`
4. **Webhook Stripe de prod** : Dashboard Stripe → Developers → Webhooks →
   Add endpoint → `https://<votre-back>.vercel.app/api/payments/webhooks/stripe`
   (événements `checkout.session.*` et `charge.refunded`) → copier le
   `whsec_...` dans `STRIPE_WEBHOOK_SECRET`.
5. **Webhook Flutterwave** : Dashboard → Settings → Webhooks → URL
   `https://<votre-back>.vercel.app/api/payments/webhooks/flutterwave` +
   définir le *secret hash* (le même que `FLUTTERWAVE_WEBHOOK_HASH`).

Limites connues de Vercel serverless : rate limiting en mémoire par instance
(best-effort) et cold starts. Si cela devient gênant, Render (service web
Python) est l'alternative prévue — le code n'a rien de spécifique à Vercel à
part `api/index.py` et `vercel.json`.

### Front-end (Vercel existant)

Ajouter la variable d'environnement `VITE_API_URL=https://<votre-back>.vercel.app`
puis redéployer.

## Checklist de tests manuels avant mise en production

**Comptes**
- [ ] Inscription → email de bienvenue reçu (+ email de confirmation Supabase si activé)
- [ ] Connexion / déconnexion / session conservée après refresh de la page
- [ ] Mot de passe oublié → email reçu → lien → nouveau mot de passe → reconnexion
- [ ] Changement de mot de passe (mauvais mot de passe actuel refusé)
- [ ] Modification du profil persistée
- [ ] Un compte non-admin ne voit pas les onglets admin et reçoit 403 sur `/api/admin/*`

**Blog**
- [ ] `/blog` liste les articles publiés (pas les brouillons)
- [ ] Création d'un brouillon en admin → invisible au public → publication → visible
- [ ] Modification et suppression d'un article

**Formations**
- [ ] Catalogue affiché avec prix en €, filtres et recherche fonctionnels
- [ ] Leçons inaccessibles sans achat (message « contenu réservé »)
- [ ] Création/édition de formation et de leçons en admin ; aperçu gratuit visible sans achat

**Paiements**
- [ ] Carte (Stripe test `4242...`) : commande `paid`, enrollment créé, email reçu, formation accessible
- [ ] Paiement Stripe abandonné (bouton retour) : commande reste `pending`, bannière « annulé »
- [ ] Cash : commande `pending_validation` + email → validation admin → `paid` + email + accès
- [ ] Annulation d'une commande en attente par l'admin
- [ ] Mobile money sandbox (Flutterwave) : lien de paiement ouvert, retour, webhook → `paid`
- [ ] Remboursement Stripe (dashboard) → commande `refunded`, accès révoqué
- [ ] Webhook avec mauvaise signature → 400 (tester avec `curl`)

**Sécurité**
- [ ] `SUPABASE_SERVICE_ROLE_KEY` absente du front et du repo (`.env` gitignoré)
- [ ] Rate limiting : >10 tentatives de login/minute → 429
- [ ] Une erreur serveur renvoie un JSON générique, jamais de stack trace
