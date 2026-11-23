# Nawia

Nawia is a laboratory database and metadata management tool for organizing sample information, laboratory processing data, and sequencing records specifically for ancientDNA research projects. It is being built with mainly human aDNA data in mind, but the goal is to abstract functionalities to seamlessly work with any kind of vertebrate aDNA data.

> Nawia is under active development and is not yet ready for production use.
> To do:
>
> - Add a CLI function and a web interface for tool's configuration;
> - Provide different example schemas and configuration files (other than the default, human-data focused one),
> - Create a function that aids the creation of a custom schema and setup on the remote database;
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
pip install git+https://github.com/path/to/Nawia .
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

Right now `config.json` shows a default configuration, but it will be changed into an empty configuration file Database credentials are requested through the Streamlit interface and are not stored in the repository configuration file, but the database address will be saved.

> We are planning to add both a CLI function and a Web Interface page for easier configuration! Stay tuned for updates.

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
