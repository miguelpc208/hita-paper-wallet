"""Strict, deterministic financial decimal rules; floats are deliberately forbidden."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
import re

class FinancialValidationError(ValueError):
    pass

RAW_DECIMAL = re.compile(r'^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$')
CENT = Decimal('0.01')

def dec(value, whole=16, scale=8, positive=False, nonnegative=False):
    if isinstance(value, float) or isinstance(value, bool) or value is None:
        raise FinancialValidationError('float/bool/NULL prohibited for authoritative amount')
    s = str(value)
    if not RAW_DECIMAL.fullmatch(s):
        raise FinancialValidationError(f'invalid canonical decimal input: {s!r}')
    try:
        value = Decimal(s)
    except InvalidOperation as exc:
        raise FinancialValidationError('invalid decimal') from exc
    if not value.is_finite():
        raise FinancialValidationError('nonfinite decimal')
    exponent = -value.as_tuple().exponent
    if exponent > scale:
        raise FinancialValidationError(f'scale>{scale}: {s}')
    if value != 0 and value.adjusted() >= whole:
        raise FinancialValidationError(f'magnitude beyond {whole} whole digits: {s}')
    if positive and value <= 0:
        raise FinancialValidationError('strictly positive amount required')
    if nonnegative and value < 0:
        raise FinancialValidationError('nonnegative amount required')
    return value

def canonical(value):
    if not isinstance(value, Decimal):
        value = dec(value)
    if value == 0:
        return '0'
    return format(value.normalize(), 'f')

def monetary(value):
    v = dec(value)
    return canonical(v)

def quantity(value, positive=False, nonnegative=False):
    v = dec(value, whole=16, scale=12, positive=positive, nonnegative=nonnegative)
    return canonical(v)

def quantize_cent(value):
    if not isinstance(value, Decimal):
        value = dec(value, whole=16, scale=12)
    with localcontext() as ctx:
        ctx.prec = 60
        return value.quantize(CENT, rounding=ROUND_HALF_EVEN)

def gross(qty, price):
    with localcontext() as ctx:
        ctx.prec = 60
        return quantize_cent(dec(qty,whole=16,scale=12,positive=True) * dec(price,whole=16,scale=12,nonnegative=True))

