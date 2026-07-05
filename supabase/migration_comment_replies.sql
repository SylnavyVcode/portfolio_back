-- ============================================================================
-- MIGRATION — Réponses aux commentaires
-- À exécuter UNIQUEMENT si vous avez déjà exécuté migration_comments_ratings.sql
-- dans sa version sans la colonne parent_id (sinon elle est déjà incluse).
-- ============================================================================

alter table public.comments
  add column if not exists parent_id uuid references public.comments (id) on delete cascade;

-- Suppression désormais réservée aux admins
drop policy if exists "comments_delete_own_or_admin" on public.comments;
drop policy if exists "comments_delete_admin" on public.comments;
create policy "comments_delete_admin"
  on public.comments for delete
  using (public.is_admin());
