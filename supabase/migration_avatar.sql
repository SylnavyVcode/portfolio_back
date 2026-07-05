-- ============================================================================
-- MIGRATION — ajoute la photo de profil (avatar).
-- À exécuter une fois dans le SQL Editor Supabase (déjà répercuté dans
-- schema.sql pour les nouvelles installations).
-- ============================================================================

alter table public.profiles
  add column if not exists avatar_url text;
