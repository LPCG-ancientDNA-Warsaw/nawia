"""Interactive commands for creating and checking the database configuration."""

from __future__ import annotations

import copy
import getpass
import re
import sys
from pathlib import Path
from typing import Any, NoReturn, cast

from nawia.configuration import (
    ConfigurationError,
    REQUIRED_MAPPING_FIELDS,
    find_project,
    load_configuration,
    save_configuration,
    validate_configuration,
    validate_project_schema,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config.json"
DEFAULT_TEMPLATE = ROOT / "templates" / "default_config.json"
PRESET_DIRECTORY = ROOT / "templates" / "config_presets"


def add_parser(subparsers: Any) -> None:
    """Registers commands `config init`, `config validate` and `config test`."""
    config_parser = subparsers.add_parser(
        "config",
        help="Create, validate, or test the database configuration",
        description="Guided setup and checks for Nawia's database configuration.",
    )
    config_commands = config_parser.add_subparsers(dest="config_command", required=True)

    init_parser = config_commands.add_parser(
        "init",
        help="Create a configuration using a preset or a custom mapping",
    )
    init_parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"Where to save the generated file (default: {DEFAULT_CONFIG})",
    )
    init_parser.add_argument(
        "--template",
        type=Path,
        help="Use this JSON template instead of choosing from the available presets",
    )
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing output file after preserving it as .bak",
    )
    init_parser.set_defaults(func=run_init)

    validate_parser = config_commands.add_parser(
        "validate",
        help="Check configuration structure without connecting to the database",
    )
    validate_parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"Configuration file to check (default: {DEFAULT_CONFIG})",
    )
    validate_parser.set_defaults(func=run_validate)

    test_parser = config_commands.add_parser(
        "test",
        help="Test database access and verify mapped tables, columns, and foreign keys",
    )
    test_parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"Configuration file to test (default: {DEFAULT_CONFIG})",
    )
    test_parser.add_argument(
        "--project",
        help="Project name or schema name when the file contains multiple projects",
    )
    test_parser.add_argument(
        "--username",
        help="Database username (the password is always requested privately)",
    )
    test_parser.set_defaults(func=run_test)


def run_validate(args: Any) -> None:
    """Run local checks without connecting to the database or modifying files."""
    try:
        config = load_configuration(args.config)
    except ConfigurationError as error:
        _fail(str(error))

    errors = validate_configuration(config)
    if errors:
        print("Invalid configuration:")
        for error in errors:
            print(f"- {error}")
        raise SystemExit(1)

    print(f"Configuration is valid: {args.config}")
    print("This check does not verify that the tables exist in the database.")


def run_test(args: Any) -> None:
    """Test the database connection and verify mappings and relationships."""
    try:
        config = load_configuration(args.config)
    except ConfigurationError as error:
        _fail(str(error))

    errors = validate_configuration(config)
    if errors:
        print("Fix the configuration errors first:")
        for error in errors:
            print(f"- {error}")
        raise SystemExit(1)

    try:
        project = find_project(config, args.project)
    except ConfigurationError as error:
        _fail(str(error))

    username = args.username or input("Database username: ").strip()
    if not username:
        _fail("The database username cannot be empty.")
    password = getpass.getpass("Database password (not saved): ")

    try:
        engine, inspector = _connect_and_inspect(config["database"], username, password)
    except ConfigurationError as error:
        _fail(str(error))

    from sqlalchemy.exc import SQLAlchemyError

    try:
        errors, warnings = validate_project_schema(project, inspector)
    except SQLAlchemyError as error:
        _fail(
            "Connected successfully, but could not read the schema "
            f"({type(error).__name__}). Check the user's permissions."
        )
    finally:
        engine.dispose()

    if errors:
        print(f"Connected successfully, but schema '{project['schema_name']}' is not valid for Nawia:")
        for error in errors:
            print(f"- {error}")
        for warning in warnings:
            print(f"Warning: {warning}")
        raise SystemExit(1)

    print(f"Connected successfully. Mappings and relationships for '{project['name']}' are valid.")
    for warning in warnings:
        print(f"Warning: {warning}")


def run_init(args: Any) -> None:
    """Guide the user through configuring Nawia for an existing database schema.

    This command reads database metadata but does not create or modify tables.
    Credentials are only used for the current connection and are not saved.
    """
    output_path = args.output.expanduser()
    if output_path.exists() and not args.force:
        _fail(
            f"The file {output_path} already exists. To protect it, init will not "
            "overwrite it: choose --output or use --force to keep a backup."
        )

    try:
        template = _choose_template(args.template)
    except ConfigurationError as error:
        _fail(str(error))

    database_value = template.get("database")
    if not isinstance(database_value, dict):
        _fail("The selected template does not contain a valid 'database' section.")
    database_template = cast(dict[str, Any], database_value)
    print("Database connection settings")
    print(
        "Enter the database server's host and port. Use localhost only when the "
        "database or an SSH tunnel is running on the same machine as Nawia."
    )
    host = _prompt_text("Database host", database_template.get("host"))
    port = _prompt_text("Database port", str(database_template.get("port") or "5432"))
    database_name = _prompt_text("Database name", database_template.get("db_name"))
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        _fail("The port must be a number between 1 and 65535.")
    username = _prompt_text("Database username")
    password = getpass.getpass("Database password (not saved): ")
    if not password:
        _fail("The password cannot be empty.")

    try:
        engine, inspector = _connect_and_inspect(
            {
                "type": "postgresql",
                "host": host,
                "port": port,
                "db_name": database_name,
            },
            username,
            password,
        )
    except ConfigurationError as error:
        _fail(str(error))

    from sqlalchemy.exc import SQLAlchemyError

    try:
        schema = _choose_schema(inspector, template)
        project_template = _choose_project_template(template)
        project_name = _prompt_text(
            "Nawia project name",
            project_template.get("name") or schema,
        )
        project_template["name"] = project_name
        project_template["schema_name"] = schema
        mappings, custom_summary_fields = _configure_mappings(
            project_template.get("mapping", {}),
            inspector,
            schema,
        )
        project_template["mapping"] = mappings

        # Summaries can only contain concepts that the user has mapped.
        summaries_value = project_template.get("summaries_fields")
        summaries = cast(
            dict[str, Any],
            summaries_value if isinstance(summaries_value, dict) else {},
        )
        current_summary_value = summaries.get("individual", [])
        current_summary = (
            [
                field
                for field in cast(list[Any], current_summary_value)
                if isinstance(field, str)
            ]
            if isinstance(current_summary_value, list)
            else []
        )
        summaries["individual"] = [
            field_name
            for field_name in [*current_summary, *custom_summary_fields]
            if field_name in project_template["mapping"]
        ]
        project_template["summaries_fields"] = summaries
        project_template.setdefault("table_hierarchies", {})
        project_template.setdefault("default_values", {})
        project_template.setdefault("users", {})

        config = copy.deepcopy(template)
        database_config_value = config.get("database")
        if not isinstance(database_config_value, dict):
            raise ConfigurationError("The selected template does not contain a valid database section.")
        database_config = cast(dict[str, Any], database_config_value)
        database_config.update(
            {
                "type": "postgresql",
                "host": host,
                "port": port,
                "db_name": database_name,
                "projects": [project_template],
            }
        )

        local_errors = validate_configuration(config)
        if local_errors:
            raise ConfigurationError(
                "The generated configuration failed local validation:\n- "
                + "\n- ".join(local_errors)
            )

        schema_errors, warnings = validate_project_schema(project_template, inspector)
        if schema_errors:
            raise ConfigurationError(
                "The generated configuration does not match the selected schema:\n- "
                + "\n- ".join(schema_errors)
            )

        backup_path = save_configuration(config, output_path, overwrite=args.force)
    except ConfigurationError as error:
        _fail(str(error))
    except SQLAlchemyError as error:
        _fail(
            "Connected successfully, but could not read the schema "
            f"({type(error).__name__}). Check the user's permissions."
        )
    finally:
        engine.dispose()

    print(f"Configuration saved to: {output_path.resolve()}")
    if backup_path:
        print(f"Previous configuration backed up to: {backup_path}")
    print("Credentials were not saved.")
    for warning in warnings:
        print(f"Warning: {warning}")


def _choose_template(template_path: Path | None) -> dict[str, Any]:
    """Load a specified template or show the available presets."""
    if template_path:
        template = load_configuration(template_path.expanduser())
        _validate_template_shape(template)
        return template

    presets: list[tuple[str, Path]] = [("Default human aDNA", DEFAULT_TEMPLATE)]
    if PRESET_DIRECTORY.exists():
        presets.extend(
            (path.stem.replace("_", " "), path)
            for path in sorted(PRESET_DIRECTORY.glob("*.json"))
        )

    print("Choose a configuration preset:")
    for index, (label, _) in enumerate(presets, start=1):
        print(f"  {index}. {label}")
    print(f"  {len(presets) + 1}. Custom (only the mappings required by the current interface)")
    choice = _prompt_index("Choice", len(presets) + 1, default=1)

    if choice == len(presets) + 1:
        template = load_configuration(DEFAULT_TEMPLATE)
        _validate_template_shape(template)
        project = _choose_project_template(template)
        project["mapping"] = {}
        project["summaries_fields"] = {"individual": []}
        project["table_hierarchies"] = {}
        project["default_values"] = {}
        project["users"] = {}
        template["database"]["projects"] = [project]
        return template

    template = load_configuration(presets[choice - 1][1])
    _validate_template_shape(template)
    return template


def _validate_template_shape(template: dict[str, Any]) -> None:
    """Reject incomplete templates before starting the interactive setup."""
    database = template.get("database")
    if not isinstance(database, dict):
        raise ConfigurationError("The template must contain a 'database' object.")
    database = cast(dict[str, Any], database)
    projects_value = database.get("projects")
    if not isinstance(projects_value, list) or not projects_value:
        raise ConfigurationError("The template must contain at least one project.")
    projects = cast(list[Any], projects_value)
    first_project_value = projects[0]
    if not isinstance(first_project_value, dict):
        raise ConfigurationError("The first project in the template must be an object.")
    first_project = cast(dict[str, Any], first_project_value)
    if not isinstance(first_project.get("mapping", {}), dict):
        raise ConfigurationError("The first project's mapping must be an object.")


def _choose_project_template(template: dict[str, Any]) -> dict[str, Any]:
    """Select the template's first project as the basis for the new configuration."""
    database_value = template.get("database")
    if not isinstance(database_value, dict):
        raise ConfigurationError("The template does not contain a valid database section.")
    database = cast(dict[str, Any], database_value)
    projects_value = database.get("projects", [])
    if not isinstance(projects_value, list):
        raise ConfigurationError("database.projects must be a list.")
    projects = cast(list[Any], projects_value)
    if not projects or not isinstance(projects[0], dict):
        raise ConfigurationError("The template must contain at least one database.projects entry.")
    return copy.deepcopy(cast(dict[str, Any], projects[0]))


def _configure_mappings(
    template_mapping: dict[str, Any],
    inspector: Any,
    schema: str,
) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Let the user confirm preset mappings or replace them with actual columns."""
    tables = sorted(inspector.get_table_names(schema=schema))
    if not tables:
        raise ConfigurationError(f"Schema '{schema}' contains no tables.")

    mappings: dict[str, dict[str, str]] = {}
    summary_fields: list[str] = []
    mapping_fields = list(dict.fromkeys([*template_mapping, *sorted(REQUIRED_MAPPING_FIELDS)]))
    for field_name in mapping_fields:
        current_value = template_mapping.get(field_name, {})
        current = (
            cast(dict[str, Any], current_value)
            if isinstance(current_value, dict)
            else {}
        )
        default_table_value = current.get("base_table")
        default_column_value = current.get("column_name")
        default_table = default_table_value if isinstance(default_table_value, str) else None
        default_column = default_column_value if isinstance(default_column_value, str) else None
        required = field_name in REQUIRED_MAPPING_FIELDS

        print(f"\nMapping for Nawia concept '{field_name}'")
        selected_table = _choose_from_list(
            "Table",
            tables,
            default=default_table if default_table in tables else None,
            optional=not required,
        )
        if selected_table is None:
            continue

        columns = sorted(
            column["name"]
            for column in inspector.get_columns(selected_table, schema=schema)
        )
        selected_column = _choose_from_list(
            "Column",
            columns,
            default=default_column if default_column in columns else None,
            optional=False,
        )
        if selected_column is None:
            raise ConfigurationError(f"No column was selected for '{field_name}'.")
        mappings[field_name] = {
            "base_table": selected_table,
            "column_name": selected_column,
        }

    while _prompt_yes_no("\nVuoi aggiungere un altro campo personalizzato?", default=False):
        field_name = _prompt_text("Nome del concetto (es. extraction_batch)")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", field_name):
            print("Use only lowercase letters, numbers, and underscores; the name must start with a letter.")
            continue
        if field_name in mappings:
            print(f"Mapping '{field_name}' already exists.")
            continue

        table = _choose_from_list("Table", tables)
        if table is None:
            raise ConfigurationError("No table was selected.")
        columns = sorted(
            column["name"] for column in inspector.get_columns(table, schema=schema)
        )
        column = _choose_from_list("Column", columns)
        if column is None:
            raise ConfigurationError("No column was selected.")
        mappings[field_name] = {"base_table": table, "column_name": column}
        if _prompt_yes_no("Include this field in the individual summary?", default=False):
            summary_fields.append(field_name)

    return mappings, summary_fields


def _choose_schema(inspector: Any, template: dict[str, Any]) -> str:
    """Show schemas containing tables and suggest the template's schema."""
    system_schemas = {"information_schema", "pg_catalog"}
    schemas = [
        schema
        for schema in inspector.get_schema_names()
        if schema not in system_schemas and inspector.get_table_names(schema=schema)
    ]
    schemas.sort()
    if not schemas:
        raise ConfigurationError("No user schemas containing tables were found.")

    database = cast(dict[str, Any], template["database"])
    projects = cast(list[dict[str, Any]], database["projects"])
    preferred_value = projects[0].get("schema_name")
    preferred = preferred_value if isinstance(preferred_value, str) else None
    print("\nAvailable schemas:")
    selected_schema = _choose_from_list(
        "Schema",
        schemas,
        default=preferred if preferred in schemas else None,
    )
    if selected_schema is None:
        raise ConfigurationError("No schema was selected.")
    return selected_schema


def _choose_from_list(
    label: str,
    choices: list[str],
    default: str | None = None,
    optional: bool = False,
) -> str | None:
    """Prompt for a numbered choice, with a default and an optional skip."""
    if not choices:
        raise ConfigurationError(f"Nessuna opzione disponibile per '{label}'.")

    default_index = choices.index(default) + 1 if default in choices else None
    print(f"{label}:")
    for index, choice in enumerate(choices, start=1):
        suffix = " (preset)" if index == default_index else ""
        print(f"  {index}. {choice}{suffix}")
    if optional:
        print("  0. Do not map this field")

    while True:
        answer = input(
            f"Choice [{default_index if default_index is not None else ''}]: "
        ).strip()
        if not answer and default_index is not None:
            return choices[default_index - 1]
        if optional and answer == "0":
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(choices):
            return choices[int(answer) - 1]
        print("Enter the number of one of the listed options.")


def _prompt_text(label: str, default: str | None = None) -> str:
    """Read non-empty text, using the default if the user presses Enter."""
    while True:
        suffix = f" [{default}]" if default else ""
        answer = input(f"{label}{suffix}: ").strip()
        if answer:
            return answer
        if default:
            return str(default)
        print("This value is required.")


def _prompt_index(label: str, maximum: int, default: int) -> int:
    """Read a number between 1 and the displayed maximum."""
    while True:
        answer = input(f"{label} [{default}]: ").strip()
        if not answer:
            return default
        if answer.isdigit() and 1 <= int(answer) <= maximum:
            return int(answer)
        print(f"Enter a number between 1 and {maximum}.")


def _prompt_yes_no(label: str, default: bool) -> bool:
    """Read a yes/no answer and allow the default by pressing Enter."""
    suffix = "Y/n" if default else "y/N"
    while True:
        answer = input(f"{label} [{suffix}]: ").strip().lower()
        if not answer:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Answer y or n.")


def _connect_and_inspect(
    database: dict[str, Any],
    username: str,
    password: str,
) -> tuple[Any, Any]:
    """Open a PostgreSQL connection and return the SQLAlchemy engine and inspector."""
    try:
        from sqlalchemy import URL, create_engine, inspect, text
        from sqlalchemy.exc import SQLAlchemyError
    except ImportError as error:
        raise ConfigurationError(
            "SQLAlchemy is not installed. Install the project dependencies with 'pip install .'."
        ) from error

    database_type = str(database.get("type", "postgresql")).lower()
    if database_type not in {"postgresql", "postgres"}:
        raise ConfigurationError("Connection testing currently supports PostgreSQL only.")

    url = URL.create(
        "postgresql+psycopg2",
        username=username,
        password=password,
        host=database["host"],
        port=int(database["port"]),
        database=database["db_name"],
    )
    try:
        engine = create_engine(url)
    except ImportError as error:
        raise ConfigurationError(
            "The PostgreSQL driver is not installed. Install the project dependencies with 'pip install .'."
        ) from error
    except SQLAlchemyError as error:
        raise ConfigurationError(
            f"Impossibile inizializzare il driver PostgreSQL ({type(error).__name__})."
        ) from error

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        inspector = inspect(engine)
    except ImportError as error:
        engine.dispose()
        raise ConfigurationError(
            "The PostgreSQL driver is not installed. Install the project dependencies with 'pip install .'."
        ) from error
    except SQLAlchemyError as error:
        engine.dispose()
        raise ConfigurationError(
            f"Database connection failed ({type(error).__name__}). "
            "Check the host, port, database name, credentials, and network access."
        ) from error
    return engine, inspector


def _fail(message: str) -> NoReturn:
    """Print a readable CLI error and exit with a non-zero status."""
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(1)
