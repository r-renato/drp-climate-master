#
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

TRUE_STRINGS: set[str] = {"1", "true", "t", "yes", "y", "on"}
FALSE_STRINGS: set[str] = {"0", "false", "f", "no", "n", "off", ""}

_NUM_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")

def _first_number_token(text: str) -> str | None:
    """
    Estrae il primo token numerico da una stringa (supporta '.' o ',' come separatore decimale).
    Esempi: "23.5°C" -> "23.5", "umidità 45,2 %" -> "45,2"
    """
    m = _NUM_RE.search(text)
    return m.group(0) if m else None

def _clamp[T: (int|float)](value: T, min_value: T | None, max_value: T | None) -> T:
    if min_value is not None and value < min_value:
        value = min_value
    if max_value is not None and value > max_value:
        value = max_value
    return value

def as_bool(
    v: Any,
    default: bool | None = None,
    *,
    strict: bool = False,
) -> bool | None:
    """
    Converte input in bool.
    - bool: restituito com'è
    - int/float/Decimal: 0 => False, !=0 => True
    - str: case-insensitive, supporta {1,true,t,yes,y,on} / {0,false,f,no,n,off,""}
    - None: restituisce 'default' (o ValueError se strict)
    """
    if v is None:
        if strict:
            raise ValueError("Cannot coerce None to bool")
        return default

    if isinstance(v, bool):
        return v

    if isinstance(v, (int, float, Decimal)):
        return (float(v) != 0.0)

    if isinstance(v, str):
        s = v.strip().lower()
        if s in TRUE_STRINGS:
            return True
        if s in FALSE_STRINGS:
            return False

    if strict:
        raise ValueError(f"Cannot coerce {v!r} to bool")
    return default

def as_float(
    v: Any,
    default: float | None = None,
    *,
    min_value: float | None = None,
    max_value: float | None = None,
    strict: bool = False,
) -> float | None:
    """
    Converte input in float.
    - float/int/Decimal: convertiti direttamente
    - str: estrae il primo numero (accetta '.' o ','), ignora unità attigue
    - None: -> default (o ValueError se strict)
    Applica clamp opzionale con min/max.
    """
    if v is None:
        if strict:
            raise ValueError("Cannot coerce None to float")
        return default

    try:
        if isinstance(v, bool):
            x = 1.0 if v else 0.0
        elif isinstance(v, float):
            x = v
        elif isinstance(v, int):
            x = float(v)
        elif isinstance(v, Decimal):
            x = float(v)
        elif isinstance(v, str):
            token = _first_number_token(v.strip())
            if token is None:
                raise ValueError("no numeric token")
            # normalizza separatore decimale
            token = token.replace(",", ".")
            x = float(token)
        else:
            raise TypeError("unsupported type")
    except Exception:
        if strict:
            raise
        return default

    x = _clamp(x, min_value, max_value)
    return x

def as_int(
    v: Any,
    default: int | None = None,
    *,
    min_value: int | None = None,
    max_value: int | None = None,
    strict: bool = False,
    rounding: str = "nearest",  # "nearest" | "floor" | "ceil" | "truncate"
) -> int | None:
    """
    Converte input in int.
    - int: restituito com'è
    - float/Decimal/str numerica: convertiti secondo 'rounding'
      • nearest  -> round(x)
      • floor    -> math.floor(x)
      • ceil     -> math.ceil(x)
      • truncate -> int(x) (taglia verso zero)
    - str con unità: estrae primo numero come _as_float
    - None: -> default (o ValueError se strict)
    Applica clamp opzionale con min/max.
    """
    if v is None:
        if strict:
            raise ValueError("Cannot coerce None to int")
        return default

    import math

    # percorso veloce
    if isinstance(v, int):
        x = v
    else:
        xf = as_float(v, None, strict=strict)
        if xf is None:
            return default
        if rounding == "nearest":
            x = int(round(xf))
        elif rounding == "floor":
            x = math.floor(xf)
        elif rounding == "ceil":
            x = math.ceil(xf)
        elif rounding == "truncate":
            x = int(xf)
        else:
            if strict:
                raise ValueError(f"Unknown rounding mode: {rounding}")
            x = int(round(xf))

    x = _clamp(x, min_value, max_value)
    return x

def slugify(text: str) -> str:
    out = []
    for ch in text.lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in (" ", "-", "_"):
            out.append("_")
    slug = "".join(out).strip("_")
    return slug