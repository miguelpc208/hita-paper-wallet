"""Compare a synthetic, account-local statement with an A0 ledger projection."""

from decimal import Context, ROUND_HALF_EVEN, localcontext

from .ledger import digest, validate_utc_seconds
from .money import FinancialValidationError, canonical, dec


_SCHEMA_VERSION = "hita.a0.reconciliation.v1"
_SYNTHETIC_MODE = "SYNTHETIC_ONLY"
_BASIS = "TRADE_DATE"
_KINDS = frozenset({"CASH", "HOLDING", "PAYABLE", "RECEIVABLE"})
_STATEMENT_FIELDS = frozenset({
    "mode", "statement_id", "account", "effective_at", "available_at", "basis", "lines",
})
_STATEMENT_LINE_FIELDS = frozenset({"kind", "key", "amount_dec", "source_record_id"})
_DECIMAL_CONTEXT = Context(prec=60, rounding=ROUND_HALF_EVEN)


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or not value.isascii() or not value.isprintable() or any(
        character.isspace() for character in value
    ):
        raise FinancialValidationError(f"{field} must be a nonempty identifier")
    return value


def _index_lines(lines: object, *, statement: bool) -> dict:
    side = "Statement" if statement else "Ledger"
    if not isinstance(lines, list):
        raise FinancialValidationError(f"{side} lines must be a list")
    indexed = {}
    for line in lines:
        required = _STATEMENT_LINE_FIELDS if statement else {"kind", "key", "amount_dec", "evidence"}
        if not isinstance(line, dict) or not required.issubset(line) or (statement and line.keys() != required):
            raise FinancialValidationError(f"{side} line has invalid fields")
        kind = _identifier(line["kind"], f"{side} line kind")
        key = _identifier(line["key"], f"{side} line key")
        if kind not in _KINDS:
            raise FinancialValidationError(f"{side} line kind is unsupported: {kind}")
        if (kind, key) in indexed:
            raise FinancialValidationError(f"Duplicate {side.lower()} line: {kind}/{key}")
        amount = line["amount_dec"]
        if not isinstance(amount, str):
            raise FinancialValidationError(f"{side} amount_dec must be a decimal string")
        dec(amount, scale=12 if kind == "HOLDING" else 8, nonnegative=statement and kind != "CASH")
        if statement:
            _identifier(line["source_record_id"], "Statement source record ID")
        else:
            evidence = line["evidence"]
            if not isinstance(evidence, list) or not evidence or not all(
                isinstance(reference, dict) and reference for reference in evidence
            ):
                raise FinancialValidationError(f"Ledger line needs evidence: {kind}/{key}")
        indexed[kind, key] = line
    return indexed


def _compare_line(kind: str, key: str, expected: dict | None, observed: dict | None) -> dict:
    expected_amount = expected["amount_dec"] if expected is not None else None
    observed_amount = observed["amount_dec"] if observed is not None else None
    difference = None
    if expected_amount is None:
        status = "UNEXPECTED_STATEMENT_LINE"
    elif observed_amount is None:
        status = "MISSING_STATEMENT_LINE"
    else:
        scale = 12 if kind == "HOLDING" else 8
        with localcontext(_DECIMAL_CONTEXT):
            delta = dec(observed_amount, scale=scale) - dec(expected_amount, scale=scale)
            difference = canonical(delta)
        status = "MATCHED" if delta == 0 else "AMOUNT_MISMATCH"
    return {
        "kind": kind,
        "key": key,
        "status": status,
        "expected_dec": expected_amount,
        "observed_dec": observed_amount,
        "difference_dec": difference,
        "statement_source_record_id": observed["source_record_id"] if observed is not None else None,
        "ledger_evidence": expected["evidence"] if expected is not None else [],
    }


def compare_reconciliation(projection: dict, statement: dict) -> dict:
    """Report exact differences with both sources and keep incompatible scopes separate.

    Difference is the observed statement value minus the expected ledger value.
    Missing groups have no value. A matching group never clears an upstream issue.
    """
    if not isinstance(statement, dict) or statement.keys() != _STATEMENT_FIELDS:
        raise FinancialValidationError("Invalid synthetic statement fields")
    if statement["mode"] != _SYNTHETIC_MODE:
        raise FinancialValidationError("Synthetic statement mode is required")
    projection_fields = {"account", "effective_at", "known_as_of", "lines", "issues"}
    if not isinstance(projection, dict) or not projection_fields.issubset(projection):
        raise FinancialValidationError("Invalid ledger projection fields")
    if projection.get("mode", _SYNTHETIC_MODE) != _SYNTHETIC_MODE:
        raise FinancialValidationError("Synthetic ledger projection mode is required")
    upstream = projection["issues"]
    if not isinstance(upstream, list) or not all(
        isinstance(issue, dict) and isinstance(issue.get("code"), str) and issue["code"] for issue in upstream
    ):
        raise FinancialValidationError("Invalid ledger projection issues")

    ledger_context = {
        "account": _identifier(projection["account"], "Ledger account"),
        "effective_at": validate_utc_seconds(projection["effective_at"]),
        "known_as_of": validate_utc_seconds(projection["known_as_of"]),
        "basis": _identifier(projection.get("basis", _BASIS), "Ledger basis"),
    }
    statement_context = {
        "statement_id": _identifier(statement["statement_id"], "Statement ID"),
        "account": _identifier(statement["account"], "Statement account"),
        "effective_at": validate_utc_seconds(statement["effective_at"]),
        "available_at": validate_utc_seconds(statement["available_at"]),
        "basis": _identifier(statement["basis"], "Statement basis"),
    }
    expected = _index_lines(projection["lines"], statement=False)
    observed = _index_lines(statement["lines"], statement=True)

    scope_issues = []
    if statement_context["account"] != ledger_context["account"]:
        scope_issues.append({"code": "ACCOUNT_MISMATCH"})
    if statement_context["effective_at"] != ledger_context["effective_at"]:
        scope_issues.append({"code": "EFFECTIVE_TIME_MISMATCH"})
    if statement_context["basis"] != _BASIS or ledger_context["basis"] != _BASIS:
        scope_issues.append({"code": "UNSUPPORTED_RECOGNITION_BASIS"})
    if statement_context["available_at"] < statement_context["effective_at"]:
        scope_issues.append({"code": "STATEMENT_AVAILABLE_BEFORE_EFFECTIVE_TIME"})
    if statement_context["available_at"] > ledger_context["known_as_of"]:
        scope_issues.append({"code": "STATEMENT_NOT_AVAILABLE"})

    issues = [*upstream, *scope_issues]
    lines = []
    if not scope_issues:
        for kind, key in sorted(expected.keys() | observed.keys()):
            pair = kind, key
            result = _compare_line(kind, key, expected.get(pair), observed.get(pair))
            lines.append(result)
            if result["status"] != "MATCHED":
                issues.append({"code": result["status"], "kind": kind, "key": key})
        if not lines:
            issues.append({"code": "NO_RECONCILIATION_LINES"})

    return {
        "schema_version": _SCHEMA_VERSION,
        "mode": _SYNTHETIC_MODE,
        "status": "UNQUALIFIED" if issues else "RECONCILED",
        "certification": "UNQUALIFIED" if issues else "INTERNAL_SYNTHETIC_MATCH",
        "statement": {
            **statement_context,
            "content_sha256": digest({
                **statement_context, "mode": _SYNTHETIC_MODE,
                "lines": [observed[pair] for pair in sorted(observed)],
            }),
        },
        "ledger": {
            **ledger_context,
            "projection_sha256": digest({
                **ledger_context, "mode": _SYNTHETIC_MODE,
                "lines": [expected[pair] for pair in sorted(expected)],
                "issues": upstream,
            }),
        },
        "lines": lines,
        "issues": issues,
    }
