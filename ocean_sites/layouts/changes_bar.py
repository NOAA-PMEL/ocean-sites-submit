from dash import html
from ocean_sites.layouts import diff_badge_style

def _count_badge(kind: str):
    return html.Span(
        style={
            **diff_badge_style(kind),
            'cursor': 'inherit',
            'gap': '4px',
            'fontSize': '10px'
        },
        children=[
            html.Span('0', id=f'metadata-changes-count-{kind}'),
            html.Span(kind),
        ]
    )

def create_changes_bar():
    return html.Div(
        id='metadata-changes-bar',
        n_clicks=0,
        style={
            'display': 'flex', 'alignItems': 'center', 'justifyContent': 'space-between', 'gap': '12px',
            'backgroundColor': '#1a1a1a', 'borderBottom': '1px solid rgba(79,142,247,.2)',
            'padding': '9px 22px', 'cursor': 'pointer', 'flexShrink': 0
        },
        children=[
            html.Div(
                style={'display': 'flex', 'alignItems': 'center', 'gap': '10px'},
                children=[
                    html.Span('▶', id='metadata-changes-chevron', style={'color': '#666', 'fontSize': '10px'}),
                    html.Span('No changes since import', id='metadata-changes-title', style={'fontSize': '12px', 'fontWeight': '600', 'color': '#ddd'}),
                ]
            ),
            html.Div(
                id='metadata-changes-dot-counts',
                style={'display': 'flex', 'gap': '12px', 'alignItems': 'center'},
                children=[_count_badge('changed'), _count_badge('added'), _count_badge('removed')]
            ),
        ]
    )

def _fmt(value):
    if value is None or value == '':
        return html.Span('(empty)', style={'color': '#555', 'fontStyle': 'italic', 'fontSize': '11px'})
    return html.Span(str(value), style={'fontFamily': 'monospace', 'fontSize': '11px', 'color': '#ccc'})

def _fmt_strike(value):
    if value is None or value == '':
        return html.Span('(empty)', style={'color': '#555', 'fontStyle': 'italic', 'fontSize': '11px'})
    return html.Span(str(value), style={'fontFamily': 'monospace', 'fontSize': '11px', 'color': '#777', 'textDecoration': 'line-through'})

def _clean_field_label(field: str) -> str:
    """extra_global_attrs.comment / extra_attrs.qc_manual -> 'comment (extra attribute)'."""
    if field.startswith('extra_global_attrs.') or field.startswith('extra_attrs.'):
        return f"{field.split('.', 1)[1]}"
    return field

def _ledger_item(kind: str, field_label: str, original=None, current=None):
    row = [
        html.Span(kind, style={**diff_badge_style(kind), 'cursor': 'default'}),
        html.Span(field_label, style={'fontFamily': 'monospace', 'color': '#999', 'minWidth': '170px', 'flexShrink': '0', 'fontSize': '11.5px'}),
    ]
    if kind == 'changed':
        row += [_fmt_strike(original), html.Span('➔', style={'color': '#555'}), _fmt(current)]
    elif kind == 'added':
        row += [_fmt(current)]
    elif kind == 'removed':
        row += [_fmt_strike(original)]

    return html.Div(
        style={
            'display': 'flex', 'alignItems': 'center', 'gap': '8px', 'padding': '5px 9px', 'fontSize': '11.5px',
            'backgroundColor': '#1c1c1c', 'border': '1px solid #262626', 'borderRadius': '4px', 'marginBottom': '4px'
        },
        children=row
    )

def _ledger_group(title: str, items: list):
    return html.Div(
        style={'marginBottom': '12px'},
        children=[
            html.Div(title, style={'fontSize': '9.5px', 'color': '#666', 'textTransform': 'uppercase', 'letterSpacing': '.07em', 'marginBottom': '6px'}),
            *items,
        ]
    )

def build_ledger_children(diff: dict):
    """Turns build_metadata_diff's cutput into the grouped rows shown when the changed bar is expanded"""
    sections = []

    ga = diff.get('global_attributes', {})
    ga_items = (
        [_ledger_item('changed', _clean_field_label(e['field']), original=e['original'], current=e['current']) for e in ga.get('changed', [])]
        + [_ledger_item('added', _clean_field_label(e['field']), current=e['current']) for e in ga.get('added', [])]
        + [_ledger_item('removed', _clean_field_label(e['field']), original=e['original']) for e in ga.get('removed', [])]
    )
    if ga_items:
        sections.append(_ledger_group('Global attributes', ga_items))

    vr = diff.get('variables', {})
    var_items = (
        [_ledger_item('added', f"{e['name']} (new variable)") for e in vr.get('added', [])]
        + [_ledger_item('removed', f"{e['name']} (variable removed)") for e in vr.get('removed', [])]
    )
    for entry in vr.get('changed', []):
        name = entry['name']
        fields = entry['fields']
        var_items += [_ledger_item('changed', f"{name}.{_clean_field_label(f['field'])}", original=f['original'], current=f['current']) for f in fields.get('changed', [])]
        var_items += [_ledger_item('added', f"{name}.{_clean_field_label(f['field'])}", current=f['current']) for f in fields.get('added', [])]
        var_items += [_ledger_item('removed', f"{name}.{_clean_field_label(f['field'])}", original=f['original']) for f in fields.get('removed', [])]
    if var_items:
        sections.append(_ledger_group('Variables', var_items))

    if not sections:
        return [html.P('No chnages yet.', style={'color': '#555', 'fontStyle': 'italic', 'fontSize': '12px'})]

    return sections

def create_changes_ledger():
    return html.Div(
        id='metadata-changes-ledger',
        style={
            'display': 'none', 'backgroundColor': '#161616', 'borderBottom': '1px solid #333',
            'padding': '12px 12px', 'flexShrink': '0', 'maxHeight': '220px', 'overflow': 'auto'
        },
        children=[]
    )

def create_tab_count_badge(tab):
    return html.Span(
        style={
            'display': 'inline-flex',
            'alignItems': 'center',
            'gap': '4px',
            'marginLeft': 'auto',
        },
        children=[
            html.Span(
                id={'type': 'sidebar-tab-count', 'index': tab['id']},
                style={'display': 'none'},
                children=[],
            ),
            html.Span(
                id={'type': 'sidebar-tab-warning-count', 'index': tab['id']},
                style={'display': 'none'},
                children=[],
            ),
        ],
        )