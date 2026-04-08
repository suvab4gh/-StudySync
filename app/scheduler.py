"""
scheduler.py – OR-Tools CP-SAT constraint solver for study schedule generation.

Problem formulation
-------------------
Decision variables:
    ``x[a][d]`` – integer, study hours allocated to assignment *a* on day *d*
                  (scaled by HOUR_SCALE to avoid floating-point in CP-SAT)

Hard constraints:
    C1  Daily capacity  – Σ_a x[a][d] ≤ daily_capacity[d]   ∀ d
    C2  Total hours     – Σ_d x[a][d] = required_hours[a]   ∀ a
    C3  Deadline        – x[a][d] = 0  for d > deadline[a]  ∀ a, d
    C4  Prerequisites   – Σ_{d≤t} x[prereq][d] = required[prereq]
                          must hold before any x[a][d] > 0 when t < d

Soft objectives (minimised via a weighted sum):
    O1  Cognitive load variance per day (reduce cognitive spikes)
    O2  Earliness bonus  – reward finishing before the deadline (reduce stress)

When the solver cannot satisfy all hard constraints (e.g., a deadline is
already in the past or there are not enough hours), it returns ``feasible=False``
with an explanatory warning instead of raising an exception.
"""

from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from typing import Any

from ortools.sat.python import cp_model  # type: ignore[import-untyped]

from app.schemas import (
    AssignmentType,
    DailyAvailability,
    ScheduleRequest,
    ScheduleResponse,
    StudyBlock,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# CP-SAT is integer-only; we scale hours by this factor to keep 15-minute
# precision while using integers internally.
HOUR_SCALE: int = 4  # 1 unit = 0.25 hours

# Minimum block size we will schedule in a single day (15 minutes)
MIN_BLOCK_UNITS: int = 1  # 1 × 0.25 h = 15 min

# Buffer days inserted before high-stakes assignments (cognitive_load >= 4)
BUFFER_DAYS_BEFORE_EXAM: int = 2

# Cognitive load weights used in the objective function (1-indexed to match schema)
_LOAD_WEIGHT: dict[int, int] = {1: 1, 2: 2, 3: 3, 4: 5, 5: 8}


# ---------------------------------------------------------------------------
# Date → day-index helpers
# ---------------------------------------------------------------------------


def _build_day_index(
    start: date, end: date
) -> tuple[list[date], dict[date, int]]:
    """
    Build a list of calendar days and a date→index lookup from *start* to *end*
    inclusive.  Returns ``(days, date_to_idx)``.
    """
    days: list[date] = []
    d = start
    while d <= end:
        days.append(d)
        d += timedelta(days=1)
    date_to_idx = {d: i for i, d in enumerate(days)}
    return days, date_to_idx


def _effective_daily_capacity(
    day: date,
    availability_map: dict[int, float],
    max_daily_hours: float,
) -> int:
    """
    Return the daily study-hour capacity for *day* in HOUR_SCALE units.

    Priority: per-weekday override (from ``ScheduleRequest.availability``)
    > global ``max_daily_hours`` cap.
    """
    wd = day.weekday()  # 0 = Monday … 6 = Sunday
    hours = min(availability_map.get(wd, max_daily_hours), max_daily_hours)
    return int(hours * HOUR_SCALE)


# ---------------------------------------------------------------------------
# Pre-processing helpers
# ---------------------------------------------------------------------------


def _resolve_deadline(assignment_due: date | None, last_day: date) -> date:
    """
    Return the effective deadline for scheduling.

    If the assignment has no due date (None) or the due date is after our
    planning horizon, we use the last day of the horizon.
    """
    if assignment_due is None or assignment_due > last_day:
        return last_day
    return assignment_due


def _build_prerequisite_map(
    titles: list[str],
    prerequisites_raw: list[list[str]],
) -> dict[int, list[int]]:
    """
    Convert string-based prerequisite lists into index-based adjacency lists.

    ``titles[i]`` → index *i* must be completed before index *j* whenever
    ``titles[i]`` appears in ``prerequisites_raw[j]``.
    """
    title_to_idx = {t: i for i, t in enumerate(titles)}
    result: dict[int, list[int]] = {i: [] for i in range(len(titles))}
    for j, prereqs in enumerate(prerequisites_raw):
        for prereq_title in prereqs:
            i = title_to_idx.get(prereq_title)
            if i is not None:
                result[j].append(i)
    return result


# ---------------------------------------------------------------------------
# OR-Tools model construction
# ---------------------------------------------------------------------------


def _build_cp_model(
    req: ScheduleRequest,
    days: list[date],
    date_to_idx: dict[date, int],
    availability_map: dict[int, float],
) -> tuple[cp_model.CpModel, dict[tuple[int, int], Any]]:
    """
    Construct the CP-SAT model.

    Returns ``(model, x)`` where ``x[(a, d)]`` is the decision variable
    representing study units allocated to assignment *a* on day *d*.
    """
    model = cp_model.CpModel()
    assignments = req.syllabus.assignments
    n_assignments = len(assignments)
    n_days = len(days)
    last_day = days[-1]

    # --- Build prerequisite adjacency ---
    titles = [a.title for a in assignments]
    prereqs_raw = [a.prerequisites for a in assignments]
    prereq_map = _build_prerequisite_map(titles, prereqs_raw)

    # --- Decision variables ---
    x: dict[tuple[int, int], Any] = {}
    for a_idx, assignment in enumerate(assignments):
        deadline = _resolve_deadline(assignment.due_date.value, last_day)
        deadline_idx = date_to_idx.get(deadline, n_days - 1)

        required_units = max(
            MIN_BLOCK_UNITS,
            int(math.ceil(assignment.estimated_hours * HOUR_SCALE)),
        )

        for d_idx in range(n_days):
            cap = _effective_daily_capacity(days[d_idx], availability_map, req.max_daily_hours)
            # Hard constraint C3: force to 0 after deadline
            if d_idx > deadline_idx:
                x[(a_idx, d_idx)] = model.new_constant(0)
            else:
                x[(a_idx, d_idx)] = model.new_int_var(0, cap, f"x_{a_idx}_{d_idx}")

        # Hard constraint C2: must schedule exactly the required units
        model.add(sum(x[(a_idx, d)] for d in range(n_days)) == required_units)

    # Hard constraint C1: daily capacity
    for d_idx in range(n_days):
        cap = _effective_daily_capacity(days[d_idx], availability_map, req.max_daily_hours)
        model.add(sum(x[(a, d_idx)] for a in range(n_assignments)) <= cap)

    # Hard constraint C4: prerequisites
    # Implement as: cumulative units of prerequisite >= its required total
    # before any units of the dependent assignment are scheduled.
    # We use a simplified approach: if assignment j has prerequisite i, then
    # for each day d, if x[j][d] > 0 then all units of i must be in days < d.
    # This is modelled with a boolean auxiliary variable.
    for j, prereq_indices in prereq_map.items():
        for i in prereq_indices:
            req_i = max(
                MIN_BLOCK_UNITS,
                int(math.ceil(assignments[i].estimated_hours * HOUR_SCALE)),
            )
            for d_idx in range(n_days):
                # cumulative units of i up to (but not including) d
                cum_i_before_d = sum(x[(i, dd)] for dd in range(d_idx))
                # If x[j][d] > 0 → cum_i_before_d must equal req_i
                # We use: x[j][d] * req_i <= cum_i_before_d * req_i  (equiv.)
                # Simplified: enforce that x[j][d] can be non-zero only if
                # cum(i, d) == req_i.
                # CP-SAT approach: introduce bool b = (x[j][d] > 0)
                # then add: b → (cum_i_before_d == req_i)
                b = model.new_bool_var(f"prereq_{j}_{i}_{d_idx}")
                model.add(x[(j, d_idx)] > 0).only_enforce_if(b)
                model.add(x[(j, d_idx)] == 0).only_enforce_if(b.Not())
                model.add(cum_i_before_d == req_i).only_enforce_if(b)

    # --- Objective: minimise cognitive load variance + reward earliness ---
    # O1: Penalise days with high total cognitive-load-weighted hours
    load_per_day: list[Any] = []
    for d_idx in range(n_days):
        day_load = sum(
            x[(a_idx, d_idx)] * _LOAD_WEIGHT.get(assignments[a_idx].cognitive_load, 3)
            for a_idx in range(n_assignments)
        )
        load_per_day.append(day_load)

    # O2: Earliness – reward finishing early by using (deadline_idx - d) as
    #     a bonus coefficient.  We subtract it from the objective.
    earliness_bonus: list[Any] = []
    for a_idx, assignment in enumerate(assignments):
        deadline = _resolve_deadline(assignment.due_date.value, last_day)
        deadline_idx = date_to_idx.get(deadline, n_days - 1)
        for d_idx in range(min(deadline_idx + 1, n_days)):
            bonus = max(0, deadline_idx - d_idx)
            earliness_bonus.append(x[(a_idx, d_idx)] * bonus)

    # Combine objectives (weighted sum)
    # Minimise: 10 * Σ load_per_day  –  1 * Σ earliness_bonus
    total_load = sum(load_per_day)
    total_earliness = sum(earliness_bonus) if earliness_bonus else 0

    model.minimize(10 * total_load - total_earliness)

    return model, x


# ---------------------------------------------------------------------------
# Solution extraction
# ---------------------------------------------------------------------------


def _extract_blocks(
    solver: cp_model.CpSolver,
    x: dict[tuple[int, int], Any],
    req: ScheduleRequest,
    days: list[date],
) -> list[StudyBlock]:
    """
    Read solution values and build ``StudyBlock`` objects.

    Only days with non-zero allocation are included.  Buffer blocks
    (auto-inserted review days before high-stakes exams) are flagged with
    ``is_buffer=True``.
    """
    assignments = req.syllabus.assignments
    blocks: list[StudyBlock] = []

    for a_idx, assignment in enumerate(assignments):
        for d_idx, day in enumerate(days):
            units = solver.value(x[(a_idx, d_idx)])
            if units <= 0:
                continue
            hours = units / HOUR_SCALE
            blocks.append(
                StudyBlock(
                    assignment_title=assignment.title,
                    assignment_type=assignment.assignment_type,
                    scheduled_date=day,
                    duration_hours=round(hours, 2),
                    cognitive_load=assignment.cognitive_load,
                    is_buffer=False,
                )
            )

    blocks.sort(key=lambda b: b.scheduled_date)
    return blocks


def _insert_buffer_blocks(
    blocks: list[StudyBlock],
    req: ScheduleRequest,
    days: list[date],
) -> list[StudyBlock]:
    """
    Insert review/buffer blocks in the BUFFER_DAYS_BEFORE_EXAM window before
    high-stakes assignments (cognitive_load >= 4).

    Buffer blocks are purely advisory metadata for the frontend; they do not
    affect constraint satisfaction.
    """
    high_stakes = [
        a for a in req.syllabus.assignments if a.cognitive_load >= 4
    ]
    buffer_blocks: list[StudyBlock] = []
    day_set = {d for d in days}

    for assignment in high_stakes:
        due = assignment.due_date.value
        if due is None:
            continue
        for offset in range(1, BUFFER_DAYS_BEFORE_EXAM + 1):
            buf_day = due - timedelta(days=offset)
            if buf_day in day_set:
                buffer_blocks.append(
                    StudyBlock(
                        assignment_title=f"[Review] {assignment.title}",
                        assignment_type=assignment.assignment_type,
                        scheduled_date=buf_day,
                        duration_hours=1.0,
                        cognitive_load=assignment.cognitive_load,
                        is_buffer=True,
                    )
                )

    all_blocks = blocks + buffer_blocks
    all_blocks.sort(key=lambda b: b.scheduled_date)
    return all_blocks


# ---------------------------------------------------------------------------
# Planning horizon helper
# ---------------------------------------------------------------------------


def _compute_horizon(req: ScheduleRequest) -> tuple[date, date]:
    """
    Determine the start and end of the planning window.

    Start: ``req.start_date`` (today by default)
    End  : the latest assignment deadline in the syllabus, or start + 16 weeks
           if no deadlines are available.
    """
    start = req.start_date
    due_dates = [
        a.due_date.value
        for a in req.syllabus.assignments
        if a.due_date.value is not None
    ]
    if due_dates:
        end = max(due_dates)
        # Ensure at least 1 day of horizon
        if end < start:
            end = start + timedelta(days=7)
    else:
        end = start + timedelta(weeks=16)
    return start, end


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_schedule(req: ScheduleRequest) -> ScheduleResponse:
    """
    Solve the study scheduling problem and return a ``ScheduleResponse``.

    Steps:
    1. Validate inputs and compute the planning horizon.
    2. Build the availability map from per-weekday overrides.
    3. Construct the CP-SAT model.
    4. Run the solver with a timeout.
    5. If feasible, extract blocks and insert advisory buffer blocks.
    6. If infeasible, return ``feasible=False`` with diagnostic warnings.
    """
    warnings: list[str] = []
    assignments = req.syllabus.assignments

    if not assignments:
        return ScheduleResponse(
            feasible=False,
            warnings=["No assignments found in the syllabus extraction"],
            solver_status="no_assignments",
        )

    start, end = _compute_horizon(req)
    days, date_to_idx = _build_day_index(start, end)
    n_days = len(days)

    # Build per-weekday availability map
    availability_map: dict[int, float] = {}
    for avail in req.availability:
        availability_map[avail.weekday] = avail.max_hours

    # Warn about assignments with past or missing deadlines
    for a in assignments:
        if a.due_date.value is None:
            warnings.append(
                f"'{a.title}' has no due date; scheduled to end of horizon"
            )
        elif a.due_date.value < start:
            warnings.append(
                f"'{a.title}' deadline {a.due_date.value} is before start date "
                f"{start} – will be placed at end of horizon"
            )

    # Build and solve
    model, x = _build_cp_model(req, days, date_to_idx, availability_map)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = req.solver_timeout_seconds
    solver.parameters.log_search_progress = False

    status = solver.solve(model)
    status_name = solver.status_name(status)

    logger.info("CP-SAT solver status: %s", status_name)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        blocks = _extract_blocks(solver, x, req, days)
        blocks = _insert_buffer_blocks(blocks, req, days)
        total_hours = sum(
            b.duration_hours for b in blocks if not b.is_buffer
        )
        if status == cp_model.FEASIBLE:
            warnings.append("Solution is feasible but may not be optimal (solver timed out)")
        return ScheduleResponse(
            feasible=True,
            blocks=blocks,
            total_study_hours=round(total_hours, 2),
            warnings=warnings,
            solver_status=status_name,
        )
    else:
        # Infeasible or unknown
        warnings.append(
            "Solver could not find a feasible schedule – check deadlines and daily availability"
        )
        if status == cp_model.INFEASIBLE:
            warnings.append(
                "Constraints are unsatisfiable: there may not be enough hours before deadlines"
            )
        return ScheduleResponse(
            feasible=False,
            warnings=warnings,
            solver_status=status_name,
        )
