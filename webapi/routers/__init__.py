"""The routes of the web API, one module per area (S1).

New routes start here as an APIRouter; the administrative areas of main.py are moved here without a change in behaviour.
A router imports the shared helpers from webapi/admin_common.py and never imports main.py. main.py registers them.
"""
