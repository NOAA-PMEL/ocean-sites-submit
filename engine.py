from portal_core.engine import PortalEngine
from layouts import changes_bar
from dash import dcc

class Engine(PortalEngine):

    name = "ocean_sites"

    def metadata_tabs(self):
        return [
            {"id": "about_this_file", "label": "Global Attributes", "icon":"🗃️"},
            {"id": "data_variables", "label": "Variables", "icon": "🏷️"}
        ]

    def modal_after_header(self):
        return [changes_bar.create_changes_bar(), changes_bar.create_changes_ledger()]

    def sidebar_tab_extras(self, tab):
        return [changes_bar.create_tab_count_badge(tab)]

    def app_stores(self):
        return [
            dcc.Store(id='parsed-data-store'), # can be used to store any parsed data 
            dcc.Store(id='metadata-load-signal-store'), # Used to load and render metatadata from db upon metadata modal opening,=.
            dcc.Store(id='edited-parsed-data-store'), # used to store edited metadata (from the og parsed data). 
            dcc.Store(id='original-parsed-data-store'), # used to pull in the original metadata to make comparisons to updated metadata
            dcc.Store(id='metadata-diff-store'), # For storing the difference between the uploaded metadata and what's been edited
            dcc.Store(id='metadata-problems-store', data=[]), # Used to store validation problems in the metadata
            dcc.Store(id='metadata-validation-mode', data={}), 
        ]