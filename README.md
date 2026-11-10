# Ocean Sites Data Submission Portal

A data submission portal for OceanSITES NetCDF files, built on
[`portal-core`](https://github.com/NOAA-PMEL/portal-core).

Researchers upload a NetCDF file; the portal parses it, presents its global
attributes and variables for review and correction, validates them against the
OceanSITES specification, and stores the result. The upload wizard, submission
grid, file handling, cloning, and user management all come from `portal-core`.
This repo contains only what is specific to Ocean Sites.

---

## Quick start

```bash
pip install -r requirements.txt
python -m ocean_sites.app     # from the repo root, NOT python ocean_sites/app.py
```

The path form puts the wrong directory on Python's search path and
`portal_core` will not be found.

Requires Postgres and Redis, plus the environment variables listed below.

---

## Repo structure

```
ocean-sites-submit/            # repo root
├── ocean_sites/               # the portal package
│   ├── app.py                 # entry point, ~10 lines
│   ├── engine.py              # PortalEngine subclass
│   ├── config.yaml            # branding, contact, deployment settings
│   ├── assets/                # NOAA-OAP logo, OceanSitesBadges.js, CSS
│   ├── callbacks/             # 3 modules, auto-discovered
│   ├── layouts/               # one <tab_id>_tab.py per tab, plus changes_bar
│   ├── models/                # NetCDF tables and pydantic schemas
│   └── utils/                 # parser, validation, diff
├── requirements.txt
├── Procfile
├── README.md
└── .gitignore
```

**Keep everything inside `ocean_sites/`.** Base derives the package name from
`Engine.__module__`. With `engine.py` at the repo root that is just `"engine"` --
no package -- and base has no `layouts/` or `callbacks/` directory to discover.
The repo may be named anything; the inner directory is what matters.

---

## How this portal plugs into base

`ocean_sites/app.py` is the whole wiring:

```python
from pathlib import Path
from portal_core.app_factory import create_app
from ocean_sites.engine import Engine

app = create_app(Engine(), Path(__file__).parent / "config.yaml")
server = app.server
```

Base receives an `Engine` and a config path. Everything else it finds **by
convention** -- nothing here is explicitly registered:

| What | Where | Must define |
|---|---|---|
| Tab layout | `ocean_sites/layouts/<tab_id>_tab.py` | `create_tab_content()` |
| Callbacks | `ocean_sites/callbacks/*.py` | `register_callbacks(app)` |
| Models | `ocean_sites/models/*.py` | classes subclassing `portal_core.models.base.Base` |
| Assets | `ocean_sites/assets/` | `.css` and `.js` auto-injected |

A callback module that does not define `register_callbacks(app)` is skipped
silently -- a misspelling is the most common reason a callback never fires.

Base logs `describe()` at startup showing exactly which modules resolved. A tab
missing from that output means the file was not found, usually a filename typo.

---

## What Ocean Sites adds

### Metadata tabs

Two tabs, declared in `ocean_sites/engine.py`:

| Tab id | Label | Layout | Callbacks |
|---|---|---|---|
| `about_this_file` | Global Attributes 🗃️ | `about_this_file_tab.py` | `about_this_file_callbacks.py` |
| `data_variables` | Variables 🏷️ | `data_variables_tab.py` | `data_variables_callbacks.py` |

`ocean_sites/layouts/changes_bar.py` is a shared helper, not a tab -- it has no
`_tab` suffix so base ignores it.

### Engine hooks

Ocean Sites overrides four:

- **`metadata_tabs()`** -- the two tabs above
- **`modal_after_header()`** -- injects the changes bar and changes ledger below
  base's metadata modal header
- **`sidebar_tab_extras(tab)`** -- a per-tab count badge showing how many fields
  have been edited or have validation problems
- **`app_stores()`** -- seven `dcc.Store` components (see below)

`clone_metadata()` is **not** overridden. Cloning a submission copies base's
tables but not the NetCDF metadata.

### Application stores

Ocean Sites mounts seven stores app-wide, which is how the parse-edit-validate
flow keeps state:

| Store | Holds |
|---|---|
| `parsed-data-store` | Raw output of the NetCDF parser |
| `original-parsed-data-store` | The unedited baseline, for comparison |
| `edited-parsed-data-store` | The working copy the user is changing |
| `metadata-diff-store` | Computed difference between original and edited |
| `metadata-problems-store` | Validation problems found |
| `metadata-validation-mode` | Which validation view is active |
| `metadata-load-signal-store` | Triggers re-render when the modal opens |

The original/edited pair is what powers the changes bar: every field the user
touches is diffed against what the file originally contained.

### Database tables

In `ocean_sites/models/ocean_sites.py`, all hanging off `oads_metadata.id` with
`ON DELETE CASCADE`:

- **`netcdf_global_attributes`** -- the file-level attributes
- **`netcdf_dimension`** -- declared dimensions
- **`netcdf_variable`** -- variables, with `VarType` distinguishing coordinate,
  data, and QC variables

Enums cover the OceanSITES controlled vocabularies: `DataMode`, `DataType`,
`FormatVersion`, `VarType`.

Pydantic schemas mirror the tables and drive validation:
`NetcdfGlobalAttributesSchema`, `NetcdfDimensionSchema`, and a
`NetcdfVariableBaseSchema` specialised into coordinate, data, and QC variants.
`OceanSitesOadsMetadataSchema` extends base's `OadsMetadataSchema`.

Generic fields -- title, abstract, DOI, publication year, publisher -- live on
base's `oads_metadata` table rather than being duplicated here.

### Parsing, validation, and diffing

`ocean_sites/utils/` is where most of this portal's real work happens:

| Module | Purpose |
|---|---|
| `netcdf_parser.py` | `OceanSitesNetcdfParser` reads an uploaded `.nc` file into a dict of global attributes, dimensions, and variables |
| `metadata_validation.py` | `validate_metadata()` checks against the schemas; problems are grouped by field (`GLOBAL`) or by variable name (`VARIABLE`) |
| `metadata_diff.py` | `build_metadata_diff()` and `summarize_diff_counts()` power the changes bar and per-tab badges |
| `utils.py` | Load and save the whole metadata set: `load_ocean_sites_metadata`, `save_all_ocean_sites_metadata`, `load_original_ocean_sites_metadata` |

`netcdf_creator.py` defines a `NetcdfCreator` class that nothing currently
imports -- it is scaffolding for writing a corrected file back out, not yet
wired into the app.

### Saving

`ocean_sites/callbacks/save_callbacks.py` does two things.

**On upload**, it runs `OceanSitesNetcdfParser` against the `.nc` file and fills
the parsed/original/edited stores, reporting into base's `data-parse-status`.

**On save**, it listens to base's `btn-save-metadata`, validates, and either
writes through `save_all_ocean_sites_metadata()` or -- if there are problems --
saves a draft and populates `metadata-problems-store` so the changes bar can
show them.

Base deliberately does not own saving: the `State()` list is made of this
portal's component ids, so only this portal can declare it. Outputs that base
also writes use `allow_duplicate=True`.

### Single file per submission

`config.yaml` sets `allow_multiple_data_files_to_submit: False`. One NetCDF file
per submission, which is why base's upload area reads "Data File" rather than
"Data Files".

---

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | yes | Postgres connection string |
| `REDIS_URL` | yes | Celery broker and backend |
| `UPLOAD_DIRECTORY` | yes | Where uploaded data files are written |
| `ARCHIVE_DIRECTORY` | yes | Archived submissions |
| `BAG_DIRECTORY` | yes | BagIt bags |
| `TAR_DIRECTORY` | yes | Tarballs for transfer |
| `NCEI_SFTP_KEY` | for SFTP | Ed25519 private key body for NCEI transfer |
| `SFTP_HOST_KEY` | for SFTP | Known-hosts entry for the SFTP server |
| `DASH_ENTERPRISE_ENV` | set by DE | `WORKSPACE` switches to diskcache instead of Celery |
| `DASH_ROUTES_PATHNAME_PREFIX` | optional | URL prefix |

**The four directory paths must be absolute and point at a persistent volume.**
A relative path resolves against whatever the working directory happens to be,
which differs between a workspace, the gunicorn web process, and the Celery
worker. A container filesystem is also ephemeral, so files written outside a
mounted volume disappear on redeploy.

---

## First run on a fresh database

```bash
python -m ocean_sites.app                     # creates tables, then Ctrl-C
portal-seed --ror-csv /path/to/ror-data.csv   # base's shared reference data
python -m ocean_sites.app
```

The first run logs an error from `_load_metadata_roles()` because the
"Data Submitter" and "Investigator" roles do not exist yet. Expected; it goes
away after seeding.

Ocean Sites has no reference data of its own -- its controlled vocabularies are
Python enums, not database tables.

---

## Upgrading portal-core

`requirements.txt` pins a release:

```
portal-core @ git+https://github.com/NOAA-PMEL/portal-core.git@v0.1.2
```

`xarray` and `netCDF4` are listed separately because this portal parses NetCDF
directly, beyond what base needs.

To upgrade:

1. Read `portal-core`'s CHANGELOG for the versions being skipped
2. Edit the tag
3. `pip install -r requirements.txt`
4. Confirm it moved: `pip show portal-core | grep Version`
5. Test: upload a `.nc` file, edit a global attribute, check the changes bar
   updates, save, reopen
6. Commit -- the upgrade is a commit in *this* repo's history, which is what
   makes a later breakage traceable

**Never pin `@main`.** That means "whatever is newest at install time," so two
deploys a week apart can get different base code with no record of it.

Versioning: **patch** is a bug fix; **minor** adds a hook with a working default
and does not affect this portal; **major** changed or removed a hook and
requires migration.

This portal overrides four hooks -- `metadata_tabs`, `modal_after_header`,
`sidebar_tab_extras`, and `app_stores` -- so a major release touching any of
those needs attention here.

If pip reports the old version after step 3, it reused a cached clone:

```bash
pip install -r requirements.txt --force-reinstall --no-deps
```

---

## Deploying

`Procfile`:

```
web: gunicorn ocean_sites.app:server --workers 4
workers: celery -A portal_core.app_factory:celery_app worker --loglevel INFO --concurrency=4
worker-beat: celery -A portal_core.app_factory:celery_app beat
```

Only the `web` line is portal-specific. The Celery lines point at
`portal_core.app_factory` because `celery_app` lives in base.

`gunicorn ocean_sites.app:server` requires `server = app.server` on its own line
in `ocean_sites/app.py`.

Do not add `--preload` without reading the note in `portal_core/constants.py` --
the database connection pool is created at import, and preloading would share
connections across forked workers.

### Two remotes

This repo pushes to two unrelated places:

```bash
git push origin main     # GitHub -- source of truth, code review
git push plotly main     # Dash Enterprise -- what actually deploys
```

Plain `git push` goes to `origin` only. **If a deploy seems to ignore your
changes, compare the commit hash in the deployment banner against
`git log --oneline -1`.** If they differ, you did not push to `plotly`.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `No module named 'portal_core'` | Not installed, or wrong virtualenv. `pip show portal-core`. |
| `No module named 'ocean_sites'` | Ran `python ocean_sites/app.py`. Use `python -m ocean_sites.app` from the repo root. |
| `Can't resolve tab '<id>': Engine is a top-level module` | `engine.py` is at the repo root. It must live inside `ocean_sites/`. |
| Tab missing, no error | Convention did not resolve the file. Check the `describe()` log and the filename. |
| Callbacks never fire | Module does not define `register_callbacks(app)`, or it is misspelled. |
| Duplicate output error | Two callbacks write the same component. Add `allow_duplicate=True`, or an id collides with a base id. |
| Changes bar shows nothing | `original-parsed-data-store` is empty -- the parser did not run or the upload failed. |
| `PermissionError` on upload | Directory env vars unset or not absolute, or the volume is not mounted. |
| `KeyError` at startup | `config.yaml` is missing a required key. |
| Deploy ignores changes | Pushed to `origin` but not `plotly`. |
| App runs unstyled | Base installed without its assets. Check base's `package-data`. |

For how base works -- the Engine contract, base's component ids, how to extend
the schema -- see the
[`portal-core` README](https://github.com/NOAA-PMEL/portal-core).

