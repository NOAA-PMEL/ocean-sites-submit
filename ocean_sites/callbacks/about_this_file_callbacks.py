from math import e
import time
import uuid
from dash import Input, Output, State, ALL, ctx, html, MATCH, Patch, no_update
from dash.exceptions import PreventUpdate
from ocean_sites.layouts import (
    _extra_attr_row,
    _extra_attr_row_children, 
    _err_text,
    _imported_var_card, 
    _dimension_strip,
    diff_badge_children, 
    diff_badge_style
    )
from ocean_sites.utils import (
    _format_value_for_ui,
    global_problems_by_field, 
    validate_metadata,
    build_metadata_diff
    )
from ocean_sites.utils.metadata_diff import GLOBAL_ATTR_ENUM_FIELDS, _coerce_enum
from portal_core.utils import app_logger as log

def register_callbacks(app):
    # Add global attribute
    @app.callback(
        Output('nc-extra-attrs-container', 'children'),
        Output('nc-extra-attrs-store', 'data'),
        Input('btn-add-nc-extra-attr', 'n_clicks'),
        State('nc-extra-attrs-store', 'data'),
        prevent_initial_call=True
    )
    def add_extra_global_attribute(add_clicks, store):
        """Appends ONE row via Patch; existing rows are never rebuilt."""
        if not add_clicks:
            raise PreventUpdate
        new_uid = str(uuid.uuid4())
        new_entry = {'uid': new_uid, 'key': '', 'value': '', 'is_original': False, 'removed': False}
        new_row = _extra_attr_row(uid=new_uid, key='', value='', is_original=False, removed=False)
        if not store:
            return [new_row], [new_entry]  # also clears the empty-state message
        children_patch = Patch(); children_patch.append(new_row)
        store_patch = Patch(); store_patch.append(new_entry)
        return children_patch, store_patch

    # 1. Update ONLY the row layout (Safe MATCH scoping)
    @app.callback(
        Output({'type': 'nc-extra-attr-row', 'index': MATCH}, 'children'),
        Output({'type': 'nc-extra-attr-row', 'index': MATCH}, 'style'),
        Input({'type': 'btn-action-nc-extra-attr', 'index': MATCH}, 'n_clicks'),
        State({'type': 'btn-action-nc-extra-attr', 'index': MATCH}, 'children'), # Inspect current text label
        State({'type': 'nc-extra-attr-key', 'index': MATCH}, 'value'),
        State({'type': 'nc-extra-attr-value', 'index': MATCH}, 'value'),
        State({'type': 'nc-extra-attr-row', 'index': MATCH}, 'style'),
        State('nc-extra-attrs-store', 'data'),
        prevent_initial_call=True
    )
    def toggle_global_attribute_row_ui(n_clicks, current_btn_label, key, value, row_style, store):
        if not n_clicks:
            raise PreventUpdate
        
        uid = ctx.triggered_id['index']
        entry = next((e for e in (store or []) if e['uid'] == uid), None)
        is_original = entry.get('is_original', False) if entry else False

        if not is_original:
            return [], {**(row_style or {}), 'display': 'none'}
        
        new_removed = (current_btn_label == 'Remove')

        row_container_style = {
            'backgroundColor': '#1c1c1c' if new_removed else '#252525',
            'border': '1px solid #333' if new_removed else '1px solid #2a2a2a',
            'borderRadius': '6px',
            'padding': '8px 12px',
            'display': 'flex',
            'alignItems': 'center',
            'gap': '10px',
            'marginBottom': '6px',
            'opacity': '0.55' if new_removed else '1'
        }
        
        return _extra_attr_row_children(
            uid=uid, key=key, value=value, var_uid=None,
            removed=new_removed, is_original=True
        ), row_container_style

    # 2. Synchronize the central store data separately using ALL
    @app.callback(
        Output('nc-extra-attrs-store', 'data', allow_duplicate=True),
        Input({'type': 'btn-action-nc-extra-attr', 'index': ALL}, 'n_clicks'),
        State({'type': 'btn-action-nc-extra-attr', 'index': ALL}, 'id'),
        State({'type': 'btn-action-nc-extra-attr', 'index': ALL}, 'children'),
        State('nc-extra-attrs-store', 'data'),
        prevent_initial_call=True
    )
    def sync_global_attribute_store_toggle(n_clicks_list, id_list, label_list, store):
        if not ctx.triggered_id:
            raise PreventUpdate
            
        target_uid = ctx.triggered_id['index']
        
        # Look up what the action type was for the specific button clicked
        clicked_index = next(i for i, identity in enumerate(id_list) if identity['index'] == target_uid)
        action_label = label_list[clicked_index]
        
        store = store or []
        entry = next((e for e in store if e['uid'] == target_uid), None)
        if entry is None:
            raise PreventUpdate # alread deleted on a re-fire

        if not entry.get('is_original', 'False'):
            # User added row - drop it from the store entirely
            return [e for e in store if e['uid'] != target_uid]

        new_removed = (action_label == 'Undo') # needs to be undo to be removed, because when clicked will change to undo
        
        updated_store = [
            {**e, 'removed': new_removed} if e['uid'] == target_uid else e
            for e in store
        ]

        return updated_store
    
    # Callback to load the global metadata upon opening the metadata modal.
    @app.callback(
        Output('nc-extra-attrs-container', 'children', allow_duplicate=True),
        Output('nc-extra-attrs-store', 'data', allow_duplicate=True),
        Output('nc-time-coverage-start', 'value'),
        Output('nc-time-coverage-end', 'value'),
        Output('nc-geospatial-vertical-min', 'value'),
        Output('nc-geospatial-vertical-max', 'value'),
        Output('nc-geospatial-lat-min', 'value'),
        Output('nc-geospatial-lat-max', 'value'),
        Output('nc-geospatial-lon-min', 'value'),
        Output('nc-geospatial-lon-max', 'value'),
        Output('nc-update-interval', 'value'),
        Output('nc-date-created', 'value'),
        Output('nc-data-mode', 'value'),
        Output('nc-data-type', 'value'),
        Output('nc-format-version', 'value'),
        Output('nc-site-code', 'value'),
        Output('nc-platform-code', 'value'),

        Output('nc-var-cards-container', 'children'),
        Output('nc-vars-store', 'data'),
        Output('nc-var-count', 'children'),
        Output('nc-dimension-strip', 'children'),
        Output('metadata-problems-store', 'data', allow_duplicate=True),
        Output('metadata-diff-store', 'data', allow_duplicate=True),

        Input('metadata-load-signal-store', 'data'),
        State('original-parsed-data-store', 'data'),
        prevent_initial_call='initial_duplicate'
        )
    def load_and_render_metadata_from_db(metadata_store, original_store):
        log.debug(f"[load_and_render_metadata_from_db]: attempting to load and render data from db...")
        
        original_store = (
            metadata_store.get('_original_dict')
            if isinstance(metadata_store, dict) and '_original_dict' in metadata_store
            else original_store
        )
        
        should_open = isinstance(metadata_store, dict) and bool(metadata_store.get('_open_modal'))
        ready_signal = {'ts': time.time()} if should_open else None

        # TO prevent metadata_store being populated with stale data from other submissions
        if not metadata_store or (not metadata_store.get("global_attributes") and not metadata_store.get("variables")):
            empty_msg = [
                html.P(
                    'No additional attributes found.',
                    id='nc-extra-attrs-empty-msg',
                    style={
                        'color': '#555',
                        'fontStyle': 'italic',
                        'fontSize': '12px',
                        'marginBottom': '8px',
                    }
                )
            ]

            return (
                empty_msg,  # nc-extra-attrs-container
                [],         # nc-extra-attrs-store
                "", "", "", "", "", "", "", "", "", "",  # Text field values (10 fields)
                None, None, None,                        # Dropdowns values (data-mode, type, format)
                "", "",                                  # site_code, platform_code
                [],         # nc-var-cards-container
                [],         # nc-vars-store
                "0 variables",  # nc-var-count
                [],          # nc-dimension-strip
                [], None
            )
        # ------------------------------------------------------------------------------------------

        original_extra_global = (
            (original_store or {}).get("global_attributes", {}).get("extra_global_attrs", {}) or {}
        )

        extra_attrs_store = []
        extra_global_attrs = metadata_store.get("global_attributes", {}).get("extra_global_attrs", {}) or {}
        for key, value in extra_global_attrs.items():
            extra_attrs_store.append({
                'uid': str(uuid.uuid4()),
                'key': key,
                'value': _format_value_for_ui(value),
                'is_original': key in original_extra_global,
                'removed': False
            })
        # Resurrect soft-removed originals as struck-out rows
        for key, value in original_extra_global.items():
            if key not in extra_global_attrs:
                extra_attrs_store.append({
                    'uid': str(uuid.uuid4()),
                    'key': key,
                    'value': _format_value_for_ui(value),
                    'is_original': True,
                    'removed': True
                })


        extra_global_attrs_children = [
            _extra_attr_row(uid=entry['uid'], key=entry['key'], value=entry['value'], is_original=entry['is_original'], removed=entry['removed'])
            for entry in extra_attrs_store
        ]

        # 2. Required fields
        required_global_attrs = metadata_store.get("global_attributes", {})
        coverage_start = required_global_attrs.get("time_coverage_start", "")
        coverage_end = required_global_attrs.get("time_coverage_end", "")
        vertical_min = required_global_attrs.get("geospatial_vertical_min", "")
        vertical_max = required_global_attrs.get("geospatial_vertical_max", "")
        lat_min = required_global_attrs.get("geospatial_lat_min", "")
        lat_max = required_global_attrs.get("geospatial_lat_max", "")
        lon_min = required_global_attrs.get("geospatial_lon_min", "")
        lon_max = required_global_attrs.get("geospatial_lon_max", "")
        update_interval = required_global_attrs.get("update_interval", "")
        date_created = required_global_attrs.get("date_created", "")
        data_mode = required_global_attrs.get("data_mode", "")
        data_type = required_global_attrs.get("data_type", "")
        format_version = required_global_attrs.get("format_version", "")  
        site_code = required_global_attrs.get("site_code", "")   
        platform_code = required_global_attrs.get("platform_code", "")

        # ---- Variables ----
        original_vars_by_name = {
            v.get('name'): v for v in (original_store or {}).get("variables", [])
        }
        vars_store = []
        metadata_vars = metadata_store.get("variables", [])
        for var in metadata_vars:
            raw_dims = var.get("dimensions", [])
            flat_dims = " ".join(raw_dims) if isinstance(raw_dims, (list, tuple)) else str(raw_dims or "")

            vars_store.append({
                'uid': str(uuid.uuid4()),
                'name': var.get('name', ''),
                'var_type': var.get('var_type', ''),
                'dtype': var.get('dtype', ''),
                'dimensions': flat_dims,
                'attributes': var,
                'observed_values': var.get('observed_values'),
            })
        vars_children = []
        for var in vars_store:
            orig_var = original_vars_by_name.get(var['name'], {})
            vars_children.append(
                _imported_var_card(
                    uid=var.get('uid'),
                    name=var.get('name'),
                    var_type=var.get('var_type'),
                    dtype=var.get('dtype'),
                    dimensions=var.get('dimensions'),
                    attributes=var.get('attributes'),
                    original_extra_attrs=orig_var.get('extra_attrs', {})
                )
            )

        # 2. Build dimensions strip
        dimensions = metadata_store.get("dimensions", [])
        dimensions_children = [_dimension_strip(dimensions)]

        # Coerce invalid enum values to None so initial validation matches the UI dropdowns
        normalized_global = dict(required_global_attrs)
        for field, enum_cls in GLOBAL_ATTR_ENUM_FIELDS.items():
            if _coerce_enum(normalized_global.get(field), enum_cls) is None:
                normalized_global[field] = None
        normalized_meta = {**metadata_store, "global_attributes": normalized_global}

        problems = validate_metadata(normalized_meta, original_store)
        diff = build_metadata_diff(original_store, normalized_meta) if original_store else None
     
        return (
            extra_global_attrs_children, 
            extra_attrs_store, 
            coverage_start, 
            coverage_end, 
            vertical_min, 
            vertical_max,
            lat_min,
            lat_max,
            lon_min,
            lon_max,
            update_interval,
            date_created,
            data_mode,
            data_type,
            format_version,
            site_code,
            platform_code,
            vars_children, 
            vars_store, 
            f"{len(vars_store)} variables",
            dimensions_children,
            problems, 
            diff
            )

    @app.callback(
        Output({'type': 'nc-field-dot', 'field': ALL}, 'style'),
        Output({'type': 'nc-field-dot', 'field': ALL}, 'children'),
        Input('metadata-diff-store', 'data'),
        prevent_initial_call=True
    )
    def update_field_badges(diff):
        if not diff:
            raise PreventUpdate

        changed_by_field = {e['field']: e for e in diff.get('global_attributes', {}).get('changed', [])}
        field_keys = [match['id']['field'] for match in ctx.outputs_list[0]]

        styles, children = [], []
        for field in field_keys:
            entry = changed_by_field.get(field)
            if entry:
                was = entry['original'] if entry['original'] not in (None, '') else '(empty)'
                styles.append(diff_badge_style('changed'))
                children.append(diff_badge_children('changed', f"was {was}"))
            else:
                styles.append({'display': 'none'})
                children.append([])

        return styles, children

    @app.callback(
        Output({'type': 'nc-extra-attr-dot', 'index': ALL}, 'style'),
        Output({'type': 'nc-extra-attr-dot', 'index': ALL}, 'children'),
        Input({'type': 'nc-extra-attr-key', 'index': ALL}, 'value'),
        Input('metadata-diff-store', 'data'),
        State({'type': 'nc-extra-attr-key', 'index': ALL}, 'id'),
        prevent_initial_call=True
    )
    def update_extra_attr_badges(key_values, diff, key_ids):
        if not diff:
            raise PreventUpdate

        ga = diff.get('global_attributes', {})
        added = {
            e['field'].split('.', 1)[1] for e in ga.get('added', [])
            if e['field'].startswith('extra_global_attrs.')
            }
        
        changed = {
            e['field'].split('.', 1)[1]: e
            for e in ga.get('changed', [])
            if e['field'].startswith('extra_global_attrs.')
        }

        dot_indices = [match['id']['index'] for match in ctx.outputs_list[0]]
        key_by_uid = {k_id['index']: (k_val or '').strip() for k_id, k_val in zip(key_ids, key_values)}

        styles, children = [], []
        for uid in dot_indices:
            key = key_by_uid.get(uid, '')
            if key in added:
                styles.append(diff_badge_style('added'))
                children.append(diff_badge_children('added', 'new attribute'))
            elif key in changed:
                was = changed[key]['original'] if changed[key]['original'] not in (None, '') else '(empty)'
                styles.append(diff_badge_style('changed'))
                children.append(diff_badge_children('changed', f"was: {was}"))
            else:
                styles.append({'display': 'none'})
                children.append([])

        return styles, children
    

    @app.callback(
        Output({'type': 'nc-field-err', 'field': ALL}, 'children'),
        Output({'type': 'nc-field-wrap', 'field': ALL}, 'className'),
        Output({'type': 'nc-extra-attr-key-err', 'index': ALL}, 'children'),
        Output({'type': 'nc-extra-attr-key', 'index': ALL}, 'className'),
        Output({'type': 'nc-extra-attr-err', 'index': ALL}, 'children'),
        Output({'type': 'nc-extra-attr-value', 'index': ALL}, 'className'),
        Output('metadata-ready-store', 'data'),
        Input('metadata-problems-store', 'data'),
        Input('metadata-validation-mode', 'data'),
        Input('current-submission', 'data'),
        State({'type': 'nc-extra-attr-key', 'index': ALL}, 'id'),
        State({'type': 'nc-extra-attr-key', 'index': ALL}, 'value'),
        State('open-metadata-store', 'data'),
        State('metadata-modal-overlay', 'style'),
        prevent_initial_call=True
    )
    def update_global_field_errors(
        problems, mode, submission_id, extra_attr_key_ids, extra_attr_key_values,
    open_metadata_store, modal_style
    ):
        #loud = bool(submission_id) and (mode or {}).get('loud_for') == submission_id
        #if not loud:
        #    # Before this submission's first save, only complain about values they actually typed
        #    problems= [p for p in (problems or []) if p['kind'] != 'missing']

        by_field = global_problems_by_field(problems or [])

        err_fields = [m['id']['field'] for m in ctx.outputs_list[0]]
        wrap_fields = [m['id']['field'] for m in ctx.outputs_list[1]]
        extra_attr_key_err_ids = [m['id'] for m in ctx.outputs_list[2]]
        extra_attr_key_class_ids = [m['id'] for m in ctx.outputs_list[3]]
        extra_attr_err_ids = [m['id'] for m in ctx.outputs_list[4]]
        extra_attr_value_ids = [m['id'] for m in ctx.outputs_list[5]]
        extra_attr_keys = {
            id_['index']: (value or '').strip()
            for id_, value in zip(extra_attr_key_ids or [], extra_attr_key_values or [])
        }
        extra_attr_problems = {}
        for problem in problems or []:
            if problem['scope'] != 'global':
                continue
            field = problem['field']
            part = problem.get('field_part') or 'value'
            extra_attr_problems.setdefault(field, {})[part] = problem

        children = []
        for f in err_fields:
            p = by_field.get(f)
            # A field named only in another problems' also_fields gets the color
            # but no a duplicate copy of the message
            children.append(
                _err_text(
                    p['message'], p['severity'], p.get('link_text'), p.get('link_url')
                )
                if p and p['field'] == f else []
            )

        classes = []
        for f in wrap_fields:
            p = by_field.get(f)
            classes.append(f"nc-field-wrap is-{p['severity']}" if p else 'nc-field-wrap')

        extra_attr_key_errors = []
        for id_ in extra_attr_key_err_ids:
            field = extra_attr_keys.get(id_['index'])
            p = extra_attr_problems.get(field, {}).get('key')
            extra_attr_key_errors.append(
                _err_text(p['message'], p['severity'])
                if p else []
            )

        extra_attr_key_classes = []
        for id_ in extra_attr_key_class_ids:
            field = extra_attr_keys.get(id_['index'])
            p = extra_attr_problems.get(field, {}).get('key')
            extra_attr_key_classes.append(
                f"nc-extra-attr-key is-{p['severity']}"
                if p else 'nc-extra-attr-key'
            )

        extra_attr_errors = []
        for id_ in extra_attr_err_ids:
            field = extra_attr_keys.get(id_['index'])
            field_problems = extra_attr_problems.get(field)
            p = (
                field_problems.get('value')
                if field_problems is not None
                else by_field.get(field)
            )
            extra_attr_errors.append(
                _err_text(
                    p['message'], p['severity'], p.get('link_text'), p.get('link_url')
                )
                if p and p['field'] == field else []
            )

        extra_attr_value_classes = []
        for id_ in extra_attr_value_ids:
            field = extra_attr_keys.get(id_['index'])
            field_problems = extra_attr_problems.get(field)
            p = (
                field_problems.get('value')
                if field_problems is not None
                else by_field.get(field)
            )
            extra_attr_value_classes.append(
                f"nc-extra-attr-value is-{p['severity']}"
                if p else 'nc-extra-attr-value'
            )
     
        is_closed = (modal_style or {}).get('display') in (None, 'none')
        ready_signal = (
            {'ts': time.time()}
            if (is_closed and open_metadata_store is not None and problems is not None)
            else no_update
        )

        return (
            children,
            classes,
            extra_attr_key_errors,
            extra_attr_key_classes,
            extra_attr_errors,
            extra_attr_value_classes,
            ready_signal,
        )
