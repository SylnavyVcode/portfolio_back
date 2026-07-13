"""Fake Supabase table-aware, réutilisable entre modules de tests.

Reproduit le minimum du query builder postgrest sur des dicts en mémoire :
eq/lt/gt, order (avec desc), limit/single, insert/update/upsert/delete.
Nécessaire dès qu'un même test exerce plusieurs tables distinctes avec des
filtres réels (ex: sections + leçons, réordonnancement par position).
"""

import uuid
from types import SimpleNamespace


class TableQuery:
    def __init__(self, rows: list[dict]):
        self._rows = rows
        self._filters: list[tuple[str, str, object]] = []
        self._op = "select"
        self._values = None
        self._order_col = None
        self._order_desc = False
        self._limit = None
        self._single = False
        self._on_conflict = None

    # ── chaînage ─────────────────────────────────────────────────────────────
    def select(self, *_a, **_k):
        self._op = "select"
        return self

    def insert(self, values):
        self._op = "insert"
        self._values = values if isinstance(values, list) else [values]
        return self

    def upsert(self, values, on_conflict=None, **_k):
        self._op = "upsert"
        self._values = values if isinstance(values, list) else [values]
        self._on_conflict = on_conflict
        return self

    def update(self, values):
        self._op = "update"
        self._values = values
        return self

    def delete(self):
        self._op = "delete"
        return self

    def eq(self, column, value):
        self._filters.append((column, "eq", value))
        return self

    def lt(self, column, value):
        self._filters.append((column, "lt", value))
        return self

    def gt(self, column, value):
        self._filters.append((column, "gt", value))
        return self

    def order(self, column, desc=False, **_k):
        self._order_col = column
        self._order_desc = desc
        return self

    def limit(self, n):
        self._limit = n
        return self

    def single(self):
        self._single = True
        return self

    def __getattr__(self, _name):  # range, maybe_single, etc.
        def method(*_a, **_k):
            return self

        return method

    # ── exécution ────────────────────────────────────────────────────────────
    def _matching(self):
        # Un filtre sur une colonne absente de la ligne est ignoré plutôt que
        # d'exclure la ligne — pratique pour des fixtures de test minimales
        # qui n'incluent que les colonnes pertinentes au scénario.
        rows = self._rows
        for col, op, val in self._filters:
            if op == "eq":
                rows = [r for r in rows if col not in r or str(r.get(col)) == str(val)]
            elif op == "lt":
                rows = [r for r in rows if col not in r or r.get(col) is None or r.get(col) < val]
            elif op == "gt":
                rows = [r for r in rows if col not in r or r.get(col) is None or r.get(col) > val]
        return rows

    def execute(self):
        # Chaque branche renvoie des *copies* des dicts stockés : comme le
        # vrai client postgrest (payload JSON désérialisé à chaque appel),
        # la donnée renvoyée par un appelant ne doit jamais être un alias
        # vivant sur l'état interne — sinon une mutation ultérieure d'une
        # ligne (ex: update de position A) se répercute silencieusement sur
        # une valeur déjà lue d'une autre ligne (ex: cur["position"] relu
        # après coup dans un swap de positions).
        if self._op == "insert":
            for row in self._values:
                row.setdefault("id", str(uuid.uuid4()))
                row.setdefault("created_at", "2026-07-05T10:00:00+00:00")
                self._rows.append(row)
            return SimpleNamespace(data=[dict(r) for r in self._values], count=len(self._values))

        if self._op == "upsert":
            conflict_cols = (self._on_conflict or "id").split(",")
            result_rows = []
            for values in self._values:
                existing = next(
                    (r for r in self._rows if all(r.get(c) == values.get(c) for c in conflict_cols)),
                    None,
                )
                if existing:
                    existing.update(values)
                    result_rows.append(dict(existing))
                else:
                    row = dict(values)
                    row.setdefault("id", str(uuid.uuid4()))
                    self._rows.append(row)
                    result_rows.append(dict(row))
            return SimpleNamespace(data=result_rows, count=len(result_rows))

        if self._op == "update":
            matched = self._matching()
            for row in matched:
                row.update(self._values)
            return SimpleNamespace(data=[dict(r) for r in matched], count=len(matched))

        if self._op == "delete":
            matched = self._matching()
            for row in matched:
                self._rows.remove(row)
            return SimpleNamespace(data=[dict(r) for r in matched], count=len(matched))

        matched = self._matching()
        if self._order_col is not None:
            matched = sorted(
                matched,
                key=lambda r: (r.get(self._order_col) is None, r.get(self._order_col)),
                reverse=self._order_desc,
            )
        if self._limit is not None:
            matched = matched[: self._limit]
        if self._single:
            return SimpleNamespace(data=(dict(matched[0]) if matched else None), count=len(matched))
        return SimpleNamespace(data=[dict(r) for r in matched], count=len(matched))


class TableAwareFake:
    def __init__(self, tables: dict[str, list[dict]]):
        self.tables = tables

    def table(self, name: str) -> TableQuery:
        return TableQuery(self.tables.setdefault(name, []))
