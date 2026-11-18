"""
Validation for Ocean Sites netCDF metadata. 

Takes the same dict shape produced by
build_global_attributes_dict() + build_variables_list() -- which is also
the shape the netCDF parser emits and the shape load_ocean_sites_metadata()
reconstructs -- and returns a flat list of problem dicts.

SeaVoX vocabularies are configured by attribute and collection code, fetched
on demand, and cached per collection in process.

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
        'field_part':      'key' | 'value' | None,
        'link_text':        str | None,
        'link_url':         str | None,
        'also_fields':      ['geospatial_lat_min'] # highlight, but don't repeat the message (for when error is related to two related fields.)   
    }
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
from dataclasses import dataclass, asdict
from http.client import HTTPException
from typing import Any, Optional
from urllib.parse import quote
from urllib.request import Request, urlopen
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

_SEAVOX_CACHE_TTL_SECONDS = 24 * 60 * 60
_SEAVOX_RETRY_SECONDS = 15 * 60
_SEAVOX_REQUEST_TIMEOUT_SECONDS = 2
_seavox_logger = logging.getLogger(__name__)
_seavox_cache_lock = threading.Lock()

# Entries map field, collection, display name, optional nested dict, and advice.
_SEAVOX_ATTRIBUTE_VOCABULARIES = {
    GLOBAL: (
        (
            "source",
            "L06",
            "SeaVoX Platform Categories",
            "extra_global_attrs",
            None,
        ),
        (
            "area",
            "C19",
            "SeaVoX salt and fresh water body gazetteer",
            "extra_global_attrs",
            "Consider renaming the 'area' attribute to 'sea_area' to match "
            "the OceanSITES v1.4 manual.",
        ),
        (
            "sea_area",
            "C19",
            "SeaVoX salt and fresh water body gazetteer",
            "extra_global_attrs",
            None,
        ),
    ),
    VARIABLE: (),
}


@dataclass
class _SeavoxVocabularyCacheEntry:
    preferred_labels: Optional[frozenset[str]] = None
    expires_at: float = 0.0
    retry_at: float = 0.0
    
_seavox_vocabulary_cache: dict[str, _SeavoxVocabularyCacheEntry] = {}

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

_QC_INDICATOR_VALUES = (
    "unknown",
    "good data",
    "probably good data",
    "potentially correctable bad data",
    "bad data",
    "nominal value",
    "interpolated value",
    "missing value",
)

_PROCESSING_LEVEL_VALUES = (
    "Raw instrument data",
    "Instrument data that has been converted to geophysical values",
    "Post-recovery calibrations have been applied",
    "Data has been scaled using contextual information",
    "Known bad data has been replaced with null values",
    "Known bad data has been replaced with values based on surrounding data",
    "Ranges applied, bad data flagged",
    "Data interpolated",
    "Data manually reviewed",
    "Data verified against model or other contextual information",
    "Other QC process applied",
)

_DM_FLAG_VALUES = ("R", "P", "D", "M")
_DM_FLAG_MEANINGS = ("real-time", "provisional", "delayed-mode", "mixed")

_HORIZONTAL_COORDINATE_REFERENCE_FRAME = "urn:ogc:def:crs:EPSG::4326"
_VERTICAL_COORDINATE_REFERENCE_FRAMES = {
    "down": "urn:ogc:def:crs:EPSG::5831",
    "up": "urn:ogc:def:crs:EPSG::5829",
}
_VERTICAL_REFERENCES = (
    "sea_level",
    "mean_sea_level",
    "mean_lower_low_water",
    "wgs84_geoid",
)

_COORDINATE_VALID_RANGES = {
    "T": {"valid_min": 0.0, "valid_max": 90000.0},
    "Y": {"valid_min": -90.0, "valid_max": 90.0},
    "X": {"valid_min": -180.0, "valid_max": 180.0},
    "Z": {"valid_min": 0.0, "valid_max": 12000.0},
}

_SPATIAL_AXIS_MIN_FIELDS = {
    "X": "geospatial_lon_min",
    "Y": "geospatial_lat_min",
    "Z": "geospatial_vertical_min",
}

_SENSOR_MOUNT_VALUES = (
    "mounted_on_fixed_structure",
    "mounted_on_surface_buoy",
    "mounted_on_mooring_line",
    "mounted_on_bottom_lander",
    "mounted_on_moored_profiler",
    "mounted_on_glider",
    "mounted_on_shipborne_fixed",
    "mounted_on_shipborne_profiler",
    "mounted_on_seafloor_structure",
    "mounted_on_benthic_node",
    "mounted_on_benthic_crawler",
    "mounted_on_surface_buoy_tether",
    "mounted_on_seafloor_structure_riser",
    "mounted_on_fixed_subsurface_vertical_profiler",
)

_SENSOR_ORIENTATION_VALUES = ("downward", "upward", "horizontal")

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
# For spatial min/max problems that must be checked even when another field fails.
# TODO: figure out times (right now are strs in DB, if want to include start/end time, need
# to make this a datetime and fix downstream)
# --------------------------------------------------------------
def _singleton_spatial_bounds(current: dict) -> set:
    dimension_sizes = {
        dimension.get("name"): dimension.get("size")
        for dimension in current.get("dimensions") or []
        if isinstance(dimension, dict) and dimension.get("name") is not None
    }
    dimensions_by_axis = {axis: set() for axis in _SPATIAL_AXIS_MIN_FIELDS}
    for variable in current.get("variables") or []:
        if not isinstance(variable, dict):
            continue
        axis = variable.get("axis")
        if not isinstance(axis, str):
            continue
        axis = axis.strip().upper()
        if axis not in dimensions_by_axis:
            continue

        dimensions = variable.get("dimensions")
        if isinstance(dimensions, str):
            dimensions = dimensions.split()
        if isinstance(dimensions, (list, tuple)):
            dimensions_by_axis[axis].update(
                name for name in dimensions if isinstance(name, str)
            )

    return {
        _SPATIAL_AXIS_MIN_FIELDS[axis]
        for axis, dimension_names in dimensions_by_axis.items()
        if dimension_names and all(
            isinstance(dimension_sizes.get(name), int)
            and not isinstance(dimension_sizes.get(name), bool)
            and 0 <= dimension_sizes[name] <= 1
            for name in dimension_names
        )
    }

def _cross_field_problems(
    g: dict, allow_equal_bounds: Optional[set] = None
) -> list:
    """
    Here rather than in the pydantic shcema because a mode_validator(mode='after')
    only runs once every field has validated -- one bad data_mode and the bounds would 
    never get checked
    """
    out = []
    allow_equal_bounds = allow_equal_bounds or set()
    for lo, hi in _BOUND_PAIRS:
        a, b = g.get(lo), g.get(hi)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            invalid_bounds = a >= b
            if lo in allow_equal_bounds and a == b:
                invalid_bounds = False
            if not invalid_bounds:
                continue
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
    link_text: Optional[str] = None
    link_url: Optional[str] = None
    field_part: Optional[str] = None

    @property
    def key(self):
        """Identity for de-duplication: one problem per input part per record."""
        return (self.scope, self.var_name, self.field, self.field_part)

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
    allow_equal_bounds = _singleton_spatial_bounds(current)

    # Enum-loss first, so its message besats the generic "is reqwuired"
    for p in _enum_loss_problems(globals_clean, original_globals):
        add(p)

    # Cross-field next-- these run whether or not the field checks pass
    for p in _cross_field_problems(globals_clean, allow_equal_bounds):
        add(p)

    try:
        NetcdfGlobalAttributesSchema.model_validate(
            globals_clean,
            context={"allow_equal_bounds": allow_equal_bounds},
        )
    except ValidationError as exc:
        for err in exc.errors():
            for p in _problems_from_error(err, GLOBAL, _GLOBAL_LABELS, _GLOBAL_FIELDS):
                add(p)

    for p in _processing_level_problems(
        globals_clean.get("extra_global_attrs"), GLOBAL
    ):
        add(p)
    for p in _seavox_vocabulary_problems(
        globals_clean, GLOBAL
    ):
        add(p)
    for p in _global_qc_indicator_problems(
        globals_clean.get("extra_global_attrs")
    ):
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
        
    problems.extend(_seavox_vocabulary_problems(cleaned_var, VARIABLE, name))
    problems.extend(_qc_indicator_problems(name, cleaned_var))
    problems.extend(_dm_indicator_problems(name, cleaned_var))

    if var_type == "coordinate":
        problems.extend(_coordinate_axis_problems(name, cleaned_var))
        
    if var_type != "coordinate":
        problems.extend(_sensor_attribute_problems(name, cleaned_var))
        
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
                severity=WARNING,
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

def _coordinate_axis_problems(name: str, var: dict) -> list[Problem]:
    axis = str(var.get("axis") or "").strip().upper()
    if axis not in _COORDINATE_VALID_RANGES:
        return []

    extra_attrs = var.get("extra_attrs")
    attrs = extra_attrs if isinstance(extra_attrs, dict) else {}

    def attr_value(field):
        value = attrs.get(field)
        if isinstance(value, bytes):
            value = value.decode(errors="replace")
        return value.strip() if isinstance(value, str) else None

    def attr_is_present(field):
        value = attrs.get(field)
        if isinstance(value, bytes):
            value = value.decode(errors="replace")
        return value is not None and (not isinstance(value, str) or bool(value.strip()))

    def attr_number(field):
        value = attrs.get(field)
        if isinstance(value, bytes):
            value = value.decode(errors="replace")
        if isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return number if math.isfinite(number) else None

    problems = []

    for field, expected in _COORDINATE_VALID_RANGES[axis].items():
        if not attr_is_present(field):
            continue
        actual = attr_number(field)
        if actual is None or actual != expected:
            problems.append(Problem(
                scope=VARIABLE,
                var_name=name,
                field=field,
                severity=WARNING,
                message=(
                    f"{axis} coordinate variables should use "
                    f"{field}={expected}."
                ),
                valid_options=[expected],
            ))

    if axis == "T":
        return problems

    if axis in {"X", "Y"}:
        expected_attrs = {
            "coordinate_reference_frame": _HORIZONTAL_COORDINATE_REFERENCE_FRAME,
            "reference": "WGS84",
        }
        for field, expected in expected_attrs.items():
            if attr_is_present(field) and attr_value(field) != expected:
                problems.append(Problem(
                    scope=VARIABLE,
                    var_name=name,
                    field=field,
                    severity=WARNING,
                    message=(
                        f"{axis} coordinate variables should use {field}={expected!r}."
                    ),
                    valid_options=[expected],
                ))
        return problems

    positive_present = attr_is_present("positive")
    positive = attr_value("positive")
    if positive_present and positive not in _VERTICAL_COORDINATE_REFERENCE_FRAMES:
        problems.append(Problem(
            scope=VARIABLE,
            var_name=name,
            field="positive",
            severity=WARNING,
            message="Z coordinate variables should use positive='up' or 'down'.",
            valid_options=["up", "down"],
        ))

    frame_present = attr_is_present("coordinate_reference_frame")
    frame = attr_value("coordinate_reference_frame")
    if frame_present:
        expected_frame = _VERTICAL_COORDINATE_REFERENCE_FRAMES.get(positive)
        valid_frames = list(_VERTICAL_COORDINATE_REFERENCE_FRAMES.values())
        if frame not in valid_frames or (
            expected_frame is not None and frame != expected_frame
        ):
            message = (
                f"Z coordinates with positive={positive!r} should use "
                f"coordinate_reference_frame={expected_frame!r}."
                if expected_frame is not None
                else (
                    "Z coordinate variables should use a valid "
                    "coordinate_reference_frame."
                )
            )
            problems.append(Problem(
                scope=VARIABLE,
                var_name=name,
                field="coordinate_reference_frame",
                severity=WARNING,
                message=message,
                valid_options=[expected_frame] if expected_frame else valid_frames,
            ))

    reference = attr_value("reference")
    if attr_is_present("reference") and reference not in _VERTICAL_REFERENCES:
        problems.append(Problem(
            scope=VARIABLE,
            var_name=name,
            field="reference",
            severity=WARNING,
            message=(
                "Z coordinate variables should use reference from: "
                f"{', '.join(_VERTICAL_REFERENCES)}."
            ),
            valid_options=list(_VERTICAL_REFERENCES),
        ))

    return problems

def _global_qc_indicator_problems(global_attrs: dict) -> list[Problem]:
    if not isinstance(global_attrs, dict) or "QC_indicator" not in global_attrs:
        return []

    value = global_attrs["QC_indicator"]
    
    if isinstance(value, bytes):
        value = value.decode(errors="replace")
    if isinstance(value, str):
        value = value.strip()

    valid_options = ["unknown", "excellent", "probably good", "mixed"]

    if isinstance(value, str) and value.lower() in valid_options:
        return []

    # Assuming GLOBAL is a constant in your file like VARIABLE is. 
    # If not, replace GLOBAL with the string "global" or however you define global scope.
    return [Problem(
        scope=GLOBAL, 
        var_name=None, 
        field="QC_indicator",
        severity=WARNING,
        message=(
            "Global QC_indicator should be one of: "
            f"{', '.join(valid_options)}."
        ),
        invalid_value=value if isinstance(value, (str, int, float, bool)) else None,
        valid_options=valid_options,
    )]
    
def _qc_indicator_problems(name: str, var: dict) -> list[Problem]:
    extra_attrs = var.get("extra_attrs")
    if not isinstance(extra_attrs, dict) or "QC_indicator" not in extra_attrs:
        return []

    value = extra_attrs["QC_indicator"]
    if isinstance(value, bytes):
        value = value.decode(errors="replace")
    if isinstance(value, str):
        value = value.strip()

    if isinstance(value, str) and value in _QC_INDICATOR_VALUES:
        return []

    return [Problem(
        scope=VARIABLE,
        var_name=name,
        field="QC_indicator",
        severity=WARNING,
        message=(
            "QC_indicator should be one of: "
            f"{', '.join(_QC_INDICATOR_VALUES)}."
        ),
        invalid_value=value if isinstance(value, (str, int, float, bool)) else None,
        valid_options=list(_QC_INDICATOR_VALUES),
    )]

def _dm_indicator_problems(name: str, var: dict) -> list[Problem]:
    extra_attrs = var.get("extra_attrs")
    if not isinstance(extra_attrs, dict) or "DM_indicator" not in extra_attrs:
        return []

    value = extra_attrs["DM_indicator"]
    if isinstance(value, bytes):
        value = value.decode(errors="replace")
    if isinstance(value, str):
        value = value.strip()

    if isinstance(value, str) and value in _DM_FLAG_VALUES:
        return []

    return [Problem(
        scope=VARIABLE,
        var_name=name,
        field="DM_indicator",
        severity=WARNING,
        message=(
            "DM_indicator should be one of: "
            f"{', '.join(_DM_FLAG_VALUES)}."
        ),
        invalid_value=value if isinstance(value, (str, int, float, bool)) else None,
        valid_options=list(_DM_FLAG_VALUES),
    )]

def _sensor_attribute_problems(name: str, var: dict) -> list[Problem]:
    extra_attrs = var.get("extra_attrs")
    if not isinstance(extra_attrs, dict):
        return []

    problems = []
    for field, valid_options in (
        ("sensor_mount", _SENSOR_MOUNT_VALUES),
        ("sensor_orientation", _SENSOR_ORIENTATION_VALUES),
    ):
        if field not in extra_attrs:
            continue

        value = extra_attrs[field]
        if isinstance(value, bytes):
            value = value.decode(errors="replace")
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
            if value in valid_options:
                continue
        elif value is None:
            continue

        problems.append(Problem(
            scope=VARIABLE,
            var_name=name,
            field=field,
            severity=WARNING,
            message=f"{field} should be one of: {', '.join(valid_options)}.",
            invalid_value=value if isinstance(value, (str, int, float, bool)) else None,
            valid_options=list(valid_options),
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

def _processing_level_problems(
    extra_attrs: Any, scope: str, var_name: Optional[str] = None
) -> list[Problem]:
    if not isinstance(extra_attrs, dict) or "processing_level" not in extra_attrs:
        return []

    value = extra_attrs["processing_level"]
    if isinstance(value, bytes):
        value = value.decode(errors="replace")
    if isinstance(value, str):
        value = value.strip()
        if value in _PROCESSING_LEVEL_VALUES:
            return []

    return [Problem(
        scope=scope,
        var_name=var_name,
        field="processing_level",
        message=(
            "processing_level must be one of: "
            f"{'; '.join(_PROCESSING_LEVEL_VALUES)}."
        ),
        invalid_value=value if isinstance(value, (str, int, float, bool)) else None,
        valid_options=list(_PROCESSING_LEVEL_VALUES),
    )]

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

def _seavox_collection_url(collection_id: str) -> str:
    return (
        "https://vocab.nerc.ac.uk/collection/"
        f"{quote(collection_id, safe='')}/current/"
    )

def _fetch_seavox_preferred_labels(collection_id: str) -> frozenset[str]:
    url = (
        f"{_seavox_collection_url(collection_id)}"
        "?_profile=nvs&_mediatype=application/ld+json"
    )
    request = Request(
        url,
        headers={"Accept": "application/ld+json"},
    )
    with urlopen(request, timeout=_SEAVOX_REQUEST_TIMEOUT_SECONDS) as response:
        vocabulary = json.loads(response.read())

    concepts = vocabulary.get("@graph") if isinstance(vocabulary, dict) else None
    if not isinstance(concepts, list):
        raise ValueError(
            f"SeaVoX {collection_id} response does not contain a concept list"
        )

    labels = set()
    for concept in concepts:
        if (
            not isinstance(concept, dict)
            or concept.get("@type") != "skos:Concept"
            or concept.get("owl:deprecated") in (True, "true")
        ):
            continue
        preferred_label = concept.get("skos:prefLabel")
        if isinstance(preferred_label, dict):
            if preferred_label.get("@language") != "en":
                continue
            preferred_label = preferred_label.get("@value")
        if isinstance(preferred_label, str) and preferred_label.strip():
            labels.add(preferred_label.strip())

    if not labels:
        raise ValueError(
            f"SeaVoX {collection_id} response contains no preferred labels"
        )
    return frozenset(labels)

def _get_seavox_preferred_labels(
    collection_id: str,
) -> tuple[Optional[frozenset[str]], Optional[str]]:
    with _seavox_cache_lock:
        cache_entry = _seavox_vocabulary_cache.setdefault(
            collection_id, _SeavoxVocabularyCacheEntry()
        )
        now = time.monotonic()
        if cache_entry.preferred_labels and now < cache_entry.expires_at:
            return cache_entry.preferred_labels, None
        if now < cache_entry.retry_at:
            if cache_entry.preferred_labels:
                return cache_entry.preferred_labels, (
                    f"SeaVoX {collection_id} could not be refreshed; "
                    "using the cached vocabulary."
                )
            return None, f"SeaVoX {collection_id} is temporarily unavailable."

        try:
            labels = _fetch_seavox_preferred_labels(collection_id)
        except (OSError, HTTPException, ValueError) as exc:
            cache_entry.retry_at = now + _SEAVOX_RETRY_SECONDS
            _seavox_logger.warning(
                "Could not load the SeaVoX %s vocabulary: %s", collection_id, exc
            )
            if cache_entry.preferred_labels:
                return cache_entry.preferred_labels, (
                    f"SeaVoX {collection_id} could not be refreshed; "
                    "using the cached vocabulary."
                )
            return None, f"SeaVoX {collection_id} is temporarily unavailable."

        cache_entry.preferred_labels = labels
        cache_entry.expires_at = now + _SEAVOX_CACHE_TTL_SECONDS
        cache_entry.retry_at = 0.0
        return labels, None

def _seavox_vocabulary_value_problem(
    value: Any,
    attribute: str,
    collection_id: str,
    vocabulary_name: str,
    scope: str,
    var_name: Optional[str] = None,
) -> list[Problem]:
    if isinstance(value, bytes):
        value = value.decode(errors="replace")
    if isinstance(value, str):
        value = value.strip()
    is_empty = value is None or value == ""
    link_text = f"{vocabulary_name} ({collection_id}) vocabulary"
    if is_empty:
        return []

    labels, vocabulary_notice = _get_seavox_preferred_labels(collection_id)

    if labels is not None and isinstance(value, str) and value in labels:
        if vocabulary_notice is None:
            return []
        message = (
            f"{attribute} matches the cached {link_text}, but it could not "
            f"be refreshed. {vocabulary_notice}"
        )
    elif labels is None:
        if not is_empty:
            message = (
                f"Could not check {attribute} against the {link_text}. "
                f"{vocabulary_notice}"
            )
            if recommendation:
                message = f"{message} {recommendation}"
    else:
        message = f"{attribute} should be in the {link_text}."
        if vocabulary_notice:
            message = f"{message} {vocabulary_notice}"

    return [Problem(
        scope=scope,
        var_name=var_name,
        field=attribute,
        severity=WARNING,
        message=message,
        invalid_value=value if isinstance(value, (str, int, float, bool)) else None,
        valid_options=sorted(labels) if labels is not None else None,
        link_text=link_text,
        link_url=_seavox_collection_url(collection_id),
        field_part="value",
    )]

def _seavox_vocabulary_problems(
    record: Any, scope: str, var_name: Optional[str] = None
) -> list[Problem]:
    if not isinstance(record, dict):
        return []

    problems = []
    for attribute, collection_id, vocabulary_name, container, recommendation in (
        _SEAVOX_ATTRIBUTE_VOCABULARIES.get(scope, ())
    ):
        attributes = record.get(container) if container else record
        if not isinstance(attributes, dict) or attribute not in attributes:
            continue
        problems.extend(_seavox_vocabulary_value_problem(
            attributes[attribute],
            attribute,
            collection_id,
            vocabulary_name,
            scope,
            var_name,
        ))
        if recommendation:
            problems.append(Problem(
                scope=scope,
                var_name=var_name,
                field=attribute,
                severity=WARNING,
                message=recommendation,
                field_part="key",
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

    def add_problem(fields: dict, field: str, problem: dict) -> None:
        existing = fields.get(field)
        if (
            existing is None
            or (existing["severity"] == WARNING and problem["severity"] == ERROR)
        ):
            fields[field] = problem

    for p in problems or []:
        if p["scope"] != VARIABLE:
            continue
        fields = out.setdefault(p["var_name"], {})
        add_problem(fields, p["field"], p)
        for other in p.get("also_fields") or ():
            add_problem(fields, other, p)
    return out

def error_count_by_variable(problems: list) -> dict:
    """{var_name: n} -- drives the count pill on a collapsed variable card."""
    counts: dict = {}
    for p in problems or []:
        if p["scope"] == VARIABLE and p["severity"] == ERROR:
            counts[p["var_name"]] = counts.get(p["var_name"], 0) + 1

    return counts

def warning_count_by_variable(problems: list) -> dict:
    """{var_name: n} -- drives the warning count pill on a variable card."""
    counts: dict = {}
    for p in problems or []:
        if p["scope"] == VARIABLE and p["severity"] == WARNING:
            counts[p["var_name"]] = counts.get(p["var_name"], 0) + 1

    return counts


    