"""Deterministic, long-only EUR simulations with an append-only paper ledger."""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from decimal import Context, Decimal, ROUND_DOWN, ROUND_HALF_EVEN, localcontext
from hashlib import sha256
import json
import sqlite3
from typing import Iterable

from hita_a0.money import dec, quantize_cent

from .contracts import Recommendation, SYNTHETIC_POLICY_VERSION
from .market import CASH, MarketData, Observation


ENGINE_VERSION = "hita-paper-wallet-v1"
ZERO = Decimal("0")
QUANTITY_TICK = Decimal("0.000000000001")
POSITION_LIMIT = Decimal("0.02")
SIGNAL_LOSS_LIMIT = Decimal("0.0015")
OPEN_LOSS_LIMIT = Decimal("0.006")
CLOSE_PUBLICATION_DELAY = timedelta(minutes=5)
WALLET_DECIMAL_CONTEXT = Context(prec=60, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True)
class WalletConfig:
    initial_cash_dec: str = "10000.00"
    commission_dec: str = "0.50"
    half_spread_bps_dec: str = "0"
    slippage_bps_dec: str = "0"
    cost_model_id: str = "synthetic-cost-v1"

    def __post_init__(self):
        for name in ("initial_cash_dec", "commission_dec"):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise ValueError(f"{name} must be a decimal string")
            amount = dec(value, scale=2, nonnegative=True)
            object.__setattr__(self, name, _decimal(quantize_cent(amount)))
        for name in ("half_spread_bps_dec", "slippage_bps_dec"):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise ValueError(f"{name} must be a decimal string")
            amount = dec(value, nonnegative=True)
            raw = format(amount.copy_abs() if amount == 0 else amount, "f")
            object.__setattr__(self, name, raw.rstrip("0").rstrip(".") if "." in raw else raw)
        with localcontext(WALLET_DECIMAL_CONTEXT):
            total_bps = Decimal(self.half_spread_bps_dec) + Decimal(self.slippage_bps_dec)
        if total_bps >= 10_000:
            raise ValueError("Total execution price adjustment must be below 10000 bps")
        if Decimal(self.initial_cash_dec) <= 0:
            raise ValueError("Initial paper cash must be positive")
        if not isinstance(self.cost_model_id, str) or not self.cost_model_id.strip():
            raise ValueError("A versioned cost model ID is required")

    @property
    def price_adjustment(self) -> Decimal:
        return (Decimal(self.half_spread_bps_dec) + Decimal(self.slippage_bps_dec)) / Decimal(10_000)


@dataclass(frozen=True)
class PaperDeposit:
    """Dated synthetic contribution for an independent scenario."""

    deposit_id: str
    effective_at: datetime
    amount_dec: str

    def __post_init__(self):
        if not isinstance(self.deposit_id, str) or not self.deposit_id.strip():
            raise ValueError("A paper deposit requires an ID")
        if not isinstance(self.effective_at, datetime) or self.effective_at.utcoffset() != timedelta(0):
            raise ValueError("A paper deposit requires a timezone-aware UTC timestamp")
        if not isinstance(self.amount_dec, str):
            raise ValueError("Paper deposit amounts must be decimal strings")
        amount = dec(self.amount_dec, scale=2, positive=True)
        object.__setattr__(self, "amount_dec", _decimal(quantize_cent(amount)))

    def payload(self) -> dict:
        return {"deposit_id": self.deposit_id, "effective_at": _time(self.effective_at), "amount_dec": self.amount_dec}


def _time(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.copy_abs() if value == 0 else value, "f")


# Row JSON keeps exact currency values and provenance available for SQL inspection.
# The foreign keys identify run, frozen decision and order independently of presentation.
_ROW_KEYS = {
    "wallet_signals": ("recommendation_id",),
    "wallet_orders": ("recommendation_id", "order_id"),
    "wallet_fills": ("recommendation_id", "order_id"),
    "wallet_cash_events": ("sequence",),
    "wallet_lots": ("recommendation_id", "book"),
    "wallet_lot_actions": ("sequence",),
    "wallet_nav": ("at",),
}
_ROW_COLLECTIONS = dict(zip(_ROW_KEYS, ("signals", "orders", "fills", "cash_events", "lots", "lot_actions", "nav")))
_ALL_TABLES = {"wallet_runs": ("run_id",), **{table: ("run_id", *keys) for table, keys in _ROW_KEYS.items()}}
_RELATIONS = {
    "wallet_signals": ("FOREIGN KEY (recommendation_id) REFERENCES frozen_recommendations(recommendation_id)",),
    "wallet_orders": ("FOREIGN KEY (run_id, recommendation_id) REFERENCES wallet_signals(run_id, recommendation_id)",),
    "wallet_fills": (
        "FOREIGN KEY (run_id, recommendation_id, order_id) "
        "REFERENCES wallet_orders(run_id, recommendation_id, order_id)",
    ),
    "wallet_lots": ("FOREIGN KEY (run_id, recommendation_id) REFERENCES wallet_signals(run_id, recommendation_id)",),
}


def initialize_wallet(db: sqlite3.Connection) -> None:
    """Initialize independent wallet tables only in a recognized paper database."""
    from .store import _transaction, _validate_database

    _validate_database(db)
    with _transaction(db):
        for table, keys in _ALL_TABLES.items():
            fields = []
            for key in keys:
                definition = f"{key} {'INTEGER' if key == 'sequence' else 'TEXT'} NOT NULL"
                if key == "run_id" and table != "wallet_runs":
                    definition += " REFERENCES wallet_runs(run_id)"
                fields.append(definition)
            fields.extend((
                "payload TEXT NOT NULL CHECK (json_valid(payload))",
                f"PRIMARY KEY ({', '.join(keys)})",
            ))
            fields.extend(_RELATIONS.get(table, ()))
            db.execute(f"CREATE TABLE IF NOT EXISTS {table} ({', '.join(fields)}) STRICT")
            for operation in ("UPDATE", "DELETE"):
                db.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_deny_{operation.lower()}
                    BEFORE {operation} ON {table} BEGIN
                        SELECT RAISE(ABORT, 'HITA paper history is append-only');
                    END""")
            existing = " AND ".join(f"{key} = NEW.{key}" for key in keys)
            db.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_deny_replacement
                BEFORE INSERT ON {table}
                WHEN EXISTS (SELECT 1 FROM {table} WHERE {existing}) BEGIN
                    SELECT RAISE(ABORT, 'HITA paper history is append-only');
                END""")


def read_wallet_run(db: sqlite3.Connection, run_id: str) -> dict:
    from .store import _validate_database

    _validate_database(db)
    row = db.execute("SELECT payload FROM wallet_runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise ValueError(f"Unknown wallet run: {run_id}")
    return json.loads(row[0])


def simulate_wallet(
    db: sqlite3.Connection,
    market: MarketData,
    recommendations: Iterable[Recommendation],
    *,
    as_of: datetime,
    config: WalletConfig = WalletConfig(),
    deposits: Iterable[PaperDeposit] = (),
) -> dict:
    """Run one reproducible evaluation on an explicitly synthetic, immutable input set."""
    from .store import _transaction, _validate_database, canonical_json

    _validate_database(db)
    if not isinstance(as_of, datetime) or as_of.utcoffset() != timedelta(0):
        raise ValueError("The evaluation cutoff must be a timezone-aware UTC timestamp")
    market.verify_fixture()
    contributions = sorted(deposits, key=lambda item: (item.effective_at, item.deposit_id))
    if len({item.deposit_id for item in contributions}) != len(contributions):
        raise ValueError("A deposit ID may appear only once per wallet run")
    if any(item.effective_at > as_of for item in contributions):
        raise ValueError("A deposit falls after its evaluation cutoff")
    records = sorted(recommendations, key=lambda item: (item.decision_at, item.recommendation_id))
    if len({record.recommendation_id for record in records}) != len(records):
        raise ValueError("A recommendation may appear only once per wallet run")
    for record in records:
        if record.decision_at > as_of:
            raise ValueError("A wallet run may include only decisions made by its evaluation cutoff")
        market.validate_recommendation(record)
        if record.cost_model_id != config.cost_model_id:
            raise ValueError("The frozen cost model ID differs from the wallet configuration")

    run_inputs = {
        "engine_version": ENGINE_VERSION,
        "mode": "PAPER_ONLY",
        "market_manifest_id": market.manifest_id,
        "currency": market.currency,
        "as_of": _time(as_of),
        "config": asdict(config),
        "deposits": [item.payload() for item in contributions],
        "recommendation_hashes": [record.content_sha256 for record in records],
        "settlement": "T_PLUS_ZERO",
        "fill_policy": "NEXT_OPEN_AND_SCHEDULED_HORIZON_CLOSE_ALL_OR_NONE",
        "volume_policy": "AGGREGATE_RECORDED_AUCTION_VOLUME_PER_BOOK",
        "tax_policy": "ZERO_SYNTHETIC_TAX",
        "cash_yield_dec": "0",
        "fx_policy": "EUR_IDENTITY_NO_CONVERSION",
        "risk_policy_version": SYNTHETIC_POLICY_VERSION,
        "close_publication_delay_seconds": int(CLOSE_PUBLICATION_DELAY.total_seconds()),
    }
    run_id = "wallet-" + sha256(canonical_json(run_inputs).encode("utf-8")).hexdigest()
    with _transaction(db):
        for record in records:
            row = db.execute(
                "SELECT content_sha256, payload FROM frozen_recommendations WHERE recommendation_id = ?",
                (record.recommendation_id,),
            ).fetchone()
            if row is None or tuple(row) != (record.content_sha256, canonical_json(record.to_dict())):
                raise ValueError(f"Exact frozen recommendation is required: {record.recommendation_id}")
        cached = db.execute("SELECT payload FROM wallet_runs WHERE run_id = ?", (run_id,)).fetchone()
        if cached is not None:
            return json.loads(cached[0])
        with localcontext(WALLET_DECIMAL_CONTEXT):
            report = _Simulation(market, as_of, config).run(records, contributions)
        report = {"run_id": run_id, **run_inputs, **report}
        db.execute("INSERT INTO wallet_runs VALUES (?, ?)", (run_id, canonical_json(report)))
        for table, keys in _ROW_KEYS.items():
            placeholders = ", ".join("?" for _ in range(len(keys) + 2))
            collection = _ROW_COLLECTIONS[table]
            rows = report[collection] + report["benchmark"].get(collection, [])
            db.executemany(
                f"INSERT INTO {table} VALUES ({placeholders})",
                (
                    (run_id, *(row[key] for key in keys), canonical_json(row))
                    for row in rows
                ),
            )
    return report


@dataclass
class _Lot:
    recommendation: Recommendation
    book: str
    instrument_id: str
    entry_at: datetime
    exit_at: datetime | None
    active_quantity: Decimal
    opening_quantity: Decimal
    committed: Decimal
    raw_entry_notional: Decimal
    capital_base: Decimal
    planned_loss: Decimal
    extra_matched_cash: Decimal = ZERO
    entitlement: Decimal = ZERO
    delisted_at: datetime | None = None
    sold_cash: Decimal | None = None
    raw_exit_notional: Decimal | None = None
    state: str = "OPEN"

    @property
    def recommendation_id(self) -> str:
        return self.recommendation.recommendation_id

    def payload(self) -> dict:
        return {
            "recommendation_id": self.recommendation_id,
            "book": self.book,
            "instrument_id": self.instrument_id,
            "entry_at": _time(self.entry_at),
            "scheduled_exit_at": _time(self.exit_at) if self.exit_at else None,
            "opening_quantity_dec": _decimal(self.opening_quantity),
            "quantity_dec": _decimal(self.active_quantity),
            "entry_cost_basis_dec": _decimal(self.committed),
            "cost_basis_dec": _decimal(ZERO if self.state == "CLOSED" else self.committed),
            "dividend_entitlement_dec": _decimal(self.entitlement),
            "state": self.state,
        }


@dataclass
class _Entitlement:
    lot: _Lot
    action_id: str
    amount: Decimal
    payable_at: datetime
    settled: bool = False


@dataclass
class _Book:
    name: str
    cash: Decimal
    lots: dict[str, _Lot] = field(default_factory=dict)
    entitlements: list[_Entitlement] = field(default_factory=list)
    used_volume: dict[tuple[str, datetime], Decimal] = field(default_factory=lambda: defaultdict(Decimal))

    @property
    def receivables(self) -> Decimal:
        return sum((item.amount for item in self.entitlements if not item.settled), ZERO)


@dataclass(frozen=True)
class _Trade:
    observation: Observation
    quantity: Decimal
    execution_price: Decimal
    notional: Decimal
    commission: Decimal
    raw_notional: Decimal
    side: str

    @property
    def cash_delta(self) -> Decimal:
        return (-self.notional if self.side == "BUY" else self.notional) - self.commission


class _Simulation:
    def __init__(self, market: MarketData, as_of: datetime, config: WalletConfig):
        self.market = market
        self.as_of = as_of
        self.config = config
        self.initial = Decimal(config.initial_cash_dec)
        self.commission = Decimal(config.commission_dec)
        self.model = _Book("MODEL", ZERO)
        self.benchmark = _Book("BENCHMARK", ZERO)
        self.signals: dict[str, dict] = {}
        self.benchmark_missing = False
        self.orders: list[dict] = []
        self.fills: list[dict] = []
        self.cash_events: list[dict] = []
        self.lot_actions: list[dict] = []
        self.nav: list[dict] = []
        self.flows: list[dict] = []

    def run(self, records: list[Recommendation], deposits: list[PaperDeposit]) -> dict:
        entries: dict[datetime, list[tuple[Recommendation, datetime | None]]] = defaultdict(list)
        exits: dict[datetime, list[str]] = defaultdict(list)
        for record in records:
            signal = {
                "recommendation_id": record.recommendation_id,
                "action": record.action,
                "decision_at": _time(record.decision_at),
                "instrument_id": record.instrument_id,
                "benchmark_id": record.benchmark_id,
                "disposition": "OBSERVING",
                "reason_code": "AWAITING_ENTRY_SESSION",
                "policy_result": record.policy_result,
                "policy_reason_codes": list(record.reason_codes),
                "risk_policy_version": record.risk_policy_version,
                "model_version": record.model_version,
                "expected_entry_at": None,
                "expected_exit_at": None,
                "outcome_status": "UNMATURED",
                "position_net_pnl_dec": None,
                "committed_capital_dec": None,
                "gross_asset_return_dec": None,
                "net_follow_return_dec": None,
                "benchmark_return_dec": None,
                "excess_return_dec": None,
                "absolute_result": "NOT_APPLICABLE",
                "relative_result": "NOT_COMPARABLE",
                "risk_result": "UNKNOWN",
            }
            self.signals[record.recommendation_id] = signal
            reason = self._decision_gate(record)
            if reason:
                self._set(signal, *reason)
                continue
            entry = self.market.next_open(record.decision_at)
            if entry is None:
                self._set(signal, "UNFILLED", "DATA_INSUFFICIENT", "NO_FUTURE_SESSION_IN_FIXTURE")
                continue
            horizon = self.market.horizon(entry.open_at, record.horizon_trading_days)
            exit_at = horizon.close_at if horizon else None
            signal["expected_entry_at"] = _time(entry.open_at)
            signal["expected_exit_at"] = _time(exit_at) if exit_at else None
            entries[entry.open_at].append((record, exit_at))
            if exit_at is not None:
                exits[exit_at].append(record.recommendation_id)

        start = min(min((item.decision_at for item in records), default=self.as_of),
                    deposits[0].effective_at if deposits else self.as_of)
        self._cash_event(self.model, start, "FUNDING", self.initial)
        self._cash_event(self.benchmark, start, "FUNDING", self.initial)
        expected_marks = sorted(
            at for session in self.market.sessions for at in (session.open_at, session.close_at)
            if at <= self.as_of
        )
        closes = {session.close_at for session in self.market.sessions if start <= session.close_at <= self.as_of}
        contributions: dict[datetime, list[PaperDeposit]] = defaultdict(list)
        for item in deposits:
            contributions[item.effective_at].append(item)
        ticks = {start, self.as_of, *closes, *entries, *exits, *contributions}
        actions = defaultdict(list)
        symbols = {instrument for record in records for instrument in (record.instrument_id, record.benchmark_id)} - {CASH}
        for symbol in sorted(symbols):
            for action in self.market.actions(symbol, start, self.as_of, self.as_of):
                actions[action.effective_at].append(action)
                ticks.add(action.effective_at)
                if action.payable_at is not None:
                    ticks.add(action.payable_at)

        for at in sorted(tick for tick in ticks if start <= tick <= self.as_of):
            index = bisect_right(expected_marks, at) - 1
            mark_at = expected_marks[index] if index >= 0 else None
            for book in (self.model, self.benchmark):
                for action in actions[at]:
                    self._corporate_action(book, action)
                self._pay_dividends(book, at)
            for deposit in contributions.get(at, ()):
                self._deposit(deposit, mark_at)
            for recommendation_id in exits[at]:
                self._exit(recommendation_id, at)
            for record, exit_at in entries[at]:
                self._entry(record, at, exit_at)
            if at in closes or at in contributions or at in (start, self.as_of):
                self._snapshot(at, mark_at)
        return self._report()

    @staticmethod
    def _set(signal: dict, disposition: str, outcome_status: str, reason: str) -> None:
        signal.update(disposition=disposition, outcome_status=outcome_status, reason_code=reason)

    @staticmethod
    def _decision_gate(record: Recommendation) -> tuple[str, str, str] | None:
        if record.action in ("ABSTAIN", "AVOID", "HOLD", "WATCHLIST"):
            return ("NO_ORDER", "NOT_APPLICABLE", record.reason_codes[0])
        if record.policy_result != "ALLOW":
            return ("BLOCKED", "UNFILLED", next(iter(record.reason_codes), "POLICY_NOT_ALLOWED"))
        if record.action == "CONSIDER_REDUCE":
            return ("NO_ORDER", "NOT_APPLICABLE", "DISCRETIONARY_REDUCTION_UNSUPPORTED")
        if record.capital_base_dec is None or Decimal(record.capital_base_dec) <= 0:
            return ("BLOCKED", "UNFILLED", "MISSING_APPROVED_DENOMINATOR")
        if record.risk_policy_version != SYNTHETIC_POLICY_VERSION or record.strategy_sleeve != "SATELLITE":
            return ("BLOCKED", "UNFILLED", "UNSUPPORTED_SYNTHETIC_RISK_POLICY")
        if record.quantity_dec is None or Decimal(record.quantity_dec) <= 0:
            return ("BLOCKED", "UNFILLED", "MISSING_POSITIVE_QUANTITY")
        if record.planned_loss_dec is None or Decimal(record.planned_loss_dec) <= 0:
            return ("BLOCKED", "UNFILLED", "MISSING_PLANNED_LOSS")
        return None

    def _quote(self, instrument: str, at: datetime, known_at: datetime) -> tuple[Observation | None, str | None]:
        quote = self.market.bar(instrument, at, known_at)
        if quote is not None:
            return quote, None
        raw = self.market.raw_bar(instrument, at, known_at)
        return None, (f"MARKET_{raw.condition}" if raw else "MARKET_OBSERVATION_UNAVAILABLE")

    def _trade(self, book: _Book, quote: Observation, quantity: Decimal, side: str) -> tuple[_Trade, str | None]:
        adjustment = self.config.price_adjustment if side == "BUY" else -self.config.price_adjustment
        raw_price = Decimal(quote.price_dec)
        price = raw_price * (1 + adjustment)
        trade = _Trade(quote, quantity, price, quantize_cent(price * quantity), self.commission,
                       raw_price * quantity, side)
        if trade.notional <= 0:
            return trade, "BELOW_CURRENCY_PRECISION"
        available_volume = Decimal(quote.volume_dec) - book.used_volume[quote.instrument_id, quote.at]
        if available_volume < quantity:
            return trade, "PARTIAL_NOT_SUPPORTED"
        if book.cash + trade.cash_delta < 0:
            return trade, "INSUFFICIENT_CASH"
        return trade, None

    def _risk_reason(self, record: Recommendation, at: datetime, trade: _Trade) -> str | None:
        base = Decimal(record.capital_base_dec)
        planned = Decimal(record.planned_loss_dec)
        if planned < -trade.cash_delta + self.commission:
            return "PLANNED_LOSS_BELOW_ZERO_RECOVERY_BOUND"
        if planned > base * SIGNAL_LOSS_LIMIT:
            return "SYNTHETIC_PER_TRADE_RISK_LIMIT"
        open_lots = [lot for lot in self.model.lots.values() if lot.state != "CLOSED"]
        if any(lot.delisted_at is not None for lot in open_lots):
            return "UNVALUED_RISK_EXPOSURE"
        exposure = trade.quantity
        exposure_base = base
        for lot in open_lots:
            if lot.instrument_id == record.instrument_id:
                exposure += lot.active_quantity
                exposure_base = min(exposure_base, lot.capital_base)
            else:
                observation = self.market.bar(lot.instrument_id, at, at)
                if observation is None:
                    return "UNVALUED_RISK_EXPOSURE"
        if exposure * max(Decimal(trade.observation.price_dec), trade.execution_price) > exposure_base * POSITION_LIMIT:
            return "SYNTHETIC_POSITION_SIZE_LIMIT"
        aggregate_base = min((lot.capital_base for lot in open_lots), default=base)
        aggregate_loss = sum((lot.planned_loss for lot in open_lots), planned)
        if aggregate_loss > min(base, aggregate_base) * OPEN_LOSS_LIMIT:
            return "SYNTHETIC_AGGREGATE_RISK_LIMIT"
        return None

    def _entry(self, record: Recommendation, at: datetime, exit_at: datetime | None) -> None:
        signal = self.signals[record.recommendation_id]
        quote, reason = self._quote(record.instrument_id, at, at)
        if quote is None:
            self._order(self.model, record, record.instrument_id, at, "BUY", record.quantity_dec, "EXPIRED", reason)
            self._set(signal, "UNFILLED", "UNFILLED", reason)
            return
        quantity = Decimal(record.quantity_dec)
        trade, rejection = self._trade(self.model, quote, quantity, "BUY")
        risk = self._risk_reason(record, at, trade)
        if risk:
            self._order(self.model, record, record.instrument_id, at, "BUY", record.quantity_dec, "REJECTED", risk)
            self._set(signal, "BLOCKED", "UNFILLED", risk)
            return
        signal["risk_result"] = "COMPLIANT"
        if rejection:
            self._order(self.model, record, record.instrument_id, at, "BUY", record.quantity_dec, "REJECTED", rejection)
            self._set(signal, "UNFILLED", "UNFILLED", rejection)
            return
        lot = self._buy(self.model, record, trade, exit_at)
        self._set(signal, "FILLED_OPEN", "UNMATURED", "AWAITING_HORIZON")
        signal["committed_capital_dec"] = _decimal(lot.committed)
        if exit_at is None:
            self._set(signal, "CENSORED", "CENSORED", "HORIZON_OUTSIDE_FIXTURE")
        if record.benchmark_id == CASH:
            return
        benchmark_quote, reason = self._quote(record.benchmark_id, at, at)
        if benchmark_quote is None:
            self._order(self.benchmark, record, record.benchmark_id, at, "BUY", None, "EXPIRED", reason)
            self.benchmark_missing = True
            return
        benchmark_price = Decimal(benchmark_quote.price_dec) * (1 + self.config.price_adjustment)
        matched_quantity = ((lot.committed - self.commission) / benchmark_price).quantize(
            QUANTITY_TICK, rounding=ROUND_DOWN,
        )
        benchmark_trade, rejection = self._trade(self.benchmark, benchmark_quote, matched_quantity, "BUY")
        if rejection:
            self._order(self.benchmark, record, record.benchmark_id, at, "BUY", _decimal(matched_quantity),
                        "REJECTED", rejection)
            self.benchmark_missing = True
            return
        matched = self._buy(self.benchmark, record, benchmark_trade, exit_at)
        matched.extra_matched_cash = lot.committed - matched.committed

    def _order(self, book, record, instrument, at, side, quantity, status, reason=None) -> str:
        order_id = f"{book.name}:{side}:{record.recommendation_id}"
        self.orders.append({
            "order_id": order_id, "book": book.name, "recommendation_id": record.recommendation_id,
            "originating_decision_at": _time(record.decision_at),
            "instrument_id": instrument, "side": side, "eligible_at": _time(at), "quantity_dec": quantity,
            "status": status, "reason_code": reason, "time_in_force": "ONE_SCHEDULED_AUCTION",
        })
        return order_id

    def _fill(self, book: _Book, record: Recommendation, trade: _Trade) -> None:
        quote = trade.observation
        order_id = self._order(book, record, quote.instrument_id, quote.at, trade.side, _decimal(trade.quantity),
                               "FILLED")
        book.used_volume[quote.instrument_id, quote.at] += trade.quantity
        reference_notional = quantize_cent(trade.raw_notional)
        difference = (trade.notional - reference_notional
                      if trade.side == "BUY" else reference_notional - trade.notional)
        self.fills.append({
            "fill_id": order_id, "order_id": order_id, "book": book.name,
            "recommendation_id": record.recommendation_id, "instrument_id": quote.instrument_id,
            "side": trade.side, "effective_at": _time(quote.at), "observation_available_at": _time(quote.available_at),
            "observation_id": quote.observation_id, "reference_price_dec": quote.price_dec,
            "execution_price_dec": _decimal(trade.execution_price), "quantity_dec": _decimal(trade.quantity),
            "notional_dec": _decimal(trade.notional), "commission_dec": _decimal(trade.commission),
            "spread_slippage_cost_dec": _decimal(difference), "cash_delta_dec": _decimal(trade.cash_delta),
        })
        principal = -trade.notional if trade.side == "BUY" else trade.notional
        # One cash event posts all-in settlement atomically; principal and expenses remain visible.
        self._cash_event(book, quote.at, f"{trade.side}_SETTLEMENT", trade.cash_delta,
                         recommendation_id=record.recommendation_id, order_id=order_id,
                         principal_dec=_decimal(principal), cost_dec=_decimal(trade.commission))

    def _cash_event(self, book, at, event_type, amount, **links) -> None:
        book.cash += amount
        self.cash_events.append({
            "sequence": len(self.cash_events) + 1, "book": book.name, "effective_at": _time(at),
            "event_type": event_type, "amount_dec": _decimal(amount), "balance_dec": _decimal(book.cash), **links,
        })

    def _deposit(self, deposit: PaperDeposit, mark_at: datetime | None) -> None:
        amount = Decimal(deposit.amount_dec)
        flow = deposit.payload()
        for book in (self.model, self.benchmark):
            value, _ = self._marked(book, mark_at, deposit.effective_at)
            pre_flow_nav = None
            if value is not None and (book is self.model or not self.benchmark_missing):
                pre_flow_nav = book.cash + value + book.receivables
            flow[f"{book.name.lower()}_pre_flow_nav_dec"] = _decimal(pre_flow_nav)
            self._cash_event(book, deposit.effective_at, "DEPOSIT", amount, deposit_id=deposit.deposit_id)
        self.flows.append(flow)

    def _time_weighted_return(self, ending_nav: Decimal | None, book: _Book) -> Decimal | None:
        if ending_nav is None:
            return None
        if not self.flows:
            return (ending_nav - self.initial) / self.initial
        segment_start = self.initial
        cumulative_growth = Decimal("1")
        for flow in self.flows:
            valuation = flow[f"{book.name.lower()}_pre_flow_nav_dec"]
            if valuation is None or segment_start <= 0:
                return None
            pre_flow = Decimal(valuation)
            cumulative_growth *= pre_flow / segment_start
            segment_start = pre_flow + Decimal(flow["amount_dec"])
        return cumulative_growth * (ending_nav / segment_start) - 1 if segment_start > 0 else None

    def _buy(self, book, record, trade, exit_at) -> _Lot:
        self._fill(book, record, trade)
        lot = _Lot(record, book.name, trade.observation.instrument_id, trade.observation.at, exit_at,
                   trade.quantity, trade.quantity, -trade.cash_delta, trade.raw_notional,
                   Decimal(record.capital_base_dec), Decimal(record.planned_loss_dec))
        book.lots[record.recommendation_id] = lot
        return lot

    def _sell(self, book: _Book, lot: _Lot, at: datetime) -> str | None:
        if lot.delisted_at is not None:
            return "DELISTED_WITHOUT_PROCEEDS"
        publication_at = at + CLOSE_PUBLICATION_DELAY
        quote, reason = self._quote(lot.instrument_id, at, min(self.as_of, publication_at))
        if quote is None and self.as_of < publication_at:
            return "AWAITING_CLOSE_PUBLICATION"
        if quote is None:
            self._order(book, lot.recommendation, lot.instrument_id, at, "SELL", _decimal(lot.active_quantity),
                        "EXPIRED", reason)
            return reason
        trade, reason = self._trade(book, quote, lot.active_quantity, "SELL")
        if reason:
            self._order(book, lot.recommendation, lot.instrument_id, at, "SELL", _decimal(lot.active_quantity),
                        "REJECTED", reason)
            return reason
        self._fill(book, lot.recommendation, trade)
        lot.sold_cash = trade.cash_delta
        lot.raw_exit_notional = trade.raw_notional
        lot.active_quantity = ZERO
        lot.state = "CLOSED"
        return None

    def _exit(self, recommendation_id: str, at: datetime) -> None:
        lot = self.model.lots.get(recommendation_id)
        if lot is None:
            return
        signal = self.signals[recommendation_id]
        reason = self._sell(self.model, lot, at)
        matched = self.benchmark.lots.get(recommendation_id)
        benchmark_reason = self._sell(self.benchmark, matched, at) if matched is not None else None
        if benchmark_reason and benchmark_reason != "AWAITING_CLOSE_PUBLICATION":
            self.benchmark_missing = True
        if reason == "AWAITING_CLOSE_PUBLICATION":
            self._set(signal, "FILLED_OPEN", "UNMATURED", reason)
            return
        if reason:
            self._set(signal, "CENSORED", "CENSORED", reason)
            return
        self._set(signal, "MATURED", "MATURED", "HORIZON_TRADE_CLOSED")
        pnl = lot.sold_cash + lot.entitlement - lot.committed
        net = pnl / lot.committed
        gross = (lot.raw_exit_notional + lot.entitlement - lot.raw_entry_notional) / lot.raw_entry_notional
        benchmark_return = None
        if lot.recommendation.benchmark_id == CASH:
            benchmark_return = ZERO
        elif matched is not None and matched.state == "CLOSED" and not benchmark_reason:
            benchmark_pnl = matched.sold_cash + matched.entitlement - matched.committed
            benchmark_return = benchmark_pnl / (matched.committed + matched.extra_matched_cash)
        relative = "NOT_COMPARABLE"
        excess = None
        if benchmark_return is not None:
            excess = net - benchmark_return
            relative = "OUTPERFORMED" if excess > 0 else ("UNDERPERFORMED" if excess < 0 else "TIED")
        signal.update(
            position_net_pnl_dec=_decimal(pnl), gross_asset_return_dec=_decimal(gross),
            net_follow_return_dec=_decimal(net), benchmark_return_dec=_decimal(benchmark_return),
            excess_return_dec=_decimal(excess), relative_result=relative,
            absolute_result="POSITIVE" if pnl > 0 else ("NEGATIVE" if pnl < 0 else "FLAT"),
        )

    def _corporate_action(self, book, action) -> None:
        for lot in book.lots.values():
            if lot.instrument_id != action.instrument_id or lot.state != "OPEN" or lot.entry_at >= action.effective_at:
                continue
            quantity_before = lot.active_quantity
            entitlement = None
            if action.kind == "SPLIT":
                lot.active_quantity *= Decimal(action.ratio_dec)
            elif action.kind == "DIVIDEND":
                entitlement = quantize_cent(lot.active_quantity * Decimal(action.amount_dec))
                lot.entitlement += entitlement
                book.entitlements.append(_Entitlement(lot, action.action_id, entitlement, action.payable_at))
            elif action.kind == "DELIST":
                lot.delisted_at = action.effective_at
                lot.state = "UNPRICED_DELISTED"
                if book is self.model:
                    self._set(self.signals[lot.recommendation_id], "CENSORED", "CENSORED", "DELISTED_WITHOUT_PROCEEDS")
                else:
                    self.benchmark_missing = True
            self.lot_actions.append({
                "sequence": len(self.lot_actions) + 1, "book": book.name,
                "recommendation_id": lot.recommendation_id, "instrument_id": action.instrument_id,
                "action_id": action.action_id, "kind": action.kind, "effective_at": _time(action.effective_at),
                "available_at": _time(action.available_at), "quantity_before_dec": _decimal(quantity_before),
                "quantity_after_dec": _decimal(lot.active_quantity), "cost_basis_dec": _decimal(lot.committed),
                "entitlement_dec": _decimal(entitlement),
                "payable_at": _time(action.payable_at) if action.payable_at else None,
            })

    def _pay_dividends(self, book, at) -> None:
        for item in book.entitlements:
            if not item.settled and item.payable_at <= at:
                item.settled = True
                self._cash_event(book, at, "DIVIDEND", item.amount, action_id=item.action_id,
                                 recommendation_id=item.lot.recommendation_id)

    def _marked(self, book: _Book, at: datetime | None, snapshot_at: datetime) -> tuple[Decimal | None, list[dict]]:
        value = ZERO
        marks = []
        for lot in book.lots.values():
            if lot.state == "CLOSED":
                continue
            if snapshot_at.date() > self.market.sessions[-1].date:
                return None, marks
            quote = (
                self.market.bar(lot.instrument_id, at, self.as_of)
                if at is not None and at >= lot.entry_at and lot.delisted_at is None else None
            )
            if quote is None:
                return None, marks
            # If an action occurred after the quote, the quote's earlier unit basis is no longer qualified.
            adjusted_after_mark = self.market.actions(lot.instrument_id, at, snapshot_at, self.as_of)
            if any(action.kind == "SPLIT" for action in adjusted_after_mark):
                return None, marks
            market_value = quantize_cent(lot.active_quantity * Decimal(quote.price_dec))
            value += market_value
            marks.append({
                "instrument_id": lot.instrument_id, "observation_id": quote.observation_id,
                "available_at": _time(quote.available_at), "quantity_dec": _decimal(lot.active_quantity),
                "price_dec": quote.price_dec, "marked_value_dec": _decimal(market_value),
            })
        return value, marks

    def _snapshot(self, at: datetime, mark_at: datetime | None) -> None:
        value, marks = self._marked(self.model, mark_at, at)
        benchmark_value, benchmark_marks = self._marked(self.benchmark, mark_at, at)
        nav = self.model.cash + value + self.model.receivables if value is not None else None
        matched_nav = None
        if benchmark_value is not None and not self.benchmark_missing:
            matched_nav = self.benchmark.cash + benchmark_value + self.benchmark.receivables
        self.nav.append({
            "at": _time(at), "mark_at": _time(mark_at) if mark_at else None,
            "valuation_knowledge_at": _time(self.as_of),
            "status": "VALUED" if nav is not None else "NOT_CERTIFIED",
            "cash_dec": _decimal(self.model.cash), "securities_value_dec": _decimal(value),
            "receivables_dec": _decimal(self.model.receivables), "payables_dec": "0",
            "nav_dec": _decimal(nav), "benchmark_nav_dec": _decimal(matched_nav),
            "time_weighted_return_dec": _decimal(self._time_weighted_return(nav, self.model)),
            "benchmark_time_weighted_return_dec": _decimal(self._time_weighted_return(matched_nav, self.benchmark)),
            "benchmark_cash_dec": _decimal(self.benchmark.cash),
            "benchmark_receivables_dec": _decimal(self.benchmark.receivables),
            "benchmark_securities_value_dec": _decimal(benchmark_value),
            "marks": marks, "benchmark_marks": benchmark_marks,
        })

    def _report(self) -> dict:
        last = self.nav[-1]
        ending = Decimal(last["nav_dec"]) if last["nav_dec"] is not None else None
        contributed = sum((Decimal(flow["amount_dec"]) for flow in self.flows), ZERO)
        baseline = self.initial + contributed
        pnl = ending - baseline if ending is not None else None
        benchmark_ending = last["benchmark_nav_dec"]
        returns = last["time_weighted_return_dec"], last["benchmark_time_weighted_return_dec"]
        excess = Decimal(returns[0]) - Decimal(returns[1]) if all(item is not None for item in returns) else None
        max_drawdown = ZERO
        peak = Decimal("1")
        if all(row["time_weighted_return_dec"] is not None for row in self.nav):
            for row in self.nav:
                wealth_index = 1 + Decimal(row["time_weighted_return_dec"])
                peak = max(peak, wealth_index)
                max_drawdown = max(max_drawdown, (peak - wealth_index) / peak)
        else:
            max_drawdown = None
        def book_rows(book: _Book) -> dict:
            return {
                "orders": [row for row in self.orders if row["book"] == book.name],
                "fills": [row for row in self.fills if row["book"] == book.name],
                "cash_events": [row for row in self.cash_events if row["book"] == book.name],
                "lots": [lot.payload() for lot in book.lots.values()],
                "lot_actions": [row for row in self.lot_actions if row["book"] == book.name],
            }

        return {
            "status": last["status"], "initial_cash_dec": _decimal(self.initial),
            "ending_cash_dec": last["cash_dec"], "ending_nav_dec": last["nav_dec"],
            "wallet_pnl_dec": _decimal(pnl), "wallet_return_dec": returns[0],
            "return_method": "TIME_WEIGHTED_RETURN",
            "total_external_deposits_dec": _decimal(contributed), "external_cash_flows": self.flows,
            "max_drawdown_dec": _decimal(max_drawdown), "benchmark_ending_nav_dec": benchmark_ending,
            "benchmark_return_dec": returns[1],
            "wallet_excess_return_dec": _decimal(excess),
            "cash_only_ending_nav_dec": _decimal(baseline),
            "cash_only_return_dec": "0",
            "benchmark_policy": "MATCHED_MODEL_COMMITTED_CAPITAL_AND_SCHEDULED_AUCTIONS",
            **book_rows(self.model),
            "benchmark": book_rows(self.benchmark),
            "nav": self.nav, "signals": list(self.signals.values()),
        }
