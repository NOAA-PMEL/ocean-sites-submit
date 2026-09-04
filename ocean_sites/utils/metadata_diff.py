from ocean_sites.models import (
    DataMode, 
    DataType, 
    FormatVersion,
    VarType
)

# field_name -> enum class per table that has enum backed columns
GLOBAL_ATTR_ENUM_FIELDS = {
    "data_mode": DataMode,
    "data_type": DataType,
    "format_version": FormatVersion
}
VARIABLE_ENUM_FIELDS = {
    "var_type": VarType
}

# Fixed-schema global attribute columns (everything except id/fk/og-snapshot/extra_attrs)
FIXED_GLOBAL_ATTR_FIELDS = (
    "site_code", "platform_code", "data_mode", "data_type",
    "format_version", "update_interval", "date_created",
    "geospatial_lat_min", "geospatial_lat_max",
    "geospatial_lon_min", "geospatial_lon_max",
    "geospatial_vertical_min", "geospatial_vertical_max",
    "time_coverage_start", "time_coverage_end",
)

FIXED_VARIABLE_FIELDS = (
    "var_type", "dtype", "dimensions",
    "standard_name", "units", "fill_value", "coordinates",
    "axis", "flag_values", "flag_meanings"
)

def _normalize(value):
    """
    Treats None and '' (or whitespace-only strings) as equivalen "no value" --
    Dash text/dropdown inputs empty as '', while a filed absent from the source
    netCDF is None. Without this, every untouched-but-empty field would register
    as a false "changed" entry.
    """
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        value = ", ".join(map(str, value))
    if isinstance(value, str) and value.strip() == "":
        return None
    if isinstance(value, str):
        return value.strip()
    return value
    
def _coerce_enum(value, enum_cls):
    """
    Return the matching enum member for 'value', or None if it doesn't match
    any valid value/name on 'enum_cls'.
    """
    if value is None:
        return None
    try:
        return enum_cls(value) # matches .value e.g. "R" "data"
    except ValueError:
        try: 
            return enum_cls[value] # fallback: matches by member name: e.g. "REAL_TIME"
        except (KeyError, TypeError):
            return None

def find_enum_issues(original: dict, current: dict) -> dict:
    """
    Derives enum issues fresh from the raw original (og_netcdf_metadata)
    vs. current metadata, instead of relying on a stored issues list. An issue exists
    when the ORIGINAL raw value was invalid for that enum AND the CURRENT value
    is still unset -- so fixing that field makes the issue disappear on its own,
    with no separate "clear it" step needed anywere.

    Returns: 
        {
            "global_attributes": [{"field": str, "invalid_value": Any, "valid_options": [str, ...]}, ...],
            "variables": {"TEMP": [...], ...},
        }
    """
    original = original or {}
    current = current or {}

    global_issues = _find_enum_issues_in_dict(
        original.get("global_attributes") or {},
        current.get("global_attributes") or {},
        GLOBAL_ATTR_ENUM_FIELDS,
    )

    orig_vars_by_name = {v.get("name"): v for v in (original.get("variables") or []) if v.get("name")}
    curr_vars_by_name = {v.get("name"): v for v in (current.get("variables") or []) if v.get("name")}

    variable_issues = {}
    for name, orig_var in orig_vars_by_name.items():
        curr_var = curr_vars_by_name.get(name, {})
        var_issues = _find_enum_issues_in_dict(orig_var, curr_var, VARIABLE_ENUM_FIELDS)
        if var_issues:
            variable_issues[name] = var_issues

    return {"global_attributes": global_issues, "variables": variable_issues}

def _find_enum_issues_in_dict(original: dict, current: dict, enum_field_map: dict) -> list:
    issues = []
    for field, enum_cls in enum_field_map.items():
        raw_value = original.get(field)
        if raw_value is None:
            continue # nothing was in the file for this field, not an issue
            
        if _coerce_enum(raw_value, enum_cls) is not None:
            continue # original value was valid
            
        # FIX: Check if the current value is still invalid/coerced to None 
        # AND it hasn't been changed to a different user value.
        curr_value = current.get(field)
        if curr_value is not None and _coerce_enum(curr_value, enum_cls) is not None:
            continue # User has selected a completely valid enum option -- fixed!

        issues.append({
            "field": field, 
            "invalid_value": raw_value,
            "valid_options": [e.value for e in enum_cls]
        })
    return issues

def diff_global_attributes(original: dict, current: dict) -> dict:
    """
    Compares two 'global attributes' dicts (same shape as 
    _global_attrs_row_to_dict / parser.metadata_dict["global_attributes"])
    and returns:

        {
            "changed": [{"field": str, "original": Any, "current": Any}, ...],
            "added": [{"field": str, "current": Any}, ...],     # extra_global_attrs only
            "removed" : [{"field": str, "original" Any}, ...],  # extra_global_attrs only
            }

    Fixed-schema fields (site_code, data_mode, etc) can only ever be "changed" --
    they always exist as keys, even if the value is None. Only extra_global_attrs
    keys (free-form JSONB) can be truly added or removed.
    """
    original = original or {}
    current = current or {}

    changed = []
    for field in FIXED_GLOBAL_ATTR_FIELDS:
        orig_val = _normalize(original.get(field))
        curr_val = _normalize(current.get(field))
        if orig_val != curr_val:
            changed.append({"field": field, "original": orig_val, "current": curr_val})

    orig_extra = original.get("extra_global_attrs") or {}
    curr_extra = current.get("extra_global_attrs") or {}

    added = [
        {"field": f"extra_global_attrs.{key}", "current": curr_extra[key]}
        for key in curr_extra.keys() - orig_extra.keys()
    ]

    removed = [
        {"field": f"extra_global_attrs.{key}", "original": orig_extra[key]}
        for key in orig_extra.keys() - curr_extra.keys()
    ]
    for key in orig_extra.keys() & curr_extra.keys():
        orig_val = _normalize(orig_extra[key])
        curr_val = _normalize(curr_extra[key])
        if orig_val != curr_val:
            changed.append({
                "field": f"extra_global_attrs.{key}",
                "original": orig_val,
                "current": curr_val
            })

    return {"changed": changed, "added": added, "removed": removed}

def diff_variables(original: list, current: list) -> dict:
    """
    Compare two 'variables' lists (same shape as parser.metadata_dict["variables"]
    /_variable_row_to_dict) and returns a diff keyed by name - variable rows have
    no stable id across saves (wiped + reinserted every time), so name is the only
    reliable join key.

        {
        "added": [{"name": str, "original": dict}, ...],
        "removed": [{"name": str, "original": dict}, ...],
        "changed": [
            {"name": str, "fields": {"changed": [...], "added": [...], "removed": [...]}},
            ...
            ],
        }

    "fields" reuses the same {"field", "original", "current"} shape as
    diff_global_attributes, including an extra_attrs sub-diff.
    """
    original = original or []
    current = current or []

    orig_by_name = {v.get("name"): v for v in original if v.get("name")}
    curr_by_name = {v.get("name"): v for v in current if v.get("name")}

    added = [
        {"name": name, "current": curr_by_name[name]}
        for name in curr_by_name.keys() - orig_by_name.keys()
    ]
    removed = [
        {"name": name, "original": orig_by_name[name]}
        for name in orig_by_name.keys() - curr_by_name.keys()
    ]
    
    changed = []
    for name in orig_by_name.keys() & curr_by_name.keys():
        field_diff = _diff_variable_fields(orig_by_name[name], curr_by_name[name])
        if field_diff["changed"] or field_diff["added"] or field_diff["removed"]:
            changed.append({"name": name, "fields": field_diff})

    return {"added": added, "removed": removed, "changed": changed}

def _diff_variable_fields(original: dict, current: dict) -> dict:
    """
    Field-level diff for a single variable. Same shape diff_global_attributes returns.
    """
    changed = []
    for field in FIXED_VARIABLE_FIELDS:
        orig_val = _normalize(original.get(field))
        curr_val = _normalize(current.get(field))
        if orig_val != curr_val:
            changed.append({"field": field, "original": orig_val, "current": curr_val})

    orig_extra = original.get("extra_attrs") or {}
    curr_extra = current.get("extra_attrs") or {}

    added = [
        {"field": f"extra_attrs.{key}", "current": curr_extra[key]}
        for key in curr_extra.keys() - orig_extra.keys()
    ]
    removed = [
        {"field": f"extra_attrs.{key}", "original": orig_extra[key]}
        for key in orig_extra.keys() - curr_extra.keys()
    ]
    for key in orig_extra.keys() & curr_extra.keys():
        orig_val = _normalize(orig_extra[key])
        curr_val = _normalize(curr_extra[key])
        if orig_val != curr_val:
            changed.append({
                "field": f"extra_attrs.{key}",
                "original": orig_val,
                "current": curr_val,
            })

    return {"changed": changed, "added": added, "removed": removed}

def build_metadata_diff(original: dict, current: dict, issues: list = None) -> dict:
    """
    Top-level entry point: combines the global-attribute diff, the variable diff,
    and any enum-validation issues from the most recent save into one structure
    for the UI to render.

    original / current: full metadata dicts shape like
    {"global_attributes": {...}, "variables": [...], "dimensions": [...]}
    (dimensions intentionally excluded -- not user-editable)

    issues: the list returned by save_all_ocean_sites_metadata, e.g.
    [{"record": "global_attributes", "field": "data_mode",
    "invalid_value": "Realtime", "valid_options": ["R", "P", "D", "M"]}, ...]
    Matches the "global_attributes" / "variable:{name}" record labels used 
    in that function.

    Returns:
        {
            "global_attributes": {"changed" [...], "added": [...], "removed": [....]},
            "variables": {"added": [...], "removed": [...], "changed": [...]},
            "issues": {
                "global_attributes": [...],     # issues with record == "global_attributes"
                "variables": [...], ...}        # issues grouped by variable name 
            },
        }
    """
    original = original or {}
    current = current or {}

    global_diff = diff_global_attributes(
        original.get("global_attributes"), current.get("global_attributes")
    )
    variables_diff = diff_variables(
        original.get("variables"), current.get("variables")
    )
    issues = find_enum_issues(original, current)

    return {
        "global_attributes": global_diff,
        "variables": variables_diff,
        "issues": issues
    }

def summarize_diff_counts(diff: dict) -> dict:
    """
    Reduces the full diff from build_metadata_diff into total changed/added/removed
    counts across global attributes AND variables (including field-level adds/removes)
    within a changed variable's extra_attrs). Single source of truth for the changes
    bar and, later, the ledger -- both should call this rather than recount themselves.
    """
    changed = len(diff["global_attributes"]["changed"])
    added = len(diff["global_attributes"]["added"])
    removed = len(diff["global_attributes"]["removed"])

    added += len(diff["variables"]["added"])
    removed += len(diff["variables"]["removed"])
    for entry in diff["variables"]["changed"]:
        fields = entry["fields"]
        changed += len(fields["changed"])
        added += len(fields["added"])
        removed += len(fields["removed"])

    return {"changed": changed, "added": added, "removed": removed}