# Nawia

Nawia is a laboratory database and metadata management tool for organizing sample information, laboratory processing data, and sequencing records specifically for ancientDNA research projects. It is being built with mainly human aDNA data in mind, but the goal is to abstract functionalities to seamlessly work with any kind of vertebrate aDNA data.

> Nawia is under active development and is not yet ready for production use.
> To do:
>
> - Add the interactive configuration page described below;
> - Provide different example schemas and configuration files (other than the default, human-data focused one),
> - When the sub-tool "Eager-helper" (temporary name) will be ready, add it to the dependencies and define the logic to create input files and output directories.

## Features

- Browse and search laboratory sample records
- View sample details across related database tables
- Edit supported database records through the web interface
- Upload sample data from Excel templates
- Export data to Excel, CSV, or SQLite
- Create and manage PostgreSQL database backups
- Parse FASTQ and BAM file metadata
- Generate and manage project-specific Excel templates

## Technology

Nawia is implemented in Python and uses:

- Streamlit for the web interface
- SQLAlchemy and PostgreSQL for database access
- pandas for data processing
- Plotly for interactive visualizations

## Installation

Python 3.11 or later is recommended. Install Nawia:

```bash
python -m pip install --upgrade pip
pip install git+https://github.com/LPCG-ancientDNA-Warsaw/nawia.git
```

You may encounter this error while trying to install it: "error: externally-managed-environment × This environment is externally managed". To avoid this, create a virtual python environment where you will install pip and Nawia, by doing:

```bash
python3 -m venv --without-pip ~/venv
curl -O https://bootstrap.pypa.io/get-pip.py
~/venv/bin/python ~/get-pip.py
~/venv/bin/python -m pip install git+https://github.com/LPCG-ancientDNA-Warsaw/nawia.git
source ~/venv/bin/activate
```

## Configuration

Nawia reads database and project settings from `config.json`. Copy or adapt `templates/default_config.json` to configure:

- PostgreSQL database name, host, and port
- Project names and database schemas
- Table mappings
- Summary fields
- Table relationships
- Default values
- Filename parsing rules for users

`config.json` contains the database address and one or more project configurations. Database credentials are requested when connecting and are not saved in this file.

The CLI can guide configuration setup and check it before Nawia is used:

```bash
# Create a config by selecting a preset or mapping fields in an existing PostgreSQL schema
nawia config init

# Interactively edit selected parts of an existing config
nawia config edit

# Check JSON structure and required mappings without connecting to the database
nawia config validate

# Test credentials, mapped tables and columns, and configured relationships
nawia config test
```

`config init` prompts for the PostgreSQL host, port, database name, and credentials before loading available schemas and configuring mappings. Credentials are used only to establish the connection and are not saved. Use the database server's hostname and port; `localhost` refers to the machine running Nawia (or to an SSH tunnel running on that same machine). The command reads database metadata but does not create, alter, or delete database tables. It will not overwrite an existing configuration unless `--force` is provided; in that case, it first saves a `.bak` copy. The configuration web page is still planned.

`config edit` lets users choose which existing configuration sections to edit: database connection, project details, mappings, summary fields, table hierarchies, and default values/user filename rules. Unselected sections are preserved. Editing the database connection displays a warning and requires confirmation; the original file is saved as `.bak` before changes are written. Run `nawia config test` after editing to verify the connection and project schema.

### Configuration concepts

#### Hierarchies

A hierarchy describes how Nawia uploads one Excel sheet into a child table that is linked to a parent table. It is configured as:

```json
"table_hierarchies": {
  "child_table": {
    "parent": "parent_table",
    "join_on": "shared_column"
  }
}
```

For example, `individuals` can be the child of `sampling_sites` joined through `site_name`. A hierarchy is not a taxonomy or a set of visual categories: it describes a real SQL relationship used when Nawia processes parent and child rows.

The current implementation expects:

- the child and parent to exist in the selected schema;
- `join_on` to exist in both tables;
- `join_on` in the parent table to be a primary or otherwise suitable unique key;
- the relationship to correspond to a real foreign key.

No hierarchy should be configured merely because two columns have the same name. Use a foreign key to represent the relationship first, because Nawia must validate and follow that relationship safely.

#### Mandatory mappings

The following mappings are currently mandatory for the existing interface:

| Concept | Purpose |
|---|---|
| `individual_id` | The stable identifier used to find one individual and join their records |
| `coord_ns` | North/south coordinate used by the map |
| `coord_we` | West/east coordinate used by the map |
| `project_id` | Identifier used to group and show project information |
| `material_description` | Description used to group and display material types |

These are **application requirements**, not universal requirements for every laboratory. A research lab without coordinates, for example, should eventually be able to disable the map and remove those mappings.

`mapping` entries also specify the table and column:

```json
"individual_id": {
  "column_name": "lab_id",
  "base_table": "individuals"
}
```

The mandatory mapping names above differ from mandatory SQL columns. A SQL column may be `NOT NULL` and still need no Nawia mapping. Conversely, a mapped column may currently be required because a specific Nawia page assumes it exists.

#### Simplest schema

The smallest useful schema for the current application would need:

- one table containing an individual identifier;
- two coordinate columns;
- one project identifier;
- one separate or joined table containing a material description;
- the primary/foreign-key relationships needed to navigate between those tables.

At present, that simple schema still needs to expose those concepts through the five mandatory mappings. It does not need the complete prehistoric/ancient-DNA workflow: a lab can omit fields such as extraction, library, sequencing, sex estimation, epoch, grave number, and quality metrics until its interface or configuration profile adds them.

This is smaller than the default human-aDNA schema but is not yet a fully generic schema, because the current UI has hard-coded assumptions for the concepts above.

#### Default human-aDNA schema

The default schema is the built-in, human-data-oriented example under `sql/schema.sql`. Its main hierarchy is:

```text
sampling_sites
  -> individuals
     -> processed_samples
        -> dna_extractions
           -> libraries
              -> indexing
                 -> dna_sequencing
                    -> dna_mapping
                    -> sex_estimation
```

The default mapping also includes archaeological context, material processing, extraction, library, sequencing, and mapping fields. Use it as a preset and simplify or extend it for an existing database; it is a starting point, not a universal model.

### Adding and displaying columns

Adding a database column does not require changing Nawia until Nawia should expose it. The recommended process is:

1. Add the column to the PostgreSQL table with an appropriate type, default, and nullability.
2. Backfill existing rows when required.
3. Add or update a mapping only for a field with an established Nawia meaning.
4. Run `nawia config validate`, then `nawia config test`.
5. Add a UI field or dashboard behavior explicitly if the existing pages should display or edit it.

A new SQL column can be optional (`NULL`) or mandatory (`NOT NULL`). If it is truly mandatory, every existing row must receive a value or a database default. For existing data, adding `NOT NULL` without a default or backfill can make the migration fail. A new mandatory column should therefore be migrated in stages:

- add a nullable column;
- backfill and validate existing records;
- change it to `NOT NULL`;
- update Excel imports and default values.

Making a **mapped application field** mandatory can break an existing configuration or workflow when it is absent from old rows or unsupported schemas. Nawia will reject missing required mappings during validation, and it will report missing tables or columns during `config test`. An unmapped new column should not break the application; it remains in the database and can be imported/exported as raw table data.

New columns can also be explored in the **Explore Data** page without adding a Nawia mapping. Nawia reads PostgreSQL column types and offers compatible chart options: histograms and box plots for numeric values, bar charts for categories (optionally aggregating a numeric field), scatter plots for two numeric fields, and line charts for date/time plus numeric fields. Charts use at most 10,000 rows returned by the database and are intended for exploration, not as statistically representative analyses.

Type-based suggestions are useful defaults, but:

- numeric vs numeric: scatter plot;
- categories vs numeric: bar chart or box plot;
- categories: count/bar chart;
- dates vs numeric: line chart;
- boolean: counts or stacked charts;
- geographic coordinate columns: map.

This is not a substitute for a mapped field. Type inference cannot reliably know the scientific meaning, unit, statistical validity, or intended denominator of a column. Use mappings and optional chart definitions for known concepts such as "endogenous DNA", while offering generic column discovery for exploratory or unknown fields.

### Configuration web page

The configuration page is a guided workflow inside Streamlit, with a read-only preview and a save action. It reuses `nawia.configuration` rather than duplicating JSON or database-validation logic.

1. **Database connection**
   - Show database name, host, port, and an optional PostgreSQL username/password.
   - Never render or save the password in `config.json`.
   - On connection success, load schema names and table names.

2. **Start**
   - Offer a default human-aDNA preset, additional presets, and custom configuration.
   - When editing an existing configuration, prefill every control from `config.json`.

3. **Project**
   - Edit project name, PostgreSQL schema, and database backup directory.
   - Do not allow duplicate project names or duplicate schemas.

4. **Mapping**
   - First show the five mandatory concepts with clear labels and status indicators.
   - Display a table and column selector for each mapping.
   - Show optional mappings with a list of currently unmapped table columns.
   - Let the researcher add custom named mappings, then mark which mappings should appear in the individual summary.

5. **Relationships**
   - Display the actual foreign-key graph discovered from PostgreSQL.
   - Provide a visual parent/child hierarchy editor.
   - Offer only existing columns and existing foreign-key relationships; do not generate SQL DDL.

6. **Validation**
   - Run local JSON validation after every change.
   - Run the database test only after an explicit "Test configuration" action.
   - Separate blocking errors from warnings, and identify the exact project, mapping, table, or column involved.

7. **Save**
   - Preview the generated JSON.
   - Save atomically to `config.json`.
   - Refuse overwrite by default; offer a `.bak` copy for an explicit overwrite.

A Streamlit implementation is available in [app_streamlit.py](nawia/app_streamlit.py), reusing the shared functions in [configuration.py](nawia/configuration.py). It is organized into:

```text
Configuration page
├── connection panel
├── preset/project selector
├── mapping editor
├── hierarchy editor
├── summary-field selector
├── validation report
└── JSON preview/save dialog
```

The hierarchy editor uses a read-only Plotly graph of PostgreSQL foreign keys plus guided edit rows. Graph arrows point from parent tables to child tables. It offers only discovered tables and relationships and does not generate or execute SQL DDL.

## Running Nawia

### Web interface

After installing the package, run:

```bash
nawia web_interface
```

The interface will print a local URL, usually `http://localhost:8501`.

### Command-line help

```bash
nawia --help
```

## Database setup

SQL files in the `sql/` directory provide (at the moment) schema and seed material:

- `sql/schema.sql` contains the database schema
- `sql/seed_test.sql` contains test data

The idea of the schema files is to help you configure your PostgreSQL or mySQL database: you can use that file or upload your own before the first use, but please ensure that the schemas hierarchies, table names and (useful) column names referenced by `config.json` exist.

## Project structure

```text
nawia/                 Python package and Streamlit application
sql/                   Database schema and seed SQL
static/                Logos and interface images
templates/             Default configuration and Excel templates
config.json            Active database and project configuration
pyproject.toml         Package metadata and dependencies
Singularity.def        Singularity container recipe
```

## Development status

Nawia is currently in its early development phase. Interfaces, configuration formats, database behavior, and command names may change.
