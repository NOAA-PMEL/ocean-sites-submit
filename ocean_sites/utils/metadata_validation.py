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
import os
import re
import math
import numpy as np
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Any, Optional
from urllib.parse import quote
import xml.etree.ElementTree as ET
from cf_units import Unit
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

_ISO8601_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")
_ISO8601_DURATION_RE = re.compile(
    r"^P(?!T?$)(\d+Y)?(\d+M)?(\d+D)?(T(?!$)(\d+H)?(\d+M)?(\d+(?:\.\d+)?S)?)?$"
)
_EXTRA_ISO_UTC_FIELDS = {"date_modified", "date_issued"}
_EXTRA_ISO_DURATION_FIELDS = {"time_coverage_duration", "time_coverage_resolution"}

_OCEANSITES_PARAMETER_MAP = {
    "AIRT": "air_temperature",
    "CAPH": "air_pressure",
    "CDIR": "direction_of_sea_water_velocity",
    "CNDC": "sea_water_electrical_conductivity",
    "CSPD": "sea_water_speed",
    "DEPTH": "depth",
    "DEWT": "dew_point_temperature",
    "DOX2": "moles_of_oxygen_per_unit_mass_in_sea_water",
    "DOXY": "mass_concentration_of_oxygen_in_sea_water",
    "DOXY_TEMP": "temperature_of_sensor_for_oxygen_in_sea_water",
    "DYNHT": "dynamic_height",
    "FLU2": "fluorescence",
    "HCSP": "sea_water_speed",
    "HEAT": "heat_content",
    "ISO17": "isotherm_depth",
    "LW": "surface_downwelling_longwave_flux_in_air",
    "OPBS": "optical_backscattering_coefficient",
    "PCO2": "surface_partial_pressure_of_carbon_dioxide_in_air",
    "PRES": "sea_water_pressure",
    "PSAL": "sea_water_practical_salinity",
    "RAIN": "rainfall_rate",
    "RAIT": "thickness_of_rainfall_amount",
    "RELH": "relative_humidity",
    "SDFA": "surface_downwelling_shortwave_flux_in_air",
    "SRAD": "isotropic_shortwave_radiance_in_air",
    "SW": "surface_downwelling_shortwave_flux_in_air",
    "TEMP": "sea_water_temperature",
    "UCUR": "eastward_sea_water_velocity",
    "UWND": "eastward_wind",
    "VAVH": "sea_surface_wave_significant_height",
    "VAVT": "sea_surface_wave_zero_upcrossing_period",
    "VCUR": "northward_sea_water_velocity",
    "VDEN": "sea_surface_wave_variance_spectral_density",
    "VDIR": "sea_surface_wave_from_direction",
    "VWND": "northward_wind",
    "WDIR": "wind_to_direction",
    "WSPD": "wind_speed"
}

_OCEANSITES_REVERSE_MAP = {}
for param, cf_name in _OCEANSITES_PARAMETER_MAP.items():
    _OCEANSITES_REVERSE_MAP.setdefault(cf_name, []).append(param)

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
_VALID_QC_DTYPES = {"int8", "byte", "i1", "|i1"}

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

_AXIS_ORDER = {"T": 0, "Z": 1, "Y": 2, "X": 3}

_FILL_ATTR_RE = re.compile(r"^_?(fill|missing)_?value$", re.IGNORECASE)

_CF_ATTR_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

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
    ("geospatial_vertical_min", "geospatial_vertical_max"),
    ("time_coverage_start", "time_coverage_end"),
)

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

        if a is None or b is None or isinstance(a, bool) or isinstance(b, bool):
            continue

        if (
            isinstance(a, str) and isinstance(b, str)
            and _ISO8601_UTC_RE.match(a) and _ISO8601_UTC_RE.match(b)
        ):
            dt_a = datetime.fromisoformat(a.replace("Z", "+00:00"))
            dt_b = datetime.fromisoformat(b.replace("Z", "+00:00"))
            invalid_bounds = dt_a > dt_b
            msg = f"End date must be on or after the start date"
        else:
            try:
                a_num, b_num = float(a), float(b)
            except (TypeError, ValueError):
                continue
            invalid_bounds = a_num > b_num
            if lo in allow_equal_bounds and a_num == b_num:
                invalid_bounds = False
            msg = f"Maximum must be greater than the minimum"

        if not invalid_bounds:
            continue
        out.append(Problem(
            scope=GLOBAL, field=hi,
            message=msg,
            also_fields=(lo,),
            ))
    return out

def _extra_datetime_problems(g: dict) -> list:
    out = []
    extra = g.get("extra_global_attrs") or {}
    for key, raw_val in extra.items():
        if raw_val is None or (isinstance(raw_val, str) and not raw_val.strip()):
            continue

        val = raw_val.strip() if isinstance(raw_val, str) else raw_val

        if key in _EXTRA_ISO_UTC_FIELDS:
            if not (isinstance(val, str) and _ISO8601_UTC_RE.match(val)):
                out.append(Problem(
                    scope=GLOBAL,
                    field=key,
                    message=(
                        f"'{val}' is not a valid ISO 8601 UTC datetime. "
                        "Expected format: YYYY-MM-DDTHH:MM:SSZ or YYYY-MM-DDTHH:MM:SS.fffffZ"
                    ),
                ))
        elif key in _EXTRA_ISO_DURATION_FIELDS:
            if not (isinstance(val, str) and _ISO8601_DURATION_RE.match(val)):
                out.append(Problem(
                    scope=GLOBAL,
                    field=key,
                    message=(
                        f"'{val}' is not a valid {key}. "
                        "Use ISO 8601 duration format (e.g. P1Y, P6M, PT12H, P1DT6H)."
                    ),
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

    #Check whether datetime fields are in ISO 8601 format
    for p in _extra_datetime_problems(globals_clean):
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

    for p in _thredds_site_code_problems(globals_clean):
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
    for p in _attribute_name_problems(
        globals_clean.get("extra_global_attrs"), GLOBAL
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
    for p in _coordinate_attribute_problems(variables):
        add(p)
    for p in _dimension_order_problems(variables):
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
    problems.extend(_data_variable_name_problems(name, cleaned_var))
    problems.extend(_numeric_attr_problems(cleaned_var))
    problems.extend(_attribute_name_problems(cleaned_var.get("extra_attrs"), VARIABLE, name))

    if var_type == "coordinate":
        problems.extend(_coordinate_axis_problems(name, cleaned_var))

        #check to make sure coordinates DO NOT have fill values
        extra_attrs = cleaned_var.get("extra_attrs") or {}
        fill_keys = [k for k in extra_attrs if _FILL_ATTR_RE.match(k)]
        if not fill_keys and _blank_to_none(cleaned_var.get("fill_value")) is not None:
            fill_keys = ["_FillValue"]

        problems.extend(
            Problem(
                scope=VARIABLE,
                var_name=name,
                field=key,
                field_part="key",
                message=f"Coordinate variables must not have a {key} attribute.",
            )
            for key in fill_keys
        )
        
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
            
    if var_type == "qc":
        raw_dtype = _blank_to_none(cleaned_var.get("dtype"))
        if raw_dtype is not None and str(raw_dtype).strip().lower() not in _VALID_QC_DTYPES:
            problems.append(Problem(
                scope=VARIABLE,
                var_name=name,
                field="dtype",
                severity=WARNING,  # switch to "warning" if non-blocking
                message=(
                    f"QC variable '{name}' has data type '{raw_dtype}', "
                    "but OceanSITES requires 'byte' (int8)."
                ),
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
        long_name = cleaned_var.get("long_name")
        if not isinstance(long_name, str) or not long_name.strip():
            problems.append(Problem(
                scope=VARIABLE,
                var_name=name,
                severity=WARNING,
                field="long_name",
                message='DM variables require a long_name attribute.',
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
        long_name = cleaned_var.get("long_name")
        if not isinstance(long_name, str) or not long_name.strip():
            problems.append(Problem(
                scope=VARIABLE,
                var_name=name,
                field="long_name",
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

def _coordinate_attribute_problems(variables: list) -> list[Problem]:
    """Validates OceanSITES coordinate requirements and cross-references."""
    problems = []
    core_axes = {"T", "Z", "Y", "X"}
    
    # 1. Pre-compute axes ONCE for the entire file. 
    # Creates a fast lookup: {"TIME": "T", "LATITUDE": "Y", "TEMP": ""}
    var_to_axis = {}
    for v in variables:
        if v.get("name"):
            # Try top-level first, fallback to nested 'attributes' if your parser groups them
            axis_val = v.get("axis") or v.get("attributes", {}).get("axis", "")
            var_to_axis[v["name"]] = str(axis_val).strip().upper()

    for var in variables:
        name = var.get("name", "(unnamed)")
        
        if var.get("var_type") != "data":
            continue
            
        dims = var.get("dimensions") or []
        # CRITICAL FIX: If dimensions is a single string, split it into a list!
        if isinstance(dims, str):
            dims = dims.strip().split()
            
        coords_str = var.get("coordinates")
        
        # 2. Instant Axis Check
        found_axes = {var_to_axis.get(d) for d in dims} & core_axes
        
        if not core_axes.issubset(found_axes) and not coords_str:
            problems.append(Problem(
                scope=VARIABLE,
                var_name=name,
                field="coordinates",
                severity=ERROR,
                message="Data variables must be mapped to all 4 spatiotemporal axes (T, Z, Y, X). Provide a 'coordinates' attribute."
            ))
            continue
            
        # 3. String Parsing & Checks
        if isinstance(coords_str, str):
            c_list = coords_str.strip().split()
            
            # Existence check: if it's not a key in var_to_axis, it doesn't exist in the file!
            missing = [c for c in c_list if c not in var_to_axis]
            if missing:
                problems.append(Problem(
                    scope=VARIABLE,
                    var_name=name,
                    field="coordinates",
                    severity=ERROR,
                    message=f"Coordinate variable(s) not found in file: {', '.join(missing)}"
                ))
                
            # 4. Order Check (T, Z, Y, X) using the pre-computed map
            axis_map = {var_to_axis.get(c): c for c in c_list}
            
            if core_axes.issubset(axis_map.keys()):
                expected_str = f"{axis_map['T']} {axis_map['Z']} {axis_map['Y']} {axis_map['X']}"
                actual_core = " ".join([c for c in c_list if c in expected_str])
                
                if actual_core != expected_str:
                    problems.append(Problem(
                        scope=VARIABLE,
                        var_name=name,
                        field="coordinates",
                        severity=WARNING, 
                        message=f"OceanSITES prefers the coordinates order (T, Z, Y, X). Expected '{expected_str}'.",
                        valid_options=[expected_str] 
                    ))

    return problems

def _dimension_order_problems(variables: list[dict]) -> list[Problem]:
    """
    CF §2.4 / OceanSITES: arbitrary dimensions belong to the left of
    spatiotemporal dimensions, and spatiotemporal dimensions must appear in
    (T, Z, Y, X) order based on each coordinate variable's `axis` attribute.
    """
    problems: list[Problem] = []

    var_to_axis = {}
    for v in variables:
        if v.get("name"):
            axis_val = (
                v.get("axis")
                or (v.get("extra_attrs") or {}).get("axis")
                or (v.get("attributes") or {}).get("axis")
                or ""
            )
            var_to_axis[v["name"]] = str(axis_val).strip().upper()

    for var in variables:
        name = var.get("name") or "(unnamed)"
        raw_dims = var.get("dimensions") or []
        dims = raw_dims.strip().split() if isinstance(raw_dims, str) else list(raw_dims)

        dtype = str(var.get("dtype") or "").lower()
        is_char = dtype in {"|s1", "s1", "char"}
        trailing_strlen = (
            [dims[-1]]
            if is_char and len(dims) > 1 and var_to_axis.get(dims[-1]) not in _AXIS_ORDER
            else []
        )
        check_dims = dims[:-1] if trailing_strlen else dims

        other_dims = [d for d in check_dims if var_to_axis.get(d) not in _AXIS_ORDER]
        st_dims = sorted(
            (d for d in check_dims if var_to_axis.get(d) in _AXIS_ORDER),
            key=lambda d: _AXIS_ORDER[var_to_axis[d]],
        )

        if not st_dims:
            continue

        expected_dims = other_dims + st_dims + trailing_strlen

        expected_dims = other_dims + st_dims
        if dims != expected_dims:
            expected_str = " ".join(expected_dims)
            problems.append(Problem(
                scope=VARIABLE,
                var_name=name,
                field="dimensions",
                severity=WARNING,
                message=(
                    f"CF §2.4 / OceanSITES requires spatiotemporal dimensions in (T, Z, Y, X) "
                    f"order with any other dimensions to their left. Expected '({expected_str})'."
                ),
                valid_options=[expected_str],
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

def _thredds_site_code_problems(global_attrs: dict) -> list[Problem]:
    if not isinstance(global_attrs, dict) or "site_code" not in global_attrs:
        return []

    site_code = global_attrs["site_code"]
    if isinstance(site_code, bytes):
        site_code = site_code.decode(errors="replace")
    if isinstance(site_code, str):
        site_code = site_code.strip()

    # If it's empty, let Pydantic handle the "required field" error
    if not site_code:
        return []

    thredds_sites = _get_thredds_site_codes()
    link_text = "NDBC GDAC catalog"
    
    # Only throw the warning if we successfully loaded the catalog
    if thredds_sites and site_code.upper() not in thredds_sites:
        return [Problem(
            scope=GLOBAL,
            var_name=None,
            field="site_code",
            severity=WARNING,
            message=(
                f"'{site_code}' does not exist in the {link_text} (yet). "
                "If this is not a brand new deployment, please check for typos."
            ),
            link_text=link_text,
            link_url="https://dods.ndbc.noaa.gov/thredds/catalog/data/oceansites/DATA/catalog.html"
        )]
        
    return []
    
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

def _parse_numeric(val):
    """Return a float/int if val is numeric (or a numeric string), else None."""
    if isinstance(val, bool) or val is None:
        return None
    if isinstance(val, (int, float, np.number)):
        return val
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        try:
            return int(s)
        except ValueError:
            try:
                return float(s)
            except ValueError:
                return None
    return None

def _numeric_attr_problems(var: dict) -> list[Problem]:
    """
    Validate that fill/missing value attributes, valid_min, and valid_max:
      1. Are numeric and compatible with the parent variable's dtype.
      2. All fill/missing attributes (e.g., _FillValue and missing_value) agree in value.
    """
    problems: list[Problem] = []
    name = var.get("name") or "(unnamed)"
    raw_dtype = var.get("dtype")

    try:
        dt = np.dtype(raw_dtype) if raw_dtype else None
    except TypeError:
        dt = None

    if dt is None or not (np.issubdtype(dt, np.integer) or np.issubdtype(dt, np.floating)):
        return problems

    extra = var.get("extra_attrs") or {}

    # 1. Collect all numeric attributes to check (top-level + extra_attrs)
    attrs_to_check: dict[str, object] = {}

    if var.get("fill_value") is not None:
        attrs_to_check["fill_value"] = var["fill_value"]
    for k in ("valid_min", "valid_max"):
        if var.get(k) is not None:
            attrs_to_check[k] = var[k]
        elif k in extra:
            attrs_to_check[k] = extra[k]

    # Pick up any _FillValue / missing_value variants from extra_attrs via _FILL_ATTR_RE
    for k, v in extra.items():
        if _FILL_ATTR_RE.match(k):
            attrs_to_check[k] = v

    parsed_fill_values: dict[str, int | float] = {}
    parsed_attrs: dict[str, int | float] = {}

    # 2. Validate numeric parsing and dtype compatibility
    for attr_name, raw_val in attrs_to_check.items():
        if raw_val is None or (isinstance(raw_val, str) and not raw_val.strip()):
            continue

        num = _parse_numeric(raw_val)
        if num is None:
            problems.append(Problem(
                scope=VARIABLE,
                var_name=name,
                field=attr_name,
                severity=ERROR,
                message=f"'{attr_name}' must be a numeric value matching {dt.name}, not '{raw_val}'.",
                invalid_value=str(raw_val),
            ))
            continue

        if np.issubdtype(dt, np.integer):
            info = np.iinfo(dt)
            if isinstance(num, float) and not num.is_integer():
                problems.append(Problem(
                    scope=VARIABLE,
                    var_name=name,
                    field=attr_name,
                    severity=ERROR,
                    message=f"'{attr_name}' ({raw_val}) cannot have a fractional value for integer type {dt.name}.",
                    invalid_value=str(raw_val),
                ))
                continue
            if not (info.min <= num <= info.max):
                problems.append(Problem(
                    scope=VARIABLE,
                    var_name=name,
                    field=attr_name,
                    severity=ERROR,
                    message=f"'{attr_name}' ({raw_val}) is out of range for {dt.name} ({info.min} to {info.max}).",
                    invalid_value=str(raw_val),
                ))
                continue

        elif np.issubdtype(dt, np.floating):
            info = np.finfo(dt)
            if math.isfinite(num) and not (info.min <= num <= info.max):
                problems.append(Problem(
                    scope=VARIABLE,
                    var_name=name,
                    field=attr_name,
                    severity=ERROR,
                    message=f"'{attr_name}' ({raw_val}) overflows {dt.name} range.",
                    invalid_value=str(raw_val),
                ))
                continue

        parsed_attrs[attr_name] = num
        if _FILL_ATTR_RE.match(attr_name):
            parsed_fill_values[attr_name] = num

    # 3. If multiple fill/missing attributes exist, verify they are all equal
    if len(parsed_fill_values) > 1:
        items = list(parsed_fill_values.items())
        ref_name, ref_raw = items[0]
        ref_val = dt.type(ref_raw)

        for other_name, other_raw in items[1:]:
            other_val = dt.type(other_raw)
            both_nan = math.isnan(ref_val) and math.isnan(other_val)
            if not both_nan and ref_val != other_val:
                problems.append(Problem(
                    scope=VARIABLE,
                    var_name=name,
                    field=other_name,
                    severity=ERROR,
                    message=f"'{other_name}' ({other_raw}) must equal '{ref_name}' ({ref_raw}).",
                #    also_fields=[ref_name],
                ))

    # 4. Check valid_min < valid_max
    if "valid_min" in parsed_attrs and "valid_max" in parsed_attrs:
        vmin = dt.type(parsed_attrs["valid_min"])
        vmax = dt.type(parsed_attrs["valid_max"])
        if not (math.isnan(vmin) or math.isnan(vmax)) and vmin > vmax:
            problems.append(Problem(
                scope=VARIABLE,
                var_name=name,
                field="valid_min",
                severity=ERROR,
                message=f"'valid_min' ({parsed_attrs['valid_min']}) must be less than 'valid_max' ({parsed_attrs['valid_max']}).",
                #also_fields=["valid_max"],
            ))

    return problems

def _attribute_name_problems(
    extra_attrs: dict | None,
    scope: str,
    var_name: str | None = None,
) -> list[Problem]:
    """Warn if any attribute name violates CF §2.3 naming conventions."""
    problems: list[Problem] = []
    if not extra_attrs:
        return problems

    for attr_name in extra_attrs:
        if attr_name in {"_FillValue"}:
            continue
        if not _CF_ATTR_NAME_RE.match(attr_name):
            suggested = re.sub(r"[^A-Za-z0-9_]+", "_", attr_name.strip()).strip("_")
            hint = f" (e.g., '{suggested}')" if suggested and _CF_ATTR_NAME_RE.match(suggested) else ""
            problems.append(Problem(
                scope=scope,
                var_name=var_name,
                field=attr_name,
                field_part="key",
                severity=ERROR,
                message=(
                    f"Attribute name violates CF §2.3: names should start with a "
                    f"letter and contain only letters, digits, and underscores{hint}."
                ),
                invalid_value=attr_name,
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

def _seavox_collection_url(collection_id: str) -> str:
    return (
        "https://vocab.nerc.ac.uk/collection/"
        f"{quote(collection_id, safe='')}/current/"
    )

_SEAVOX_CACHE = {}

def _get_seavox_preferred_labels(collection_id: str) -> tuple[frozenset[str] | None, str | None]:
    global _SEAVOX_CACHE
    collection_id = collection_id.upper()
    
    # 1. Return instantly from RAM if we've already loaded it
    if collection_id in _SEAVOX_CACHE:
        return _SEAVOX_CACHE[collection_id], None

    # 2. Read from the local JSON file
    try:
        current_dir = os.path.dirname(__file__)
        json_path = os.path.join(current_dir, "..", "assets", f"seavox_{collection_id.upper()}.json")
        
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        labels = set()
        
        # 3. Parse using your exact existing rules!
        for concept in data.get("@graph", []):
            if concept.get("owl:deprecated") in (True, "true", "True"):
                continue
                
            preferred_label = concept.get("skos:prefLabel")
            if isinstance(preferred_label, dict):
                if preferred_label.get("@language") != "en":
                    continue
                preferred_label = preferred_label.get("@value")
                
            if isinstance(preferred_label, str) and preferred_label.strip():
                labels.add(preferred_label.strip())
        
        if not labels:
            return None, f"Local SeaVoX {collection_id} file contains no valid labels."
            
        # Freeze and cache it
        frozen_labels = frozenset(labels)
        _SEAVOX_CACHE[collection_id] = frozen_labels
        return frozen_labels, None
        
    except FileNotFoundError:
        return None, f"Local vocabulary file for {collection_id} is missing. Please run update_vocabularies.py."
    except Exception as e:
        return None, f"Error reading local {collection_id} file: {e}"

def _seavox_vocabulary_value_problem(
    value: Any,
    attribute: str,
    collection_id: str,
    vocabulary_name: str,
    scope: str,
    var_name: Optional[str] = None,
) -> list[Problem]:

    recommendation = None

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

_THREDDS_CACHE = None

def _get_thredds_site_codes() -> set[str]:
    global _THREDDS_CACHE
    
    if _THREDDS_CACHE is not None:
        return _THREDDS_CACHE

    try:
        current_dir = os.path.dirname(__file__)
        xml_path = os.path.join(current_dir, "..", "assets", "thredds_catalog.xml")
        
        tree = ET.parse(xml_path)
        root = tree.getroot()
        
        sites = set()
        # Iterate through all elements ignoring namespaces
        for elem in root.iter():
            if elem.tag.endswith('catalogRef') or elem.tag.endswith('dataset'):
                # Grab anything that acts like a name or title
                for attr_key, attr_val in elem.attrib.items():
                    if attr_key.endswith('title') or attr_key == 'name':
                        # Ignore the root catalog itself
                        if attr_val not in ("DATA", "catalog.xml", "oceansites", ""):
                            sites.add(attr_val.upper()) # Upper case for safe comparison
                            
        _THREDDS_CACHE = sites
        return sites
        
    except Exception as e:
        print(f"THREDDS LOCAL READ ERROR: {e}")
        return set()
    
_CF_CACHE = None

def _get_cf_standard_names() -> dict:
    global _CF_CACHE
    
    # Cache for 24 hours
    if _CF_CACHE is not None:
        return _CF_CACHE

    cf_dict = {}
    try:
        current_dir = os.path.dirname(__file__)
        xml_path = os.path.join(current_dir, "..", "assets", "cf-standard-name-table.xml")
        
        # Parse the local XML file
        tree = ET.parse(xml_path)
        root = tree.getroot()

        for entry in root.findall('entry'):
            name = entry.get('id')
            units = entry.findtext('canonical_units')
            if name and units:
                cf_dict[name] = units

        for alias in root.findall('alias'):
            alias_name = alias.get('id')
            target_name = alias.findtext('entry_id')
            # If the target exists in our dict, copy its units to the alias
            if alias_name and target_name and target_name in cf_dict:
                cf_dict[alias_name] = cf_dict[target_name]
                
        _CF_CACHE = cf_dict
        return cf_dict
    except Exception as e:
        # Fails silently and falls back to whatever was cached
        print(f"CF LOCAL READ ERROR: {e}")
        _CF_CACHE = {}
        return _CF_CACHE

def _data_variable_name_problems(name: str, var: dict) -> list[Problem]:
    """Validates standard_name, long_name, and Parameter code mappings for Data Variables."""
    if var.get("var_type") != "data":
        return []

    problems = []
    
    standard_name = var.get("standard_name")
    if isinstance(standard_name, str): standard_name = standard_name.strip() or None
    
    long_name = var.get("long_name")
    if isinstance(long_name, str): long_name = long_name.strip() or None

    if not standard_name and not long_name:
        # Paint the standard_name box red
        problems.append(Problem(
            scope=VARIABLE,
            var_name=name,
            field="standard_name",
            severity=ERROR,
            message="Either standard_name or long_name must be provided."
        ))

    # Note: Pydantic already enforces that at least ONE is present, so we just check validity.
    active_name = None
    cf_names = _get_cf_standard_names()

    cf_link_text = "CF Standard Name Table"
    cf_link_url = "https://cfconventions.org/Data/cf-standard-names/current/build/cf-standard-name-table.html"

    # 1. Verify CF Standard Name legitimacy
    if not cf_names:
        # THE NETWORK CHECK: If the list is empty, warn the user instead of failing silently!
        problems.append(Problem(
            scope=VARIABLE, 
            var_name=name, 
            field="standard_name" if standard_name else "long_name", 
            severity=WARNING,
            message=f"Could not reach the server to validate this name against the {cf_link_text}. Network connection failed.",
            link_text=cf_link_text,
            link_url=cf_link_url
        ))
        active_name = standard_name or long_name
    else:
        if standard_name:
            if cf_names and standard_name not in cf_names:
                problems.append(Problem(
                    scope=VARIABLE,
                    var_name=name,
                    field="standard_name",
                    severity=ERROR,
                    message=(
                        f"'{standard_name}' is not in the official {cf_link_text}. "
                        "If this is a non-standard parameter, please clear this field and use "
                        "the 'Long Name' attribute instead."
                    ),
                    link_text=cf_link_text,
                    link_url=cf_link_url
                ))
                active_name = long_name or standard_name  # fallback for the mapping checks
            else:
                active_name = standard_name
        else:
            if long_name and cf_names and long_name in cf_names:
                problems.append(Problem(
                    scope=VARIABLE,
                    var_name=name,
                    field="long_name",
                    severity=ERROR,
                    message=(
                        f"'{long_name}' is a valid CF standard name. "
                        "We recommend moving it to the 'Standard name' field."
                    )
                ))
            active_name = long_name

    # 2. Cross-reference checks against the OceanSITES manual
    param_code = str(name).strip().upper()
    
    # Check if the parameter code matches the expected long_name / standard_name
    expected_active = _OCEANSITES_PARAMETER_MAP.get(param_code)
    if expected_active and active_name and active_name != expected_active:
        field = "standard_name" if standard_name else "long_name"
        problems.append(Problem(
            scope=VARIABLE,
            var_name=name,
            field=field,
            severity=WARNING,
            message=f"Parameter '{param_code}' typically uses the name '{expected_active}'.",
            valid_options=[expected_active]
        ))

    # Check if the provided name expects a different parameter code
    if active_name in _OCEANSITES_REVERSE_MAP:
        expected_params = _OCEANSITES_REVERSE_MAP[active_name]
        if param_code not in expected_params:
            field = "standard_name" if standard_name else "long_name"
            problems.append(Problem(
                scope=VARIABLE,
                var_name=name,
                field=field,
                severity=WARNING,
                message=f"Name '{active_name}' is normally associated with parameter code(s): {', '.join(expected_params)}."
            ))

    # Check for UDUNITS unit compatability
    units = var.get("units") # Make sure to grab 'units' from your variable dictionary

    if not units or not isinstance(units, str):
        return problems
    
    if units.strip().lower() in ["psu", "pss-78"]:
        problems.append(Problem(
            scope=VARIABLE,
            var_name=name,
            field="units",
            severity=ERROR,
            message=(
                f"'{units}' is not a valid CF/UDUNITS unit because salinity is a dimensionless ratio. "
                "Per CF Conventions, for practical salinity, use '1' or '1e-3' instead."
                "For absolute salinity, use 'g/kg' (or 'g kg-1')."
            )
        ))
        return problems
    
    try:
        file_u = Unit(units)
    except ValueError:
        problems.append(Problem(
            scope=VARIABLE,
            var_name=name,
            field="units",
            severity=WARNING, 
            message=f"'{units}' is not a recognized UDUNITS-2 format."
        ))
        return problems
        
    if standard_name and cf_names and standard_name in cf_names:
        canonical = cf_names[standard_name]
        try:
            can_u = Unit(canonical)
            
            if not file_u.is_convertible(can_u):
                problems.append(Problem(
                    scope=VARIABLE,
                    var_name=name,
                    field="units",
                    severity=WARNING,
                    message=f"Units '{units}' are not compatible with standard_name '{standard_name}'. Expected a unit convertible to '{canonical}'."
                ))
        except ValueError:
            pass

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


    