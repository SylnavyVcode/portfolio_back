-- ============================================================================
-- SCHÉMA SUPABASE — Portfolio Valmy Mabika
-- Modules : comptes (profiles), formations, panier, commandes/paiements,
--           inscriptions (enrollments), blog.
-- À exécuter dans le SQL Editor du projet Supabase (une seule fois).
-- Toutes les politiques RLS sont explicites.
--
-- Rappel d'architecture :
--   - Le front (supabase-js) n'accède aux tables qu'avec la clé anon
--     => c'est le RLS ci-dessous qui le protège.
--   - Le back FastAPI utilise la clé service_role (bypass RLS) pour les
--     écritures sensibles : commandes, paiements, enrollments, rôle admin.
-- ============================================================================

-- ─────────────────────────────────────────────────────────────────────────────
-- 0. Extensions & helpers
-- ─────────────────────────────────────────────────────────────────────────────

create extension if not exists "pgcrypto";

-- Les fonctions is_admin() et has_course_access() référencent des tables
-- créées plus bas dans ce script ; on désactive la validation des corps de
-- fonctions à la création (même technique que pg_dump), le temps du script.
set check_function_bodies = off;

-- Fonction utilitaire : l'utilisateur courant est-il admin ?
-- SECURITY DEFINER pour pouvoir lire profiles sans être bloqué par le RLS
-- de profiles lui-même (évite la récursion de policies).
create or replace function public.is_admin()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1 from public.profiles
    where id = auth.uid() and role = 'admin'
  );
$$;

-- Trigger générique : maintient updated_at.
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. PROFILES — extension de auth.users, avec rôle user/admin
-- ─────────────────────────────────────────────────────────────────────────────

create table public.profiles (
  id            uuid primary key references auth.users (id) on delete cascade,
  full_name     text not null default '',
  role          text not null default 'user' check (role in ('user', 'admin')),
  date_of_birth date,
  phone         text,
  address       text,
  city          text,
  country       text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create trigger trg_profiles_updated_at
  before update on public.profiles
  for each row execute function public.set_updated_at();

-- Création automatique du profil à l'inscription (Supabase Auth).
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, full_name)
  values (new.id, coalesce(new.raw_user_meta_data ->> 'full_name', ''));
  return new;
end;
$$;

create trigger trg_on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- Anti-escalade : un utilisateur final ne peut pas changer son propre rôle.
-- Autorisé quand il n'y a pas de contexte utilisateur (auth.uid() null :
-- SQL Editor, migrations, back-end en service_role) ou quand l'appelant
-- est déjà admin.
create or replace function public.protect_role_change()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if new.role is distinct from old.role
     and auth.uid() is not null
     and not public.is_admin() then
    raise exception 'Changement de rôle non autorisé';
  end if;
  return new;
end;
$$;

create trigger trg_protect_role_change
  before update on public.profiles
  for each row execute function public.protect_role_change();

alter table public.profiles enable row level security;

create policy "profiles_select_own"
  on public.profiles for select
  using (auth.uid() = id or public.is_admin());

create policy "profiles_update_own"
  on public.profiles for update
  using (auth.uid() = id or public.is_admin())
  with check (auth.uid() = id or public.is_admin());

-- Pas de policy INSERT/DELETE : la création passe par le trigger
-- handle_new_user, la suppression par cascade depuis auth.users.

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. COURSES — catalogue des formations (bilingue fr/en, prix EUR)
-- ─────────────────────────────────────────────────────────────────────────────

create table public.courses (
  id              uuid primary key default gen_random_uuid(),
  slug            text not null unique,                -- ex: web-react-ts-2026-05-08 (URLs front conservées)
  theme           text not null check (theme in ('web', 'python', 'data', 'testing')),
  level           text not null check (level in ('beginner', 'intermediate', 'advanced')),
  title_fr        text not null,
  title_en        text not null,
  summary_fr      text not null default '',
  summary_en      text not null default '',
  prerequisites   jsonb not null default '[]',         -- [{fr, en}, ...]
  preview         jsonb,                               -- {kind: code|dataset|runner, content}
  preview_label_fr text not null default '',
  preview_label_en text not null default '',
  price_amount    numeric(10,2) not null check (price_amount >= 0),
  currency        text not null default 'EUR',
  duration_hours  numeric(5,1) not null default 0,
  instructor      text not null default 'Valmy Mabika',
  rating          numeric(2,1) not null default 0 check (rating between 0 and 5),
  students_count  integer not null default 0,
  is_published    boolean not null default false,
  published_at    timestamptz,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

create trigger trg_courses_updated_at
  before update on public.courses
  for each row execute function public.set_updated_at();

alter table public.courses enable row level security;

create policy "courses_select_published_or_admin"
  on public.courses for select
  using (is_published = true or public.is_admin());

create policy "courses_admin_insert"
  on public.courses for insert
  with check (public.is_admin());

create policy "courses_admin_update"
  on public.courses for update
  using (public.is_admin())
  with check (public.is_admin());

create policy "courses_admin_delete"
  on public.courses for delete
  using (public.is_admin());

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. COURSE_LESSONS — contenu structuré d'une formation
--    Le contenu complet n'est visible que par les inscrits (enrollment actif),
--    sauf leçons marquées "aperçu gratuit".
-- ─────────────────────────────────────────────────────────────────────────────

create table public.course_lessons (
  id               uuid primary key default gen_random_uuid(),
  course_id        uuid not null references public.courses (id) on delete cascade,
  position         integer not null default 0,
  title_fr         text not null,
  title_en         text not null,
  content_fr       text,                               -- markdown
  content_en       text,
  video_url        text,
  duration_minutes integer not null default 0,
  is_free_preview  boolean not null default false,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  unique (course_id, position)
);

create index idx_lessons_course on public.course_lessons (course_id);

create trigger trg_lessons_updated_at
  before update on public.course_lessons
  for each row execute function public.set_updated_at();

-- L'utilisateur courant a-t-il un accès actif à cette formation ?
create or replace function public.has_course_access(p_course_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1 from public.enrollments e
    where e.course_id = p_course_id
      and e.user_id = auth.uid()
      and e.status = 'active'
  );
$$;

alter table public.course_lessons enable row level security;

create policy "lessons_select_enrolled_or_preview_or_admin"
  on public.course_lessons for select
  using (
    is_free_preview = true
    or public.has_course_access(course_id)
    or public.is_admin()
  );

create policy "lessons_admin_insert"
  on public.course_lessons for insert
  with check (public.is_admin());

create policy "lessons_admin_update"
  on public.course_lessons for update
  using (public.is_admin())
  with check (public.is_admin());

create policy "lessons_admin_delete"
  on public.course_lessons for delete
  using (public.is_admin());

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. CART_ITEMS — panier persistant côté serveur
-- ─────────────────────────────────────────────────────────────────────────────

create table public.cart_items (
  user_id   uuid not null references public.profiles (id) on delete cascade,
  course_id uuid not null references public.courses (id) on delete cascade,
  added_at  timestamptz not null default now(),
  primary key (user_id, course_id)
);

alter table public.cart_items enable row level security;

create policy "cart_select_own"
  on public.cart_items for select
  using (auth.uid() = user_id);

create policy "cart_insert_own"
  on public.cart_items for insert
  with check (auth.uid() = user_id);

create policy "cart_delete_own"
  on public.cart_items for delete
  using (auth.uid() = user_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. ORDERS / ORDER_ITEMS — commandes
--    Statuts : pending (paiement en ligne en cours), pending_validation (cash),
--              paid, failed, canceled, refunded.
--    Écritures réservées au back-end (service_role) — aucune policy INSERT/
--    UPDATE côté client : le front lit seulement.
-- ─────────────────────────────────────────────────────────────────────────────

create table public.orders (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid not null references public.profiles (id) on delete cascade,
  status         text not null default 'pending' check (
                   status in ('pending', 'pending_validation', 'paid',
                              'failed', 'canceled', 'refunded')
                 ),
  payment_method text not null check (
                   payment_method in ('card', 'paypal', 'mobile_money', 'cash')
                 ),
  total_amount   numeric(10,2) not null check (total_amount >= 0),
  currency       text not null default 'EUR',
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

create index idx_orders_user on public.orders (user_id);
create index idx_orders_status on public.orders (status);

create trigger trg_orders_updated_at
  before update on public.orders
  for each row execute function public.set_updated_at();

create table public.order_items (
  id             uuid primary key default gen_random_uuid(),
  order_id       uuid not null references public.orders (id) on delete cascade,
  course_id      uuid not null references public.courses (id),
  title_snapshot text not null,
  price_snapshot numeric(10,2) not null,
  unique (order_id, course_id)
);

create index idx_order_items_order on public.order_items (order_id);

alter table public.orders enable row level security;
alter table public.order_items enable row level security;

create policy "orders_select_own_or_admin"
  on public.orders for select
  using (auth.uid() = user_id or public.is_admin());

create policy "order_items_select_own_or_admin"
  on public.order_items for select
  using (
    exists (
      select 1 from public.orders o
      where o.id = order_id
        and (o.user_id = auth.uid() or public.is_admin())
    )
  );

-- ─────────────────────────────────────────────────────────────────────────────
-- 6. PAYMENTS — transactions liées aux commandes
--    Écritures uniquement via le back (webhooks Stripe/PayPal/Flutterwave,
--    validation cash par admin). Le client lit ses propres paiements.
-- ─────────────────────────────────────────────────────────────────────────────

create table public.payments (
  id                 uuid primary key default gen_random_uuid(),
  order_id           uuid not null references public.orders (id) on delete cascade,
  provider           text not null check (
                       provider in ('stripe', 'paypal', 'flutterwave', 'cash')
                     ),
  provider_reference text,                             -- session/transaction id externe
  status             text not null default 'pending' check (
                       status in ('pending', 'succeeded', 'failed', 'refunded')
                     ),
  amount             numeric(10,2) not null,
  currency           text not null default 'EUR',
  raw_payload        jsonb,                            -- dernier événement webhook (debug/audit)
  validated_by       uuid references public.profiles (id),  -- admin (paiement cash)
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now()
);

create index idx_payments_order on public.payments (order_id);
create unique index idx_payments_provider_ref
  on public.payments (provider, provider_reference)
  where provider_reference is not null;                -- idempotence des webhooks

create trigger trg_payments_updated_at
  before update on public.payments
  for each row execute function public.set_updated_at();

alter table public.payments enable row level security;

create policy "payments_select_own_or_admin"
  on public.payments for select
  using (
    exists (
      select 1 from public.orders o
      where o.id = order_id
        and (o.user_id = auth.uid() or public.is_admin())
    )
  );

-- ─────────────────────────────────────────────────────────────────────────────
-- 7. ENROLLMENTS — qui a accès à quelle formation
--    Créés par le back quand une commande passe à "paid".
-- ─────────────────────────────────────────────────────────────────────────────

create table public.enrollments (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references public.profiles (id) on delete cascade,
  course_id  uuid not null references public.courses (id) on delete cascade,
  order_id   uuid references public.orders (id) on delete set null,
  status     text not null default 'active' check (status in ('active', 'revoked')),
  granted_at timestamptz not null default now(),
  unique (user_id, course_id)
);

create index idx_enrollments_user on public.enrollments (user_id);
create index idx_enrollments_course on public.enrollments (course_id);

alter table public.enrollments enable row level security;

create policy "enrollments_select_own_or_admin"
  on public.enrollments for select
  using (auth.uid() = user_id or public.is_admin());

-- Pas de policy INSERT/UPDATE/DELETE : réservé au back (service_role).

-- ─────────────────────────────────────────────────────────────────────────────
-- 8. BLOG_POSTS — blog public avec brouillons
-- ─────────────────────────────────────────────────────────────────────────────

create table public.blog_posts (
  id           uuid primary key default gen_random_uuid(),
  slug         text not null unique,                   -- ex: react-best-practices
  status       text not null default 'draft' check (status in ('draft', 'published')),
  category_fr  text not null default '',
  category_en  text not null default '',
  title_fr     text not null,
  title_en     text not null,
  excerpt_fr   text not null default '',
  excerpt_en   text not null default '',
  content_fr   jsonb not null default '[]',            -- tableau de paragraphes (format actuel du front)
  content_en   jsonb not null default '[]',
  image_url    text,
  read_time    integer not null default 5,
  featured     boolean not null default false,
  author_id    uuid references public.profiles (id) on delete set null,
  published_at timestamptz,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

create index idx_blog_status on public.blog_posts (status);

create trigger trg_blog_updated_at
  before update on public.blog_posts
  for each row execute function public.set_updated_at();

alter table public.blog_posts enable row level security;

create policy "blog_select_published_or_admin"
  on public.blog_posts for select
  using (status = 'published' or public.is_admin());

create policy "blog_admin_insert"
  on public.blog_posts for insert
  with check (public.is_admin());

create policy "blog_admin_update"
  on public.blog_posts for update
  using (public.is_admin())
  with check (public.is_admin());

create policy "blog_admin_delete"
  on public.blog_posts for delete
  using (public.is_admin());

-- ============================================================================
-- FIN DU SCHÉMA
--
-- Étape manuelle après exécution : promouvoir votre compte en admin.
-- 1) Créez votre compte via la page /auth du site (ou Supabase Auth > Users).
-- 2) Puis exécutez :
--    update public.profiles set role = 'admin'
--    where id = (select id from auth.users where email = 'valmymabika@gmail.com');
-- ============================================================================
