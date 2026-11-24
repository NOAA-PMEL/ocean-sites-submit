from dash import Input, Output, State, ALL, html, ctx, no_update, MATCH, Patch
from dash.exceptions import PreventUpdate
from ocean_sites.layouts import (
    _var_card_body_children, 
    _var_err_pill_style,
    _var_warn_pill_style,
    _extra_attr_row, 
    _extra_attr_row_children, 
    diff_badge_children, 
    diff_badge_style, 
    _err_text
)
from ocean_sites.utils import (
    error_count_by_variable,
    warning_count_by_variable,
    variable_problems_by_name,
    _visible_problems,
)
import uuid

_VAR_TYPE_COLORS = {
    'all': '#4f8ef7',
    'data': '#b968e0',
    'coordinate': '#38d9a9',
    'qc': '#f7b44f',
    'dm': '#63a6ff',
    'uncertainty': '#d58aff'
}
_VAR_TYPE_LABLES = {
    'all': 'total',
    'data': 'data',
    'coordinate': 'coordinate',
    'qc': 'QC',
    'dm': 'DM',
    'uncertainty': 'uncertainty'
}
_EXTRA_SECTION_FIELDS = {'ancillary_variables'}

def register_callbacks(app):

    @app.callback(
        Output({'type': 'nc-var-card-body', 'index': MATCH}, 'style'),
        Output({'type': 'nc-var-card-body', 'index': MATCH}, 'children'),
        Output({'type': 'nc-var-arrow', 'index': MATCH}, 'children'),
        Input({'type': 'nc-var-card-header', 'index': MATCH}, 'n_clicks'),
        State({'type': 'nc-var-card-body', 'index': MATCH}, 'style'),
        State({'type': 'nc-var-card-body', 'index': MATCH}, 'children'),
        State({'type': 'nc-var-extra-store', 'index': MATCH}, 'data'),
        State('nc-vars-store', 'data'),
        prevent_initial_call=True
    )
    def toggle_var_card(n_clicks, current_style, body_children, extra_store, vars_store):
        """
        Expand/collapse. First expand lazily builds the body; afterwards
        collapse/expand only flips display so typed edits survive.
        """
        uid = ctx.triggered_id['index']

        if current_style.get('display') == 'none':
            if not body_children:
                entry = next((v for v in (vars_store or []) if v.get('uid') == uid), None)
                if entry is None:
                    raise PreventUpdate
                children = _var_card_body_children(
                    uid=uid,
                    var_type=entry.get('var_type'),
                    attributes=entry.get('attributes') or {},
                    extra_store_data=extra_store or [],
                )
            else:
                children = no_update
            return {**current_style, 'display': 'block'}, children, '▾'

        return {**current_style, 'display': 'none'}, no_update, '▸'

    # --------------------------------------
    # Toggle variable data type panels
    # --------------------------------------
    @app.callback(
        Output({'type': 'nc-var-panel-shared', 'index': MATCH}, 'style'),
        Output({'type': 'nc-var-panel-data', 'index': MATCH}, 'style'),
        Output({'type': 'nc-var-panel-coord', 'index': MATCH}, 'style'),
        Output({'type': 'nc-var-panel-qc', 'index': MATCH}, 'style'),

        Input({'type': 'nc-var-type-dropdown', 'index': MATCH}, 'value'),
        prevent_initial_call=True
    )
    def toggle_var_type_panels(var_type):
        """
        Shows the relevant attribute panels when a variable type is selected
        in a manual card. Mirrors the cov-type-dropdown pattern in the arctic 
        spatial tab.
        """
        show = {'display': 'block'}
        hide = {'display': 'none'}

        if not var_type:
            return hide, hide, hide, hide

        return (
            show,
            show if var_type == 'data' else hide,
            show if var_type == 'coordinate' else hide,
            show if var_type == 'qc' else hide
        )

    # --------------------------------------
    # Add attribute within the var card
    # --------------------------------------
    @app.callback(
        Output({'type': 'nc-var-extra-container', 'index': MATCH}, 'children'),
        Output({'type': 'nc-var-extra-store', 'index': MATCH}, 'data'),
        Input({'type': 'btn-add-nc-var-attr', 'index': MATCH}, 'n_clicks'),
        State({'type': 'nc-var-extra-store', 'index': MATCH}, 'data'),
        prevent_initial_call=True
    )
    def add_var_extra_attr(n_clicks, store):
        if not n_clicks:
            raise PreventUpdate
        var_uid = ctx.triggered_id['index']
        new_uid = str(uuid.uuid4())
        new_entry = {'uid': new_uid, 'key': '', 'value': '', 'is_original': False, 'removed': False}
        new_row = _extra_attr_row(uid=new_uid, key='', value='', var_uid=var_uid,
                                  is_original=False, removed=False)
        if not store:
            return [new_row], [new_entry]
        children_patch = Patch(); children_patch.append(new_row)
        store_patch = Patch(); store_patch.append(new_entry)
        
        return children_patch, store_patch
        
    @app.callback(
        Output({'type': 'nc-var-extra-container', 'index': MATCH}, 'children', allow_duplicate=True),
        Output({'type': 'nc-var-extra-store', 'index': MATCH}, 'data', allow_duplicate=True),
        Input({'type': 'btn-remove-nc-var-extra-attr', 'index': MATCH, 'attr_uid': ALL}, 'n_clicks'),
        State({'type': 'nc-var-extra-store', 'index': MATCH}, 'data'),
        prevent_initial_call=True
    )
    def remove_var_extra_attr(n_clicks, store):
        """
        Removes a single extra-attribute row from within on variable card.
        MATCH on 'index' (the variable's uid) keeps this scoped to the card
        that was clicked, so it never touches another card's store/container.
        """
        if not any(n for n in n_clicks if n):
            raise PreventUpdate

        var_uid = ctx.triggered_id['index']
        attr_uid_to_remove = ctx.triggered_id['attr_uid']

        entry = next((e for e in (store or []) if e['uid'] == attr_uid_to_remove), None)
        if entry is None or entry.get('is_original'):
            raise PreventUpdate

        updated_store = [entry for entry in store if entry['uid'] != attr_uid_to_remove]

        if updated_store:
            children = [
                _extra_attr_row(
                    uid=entry['uid'], key=entry['key'], value=entry['value'], var_uid=var_uid,
                    is_original=entry.get('is_original', False), removed=entry.get('removed', False))
                for entry in updated_store
            ]
        else:
            children = [
                html.P(
                    'No extra attributes',
                    style={
                        'color': '#555',
                        'fontStyle': 'italic',
                        'fontSize': '12px',
                        'marginBottom': '8px'
                    }
                )
            ]

        return children, updated_store

    # 1. Update ONLY the variable row layout (Scoped to both var index and attr_uid)
    @app.callback(
        Output({'type': 'nc-var-extra-attr-row', 'index': MATCH, 'attr_uid': MATCH}, 'children'),
        Output({'type': 'nc-var-extra-attr-row', 'index': MATCH, 'attr_uid': MATCH}, 'style'),
        Input({'type': 'btn-action-nc-var-extra-attr', 'index': MATCH, 'attr_uid': MATCH}, 'n_clicks'),
        State({'type': 'btn-action-nc-var-extra-attr', 'index': MATCH, 'attr_uid': MATCH}, 'children'),
        State({'type': 'nc-var-extra-attr-key', 'index': MATCH, 'attr_uid': MATCH}, 'value'),
        State({'type': 'nc-var-extra-attr-value', 'index': MATCH, 'attr_uid': MATCH}, 'value'),
        State({'type': 'nc-var-extra-attr-row', 'index': MATCH, 'attr_uid': MATCH}, 'style'),
        State({'type': 'nc-var-extra-store', 'index': MATCH}, 'data'),
        prevent_initial_call=True
    )
    def toggle_var_extra_attr_row_ui(n_clicks, current_btn_label, key, value, row_style, store):
        if not n_clicks:
            raise PreventUpdate

        var_uid = ctx.triggered_id['index']
        attr_uid = ctx.triggered_id['attr_uid']

        entry = next((e for e in (store or []) if e['uid'] == attr_uid), None)
        is_original = entry.get('is_original', 'False') if entry else False

        if not is_original:
            return [], {**(row_style or {}), 'display': 'none'}
        
        # If the button says 'Remove', we want to set soft-removed state to True
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
            uid=attr_uid, key=key, value=value, var_uid=var_uid,
            removed=new_removed, is_original=True
        ), row_container_style

    @app.callback(
        Output({'type': 'nc-var-extra-store', 'index': ALL}, 'data', allow_duplicate=True),
        Input({'type': 'btn-action-nc-var-extra-attr', 'index': ALL, 'attr_uid': ALL}, 'n_clicks'),
        State({'type': 'btn-action-nc-var-extra-attr', 'index': ALL, 'attr_uid': ALL}, 'id'),
        State({'type': 'btn-action-nc-var-extra-attr', 'index': ALL, 'attr_uid': ALL}, 'children'),
        State({'type': 'nc-var-extra-store', 'index': ALL}, 'id'),
        State({'type': 'nc-var-extra-store', 'index': ALL}, 'data'),
        prevent_initial_call=True
    )
    def sync_var_extra_store_toggle(n_clicks_list, id_list, label_list, store_ids, store_datas):
        if not ctx.triggered_id:
            raise PreventUpdate

        target_var_uid = ctx.triggered_id['index']
        target_attr_uid = ctx.triggered_id['attr_uid']
        
        # Find which index inside the input arrays matches the button that got clicked
        clicked_idx = next(
            i for i, identity in enumerate(id_list) 
            if identity['index'] == target_var_uid and identity['attr_uid'] == target_attr_uid
        )
        action_label = label_list[clicked_idx]
        new_removed = (action_label == 'Undo') # needs to be Undo for new_removed to be True because will switch to Udno undo once removed is clicked.

        updated_outputs = []
        for s_id, s_data in zip(store_ids, store_datas):
            # Target ONLY the store map configuration belonging to the parent variable card
            if s_id['index'] != target_var_uid:
                updated_outputs.append(no_update)
                continue

            current_data = s_data or []
            entry = next((e for e in current_data if e['uid'] == target_attr_uid), None)
            if entry is None:
                updated_outputs.append(no_update) #already deleted on refire
            elif not entry.get('is_original', False):
                # user added row: drop it entirely
                updated_outputs.append([e for e in current_data if e['uid'] != target_attr_uid])
            else:
                updated_outputs.append([
                    {**e, 'removed': new_removed} if e['uid'] == target_attr_uid else e
                    for e in current_data
                ])
                
        return updated_outputs

    # --------------------------------------
    # Filter Variable Cards - 3 callbacks
    # --------------------------------------
    @app.callback(
        Output('nc-var-filter-store', 'data'),
        Input({'type': 'nc-var-filter-btn', 'index': ALL}, 'n_clicks'),
        Input('current-submission', 'data'),
        prevent_initial_call=True,
    )
    def update_filter_store(n_clicks, current_submission):
        """
        Updates the active filter store when a filter button is clicked.
        """
        # 1. Identify what triggered the callback
        trigger = ctx.triggered_id

        # 2. If the workspace switched submissions, reset the filter completely
        if trigger == 'current-submission':
            return 'all'

        # 3. Otherwise, check if a button click actually happened 
        # (prevents updating on initial button mounting arrays)
        if not any(n for n in n_clicks if n):
            return no_update

        # 4. Return the specific filter string ('data', 'qc', etc.)
        return trigger['index']

    @app.callback(
        Output({'type': 'nc-var-filter-btn', 'index': ALL}, 'style'),
        Input('nc-var-filter-store', 'data'),
        prevent_initial_call=False
    )
    def update_filter_btn_styles(active_filter):
        """
        Updates filter button styles to reflect the currently active filter.
        """
        # handle the initial page-load state cleanly if the store hasn't initialized
        current_filter = active_filter if active_filter is not None else 'all'
        
        # Extract exactly which button is which from the pattern match context
        indices = [match['id']['index'] for match in ctx.outputs_list]

        base_style = {
            'borderRadius': '3px',
            'fontSize': '10px',
            'fontWeight': '600',
            'padding': '4px 8px',
            'cursor': 'pointer',
            'textTransform': 'uppercase',
            'letterSpacing': '.04em',
            'fontFamily': 'inherit'
        }

        inactive_style = {
            **base_style,
            'background': 'transparent',
            'border': '1px solid #2d2d2d',
            'color': '#3a3a3a'
        }

        def active_style_for(index):
            color = _VAR_TYPE_COLORS.get(index, '#4f8ef7')
            return {
                **base_style,
                'background': f'{color}14',
                'border': f'1px solid {color}40',
                'color': color
            }

        return [
            active_style_for(index) if index == current_filter else inactive_style
            for index in indices
        ]

    @app.callback(
        Output({'type': 'nc-var-card', 'index': ALL}, 'style'),
        Output('nc-var-count', 'children', allow_duplicate=True),
        Input('nc-var-filter-store', 'data'),
        Input('nc-vars-store', 'data'),
        Input('nc-var-search', 'value'),
        prevent_initial_call=True
    )
    def filter_var_cards(active_filter, store, search_query):
        """
        Shows or hides variable cards based on the active type filter.
        Matches cards by uid order from the store.

        Also fires when nc-vars-store changes (not just the filter store)
        sow newly loaded/removed variables get the current filter re-applied
        rather than showing stale visibility.
        """
        current_filter = active_filter if active_filter else 'all'
        color = _VAR_TYPE_COLORS.get(current_filter, '#4f8ef7')
        label = _VAR_TYPE_LABLES.get(current_filter, 'total')
        query = (search_query or '').strip().lower()
        
        if not store:
            return [], html.Span('0 total variables', style={'color': color})

        indices = [match['id']['index'] for match in ctx.outputs_list[0]]

        # Build a lookup uid -> source/var_type from the store
        store_map = {entry['uid']: entry for entry in store}

        styles = []
        visible_count = 0
        for index in indices:
            entry = store_map.get(index, {})
            var_type = entry.get('var_type')
            name = entry.get('name') or ''

            type_match = current_filter == 'all' or var_type == current_filter
            search_match = not query or query in name.lower()
            visible = type_match and search_match

            if visible:
                visible_count += 1

            styles.append({
                'backgroundColor': '#252525',
                'border': '1px solid #2a2a2a',
                'borderRadius': '6px',
                'marginBottom': '8px',
                'overflow': 'hidden',
                'display': 'block' if visible else 'none',
            })

        var_word = 'variable' if visible_count == 1 else 'variables'
        count_text = f'{visible_count} {label} {var_word}'
        return styles, html.Span(count_text, style={'color': color})

    @app.callback(
        Output({'type': 'nc-var-field-dot', 'index': ALL, 'field': ALL}, 'style'),
        Output({'type': 'nc-var-field-dot', 'index': ALL, 'field': ALL}, 'children'),
        Input('metadata-diff-store', 'data'),
        State('nc-vars-store', 'data'),
        prevent_initial_call=True
    )
    def update_var_field_badges(diff, vars_store):
        if not diff:
            raise PreventUpdate

        name_by_uid = {v['uid']: v.get('name') for v in (vars_store or [])}
        
        # Safely extract changes, defaulting to empty dicts if the keys don't exist yet
        changed_by_var = {
            entry['name']: {f['field']: f for f in entry['fields']['changed']}
            for entry in diff.get('variables', {}).get('changed', [])
        }

        # ctx.outputs_list[0] contains all the matching IDs currently in the DOM
        dot_ids = [match['id'] for match in ctx.outputs_list[0]]
        styles, children = [], []
        
        for id_ in dot_ids:
            name = name_by_uid.get(id_['index'])
            
            # Check if this specific variable has changes, and if this specific field was changed
            entry = changed_by_var.get(name, {}).get(id_['field']) if name else None
            
            if entry:
                was = entry['original'] if entry['original'] not in (None, '') else '(empty)'
                styles.append(diff_badge_style('changed'))
                children.append(diff_badge_children('changed', f"was: {was}"))
            else:
                # Field was NOT changed: hide the dot and return empty tooltip
                styles.append({'display': 'none'})
                children.append([])
        
        # This will now always return a list of 30 items (or however many fields exist)
        return styles, children

    @app.callback(
        Output({'type': 'nc-var-extra-attr-dot', 'index': ALL, 'attr_uid': ALL}, 'style'),
        Output({'type': 'nc-var-extra-attr-dot', 'index': ALL, 'attr_uid': ALL}, 'children'),
        Input({'type': 'nc-var-extra-attr-key', 'index': ALL, 'attr_uid': ALL}, 'value'),
        Input('metadata-diff-store', 'data'),
        State({'type': 'nc-var-extra-attr-key', 'index': ALL, 'attr_uid': ALL}, 'id'),
        State('nc-vars-store', 'data'),
        prevent_initial_call=True
    )
    def update_var_extra_attr_badges(key_values, diff, key_ids, vars_store):
        if not diff:
            raise PreventUpdate

        name_by_uid = {v['uid']: v.get('name') for v in (vars_store or [])}
        added_by_var, changed_by_var, removed_by_var = {}, {}, {}
        for entry in diff.get('variables', {}).get('changed', []):
            name = entry['name']
            fields = entry['fields']
            added_by_var[name] = {
                f['field'].split('.', 1)[1] for f in fields['added'] if f['field'].startswith('extra_attrs.')
            }
            changed_by_var[name] = {
                f['field'].split('.', 1)[1]: f for f in fields['changed'] if f['field'].startswith('extra_attrs.')
            }

        dot_ids = [match['id'] for match in ctx.outputs_list[0]]
        key_by_id = {(k_id['index'], k_id['attr_uid']): (k_val or '').strip() for k_id, k_val in zip(key_ids, key_values)}
        
        styles, children = [], []
        for id_ in dot_ids:
            name = name_by_uid.get(id_['index'])
            key = key_by_id.get((id_['index'], id_['attr_uid']), '')
            added = added_by_var.get(name, {})
            changed = changed_by_var.get(name, {})
            
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
        Output({'type': 'nc-var-err-count', 'index': ALL}, 'children'),
        Output({'type': 'nc-var-err-count', 'index': ALL}, 'style'),
        Output({'type': 'nc-var-warn-count', 'index': ALL}, 'children'),
        Output({'type': 'nc-var-warn-count', 'index': ALL}, 'style'),
        Output({'type': 'nc-var-card', 'index': ALL}, 'className'),
        Input('metadata-problems-store', 'data'),
        Input('metadata-validation-mode', 'data'),
        Input('current-submission', 'data'),
        State('nc-vars-store', 'data'),
        prevent_initial_call=True
    )
    def update_var_card_errors(problems, mode, submission_id, vars_store):
        """
        Severity counts stay visible on collapsed cards whose bodies mount lazily.
        """
        #problems = _visible_problems(problems, mode, submission_id)
        error_counts = error_count_by_variable(problems)
        warning_counts = warning_count_by_variable(problems)
        name_by_uid = {v['uid']: v.get('name') for v in (vars_store or [])}

        error_pill_uids = [m['id']['index'] for m in ctx.outputs_list[0]]
        warning_pill_uids = [m['id']['index'] for m in ctx.outputs_list[2]]
        card_uids = [m['id']['index'] for m in ctx.outputs_list[4]]

        error_children, error_styles = [], []
        for uid in error_pill_uids:
            var_name = name_by_uid.get(uid)
            # Guard lookup: do not let global dataset errors (key: None) apply to cards
            n = error_counts.get(var_name, 0) if var_name else 0
    
            if n:
                error_children.append(f"{n} error" if n == 1 else f"{n} errors")
                error_styles.append(_var_err_pill_style())
            else:
                error_children.append([])
                error_styles.append({'display': 'none'})

        warning_children, warning_styles = [], []
        for uid in warning_pill_uids:
            var_name = name_by_uid.get(uid)
            n = warning_counts.get(var_name, 0) if var_name else 0
            if n:
                warning_children.append(f"{n} warning" if n == 1 else f"{n} warnings")
                warning_styles.append(_var_warn_pill_style())
            else:
                warning_children.append([])
                warning_styles.append({'display': 'none'})

        classes = []
        for uid in card_uids:
            var_name = name_by_uid.get(uid)
            has_error = bool(var_name and error_counts.get(var_name, 0))
            has_warning = bool(var_name and warning_counts.get(var_name, 0))
            if has_error:
                classes.append('nc-var-card has-errors')
            elif has_warning:
                classes.append('nc-var-card has-warnings')
            else:
                classes.append('nc-var-card')
        
        return error_children, error_styles, warning_children, warning_styles, classes

    @app.callback(
        Output({'type': 'nc-var-field-err', 'index': ALL, 'field': ALL}, 'children'),
        Output({'type': 'nc-var-field-wrap', 'index': ALL, 'field': ALL}, 'className'),
        Output({'type': 'nc-var-record-err', 'index': ALL}, 'children'),
        Output({'type': 'nc-var-extra-attr-err', 'index': ALL, 'attr_uid': ALL}, 'children'),
        Output({'type': 'nc-var-extra-attr-value', 'index': ALL, 'attr_uid': ALL}, 'className'),
        Output({'type': 'nc-var-extra-attr-key', 'index': ALL, 'attr_uid': ALL}, 'className'),
        Output({'type': 'nc-var-extra-attr-key-err', 'index': ALL, 'attr_uid': ALL}, 'children'),
        Output({'type': 'nc-var-extra-section-err', 'index': ALL}, 'children'),
        Input('metadata-problems-store', 'data'),
        Input('metadata-validation-mode', 'data'),
        Input('current-submission', 'data'),
        Input({'type': 'nc-var-card-body', 'index': ALL}, 'style'), #  Body style flip on expand/collapse. Without this input, a card opened after the last validation run would mount empty error slots and never fill them
        State('nc-vars-store', 'data'),
        State({'type': 'nc-var-extra-attr-key', 'index': ALL, 'attr_uid': ALL}, 'id'),
        State({'type': 'nc-var-extra-attr-key', 'index': ALL, 'attr_uid': ALL}, 'value'),
        State({'type': 'nc-var-extra-section-err', 'index': ALL}, 'id'),
        prevent_initial_call=True
    )
    def update_var_field_errors(
        problems, mode, submission_id, body_styles, vars_store, extra_attr_key_ids, extra_attr_key_values, extra_section_err_ids
    ):
        #problems = _visible_problems(problems, mode, submission_id)
        by_name = variable_problems_by_name(problems)
        name_by_uid = {v['uid']: v.get('name') for v in (vars_store or [])}

        err_ids = [m['id'] for m in ctx.outputs_list[0]]
        wrap_ids = [m['id']for m in ctx.outputs_list[1]]
        record_ids = [m['id'] for m in ctx.outputs_list[2]]
        extra_attr_err_ids = [m['id'] for m in ctx.outputs_list[3]]
        extra_attr_value_ids = [m['id'] for m in ctx.outputs_list[4]]
        extra_attr_key_out_ids = [m['id'] for m in ctx.outputs_list[5]]
        extra_attr_key_err_ids = [m['id'] for m in ctx.outputs_list[6]]
        extra_attr_keys = {
            (id_['index'], id_['attr_uid']): (value or '').strip()
            for id_, value in zip(extra_attr_key_ids or [], extra_attr_key_values or [])
        }

        def _lookup(id_):
            return by_name.get(name_by_uid.get(id_['index']), {}).get(id_['field'], [])

        children = []
        for id_ in err_ids:
            probs = _lookup(id_)
            children.append([
                _err_text(p['message'], p['severity'], p.get('link_text'), p.get('link_url'))
                for p in probs
                if p['field'] == id_['field']
            ])

        classes = []
        for id_ in wrap_ids:
            probs = _lookup(id_)
            classes.append(f"nc-field-wrap is-{probs[0]['severity']}" if probs else 'nc-field-wrap')

        visible_fields = {
            'standard_name', 'long_name', 'units', 'fill_value', 'coordinates', 'axis',
            'flag_values', 'flag_meanings',
        }
        record_errors = []
        for id_ in record_ids:
            var_name = name_by_uid.get(id_['index'])
            field_problems = by_name.get(var_name, {})
            extra_fields = {
                field for (uid, _), field in extra_attr_keys.items()
                if uid == id_['index']
            }
            record_errors.append([
                _err_text(
                    problem['message'], problem['severity'],
                    problem.get('link_text'), problem.get('link_url')
                )
                for field, probs in field_problems.items()
                if field not in visible_fields
                and field not in extra_fields
                and field not in _EXTRA_SECTION_FIELDS
                for problem in probs
            ])

        extra_section_errors = []
        for id_ in extra_section_err_ids:
            var_name = name_by_uid.get(id_['index'])
            field_problems = by_name.get(var_name, {})
            extra_fields = {
                field for (uid, _), field in extra_attr_keys.items()
                if uid == id_['index']
            }
            extra_section_errors.append([
                _err_text(
                    problem['message'], problem['severity'],
                    problem.get('link_text'), problem.get('link_url')
                )
                for field, probs in field_problems.items()
                if field in _EXTRA_SECTION_FIELDS and field not in extra_fields
                for problem in probs
            ])

        extra_attr_errors = []
        for id_ in extra_attr_err_ids:
            var_name = name_by_uid.get(id_['index'])
            field = extra_attr_keys.get((id_['index'], id_['attr_uid']))
            probs = by_name.get(var_name, {}).get(field, [])
            value_problems = [p for p in probs if p.get('field_part') != 'key']
            extra_attr_errors.append([
                _err_text(
                    problem['message'], problem['severity'],
                    problem.get('link_text'), problem.get('link_url')
                )
                for problem in value_problems
            ])

        extra_attr_value_classes = []
        for id_ in extra_attr_value_ids:
            var_name = name_by_uid.get(id_['index'])
            field = extra_attr_keys.get((id_['index'], id_['attr_uid']))
            probs = by_name.get(var_name, {}).get(field, [])
            value_problems = [p for p in probs if p.get('field_part') != 'key']
            extra_attr_value_classes.append(
                f"nc-var-extra-attr-value is-{value_problems[0]['severity']}"
                if value_problems else 'nc-var-extra-attr-value'
            )

        extra_attr_key_classes = []
        for id_ in extra_attr_key_out_ids:
            var_name = name_by_uid.get(id_['index'])
            field = extra_attr_keys.get((id_['index'], id_['attr_uid']))
            probs = by_name.get(var_name, {}).get(field, [])
            key_problems = [p for p in probs if p.get('field_part') == 'key']
            extra_attr_key_classes.append(
                f"nc-var-extra-attr-key is-{key_problems[0]['severity']}"
                if key_problems else 'nc-var-extra-attr-key'
            )

        extra_attr_key_errors = []
        for id_ in extra_attr_key_err_ids:
            var_name = name_by_uid.get(id_['index'])
            field = extra_attr_keys.get((id_['index'], id_['attr_uid']))
            probs = by_name.get(var_name, {}).get(field, [])
            key_problems = [p for p in probs if p.get('field_part') == 'key']
            extra_attr_key_errors.append([
                _err_text(
                    problem['message'], problem['severity'],
                    problem.get('link_text'), problem.get('link_url')
                )
                for problem in key_problems
            ])

        return children, classes, record_errors, extra_attr_errors, extra_attr_value_classes, extra_attr_key_classes, extra_attr_key_errors, extra_section_errors



    # # Callback to load the variable metadata upon opening
    # @app.callback(
    #     Output('nc-var-cards-container', 'children'),
    #     Output('nc-vars-store', 'data'),
    #     Output('nc-var-count', 'children'),
    #     Output('nc-dimension-strip', 'children'),
    #     Input('metadata-load-signal-store', 'data'),
    #     prevent_initial_call='initial_duplicate'
    #     )
    # def load_and_render_variables_from_db(metadata_store):
    #     log.debug(f"[load_variables_from_db]: attempting to load and render data from db...")

    #     if not metadata_store or metadata_store == {}:
    #         raise PreventUpdate

    #     # 1. Build variable store and container
    #     vars_store = []
    #     metadata_vars = metadata_store.get("variables", [])
    #     for var in metadata_vars:
    #         vars_store.append({
    #             'uid': str(uuid.uuid4()),
    #             'name': var.get('name', ''),
    #             'var_type': var.get('var_type', ''),
    #             'dtype': var.get('dtype', ''),
    #             'dimensions': var.get("dimensions", []),
    #             'attributes': var
    #         })
    #     vars_children = []
    #     for var in vars_store:
    #         vars_children.append(
    #             _imported_var_card(
    #                 uid=var.get('uid'),
    #                 name=var.get('name'),
    #                 var_type=var.get('var_type'),
    #                 dtype=var.get('dtype'),
    #                 dimensions=" ".join(var.get("dimensions")),
    #                 attributes=var.get('attributes')
    #             )
    #         )

    #     # 2. Build dimensions strip
    #     dimensions = metadata_store.get("dimensions", [])
    #     dimensions_children = [_dimension_strip(dimensions)]
        
    #     return (
    #         vars_children, 
    #         vars_store, 
    #         f"{len(vars_store)} variables",
    #         dimensions_children
    #         )


    # ------------------------------------------------------
    # Add manual variable card - removed this functionality
    # ------------------------------------------------------
    # @app.callback(
        # Output('nc-var-cards-container', 'children'),
        # Output('nc-vars-store', 'data'),
        # Output('nc-var-count', 'children'),

    #     Input('btn-add-nc-var', 'n_clicks'),

    #     State('nc-vars-store', 'data'),
    #     State('nc-var-cards-container', 'children'),
    #     State('nc-var-count', 'children'),
    #     prevent_initial_call=True
    # )
    # def add_manual_var_card(n_clicks, store, current_children, current_var_count):
    #     """
    #     Appends a new blank manual variable card to the container
    #     and adds a corresponding entry to the vars store.
    #     """
        # uid = str(uuid.uuid4())

        # updated_store = store + [{
        #     'uid': uid,
        #     'source': 'manual',
        #     'name': '',
        #     'var_type': None,
        #     'dtype': None,
        #     'shape': None,
        #     'dimensions': None,
        #     'attributes': {}
        # }]

        # # Drop the empty state message if it's the only child
        # existing = current_children if isinstance(current_children, list) else []
        # existing = [c for c in existing if c.get('props', {}).get('id') != 'nc-var-empty-msg']

        # updated_children = existing + [_manual_var_card(uid=uid)]

        # # variable count
        # new_count = int(current_var_count.split(' ')[0]) + 1
        # var_word = 'variables' if new_count != 1 else 'variable'
        # updated_var_count = f"{new_count} {var_word}"

        # return updated_children, updated_store, updated_var_count

    # # --------------------------------------
    # # Remove variable card
    # # --------------------------------------
    # @app.callback(
    #     Output('nc-var-cards-container', 'children', allow_duplicate=True),
    #     Output('nc-vars-store', 'data', allow_duplicate=True),
    #     Output('nc-var-count', 'children', allow_duplicate=True),

    #     Input({'type': 'btn-remove-nc-var', 'index': ALL}, 'n_clicks'),

    #     State('nc-vars-store', 'data'),
    #     State('nc-var-cards-container', 'children'),
    #     State('nc-var-count', 'children'),
    #     prevent_initial_call=True
    # )
    # def remove_var_card(remove_clicks, store, current_children, current_var_count):
    #     """
    #     Removes a variabel card from the container and its entry from the store.
    #     """
    #     if not any(n for n in remove_clicks if n):
    #         return no_update, no_update, no_update

    #     uid_to_remove = ctx.triggered_id['index']
    #     updated_store = [entry for entry in store if entry['uid'] != uid_to_remove]

    #     updated_children = [
    #         c for c in current_children
    #         if c.get('props', {}).get('id', {}).get('index') != uid_to_remove
    #     ]

    #     if not updated_children:
    #         updated_children = [
    #             html.P(
    #                 'No variables imported yet.',
    #                 id='nc-var-empty-msg',
    #                 style={
    #                     'color': '#555',
    #                     'fontStyle': 'italic',
    #                     'fontSize': '12px',
    #                     'marginBottom': '8px'
    #                 }
    #             )
    #         ]

    #     # variable count
    #     new_count = int(current_var_count.split(' ')[0]) - 1
    #     var_word = 'variables' if new_count != 1 else 'variable'
    #     updated_var_count = f"{new_count} {var_word}"
        
    #     return updated_children, updated_store, updated_var_count


    

     
            
        

    
    
