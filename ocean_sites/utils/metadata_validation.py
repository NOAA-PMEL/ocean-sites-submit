"""
Validation for Ocean Sites netCDF metadata. 

Pure functions. Takes the same dict shape produced by
build_global_attributes_dict() + build_variables_list() -- which is also
the shape the netCDF parser emits and the shape load_ocean_sites_metadata()
reconstructs -- and returns a flat list of problem dicts.

    problems = validate_metadata(edited_dict, original_dict)

Each problem is a plain dict (JSON-safe, so it round-trips through dcc.Store):

    {
        'scope':           'global' | 'variable',
        'var_name':         None | 'TEMP',
        'field':            'data_mode',
        'severity':         'error' | 'warning',
        'message':          'Data Mode is required',
        'invalid_value':    'Realtime' | None,
        'valid_options':    ['R', 'P', 'D', 'M'] | None,
        'also_fields':      ['geospatial_lat_min'] # highlight, but don't repeat the message (for when error is related to two related fields.)   
    }
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Any, Optional
from pydantic import ValidationError
from ocean_sites.models import (
    NetcdfGlobalAttributes,
    NetcdfVariable,
    NetcdfGlobalAttributesSchema,
    NetcdfDataVariableSchema,
    NetcdfCoordinateVariableSchema,
    NetcdfQCVariableSchema,
    NetcdfDMVariableSchema,
    NetcdfUncertaintyVariableSchema
)
from ocean_sites.utils.metadata_diff import (
    _coerce_enum,
    GLOBAL_ATTR_ENUM_FIELDS,
)

# --------------------------------------------------------------
# Contants
# --------------------------------------------------------------
ERROR = "error"
WARNING = "warning"

GLOBAL = "global"
VARIABLE = "variable"

_SCHEMA_BY_VAR_TYPE = {
    "data": NetcdfDataVariableSchema,
    "coordinate": NetcdfCoordinateVariableSchema,
    "qc": NetcdfQCVariableSchema,
    "dm": NetcdfDMVariableSchema,
    "uncertainty": NetcdfUncertaintyVariableSchema
}

_QC_FLAG_VALUES = ("0", "1", "2", "3", "4", "7", "8", "9")
_QC_FLAG_MEANINGS = (
    "unknown",
    "good_data",
    "probably_good_data",
    "potentially_correctable_bad_data",
    "bad_data",
    "nominal_value",
    "interpolated_value",
    "missing_value",
)

_DM_FLAG_VALUES = ("R", "P", "D", "M")
_DM_FLAG_MEANINGS = ("real-time", "provisional", "delayed-mode", "mixed")

# Same list save_all_ocean_sites_metadata() casts before insertion. Kept in sync so the validator sees the same types the DB will.
VARIABLE_STRING_FIELDS = (
    "fill_value", "flag_values", "flag_meanings", "axis", "standard_name", "units"
)

def _labels_for(model) -> dict:
    """"Pull the human labels off the SQL Alchemy column info dicts"""
    return {
        c.name: (c.info or {}).get("label", c.name)
        for c in model.__table__.columns
    }

_GLOBAL_LABELS = _labels_for(NetcdfGlobalAttributes)
_VARIABLE_LABELS = _labels_for(NetcdfVariable)
_GLOBAL_FIELDS = set(_GLOBAL_LABELS)
_VARIABLE_FIELDS = set(_VARIABLE_LABELS)

_BOUND_PAIRS = (
    ("geospatial_lat_min", "geospatial_lat_max"),
    ("geospatial_lon_min", "geospatial_lon_max"),
    ("geospatial_vertical_min", "geospatial_vertical_max")
)

# --------------------------------------------------------------
# For lat/lon min/max problems (won't catch without this because of how pydantic works)
# TODO: figure out times (right now are strs in DB, if want to include start/end time, need
# to make this a datetime and fix downstream)
# --------------------------------------------------------------
def _cross_field_problems(g: dict) -> list:
    """
    Here rather than in the pydantic shcema because a mode_validator(mode='after')
    only runs once every field has validated -- one bad data_mode and the bounds would 
    never get checked
    """
    out = []
    for lo, hi in _BOUND_PAIRS:
        a, b = g.get(lo), g.get(hi)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and a >= b:
            out.append(Problem(
                scope=GLOBAL, field=hi,
                message=f"Must be greater than the minimum ({a}).",
                also_fields=(lo,),
            ))
    return out

# --------------------------------------------------------------
# Problem
# --------------------------------------------------------------
@dataclass(frozen=True) # frozen=True makes it immutable.
class Problem:
    scope: str
    field: str
    message: str
    severity: str = ERROR
    kind: str = "invalid"
    var_name: Optional[str] = None
    invalid_value: Any = None
    valid_options: Optional[list] = None
    also_fields: tuple = ()

    @property
    def key(self):
        """Idenitty for de-duplication: one problem per field per record."""
        return (self.scope, self.var_name, self.field)

# --------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------
def _blank_to_none(value):
    """
    An empty text input arrives as '' and pydantic happily accepts '' for a 
    required str, so blanks have to become None before validating or nothing
    is ever reported or missing.
    """
    if isinstance(value, str) and not value.strip():
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return value

def _clean(d: dict) -> dict:
    """Blank-strip, then stringify the columns the DB stores as text."""
    return {k: _blank_to_none(v) for k, v in (d or {}).items()}

def _clean_variable(var: dict) -> dict:
    """Blank-strip, then stringify the columsn the DB stores as text."""
    cleaned = _clean(var)
    dimensions = cleaned.get("dimensions")
    if isinstance(dimensions, (list, tuple)):
        cleaned["dimensions"] = " ".join(str(dimension) for dimension in dimensions)
    for name in VARIABLE_STRING_FIELDS:
        value = cleaned.get(name)
        if value is not None and not isinstance(value, str):
            cleaned[name] = str(value)
    return cleaned

# --------------------------------------------------------------
# Turning pydantic errors into Problems
# --------------------------------------------------------------
def _humanize(err: dict, labels: dict) -> str:
    """
    Pydantic's default messages are accurate but not addressed to anyone.
    Rewrite the two or three that come up constantly; pass the rest through.
    """
    loc = err.get("loc") or ()
    name = loc[0] if loc else None
    label = labels.get(name, name)
    etype = err.get("type", "")
    msg = err.get("msg", "")

    # A required field we handed None -- i.e. the user left it empty
    if etype == "missing" or err.get("input",  "__sentinel__") is None:
        return f"{label} is required."

    if etype == "enum":
        # Inputs should be 'R', 'P', 'D' or 'M'
        options = msg.split("should be", 1)[-1].strip()
        return f"{label} must be {options}."

    if etype.startswith("float") or etype.startswith("int"):
        return f"{label} msut be a number"

    # Custom validators (ISO 8601, update_interval) already writes good prose
    return msg.removeprefix("Value error, ")

def _fields_named_in(message: str, known: set) -> list:
    """
    Model-level validators (check_spatial_bounds, for example) have no field location, so
    attribute them by which known field names appear in the message. Works for any future
    cross-field check as long as its message names the fields.
    """
    hits = [f for f in known if f in message]
    # Longest first, so 'geospatial_lat_min' wins over a hypothetical 'lat'
    return sorted(hits, key=len, reverse=True)

def _problems_from_error(err: dict, scope: str, labels: dict, known: set, var_name: str = None) -> list:
    loc = err.get("loc") or ()
    message = _humanize(err, labels)
    is_missing = err.get("type") == "missing" or err.get("input", "__s__") is None

    if loc: 
        return [Problem(
            scope=scope,
            var_name=var_name,
            field=str(loc[0]),
            message=message,
            kind="missing" if is_missing else "invalid",
            invalid_value=err.get("input")
        )]

    # No location: a model_validator. Attribute it to the fileds it names
    named = _fields_named_in(err.get("msg", ""), known)
    if not named:
        return [Problem(scope=scope, var_name=var_name, field="_record", message=message)]

    # Point at the last field mentioned - in "min must be less than max" that
    # is the max, which is usually the one the reader should look at.
    primary = named[-1] if len(named) > 1 else named[0]
    others = tuple(f for f in named if f != primary)
    return [Problem(
        scope=scope,
        var_name=var_name,
        field=primary,
        message=message,
        also_fields=others
    )]

# --------------------------------------------------------------
# Enum values lost at parse time
# --------------------------------------------------------------
def _enum_loss_problems(current_globals: dict, original_globals: dict) -> list:
    """
    _strip_invalid_enum_fields() drops an unparseable enum and saves NULL, so
    the user sees an empty dropdown with no explanation. The original value 
    survives in og_netcdf_metadata, so reover it and say waht was lost.

    Only fires when the field is *still* empty. Once the user picks a valid
    value there is nothing to warn about -- the diff already tells the story.
    """
    problems = []
    for name, enum_cls in GLOBAL_ATTR_ENUM_FIELDS.items():
        if current_globals.get(name) is not None:
            continue

        raw = _blank_to_none((original_globals or {}).get(name))
        if raw is None:
            continue
        if _coerce_enum(raw, enum_cls) is not None:
            continue # was valid' the user cleared it themselves

        options = [str(e.value) for e in enum_cls]
        label = _GLOBAL_LABELS.get(name, name)
        problems.append(Problem(
            scope=GLOBAL,
            field=name,
            message=(
                f"Your file said {raw!r}, which isn't a valid {label}. "
                f"Choose one of {', '.join(options)}."
            ),
            invalid_value=raw,
            valid_options=options
        ))
    return problems

# --------------------------------------------------------------
# Entry point
# --------------------------------------------------------------
def validate_metadata(current: dict, original: dict = None) -> list:
    """
    Validate a full metadata dict. 'original' is og_netcdf_metadata and is
    optional -- pass it to get the better "your file said X" messages on
    enum fields that were dropped at parse time.

    Returns a list of plain dicts, ordered globals-first then variables in 
    store order.
    """
    problems: list = []
    seen: set = set()

    def add(p: Problem):
        if p.key in seen:
            return # first message for a field wins, it si the more specific one
        seen.add(p.key)
        problems.append(p)

    current = current or {}
    globals_clean = _clean(current.get("global_attributes") or {})
    original_globals = (original or {}).get("global_attributes") or {}

    # Enum-loss first, so its message besats the generic "is reqwuired"
    for p in _enum_loss_problems(globals_clean, original_globals):
        add(p)

    # Cross-field next-- these run whether or not the field checks pass
    for p in _cross_field_problems(globals_clean):
        add(p)

    try:
        NetcdfGlobalAttributesSchema.model_validate(globals_clean)
    except ValidationError as exc:
        for err in exc.errors():
            for p in _problems_from_error(err, GLOBAL, _GLOBAL_LABELS, _GLOBAL_FIELDS):
                add(p)

    variables = current.get("variables") or []
    for var in variables:
        for p in _validate_variable(var):
            add(p)
    for p in _ancillary_variable_problems(variables):
        add(p)
    for p in _ancillary_reference_problems(variables):
        add(p)

    return [asdict(p) for p in problems]

def _validate_variable(var: dict) -> list:
    name = var.get("name") or "(unamed)"
    var_type = var.get("var_type")
    schema = _SCHEMA_BY_VAR_TYPE.get(var_type)

    if schema is None:
        return [Problem(
            scope=VARIABLE,
            var_name=name,
            field='var_type',
            message=(
                f"{var_type!r} isn't a variable type this portal understands. "
                f"Expected on of {', '.join(_SCHEMA_BY_VAR_TYPE)}"
            ),
            invalid_value=var_type,
            valid_options=list(_SCHEMA_BY_VAR_TYPE)
        )]

    cleaned_var = _clean_variable(var)
    problems = []
    try:
        schema.model_validate(cleaned_var)
    except ValidationError as exc:
        problems.extend(
            p
            for err in exc.errors()
            for p in _problems_from_error(
                err, VARIABLE, _VARIABLE_LABELS, _VARIABLE_FIELDS, var_name=name
            )
        )

    if var_type in ("qc", "dm"):
        flag_values = tuple(
            token
            for value in (cleaned_var.get("flag_values") or "").split(",")
            for token in value.split()
        )
        flag_meanings = tuple((cleaned_var.get("flag_meanings") or "").split())
        expected_values, expected_meanings = (
            (_QC_FLAG_VALUES, _QC_FLAG_MEANINGS)
            if var_type == "qc"
            else (_DM_FLAG_VALUES, _DM_FLAG_MEANINGS)
        )
        if (
            flag_values
            and flag_meanings
            and (flag_values != expected_values or flag_meanings != expected_meanings)
        ):
            label = "QC" if var_type == "qc" else "DM"
            problems.append(Problem(
                scope=VARIABLE,
                var_name=name,
                field="flag_values",
                message=(
                    f"{label} flag values and meanings must match the required mapping: "
                    f"{', '.join(expected_values)} maps in order to "
                    f"{' '.join(expected_meanings)}."
                ),
                also_fields=("flag_meanings",),
            ))

    if var_type == "dm":
        dtype = str(cleaned_var.get("dtype") or "").lower()
        if dtype not in {"|s1", "s1", "char"}:
            problems.append(Problem(
                scope=VARIABLE,
                var_name=name,
                field="dtype",
                message="DM variables must use the NetCDF Char datatype.",
            ))
        extra_attrs = cleaned_var.get("extra_attrs")
        long_name = extra_attrs.get("long_name") if isinstance(extra_attrs, dict) else None
        if long_name != "data mode":
            problems.append(Problem(
                scope=VARIABLE,
                var_name=name,
                field="extra_attrs",
                message='DM variables must have long_name set to "data mode".',
            ))

    if var_type == "uncertainty":
        dtype = str(cleaned_var.get("dtype") or "").lower()
        if dtype not in {"float32", "f4"}:
            problems.append(Problem(
                scope=VARIABLE,
                var_name=name,
                field="dtype",
                message="Uncertainty variables must use the NetCDF Float datatype.",
            ))
        extra_attrs = cleaned_var.get("extra_attrs")
        long_name = extra_attrs.get("long_name") if isinstance(extra_attrs, dict) else None
        if not isinstance(long_name, str) or not long_name.strip():
            problems.append(Problem(
                scope=VARIABLE,
                var_name=name,
                field="extra_attrs",
                message="Uncertainty variables require a long_name attribute.",
            ))

    return problems


def _ancillary_variable_problems(variables: list[dict]) -> list[Problem]:
    by_name = {str(var.get("name") or "").casefold(): var for var in variables}
    problems = []

    def dimension_names(value):
        if isinstance(value, (list, tuple)):
            return tuple(str(item) for item in value)
        return tuple(str(value or "").split())

    for var in variables:
        var_type = var.get("var_type")
        if var_type not in ("dm", "uncertainty"):
            continue

        name = str(var.get("name") or "")
        suffix = "_DM" if var_type == "dm" else "_UNCERTAINTY"
        parameter_name = name[:-len(suffix)] if name.upper().endswith(suffix) else ""
        parameter = by_name.get(parameter_name.casefold())
        if parameter is None or parameter.get("var_type") != "data":
            problems.append(Problem(
                scope=VARIABLE,
                var_name=name,
                field="dimensions",
                message=f"A {var_type.upper()} variable must have a matching data variable.",
            ))
            continue

        if dimension_names(var.get("dimensions")) != dimension_names(parameter.get("dimensions")):
            problems.append(Problem(
                scope=VARIABLE,
                var_name=name,
                field="dimensions",
                message="Ancillary variable dimensions must match the paired data variable.",
            ))
        if (
            var_type == "uncertainty"
            and _blank_to_none(var.get("units")) != _blank_to_none(parameter.get("units"))
        ):
            problems.append(Problem(
                scope=VARIABLE,
                var_name=name,
                field="units",
                message="Uncertainty units must match the paired data variable.",
            ))
    return problems

def _ancillary_reference_problems(variables: list[dict]) -> list[Problem]:
    variable_names = {
        var.get("name")
        for var in variables
        if isinstance(var.get("name"), str)
    }
    problems = []

    for var in variables:
        extra_attrs = var.get("extra_attrs")
        if not isinstance(extra_attrs, dict):
            continue

        for field in ("ancillary_variable", "ancillary_variables"):
            value = extra_attrs.get(field)
            if value is None:
                continue
            if hasattr(value, "tolist"):
                value = value.tolist()
            values = value if isinstance(value, (list, tuple)) else [value]
            references = []
            for item in values:
                if isinstance(item, bytes):
                    item = item.decode()
                references.extend(str(item).replace(",", " ").split())

            missing = list(dict.fromkeys(
                name for name in references if name not in variable_names
            ))
            if missing:
                problems.append(Problem(
                    scope=VARIABLE,
                    var_name=var.get("name") or "(unamed)",
                    field=field,
                    message=(
                        f"Referenced variable(s) not found: {', '.join(missing)}."
                    ),
                ))

    return problems

# --------------------------------------------------------------
# Read helpers for the UI layer
# --------------------------------------------------------------
def blocks_submission(problems: list) -> bool:
    return any(p["severity"] == ERROR for p in problems or [])

def count_problems(problems: list) -> dict:
    problems = problems or []
    return {
        "errors": sum(1 for p in problems if p['severity'] == ERROR),
        "warnings": sum(1 for p in problems if p['severity'] == WARNING)
    }

def global_problems_by_field(problems: list) -> dict:
    """
    {field_name: problem}. Fields listed in another problem's 'also_fields'
    map to that same problem, so they can be highlighted without repeating
    the message underneath them.
    """
    out = {}
    for p in problems or []:
        if p["scope"] != GLOBAL:
            continue
        out.setdefault(p["field"], p)
        for other in p.get("also_fields") or ():
            out.setdefault(other, p)
    return out

def variable_problems_by_name(problems: list) -> dict:
    """{var_name: {field_name: problem}} -- keyed to match nc-vars-store names"""
    out: dict = {}
    for p in problems or []:
        if p["scope"] != VARIABLE:
            continue
        fields = out.setdefault(p["var_name"], {})
        fields.setdefault(p["field"], p)
        for other in p.get("also_fields") or ():
            fields.setdefault(other, p)
    return out

def error_count_by_variable(problems: list) -> dict:
    """{var_name: n} -- drives the count pill on a collapsed variable card."""
    counts: dict = {}
    for p in problems or []:
        if p["scope"] == VARIABLE and p["severity"] == ERROR:
            counts[p["var_name"]] = counts.get(p["var_name"], 0) + 1

    return counts


    