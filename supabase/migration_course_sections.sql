-- ============================================================================
-- MIGRATION — Sections de cours, types de contenu, objectifs pédagogiques,
-- suivi de progression apprenant (module formations, phase 1).
-- À exécuter dans le SQL Editor de Supabase sur un projet existant.
-- (Le schéma complet pour une installation neuve est dans schema.sql.)
-- ============================================================================

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. COURSES — objectifs pédagogiques ("ce que vous allez apprendre")
-- ─────────────────────────────────────────────────────────────────────────────

alter table public.courses
  add column learning_objectives jsonb not null default '[]';  -- [{fr, en}, ...]

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. COURSE_SECTIONS — regroupe les leçons d'une formation (remplace la liste
--    plate) : Section > Leçons, à l'image d'Udemy/OpenClassrooms/Coursera.
-- ─────────────────────────────────────────────────────────────────────────────

create table public.course_sections (
  id         uuid primary key default gen_random_uuid(),
  course_id  uuid not null references public.courses (id) on delete cascade,
  position   integer not null default 0,
  title_fr   text not null,
  title_en   text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (course_id, position)
);

create index idx_sections_course on public.course_sections (course_id);

create trigger trg_sections_updated_at
  before update on public.course_sections
  for each row execute function public.set_updated_at();

alter table public.course_sections enable row level security;

-- Visible dès lors que la formation elle-même l'est (le titre de section
-- n'est pas un contenu sensible, contrairement au contenu des leçons).
create policy "sections_select_course_visible"
  on public.course_sections for select
  using (
    exists (
      select 1 from public.courses c
      where c.id = course_id and (c.is_published or public.is_admin())
    )
  );

create policy "sections_admin_insert"
  on public.course_sections for insert
  with check (public.is_admin());

create policy "sections_admin_update"
  on public.course_sections for update
  using (public.is_admin())
  with check (public.is_admin());

create policy "sections_admin_delete"
  on public.course_sections for delete
  using (public.is_admin());

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. COURSE_LESSONS — rattachement à une section + type de contenu
-- ─────────────────────────────────────────────────────────────────────────────

alter table public.course_lessons
  add column section_id uuid references public.course_sections (id) on delete cascade,
  add column content_type text not null default 'video'
    check (content_type in ('video', 'text', 'quiz'));

-- Backfill : une section "Contenu" par formation ayant déjà des leçons, pour
-- que la migration soit compatible avec du contenu déjà saisi via l'admin.
insert into public.course_sections (course_id, position, title_fr, title_en)
select distinct course_id, 0, 'Contenu', 'Content'
from public.course_lessons
where section_id is null;

update public.course_lessons cl
set section_id = cs.id
from public.course_sections cs
where cs.course_id = cl.course_id
  and cl.section_id is null;

alter table public.course_lessons alter column section_id set not null;

-- La position d'une leçon est désormais relative à sa section, pas à la
-- formation entière.
alter table public.course_lessons drop constraint course_lessons_course_id_position_key;
alter table public.course_lessons add constraint course_lessons_section_id_position_key
  unique (section_id, position);

create index idx_lessons_section on public.course_lessons (section_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. LESSON_PROGRESS — suivi de complétion apprenant (par inscription)
-- ─────────────────────────────────────────────────────────────────────────────

create table public.lesson_progress (
  id            uuid primary key default gen_random_uuid(),
  enrollment_id uuid not null references public.enrollments (id) on delete cascade,
  lesson_id     uuid not null references public.course_lessons (id) on delete cascade,
  completed_at  timestamptz not null default now(),
  unique (enrollment_id, lesson_id)
);

create index idx_lesson_progress_enrollment on public.lesson_progress (enrollment_id);

alter table public.lesson_progress enable row level security;

create policy "lesson_progress_select_own_or_admin"
  on public.lesson_progress for select
  using (
    exists (
      select 1 from public.enrollments e
      where e.id = enrollment_id and (e.user_id = auth.uid() or public.is_admin())
    )
  );

-- Pas de policy INSERT/UPDATE/DELETE : écriture réservée au back (service_role),
-- comme pour enrollments.
