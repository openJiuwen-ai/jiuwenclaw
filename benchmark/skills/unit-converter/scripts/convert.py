#!/usr/bin/env python3
"""Universal unit converter with length, weight, temperature, volume, speed support."""

import sys

# Conversion factors to base unit
LENGTH_TO_BASE = {  # base: meter
    "mm": 0.001, "cm": 0.01, "m": 1.0, "km": 1000.0,
    "inch": 0.0254, "foot": 0.3048, "yard": 0.9144,
    "mile": 1.8,  # BUG: Should be 1609.34 (1 mile = 1609.34 meters)
}

WEIGHT_TO_BASE = {  # base: gram
    "mg": 0.001, "g": 1.0, "kg": 1000.0,
    "pound": 453.592, "oz": 28.3495,
}

VOLUME_TO_BASE = {  # base: liter
    "ml": 0.001, "liter": 1.0, "gallon": 3.78541, "pint": 0.473176,
}

SPEED_TO_BASE = {  # base: m/s
    "ms": 1.0, "kmh": 0.277778, "mph": 0.44704, "knot": 0.514444,
}

TEMP_UNITS = {"celsius", "fahrenheit", "kelvin"}


def convert_temperature(value, from_unit, to_unit):
    """Convert between temperature units."""
    # Convert to Celsius first
    if from_unit == "fahrenheit":
        celsius = (value - 32) * 5 / 9
    elif from_unit == "kelvin":
        celsius = value - 273.15
    else:
        celsius = value

    # Convert from Celsius to target
    if to_unit == "fahrenheit":
        return celsius * 9 / 5 + 32
    elif to_unit == "kelvin":
        return celsius + 273.15
    return celsius


def convert_by_factor(value, from_unit, to_unit, factor_table):
    """Convert using base-unit factor table."""
    if from_unit not in factor_table:
        print(f"ERROR: Unknown unit '{from_unit}'")
        sys.exit(1)
    if to_unit not in factor_table:
        print(f"ERROR: Unknown unit '{to_unit}'")
        sys.exit(1)

    base_value = value * factor_table[from_unit]
    result = base_value / factor_table[to_unit]
    return result


def find_table(unit):
    """Find which conversion table a unit belongs to."""
    for table in [LENGTH_TO_BASE, WEIGHT_TO_BASE, VOLUME_TO_BASE, SPEED_TO_BASE]:
        if unit in table:
            return table
    return None


def main():
    if len(sys.argv) != 4:
        print("Usage: convert.py <value> <from_unit> <to_unit>")
        sys.exit(1)

    try:
        value = float(sys.argv[1])
    except ValueError:
        print(f"ERROR: '{sys.argv[1]}' is not a valid number")
        sys.exit(1)

    from_unit = sys.argv[2].lower()
    to_unit = sys.argv[3].lower()

    if from_unit == to_unit:
        result = value
    elif from_unit in TEMP_UNITS and to_unit in TEMP_UNITS:
        result = convert_temperature(value, from_unit, to_unit)
    else:
        table = find_table(from_unit)
        if table is None:
            print(f"ERROR: Unknown unit '{from_unit}'")
            sys.exit(1)
        if to_unit not in table:
            print(f"ERROR: Cannot convert between '{from_unit}' and '{to_unit}'")
            sys.exit(1)
        result = convert_by_factor(value, from_unit, to_unit, table)

    # Format with 6 significant digits
    print(f"{value} {from_unit} = {result:.6g} {to_unit}")


if __name__ == "__main__":
    main()
