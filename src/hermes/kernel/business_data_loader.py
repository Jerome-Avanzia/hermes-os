"""Business Data Loader — reads businesses/{business}/ into typed objects.

Follows the Business Knowledge Specification v1.1.0.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from hermes.models import (
    Bottleneck,
    Business,
    Decision,
    Experiment,
    Goal,
    KPI,
    Lesson,
    Opportunity,
    Strategy,
)

# JSON Schema enum values for warning detection
_BOTTLENECK_CATEGORIES = frozenset(
    {"product", "marketing", "sales", "operations", "finance", "technology"}
)
_BOTTLENECK_STATUSES = frozenset({"open", "mitigating", "resolved"})
_DECISION_STATUSES = frozenset({"proposed", "approved", "implemented", "reversed", "closed"})
_EXPERIMENT_STATUSES = frozenset({"planned", "running", "completed", "cancelled"})
_GOAL_STATUSES = frozenset({"planned", "active", "completed", "cancelled"})
_KPI_STATUSES = frozenset({"on_track", "at_risk", "off_track"})
_LESSON_SOURCES = frozenset({"decision", "experiment", "project", "incident", "review"})
_OPPORTUNITY_STATUSES = frozenset(
    {"backlog", "planned", "active", "completed", "rejected"}
)
_IMPACT_EFFORT = frozenset({"low", "medium", "high"})


@dataclass(slots=True)
class BusinessData:
    """Complete typed output from loading a business directory."""

    business: Business | None = None
    strategy: Strategy | None = None
    goals: list[Goal] = field(default_factory=list)
    kpis: list[KPI] = field(default_factory=list)
    bottlenecks: list[Bottleneck] = field(default_factory=list)
    opportunities: list[Opportunity] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    experiments: list[Experiment] = field(default_factory=list)
    lessons: list[Lesson] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# -- Normalization ------------------------------------------------------------


def _normalize_status(value: str) -> str:
    """Lowercase, spaces to underscores.  Never remap business meaning."""
    return value.strip().lower().replace(" ", "_")


# -- Prose extraction ---------------------------------------------------------


def _extract_section(text: str, heading: str) -> str | None:
    """Extract prose from a ``## heading`` section as a single paragraph."""
    pattern = rf"^##\s+{re.escape(heading)}\s*$"
    lines = text.split("\n")
    capturing = False
    result_parts: list[str] = []
    for line in lines:
        if re.match(pattern, line.strip()):
            capturing = True
            continue
        if capturing:
            stripped = line.strip()
            if stripped.startswith("## ") or stripped.startswith("# "):
                break
            if re.match(r"^-{4,}$", stripped):
                break
            if stripped:
                result_parts.append(stripped)
    return " ".join(result_parts) if result_parts else None


# -- Pandoc table parsing -----------------------------------------------------


def _is_separator(line: str) -> bool:
    """A separator line has multiple dash groups separated by spaces."""
    stripped = line.strip()
    if not stripped or not all(c in "-  " for c in stripped):
        return False
    groups = re.findall(r"-+", stripped)
    return len(groups) >= 2


def _is_full_boundary(line: str) -> bool:
    """A boundary line is one continuous group of dashes (10+ chars)."""
    stripped = line.strip()
    return bool(stripped) and bool(re.match(r"^-{10,}$", stripped))


def _parse_column_boundaries(separator: str) -> list[tuple[int, int]]:
    """Return (start, end) positions for each dash group in the separator."""
    columns: list[tuple[int, int]] = []
    in_dash = False
    col_start = 0
    for j, ch in enumerate(separator):
        if ch == "-" and not in_dash:
            col_start = j
            in_dash = True
        elif ch != "-" and in_dash:
            columns.append((col_start, j))
            in_dash = False
    if in_dash:
        columns.append((col_start, len(separator)))
    return columns


def _parse_table(text: str, section_heading: str) -> list[dict[str, str]]:
    """Parse a pandoc fixed-width table under the given ``## heading``.

    Handles both full-boundary format (opening/closing dash lines) and
    simple format (header → separator → data).
    """
    lines = text.split("\n")

    # 1. Find the section heading
    section_start = None
    for i, line in enumerate(lines):
        if line.strip() == f"## {section_heading}":
            section_start = i
            break
    if section_start is None:
        return []

    # 2. Find the separator line (multiple dash groups)
    separator_idx = None
    for i in range(section_start + 1, len(lines)):
        stripped = lines[i].strip()
        if not stripped:
            continue
        if stripped.startswith("## ") or stripped.startswith("# "):
            break
        if _is_separator(lines[i]):
            separator_idx = i
            break
    if separator_idx is None:
        return []

    # 3. Header is the non-blank line immediately before the separator
    header_idx = separator_idx - 1
    while header_idx > section_start and not lines[header_idx].strip():
        header_idx -= 1
    if header_idx <= section_start:
        return []

    # 4. Parse column boundaries and names
    col_bounds = _parse_column_boundaries(lines[separator_idx])
    header_line = lines[header_idx]
    col_names: list[str] = []
    for start, end in col_bounds:
        name = header_line[start:end].strip() if start < len(header_line) else ""
        col_names.append(name)

    # 5. Find data end
    data_start = separator_idx + 1
    data_end = len(lines)
    for i in range(data_start, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith("## ") or stripped.startswith("# "):
            data_end = i
            break
        if _is_full_boundary(lines[i]):
            data_end = i
            break
        if re.match(r"^-{4,}$", stripped):
            data_end = i
            break

    # 6. Group data lines into logical rows (separated by blank lines)
    logical_rows: list[list[str]] = []
    current_row: list[str] = []
    for line in lines[data_start:data_end]:
        if not line.strip():
            if current_row:
                logical_rows.append(current_row)
                current_row = []
        else:
            current_row.append(line)
    if current_row:
        logical_rows.append(current_row)

    # 7. Extract cell values
    result: list[dict[str, str]] = []
    for row_lines in logical_rows:
        cells: dict[str, str] = {}
        for idx, (start, end) in enumerate(col_bounds):
            if idx >= len(col_names):
                continue
            parts: list[str] = []
            for line in row_lines:
                if start < len(line):
                    cell_text = line[start : min(end, len(line))].strip()
                    if cell_text:
                        parts.append(cell_text)
            cells[col_names[idx]] = " ".join(parts)
        result.append(cells)

    return result


# -- Loader -------------------------------------------------------------------


class BusinessDataLoader:
    """Loads business knowledge from ``businesses/{business}/`` into typed objects.

    Follows the Business Knowledge Specification v1.1.0.
    Completely stateless.  No persistence.  No lifecycle transitions.
    """

    def load(self, business_dir: Path) -> BusinessData:
        """Load all typed business objects from a business directory."""
        if not business_dir.is_dir():
            raise FileNotFoundError(
                f"Business directory not found: {business_dir}"
            )

        dir_name = business_dir.name
        business_id = "biz_" + dir_name.lower()
        warnings: list[str] = []

        business = self._load_business(
            business_dir / "Business_Profile.md", business_id, dir_name, warnings
        )
        strategy = self._load_strategy(
            business_dir / "Strategy.md", business_id, dir_name, warnings
        )
        goals = self._load_goals(
            business_dir / "Goals.md", business_id, warnings
        )
        kpis = self._load_kpis(
            business_dir / "KPIs.md", business_id, dir_name, warnings
        )
        bottlenecks = self._load_bottlenecks(
            business_dir / "Bottlenecks.md", business_id, warnings
        )
        opportunities = self._load_opportunities(
            business_dir / "Opportunities.md", business_id, warnings
        )
        decisions = self._load_decisions(
            business_dir / "Decisions.md", business_id, warnings
        )
        experiments = self._load_experiments(
            business_dir / "Experiments.md", business_id, warnings
        )
        lessons = self._load_lessons(
            business_dir / "Lessons_Learned.md", business_id, warnings
        )

        return BusinessData(
            business=business,
            strategy=strategy,
            goals=goals,
            kpis=kpis,
            bottlenecks=bottlenecks,
            opportunities=opportunities,
            decisions=decisions,
            experiments=experiments,
            lessons=lessons,
            warnings=warnings,
        )

    # -- Single-object loaders ------------------------------------------------

    def _load_business(
        self, path: Path, business_id: str, dir_name: str, warnings: list[str]
    ) -> Business | None:
        if not path.is_file():
            warnings.append(f"Missing required file: {path.name}")
            return None
        text = path.read_text(encoding="utf-8")
        mission = _extract_section(text, "Mission")
        if not mission:
            warnings.append(f"Missing ## Mission section in {path.name}")
            return None
        return Business(business_id=business_id, name=dir_name, mission=mission)

    def _load_strategy(
        self, path: Path, business_id: str, dir_name: str, warnings: list[str]
    ) -> Strategy | None:
        if not path.is_file():
            warnings.append(f"Missing required file: {path.name}")
            return None
        text = path.read_text(encoding="utf-8")
        objective = _extract_section(text, "Strategic Objective")
        if not objective:
            warnings.append(
                f"Missing ## Strategic Objective section in {path.name}"
            )
            return None
        first_period = objective.find(".")
        title = objective[:first_period].strip() if first_period > 0 else objective

        review_frequency: str | None = None
        cadence = _extract_section(text, "Review Cadence")
        if cadence:
            for line_part in cadence.split("-"):
                lower = line_part.lower()
                if "monthly" in lower and "strategy" in lower:
                    review_frequency = "monthly"
                    break

        strategy_id = f"strat_{dir_name.lower()}_001"
        return Strategy(
            strategy_id=strategy_id,
            business_id=business_id,
            title=title,
            objective=objective,
            review_frequency=review_frequency,
        )

    # -- Table-based loaders --------------------------------------------------

    def _load_goals(
        self, path: Path, business_id: str, warnings: list[str]
    ) -> list[Goal]:
        if not path.is_file():
            warnings.append(f"Missing required file: {path.name}")
            return []
        text = path.read_text(encoding="utf-8")
        rows = _parse_table(text, "Goals")
        if not rows:
            return []

        goals: list[Goal] = []
        seen: set[str] = set()
        for row in rows:
            goal_id = row.get("ID", "").strip()
            if not goal_id:
                warnings.append("Goal row missing ID column, skipping")
                continue
            if goal_id in seen:
                warnings.append(f"Duplicate goal ID: {goal_id}, skipping")
                continue
            seen.add(goal_id)

            title = row.get("Goal", "").strip()
            description = row.get("Description", "").strip()
            target_value = row.get("Target", "").strip()
            target_date = row.get("Target Date", "").strip()
            owner = row.get("Owner", "").strip()
            status_raw = row.get("Status", "").strip()
            strategy_id = row.get("Strategy", "").strip() or None

            missing = [
                f
                for f, v in [
                    ("Goal", title),
                    ("Description", description),
                    ("Target", target_value),
                    ("Target Date", target_date),
                    ("Owner", owner),
                    ("Status", status_raw),
                ]
                if not v
            ]
            if missing:
                warnings.append(
                    f"Goal {goal_id} missing required columns: "
                    f"{', '.join(missing)}, skipping"
                )
                continue

            status = _normalize_status(status_raw)
            if status not in _GOAL_STATUSES:
                warnings.append(f"Goal {goal_id}: unknown status '{status}'")

            goals.append(
                Goal(
                    goal_id=goal_id,
                    business_id=business_id,
                    title=title,
                    description=description,
                    target_value=target_value,
                    target_date=target_date,
                    owner=owner,
                    status=status,
                    strategy_id=strategy_id,
                )
            )
        return goals

    def _load_kpis(
        self, path: Path, business_id: str, dir_name: str, warnings: list[str]
    ) -> list[KPI]:
        if not path.is_file():
            warnings.append(f"Missing required file: {path.name}")
            return []
        text = path.read_text(encoding="utf-8")
        rows = _parse_table(text, "Growth KPIs")
        if not rows:
            return []

        kpis: list[KPI] = []
        seen: set[str] = set()
        seq = 1
        for row in rows:
            kpi_name = row.get("KPI", "").strip()
            if not kpi_name:
                warnings.append("KPI row missing KPI column, skipping")
                continue

            goal_id = row.get("Goal", "").strip()
            unit = row.get("Unit", "").strip()
            current_raw = row.get("Current", "").strip()
            target_raw = row.get("Target", "").strip()
            frequency = row.get("Frequency", "").strip()
            status_raw = row.get("Status", "").strip()

            missing: list[str] = []
            if not goal_id:
                missing.append("Goal")
            if not unit:
                missing.append("Unit")
            if not frequency:
                missing.append("Frequency")
            if not status_raw:
                missing.append("Status")

            current_value: float | None = None
            try:
                current_value = float(current_raw)
            except (ValueError, TypeError):
                missing.append(f"Current ('{current_raw}')")

            target_value: float | None = None
            try:
                target_value = float(target_raw)
            except (ValueError, TypeError):
                missing.append(f"Target ('{target_raw}')")

            if missing:
                warnings.append(
                    f"KPI '{kpi_name}' missing or unparseable required fields: "
                    f"{', '.join(missing)}, skipping"
                )
                continue

            kpi_id = f"kpi_{dir_name.lower()}_{seq:03d}"
            if kpi_id in seen:
                warnings.append(f"Duplicate KPI ID: {kpi_id}, skipping")
                continue
            seen.add(kpi_id)
            seq += 1

            status = _normalize_status(status_raw)
            if status not in _KPI_STATUSES:
                warnings.append(f"KPI {kpi_id}: unknown status '{status}'")

            owner = row.get("Owner", "").strip() or None

            kpis.append(
                KPI(
                    kpi_id=kpi_id,
                    business_id=business_id,
                    goal_id=goal_id,
                    name=kpi_name,
                    unit=unit,
                    current_value=current_value,
                    target_value=target_value,
                    frequency=frequency,
                    status=status,
                    owner=owner,
                )
            )
        return kpis

    def _load_bottlenecks(
        self, path: Path, business_id: str, warnings: list[str]
    ) -> list[Bottleneck]:
        if not path.is_file():
            warnings.append(f"Missing required file: {path.name}")
            return []
        text = path.read_text(encoding="utf-8")
        rows = _parse_table(text, "Active Bottlenecks")
        if not rows:
            return []

        bottlenecks: list[Bottleneck] = []
        seen: set[str] = set()
        for row in rows:
            bot_id = row.get("ID", "").strip()
            if not bot_id:
                warnings.append("Bottleneck row missing ID column, skipping")
                continue
            if bot_id in seen:
                warnings.append(f"Duplicate bottleneck ID: {bot_id}, skipping")
                continue
            seen.add(bot_id)

            title = row.get("Description", "").strip()
            area = row.get("Area", "").strip()
            impact_raw = row.get("Impact", "").strip()
            status_raw = row.get("Status", "").strip()

            missing = [
                f
                for f, v in [
                    ("Description", title),
                    ("Area", area),
                    ("Impact", impact_raw),
                    ("Status", status_raw),
                ]
                if not v
            ]
            if missing:
                warnings.append(
                    f"Bottleneck {bot_id} missing required columns: "
                    f"{', '.join(missing)}, skipping"
                )
                continue

            category = _normalize_status(area)
            if category not in _BOTTLENECK_CATEGORIES:
                warnings.append(
                    f"Bottleneck {bot_id}: unknown category '{category}'"
                )

            impact = impact_raw.strip().lower()
            if impact not in _IMPACT_EFFORT:
                warnings.append(
                    f"Bottleneck {bot_id}: unknown impact '{impact}'"
                )

            status = _normalize_status(status_raw)
            if status not in _BOTTLENECK_STATUSES:
                warnings.append(
                    f"Bottleneck {bot_id}: unknown status '{status}'"
                )

            owner = row.get("Owner", "").strip() or None

            bottlenecks.append(
                Bottleneck(
                    bottleneck_id=bot_id,
                    business_id=business_id,
                    title=title,
                    category=category,
                    impact=impact,
                    status=status,
                    owner=owner,
                )
            )
        return bottlenecks

    def _load_opportunities(
        self, path: Path, business_id: str, warnings: list[str]
    ) -> list[Opportunity]:
        if not path.is_file():
            warnings.append(f"Missing required file: {path.name}")
            return []
        text = path.read_text(encoding="utf-8")
        rows = _parse_table(text, "Opportunity Pipeline")
        if not rows:
            return []

        opportunities: list[Opportunity] = []
        seen: set[str] = set()
        for row in rows:
            opp_id = row.get("ID", "").strip()
            if not opp_id:
                warnings.append("Opportunity row missing ID column, skipping")
                continue
            if opp_id in seen:
                warnings.append(
                    f"Duplicate opportunity ID: {opp_id}, skipping"
                )
                continue
            seen.add(opp_id)

            title = row.get("Opportunity", "").strip()
            impact_raw = row.get("Expected Impact", "").strip()
            effort_raw = row.get("Effort", "").strip()
            status_raw = row.get("Status", "").strip()
            owner = row.get("Owner", "").strip()

            missing = [
                f
                for f, v in [
                    ("Opportunity", title),
                    ("Expected Impact", impact_raw),
                    ("Effort", effort_raw),
                    ("Status", status_raw),
                    ("Owner", owner),
                ]
                if not v
            ]
            if missing:
                warnings.append(
                    f"Opportunity {opp_id} missing required columns: "
                    f"{', '.join(missing)}, skipping"
                )
                continue

            impact = impact_raw.strip().lower()
            if impact not in _IMPACT_EFFORT:
                warnings.append(
                    f"Opportunity {opp_id}: unknown expected_impact '{impact}'"
                )

            effort = effort_raw.strip().lower()
            if effort not in _IMPACT_EFFORT:
                warnings.append(
                    f"Opportunity {opp_id}: unknown estimated_effort '{effort}'"
                )

            status = _normalize_status(status_raw)
            if status not in _OPPORTUNITY_STATUSES:
                warnings.append(
                    f"Opportunity {opp_id}: unknown status '{status}'"
                )

            opportunities.append(
                Opportunity(
                    opportunity_id=opp_id,
                    business_id=business_id,
                    title=title,
                    description=title,
                    expected_impact=impact,
                    estimated_effort=effort,
                    owner=owner,
                    status=status,
                )
            )
        return opportunities

    def _load_decisions(
        self, path: Path, business_id: str, warnings: list[str]
    ) -> list[Decision]:
        if not path.is_file():
            warnings.append(f"Missing required file: {path.name}")
            return []
        text = path.read_text(encoding="utf-8")
        rows = _parse_table(text, "Decision Log")
        if not rows:
            return []

        decisions: list[Decision] = []
        seen: set[str] = set()
        for row in rows:
            dec_id = row.get("ID", "").strip()
            if not dec_id:
                warnings.append("Decision row missing ID column, skipping")
                continue
            if dec_id in seen:
                warnings.append(f"Duplicate decision ID: {dec_id}, skipping")
                continue
            seen.add(dec_id)

            title = row.get("Decision", "").strip()
            date = row.get("Date", "").strip()
            status_raw = row.get("Status", "").strip()
            rationale = row.get("Expected Impact", "").strip()

            missing = [
                f
                for f, v in [
                    ("Decision", title),
                    ("Date", date),
                    ("Status", status_raw),
                    ("Expected Impact", rationale),
                ]
                if not v
            ]
            if missing:
                warnings.append(
                    f"Decision {dec_id} missing required columns: "
                    f"{', '.join(missing)}, skipping"
                )
                continue

            status = _normalize_status(status_raw)
            if status not in _DECISION_STATUSES:
                warnings.append(
                    f"Decision {dec_id}: unknown status '{status}'"
                )

            decisions.append(
                Decision(
                    decision_id=dec_id,
                    business_id=business_id,
                    title=title,
                    context=title,
                    rationale=rationale,
                    status=status,
                    decision_date=date,
                )
            )
        return decisions

    def _load_experiments(
        self, path: Path, business_id: str, warnings: list[str]
    ) -> list[Experiment]:
        if not path.is_file():
            warnings.append(f"Missing required file: {path.name}")
            return []
        text = path.read_text(encoding="utf-8")
        rows = _parse_table(text, "Experiment Log")
        if not rows:
            return []

        experiments: list[Experiment] = []
        seen: set[str] = set()
        for row in rows:
            exp_id = row.get("ID", "").strip()
            if not exp_id:
                warnings.append("Experiment row missing ID column, skipping")
                continue
            if exp_id in seen:
                warnings.append(
                    f"Duplicate experiment ID: {exp_id}, skipping"
                )
                continue
            seen.add(exp_id)

            hypothesis = row.get("Hypothesis", "").strip()
            metric = row.get("Metric", "").strip()
            status_raw = row.get("Status", "").strip()

            missing = [
                f
                for f, v in [
                    ("Hypothesis", hypothesis),
                    ("Metric", metric),
                    ("Status", status_raw),
                ]
                if not v
            ]
            if missing:
                warnings.append(
                    f"Experiment {exp_id} missing required columns: "
                    f"{', '.join(missing)}, skipping"
                )
                continue

            first_period = hypothesis.find(".")
            title = (
                hypothesis[:first_period].strip()
                if first_period > 0
                else hypothesis
            )

            status = _normalize_status(status_raw)
            if status not in _EXPERIMENT_STATUSES:
                warnings.append(
                    f"Experiment {exp_id}: unknown status '{status}'"
                )

            result_raw = row.get("Result", "").strip()
            outcome = None if (not result_raw or result_raw.upper() == "TBD") else result_raw

            start_date = row.get("Date", "").strip() or None

            experiments.append(
                Experiment(
                    experiment_id=exp_id,
                    business_id=business_id,
                    title=title,
                    hypothesis=hypothesis,
                    success_criteria=metric,
                    status=status,
                    outcome=outcome,
                    start_date=start_date,
                )
            )
        return experiments

    def _load_lessons(
        self, path: Path, business_id: str, warnings: list[str]
    ) -> list[Lesson]:
        if not path.is_file():
            warnings.append(f"Missing required file: {path.name}")
            return []
        text = path.read_text(encoding="utf-8")
        rows = _parse_table(text, "Lesson Log")
        if not rows:
            return []

        lessons: list[Lesson] = []
        seen: set[str] = set()
        for row in rows:
            les_id = row.get("ID", "").strip()
            if not les_id:
                warnings.append("Lesson row missing ID column, skipping")
                continue
            if les_id in seen:
                warnings.append(f"Duplicate lesson ID: {les_id}, skipping")
                continue
            seen.add(les_id)

            lesson_text = row.get("Lesson", "").strip()
            category = row.get("Category", "").strip()
            action = row.get("Action", "").strip()
            date = row.get("Date", "").strip()

            missing = [
                f
                for f, v in [
                    ("Lesson", lesson_text),
                    ("Category", category),
                    ("Action", action),
                    ("Date", date),
                ]
                if not v
            ]
            if missing:
                warnings.append(
                    f"Lesson {les_id} missing required columns: "
                    f"{', '.join(missing)}, skipping"
                )
                continue

            source = category.strip().lower()
            if source not in _LESSON_SOURCES:
                warnings.append(f"Lesson {les_id}: unknown source '{source}'")

            lessons.append(
                Lesson(
                    lesson_id=les_id,
                    business_id=business_id,
                    title=lesson_text,
                    summary=lesson_text,
                    source=source,
                    recommendation=action,
                    date=date,
                )
            )
        return lessons
