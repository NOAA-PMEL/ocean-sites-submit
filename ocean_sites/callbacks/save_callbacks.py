import os
import copy
from sqlalchemy import select
from dash import Input, Output, State, html, no_update, ALL, ctx
from dash.exceptions import PreventUpdate
from sqlalchemy.orm import Session
from ocean_sites.utils import (
    build_metadata_diff, 
    summarize_diff_counts,
    validate_metadata,
    OceanSitesNetcdfParser,
    load_ocean_sites_metadata, 
    save_all_ocean_sites_metadata, 
    load_original_ocean_sites_metadata,
    build_global_attributes_dict,
    build_variables_list,
    GLOBAL, 
    VARIABLE
    )
from ocean_sites.layouts import(
    build_ledger_children,
)
from ocean_sites.utils.utils import _sanitize_for_json
from portal_core.constants import (
    _OVERLAY_HIDDEN, 
    _PANEL_CLOSED, 
    UPLOAD_DIRECTORY,
    postgres_engine,
)
from ocean_sites.models.ocean_sites import NetcdfGlobalAttributes
from portal_core.utils import (
    get_oads_metadata_id,
    get_data_files,
    app_logger as log
)



# Which tab owns which problem scope (must match the ids of the tabs given in instance_config.yaml)
_SCOPE_BY_TAB = {
    'about_this_file': GLOBAL,
    'data_variables': VARIABLE,
}

_COUNT_STYLE = {
    'fontSize': '9.5px', 
    'fontWeight': '700',
    'padding': '1px 6px',
    'borderRadius': '8px',
    'lineHeight': '1.5',
    'color': '#ff4d4d',                         # Vibrant high-contrast error crimson
    'backgroundColor': '#2a1215',               # Dark crimson solid background container
    'border': '1px solid rgba(255, 77, 77, 0.4)' # Translucent red accent border matching the cards
}

_WARNING_COUNT_STYLE = {
    **_COUNT_STYLE,
    'color': '#f7b44f',
    'backgroundColor': 'rgba(247, 180, 79, .1)',
    'border': '1px solid rgba(247, 180, 79, .45)',
}

def register_callbacks(app):
    
    @app.callback(
        Output('parsed-data-store', 'data'),
        Output('edited-parsed-data-store', 'data'),
        Output('data-parse-status', 'children'),
        Output('original-parsed-data-store', 'data'), # stores the original parsed data to compare to edited version
        Output('metadata-load-signal-store', 'data'),
        Input('newly-uploaded-data-file', 'data'),
        State('current-submission', 'data'),
        prevent_initial_call=True
    )
    def parse_uploaded_netcdf(newly_uploaded_signal, state_current_submission):
        """
        Callback to run the OceanSitesNetcdfParser on the .nc file after its uploaded 
        and store that metadata_dict into the parsed-data-store and edited-parsed-data-store.
        Both stores copy the same data since this is the initial seeding.
        """
        # Guard against initialization or an empty workspace state
        if not state_current_submission:
            raise PreventUpdate
            
        # Query the underlying DB to see what the generic upload callback just inserted
        df_data = get_data_files(state_current_submission, "data")
        
        # If no file exists yet, or it's a structural reset, clear out status silently
        if df_data.empty:
            return {}, {}, "", {}, {}
            
        # Get the latest uploaded row (optimized for single-file portal constraint)
        latest_file = df_data.iloc[-1]
        filename = latest_file['filename']
        
        # Guard file suffix validation safely
        if not str(filename).lower().endswith('.nc'):
            return no_update, no_update, html.Div(
                f"'{filename}' isn't a netCDF file - metadata fields won't auto-populate.",
                style={'color': '#f7b44f', 'fontSize': '12px', 'marginTop': '8px', 'fontWeight': 'bold'}
            ), no_update, no_update
            
        # Resolve the absolute system path where the generic upload callback dropped the file
        file_path = os.path.join(UPLOAD_DIRECTORY, str(state_current_submission), filename)
        # Verify file availability before passing to reader
        if not os.path.exists(file_path):
            raise PreventUpdate

        # oads_metadata_id = submission_db.get_or_create_oads_metadata_id(session=Session(constants.postgres_engine), submission_id=state_current_submission)
            
        try:
            # Instantiate parser directly from disk location—no base64 string splitting needed!
            parser = OceanSitesNetcdfParser(file_path)
            metadata_dict = _sanitize_for_json(copy.deepcopy(parser.metadata_dict))
            # Convert variable dimensions from lists to space-separated strings
            # to line up with the validator expectations from day one.
            for var in metadata_dict.get('variables', []):
                dims = var.get('dimensions')
                if isinstance(dims, (list, tuple)):
                    var['dimensions'] = " ".join(dims)

                if isinstance(var.get('extra_attrs'), dict) and 'long_name' in var['extra_attrs']:
                    var['long_name'] = var['extra_attrs'].pop('long_name')
        
        except Exception as e:
            return no_update, no_update, html.Div(
                f"Couldn't read metadata from {filename}: {str(e)}. "
                f"You can still fill in metadata manually.",
                style={'color': '#f76f6f', 'fontSize': '12px', 'marginTop': '8px'}
            ), no_update, no_update
        
        # Parsing succeeded- persis it. so DB failure is report distincly from parsing failure
        try:
            with Session(postgres_engine) as session:
                # Save the normalized metadata as the original snapshot.
                issues = save_all_ocean_sites_metadata(
                    session=session,
                    submission_id=state_current_submission,
                    netcdf_metadata_dict=metadata_dict,
                    is_new_upload=True
                )
        except Exception as e:
            # Parsed fine, but saving to the DB blew up (session already rolled back)
            return no_update, no_update, html.Div(
                f"Parsed {filename}, but saving the metadata failed: {str(e)}. "
                f"Nothing was saved -- please try re-uploading. "
                f"Please review metadata in step 3.",
                style={'color': '#f76f6f', 'fontSize': '12px', 'marginTop': '8px', 'fontWeight': 'bold'}
            ), no_update, no_update
        
        metadata_dict['_submission_id'] = state_current_submission
        load_signal = {**metadata_dict, '_original_dict': metadata_dict}
        
        return metadata_dict, metadata_dict, html.Div(
                f"✅ Successfully parsed metadata from {filename} "
                f"({len(metadata_dict.get('variables', []))} variables, {len(metadata_dict.get('dimensions', []))} dimensions) "
                f"Please review metadata in step 3.",
                style={'color': '#38d9a9', 'fontSize': '12px', 'marginTop': '8px', 'fontWeight': 'bold'},
            ), metadata_dict, load_signal

    @app.callback(
        Output('edited-parsed-data-store', 'data', allow_duplicate=True),
        Output('original-parsed-data-store', 'data', allow_duplicate=True), # for comparing edits
        Output('metadata-load-signal-store', 'data', allow_duplicate=True), 
        Output('data-parse-status', 'children', allow_duplicate=True),   # <-- Clear file parse alerts
        Output('metadata-save-status', 'children', allow_duplicate=True),   # <-- Clear draft save alerts
        Input('current-submission', 'data'),
        Input('open-metadata-store', 'data'),
        prevent_initial_call=True
    )
    def load_existing_ocean_sites_metadata(state_current_submission, open_metadata_store):
        """
        Fires when the workspace loads/switches to a submission. If metadata was
        already saved for this submission (from a prior upload + edit session),
        hydrate edited-parsed-data-store with the CURRENT (possibly edited) DB
        state, so the UI fields auto-populate from where the user left off.

        If nothing's been saved yet (brand new submission, no upload yet), this
        does nothinga nd leaves the store for parse_uploaded_netcdf to populate 
        once a file is actually parsed.
        """
        log.info("Attempting to load ocean sites metadata from DB....")
        if not state_current_submission:
            raise PreventUpdate

        should_open = (
            'open-metadata-store.data' in ctx.triggered_prop_ids
            and open_metadata_store is not None
        )
        
        with Session(postgres_engine) as session:
            metadata_dict = load_ocean_sites_metadata(
                session=session,
                submission_id=state_current_submission
            )
            original_dict = load_original_ocean_sites_metadata(
                session=session,
                submission_id=state_current_submission
            )

        if metadata_dict is None:
            load_signal = {'_open_modal': True} if should_open else None
            return {}, {}, load_signal, "", ""

        metadata_dict['_submission_id'] = state_current_submission
        if original_dict is not None:
            original_dict['_submission_id'] = state_current_submission

        load_signal = {**metadata_dict, 
                      '_open_modal': should_open,
                      '_original_dict': original_dict,
                      }

        log.info("The metadata was successfully loaded from the DB and added to the edited-parsed-data-store")
        return metadata_dict, original_dict, load_signal, "", ""

    @app.callback(
        Output('notifications', 'children', allow_duplicate=True),
        Output('metadata-save-status', 'children'),
        Output('metadata-modal-overlay', 'style', allow_duplicate=True),
        Output('wizard-panel', 'style', allow_duplicate=True),
        Output('wizard-overlay', 'style', allow_duplicate=True),
        Output('metadata-problems-store', 'data', allow_duplicate=True),
        Output('metadata-validation-mode', 'data', allow_duplicate=True),
        Input('btn-save-metadata', 'n_clicks'),
        State('current-submission', 'data'),
        State('nc-site-code', 'value'), # GLOBAL ATTRIBUTES START
        State('nc-platform-code', 'value'),
        State('nc-data-mode', 'value'),
        State('nc-data-type', 'value'),
        State('nc-format-version', 'value'),
        State('nc-update-interval', 'value'),
        State('nc-date-created', 'value'),
        State('nc-geospatial-lat-min', 'value'),
        State('nc-geospatial-lat-max', 'value'),
        State('nc-geospatial-lon-min', 'value'),
        State('nc-geospatial-lon-max', 'value'),
        State('nc-geospatial-vertical-min', 'value'),
        State('nc-geospatial-vertical-max', 'value'),
        State('nc-time-coverage-start', 'value'),
        State('nc-time-coverage-end', 'value'),
        State({'type': 'nc-extra-attr-key', 'index': ALL}, 'id'),
        State({'type': 'nc-extra-attr-key', 'index': ALL}, 'value'),
        State({'type': 'nc-extra-attr-value', 'index': ALL}, 'id'),
        State({'type': 'nc-extra-attr-value', 'index': ALL}, 'value'), #GLOBAL ATTRIBUTES END
        State('edited-parsed-data-store', 'data'), # <- for dimensions (not user editable)
        State('nc-vars-store', 'data'),
        State({'type': 'nc-var-standard-name', 'index': ALL}, 'id'), # VARIABLES START. Can map each id back to the variable
        State({'type': 'nc-var-standard-name', 'index': ALL}, 'value'),
        State({'type': 'nc-var-long-name', 'index': ALL}, 'id'),   
        State({'type': 'nc-var-long-name', 'index': ALL}, 'value'),
        State({'type': 'nc-var-units', 'index': ALL}, 'id'),
        State({'type': 'nc-var-units', 'index': ALL}, 'value'),
        State({'type': 'nc-var-fill-value', 'index': ALL}, 'id'),
        State({'type': 'nc-var-fill-value', 'index': ALL}, 'value'),
        State({'type': 'nc-var-coordinates', 'index': ALL}, 'id'),
        State({'type': 'nc-var-coordinates', 'index': ALL}, 'value'),
        State({'type': 'nc-var-axis', 'index': ALL}, 'id'),
        State({'type': 'nc-var-axis', 'index': ALL}, 'value'),
        State({'type': 'nc-var-flag-values', 'index': ALL}, 'id'),
        State({'type': 'nc-var-flag-values', 'index': ALL}, 'value'),
        State({'type': 'nc-var-flag-meanings', 'index': ALL}, 'id'),
        State({'type': 'nc-var-flag-meanings', 'index': ALL}, 'value'),
        State({'type': 'nc-var-extra-store', 'index': ALL}, 'id'),
        State({'type': 'nc-var-extra-store', 'index': ALL}, 'data'), 
        State({'type': 'nc-var-extra-attr-key', 'index': ALL, 'attr_uid': ALL}, 'id'),
        State({'type': 'nc-var-extra-attr-key', 'index': ALL, 'attr_uid': ALL}, 'value'),
        State({'type': 'nc-var-extra-attr-value', 'index': ALL, 'attr_uid': ALL}, 'id'),
        State({'type': 'nc-var-extra-attr-value', 'index': ALL, 'attr_uid': ALL}, 'value'), # VARIABLES END
        State('nc-extra-attrs-store', 'data'),
        State({'type': 'nc-var-extra-store', 'index': ALL}, 'id'),
        State({'type': 'nc-var-extra-store', 'index': ALL}, 'data'),
        State('original-parsed-data-store', 'data'),
        prevent_initial_call=True
    )
    def save_metadata(
        n_clicks, submission_id,
        site_code, platform_code, data_mode,
        data_type, format_version, update_interval,
        date_created, lat_min, lat_max, lon_min, lon_max,
        vertical_min, vertical_max, time_start, time_end,
        extra_attr_key_ids, extra_attr_key_values,
        extra_attr_value_ids, extra_attr_value_values, edited_parsed_data,
        vars_store, standard_name_ids, standard_name_values,
        long_name_ids, long_name_values,
        units_ids, units_values, fill_value_ids, fill_value_values,
        coordinate_ids, coordinate_values, axis_ids, axis_values,
        flag_values_ids, flag_values_values, 
        flag_meanings_ids, flag_meanings_values, 
        extra_var_store_ids, extra_var_store_values,
        var_extra_attr_key_ids, var_extra_attr_key_values,
        var_extra_attr_value_ids, var_extra_attr_value_values,
        extra_attrs_store, var_extra_store_ids2, var_extra_store_data2,
        original_parsed_data
        ):

        if not submission_id:
            raise PreventUpdate

        var_extra_stores = [
            {'id': i, 'data': d} for i, d in zip(var_extra_store_ids2, var_extra_store_data2)
        ]

        global_attributes = build_global_attributes_dict(
            site_code=site_code, platform_code=platform_code, data_mode=data_mode,
            data_type=data_type, format_version=format_version,
            update_interval=update_interval, date_created=date_created,
            lat_min=lat_min, lat_max=lat_max, lon_min=lon_min, lon_max=lon_max,
            vertical_min=vertical_min, vertical_max=vertical_max, 
            time_start=time_start, time_end=time_end,
            extra_attr_key_ids=extra_attr_key_ids, extra_attr_key_values=extra_attr_key_values,
            extra_attr_value_ids=extra_attr_value_ids, extra_attr_value_values=extra_attr_value_values,
            extra_attrs_store=extra_attrs_store
        )

        variables = build_variables_list(
            vars_store=vars_store, standard_name_ids=standard_name_ids, standard_name_values=standard_name_values, 
            long_name_ids=long_name_ids, long_name_values=long_name_values,
            units_ids=units_ids, units_values=units_values, fill_value_ids=fill_value_ids, fill_value_values=fill_value_values, 
            coordinate_ids=coordinate_ids, coordinate_values=coordinate_values, axis_ids=axis_ids, axis_values=axis_values,
            flag_values_ids=flag_values_ids, flag_values_values=flag_values_values,
            flag_meanings_ids=flag_meanings_ids, flag_meanings_values=flag_meanings_values,
            var_extra_attr_key_ids=var_extra_attr_key_ids, var_extra_attr_key_values=var_extra_attr_key_values,
            var_extra_attr_value_ids=var_extra_attr_value_ids, var_extra_attr_value_values=var_extra_attr_value_values,
            var_extra_stores=var_extra_stores
        )

        # --- 3. Dimensions arent' user-editable, so carry forward what was loaded
        dimensions = (edited_parsed_data or {}).get("dimensions", [])

        netcdf_metadata_dict = {
            "global_attributes": global_attributes,
            "dimensions": dimensions,
            "variables": variables
        }

        try:
            with Session(postgres_engine) as session:
                issues = save_all_ocean_sites_metadata(
                    session=session,
                    submission_id=submission_id, 
                    netcdf_metadata_dict=netcdf_metadata_dict,
                    is_new_upload=False
                )

        except Exception as e:
            log.exception("Failed to save ocean sites metadata")
            return no_update, (html.Div(
                f"Failed to save metadata: {e}",
                style={'color': '#f76f6f', 'fontSize': '12px'}
            ),
            no_update, no_update, no_update, no_update, no_update
        )

        problems = validate_metadata(netcdf_metadata_dict, original_parsed_data)
        errors = [p for p in problems if p['severity'] == 'error']

        validation_mode = {'loud_for': submission_id}

        if errors:
            return no_update, html.Div(
                f"Saved as draft. {len(errors)} thing{'s' if len(errors) != 1 else ''} "
                f"still need fixing before this can be submitted.",
                style={
                'backgroundColor': 'rgba(247,180,79,0.1)',
                'border': '1px solid rgba(247,180,79,0.3)',
                'borderRadius': '4px',
                'color': '#f7b44f',
                'padding': '10px 14px',
                'fontSize': '13px',
                'marginBottom': '10px'
            },
            ), no_update, no_update, no_update, problems, validation_mode

        return html.Div(
            '✅ Metadata successfully saved.',
            style={
                'backgroundColor': 'rgba(56, 217, 169, 0.1)',
                'border': '1px solid rgba(56, 217, 169, 0.3)',
                'borderRadius': '4px',
                'color': '#38d9a9',
                'padding': '10px 14px',
                'fontSize': '13px',
                'marginBottom': '10px'
            },
        ), [], {'display': 'none'}, _PANEL_CLOSED, _OVERLAY_HIDDEN, [], validation_mode

    @app.callback(
        Output('edited-parsed-data-store', 'data', allow_duplicate=True),

        Input('nc-site-code', 'value'), # GLOBAL ATTRIBUTES START
        Input('nc-platform-code', 'value'),
        Input('nc-data-mode', 'value'),
        Input('nc-data-type', 'value'),
        Input('nc-format-version', 'value'),
        Input('nc-update-interval', 'value'),
        Input('nc-date-created', 'value'),
        Input('nc-geospatial-lat-min', 'value'),
        Input('nc-geospatial-lat-max', 'value'),
        Input('nc-geospatial-lon-min', 'value'),
        Input('nc-geospatial-lon-max', 'value'),
        Input('nc-geospatial-vertical-min', 'value'),
        Input('nc-geospatial-vertical-max', 'value'),
        Input('nc-time-coverage-start', 'value'),
        Input('nc-time-coverage-end', 'value'),
        Input({'type': 'nc-extra-attr-key', 'index': ALL}, 'value'),
        Input({'type': 'nc-extra-attr-value', 'index': ALL}, 'value'),

        Input('nc-vars-store', 'data'),
        Input({'type': 'nc-var-standard-name', 'index': ALL}, 'value'),
        Input({'type': 'nc-var-long-name', 'index': ALL}, 'value'),
        Input({'type': 'nc-var-units', 'index': ALL}, 'value'),
        Input({'type': 'nc-var-fill-value', 'index': ALL}, 'value'),
        Input({'type': 'nc-var-coordinates', 'index': ALL}, 'value'),
        Input({'type': 'nc-var-axis', 'index': ALL}, 'value'),
        Input({'type': 'nc-var-flag-values', 'index': ALL}, 'value'),
        Input({'type': 'nc-var-flag-meanings', 'index': ALL}, 'value'),
        Input({'type': 'nc-var-extra-attr-key', 'index': ALL, 'attr_uid': ALL}, 'value'),
        Input({'type': 'nc-var-extra-attr-value', 'index': ALL, 'attr_uid': ALL}, 'value'), 

        Input('nc-extra-attrs-store', 'data'),
        Input({'type': 'nc-var-extra-store', 'index': ALL}, 'data'),

        State({'type': 'nc-extra-attr-key', 'index': ALL}, 'id'),
        State({'type': 'nc-extra-attr-value', 'index': ALL}, 'id'),
        State({'type': 'nc-var-standard-name', 'index': ALL}, 'id'),
        State({'type': 'nc-var-long-name', 'index': ALL}, 'id'),
        State({'type': 'nc-var-units', 'index': ALL}, 'id'),
        State({'type': 'nc-var-fill-value', 'index': ALL}, 'id'),
        State({'type': 'nc-var-coordinates', 'index': ALL}, 'id'),
        State({'type': 'nc-var-axis', 'index': ALL}, 'id'),
        State({'type': 'nc-var-flag-values', 'index': ALL}, 'id'),
        State({'type': 'nc-var-flag-meanings', 'index': ALL}, 'id'),
        State({'type': 'nc-var-extra-attr-key', 'index': ALL, 'attr_uid': ALL}, 'id'),
        State({'type': 'nc-var-extra-attr-value', 'index': ALL, 'attr_uid': ALL}, 'id'),

        State('edited-parsed-data-store', 'data'),
        State({'type': 'nc-var-extra-store', 'index': ALL}, 'id'),
        State('current-submission', 'data'),
        prevent_initial_call=True
    )
    def sync_edited_store(
        site_code, platform_code, data_mode, data_type, format_version,
        update_interval, date_created, lat_min, lat_max, lon_min, lon_max,
        vertical_min, vertical_max, time_start, time_end, 
        extra_attr_key_values, extra_attr_value_values,
        vars_store, standard_name_values, long_name_values, units_values, fill_values_values,
        coordinate_values, axis_values, flag_values_values, flag_meanings_values,
        var_extra_attr_key_values, var_extra_attr_value_values,
        extra_attrs_store,
        var_extra_store_data2,
        extra_attr_key_ids, extra_attr_value_ids,
        standard_name_ids, long_name_ids, units_ids, fill_value_ids, coordinate_ids, axis_ids,
        flag_values_ids, flag_meanings_ids,
        var_extra_attr_key_ids, var_extra_attr_value_ids, current_edited_data,
        var_extra_store_ids2, submission_id
    ):
        """
        Mirrors live filed values into edited-parsed-data-store on every debounced
        edit, so metadata-diff-store (and therefore the changes bar) refelcts typing
        immediate instead of only after a Save + reload round trip.
        """
        if current_edited_data is None:
            raise PreventUpdate

        # Guard against premature sync calls during form initialization/loading
        if (
            not vars_store
            or ctx.triggered_id == 'nc-vars-store'
            or current_edited_data.get('_submission_id') != submission_id
        ):
            raise PreventUpdate

        var_extra_stores = [
            {'id': i, 'data': d} for i, d in zip(var_extra_store_ids2, var_extra_store_data2)
        ]

        global_attributes = build_global_attributes_dict(
            site_code=site_code, platform_code=platform_code, data_mode=data_mode,
            data_type=data_type, format_version=format_version,
            update_interval=update_interval, date_created=date_created,
            lat_min=lat_min, lat_max=lat_max, lon_min=lon_min, lon_max=lon_max,
            vertical_min=vertical_min, vertical_max=vertical_max, 
            time_start=time_start, time_end=time_end,
            extra_attr_key_ids=extra_attr_key_ids, extra_attr_key_values=extra_attr_key_values,
            extra_attr_value_ids=extra_attr_value_ids, extra_attr_value_values=extra_attr_value_values,
            extra_attrs_store=extra_attrs_store
        )

        variables = build_variables_list(
            vars_store=vars_store, standard_name_ids=standard_name_ids, standard_name_values=standard_name_values, 
            long_name_ids=long_name_ids, long_name_values=long_name_values,
            units_ids=units_ids, units_values=units_values, fill_value_ids=fill_value_ids, fill_value_values=fill_values_values, 
            coordinate_ids=coordinate_ids, coordinate_values=coordinate_values, axis_ids=axis_ids, axis_values=axis_values,
            flag_values_ids=flag_values_ids, flag_values_values=flag_values_values,
            flag_meanings_ids=flag_meanings_ids, flag_meanings_values=flag_meanings_values,
            var_extra_attr_key_ids=var_extra_attr_key_ids, var_extra_attr_key_values=var_extra_attr_key_values,
            var_extra_attr_value_ids=var_extra_attr_value_ids, var_extra_attr_value_values=var_extra_attr_value_values,
            var_extra_stores=var_extra_stores
        )

        return {
            **current_edited_data,
            "global_attributes": global_attributes,
            "variables": variables
        }
        
    @app.callback(
    Output('metadata-diff-store', 'data'),
    Input('original-parsed-data-store', 'data'),
    Input('edited-parsed-data-store', 'data'),
    prevent_initial_call=True
    ) 
    def compute_metadata_diff(original, current):
        """
        Recomputes the full diff (global attrs + variables + enum issues) any time
        either the baseline (original-parsed-data-store) or the live/edited state
        (edited-parsed-data-store) changes -- i.e. on upload, on reload, and on
        every field edit once those inputs feed this store live
        """
        if not original or not current:
            return None  # clears stale badges immediately while loading
        if original.get("_submission_id") != current.get("_submission_id"):
            raise PreventUpdate

        return build_metadata_diff(original, current)

    @app.callback(
    Output('metadata-problems-store', 'data'),
    Input('original-parsed-data-store', 'data'),
    Input('edited-parsed-data-store', 'data'),
    prevent_initial_call=True
    ) 
    def compute_metadata_problems(original, current):
        if not current:
            return []
        return validate_metadata(current, original)

    @app.callback(
    Output('metadata-validation-mode', 'data'),
    Input('btn-save-metadata', 'n_clicks'),
    Input('metadata-load-signal-store', 'data'),
    Input('newly-uploaded-data-file', 'data'), # <-- Upgraded to an Input
    State('current-submission', 'data'),
    State('metadata-validation-mode', 'data'), # <-- Added State to track active session flags
    prevent_initial_call=True
)
    def set_validation_mode(save_clicks, load_signal, newly_uploaded, submission_id, current_mode):
        """
        This callback is a bit complicated and its because we wanted the metadata-validation-mode to
        be quiet after initial parsing, but then turn load after saving. We wanted the loudness to persist 
        after that even if a user switches off the submission or refreshes the UI
        """
        # Initialize the dict if it's empty (e.g., on first page load)
        current_mode = current_mode or {}
        trigger = ctx.triggered_id

        if not submission_id:
            return current_mode

        # 1. A file was JUST uploaded into this workspace
        if trigger == 'newly-uploaded-data-file':
            # Flag this submission as actively "quiet" for this session
            current_mode['quiet_for'] = submission_id
            if current_mode.get('loud_for') == submission_id:
                current_mode['loud_for'] = None
            return current_mode

        # 2. The user explicitly clicked Save
        if trigger == 'btn-save-metadata':
            # Flag this submission as loud, and remove the quiet flag
            current_mode['loud_for'] = submission_id
            if current_mode.get('quiet_for') == submission_id:
                current_mode['quiet_for'] = None
            return current_mode

        # 3. Data finished rendering (Page refresh, tab switch, or post-upload parse)
        if trigger == 'metadata-load-signal-store':
            if not load_signal or load_signal == {}:
                return current_mode
                
            # If we explicitly marked this submission as quiet due to a fresh upload 
            # in this active browser session, respect the quiet flag!
            if current_mode.get('quiet_for') == submission_id:
                return current_mode

            # Otherwise (page refresh, or tab switch AFTER clicking save), check the DB.
            # If a row exists, they've been here before -> Go Loud.
            with Session(postgres_engine) as session:
                oads_id = get_oads_metadata_id(session, submission_id)
                if oads_id is not None:
                    has_saved = session.execute(
                        select(NetcdfGlobalAttributes.id)
                        .where(NetcdfGlobalAttributes.oads_metadata_id == oads_id)
                    ).scalar_one_or_none()
                    
                    if has_saved is not None:
                        current_mode['loud_for'] = submission_id
                        return current_mode

        return current_mode

    @app.callback(
        Output('metadata-changes-title', 'children'),
        Output('metadata-changes-count-changed', 'children'),
        Output('metadata-changes-count-added', 'children'),
        Output('metadata-changes-count-removed', 'children'),
        Input('metadata-diff-store', 'data'),
        prevent_initial_call=True
    )
    def update_changes_bar(diff):
        if not diff:
            raise PreventUpdate

        counts = summarize_diff_counts(diff)
        total = counts['changed'] + counts['added'] + counts['removed']
        title = f"{total} change{'s' if total != 1 else ''} since import" if total else "No changes since import"

        return title, str(counts['changed']), str(counts['added']), str(counts['removed'])

    @app.callback(
        Output('metadata-changes-ledger', 'style'),
        Output('metadata-changes-chevron', 'style'),
        Input('metadata-changes-bar', 'n_clicks'),
        State('metadata-changes-ledger', 'style'),
        prevent_initial_call=True
    )
    def toggle_changes_ledger(n_clicks, current_style):
        is_open = current_style.get('display') != 'none'
        new_display = 'none' if is_open else 'block'
        return (
            {**current_style, 'display': new_display},
            {'color': '#666', 'fontSize': '10px', 'transform': 'none' if is_open else 'rotate(90deg)'},
        )

    @app.callback(
        Output('metadata-changes-ledger', 'children'),
        Input('metadata-diff-store', 'data'),
        prevent_initial_call=True
    )
    def update_changes_ledger(diff):
        if not diff:
            raise PreventUpdate
        return build_ledger_children(diff)

    @app.callback(
        Output({'type': 'sidebar-tab-count', 'index': ALL}, 'children'),
        Output({'type': 'sidebar-tab-count', 'index': ALL}, 'style'),
        Output({'type': 'sidebar-tab-warning-count', 'index': ALL}, 'children'),
        Output({'type': 'sidebar-tab-warning-count', 'index': ALL}, 'style'),
        Input('metadata-problems-store', 'data'),
        Input('metadata-validation-mode', 'data'),
        Input('current-submission', 'data'),
        prevent_initial_call=True
    )
    def update_sidebar_counts(problems, mode, submission_id):
        loud = bool(submission_id) and (mode or {}).get('loud_for') == submission_id
        all_problems = problems or []
        counted_errors = all_problems
        #counted_errors = (
        #    all_problems
        #    if loud
        #    else [p for p in all_problems if p['kind'] == 'missing']
        #)

        error_counts = {}
        for p in counted_errors:
            if p['severity'] != 'error':
                continue
            error_counts[p['scope']] = error_counts.get(p['scope'], 0) + 1

        warning_counts = {}
        for p in all_problems:
            if p['severity'] != 'warning':
                continue
            warning_counts[p['scope']] = warning_counts.get(p['scope'], 0) + 1

        error_children, error_styles = [], []
        for match in ctx.outputs_list[0]:
            n = error_counts.get(_SCOPE_BY_TAB.get(match['id']['index']), 0)
            error_children.append(str(n) if n else [])
            error_styles.append(_COUNT_STYLE if n else {'display': 'none'})

        warning_children, warning_styles = [], []
        for match in ctx.outputs_list[2]:
            n = warning_counts.get(_SCOPE_BY_TAB.get(match['id']['index']), 0)
            warning_children.append(str(n) if n else [])
            warning_styles.append(_WARNING_COUNT_STYLE if n else {'display': 'none'})

        return error_children, error_styles, warning_children, warning_styles