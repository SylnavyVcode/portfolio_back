-- ============================================================================
-- MIGRATION — Commentaires & notes (blog + formations)
-- À exécuter dans le SQL Editor de Supabase sur un projet existant.
-- (Le schéma complet pour une installation neuve est dans schema.sql.)
-- ============================================================================

-- ─────────────────────────────────────────────────────────────────────────────
-- COMMENTS — commentaires sur un article de blog OU une formation
-- ─────────────────────────────────────────────────────────────────────────────

create table public.comments (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references public.profiles (id) on delete cascade,
  post_id    uuid references public.blog_posts (id) on delete cascade,
  course_id  uuid references public.courses (id) on delete cascade,
  -- réponse à un commentaire racine (un seul niveau de profondeur)
  parent_id  uuid references public.comments (id) on delete cascade,
  content    text not null check (char_length(content) between 1 and 2000),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  -- exactement une cible : article OU formation
  check (num_nonnulls(post_id, course_id) = 1)
);

create index idx_comments_post on public.comments (post_id) where post_id is not null;
create index idx_comments_course on public.comments (course_id) where course_id is not null;

create trigger trg_comments_updated_at
  before update on public.comments
  for each row execute function public.set_updated_at();

alter table public.comments enable row level security;

create policy "comments_select_all"
  on public.comments for select
  using (true);

create policy "comments_insert_own"
  on public.comments for insert
  with check (auth.uid() = user_id);

create policy "comments_update_own"
  on public.comments for update
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

create policy "comments_delete_admin"
  on public.comments for delete
  using (public.is_admin());

-- ─────────────────────────────────────────────────────────────────────────────
-- RATINGS — note 1..5 par utilisateur sur un article OU une formation
--   (une seule note par utilisateur et par cible, mise à jour possible)
-- ─────────────────────────────────────────────────────────────────────────────

create table public.ratings (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references public.profiles (id) on delete cascade,
  post_id    uuid references public.blog_posts (id) on delete cascade,
  course_id  uuid references public.courses (id) on delete cascade,
  score      integer not null check (score between 1 and 5),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (num_nonnulls(post_id, course_id) = 1),
  unique (user_id, post_id),
  unique (user_id, course_id)
);

create index idx_ratings_post on public.ratings (post_id) where post_id is not null;
create index idx_ratings_course on public.ratings (course_id) where course_id is not null;

create trigger trg_ratings_updated_at
  before update on public.ratings
  for each row execute function public.set_updated_at();

alter table public.ratings enable row level security;

create policy "ratings_select_all"
  on public.ratings for select
  using (true);

create policy "ratings_insert_own"
  on public.ratings for insert
  with check (auth.uid() = user_id);

create policy "ratings_update_own"
  on public.ratings for update
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

create policy "ratings_delete_own"
  on public.ratings for delete
  using (auth.uid() = user_id);

-- La colonne courses.rating (affichée au catalogue) reste synchronisée
-- avec la moyenne des notes réelles de la formation.
create or replace function public.sync_course_rating()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  target uuid := coalesce(new.course_id, old.course_id);
begin
  if target is not null then
    update public.courses c
    set rating = coalesce(
      (select round(avg(r.score)::numeric, 1) from public.ratings r where r.course_id = target),
      0
    )
    where c.id = target;
  end if;
  return coalesce(new, old);
end;
$$;

create trigger trg_sync_course_rating
  after insert or update or delete on public.ratings
  for each row execute function public.sync_course_rating();
