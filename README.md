# Nawia

Nawia is a laboratory database and metadata management tool for organizing sample information, laboratory processing data, and sequencing records.

> Nawia is under active development and is not yet ready for production use.

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

Python 3.11 or later is recommended.

From the repository root, create a virtual environment and install the project:

```bash
python -m venv .venv
```

Activate the environment:

```powershell
# Windows PowerShell
.venv\Scripts\Activate.ps1
```

```bash
# Linux or macOS
source .venv/bin/activate
```

Install Nawia:

```bash
python -m pip install --upgrade pip
python -m pip install .
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

An example configuration is available in `config.json`.

Database credentials are requested through the Streamlit interface and are not stored in the repository configuration file.

## Running Nawia

### Web interface

After installing the package, run:

```bash
nawia web_interface
```

You can also launch Streamlit directly:

```bash
streamlit run nawia/app_streamlit.py
```

The interface will print a local URL, usually `http://localhost:8501`.

### Command-line help

```bash
nawia --help
```

### Parsing files

The parser command can inspect FASTQ or BAM files using the filename rules configured for a user:

```bash
nawia parse --user <username> --fastq <folder>
nawia parse --user <username> --bam <folder>
```

Add `--dry-run` to preview actions without updating the database.

## Database setup

SQL files in the `sql/` directory provide schema and seed material:

- `sql/schema.sql` contains the database schema
- `sql/seed_test.sql` contains test data

Before first use, configure PostgreSQL and ensure the schemas referenced by `config.json` exist.

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
