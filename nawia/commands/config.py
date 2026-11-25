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

# To do: add more presets. Human, mammals, multispecies, etc...
# To do: add the possibility of adding new projects without messing up the initialized one.

def add_parser(subparsers):
    """
    Register the configuration setup, edit, validation, and test commands.
    
    Input:
    - subparsers
    
    Output:
    - None.
    """
    # CONFIG PARSER
    config_parser = subparsers.add_parser(
        "config",
        help = "Create, edit, validate, or test the database configuration",
        description = "Guided setup, editing, and checks for Nawia's database configuration.",
    )

    config_commands = config_parser.add_subparsers(dest = "config_command", required = True)

    # INIT PARSER
    init_parser = config_commands.add_parser("init", help = "Create a configuration using a preset or a custom mapping",)

    # Arguments for the init parser
    init_parser.add_argument(
        "--output",
        type = Path,
        default = DEFAULT_CONFIG,
        help = f"Where to save the generated file (default: {DEFAULT_CONFIG}).",
    )

    init_parser.add_argument(
        "--template",
        type = Path,
        help = "Use this JSON template instead of choosing from the available presets (not recommended)",
    )

    init_parser.add_argument(
        "--force",
        action = "store_true",
        help = "Replace an existing output file after preserving it as .bak",
    )

    init_parser.set_defaults(func = run_init)

    # EDIT PARSER
    edit_parser = config_commands.add_parser(
        "edit",
        help="Edit selected sections of an existing configuration",
    )

    edit_parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help = f"Configuration file to edit (default: {DEFAULT_CONFIG})",
    )

    edit_parser.set_defaults(func = run_edit)

    # VALIDATE PARSER
    validate_parser = config_commands.add_parser(
        "validate",
        help = "Check configuration structure without connecting to the database",
    )

    validate_parser.add_argument(
        "--config",
        type = Path,
        default=  DEFAULT_CONFIG,
        help = f"Configuration file to check (default: {DEFAULT_CONFIG})",
    )

    validate_parser.set_defaults(func = run_validate)

    # TEST PARSER
    test_parser = config_commands.add_parser(
        "test",
        help = "Test database access and verify mapped tables, columns, and foreign keys",
    )

    test_parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help = f"Configuration file to test (default: {DEFAULT_CONFIG})",
    )

    test_parser.add_argument(
        "--project",
        help = "Project name or schema name when the file contains multiple projects",
    )

    test_parser.add_argument(
        "--username",
        help="Database username (the password is always requested privately)",
    )

    test_parser.set_defaults(func = run_test)


def run_validate(args):
    """
    Run local checks without connecting to the database or modifying files.
    
    Input:
    - args

    Output:
    - None.
    """

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


def run_test(args):
    """
    Test the database connection and verify mappings and relationships.
    """

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
    """
    Guide the user through configuring Nawia for an existing database schema.

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
    print("Enter the database server's host and port. Use localhost only when the "
        "database or an SSH tunnel is running on the same machine as Nawia.")
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


def run_edit(args):
    """
    Edit selected parts of an existing config while preserving the rest.
    """
    config_path = args.config.expanduser()
    try:
        original = load_configuration(config_path)
    except ConfigurationError as error:
        _fail(str(error))

    existing_errors = validate_configuration(original)
    if existing_errors:
        print("Fix the existing configuration errors before editing:")
        for error in existing_errors:
            print(f"- {error}")
        raise SystemExit(1)

    database_value = original.get("database")
    if not isinstance(database_value, dict):
        _fail("The configuration must contain a valid 'database' object.")
    database = cast(dict[str, Any], database_value)
    projects_value = database.get("projects")
    if not isinstance(projects_value, list) or not projects_value or any(
        not isinstance(project, dict) for project in projects_value
    ):
        _fail("The configuration must contain at least one valid project.")

    config = copy.deepcopy(original)
    selected_sections = _choose_edit_sections()
    if not selected_sections:
        print("No sections selected. The configuration was not changed.")
        return

    if "connection" in selected_sections:
        print(
            "\nWARNING: Changing these settings can point Nawia to a different "
            "database. This may change which data you view or edit."
        )
        if not _prompt_yes_no("Continue with database connection changes?", default=False):
            selected_sections.remove("connection")
    if not selected_sections:
        print("No changes were made.")
        return

    database_config = cast(dict[str, Any], config["database"])
    if "connection" in selected_sections:
        database_config["host"] = _prompt_text(
            "Database host",
            str(database_config.get("host") or ""),
        )
        port = _prompt_text(
            "Database port",
            str(database_config.get("port") or "5432"),
        )
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            _fail("The port must be a number between 1 and 65535.")
        database_config["port"] = port
        database_config["db_name"] = _prompt_text(
            "Database name",
            str(database_config.get("db_name") or ""),
        )

    project_sections = {
        "project",
        "mappings",
        "summaries",
        "hierarchies",
        "defaults_users",
    }
    if project_sections.intersection(selected_sections):
        try:
            project = _choose_project_for_edit(
                cast(list[dict[str, Any]], database_config["projects"])
            )
            if "project" in selected_sections:
                project["name"] = _prompt_text(
                    "Project name",
                    str(project.get("name") or ""),
                )
                project["schema_name"] = _prompt_text(
                    "Database schema",
                    str(project.get("schema_name") or ""),
                )
                project["backups_path"] = _prompt_optional_text(
                    "Backup directory",
                    str(project.get("backups_path") or ""),
                )
            if "mappings" in selected_sections:
                _edit_mappings(project)
            if "summaries" in selected_sections:
                _edit_summary_fields(project)
            if "hierarchies" in selected_sections:
                _edit_hierarchies(project)
            if "defaults_users" in selected_sections:
                _edit_defaults_and_users(project)
        except ConfigurationError as error:
            _fail(str(error))

    errors = validate_configuration(config)
    if errors:
        print("The edited configuration failed validation:")
        for error in errors:
            print(f"- {error}")
        raise SystemExit(1)

    if config == original:
        print("No changes were made.")
        return

    try:
        backup_path = save_configuration(config, config_path, overwrite=True)
    except ConfigurationError as error:
        _fail(str(error))

    print(f"Configuration saved to: {config_path.resolve()}")
    if backup_path:
        print(f"Previous configuration backed up to: {backup_path}")
    print("Run 'nawia config test' to verify the database connection and project schema.")


def _choose_edit_sections() -> list[str]:
    sections = [
        ("connection", "Database connection (host, port, database name)"),
        ("project", "Project details (name, schema, backup directory)"),
        ("mappings", "Field mappings"),
        ("summaries", "Summary fields"),
        ("hierarchies", "Table relationships and hierarchies"),
        ("defaults_users", "Default values and user filename parsing"),
    ]
    print("Select the sections to edit. Unselected sections will be kept unchanged:")
    for index, (_, label) in enumerate(sections, start=1):
        print(f"  {index}. {label}")
    print("  0. Cancel")

    while True:
        answer = input("Sections (comma-separated numbers): ").strip()
        if answer == "0":
            return []
        try:
            indexes = [int(value.strip()) for value in answer.split(",")]
        except ValueError:
            indexes = []
        if (
            indexes
            and len(indexes) == len(set(indexes))
            and all(1 <= index <= len(sections) for index in indexes)
        ):
            return [sections[index - 1][0] for index in indexes]
        print("Enter one or more listed numbers separated by commas, or 0 to cancel.")


def _choose_project_for_edit(projects: list[dict[str, Any]]) -> dict[str, Any]:
    if len(projects) == 1:
        return projects[0]

    choices = [
        f"{project.get('name', '(unnamed)')} [{project.get('schema_name', '(no schema)')}]"
        for project in projects
    ]
    selected = _choose_from_list("Project", choices)
    if selected is None:
        raise ConfigurationError("No project was selected.")
    return projects[choices.index(selected)]


def _prompt_optional_text(label: str, current: str) -> str:
    answer = input(f"{label} [{current or 'empty'}] (enter '-' to clear): ").strip()
    if answer == "-":
        return ""
    return answer or current


def _edit_mappings(project: dict[str, Any]) -> None:
    mappings_value = project.get("mapping")
    if not isinstance(mappings_value, dict):
        raise ConfigurationError("The project's mapping must be an object.")
    mappings = cast(dict[str, Any], mappings_value)

    while True:
        action = _prompt_index("Mappings: 1=add, 2=edit, 3=remove, 4=done", 4, 4)
        if action == 4:
            return
        if action == 1:
            field_name = _prompt_text("New mapping name")
            if not re.fullmatch(r"[a-z][a-z0-9_]*", field_name):
                print("Use lowercase letters, numbers, and underscores; start with a letter.")
                continue
            if field_name in mappings:
                print(f"Mapping '{field_name}' already exists.")
                continue
        else:
            if not mappings:
                print("There are no mappings to edit or remove.")
                continue
            field_name = cast(
                str,
                _choose_from_list("Mapping", sorted(mappings)),
            )
            if action == 3:
                if field_name in REQUIRED_MAPPING_FIELDS:
                    print(f"Required mapping '{field_name}' cannot be removed.")
                    continue
                del mappings[field_name]
                summaries = project.get("summaries_fields")
                if isinstance(summaries, dict):
                    for summary_name, summary_fields in summaries.items():
                        if isinstance(summary_fields, list):
                            summaries[summary_name] = [
                                value for value in summary_fields if value != field_name
                            ]
                print(f"Removed mapping '{field_name}'.")
                continue

        current_value = mappings.get(field_name, {})
        current = current_value if isinstance(current_value, dict) else {}
        mappings[field_name] = {
            "base_table": _prompt_text(
                "Table",
                str(current.get("base_table") or ""),
            ),
            "column_name": _prompt_text(
                "Column",
                str(current.get("column_name") or ""),
            ),
        }


def _edit_summary_fields(project: dict[str, Any]) -> None:
    mapping_value = project.get("mapping")
    if not isinstance(mapping_value, dict) or not mapping_value:
        raise ConfigurationError("Add at least one mapping before editing summary fields.")
    mapping_names = sorted(cast(dict[str, Any], mapping_value))
    summaries_value = project.get("summaries_fields")
    summaries = (
        cast(dict[str, Any], summaries_value)
        if isinstance(summaries_value, dict)
        else {}
    )
    current_value = summaries.get("individual", [])
    current = (
        [value for value in current_value if isinstance(value, str)]
        if isinstance(current_value, list)
        else []
    )

    print("Available mappings for the individual summary:")
    for index, name in enumerate(mapping_names, start=1):
        marker = " (currently selected)" if name in current else ""
        print(f"  {index}. {name}{marker}")
    print("Enter comma-separated numbers, press Enter to keep the current selection, or type 'none'.")
    while True:
        answer = input("Summary fields: ").strip()
        if not answer:
            selected = current
            break
        if answer.lower() == "none":
            selected = []
            break
        try:
            indexes = [int(value.strip()) for value in answer.split(",")]
        except ValueError:
            indexes = []
        if (
            indexes
            and len(indexes) == len(set(indexes))
            and all(1 <= index <= len(mapping_names) for index in indexes)
        ):
            selected = [mapping_names[index - 1] for index in indexes]
            break
        print("Enter valid mapping numbers, or type 'none'.")
    summaries["individual"] = selected
    project["summaries_fields"] = summaries


def _edit_hierarchies(project: dict[str, Any]) -> None:
    hierarchy_value = project.get("table_hierarchies")
    if not isinstance(hierarchy_value, dict):
        raise ConfigurationError("The project's table_hierarchies must be an object.")
    hierarchies = cast(dict[str, Any], hierarchy_value)

    while True:
        action = _prompt_index("Hierarchies: 1=add, 2=edit, 3=remove, 4=done", 4, 4)
        if action == 4:
            return
        if action == 1:
            child = _prompt_text("Child table")
            if child in hierarchies:
                print(f"A hierarchy for '{child}' already exists.")
                continue
            current: dict[str, Any] = {}
        else:
            if not hierarchies:
                print("There are no hierarchies to edit or remove.")
                continue
            child = cast(str, _choose_from_list("Child table", sorted(hierarchies)))
            if action == 3:
                del hierarchies[child]
                print(f"Removed hierarchy for '{child}'.")
                continue
            current_value = hierarchies[child]
            current = current_value if isinstance(current_value, dict) else {}

        hierarchies[child] = {
            "parent": _prompt_text("Parent table", str(current.get("parent") or "")),
            "join_on": _prompt_text("Join column", str(current.get("join_on") or "")),
        }


def _edit_defaults_and_users(project: dict[str, Any]) -> None:
    _edit_string_entries(
        "Default values",
        project,
        "default_values",
    )
    _edit_user_parsers(project)


def _edit_string_entries(
    label: str,
    project: dict[str, Any],
    key: str,
) -> None:
    entries_value = project.get(key)
    if not isinstance(entries_value, dict):
        raise ConfigurationError(f"The project's {key} must be an object.")
    entries = cast(dict[str, Any], entries_value)

    while True:
        action = _prompt_index(f"{label}: 1=add, 2=edit, 3=remove, 4=done", 4, 4)
        if action == 4:
            return
        if action == 1:
            entry_name = _prompt_text(f"New {label.lower()} key")
            if entry_name in entries:
                print(f"'{entry_name}' already exists.")
                continue
        else:
            if not entries:
                print(f"There are no {label.lower()} entries to edit or remove.")
                continue
            entry_name = cast(str, _choose_from_list(label, sorted(entries)))
            if action == 3:
                del entries[entry_name]
                print(f"Removed '{entry_name}'.")
                continue
        current_value = entries.get(entry_name, "")
        answer = input(
            f"Value for '{entry_name}' [{current_value}]: "
        ).strip()
        entries[entry_name] = "" if answer == "-" else answer or current_value


def _edit_user_parsers(project: dict[str, Any]) -> None:
    users_value = project.get("users")
    if not isinstance(users_value, dict):
        raise ConfigurationError("The project's users must be an object.")
    users = cast(dict[str, Any], users_value)

    while True:
        action = _prompt_index("User filename rules: 1=add, 2=edit, 3=remove, 4=done", 4, 4)
        if action == 4:
            return
        if action == 1:
            username = _prompt_text("New username rule")
            if username in users:
                print(f"A filename rule for '{username}' already exists.")
                continue
            current: dict[str, Any] = {}
        else:
            if not users:
                print("There are no user filename rules to edit or remove.")
                continue
            username = cast(str, _choose_from_list("Username rule", sorted(users)))
            if action == 3:
                del users[username]
                print(f"Removed filename rule for '{username}'.")
                continue
            current_value = users[username]
            current = current_value if isinstance(current_value, dict) else {}

        while True:
            regex = input(f"Filename regular expression [{current.get('regex', '')}]: ").strip()
            regex = (
                ""
                if regex == "-"
                else regex or str(current.get("regex", ""))
            )
            try:
                re.compile(regex)
                break
            except re.error as error:
                print(f"Invalid regular expression: {error}")
        groups_value = current.get("groups", [])
        groups_default = ", ".join(
            value for value in groups_value if isinstance(value, str)
        ) if isinstance(groups_value, list) else ""
        groups_answer = input(
            f"Capture groups, comma-separated [{groups_default}] (or 'none' to clear): "
        ).strip()
        if groups_answer.lower() == "none":
            groups = []
        elif groups_answer:
            groups = [value.strip() for value in groups_answer.split(",") if value.strip()]
        elif isinstance(groups_value, list):
            groups = [value for value in groups_value if isinstance(value, str)]
        else:
            groups = []
        users[username] = {"regex": regex, "groups": groups}


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

    while _prompt_yes_no("\nDo you want to add a new custom field?", default=False):
        field_name = _prompt_text("Field name (ex. extraction_batch)")
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
        raise ConfigurationError(f"No option available for '{label}'.")

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
            f"Could not initialize the PostgreSQL driver ({type(error).__name__})."
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
