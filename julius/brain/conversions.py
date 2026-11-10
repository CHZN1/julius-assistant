"""Dimension-checked units and explicit ISO date/time calculations."""

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

# Base units: metres, kilograms, seconds, litres. No live exchange rates.
UNITS = {
    "m": ("length", "1"),
    "km": ("length", "1000"),
    "cm": ("length", "0.01"),
    "mm": ("length", "0.001"),
    "in": ("length", "0.0254"),
    "ft": ("length", "0.3048"),
    "yd": ("length", "0.9144"),
    "mi": ("length", "1609.344"),
    "kg": ("mass", "1"),
    "g": ("mass", "0.001"),
    "lb": ("mass", "0.45359237"),
    "oz": ("mass", "0.028349523125"),
    "s": ("time", "1"),
    "min": ("time", "60"),
    "h": ("time", "3600"),
    "day": ("time", "86400"),
    "l": ("volume", "1"),
    "ml": ("volume", "0.001"),
    "us_gal": ("volume", "3.785411784"),
    "us_cup": ("volume", "0.2365882365"),
}
ALIASES = {
    "meters": "m",
    "metres": "m",
    "meter": "m",
    "kilometers": "km",
    "kilometres": "km",
    "miles": "mi",
    "mile": "mi",
    "feet": "ft",
    "foot": "ft",
    "inches": "in",
    "inch": "in",
    "pounds": "lb",
    "pound": "lb",
    "kilograms": "kg",
    "grams": "g",
    "ounces": "oz",
    "seconds": "s",
    "minutes": "min",
    "hours": "h",
    "days": "day",
    "liters": "l",
    "litres": "l",
    "celsius": "c",
    "fahrenheit": "f",
    "kelvin": "k",
}


def convert_units(value, from_unit, to_unit):
    def unit(raw):
        name = raw.strip().lower().replace("°", "")
        return ALIASES.get(name, name)

    a, b = unit(from_unit), unit(to_unit)
    try:
        n = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("Please supply a finite number.")
    if not n.is_finite() or abs(n) > Decimal("1e100"):
        raise ValueError("Value exceeds the supported range.")
    if a in {"c", "f", "k"} and b in {"c", "f", "k"}:
        k = (
            n
            if a == "k"
            else n + Decimal("273.15")
            if a == "c"
            else (n - 32) * 5 / 9 + Decimal("273.15")
        )
        if k < 0:
            raise ValueError("Temperature is below absolute zero.")
        answer = (
            k
            if b == "k"
            else k - Decimal("273.15")
            if b == "c"
            else (k - Decimal("273.15")) * 9 / 5 + 32
        )
    elif a in UNITS and b in UNITS and UNITS[a][0] == UNITS[b][0]:
        answer = n * Decimal(UNITS[a][1]) / Decimal(UNITS[b][1])
    else:
        raise ValueError(
            "Units must have the same dimension. Specify US gallons/cups explicitly; currencies are not supported."
        )
    return f"{n} {a} = {answer.normalize():.12g} {b}"


def local_datetime(value, zone):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    tz = ZoneInfo(zone)
    if parsed.tzinfo is not None:
        return parsed.astimezone(tz)
    options = []
    for fold in (0, 1):
        aware = parsed.replace(tzinfo=tz, fold=fold)
        if aware.astimezone(UTC).astimezone(tz).replace(tzinfo=None) == parsed:
            if not options or aware.utcoffset() != options[0].utcoffset():
                options.append(aware)
    if not options:
        raise ValueError(
            "That local time does not exist because the clock changes. Choose another time."
        )
    if len(options) > 1:
        raise ValueError("That local time occurs twice. Include its explicit UTC offset.")
    return options[0]


def date_calculate(
    start, operation, timezone_name="America/Los_Angeles", amount=0, end=None, target_timezone=None
):
    dt = local_datetime(start, timezone_name)
    if operation == "convert_timezone":
        if not target_timezone:
            raise ValueError("Please specify a destination time zone.")
        return dt.astimezone(ZoneInfo(target_timezone)).isoformat()
    if operation == "difference":
        if not end:
            raise ValueError("Please specify an end date/time.")
        other = local_datetime(end, timezone_name)
        return (
            f"{(other.astimezone(UTC) - dt.astimezone(UTC)).total_seconds() / 3600:g} elapsed hours"
        )
    if (
        operation not in {"add_days", "add_hours"}
        or not isinstance(amount, (int, float))
        or isinstance(amount, bool)
        or not math.isfinite(amount)
        or abs(amount) > 36600
    ):
        raise ValueError(
            "Use add_days, add_hours, difference or convert_timezone with a bounded amount."
        )
    if operation == "add_days":
        if int(amount) != amount:
            raise ValueError("Calendar days must be a whole number; use hours for fractions.")
        target = dt.replace(tzinfo=None) + timedelta(days=amount)
        return local_datetime(target.isoformat(), timezone_name).isoformat()
    return (
        (dt.astimezone(UTC) + timedelta(hours=amount))
        .astimezone(ZoneInfo(timezone_name))
        .isoformat()
    )
