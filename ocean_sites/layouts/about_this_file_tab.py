from typing import Optional
from dash import html, dcc
from portal_core.utils import _section_label
from portal_core.constants import (
    _label_style,
    _input_style,
    _dropdown_style,
    _BORDER_COLOR,
)
from ocean_sites.models.ocean_sites import DataMode, DataType, FormatVersion

_DIFF_BADGE_COLORS = {
    'changed': '#f7b44f',
    'added': '#38d9a9',
    'removed': '#f76f6f'
}

_BADGE_TOOLTIP_STYLE = {
    'position': 'absolute', 'bottom': '130%', 'left': '0',
    'backgroundColor': '#181818', 'border': '1px solid #444',
    'borderRadius': '5px', 'padding': '6px 9px', 'fontSize': '10.5px',
    'whiteSpace': 'nowrap', 'display': 'none',
    'boxShadow': '0 6px 16px rgba(0,0,0,.5)', 'zIndex': '999',
    'fontFamily': 'monospace', 'color': '#ccc',
    'fontWeight': '400', 'textTransform': 'none', 'letterSpacing': 'normal'
}

def diff_badge_style(kind: str) -> dict:
    """Visible pill style for a changed/added/removed badge"""
    color = _DIFF_BADGE_COLORS[kind]
    return {
        'display': 'inline-flex', 'alignItems': 'center',
        'position': 'relative', 'cursor': 'help', 'flexShrink': '0',
        'fontSize': '8.5px', 'fontWeight': '700',
        'textTransform': 'uppercase', 'letterSpacing': '.06em',
        'padding': '1px 6px', 'borderRadius': '3px', 'lineHeight': '1.5',
        'color': color,
        'backgroundColor': f'{color}1f',
        'border': f'1px solid {color}40'
    }

def diff_badge_children(kind: str, tooltip_text: str = None) -> list:
    """Badge label text plus (optionally) the hover tooltip nest inside."""
    children = [kind]
    if tooltip_text:
        children.append(
            html.Span(tooltip_text, className='nc-field-tooltip', style=_BADGE_TOOLTIP_STYLE)
        )
    return children
# ---------------------------------------------------------------------------
# Helper: the blue left-border note that explains the netCDF connection.
# Sits at the top of any panel whose fields map directly to global attributes.
# ---------------------------------------------------------------------------
def _global_attr_note(extra_text=None):
    """
    Blue left-border callout explaining that these fields mirror netCDF
    global attributes. Pass extra_text to append a portal-specific sentence.
    """
    base = "These fields map directly to global attributes in your netCDF file."
    if extra_text:
        base=f"{base} {extra_text}"

    return html.Div(
        style={
            'display': 'flex',
            'alignItems': 'flex-start',
            'gap': '9px',
            'backgroundColor': '#1a1a1a',
            'border': f'1px solid $4f8ef7',
            'borderLeft': '3px solid #3faef7',
            'borderRadius': '4px',
            'padding': '9px 12px',
            'marginBottom': '18px',
            'fontSize': '11px',
            'color': '#666',
            'lineHeight': '1.6'
        },
        children=[
            html.Span('ⓘ', style={'color': '#4f8ef7', 'flexShrink': '0', 'marginTop': '1px'}),
            html.Span([
                base + " If anything looks wrong, fix it here - or correct the attribute in the source file and re-upload. ",
            ]),
        ]
    )

# -----------------------------------------------------------------------------------
# Helper: Creates the field label with the changes badge related to metadata-diff-store
# -----------------------------------------------------------------------------------
def _field_label(text: str, field_key: str):
    """
    Label + a hidden-by-default dot that update_field_dots() reveals when 
    metadata-diff-store shows this field as changed. field_key must match
    a key in FIXED_GLOBAL_ATTR_FIELDS (metadata_diff.py) exactly.
    """
    return html.Div(
        style={'display': 'flex', 'alignItems': 'center', 'gap': '6px', 'marginBottom': '4px'},
        children=[
            html.Label(text, style={**_label_style, 'marginBottom': '0'}),
            html.Span(
                id={'type': 'nc-field-dot', 'field': field_key},
                className='nc-field-dot',
                style={'display': 'none'},
                children=[],
            ),
        ]
    )

# -----------------------------------------------------------------------------
# Helper: monospace attribute-key hint that sits between the label and input.
# Mirrors the pattern used in the arctic spatial coverage tab for nc_key hints.
# -----------------------------------------------------------------------------
def _nc_key(key: str):
    return html.Span(
        key,
        style={
            'display': 'block',
            'fontSize': '9px',
            'fontFamily': 'monospace',
            'color': '#444',
            'marginBottom': '4px'
        }
    )

def _field_block(label: str, nc_key: str, field_key: str, component):
    """
    label       - what the user reads, e.g. 'Data mode'
    nc_key      - the netCDF attribute name show in monospace under it
    field_key   - the DB column name; must match the 'field' on a Problem
    component   - the dcc.Input / dcc.Dropdown itself 
    """
    return html.Div(
        id={'type': 'nc-field-wrap', 'field': field_key},
        className='nc-field-wrap',
        children=[
            _field_label(label, field_key),
            _nc_key(nc_key),
            component,
            html.Div(
                id={'type': 'nc-field-err', 'field': field_key},
                className='nc-field-err',
                children=[]
            )
        ]
    )

def _err_text(
    message: str,
    severity: str,
    link_text: Optional[str] = None,
    link_url: Optional[str] = None,
):
    """
    Renders clean, minimal inline validation text below field elements.
    """
    if not message:
        return []
        
    is_error = severity == 'error'
    text_color = '#ff4d4d' if is_error else '#f7b44f'
    message_children = [html.Span(message)]
    if link_text and link_url and link_text in message:
        before, _, after = message.partition(link_text)
        message_children = []
        if before:
            message_children.append(html.Span(before))
        message_children.append(html.A(
            link_text,
            href=link_url,
            target='_blank',
            rel='noopener noreferrer',
        ))
        if after:
            message_children.append(html.Span(after))

    return html.Div(
        style={
            'color': text_color,
            'fontSize': '11px',
            'marginTop': '5px',
            'display': 'flex',
            'alignItems': 'center',
            'gap': '4px',
            'fontWeight': '500'
        },
        children=[
            html.Span("✕" if is_error else "⚠️", style={'fontSize': '10px'}),
            html.Span(message_children)
        ]
    )
# -----------------------------------------------------------------------------
# Helper: build the coverage field (for lat/lon min/max and vertical min/max)
# -----------------------------------------------------------------------------
def _coverage_field(field_id: str, label: str, nc_key: str, placeholder: str, diff_field: str = None):
    return _field_block(
        label=label,
        nc_key=nc_key,
        field_key=diff_field or nc_key,
        component=dcc.Input(
            id=field_id,
            type='text', # Needed to turn to text to preserve decimal places (e.g. 50.00 instead of 50)
            step='any',
            debounce=True,
            style=_input_style,
            placeholder=placeholder,
        ),
    )

# -----------------------------------------------------------------------------
# Helper: add extra attribute row (used in the callbacks)
# -----------------------------------------------------------------------------
def _extra_attr_row_children(uid: str, key: str, value: str, var_uid: str, removed: bool, is_original: bool):
    """
    Builds the inner children of one extra-attr row. Factored out of the _extra_attr_row function so
    the soft-remove/undo callback can regenerate ONLY this row's contents via a MATCH-scoped Output, 
    without ever touching the container's children list (which seems to cause a visual glitch in the UI)
    """
    if var_uid is not None:
        key_id = {'type': 'nc-var-extra-attr-key', 'index': var_uid, 'attr_uid': uid}
        value_id = {'type': 'nc-var-extra-attr-value', 'index': var_uid, 'attr_uid': uid}
        dot = html.Span(
            id={'type': 'nc-var-extra-attr-dot', 'index': var_uid, 'attr_uid': uid},
            className='nc-field-dot', # Pulls in hover action definitions
            style={'display': 'none'},
            children=[]
        )
    else:
        key_id = {'type': 'nc-extra-attr-key', 'index': uid}
        value_id = {'type': 'nc-extra-attr-value', 'index': uid}
        dot = html.Span(
            id={'type': 'nc-extra-attr-dot', 'index': uid},
            className='nc-field-dot', # Matches your working CSS hover class selector
            style={'display': 'none'},
            children=[]
        )
    strike = {'textDecoration': 'line-through', 'color': '#777'} if removed else {}
    if var_uid is not None:
        action_id = {'type': 'btn-action-nc-var-extra-attr', 'index': var_uid, 'attr_uid': uid}
    else:
        action_id = {'type': 'btn-action-nc-extra-attr', 'index': uid}

    # Style configuration variables dynamically
    btn_text = 'Undo' if removed else 'Remove'
    btn_style = {
        'backgroundColor': 'rgba(0, 210, 255, 0.08)' if removed else 'rgba(247,111,111,.08)',
        'border': '1px solid rgba(0, 210, 255, 0.18)' if removed else '1px solid rgba(247,111,111,.18)',
        'borderRadius': '4px',
        'color': '#00d2ff' if removed else '#f76f6f',
        'cursor': 'pointer',
        'fontSize': '11px',
        'padding': '3px 9px',
        'flexShrink': '0'
    }

    # Render a single button container element
    action_button = html.Button(btn_text, id=action_id, n_clicks=0, style=btn_style)
    value_input = dcc.Input(
        id=value_id,
        className='nc-var-extra-attr-value' if var_uid is not None else 'nc-extra-attr-value',
        type='text',
        value=value,
        placeholder='value',
        debounce=True,
        disabled=removed,
        style={
            **_input_style,
            'flex': '2',
            'fontSize': '12px',
            **strike
        },
    )
    if var_uid is not None:
        value_input = html.Div(
            style={'display': 'flex', 'flexDirection': 'column', 'flex': '2', 'minWidth': 0},
            children=[
                value_input,
                html.Div(
                    id={'type': 'nc-var-extra-attr-err', 'index': var_uid, 'attr_uid': uid},
                    className='nc-field-err',
                    children=[],
                ),
            ],
        )
    else:
        value_input = html.Div(
            style={'display': 'flex', 'flexDirection': 'column', 'flex': '2', 'minWidth': 0},
            children=[
                value_input,
                html.Div(
                    id={'type': 'nc-extra-attr-err', 'index': uid},
                    className='nc-field-err',
                    children=[],
                ),
            ],
        )

    key_input = dcc.Input(
        id=key_id,
        className='nc-var-extra-attr-key' if var_uid is not None else 'nc-extra-attr-key',
        type='text',
        value=key,
        placeholder='attribute name',
        debounce=True,
        disabled=removed,
        style={
            **_input_style,
            'flex': '1',
            'fontFamily': 'monospace',
            'fontSize': '12px',
            **strike
        },
    )
    if var_uid is None:
        key_input = html.Div(
            style={'display': 'flex', 'flexDirection': 'column', 'flex': '1', 'minWidth': 0},
            children=[
                key_input,
                html.Div(
                    id={'type': 'nc-extra-attr-key-err', 'index': uid},
                    className='nc-field-err',
                    children=[],
                ),
            ],
        )

    return [
        dot,
        key_input,
        value_input,
        html.Span(
            className='nc-field-dot',
            style=diff_badge_style('removed'),
            children=diff_badge_children('removed', f"was: {value}" if value else None),
        ) if removed else None,
        action_button
    ]
    
def _extra_attr_row(uid: str, key: str = '', value: str = '', var_uid: str=None, removed: bool = False, is_original: bool = False):
    """
    A single key/value row for the extra_global_attrs section.
    Rendered by callback into nc-extra-attrs-container.

    uid - unique string id (e.g. str(uuid.uuid4()))
    key - pre-populated attribute name (from file parse)
    value - pre-populated attribute value (from file parse)
    var_uid - pass teh parent variable's uid when this row lives inside a
                variable card's extra-attributes list. This swaps the 
                component id "type"s to a variable scoped set so the 
                remove button doesn't collide with the global attributes
                remove button.
    removed - true if this row was soft-removed (cross-out, undo shown)
    is_original - true if this key existed in the ORIGINAL parsed file. 
    """
    row_id = (
        {'type': 'nc-var-extra-attr-row', 'index': var_uid, 'attr_uid': uid}
        if var_uid is not None else
        {'type': 'nc-extra-attr-row', 'index': uid}
    )

    return html.Div(
        id=row_id,
        style={
            'backgroundColor': '#1c1c1c' if removed else '#252525',
            'border': '1px solid #333' if removed else f'1px solid {_BORDER_COLOR}',
            'borderRadius': '6px',
            'padding': '8px 12px',
            'display': 'flex',
            'alignItems': 'center',
            'gap': '10px',
            'marginBottom': '6px', 
            'opacity': '0.55' if removed else '1'
        },
        children=_extra_attr_row_children(uid, key, value, var_uid, removed, is_original)
    )
# ---------------------------------------------------------------------------
# Main layout
# ---------------------------------------------------------------------------
def create_tab_content():
    grid = {
        'display': 'grid',
        'gap': '12px',
        'maxWidth': '480px',
        'marginBottom': '14px'
    }

    return html.Div(
        children=[
            html.H4(
                'Global Attributes',
                style={'color': '#e0e0e0', 'marginBottom': '10px'}
            ),
            html.P(
                'Review the global attributes imported from your netCDF file. '
                'Correct any values that look wrong before saving.',
                style={'color': '#999', 'marginBottom': '20px'}
            ),

            _global_attr_note(),

            # ---- Identification and Conventions ------

            _section_label("Identification"),

            html.Div(
                style={**grid, 'gridTemplateColumns': '1fr 1fr'},
                children=[
                    _field_block(
                        'Site code', 'site_code', 'site_code',
                        dcc.Input(id='nc-site-code', type='text', placeholder='e.g. PAPA', style=_input_style, debounce=True),
                    ),
                    _field_block(
                        'Platform code', 'platform_code', 'platform_code',
                        dcc.Input(
                            id='nc-platform-code',
                            type='text',
                            placeholder='e.g. PAPA',
                            style=_input_style,
                            debounce=True),
                        ),
                    ]),

            html.Div(
                style={**grid, 'gridTemplateColumns': '1fr 1fr'},
                children=[
                   _field_block('Data mode', 'data_mode', 'data_mode',
                        dcc.Dropdown(
                            id='nc-data-mode',
                            options=[{'label': m.label, 'value': m.value} for m in DataMode],
                            placeholder='Select...',
                            clearable=False,
                            style=_dropdown_style,
                        ),
                    ),
                    _field_block('Data type', 'data_type', 'data_type',
                        dcc.Dropdown(
                            id='nc-data-type',
                            options=[{'label': t.value, 'value': t.value} for t in DataType],
                            placeholder='Select...',
                            clearable=False,
                            style=_dropdown_style
                        ),
                    ),
                    _field_block('Format version', 'format_version', 'format_version',
                        dcc.Dropdown(
                            id='nc-format-version',
                            options=[{'label': v.value, 'value': v.value} for v in FormatVersion],
                            placeholder='Select...',
                            clearable=False,
                            style=_dropdown_style
                        ),
                    ),
                ]
            ),
            html.Div(
                style={**grid, 'gridTemplateColumns': '1fr 1fr'},
                children=[
                    _field_block('Update interval', 'update_interval', 'update_interval',
                        dcc.Input(
                            id='nc-update-interval',
                            type='text',
                            placeholder="ISO 8601 duration or 'void'",
                            style=_input_style,
                            debounce=True),
                        ),
                    _field_block(
                        'Date created', 'date_created', 'date_created',
                        dcc.Input(
                            id='nc-date-created',
                            type='text',
                            placeholder="e.g. 2016-10-11T19:19:21Z",
                            style={**_input_style, 'fontFamily': 'monospace', 'fontSize': '12px'},
                            debounce=True),
                        ),
                    ]
                ),
            # ---- Spatial Coverage ------

            _section_label("Spatial Coverage"),

            html.Div([
                html.Div(
                    style={**grid, 'gridTemplateColumns': '1fr 1fr', 'marginBottom': '4px'},
                    children=[
                        _coverage_field('nc-geospatial-lat-min', 'Latitude Min', 'geospatial_lat_min', 'in degrees'),
                        _coverage_field('nc-geospatial-lat-max', 'Latitude Max', 'geospatial_lat_max', 'in degrees'),
                        _coverage_field('nc-geospatial-lon-min', 'Longitude Min', 'geospatial_lon_min', 'in degrees'),
                        _coverage_field('nc-geospatial-lon-max', 'Longitude Max', 'geospatial_lon_max', 'in degrees'),
                    ],
                ),

                html.Div(
                    style={**grid, 'gridTemplateColumns': '1fr 1fr', 'marginBottom': '4px'},
                    children=[
                        _coverage_field('nc-geospatial-vertical-min', 'Vertical min', 'geospatial_vertical_min', 'e.g. 200'),
                        _coverage_field('nc-geospatial-vertical-max', 'Vertical max', 'geospatial_vertical_max', 'e.g. 5000'),
                    ]
                ),
            ]),

             # -------------- Temporal Coverage --------------------------

            _section_label("Temporal Coverage"),

            html.Div(
                style={**grid, 'gridTemplateColumns': '1fr 1fr', 'marginBottom': '4px'},
                children=[
                    _field_block('Start date', 'time_coverage_start', 'time_coverage_start',
                        dcc.Input(
                            id='nc-time-coverage-start',
                            type='text',
                            placeholder='e.g. 2006-03-01T00:00:00Z',
                            style=_input_style,
                            debounce=True),
                        ),
                    _field_block('End date', 'time_coverage_end', 'time_coverage_end',
                        dcc.Input(
                            id='nc-time-coverage-end',
                            type='text',
                            placeholder='e.g. 2006-03-05T23:59:29Z”',
                            style=_input_style,
                            debounce=True),
                        ),
                    ]
                ),

            # -------------- Additional global attributes --------------------------
            # Key/value pairs for anything in the extra_global attrs JSONB field
            # Same pattern as the arctic portal's "additional attributes" section
            
            _section_label('Additional global attributes'),

            html.P(
                'Any other global attributes found in the file that are not covered above.',
                style={'color': '#555', 'fontSize': '12px', 'marginBottom': '10px'}
            ),

            html.Div(
                id='nc-extra-attrs-container',
                children=[
                    html.P(
                        'No additional attributes found.',
                        id='nc-extra-attrs-empty-msg',
                        style={
                            'color': '#555',
                            'fontStyle': 'italic',
                            'fontSize': '12px',
                            'marginBottom': '8px',
                        }
                    ),
                ]
            ),

            # Add row button - same style as arctic portal action buttons
            html.Button(
                '+ add attribute',
                id='btn-add-nc-extra-attr',
                n_clicks=0,
                style={
                            'backgroundColor': 'rgba(79,142,247,.05)',
                            'border': f'1px dashed {_BORDER_COLOR}',
                            'borderRadius': '4px',
                            'color': '#4f8ef7',
                            'cursor': 'pointer',
                            'fontSize': '11px',
                            'fontWeight': '600',
                            'padding': '5px 12px',
                            'fontFamily': 'inherit',
                            'marginTop': '8px',
                            'transition': 'all 0.2s ease', # Smooth hover state transitions
                        }
            ),

            # Store: list of {key: value} dicts - source of truth for extra attrs
            dcc.Store(id='nc-extra-attrs-store', data=[])
        ]
    )