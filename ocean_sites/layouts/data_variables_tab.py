import uuid
from dash import html, dcc
from portal_core.constants import (
    _label_style,
    _input_style,
    _BORDER_COLOR,
    _TEXT_COLOR
)
from portal_core.utils import _section_label
from ocean_sites.utils import _format_value_for_ui
from ocean_sites.layouts import _extra_attr_row

# ----------------------------------------------------------
# Dimension pill strip
# ----------------------------------------------------------
def _dimension_pill(name: str, size: int, unlimited: bool = False):
    """
    A single dimension pill showing name = size with optional unlimited tag.
    """
    return html.Div(
        style={
            'display': 'inline-flex',
            'alignItems': 'baseline',
            'gap': '0',
            'backgroundColor': '#1e1e1e',
            'border': f'1px solid {_BORDER_COLOR}',
            'borderRadius': '3px',
            'padding': '3px 8px',
            'fontFamily': 'monospace',
            'fontSize': '11px',
            'whiteSpace': 'nowrap'
        },
        children=[
            html.Span(name, style={'color': '#4f8ef7'}),
            html.Span('=', style={'color': '#333', 'margin': '0 3px'}),
            html.Span(
                str(size),
                style={'color': '#38d9a9' if unlimited else '#888'}
            ),
            # Unlimited tag - only rendered when unlimited=True
            html.Span(
                'unlimited',
                style={
                    'fontSize': '9px',
                    'fontWeight': '600',
                    'backgroundColor': 'rgba(56,217,169,.1)',
                    'color': '#38d9a9',
                    'border': '1px solid rgba(56,217,169,.2)',
                    'borderRadius': '2px',
                    'padding': '1px 4px',
                    'marginLeft': '5px',
                    'fontFamily': 'inherit'
                }
            ) if unlimited else None
        ]
    )

def _dimension_strip(dimensions: list):
    """
    Compact pill strip showing all file dimensions above the variable cards.
    dimensions: list of {'name': str, 'size': int, 'unlimited': bool}
    Rendered once from the parsed file: read-only
    """
    return html.Div(
        style={
            'display': 'flex',
            'alignItems': 'center',
            'gap': '6px',
            'flexWrap': 'wrap',
            'padding': '8px 12px',
            'backgroundColor': '#1a1a1a',
            'border': f'1px solid #2a2a2a',
            'borderRadius': '5px',
            'marginBottom': '18px'
        },
        children=[
            html.Span(
                'Dimensions',
                style={
                    'fontSize': '10px',
                    'fontWeight': '600',
                    'color': '#666',
                    'textTransform': 'uppercase',
                    'letterSpacing': '.06em',
                    'marginRight': '2px',
                    'flexShrink': '0',
                }
            ),
            *[
                _dimension_pill(
                    name=d['name'],
                    size=d['size'],
                    unlimited=d.get('unlimited', False)
                )
                for d in dimensions
            ]
        ]
    )

# ----------------------------------------------------------
# Variable errors count pill
# ----------------------------------------------------------
def _var_err_pill_style():
    return {
        'fontSize': '10px',
        'fontWeight': '700',
        'textTransform': 'uppercase',
        'letterSpacing': '0.06em',
        'padding': '2px 8px',
        'borderRadius': '4px',
        'lineHeight': '1.4',
        'whiteSpace': 'nowrap',
        'color': '#ff4d4d',                         # High contrast vibrant red text
        'backgroundColor': '#2a1215',               # Dark crimson solid background container
        'border': '1px solid rgba(255, 77, 77, 0.45)', # Sharp contrast warning border accent
        'display': 'inline-flex',
        'alignItems': 'center',
        'gap': '4px',
        'boxShadow': '0 1px 3px rgba(0,0,0,0.3)'    # Gives elevation over header bar background
    }

# ----------------------------------------------------------
# Imported Variable Card
# ----------------------------------------------------------
def _var_type_badge(var_type: str):
    """Colored badge matching the VarType enum value."""
    styles = {
        'data': {
            'color': '#b968e0', 'background': 'rgba(185,104,224,.08)', 'border': '1px solid rgba(185,104,224,.12)'},
        'coordinate': {
            'color': '#38d9a9', 'background': 'rgba(56,217,169,.08)', 'border': '1px solid rgba(56,217,169,.12)'},
        'qc': {
            'color': '#f7b44f', 'background': 'rgba(247,180,79,.08)', 'border': '1px solid rgba(247,180,79,.12)'}
    }
    labels = {
        'data': 'Data',
        'coordinate': 'Coord',
        'qc': 'QC'
    }
    s = styles.get(var_type, {})
    return html.Span(
        labels.get(var_type, var_type),
        style={
            'fontSize': '10px',
            'padding': '2px 7px',
            'borderRadius': '3px',
            'fontWeight': '600',
            **s,
        }
    )

def _var_card_body_children(uid: str, var_type: str, attributes: dict, extra_store_data: list):
    """
    Body content of a variable card, built lazily on first expand.
    """
    attr_inputs = _attr_inputs_for_type(uid, var_type, attributes)

    if extra_store_data:
        extra_attr_children = [
            _extra_attr_row(uid=e['uid'], key=e['key'], value=e['value'], var_uid=uid,
                            is_original=e.get('is_original', False), removed=e.get('removed', False))
            for e in extra_store_data
        ]
    else:
        extra_attr_children = [
            html.P('No extra attributes',
                   style={'color': '#555', 'fontStyle': 'italic', 'fontSize': '12px', 'marginBottom': '8px'})
        ]

    return [
        html.Div(
            style={'display': 'grid', 'gridTemplateColumns': '1fr 1fr 1fr',
                   'gap': '12px', 'marginBottom': '16px'},
            children=attr_inputs
        ),
        _section_label('Extra attributes'),
        html.Div(id={'type': 'nc-var-extra-container', 'index': uid}, children=extra_attr_children),
        html.Button(
            '+ add attribute',
            id={'type': 'btn-add-nc-var-attr', 'index': uid},
            n_clicks=0,
            style={
                'backgroundColor': 'rgba(79,142,247,.05)',
                'border': f'1px dashed {_BORDER_COLOR}',
                'borderRadius': '4px', 'color': '#4f8ef7', 'cursor': 'pointer',
                'fontSize': '11px', 'fontWeight': '600', 'padding': '5px 12px',
                'fontFamily': 'inherit', 'marginTop': '8px', 'transition': 'all 0.2s ease',
            }
        ),
    ]

def _imported_var_card(uid: str, name: str, var_type: str, dtype: str,
                       dimensions: str, attributes: dict, original_extra_attrs=None):
    """
    Lightweight shell: header + always-mounted store + EMPTY body.
    Body is populated on first expand (toggle_var_card), so hundreds of cards
    render on load without mounting thousands of inputs.
    """
    original_extra_attrs = original_extra_attrs or {}
    extra_attrs_dict = attributes.get('extra_attrs', {}) or {}

    initial_store_data = [
        {'uid': str(uuid.uuid4()), 'key': k, 'value': _format_value_for_ui(v),
         'is_original': k in original_extra_attrs, 'removed': False}
        for k, v in extra_attrs_dict.items()
    ]
    # Soft-removed originals: in the original snapshot but absent from current
    # state -> resurrect as struck-out rows so Undo still works after reload.
    for k, v in original_extra_attrs.items():
        if k not in extra_attrs_dict:
            initial_store_data.append({
                'uid': str(uuid.uuid4()), 'key': k, 'value': _format_value_for_ui(v),
                'is_original': True, 'removed': True,
            })

    return html.Div(
        id={'type': 'nc-var-card', 'index': uid},
        className='nc-var-card',
        style={
            'backgroundColor': '#252525', 'border': f'1px solid {_BORDER_COLOR}',
            'borderRadius': '6px', 'marginBottom': '10px', 'overflow': 'hidden',
        },
        children=[
            html.Div(  # Header — identical to your original
                id={'type': 'nc-var-card-header', 'index': uid},
                className='nc-var-card-hd',
                n_clicks=0,
                style={
                    'display': 'flex', 'alignItems': 'center', 'justifyContent': 'space-between',
                    'padding': '10px 14px', 'backgroundColor': '#2a2a2a',
                    'borderBottom': f'1px solid {_BORDER_COLOR}',
                    'borderTopLeftRadius': '6px', 'borderTopRightRadius': '6px',
                    'cursor': 'pointer'
                },
                children=[
                    html.Div(
                        style={'display': 'flex', 'alignItems': 'center', 'gap': '10px'},
                        children=[
                            html.Span('▶', id={'type': 'nc-var-arrow', 'index': uid},
                                    style={'fontSize': '12px', 'color': '#666'}),
                            html.Div(
                                style={'display': 'flex', 'alignItems': 'center', 'gap': '8px'},
                                children=[
                                    html.Span(name, style={'fontSize': '13px', 'fontWeight': '600',
                                                        'fontFamily': 'monospace', 'color': _TEXT_COLOR}),
                                    html.Span(f'{dtype} ⋅ ({dimensions})',
                                            style={'fontSize': '11px', 'color': '#666'}),
                                    _var_type_badge(var_type),
                                ]),
                        ]
                    ),
                    html.Div(
                        style={'display': 'flex', 'alignItems': 'center'},
                        children=[
                            html.Span(
                                id={'type': 'nc-var-err-count', 'index': uid},
                                style={'display': 'none'},
                                children=[],
                            ),
                        ]
                    ),
                ]
            ),

            # Store OUTSIDE the lazy body: must exist before expansion so
            # sync/save/diff can read unmounted cards' extra attrs.
            dcc.Store(id={'type': 'nc-var-extra-store', 'index': uid}, data=initial_store_data),

            # Body: empty until first expand
            html.Div(
                id={'type': 'nc-var-card-body', 'index': uid},
                style={'display': 'none', 'padding': '16px'},
                children=[]
            ),
        ]
    )

def _attr_inputs_for_type(uid: str, var_type: str, attributes: dict):
    """
    Returns the list of attribute input Divs for the given var_type.
    Shared fields (standard_name, units) come first, then type-specific ones.
    Read-only structural fields (dtype, shape, dimensions) render as plain text.
    """
    def _field(label, component, span=1, field_key=None):
        style = {'gridColumn': f'span {span}'} if span > 1 else {}

        if not field_key:
            return html.Div(style=style, children=[html.Label(label, style=_label_style), component])

        return html.Div(
            id={'type': 'nc-var-field-wrap', 'index': uid, 'field': field_key},
            className='nc-field-wrap',
            style=style,
            children=[
                _var_field_label(label, uid, field_key),
                component,
                html.Div(
                    id={'type': 'nc-var-field-err', 'index': uid, 'field': field_key},
                    className='nc-field-err',
                    children=[]
                ),
            ]
        )

    def _inp(comp_id, placeholder, db_key, monospace=False):
        display_value = _format_value_for_ui(attributes.get(db_key, None))
        return dcc.Input(
            id=comp_id,
            type='text',
            value=display_value,
            placeholder=placeholder,
            debounce=True,
            style={
                **_input_style,
                **(({'fontFamily': 'monospace', 'fontSize': '12px'}) if monospace else {}),
            }
        )

    if var_type == 'data':
        return [
            _field('Standard name', _inp({'type': 'nc-var-standard-name', 'index': uid}, 'CF  standard name', 'standard_name'), field_key='standard_name'),
            _field('Units', _inp({'type': 'nc-var-units', 'index': uid}, 'e.g. meters', 'units'), field_key='units'),
            _field('Fill value', _inp({'type': 'nc-var-fill-value', 'index': uid}, 'e.g. NaNf', 'fill_value', monospace=True), field_key='fill_value'),
            _field('Coordinates', _inp({'type': 'nc-var-coordinates', 'index': uid}, 'e.g. TIME LATITUDE LONGITUDE', 'coordinates', monospace=True), span=2, field_key='coordinates'),
        ]
    elif var_type == 'coordinate':
        return [
            _field('Standard name', _inp({'type': 'nc-var-standard-name', 'index': uid}, 'CF  standard name', 'standard_name'), field_key='standard_name'),
            _field('Units', _inp({'type': 'nc-var-units', 'index': uid}, 'e.g. meters', 'units'), field_key='units'),
            _field('Axis', _inp({'type': 'nc-var-axis', 'index': uid}, 'x / Y / Z / T', 'axis', monospace=True), field_key='axis'),
        ]
    elif var_type == 'qc':
        return [
            _field('Units', _inp({'type': 'nc-var-units', 'index': uid}, 'e.g. 1', 'units'), field_key='units'),
            _field('Fill value', _inp({'type': 'nc-var-fill-value', 'index': uid}, 'e.g. -128b', 'fill_value', monospace=True), field_key='fill_value'),
            _field('Flag values', _inp({'type': 'nc-var-flag-values', 'index': uid}, 'e.g. 0b, 1b, 2b, 3b, 4b, 7b, 8b, 9b', 'flag_values', monospace=True), span=3, field_key='flag_values'),
            _field('Flag meanings', _inp({'type': 'nc-var-flag-meanings', 'index': uid}, 'e.g. unknown good_data probably_good_data potentially_correctable_bad_data bad_data nominal_value interpolated_value missing_value', 'flag_meanings', monospace=True), span=3, field_key='flag_meanings'),
        ]

    # Fallback - render all attributes as generic inputs
    return [
        _field(k, _inp({'type': f'nc-var-atrr-k', 'index': uid}, '', k))
        for k, v in attributes.items()
    ]

# ----------------------------------------------------------
# Diff dots variable label helper function
# ---------------------------------------------------------- 
def _var_field_label(label: str, uid: str, field_key: str):
    return html.Div(
        style={'display': 'flex', 'alignItems': 'center', 'gap': '6px', 'marginBottom': '4px'},
        children=[
            html.Label(label, style={**_label_style, 'marginBottom': '0'}),
            html.Span(
                id={'type': 'nc-var-field-dot', 'index': uid, 'field': field_key},
                className='nc-field-dot',
                style={'display': 'none'},
                children=[],
            )
        ]
    )

# ----------------------------------------------------------
# Manual add card - Removed this functionality.
# ----------------------------------------------------------
# def _manual_var_card(uid: str):
#     """
#     A blank variable card for a manually added variable.
#     The variable type dropdown reveals the relevant attribute panels.
#     """
#     return html.Div(
#         id={'type': 'nc-var-card', 'index': uid},
#         style={
#             'backgroundColor': '#252525',
#             'border': '1px solid rgba(150,79,247,.2)',
#             'borderRadius': '6px',
#             'marginBottom': '8px', 
#             'overflow': 'hidden',
#         },
#         children=[
#             html.Div(
#                 id={'type': 'nc-var-card-header', 'index': uid},
#                 n_clicks=0,
#                 style={
#                     'display': 'flex',
#                     'alignItems': 'center',
#                     'justifyContent': 'space-between',
#                     'padding': '9px 13px',
#                     'backgroundColor': '#29252e',
#                     'borderBottom': '1px solid rgba(150,79,247,.12)',
#                     'borderTopLeftRadius': '6px',
#                     'borderTopRightRadius': '6px',
#                     'cursor': 'pointer',
#                 },
#                 children=[
#                     html.Div(
#                         style={'display': 'flex', 'alignItems': 'center', 'gap': '9px'},
#                         children=[
#                             html.Span(
#                                 '▼',
#                                 id={'type': 'nc-var-arrow', 'index': uid},
#                                 style={'fontSize': '10px', 'color': '#555'}
#                             ),
#                             html.Div([
#                                 # Wrapper row to put 'New variable' and 'Manual' next to each other
#                                 html.Div(
#                                     style={'display': 'flex', 'alignItems': 'center', 'gap': '8px'},
#                                     children=[
#                                         html.Span(
#                                             id={'type': 'nc-var-card-title', 'index': uid},
#                                             children='New variable',
#                                             style={
#                                                 'fontSize': '13px',
#                                                 'fontWeight': '600',
#                                                 'fontFamily': 'monospace',
#                                                 'color': _TEXT_COLOR,
#                                             }
#                                         ),
#                                         html.Span(
#                                             'Manual',
#                                             style={
#                                                 'fontSize': '10px',
#                                                 'padding': '2px 7px',
#                                                 'borderRadius': '3px',
#                                                 'fontWeight': '600',
#                                                 'color': '#a06ff7',
#                                                 'backgroundColor': 'rgba(150,79,247,.08)',
#                                                 'border': '1px solid rgba(150,79,247,.12)',
#                                             }
#                                         ),
#                                     ]
#                                 ),
#                                 html.Div(
#                                     'manually added',
#                                     style={'fontSize': '11px', 'color': '#444', 'marginTop': '1px'}
#                                 ),
#                             ]),
#                         ]
#                     ),
#                     html.Div(
#                         style={'display': 'flex', 'alignItems': 'center', 'gap': '6px'},
#                         children=[
#                             html.Button(
#                                 'Remove',
#                                 id={'type': 'btn-remove-nc-var', 'index': uid},
#                                 style={
#                                     'backgroundColor': 'rgba(247,111,111,.08)',
#                                     'border': '1px solid rgba(247,111,111,.18)',
#                                     'borderRadius': '4px',
#                                     'color': '#f76f6f',
#                                     'cursor': 'pointer',
#                                     'fontSize': '11px',
#                                     'padding': '3px 9px',
#                                 }
#                             ),
#                         ]
#                     ),
#                 ]
#             ),

#             # Body - open by default for manual cards so user fills it in
#             html.Div(
#                 id={'type': 'nc-var-card-body', 'index': uid},
#                 style={'display': 'block', 'padding': '14px'},
#                 children=[
                    
#                     # Type + Name row - drives conditional panels below,
#                     # mirrors cov-type dropdown pattern from arctic spatil tab
#                     html.Div(
#                         style={
#                             'display': 'grid',
#                             'gridTemplateColumns': '1fr 1fr',
#                             'gap': '12px',
#                             'maxWidth': '480px',
#                             'marginBottom': '16px',
#                         },
#                         children=[
#                             html.Div([
#                                 html.Label('Variable type', style=_label_style),
#                                 dcc.Dropdown(
#                                     id={'type': 'nc-var-type-dropdown', 'index': uid},
#                                     options=[{'label': v.label, 'value': v.value} for v in VarType],
#                                     placeholder='Select type...',
#                                     clearable=False,
#                                     style={**_dropdown_style, 'zIndex': 900},
#                                 ),
#                             ]),
#                             html.Div([
#                                 html.Label('Variable name', style=_label_style),
#                                 dcc.Input(
#                                     id={'type': 'nc-var-name-input', 'index': uid},
#                                     type='text',
#                                     placeholder='e.g. CAPH',
#                                     debounce=True,
#                                     style={**_input_style, 'fontFamily': 'monospace', 'fontSize': '12px'},
#                                 ),
#                             ]),
#                         ]
#                     ),

#                     # Shared fields - always shown once a type is selected,
#                     # hidden initially
#                     html.Div(
#                         id={'type': 'nc-var-panel-shared', 'index': uid},
#                         style={'display': 'none'},
#                         children=[
#                             _section_label('Common attributes'),
#                             html.Div(
#                                 style={
#                                     'display': 'grid',
#                                     'gridTemplateColumns': '1fr 1fr',
#                                     'gap': '12px',
#                                     'maxWidth': '480px',
#                                     'marginBottom': '12px'
#                                 },
#                                 children=[
#                                     html.Div([
#                                         html.Label('Data type', style=_label_style),
#                                         dcc.Dropdown(
#                                             id={'type': 'nc-var-dtype', 'index': uid},
#                                             options=[
#                                                 {'label': t, 'value': t}
#                                                 for t in ['float32', 'float64', 'int8', 'int16', 'int32', 'char']
#                                             ],
#                                             placeholder='Select...',
#                                             clearable=False,
#                                             style={**_dropdown_style},
#                                         ),
#                                     ]),
#                                     html.Div([
#                                         html.Label('Units', style=_label_style),
#                                         dcc.Input(
#                                             id={'type': 'nc-var-units', 'index': uid},
#                                             type='text',
#                                             placeholder='e.g. meters',
#                                             debounce=True,
#                                             style=_input_style,
#                                         ),
#                                     ]),
#                                     html.Div([
#                                         html.Label('Standard name', style=_label_style),
#                                         dcc.Input(
#                                             id={'type': 'nc-var-standard-name', 'index': uid},
#                                             type='text',
#                                             placeholder='CF standard name...',
#                                             debounce=True,
#                                             style=_input_style,
#                                         ),
#                                     ]),
#                                     html.Div([
#                                         html.Label('Dimensions', style=_label_style),
#                                         dcc.Input(
#                                             id={'type': 'nc-var-dimesnions', 'index': uid},
#                                             type='text',
#                                             placeholder='e.g. DEPTH, TIME, LATITUDE',
#                                             debounce=True,
#                                             style={**_input_style, 'fontFamily': 'monospace', 'fontSize': '12px'},
#                                         ),
#                                     ]),
#                                 ]
#                             ),
#                         ]
#                     ),

#                     # Data variable panel - shown when type == 'data'
#                     html.Div(
#                         id={'type': 'nc-var-panel-data', 'index': uid},
#                         style={'display': 'none'},
#                         children=[
#                             _section_label('Data variable attributes'),
#                             html.Div(
#                                 style={
#                                     'display': 'grid',
#                                     'gridTemplateColumns': '1fr 1fr',
#                                     'gap': '12px',
#                                     'maxWidth': '480px',
#                                     'marginBottom': '12px',
#                                 },
#                                 children=[
#                                     html.Div([
#                                         html.Label('Fill value', style=_label_style),
#                                         dcc.Input(
#                                             id={'type': 'nc-var-fill-value', 'index': uid},
#                                             type='text',
#                                             placeholder='e.g. NaNf',
#                                             debounce=True,
#                                             style={**_input_style, 'fontFamily': 'monospace', 'fontSize': '12px'},
#                                         ),
#                                     ]),
#                                     html.Div([
#                                         html.Label('Coordinates', style=_label_style),
#                                         dcc.Input(
#                                             id={'type': 'nc-var-coordinates', 'index': uid},
#                                             type='text',
#                                             placeholder='e.g. TIME HEIGHT_WIND LATITUDE LONGITUDE',
#                                             debounce=True,
#                                             style={**_input_style, 'fontFamily': 'monospace', 'fontSize': '12px'},
#                                         ),
#                                     ]),
#                                 ]
#                             ),
#                         ]
#                     ),

#                     # Coordinate variable panel - shown when data_type == 'coordinate'
#                     html.Div(
#                         id={'type': 'nc-var-panel-coord', 'index': uid},
#                         style={'display': 'none'},
#                         children=[
#                             _section_label('Coordinate variable attributes'),
#                             html.Div(
#                                 style={
#                                     'display': 'grid',
#                                     'gridTemplateColumns': '1fr 1fr',
#                                     'gap': '12px',
#                                     'maxWidth': '480px', 
#                                     'marginBottom': '12px'
#                                 },
#                                 children=[
#                                     html.Div([
#                                         html.Label('Axis', style=_label_style),
#                                         dcc.Dropdown(
#                                             id={'type': 'nc-var-axis', 'index': uid},
#                                             options=[
#                                                 {'label': a, 'value': a}
#                                                 for a in ['X', 'Y', 'Z', 'T']
#                                             ],
#                                             placeholder='Select...',
#                                             clearable=False,
#                                             style=_dropdown_style,
#                                         ),
#                                     ]),
#                                     #  html.Div([
#                                     #     html.Label('Positive direction', style=_label_style),
#                                     #     dcc.Dropdown(
#                                     #         id={'type': 'nc-var-positive', 'index': uid},
#                                     #         options=[
#                                     #             {'label': 'Up', 'value': 'up'},
#                                     #             {'label': 'Down', 'value': 'down'},
#                                     #         ],
#                                     #         placeholder='Select...',
#                                     #         clearable=False,
#                                     #         style=_dropdown_style,
#                                     #     ),
#                                     # ]),
#                                 ]
#                             ),
#                         ]
#                     ),

#                     # QC variabel panel - shown when data type == 'qc'
#                     html.Div(
#                         id={'type': 'nc-var-panel-qc', 'index': uid},
#                         style={'display': 'none'},
#                         children=[
#                             _section_label('QC variable attributes'),
#                             html.Div(
#                                 style={
#                                     'maxWidth': '480px', 
#                                     'marginBottom': '12px'
#                                 },
#                                 children=[
#                                     html.Div(
#                                         style={
#                                             'display': 'grid',
#                                             'gridTemplateColumns': '1fr 1fr',
#                                             'gap': '12px',
#                                             'marginBottom': '12px'
#                                         },
#                                         children=[
#                                             html.Div([
#                                                 html.Label('Fill value', style=_label_style),
#                                                 dcc.Input(
#                                                     id={'type': 'nc-var-fill-value', 'index': uid},
#                                                     type='text',
#                                                     placeholder='e.g. -128b',
#                                                     debounce=True,
#                                                     style={**_input_style, 'fontFamily': 'monospace', 'fontSize': '12px'},
#                                                 ),
#                                             ]),
#                                         ]
#                                     ),
#                                     html.Div([
#                                         html.Label('Flag values', style=_label_style),
#                                         dcc.Input(
#                                             id={'type': 'nc-var-flag-values', 'index': uid},
#                                             type='text',
#                                             placeholder='e.g. 0b, 1b, 2b',
#                                             debounce=True,
#                                             style={**_input_style, 'fontFamily': 'monospace', 'fontSize': '12px', 'marginBottom': '10px'},
#                                         ),
#                                     ]),
#                                     html.Div([
#                                         html.Label('Flag meanings', style=_label_style),
#                                         dcc.Input(
#                                             id={'type': 'nc-var-flag-meanin', 'index': uid},
#                                             type='text',
#                                             placeholder='e.g. unknown good_data probably_good_data',
#                                             debounce=True,
#                                             style={**_input_style, 'fontFamily': 'monospace', 'fontSize': '12px', 'marginBottom': '10px'},
#                                         ),
#                                     ]),
#                                 ]
#                             ),
#                         ]
#                     ),
                    
#                     # Extra attributes - always at the bottom of a manual card
#                     _section_label('Extra attributes'),
#                     html.Div(
#                         id={'type': 'nc-var-extra-container', 'index': uid},
#                         children=[
#                             html.P(
#                                 'No extra attributes',
#                                 style={
#                                     'color': '#555', 
#                                     'fontStyle': 'italic',
#                                     'fontSize': '12px',
#                                     'marginBottom': '8px'
#                                 }
#                             )
#                         ]
#                     ),
#                     html.Button(
#                         '+ add attribute',
#                         id={'type': 'btn-add-nc-var-attr', 'index': uid},
#                         n_clicks=0,
#                         style={
#                             'backgroundColor': 'rgba(79,142,247,.05)',
#                             'border': '1px solid rgba(79,142,247,.25)',
#                             'borderRadius': '4px',
#                             'color': '#4f8ef7',
#                             'cursor': 'pointer',
#                             'fontSize': '11px',
#                             'fontWeight': '600',
#                             'padding': '5px 12px',
#                             'fontFamily': 'inherit',
#                             'marginTop': '8px',
#                             'transition': 'all 0.2s ease', # Smooth hover state transitions
#                         }
#                     ),

#                     dcc.Store(
#                         id={'type': 'nc-var-extra-store', 'index': uid},
#                         data=[]
#                     ),
#                 ]
#             ),
#         ]
#     )

# ----------------------------------------------------------
# Main Tab Layout
# ----------------------------------------------------------
def create_tab_content():
    # Placeholder dimesnions - replaced by parsed values on file upload
    _placeholder_dims = [
        {'name': 'Latitude', 'size': 0, 'unlimited': False},
        {'name': 'Longitude', 'size': 0, 'unlimited': False},
        {'name': 'Time', 'size': 0, 'unlimited': True}
    ]
    return html.Div(
        children=[
            html.H4(
                'Variables',
                style={'color': '#e0e0e0', 'marginBottom': '10px'}
            ),
            html.P(
                'Variables are imported from your netCDF file. '
                'Review and correct their attributes, or add/remove any attributes.',
                style={'color': '#999', 'marginBottom': '20px'}
            ),

            # Dimensions strip - populated by callback on file upload,
            # shown with placeholder zeros until then
            html.Div(
                id='nc-dimension-strip',
                children=[_dimension_strip(_placeholder_dims)],
            ),

            # Toolbar: search + type filters + add button
            html.Div(
                style={
                    'display': 'flex',
                    'gap': '12px',
                    'marginBottom': '16px',
                    'alignItems': 'center',
                },
                children=[
                    dcc.Input(
                        id='nc-var-search',
                        type='text',
                        placeholder="Search variables...",
                        debounce=False,
                        style={
                            **_input_style,
                            'flex': '1',
                            'fontSize': '12px'
                        }
                    ),
                    # Type filter buttons - controlled by callback
                    html.Div(
                        id='nc-var-filter-btns',
                        style={'display': 'flex', 'gap': '3px'},
                        children=[
                            html.Button(
                                label, 
                                id={'type': 'nc-var-filter-btn', 'index': val},
                                n_clicks=0,
                                style={
                                    'background': 'none',
                                    'border': f'1px solid {"#2d2d2d" if val != "all" else "rgba(79,142,247,.25)"}',
                                    'borderRadius': '3px',
                                    'color': '#4f8ef7' if val == 'all' else '#999',
                                    'backgroundColor': 'rgba(79,142,247,.08)' if val == 'all' else 'none',
                                    'fontSize': '10px',
                                    'fontWeight': '600',
                                    'padding': '4px 8px',
                                    'cursor': 'pointer',
                                    'textTransform': 'uppercase',
                                    'letterSpacing': '.04em',
                                    'fontFamily': 'inherit',
                                }
                            )
                            for label, val in [
                                ('All', 'all'),
                                ('Data', 'data'),
                                ('Coord', 'coordinate'),
                                ('QC', 'qc'),
                            ]
                        ]
                    ),
                    # Removed manually adding variable.
                    # html.Button(
                    #     '+ add variable', 
                    #     id='btn-add-nc-var',
                    #     n_clicks=0,
                    #     style={
                    #         'backgroundColor': 'rgba(79,142,247,.08)',
                    #         'border': '1px solid rgba(79,142,247,.18)',
                    #         'borderRadius': '4px',
                    #         'color': '#4f8ef7',
                    #         'fontSize': '11px',
                    #         'fontWeight': '600',
                    #         'padding': '5px 10px',
                    #         'cursor': 'pointer',
                    #         'whiteSpace': 'nowrap',
                    #         'fontFamily': 'inherit'
                    #     }
                    # ),
                ]
            ),

            # Variable count summary
            html.Div(
                id='nc-var-count',
                style={
                    'fontSize': '11px',
                    'color': '#4f8ef7', 
                    'fontWeight': '600',
                    'textTransform': 'uppercase',
                    'letterSpacing': '.04em',
                    'marginBottom': '10px'
                },
    #             {
    # 'width': '100%',
    # 'backgroundColor': '#2a2a2a',
    # 'border': '1px solid #444',
    # 'borderRadius': '4px',
    # 'color': '#e0e0e0',
    # 'padding': '8px 12px',
    # 'fontSize': '13px',
    # 'boxSizing': 'border-box',
    # 'marginBottom': '10px'
    # }
                children='0 variables'
            ),

            # Variable cards container - populated by callback
            html.Div(
                id='nc-var-cards-container',
                children=[
                    html.P(
                        'No variables imported yet.',
                        id='nc-var-empty-msg',
                        style={
                            'color': '#555', 
                            'fontStyle': 'italic',
                            'fontSize': '12px',
                            'marginBottom': '8px'
                        }
                    )
                ]
            ),

            # Store: source of truth for the variables list
            # Each entry: {'uid': str, 'name': str, 'var_type': str,
            #              'dtype': str, 'shape': str, 'dimesnsions': str,
            #               'attributes': dict, 'source': 'imported' | 'manual'}
            dcc.Store(id='nc-vars-store', data=[]),

            # Store: currently active type filter
            dcc.Store(id='nc-var-filter-store', data='all'),
        ]
    )
