"""Fixed-dialect, positive-allowlist policy for bounded SQL evidence reads."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC
import re
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.tokens import TokenType

from lode.application.intake import canonical_hash
from lode.evidence_access.budget import intersect_budget
from lode.evidence_access.candidate import NativeReadCandidateInput, QueryPayload
from lode.evidence_access.types import (
    AccessContext,
    AccessRejection,
    BoundNativeAction,
    ParsedNativeAction,
    PolicyEvaluation,
)

_PARSER_VERSION = "sqlglot-30.17.0"
_POLICY_VERSION = "sql-safe-subset.1"
_DIALECTS = {"postgres", "mysql"}
_SYSTEM_SCHEMAS = {"information_schema", "pg_catalog", "mysql", "performance_schema", "sys"}
_ALLOWED_FUNCTIONS = {
    "AVG",
    "COALESCE",
    "COUNT",
    "DATE_TRUNC",
    "LENGTH",
    "LOWER",
    "MAX",
    "MIN",
    "SUM",
    "UPPER",
}
_ALLOWED_NODE_NAMES = {
    "Add",
    "Alias",
    "And",
    "Between",
    "Boolean",
    "Column",
    "CTE",
    "Div",
    "EQ",
    "From",
    "Group",
    "GT",
    "GTE",
    "Having",
    "Identifier",
    "In",
    "Is",
    "Join",
    "LT",
    "LTE",
    "Limit",
    "Literal",
    "Mod",
    "Mul",
    "NEQ",
    "Neg",
    "Not",
    "Null",
    "Or",
    "Order",
    "Ordered",
    "Paren",
    "Select",
    "Star",
    "Sub",
    "Table",
    "TableAlias",
    "Var",
    "Where",
    "With",
}


@dataclass(frozen=True, slots=True)
class _TableRef:
    key: str
    alias: str
    columns: frozenset[str]
    time_column: str
    stable_order: tuple[str, ...]
    select: exp.Select


class SQLPolicy:
    language = "sql"
    parser_name = "sqlglot"
    parser_version = _PARSER_VERSION
    policy_version = _POLICY_VERSION

    def parse(self, candidate: NativeReadCandidateInput) -> ParsedNativeAction:
        if not isinstance(candidate.payload, QueryPayload):
            raise AccessRejection("invalid_syntax", "SQL requires a query string")
        parsed: dict[str, exp.Select] = {}
        modes: set[str] = set()
        failures: list[AccessRejection] = []
        for dialect in sorted(_DIALECTS):
            try:
                tree, mode = self._parse_candidate(candidate.payload.query, dialect)
                self._validate_syntax_tree(tree)
                self._value_slots(tree, set(candidate.value_bindings))
                parsed[dialect] = tree
                modes.add(mode)
            except AccessRejection as exc:
                failures.append(exc)
        if not parsed:
            priority = (
                "write_semantics",
                "scope_violation",
                "budget_violation",
                "unsupported_node",
                "invalid_syntax",
            )
            for code in priority:
                if any(failure.code == code for failure in failures):
                    raise AccessRejection(code, "SQL is outside the enabled safe subset")
            raise AccessRejection("invalid_syntax", "SQL is invalid in every supported dialect")
        slots = self._value_slots(next(iter(parsed.values())), set(candidate.value_bindings))
        action = {
            "query": candidate.payload.query,
            "parsed_dialects": sorted(parsed),
            "requested_mode": modes.pop(),
        }
        return ParsedNativeAction(
            language=self.language,
            canonical_action=action,
            parse_tree_hash=canonical_hash(
                {dialect: tree.dump() for dialect, tree in sorted(parsed.items())}
            ),
            structural_hash=canonical_hash(
                {dialect: self._structural_hash(tree) for dialect, tree in sorted(parsed.items())}
            ),
            value_slots=slots,
            parser_name=self.parser_name,
            parser_version=self.parser_version,
        )

    def evaluate(
        self,
        action: ParsedNativeAction,
        candidate: NativeReadCandidateInput,
        context: AccessContext,
    ) -> PolicyEvaluation:
        dialect = context.scope_config.get("dialect")
        if not isinstance(dialect, str) or dialect not in _DIALECTS:
            raise AccessRejection("scope_violation", "SQL snapshot dialect is invalid")
        parsed_dialects = action.canonical_action.get("parsed_dialects")
        if not isinstance(parsed_dialects, list) or dialect not in parsed_dialects:
            raise AccessRejection("invalid_syntax", "SQL is invalid for the snapshot dialect")
        budget, diff = intersect_budget(candidate, context)
        if budget.window_start is None or budget.window_end is None:
            raise AccessRejection("budget_violation", "SQL requires an absolute bounded window")
        tree, requested_mode = self._parse_candidate(str(action.canonical_action["query"]), dialect)
        if requested_mode != action.canonical_action.get("requested_mode"):
            raise AccessRejection("invalid_syntax", "SQL execution mode changed after parsing")
        effective = tree.copy()
        tables = self._validate_with_catalog(effective, context)
        if all(
            select.args.get("where") is None for select in effective.find_all(exp.Select)
        ) and not any(self._required_predicates(context, table) for table in tables):
            raise AccessRejection("scope_violation", "SQL query has no evidence-relevant predicate")

        for table in tables:
            predicate: exp.Expression | None = None
            for column, value in sorted(self._required_predicates(context, table).items()):
                clause = exp.column(column, table=table.alias).eq(exp.convert(value))
                predicate = clause if predicate is None else exp.and_(predicate, clause)
            time_column = exp.column(table.time_column, table=table.alias)
            time_clause = exp.and_(
                exp.GTE(
                    this=time_column,
                    expression=exp.Literal.string(budget.window_start.astimezone(UTC).isoformat()),
                ),
                exp.LT(
                    this=time_column.copy(),
                    expression=exp.Literal.string(budget.window_end.astimezone(UTC).isoformat()),
                ),
            )
            predicate = time_clause if predicate is None else exp.and_(predicate, time_clause)
            table.select.where(predicate, append=True, copy=False)

        supplied_limit = self._literal_limit(effective)
        effective_limit = min(supplied_limit or budget.result_limit, budget.result_limit)
        effective = effective.limit(effective_limit, copy=False)
        if effective.args.get("order") is None:
            primary = tables[0]
            order_alias = self._outer_cte_alias(effective) or primary.alias
            if order_alias != primary.alias:
                projected = self._projected_names(effective)
                if not set(primary.stable_order) <= projected:
                    raise AccessRejection(
                        "scope_violation",
                        "SQL CTE outer projection must include stable order columns",
                    )
            effective = effective.order_by(
                *(exp.column(column, table=order_alias) for column in primary.stable_order),
                copy=False,
            )
        query = effective.sql(dialect=dialect, pretty=False, comments=False)
        reparsed = self._parse_one(query, dialect)
        self._validate_with_catalog(reparsed, context)
        effective_action = {
            "adapter_kind": f"{dialect}_sql",
            "dialect": dialect,
            "query": query,
            "execution_mode": requested_mode,
            "row_limit": effective_limit,
            "timeout_ms": budget.timeout_ms,
            "output_bytes": budget.output_bytes,
            "max_estimated_rows": self._positive_scope_int(context, "max_estimated_rows", 100_000),
            "max_estimated_cost": self._positive_scope_int(context, "max_estimated_cost", 100_000),
        }
        diff["mandatory_scope"] = {
            "tables": [table.key for table in tables],
            "time_columns": {table.key: table.time_column for table in tables},
            "required_predicates": {
                table.key: sorted(self._required_predicates(context, table)) for table in tables
            },
        }
        if supplied_limit != effective_limit:
            diff["limit"] = {"requested": supplied_limit, "effective": effective_limit}
        return PolicyEvaluation(
            effective_action=effective_action,
            effective_structural_hash=self._effective_structural_hash(effective_action),
            validation_decisions=(
                {"check": "sql_complete_ast", "outcome": "allow", "dialect": dialect},
                {
                    "check": "sql_catalog_scope",
                    "outcome": "allow",
                    "tables": [table.key for table in tables],
                },
                {"check": "sql_read_only_subset", "outcome": "allow"},
                {"check": "sql_mandatory_constraints", "outcome": "allow"},
            ),
            constraint_diff=diff,
            effective_budget=budget,
        )

    def bind_values(
        self,
        action: ParsedNativeAction,
        evaluation: PolicyEvaluation,
        values: Mapping[str, str],
    ) -> BoundNativeAction:
        effective = dict(evaluation.effective_action)
        dialect = str(effective["dialect"])
        tree = self._parse_one(str(effective["query"]), dialect)
        slots = self._literal_nodes(tree, set(values))
        if set(slots) != set(values):
            raise AccessRejection("scope_violation", "resolved SQL values do not match slots")
        for sentinel, literal in slots.items():
            literal.set("this", values[sentinel])
        query = tree.sql(dialect=dialect, pretty=False, comments=False)
        reparsed = self._parse_one(query, dialect)
        self._validate_syntax_tree(reparsed)
        bound = {**effective, "query": query}
        shape = self._effective_structural_hash(bound)
        if shape != evaluation.effective_structural_hash:
            raise AccessRejection("invalid_syntax", "ValueRef binding changed SQL AST structure")
        return BoundNativeAction(
            language=self.language,
            canonical_action=bound,
            structural_hash=shape,
            parse_tree_hash=canonical_hash(reparsed.dump()),
        )

    @staticmethod
    def _parse_one(query: str, dialect: str) -> exp.Select:
        try:
            tokens = sqlglot.tokenize(query, read=dialect)
            if any(token.comments for token in tokens) or any(
                token.token_type is TokenType.SEMICOLON for token in tokens
            ):
                raise AccessRejection("invalid_syntax", "SQL comments and semicolons are disabled")
            trees = sqlglot.parse(
                query,
                read=dialect,
                error_level=sqlglot.ErrorLevel.RAISE,
                max_errors=1,
                max_nodes=2_000,
            )
        except AccessRejection:
            raise
        except (sqlglot.ParseError, sqlglot.TokenError, ValueError) as exc:
            raise AccessRejection("invalid_syntax", "SQL parser rejected the query") from exc
        if len(trees) != 1 or not isinstance(trees[0], exp.Select):
            code = "write_semantics" if trees else "invalid_syntax"
            raise AccessRejection(code, "only one SELECT statement is allowed")
        return trees[0]

    def _parse_candidate(self, query: str, dialect: str) -> tuple[exp.Select, str]:
        match = re.fullmatch(r"\s*EXPLAIN\s+(.+?)\s*", query, flags=re.IGNORECASE | re.DOTALL)
        if match is None:
            return self._parse_one(query, dialect), "select"
        inner = match.group(1)
        if re.match(
            r"(?i)(?:ANALYZE|ANALYSE|VERBOSE|FORMAT|COSTS|BUFFERS|TIMING|SUMMARY)\b|\(",
            inner,
        ):
            raise AccessRejection("unsupported_node", "SQL EXPLAIN options are disabled")
        return self._parse_one(inner, dialect), "explain"

    def _validate_syntax_tree(self, tree: exp.Select) -> None:
        nodes = list(tree.walk())
        if len(nodes) > 2_000:
            raise AccessRejection("budget_violation", "SQL AST node budget exceeded")
        selects = [node for node in nodes if isinstance(node, exp.Select)]
        with_clause = tree.args.get("with_")
        if len(selects) > 2 or (len(selects) == 2 and with_clause is None):
            raise AccessRejection("unsupported_node", "SQL subqueries are disabled")
        if with_clause is not None:
            if (
                with_clause.args.get("recursive")
                or len(with_clause.expressions) != 1
                or not isinstance(with_clause.expressions[0], exp.CTE)
                or not isinstance(with_clause.expressions[0].this, exp.Select)
                or with_clause.expressions[0].args.get("materialized") is not None
            ):
                raise AccessRejection("unsupported_node", "SQL CTE shape is disabled")
        if tree.args.get("into") is not None or tree.args.get("locks"):
            raise AccessRejection("write_semantics", "SQL write or locking semantics are disabled")
        if tree.args.get("offset") is not None:
            raise AccessRejection("budget_violation", "SQL OFFSET pagination is disabled")
        for node in nodes:
            node_name = type(node).__name__
            if node_name not in _ALLOWED_NODE_NAMES and isinstance(node, exp.Func):
                if node.sql_name().upper() not in _ALLOWED_FUNCTIONS:
                    raise AccessRejection(
                        "unsupported_node",
                        "SQL function is not allowlisted",
                        {"function": node.sql_name().upper()},
                    )
                continue
            if node_name not in _ALLOWED_NODE_NAMES:
                raise AccessRejection(
                    "unsupported_node",
                    "SQL AST node is not allowlisted",
                    {"node": node_name},
                )
            if isinstance(node, exp.Star) and not isinstance(node.parent, exp.Count):
                raise AccessRejection("scope_violation", "SQL wildcard projection is disabled")
            if isinstance(node, exp.Join):
                side = (node.side or "").upper()
                kind = (node.kind or "").upper()
                if side or kind not in {"", "INNER"} or node.args.get("on") is None:
                    raise AccessRejection("unsupported_node", "SQL join type is disabled")
        for select in selects:
            self._literal_limit(select)

    def _validate_with_catalog(
        self, tree: exp.Select, context: AccessContext
    ) -> tuple[_TableRef, ...]:
        self._validate_syntax_tree(tree)
        catalog_tables = context.schema_catalog.get("tables")
        allowed_tables = context.scope_config.get("allowed_tables")
        if not isinstance(catalog_tables, dict) or not isinstance(allowed_tables, list):
            raise AccessRejection("scope_violation", "SQL snapshot catalog is invalid")
        max_joins = self._positive_scope_int(context, "max_joins", 2)
        cte_outputs = self._cte_outputs(tree)
        table_nodes = list(tree.find_all(exp.Table))
        physical_nodes = [node for node in table_nodes if node.name not in cte_outputs or node.db]
        if not 1 <= len(physical_nodes) <= max_joins + 1:
            raise AccessRejection("budget_violation", "SQL table or join budget exceeded")
        tables: list[_TableRef] = []
        sources: dict[int, dict[str, frozenset[str]]] = {}
        for node in table_nodes:
            select = self._containing_select(node)
            if not node.db and node.name in cte_outputs:
                if select is not tree:
                    raise AccessRejection("scope_violation", "SQL CTE reference is not canonical")
                sources.setdefault(id(select), {})[node.alias_or_name] = cte_outputs[node.name]
                continue
            if not isinstance(node.this, exp.Identifier) or node.catalog:
                raise AccessRejection("scope_violation", "SQL table reference is not canonical")
            if cte_outputs and select is tree:
                raise AccessRejection("unsupported_node", "SQL CTE outer query must use its CTE")
            schema = node.db
            if not schema or schema.lower() in _SYSTEM_SCHEMAS:
                raise AccessRejection("scope_violation", "SQL table must use an allowed schema")
            key = f"{schema}.{node.name}"
            descriptor = catalog_tables.get(key)
            if key not in allowed_tables or not isinstance(descriptor, dict):
                raise AccessRejection("scope_violation", "SQL table is outside snapshot scope")
            columns = descriptor.get("columns")
            time_column = descriptor.get("time_column")
            stable_order = descriptor.get("stable_order")
            if (
                not isinstance(columns, dict)
                or not columns
                or not isinstance(time_column, str)
                or time_column not in columns
                or not isinstance(stable_order, list)
                or not stable_order
                or any(not isinstance(item, str) or item not in columns for item in stable_order)
            ):
                raise AccessRejection("scope_violation", "SQL table catalog entry is invalid")
            alias = node.alias_or_name
            source = sources.setdefault(id(select), {})
            table = _TableRef(
                key,
                alias,
                frozenset(columns),
                time_column,
                tuple(stable_order),
                select,
            )
            if alias in source:
                raise AccessRejection("invalid_syntax", "SQL table alias is duplicated")
            source[alias] = table.columns
            tables.append(table)
        for column in tree.find_all(exp.Column):
            select_sources = sources.get(id(self._containing_select(column)), {})
            all_columns = (
                frozenset().union(*select_sources.values()) if select_sources else frozenset()
            )
            if column.name not in all_columns:
                raise AccessRejection("scope_violation", "SQL column is outside snapshot catalog")
            if column.table:
                columns = select_sources.get(column.table)
                if columns is None or column.name not in columns:
                    raise AccessRejection(
                        "scope_violation", "SQL qualified column is outside scope"
                    )
        return tuple(tables)

    @staticmethod
    def _containing_select(node: exp.Expression) -> exp.Select:
        cursor: exp.Expression | None = node
        while cursor is not None and not isinstance(cursor, exp.Select):
            cursor = cursor.parent
        if not isinstance(cursor, exp.Select):
            raise AccessRejection("invalid_syntax", "SQL node is outside a SELECT")
        return cursor

    @classmethod
    def _cte_outputs(cls, tree: exp.Select) -> dict[str, frozenset[str]]:
        with_clause = tree.args.get("with_")
        if with_clause is None:
            return {}
        cte = with_clause.expressions[0]
        names = cls._projected_names(cte.this)
        if not names or len(names) != len(cte.this.expressions):
            raise AccessRejection(
                "unsupported_node", "SQL CTE projections must be named scalar columns"
            )
        return {cte.alias_or_name: names}

    @staticmethod
    def _projected_names(select: exp.Select) -> frozenset[str]:
        names: list[str] = []
        for expression in select.expressions:
            if isinstance(expression, exp.Column):
                names.append(expression.name)
            elif isinstance(expression, exp.Alias) and isinstance(expression.this, exp.Column):
                names.append(expression.alias)
            else:
                return frozenset()
        if len(set(names)) != len(names):
            raise AccessRejection("invalid_syntax", "SQL projection names are duplicated")
        return frozenset(names)

    @staticmethod
    def _outer_cte_alias(tree: exp.Select) -> str | None:
        outputs = SQLPolicy._cte_outputs(tree)
        if not outputs:
            return None
        from_clause = tree.args.get("from_")
        table = from_clause.this if isinstance(from_clause, exp.From) else None
        if not isinstance(table, exp.Table) or table.name not in outputs:
            raise AccessRejection("unsupported_node", "SQL CTE outer source is invalid")
        return table.alias_or_name

    @staticmethod
    def _required_predicates(context: AccessContext, table: _TableRef) -> Mapping[str, Any]:
        configured = context.scope_config.get("required_predicates", {})
        values = configured.get(table.key, {}) if isinstance(configured, dict) else None
        if not isinstance(values, dict) or any(column not in table.columns for column in values):
            raise AccessRejection("scope_violation", "SQL required predicates are invalid")
        if any(not isinstance(value, (str, int, float, bool)) for value in values.values()):
            raise AccessRejection("scope_violation", "SQL required predicate value is invalid")
        return values

    @staticmethod
    def _literal_limit(tree: exp.Select) -> int | None:
        limit = tree.args.get("limit")
        if limit is None:
            return None
        value = limit.expression
        if not isinstance(value, exp.Literal) or value.is_string:
            raise AccessRejection("invalid_syntax", "SQL LIMIT must be an integer literal")
        try:
            result = int(value.this)
        except ValueError as exc:
            raise AccessRejection("invalid_syntax", "SQL LIMIT is invalid") from exc
        if result <= 0:
            raise AccessRejection("budget_violation", "SQL LIMIT must be positive")
        return result

    @staticmethod
    def _positive_scope_int(context: AccessContext, key: str, default: int) -> int:
        value = context.scope_config.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise AccessRejection("scope_violation", "SQL scope budget is invalid", {"field": key})
        return value

    def _value_slots(
        self, tree: exp.Select, sentinels: set[str]
    ) -> dict[str, tuple[str | int, ...]]:
        nodes = self._literal_nodes(tree, sentinels)
        return {sentinel: ("string_literal", index) for index, sentinel in enumerate(sorted(nodes))}

    @staticmethod
    def _literal_nodes(tree: exp.Select, sentinels: set[str]) -> dict[str, exp.Literal]:
        slots: dict[str, exp.Literal] = {}
        for literal in tree.find_all(exp.Literal):
            value = str(literal.this)
            exact = value in sentinels
            if exact:
                if not literal.is_string or value in slots:
                    raise AccessRejection(
                        "invalid_syntax", "SQL sentinel must occupy one string literal"
                    )
                slots[value] = literal
            elif any(sentinel in value for sentinel in sentinels):
                raise AccessRejection(
                    "invalid_syntax", "SQL sentinel must occupy a complete string literal"
                )
        if set(slots) != sentinels:
            raise AccessRejection("invalid_syntax", "every SQL binding must occupy one literal")
        return slots

    @staticmethod
    def _structural_hash(tree: exp.Select) -> str:
        shape: list[dict[str, Any]] = []
        for node in tree.walk():
            node_name = type(node).__name__
            entry: dict[str, Any] = {"node": node_name}
            if isinstance(node, exp.Identifier):
                entry["identifier"] = node.this
            elif isinstance(node, exp.Literal):
                entry["literal_type"] = "string" if node.is_string else "number"
            elif node_name not in _ALLOWED_NODE_NAMES and isinstance(node, exp.Func):
                entry["function"] = node.sql_name().upper()
            shape.append(entry)
        return canonical_hash(shape)

    def _effective_structural_hash(self, action: Mapping[str, Any]) -> str:
        tree = self._parse_one(str(action["query"]), str(action["dialect"]))
        envelope = {
            "adapter_kind": action["adapter_kind"],
            "dialect": action["dialect"],
            "execution_mode": action["execution_mode"],
            "query_shape": self._structural_hash(tree),
            "row_limit": type(action["row_limit"]).__name__,
            "timeout_ms": type(action["timeout_ms"]).__name__,
            "output_bytes": type(action["output_bytes"]).__name__,
            "max_estimated_rows": type(action["max_estimated_rows"]).__name__,
            "max_estimated_cost": type(action["max_estimated_cost"]).__name__,
        }
        return canonical_hash(envelope)
