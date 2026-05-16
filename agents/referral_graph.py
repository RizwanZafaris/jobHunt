"""
agents/referral_graph.py — Referral-graph path-finder + import logic.

This module is the engine behind the Network surface (audit ref P1.1 in
docs/AUDIT_360_SYNTHESIS.md). It owns:

  1.  ReferralGraph — wraps the Supabase service-role client and lazily
      builds an in-memory NetworkX DiGraph from `edges` rows so the
      shortest-path search runs in process and not in Postgres.

  2.  find_paths — core algorithm. For a target_company, find the
      strongest 1- and 2-hop intro paths from the user's "me" node to
      anyone currently employed at that company.

  3.  import_linkedin_csv — parse LinkedIn's connections CSV export and
      seed `people` + `me_first_degree` edges with strength 0.7.

  4.  add_introduction_path — manually record "Bob can introduce you to
      Alice at Stripe" as an `introduced_by` edge with high strength.

  5.  populate_target_company_employees — refresh the denormalised cache
      that maps every target_company → people currently employed there
      (fuzzy company-name match). Called after CSV import or via cron.

Networking model
----------------
Edges are directed and typed (kind in {me_first_degree, colleague,
classmate, friend, family, introduced_by, same_company_overlap,
referenced_in_outreach}) with strength in [0, 1]. The path-finder
treats edge weight = (1 - strength), so Dijkstra returns the path
with the highest cumulative trust.

NetworkX is the right tool here because:
  - graphs are user-scoped (≤ 5k people for V1) — fits in memory
  - Postgres recursive CTE is awkward to maintain over RLS
  - we need shortest_path + simple_paths variants for "show top 5 paths"

Use NetworkX>=3.4 (see _pending_deps_referral.txt).

Privacy
-------
V1 imports CSV exports the user owns. The user_id of every row written
here is the importing user; service-role bypasses RLS. See
api/NETWORK.md "Privacy & TOS".
"""
from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ── Pydantic response models ──────────────────────────────────────────────
class Person(BaseModel):
    """A person row, trimmed for API/path responses."""
    id: UUID
    full_name: str
    linkedin_url: Optional[str] = None
    headline: Optional[str] = None
    profile_picture_url: Optional[str] = None
    current_company: Optional[str] = None
    current_role: Optional[str] = None
    source: Optional[str] = None


class ReferralPath(BaseModel):
    """One ranked intro path from `me` to `target_employee`.

    `path` is the full ordered list of Person nodes including endpoints.
    `hop_count` is len(path) - 1. `total_strength` is the geometric mean
    of the edge strengths (so a single weak link drags the score down,
    matching how real intros work). `target_employee` is path[-1].
    """
    target_employee: Person
    target_company_id: UUID
    target_company_name: Optional[str] = None
    hop_count: int
    total_strength: float
    path: list[Person]
    edge_kinds: list[str] = Field(default_factory=list)


# ── Constants ─────────────────────────────────────────────────────────────
ME_SOURCE_TAG: str = "me"
DEFAULT_MAX_HOPS: int = 2
DEFAULT_MIN_STRENGTH: float = 0.3
LINKEDIN_CSV_FIRST_DEGREE_STRENGTH: float = 0.7
INTRODUCTION_DEFAULT_STRENGTH: float = 0.85

# LinkedIn's standard CSV columns (export from
# https://www.linkedin.com/mypreferences/d/data-substitutions). The exact
# header capitalisation has been stable for years.
LINKEDIN_CSV_COLUMNS: tuple[str, ...] = (
    "First Name",
    "Last Name",
    "URL",
    "Email Address",
    "Company",
    "Position",
    "Connected On",
)


# ──────────────────────────────────────────────────────────────────────────
# ReferralGraph — the main engine
# ──────────────────────────────────────────────────────────────────────────
class ReferralGraph:
    """User-scoped graph operations.

    One instance per user_id per request. The caller owns the lifetime;
    the graph is rebuilt on demand from Supabase. Caching across requests
    is intentionally avoided in V1 — graphs are small enough to rebuild
    per request and avoid stale-cache bugs.
    """

    def __init__(self, user_id: UUID | str) -> None:
        self.user_id: UUID = user_id if isinstance(user_id, UUID) else UUID(str(user_id))
        # Lazily-imported supabase client; module import keeps clean for tests.
        from db.client import get_supabase
        self._db = get_supabase()
        self._graph = None  # type: Any  # nx.DiGraph, populated on demand
        self._person_index: dict[str, dict[str, Any]] = {}

    # ── Building the in-memory graph ──────────────────────────────────────
    def _build_graph(self) -> Any:
        """Load all of the user's people + edges into a NetworkX DiGraph.

        Returns the graph. Idempotent — caches on the instance.
        """
        if self._graph is not None:
            return self._graph

        # Local import — keeps NetworkX out of cold paths and lets the rest
        # of the module import even if networkx isn't installed yet.
        try:
            import networkx as nx
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "networkx>=3.4 is required for the referral graph. "
                "See _pending_deps_referral.txt."
            ) from exc

        people_rows = (
            self._db.table("people")
            .select("id, full_name, linkedin_url, headline, profile_picture_url, source")
            .eq("user_id", str(self.user_id))
            .execute()
        ).data or []

        edges_rows = (
            self._db.table("edges")
            .select("src_person, dst_person, kind, strength")
            .eq("user_id", str(self.user_id))
            .execute()
        ).data or []

        graph = nx.DiGraph()
        for row in people_rows:
            pid = str(row["id"])
            self._person_index[pid] = row
            graph.add_node(pid, **row)

        for row in edges_rows:
            src = str(row["src_person"])
            dst = str(row["dst_person"])
            strength = float(row.get("strength") or 0.5)
            # Higher strength → smaller weight. Floor at 1e-3 so a perfect
            # 1.0 edge isn't zero-weight (Dijkstra prefers strict positives).
            weight = max(1.0 - strength, 1e-3)
            existing = graph.get_edge_data(src, dst)
            if existing is None or weight < existing["weight"]:
                graph.add_edge(
                    src, dst,
                    weight=weight,
                    strength=strength,
                    kind=row.get("kind"),
                )

        self._graph = graph
        return graph

    def _person_to_model(self, pid: str) -> Person:
        row = self._person_index.get(pid) or {}
        # Best-effort: fetch the current employment for the headline if
        # we don't already have it cached.
        current_company, current_role = self._current_employment(pid)
        return Person(
            id=UUID(pid),
            full_name=row.get("full_name") or "(unknown)",
            linkedin_url=row.get("linkedin_url"),
            headline=row.get("headline"),
            profile_picture_url=row.get("profile_picture_url"),
            current_company=current_company,
            current_role=current_role,
            source=row.get("source"),
        )

    def _current_employment(self, person_id: str) -> tuple[Optional[str], Optional[str]]:
        """Look up the person's current (is_current=true) employment.

        Returns (company_name, role). Both None if no current employment
        is recorded. We swallow errors and degrade gracefully — the
        path-finder is still useful without job titles.
        """
        try:
            res = (
                self._db.table("employments")
                .select("company_name, role")
                .eq("person_id", person_id)
                .eq("is_current", True)
                .limit(1)
                .execute()
            )
            rows = res.data or []
            if rows:
                return rows[0].get("company_name"), rows[0].get("role")
        except Exception as exc:  # pragma: no cover — non-fatal
            logger.debug("current employment lookup failed for %s: %s", person_id, exc)
        return None, None

    # ── "me" node helper ──────────────────────────────────────────────────
    def me_person_id(self) -> Optional[str]:
        """Return the id of the row tagged source='me' for this user.

        Returns None if the user hasn't been seeded yet — the caller
        should create one (see ensure_me_node).
        """
        res = (
            self._db.table("people")
            .select("id")
            .eq("user_id", str(self.user_id))
            .eq("source", "me")
            .limit(1)
            .execute()
        )
        rows = res.data or []
        return str(rows[0]["id"]) if rows else None

    def ensure_me_node(self, full_name: str, linkedin_url: Optional[str] = None) -> str:
        """Get-or-create the user's own 'me' row. Returns the id as str.

        Idempotent — safe to call from CSV import. Two parallel callers
        will race once but the (user_id, linkedin_url) unique partial
        index + the 'me' source filter resolves to one row.
        """
        existing = self.me_person_id()
        if existing:
            return existing
        payload: dict[str, Any] = {
            "id": str(uuid4()),
            "user_id": str(self.user_id),
            "full_name": full_name,
            "source": "me",
        }
        if linkedin_url:
            payload["linkedin_url"] = linkedin_url
        result = self._db.table("people").insert(payload).execute()
        rows = result.data or []
        if not rows:
            raise RuntimeError("Failed to create 'me' node for user")
        return str(rows[0]["id"])

    # ── Path-finder ───────────────────────────────────────────────────────
    def find_paths(
        self,
        target_company_id: UUID | str,
        max_hops: int = DEFAULT_MAX_HOPS,
        min_strength: float = DEFAULT_MIN_STRENGTH,
        limit: int = 10,
    ) -> list[ReferralPath]:
        """Return ranked intro paths from `me` to people at the target.

        Algorithm (5-step):
          1. Resolve `me` node id; if missing, return [] (no graph yet).
          2. Read target_company_employees rows for the target — this is
             the candidate frontier (people we know who work there).
          3. Build the graph; skip if me → target hop would exceed
             max_hops in raw nodes.
          4. For each candidate, run Dijkstra (weight = 1 - strength);
             reject paths whose total geometric-mean strength is below
             min_strength or whose hop count exceeds max_hops.
          5. Sort by (-total_strength, hop_count) and return the top
             `limit` results.
        """
        try:
            import networkx as nx
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "networkx>=3.4 is required; see _pending_deps_referral.txt"
            ) from exc

        target_id = str(target_company_id)
        me_id = self.me_person_id()
        if me_id is None:
            return []

        # Candidate set: everyone the user knows who currently works at the target.
        tce_rows = (
            self._db.table("target_company_employees")
            .select("person_id, role, role_seniority, current_since")
            .eq("user_id", str(self.user_id))
            .eq("target_company_id", target_id)
            .execute()
        ).data or []
        if not tce_rows:
            return []

        graph = self._build_graph()
        if me_id not in graph:
            return []

        # Look up the human-readable company name once if we can.
        target_company_name = self._target_company_name(target_id)

        results: list[ReferralPath] = []
        for tce in tce_rows:
            target_person_id = str(tce["person_id"])
            if target_person_id not in graph:
                continue
            try:
                node_path: list[str] = nx.shortest_path(
                    graph, source=me_id, target=target_person_id, weight="weight"
                )
            except nx.NetworkXNoPath:
                continue
            except nx.NodeNotFound:
                continue

            hop_count = len(node_path) - 1
            if hop_count > max_hops or hop_count < 1:
                continue

            edge_strengths: list[float] = []
            edge_kinds: list[str] = []
            for src, dst in zip(node_path, node_path[1:]):
                ed = graph.get_edge_data(src, dst) or {}
                edge_strengths.append(float(ed.get("strength") or 0.0))
                edge_kinds.append(str(ed.get("kind") or ""))

            # Geometric mean — one weak hop tanks the path. Avoids the
            # arithmetic-mean trap where a 0.95 + 0.05 chain looks "ok".
            if not edge_strengths:
                continue
            product = 1.0
            for s in edge_strengths:
                product *= max(s, 1e-6)
            geom_mean = product ** (1.0 / len(edge_strengths))
            if geom_mean < min_strength:
                continue

            path_models = [self._person_to_model(pid) for pid in node_path]
            results.append(ReferralPath(
                target_employee=path_models[-1],
                target_company_id=UUID(target_id),
                target_company_name=target_company_name,
                hop_count=hop_count,
                total_strength=round(geom_mean, 4),
                path=path_models,
                edge_kinds=edge_kinds,
            ))

        results.sort(key=lambda p: (-p.total_strength, p.hop_count))
        return results[:limit]

    def _target_company_name(self, target_company_id: str) -> Optional[str]:
        """Best-effort lookup of the target company's display name.

        B11: was querying a non-existent `target_companies` table. The
        real schema marks targets on the `companies` table via `is_target`.
        target_company_id here is `companies.id`.
        """
        try:
            res = (
                self._db.table("companies")
                .select("name")
                .eq("id", target_company_id)
                .limit(1)
                .execute()
            )
            rows = res.data or []
            if rows:
                return rows[0].get("name")
        except Exception:
            return None
        return None

    # ── Mutations: people + edges ─────────────────────────────────────────
    def upsert_person(
        self,
        full_name: str,
        linkedin_url: Optional[str] = None,
        email: Optional[str] = None,
        headline: Optional[str] = None,
        source: str = "manual",
        notes: Optional[str] = None,
        location: Optional[str] = None,
    ) -> str:
        """Create-or-merge a person; returns the id as str.

        Merge key is (user_id, linkedin_url) when linkedin_url is set;
        otherwise we always insert (no auto-merge on name alone — too
        ambiguous). Caller is expected to dedupe by name when wanted.
        """
        if linkedin_url:
            existing = (
                self._db.table("people")
                .select("id")
                .eq("user_id", str(self.user_id))
                .eq("linkedin_url", linkedin_url)
                .limit(1)
                .execute()
            ).data or []
            if existing:
                return str(existing[0]["id"])

        payload: dict[str, Any] = {
            "id": str(uuid4()),
            "user_id": str(self.user_id),
            "full_name": full_name,
            "source": source,
        }
        if linkedin_url: payload["linkedin_url"] = linkedin_url
        if email: payload["email"] = email
        if headline: payload["headline"] = headline
        if notes: payload["notes"] = notes
        if location: payload["location"] = location

        result = self._db.table("people").insert(payload).execute()
        rows = result.data or []
        if not rows:
            raise RuntimeError(f"Failed to insert person {full_name!r}")
        return str(rows[0]["id"])

    def upsert_employment(
        self,
        person_id: str,
        company_name: str,
        role: Optional[str] = None,
        role_seniority: Optional[str] = None,
        started_at: Optional[date] = None,
        ended_at: Optional[date] = None,
        company_id: Optional[str] = None,
        source: Optional[str] = None,
    ) -> str:
        """Insert an employment row. Doesn't try to merge — duplicates
        are cheap and the worker reconciles current/non-current state.
        """
        payload: dict[str, Any] = {
            "id": str(uuid4()),
            "person_id": person_id,
            "company_name": company_name,
        }
        if role: payload["role"] = role
        if role_seniority: payload["role_seniority"] = role_seniority
        if started_at: payload["started_at"] = started_at.isoformat()
        if ended_at: payload["ended_at"] = ended_at.isoformat()
        if company_id: payload["company_id"] = company_id
        if source: payload["source"] = source

        result = self._db.table("employments").insert(payload).execute()
        rows = result.data or []
        if not rows:
            raise RuntimeError("Failed to insert employment")
        return str(rows[0]["id"])

    def upsert_edge(
        self,
        src_person: str,
        dst_person: str,
        kind: str,
        strength: float = 0.5,
        evidence: Optional[dict[str, Any]] = None,
    ) -> str:
        """Insert (or no-op on conflict) one edge. Returns the row id.

        The unique index on (user_id, src, dst, kind) makes this a true
        upsert without us having to check first.
        """
        if src_person == dst_person:
            raise ValueError("self-edges are not allowed")
        payload = {
            "user_id": str(self.user_id),
            "src_person": src_person,
            "dst_person": dst_person,
            "kind": kind,
            "strength": float(max(0.0, min(1.0, strength))),
            "evidence": evidence or {},
        }
        result = (
            self._db.table("edges")
            .upsert(payload, on_conflict="user_id,src_person,dst_person,kind")
            .execute()
        )
        rows = result.data or []
        if rows:
            return str(rows[0]["id"])
        # Some PostgREST versions don't return the upserted row — fetch.
        existing = (
            self._db.table("edges")
            .select("id")
            .eq("user_id", str(self.user_id))
            .eq("src_person", src_person)
            .eq("dst_person", dst_person)
            .eq("kind", kind)
            .limit(1)
            .execute()
        ).data or []
        if existing:
            return str(existing[0]["id"])
        raise RuntimeError("upsert_edge returned no rows")

    # ── Manual introduction path ──────────────────────────────────────────
    def add_introduction_path(
        self,
        src_person_id: str,
        dst_person_id: str,
        evidence: str,
        strength: float = INTRODUCTION_DEFAULT_STRENGTH,
    ) -> dict[str, Any]:
        """Record "src can introduce me to dst".

        Creates one `introduced_by` edge from src → dst with high default
        strength and a free-text evidence note. The dashboard surface
        calls this when the user manually says "Bob can intro me to
        Alice".

        Returns the created row as a plain dict.
        """
        edge_id = self.upsert_edge(
            src_person=src_person_id,
            dst_person=dst_person_id,
            kind="introduced_by",
            strength=strength,
            evidence={"note": evidence, "ts": _utcnow_iso()},
        )
        return {
            "id": edge_id,
            "src_person": src_person_id,
            "dst_person": dst_person_id,
            "kind": "introduced_by",
            "strength": strength,
        }

    # ── LinkedIn CSV import ───────────────────────────────────────────────
    def import_linkedin_csv(
        self,
        csv_path_or_text: str,
        is_path: bool = True,
    ) -> dict[str, Any]:
        """Parse a LinkedIn connections CSV and seed the graph.

        Behaviour:
          - Each non-empty row becomes one `people` row (source='linkedin_csv').
          - For each, one `me_first_degree` edge (me → person) is added with
            strength 0.7. (We don't infer the reverse — strong-tie symmetry
            isn't always real, and the path-finder reads the directed graph.)
          - If the row has Company + Position, an `employments` row with
            is_current=true (no ended_at) is inserted alongside.
          - Existing connections (same linkedin_url) are merged, not
            duplicated.

        Args:
          csv_path_or_text: filesystem path OR the raw CSV text
          is_path: True if the first arg is a path, False if it's the text

        Returns: dict with keys
          {imported, skipped, edges_created, employments_created, errors}
        """
        text = self._read_csv(csv_path_or_text, is_path=is_path)
        reader = csv.DictReader(io.StringIO(text))

        imported = 0
        skipped = 0
        edges_created = 0
        employments_created = 0
        errors: list[str] = []

        # Resolve the user's own row up front. The user's full_name comes
        # from the users table since the CSV doesn't carry it.
        me_full_name = self._user_full_name() or "Me"
        me_id = self.ensure_me_node(full_name=me_full_name)

        for row in reader:
            try:
                first = (row.get("First Name") or "").strip()
                last = (row.get("Last Name") or "").strip()
                full_name = f"{first} {last}".strip()
                if not full_name:
                    skipped += 1
                    continue

                linkedin_url = (row.get("URL") or "").strip() or None
                email = (row.get("Email Address") or "").strip() or None
                company = (row.get("Company") or "").strip() or None
                position = (row.get("Position") or "").strip() or None
                connected_on = (row.get("Connected On") or "").strip() or None

                person_id = self.upsert_person(
                    full_name=full_name,
                    linkedin_url=linkedin_url,
                    email=email,
                    headline=position,
                    source="linkedin_csv",
                    notes=f"connected_on={connected_on}" if connected_on else None,
                )
                imported += 1

                # me → person, first-degree connection.
                self.upsert_edge(
                    src_person=me_id,
                    dst_person=person_id,
                    kind="me_first_degree",
                    strength=LINKEDIN_CSV_FIRST_DEGREE_STRENGTH,
                    evidence={"source": "linkedin_csv", "connected_on": connected_on},
                )
                edges_created += 1

                if company:
                    self.upsert_employment(
                        person_id=person_id,
                        company_name=company,
                        role=position,
                        # ended_at unset → is_current = TRUE (generated col).
                        source="linkedin_csv",
                    )
                    employments_created += 1
            except Exception as exc:
                errors.append(f"row error: {exc!r}")

        return {
            "imported": imported,
            "skipped": skipped,
            "edges_created": edges_created,
            "employments_created": employments_created,
            "errors": errors[:20],  # cap
        }

    @staticmethod
    def _read_csv(csv_path_or_text: str, is_path: bool) -> str:
        if not is_path:
            return csv_path_or_text
        with open(csv_path_or_text, "r", encoding="utf-8-sig") as f:
            return f.read()

    def _user_full_name(self) -> Optional[str]:
        """Read the user's display name from `users` for the 'me' node."""
        try:
            res = (
                self._db.table("users")
                .select("full_name")
                .eq("id", str(self.user_id))
                .limit(1)
                .execute()
            )
            rows = res.data or []
            if rows and rows[0].get("full_name"):
                return rows[0]["full_name"]
        except Exception:
            pass
        return None

    # ── target_company_employees refresh ──────────────────────────────────
    def populate_target_company_employees(
        self,
        fuzzy_threshold: float = 0.4,
        prune_stale: bool = True,
    ) -> dict[str, Any]:
        """Refresh `target_company_employees` for this user.

        For every target_company in target_companies(user_id=this user),
        find every person currently employed at that company (fuzzy match
        on `employments.company_name` via pg_trgm similarity) and upsert
        one row per (target, person) into target_company_employees.

        `fuzzy_threshold` is a similarity in [0, 1] passed to pg_trgm's
        word_similarity operator. 0.4 catches "Stripe" ↔ "Stripe, Inc."
        without confusing it with "Stripes Brewing".

        Args:
          fuzzy_threshold: min pg_trgm similarity to count as a match.
          prune_stale: if True, delete target_company_employees rows
            whose last_seen_at is older than this run's start time.
        """
        run_started = _utcnow_iso()

        # B11: was querying non-existent `target_companies`. Real schema:
        # targets are `companies` rows with is_target=true for this user.
        # target_company_id elsewhere is companies.id.
        targets = (
            self._db.table("companies")
            .select("id, name")
            .eq("user_id", str(self.user_id))
            .eq("is_target", True)
            .neq("is_phantom", True)
            .execute()
        ).data or []

        matched = 0
        upserted = 0
        per_target: list[dict[str, Any]] = []

        for tgt in targets:
            tgt_id = str(tgt["id"])
            tgt_name = tgt.get("name") or ""
            if not tgt_name:
                continue

            # Pull the user's people-employments via a join through people.
            # Two-step lookup keeps us inside the supabase-py REST client.
            people_rows = (
                self._db.table("people")
                .select("id")
                .eq("user_id", str(self.user_id))
                .execute()
            ).data or []
            person_ids = [str(p["id"]) for p in people_rows]
            if not person_ids:
                continue

            employments = (
                self._db.table("employments")
                .select("person_id, company_name, role, role_seniority, started_at")
                .in_("person_id", person_ids)
                .eq("is_current", True)
                .execute()
            ).data or []

            tgt_lower = tgt_name.lower()
            for emp in employments:
                cn = (emp.get("company_name") or "").lower()
                if not cn:
                    continue
                # Cheap python fuzzy: prefer exact substring; else fall
                # back to a token-overlap heuristic. Pg_trgm-grade scoring
                # would require an RPC, which is overkill for V1.
                if cn == tgt_lower or tgt_lower in cn or cn in tgt_lower:
                    similarity = 1.0
                else:
                    similarity = _jaccard(cn, tgt_lower)
                if similarity < fuzzy_threshold:
                    continue
                matched += 1
                payload = {
                    "user_id": str(self.user_id),
                    "target_company_id": tgt_id,
                    "person_id": emp["person_id"],
                    "role": emp.get("role"),
                    "role_seniority": emp.get("role_seniority"),
                    "current_since": emp.get("started_at"),
                    "last_seen_at": run_started,
                }
                self._db.table("target_company_employees").upsert(
                    payload,
                    on_conflict="target_company_id,person_id",
                ).execute()
                upserted += 1

            per_target.append({
                "target_company_id": tgt_id,
                "name": tgt_name,
                "matches": sum(
                    1 for emp in employments
                    if (
                        emp.get("company_name") or ""
                    ).lower() == tgt_lower
                ),
            })

        pruned = 0
        if prune_stale:
            stale = (
                self._db.table("target_company_employees")
                .delete()
                .eq("user_id", str(self.user_id))
                .lt("last_seen_at", run_started)
                .execute()
            ).data or []
            pruned = len(stale)

        return {
            "targets_scanned": len(targets),
            "matched": matched,
            "upserted": upserted,
            "pruned": pruned,
            "per_target": per_target,
            "started_at": run_started,
        }


# ── Helpers ───────────────────────────────────────────────────────────────
def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jaccard(a: str, b: str) -> float:
    """Token-Jaccard overlap on lowercased word sets. Used as a cheap
    company-name fuzzy match. Punctuation-tolerant (split on non-alnum).
    Returns 0..1.
    """
    import re
    ta = {t for t in re.split(r"[^a-z0-9]+", a) if t}
    tb = {t for t in re.split(r"[^a-z0-9]+", b) if t}
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    union = ta | tb
    return len(inter) / len(union)


# ── Convenience top-level functions ───────────────────────────────────────
# These wrap a fresh ReferralGraph(user_id) so callers don't need to
# instantiate the class for one-shot calls.

def find_paths(
    user_id: UUID | str,
    target_company_id: UUID | str,
    max_hops: int = DEFAULT_MAX_HOPS,
    min_strength: float = DEFAULT_MIN_STRENGTH,
    limit: int = 10,
) -> list[ReferralPath]:
    return ReferralGraph(user_id).find_paths(
        target_company_id=target_company_id,
        max_hops=max_hops,
        min_strength=min_strength,
        limit=limit,
    )


def import_linkedin_csv(user_id: UUID | str, csv_path: str) -> dict[str, Any]:
    return ReferralGraph(user_id).import_linkedin_csv(csv_path, is_path=True)


def import_linkedin_csv_text(user_id: UUID | str, csv_text: str) -> dict[str, Any]:
    return ReferralGraph(user_id).import_linkedin_csv(csv_text, is_path=False)


def add_introduction_path(
    user_id: UUID | str,
    src_person_id: str,
    dst_person_id: str,
    evidence: str,
) -> dict[str, Any]:
    return ReferralGraph(user_id).add_introduction_path(
        src_person_id=src_person_id,
        dst_person_id=dst_person_id,
        evidence=evidence,
    )


def populate_target_company_employees(user_id: UUID | str) -> dict[str, Any]:
    return ReferralGraph(user_id).populate_target_company_employees()
