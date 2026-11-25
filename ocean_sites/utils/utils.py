import copy
import numpy as np
import math
from ocean_sites.models import (
    NetcdfGlobalAttributes, 
    NetcdfDimension, 
    NetcdfVariable, 
    )
from typing import Optional
from portal_core.utils import (
    app_logger as log,
    get_or_create_oads_metadata_id, 
    get_oads_metadata_id
)
from sqlalchemy import select
from ocean_sites.utils.metadata_diff import _coerce_enum, GLOBAL_ATTR_ENUM_FIELDS, VARIABLE_ENUM_FIELDS
from ocean_sites.utils.variable_types import infer_special_variable_type

GLOBAL_ATTRIBUTES = "global_attributes" # keys in nc parser dict
OG_NETCDF_METADATA = "og_netcdf_metadata"
DIMENSIONS = "dimensions"
VARIABLES = "variables"

# columns that are typed as string in the sQLAclhemy model by may arrive as numeric/native
# types once numpy scalars are unwrapped (e.g. fill_value)
VARIABLE_STRING_FIELDS = ("fill_value", "flag_values", "flag_meanings", "axis", "standard_name", "long_name", "units")

GLOBAL_ATTR_EXCLUDE = {"id", "oads_metadata_id", "og_netcdf_metadata", "extra_global_attrs"}
GLOBAL_ATTR_FIELDS = tuple(
    c.name for c in NetcdfGlobalAttributes.__table__.columns 
    if c.name not in GLOBAL_ATTR_EXCLUDE
)

VARIABLE_EXCLUDE = {"id", "oads_metadata_id"}
VARIABLE_FIELDS = tuple(
    c.name for c in NetcdfVariable.__table__.columns 
    if c.name not in VARIABLE_EXCLUDE
)

NUMERIC_FIELDS = {
        "geospatial_lat_min", "geospatial_lat_max", 
        "geospatial_lon_min", "geospatial_lon_max", 
        "geospatial_vertical_min", "geospatial_vertical_max"
    }

def _format_value_for_ui(value):
    """Converts lists/arrays/NaNs into safe types for Dash text inputs."""
    if isinstance(value, (list, tuple)):
        return ", ".join(map(str, value))
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return value
    
def _sanitize_for_json(value):
    """
    Recursively converts numpy scalar/array types and byte strings into
    plain JSON-serializable Python types (int, float, bool, str, list).
    Needed
    because values pulled from xarray/netCDF attrs (via var.attrs) are
    frequently numpy scalars, whcih json.dumps can't handel -- and that's
    what SQLAlchemy uses under the hood to write JSONB columns. dcc.Store
    round-trips tolerate numpy fine (Dash's encoder handles it), so this
    santization only needs to happen here, at the DB boundary.
    """
    # 1. Handle NumPy scalars and byte strings
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, np.ndarray):
        return [_sanitize_for_json(x) for x in value.tolist()]
    if isinstance(value, dict):
        return {k: _sanitize_for_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_for_json(v) for v in value]
    if isinstance(value, str) and value.strip() == "": # remove empty strings and make None
        return None
    return value

def _strip_invalid_enum_fields(kwargs: dict, enum_field_map: dict, record_label: str, issues: list):
    """
    Mutates 'kwargs' in place: for each field in 'enum_field_map', replaces the raw value
    with the proper enum member if valid, or removes the key entirely (leaving the 
    column NULL) if invalid -- logging + recording the mismatch either way.
    """ 
    for field_name, enum_cls in enum_field_map.items():
        if field_name not in kwargs:
            continue
        raw_value = kwargs[field_name]
        if raw_value is None:
            continue
        matched = _coerce_enum(raw_value, enum_cls)
        if matched is None:
            valid_options = [e.value for e in enum_cls]
            log.warning(
                "Invalid %s value %r on %s (valid options: %s). Leaving field unset",
                field_name, raw_value, record_label, valid_options
            )
            issues.append({
                "record": record_label,
                "field": field_name,
                "invalid_value": raw_value,
                "valid_options": valid_options
            })
            kwargs.pop(field_name)
        else:
            kwargs[field_name] = matched

def save_all_ocean_sites_metadata(session, submission_id: str, netcdf_metadata_dict: dict, is_new_upload: bool=False) -> list:
    """
    netcdf_metadata_dict will be dict formatted like the output of netCDF parser. This function
    will be used to save the data directly after parsing and the edited metadata after 
    clicking the Save button. Returns a list of enum-validation issues (possibly empty).
    e.g.:
        [{'record': 'global_attributes', 'field': 'data_mode',
        'invalid_value': "Realtime', 'valid_options': ['R', 'P', 'D', 'M']}]
    Any field with an invliad enum value is left NULL in the DB rather than blocking the whole
    save; the mismatch is logged and included in the returned list so the caller can surface 
    it in the UI.
    """
    log.info("Saving Ocean sites metadata...")
    issues = []
    # Needs to sanitize the json attr fields for dtypes to fit into sQLAclehmyt ables
    netcdf_metadata_dict = _sanitize_for_json(netcdf_metadata_dict)

    # 1. Get the associated oads metadata id or create it.
    oads_metadata_id = get_or_create_oads_metadata_id(session=session, submission_id=submission_id)

    # 2. Figure out what should be stored as teh "origina" (og) netcdf metadata
    #   - If we already have one stored, keep it as-is (don't overwrite on edits).abs
    #   - If we don't have one yet, THIS call is the original save, so snapshot the 
    #     raw incoming dict *before* we mutate anything.
    stmt = (
        select(NetcdfGlobalAttributes.og_netcdf_metadata)
        .where(NetcdfGlobalAttributes.oads_metadata_id == oads_metadata_id)
    )
    existing_og_metadata = session.execute(stmt).scalar_one_or_none()
    
    if is_new_upload:
        # A freshly uploaded/re-uploaded file always becomes the new baseline
        og_metadata_to_store = copy.deepcopy(netcdf_metadata_dict)
    elif existing_og_metadata is not None:
        og_metadata_to_store = existing_og_metadata
    else:
        # deepcopy so later mutatations to netcdf_metadata_dict can't leak into the snapshot
        og_metadata_to_store = copy.deepcopy(netcdf_metadata_dict)

    try:
        # 3. Wipe any existing rows for this oads_metadata_id
        session.query(NetcdfGlobalAttributes).filter_by(oads_metadata_id=oads_metadata_id).delete(synchronize_session=False)
        session.query(NetcdfDimension).filter_by(oads_metadata_id=oads_metadata_id).delete(synchronize_session=False)
        session.query(NetcdfVariable).filter_by(oads_metadata_id=oads_metadata_id).delete(synchronize_session=False)
    
        # 4. Add global attributes. This is a SINGLE record (one dictionary)
        global_attrs = dict(netcdf_metadata_dict[GLOBAL_ATTRIBUTES])
        _strip_invalid_enum_fields(global_attrs, GLOBAL_ATTR_ENUM_FIELDS, GLOBAL_ATTRIBUTES, issues)
        global_attrs[OG_NETCDF_METADATA] = og_metadata_to_store
        session.add(NetcdfGlobalAttributes(oads_metadata_id=oads_metadata_id, **global_attrs))

        # 5. Dimensions and variables ARE collections -- one row each
        for item in netcdf_metadata_dict[DIMENSIONS]:
            session.add(NetcdfDimension(oads_metadata_id=oads_metadata_id, **item))

        # 6. Variables
        for item in netcdf_metadata_dict[VARIABLES]:
            var_kwargs = dict(item)
            # Per-variable "dimensions" comes from the parser as a list of dim
            # names (E.g. (["time", "lat", "lon"]) -- NOT the same thing as the
            # top-level 'dimesnions' collection above. The column is a String
            # ("time lat lon" style), so it must be joined before insertion or 
            # the DB adapter will choke on a raw python list.abs
            dims = var_kwargs.get("dimensions")
            if isinstance(dims, (list, tuple)):
                var_kwargs["dimensions"] = " ".join(dims)

            record_label = f"variable:{var_kwargs.get('name', 'unknown')}"
            _strip_invalid_enum_fields(var_kwargs, VARIABLE_ENUM_FIELDS, record_label, issues)

            # These columns are String in the model, but after numpy santization
            # above they many not be plain int/float (e.g. fill_value = -999.0)
            # Cast explicitly so the DB driver isn't handed a numeric for a text column
            for field_name in VARIABLE_STRING_FIELDS:
                if field_name in var_kwargs and var_kwargs[field_name] is not None:
                    var_kwargs[field_name] = str(var_kwargs[field_name])
            
            session.add(NetcdfVariable(oads_metadata_id=oads_metadata_id, **var_kwargs))

        session.commit()
    except Exception:
        session.rollback()
        log.exception("Failed to save ocean sites metadata: rolled back.")
        raise
                
    log.info(f"Ocean sites metadata saved (oads_metadata_id={oads_metadata_id})")
    return issues

def _enum_value(maybe_enum):
    """Unwraps a SQLAlchemy enum column value to its plain string, or None"""
    if maybe_enum is None:
        return None
    return maybe_enum.value if hasattr(maybe_enum, "value") else maybe_enum
    
def _iso_or_none(dt):
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

def _global_attrs_row_to_dict(row: NetcdfGlobalAttributes) -> dict:
    info = {}
    for field_name in GLOBAL_ATTR_FIELDS:
        value = getattr(row, field_name)
        if field_name in ("data_mode", "data_type", "format_version"):
            info[field_name] = _enum_value(value)
        elif field_name in ("time_coverage_start", "time_coverage_end"):
            info[field_name] = _iso_or_none(value)
        elif field_name in NUMERIC_FIELDS:
            info[field_name] = str(value) if value is not None else "" # need to stringify numeric fields to preserve decimal places (e.g. 50.00 vs. 50, otherwise will drop)
        else:
            info[field_name]= value
    info["extra_global_attrs"] = row.extra_global_attrs or {}
    return info

def _dimension_row_to_dict(row: NetcdfDimension) -> dict:
    return {
        "name": row.name,
        "size": row.size,
        "unlimited": row.unlimited
    }

def _variable_row_to_dict(row: NetcdfVariable) -> dict:
    var_dict = {}
    for field_name in VARIABLE_FIELDS:
        value = getattr(row, field_name)
        if field_name == "var_type":
            var_type = _enum_value(value)
            if var_type == "data":
                var_type = infer_special_variable_type(row.name) or var_type
            var_dict[field_name] = var_type
        elif field_name == "dimensions":
            var_dict[field_name] = value.split(" ") if value else []
        elif field_name == "extra_attrs":
            var_dict[field_name] = value or {}
        else:
            var_dict[field_name] = value
    return var_dict

def load_ocean_sites_metadata(session, submission_id: str) -> Optional[dict]:
    """
    Reads the currently save (possibly edited) Ocean Sites metadata for a sumbission
    and reconstructs it into the same shape as the parser.metadata_dict:
    {"global_attributes": {...}, "dimensions": {...}, "variables": [...]}
    Returns None if there's no oads_metadata record for this submission yet, or no 
    global attributes have been saved yet (ie. nothing as been uploaded/parsed).
    Callers should treat None as 'nothing to hydrate the UI with yet
    """
    oads_metadata_id = get_oads_metadata_id(session, submission_id)
    if oads_metadata_id is None:
        return None

    # Notice that scalar_one_or_none() is now OUTSIDE the execute() parentheses
    global_attrs_row = session.execute(
        select(NetcdfGlobalAttributes).where(NetcdfGlobalAttributes.oads_metadata_id == oads_metadata_id)
    ).scalar_one_or_none()

    dimension_rows = session.execute(
        select(NetcdfDimension)
        .where(NetcdfDimension.oads_metadata_id == oads_metadata_id)
        .order_by(NetcdfDimension.id)
    ).scalars().all()

    variable_rows = session.execute(
        select(NetcdfVariable)
        .where(NetcdfVariable.oads_metadata_id == oads_metadata_id)
        .order_by(NetcdfVariable.id)
    ).scalars().all()

    if global_attrs_row is None and not dimension_rows and not variable_rows:
        return None

    log.info(f"Loaded ocean sites metadata for oads_metadata_id={oads_metadata_id}")

    return {
        GLOBAL_ATTRIBUTES: _global_attrs_row_to_dict(global_attrs_row),
        DIMENSIONS:[_dimension_row_to_dict(d) for d in dimension_rows],
        VARIABLES: [_variable_row_to_dict(v) for v in variable_rows]
        }

def load_original_ocean_sites_metadata(session, submission_id: str) -> Optional[dict]:
    """
    Reads the frozen 'original' snapshot (og_netcdf_metadata) for a submission --
    this is the baseline the diff compares against, distinct from
    load_ocean_sites_metadata() which reads the current/edited state.
    Returns None if nothing's been uploaded/parsed yet.
    """
    oads_metadata_id = get_oads_metadata_id(session, submission_id)
    if oads_metadata_id is None:
        return None

    stmt = (select(NetcdfGlobalAttributes.og_netcdf_metadata)
            .where(NetcdfGlobalAttributes.oads_metadata_id==oads_metadata_id)
            )

    og_dict = session.execute(stmt).scalar_one_or_none()

    # force string fields to be string fields else ui will see them as different (e.g. fill_value of -128 float vs. string)
    for var in og_dict["variables"]:
        if var.get("var_type") == "data":
            var["var_type"] = infer_special_variable_type(var.get("name")) or var["var_type"]
        for field_name in VARIABLE_STRING_FIELDS:
            if field_name in var and var[field_name] is not None:
                var[field_name] = str(var[field_name])
    return og_dict

def build_global_attributes_dict(
    site_code, platform_code, data_mode, data_type, format_version,
    update_interval, date_created, lat_min, lat_max, lon_min, lon_max,
    vertical_min, vertical_max, time_start, time_end, 
    extra_attr_key_ids, extra_attr_key_values, extra_attr_value_ids, extra_attr_value_values,
    extra_attrs_store=None,
):
    """
    Used in the save metadata and when changed the edited-parsed_store so
    the metadata diffs can be updated live.
    """
    removed_uids = {e['uid'] for e in (extra_attrs_store or []) if e.get('removed')}

    extra_keys_map = {k['index']: v for k, v in zip(extra_attr_key_ids, extra_attr_key_values)}
    extra_values_map = {k['index']: v for k, v in zip(extra_attr_value_ids, extra_attr_value_values)}
    live_extra_global_attrs = {}
    for uid, k in extra_keys_map.items():
        if uid in removed_uids:
            continue # soft-removed, exclude from  saved/current state
        v = extra_values_map.get(uid)
        if k and k.strip():
            live_extra_global_attrs[k.strip()] = v

    return {
        "site_code": site_code, "platform_code": platform_code, "data_mode": data_mode,
        "data_type": data_type, "format_version": format_version, "update_interval": update_interval,
        "date_created": date_created, "geospatial_lat_min": lat_min, "geospatial_lat_max": lat_max,
        "geospatial_lon_min": lon_min, "geospatial_lon_max": lon_max,
        "geospatial_vertical_min": vertical_min, "geospatial_vertical_max": vertical_max,
        "time_coverage_start": time_start, "time_coverage_end": time_end, 
        "extra_global_attrs": live_extra_global_attrs
    }

def build_variables_list(
    vars_store, standard_name_ids, standard_name_values, long_name_ids, long_name_values, units_ids, units_values, 
    fill_value_ids, fill_value_values, coordinate_ids, coordinate_values, 
    axis_ids, axis_values, flag_values_ids, flag_values_values,
    flag_meanings_ids, flag_meanings_values, 
    var_extra_attr_key_ids, var_extra_attr_key_values,
    var_extra_attr_value_ids, var_extra_attr_value_values,
    var_extra_stores=None
):
    """
    Used in the save metadata and when changed the edited-parsed_store so
    the metadata diffs can be updated live.
    """
    def _by_uids(ids, values):
        return {id_['index']: val for id_, val in zip(ids, values)}

    standard_name_map = _by_uids(standard_name_ids, standard_name_values)
    long_name_map = _by_uids(long_name_ids, long_name_values)
    units_map = _by_uids(units_ids, units_values)
    fill_values_map = _by_uids(fill_value_ids, fill_value_values)
    coordinate_map = _by_uids(coordinate_ids, coordinate_values)
    axis_map = _by_uids(axis_ids, axis_values)
    flag_values_map = _by_uids(flag_values_ids, flag_values_values)
    flag_meanings_map = _by_uids(flag_meanings_ids, flag_meanings_values)

    # Build removed uids per variable
    removed_by_var = {}
    for entry in (var_extra_stores or []):
        var_uid = entry['id']['index']
        removed_by_var[var_uid] = {e['uid'] for e in (entry.get('data') or []) if e.get('removed')}

    extra_var_attrs_map = {}
    for k_id, k_val, v_val in zip(var_extra_attr_key_ids or [], var_extra_attr_key_values or [], var_extra_attr_value_values or []):
        var_uid = k_id['index']
        attr_uid = k_id['attr_uid']
        if attr_uid in removed_by_var.get(var_uid, set()):
            continue
        key_text = (k_val or '').strip()
        if not key_text:
            continue
        extra_var_attrs_map.setdefault(var_uid, {})[key_text] = v_val

    # uids with at least one mounted fixed-field input = expanded cards
    mounted = set()
    for ids in (standard_name_ids, long_name_ids, units_ids, fill_value_ids, coordinate_ids,
                axis_ids, flag_values_ids, flag_meanings_ids):
        mounted.update(id_['index'] for id_ in (ids or []))

    variables = []
    for var in (vars_store or []):
        uid = var.get('uid')
        var_type = var.get('var_type')
        attrs = var.get('attributes') or {}

        def live_or_stored(live_map, field):
            # membership test (not .get default) so a deliberately cleared
            # input stays empty rather than resurrecting the stored value
            return live_map[uid] if uid in live_map else attrs.get(field)

        if uid in mounted:
            extra = extra_var_attrs_map.get(uid, {})
        else:
            # Unmounted card: derive extras from its store (respects removed flags)
            store_entries = next(
                (e.get('data') or [] for e in (var_extra_stores or []) if e['id']['index'] == uid), [])
            extra = {
                (e.get('key') or '').strip(): e.get('value')
                for e in store_entries
                if not e.get('removed') and (e.get('key') or '').strip()
            }

        variable_dict = {
            "name": var.get('name'), "var_type": var_type, "dtype": var.get('dtype'),
            "dimensions": var.get('dimensions') or [],
            "extra_attrs": extra,
            "observed_values": var.get('observed_values'),
        }
        if var_type == 'data':
            variable_dict.update({
                "standard_name": live_or_stored(standard_name_map, 'standard_name'),
                "long_name": live_or_stored(long_name_map, 'long_name'),
                "units": live_or_stored(units_map, 'units'),
                "fill_value": live_or_stored(fill_values_map, 'fill_value'),
                "coordinates": live_or_stored(coordinate_map, 'coordinates'),
            })
        elif var_type == "coordinate":
            variable_dict.update({
                "standard_name": live_or_stored(standard_name_map, 'standard_name'),
                "long_name": live_or_stored(long_name_map, 'long_name'),
                "units": live_or_stored(units_map, 'units'),
                "axis": live_or_stored(axis_map, 'axis'),
            })
        elif var_type == "qc":
            variable_dict.update({
                "long_name": live_or_stored(long_name_map, 'long_name'),
                "fill_value": live_or_stored(fill_values_map, "fill_value"),
                "flag_values": live_or_stored(flag_values_map, 'flag_values'),
                "flag_meanings": live_or_stored(flag_meanings_map, 'flag_meanings'),
            })
        elif var_type == "dm":
            variable_dict.update({
                "long_name": live_or_stored(long_name_map, 'long_name'),
                "flag_values": live_or_stored(flag_values_map, 'flag_values'),
                "flag_meanings": live_or_stored(flag_meanings_map, 'flag_meanings'),
            })
        elif var_type == "uncertainty":
            variable_dict.update({
                "long_name": live_or_stored(long_name_map, 'long_name'),
                "units": live_or_stored(units_map, 'units'),
                "fill_value": live_or_stored(fill_values_map, 'fill_value'),
            })
        variables.append(variable_dict)

    return variables

def _visible_problems(problems, mode, submission_id):
    """
    Used in variables callbacks for errors. Same quiet/loud
    rule as the About dataset tab
    """
    loud = bool(submission_id) and (mode or {}).get('loud_for') == submission_id
    if loud:
        return problems or []
    return [p for p in (problems or []) if p['kind'] != 'missing']
