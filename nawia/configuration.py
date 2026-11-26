from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, cast

# Add here required mapping fields, but keep it minimal
REQUIRED_MAPPING_FIELDS = {
    "individual_id",
    "coord_ns",
    "coord_we",
    "project_id",
    "material_description",
}

class ConfigurationError(ValueError):
    """
    Display error messages during configuration.
    """


def default_configuration_path() -> Path:
    """Return the active config path, honoring explicit and existing local configs."""
    configured_path = os.environ.get("NAWIA_CONFIG", "").strip()
    if configured_path:
        return Path(configured_path).expanduser()

    working_directory_config = Path.cwd() / "config.json"
    if working_directory_config.is_file():
        return working_directory_config

    if os.name == "nt":
        app_data = os.environ.get("APPDATA")
        if app_data:
            return Path(app_data).expanduser() / "Nawia" / "config.json"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Nawia" / "config.json"

    config_home = os.environ.get("XDG_CONFIG_HOME")
    base_path = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return base_path / "nawia" / "config.json"


def default_data_directory() -> Path:
    """Return Nawia's writable per-user data directory."""
    if os.name == "nt":
        app_data = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if app_data:
            return Path(app_data).expanduser() / "Nawia"
        return Path.home() / "AppData" / "Local" / "Nawia"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Nawia"

    data_home = os.environ.get("XDG_DATA_HOME")
    base_path = Path(data_home).expanduser() if data_home else Path.home() / ".local" / "share"
    return base_path / "nawia"


def backup_root_path(configured_path: str | os.PathLike[str] | None = None) -> Path:
    """Resolve a configured backup path or use Nawia's writable data directory."""
    configured_value = str(configured_path).strip() if configured_path is not None else ""
    if configured_value:
        backup_root = Path(configured_value).expanduser()
        if not backup_root.is_absolute():
            backup_root = default_configuration_path().expanduser().resolve().parent / backup_root
        return backup_root

    return default_data_directory() / "backups"


def packaged_configuration_path() -> Path:
    """Return the default configuration bundled inside the Nawia package."""
    return Path(__file__).resolve().parent / "resources" / "config.json"


def _as_string_keyed_mapping(value):
    """
    Recognises if an element is of Mapping type or not.

    Input:
    - value: any object
    
    Output:
    - an object of class Mapping, or None.
    """

    if isinstance(value, Mapping) and all(
        isinstance(key, str) for key in value.keys()
    ):
        return value
    return None


def load_configuration(path):
    """
    Loads a Json file and highlights missing or malformed fields.

    Input: 
    - path to a json file.

    Output:
    - a dictionary with the config file info, if everything is okay. Raises errors if there are problems.
    """
    try:
        with path.open("r", encoding="utf-8-sig") as config_file:
            config = json.load(config_file)
    
    except FileNotFoundError as error:
        raise ConfigurationError(f"Configuration file not found: {path}") from error
    
    except json.JSONDecodeError as error:
        raise ConfigurationError(
            f"Json not valid {path}, row {error.lineno}, column {error.colno}: {error.msg}"
        ) from error
    
    except OSError as error:
        raise ConfigurationError(f"Impossible reading json file {path}: {error}") from error

    if not isinstance(config, dict):
        raise ConfigurationError("Json not correctly formatted: impossible creating a Python dictionary out of it.")
    
    return config


def validate_configuration(config):
    """
    Checks configuration, without connecting to the remote database. This simply checks if Nawia is able to recognise the fileds.
    Does not show if tables nor columns actually exist: to do so, use ``nawia config test``.

    Input:
    - a Python dictionary (obtained from the json config file)

    Output:
    - return any errors (so it's good if it doesn't return anything ;D)
    """
    # Here we keep track of all errors:
    errors = []
    database = _as_string_keyed_mapping(config.get("database"))

    # Condition 0: is there even a database configuration?
    if database is None:
        return ["No object 'database' found in the config file."]

    # Condition 1: essential keys to establish connection.
    for key in ("db_name", "host", "port"):
        if not str(database.get(key, "")).strip():
            errors.append(f"'database.{key}' is mandatory.")

    # Condition 2: type of database.
    database_type = str(database.get("type", "postgresql")).lower()
    if database_type not in {"postgresql", "postgres"}:
        errors.append(f"Type of database '{database_type}' not supported: types of supported databases are PostgreSQL and mySQL.")

    # Condition 3: port. Aribitrary number, but still necessary!
    port = database.get("port")
    if port and (not str(port).isdigit() or not 1 <= int(port) <= 65535):
        errors.append("'database.port' must be an integer between 1 and 65535.")

    # Condition 4: project availability. Remember: you can configure as many projects as you want... but at least one!
    projects = database.get("projects")
    if not isinstance(projects, list) or not projects:
        return errors + ["'database.projects' must be a list containing at least one object."]

    # Condition 5: projects' names and schemas.
    seen_names = set()
    seen_schemas = set()
    for index, project_value in enumerate(projects):
        prefix = f"database.projects[{index}]"
        project = _as_string_keyed_mapping(project_value)
        
        if project is None:
            errors.append(f"'{prefix}' must be an object.")
            continue

        name = str(project.get("name", "")).strip()
        schema = str(project.get("schema_name", "")).strip()

        # Duplicated names are not allowed because Nawia will not be able to extract the correct infomation.
        if not name:
            errors.append(f"'{prefix}.name' is mandatory.")
        elif name in seen_names:
            errors.append(f"Duplicated project name: '{name}'.")
        else:
            seen_names.add(name)

        # Schema name also must be unique (even though, it can be the same as the project name). This is more
        # of an internal rule of SQL databases!
        if not schema:
            errors.append(f"'{prefix}.schema_name' is mandatory.")
        elif schema in seen_schemas:
            errors.append(f"Schema configured more than once: '{schema}'.")
        else:
            seen_schemas.add(schema)

        # Mapping. Nawia is not smart enough to understand that your column called "Mouse ID" is the ID of the sample.
        # Please map the necessary values and make sure they follow the necessary constraints.
        # We tried to keep them to a minimum, but unfortunately some strictness was necessary: please refer to documentation.
        mapping_value = _as_string_keyed_mapping(project.get("mapping"))
        if mapping_value is None:
            errors.append(f"'{prefix}.mapping' must be an object.")
        mapping = mapping_value if mapping_value is not None else {}

        for required_field in sorted(REQUIRED_MAPPING_FIELDS):
            if required_field not in mapping:
                errors.append(
                    f"Mandatory mapping field missing: '{prefix}.mapping.{required_field}'."
                )

        for field_name, field_mapping in mapping.items():
            field_prefix = f"{prefix}.mapping.{field_name}"
            field_mapping_object = _as_string_keyed_mapping(field_mapping)
            if field_mapping_object is None:
                errors.append(f"'{field_prefix}' must be an object.")
                continue
            for key in ("base_table", "column_name"):
                if not str(field_mapping_object.get(key, "")).strip():
                    errors.append(f"'{field_prefix}.{key}' is mandatory.")

        individual_mapping = _as_string_keyed_mapping(mapping.get("individual_id"))
        project_mapping = _as_string_keyed_mapping(mapping.get("project_id"))
        if individual_mapping is not None and project_mapping is not None:
            if project_mapping.get("base_table") != individual_mapping.get("base_table"):
                errors.append(
                    f"'{prefix}.mapping.project_id.base_table' must coincide with "
                    "individual_id: the Project Overview page assumes they come from the same table. "
                    "We are currently working on this to be less strict."
                )
        coord_ns_mapping = _as_string_keyed_mapping(mapping.get("coord_ns"))
        coord_we_mapping = _as_string_keyed_mapping(mapping.get("coord_we"))
        if coord_ns_mapping is not None and coord_we_mapping is not None:
            if coord_ns_mapping.get("base_table") != coord_we_mapping.get("base_table"):
                errors.append(
                    f"'{prefix}.mapping.coord_ns' and coord_we must be in the same table."
                )

        summaries = _as_string_keyed_mapping(project.get("summaries_fields", {}))
        if summaries is None:
            errors.append(f"'{prefix}.summaries_fields' must be an object.")
        else:
            individual_fields = summaries.get("individual", [])
            if not isinstance(individual_fields, list):
                errors.append(f"'{prefix}.summaries_fields.individual' must be a list.")
            else:
                for field_name in cast(list[Any], individual_fields):
                    if not isinstance(field_name, str):
                        errors.append(
                            f"'{prefix}.summaries_fields.individual' must only contain strings"
                        )
                        continue
                    if field_name not in mapping:
                        errors.append(
                            f"'{prefix}.summaries_fields.individual' quotes "
                            f"'{field_name}', which doesn't exist in the mapping."
                        )

        hierarchies = _as_string_keyed_mapping(project.get("table_hierarchies", {}))
        if hierarchies is None:
            errors.append(f"'{prefix}.table_hierarchies' must be an object.")
        else:
            for child, relationship in hierarchies.items():
                relationship_prefix = f"{prefix}.table_hierarchies.{child}"
                relationship_object = _as_string_keyed_mapping(relationship)
                if relationship_object is None:
                    errors.append(f"'{relationship_prefix}' must be an object.")
                    continue
                if not str(relationship_object.get("parent", "")).strip():
                    errors.append(f"'{relationship_prefix}.parent' is mandatory.")
                join_on = relationship_object.get("join_on")
                if not isinstance(join_on, str) or not join_on.strip():
                    errors.append(
                        f"'{relationship_prefix}.join_on' must be a non empty string."
                    )

        defaults = _as_string_keyed_mapping(project.get("default_values", {}))
        if defaults is None:
            errors.append(f"'{prefix}.default_values' must be an object.")

        users = _as_string_keyed_mapping(project.get("users", {}))
        if users is None:
            errors.append(f"'{prefix}.users' must be an object.")
        else:
            for username, user_config in users.items():
                user_prefix = f"{prefix}.users.{username}"
                user_config_object = _as_string_keyed_mapping(user_config)
                if user_config_object is None:
                    errors.append(f"'{user_prefix}' must be an object.")
                    continue
                regex = user_config_object.get("regex", "")
                if not isinstance(regex, str):
                    errors.append(f"'{user_prefix}.regex' must be a string.")
                elif regex:
                    try:
                        re.compile(regex)
                    except re.error as error:
                        errors.append(f"Regex not valid found in '{user_prefix}.regex': {error}.")
                groups = user_config_object.get("groups", [])
                if not isinstance(groups, list) or any(
                    not isinstance(group, str) for group in cast(list[Any], groups)
                ):
                    errors.append(f"'{user_prefix}.groups' must be a list of strings.")

    return errors


def find_project(config, project_name):
    """
    Returns the required project's configuration.

    Input:
    - config: the json configuration file, converted in a Python Mapping object.
    - project_name: the required project name. If none is given, returns the only one present in the json file.

    Returns:
    - a dictionary with all information fields relative to a specific project.
    """
    database = _as_string_keyed_mapping(config.get("database"))
    projects_value = database.get("projects") if database is not None else None
    
    if not isinstance(projects_value, list):
        raise ConfigurationError("The configuration file does not have a valid database.projects field.")
    
    projects = list(projects_value)
    
    if project_name is None:
        if len(projects) != 1:
            raise ConfigurationError("Configuration file contains more than one project: specify it with --project.")
        
        selected_project = _as_string_keyed_mapping(projects[0])
        
        if selected_project is None:
            raise ConfigurationError("The project given is not valid.")
        
        return selected_project

    for project_value in projects:
        project = _as_string_keyed_mapping(project_value)
        if project and (project.get("name") == project_name or project.get("schema_name") == project_name):
            return project
    raise ConfigurationError(f"Project or schema '{project_name}' not found in the config file.")


def validate_project_schema(project, inspector):
    """
    Compares mapping and the true database. This must open a connection.

    Input:
    - project: an object. Will be checked to see if it is associated with a project info in the config file.
    - inspector: an object of type "inspector". It is able to fetch information about an SQL database.

    Output:
    - a pass if everything is alright.
    """
    errors = []
    warnings = []
    project_object = _as_string_keyed_mapping(project)

    if project_object is None:
        return ["The project configuration is not valid."], warnings
    
    schema_value = project_object.get("schema_name")
    if not isinstance(schema_value, str) or not schema_value:
        return ["The project does not specify a valid schema name, required for parsing an SQL database"], warnings
    
    schema = schema_value

    schemas = set(inspector.get_schema_names())
    if schema not in schemas:
        return [f"The schema '{schema}' does not exist in the database."], warnings

    tables = set(inspector.get_table_names(schema=schema))
    columns_by_table = {}
    
    for table in tables:
        columns_by_table[table] = {
            column["name"]
            for column_value in inspector.get_columns(table, schema=schema)
            if (column := _as_string_keyed_mapping(column_value))
            and isinstance(column.get("name"), str)
        }

    # Here we check for connections between tables. This is checked for SQL compliance: remember that it is quite of a
    # strict structure, with hierarchies and columns in table A that recall columns in table B. Nawia tries to facilitate
    # this as much as possible, but it is still worth to check SQL syntax, to avoid errors.
    graph = {table: set() for table in tables}
    foreign_keys = set()
    edge_constraints = {}
    
    for child in tables:
        for foreign_key_value in inspector.get_foreign_keys(child, schema=schema):
            foreign_key = _as_string_keyed_mapping(foreign_key_value)
            
            if foreign_key is None:
                continue
            
            parent_value = foreign_key.get("referred_table")
            parent = parent_value if isinstance(parent_value, str) else None
            referred_schema_value = foreign_key.get("referred_schema")
            
            referred_schema = (
                referred_schema_value
                if isinstance(referred_schema_value, str)
                else schema)
            
            child_columns_value = foreign_key.get("constrained_columns")
            parent_columns_value = foreign_key.get("referred_columns")
            
            child_columns = (
                [column for column in cast(list[Any], child_columns_value) if isinstance(column, str)]
                if isinstance(child_columns_value, list)
                else [] )
            
            parent_columns = (
                [column for column in cast(list[Any], parent_columns_value) if isinstance(column, str)]
                if isinstance(parent_columns_value, list)
                else [])
            
            if (referred_schema != schema
                or parent is None or parent not in tables
                or not child_columns or not parent_columns
                or len(child_columns) != len(parent_columns)):
                continue
            
            graph[child].add(parent)
            graph[parent].add(child)
            constraint = tuple(zip(child_columns, parent_columns))
           
            edge_constraints.setdefault((child, parent), set()).add(constraint)
            
            edge_constraints.setdefault((parent, child), set()).add(
                tuple((parent_column, child_column) for child_column, parent_column in constraint))
           
            foreign_keys.update(
                (child, child_column, parent, parent_column)
                for child_column, parent_column in zip(child_columns, parent_columns))

    for (child, parent), constraints in edge_constraints.items():
        if len(constraints) > 1:
            warnings.append(
                f"There are several Foreign Keys (FK) between child table '{child}' and parent table '{parent}'; "
                " the current query builder might choose a suboptimal join condition.")

    mapping = _as_string_keyed_mapping(project_object.get("mapping"))
    
    if mapping is None:
        return ["The project does not contain a valid mapping."], warnings
    individual_mapping = _as_string_keyed_mapping(mapping.get("individual_id"))
    
    if individual_mapping is None:
        return ["Missing the mapping for individual_id. (Very important!)"], warnings
    base_table_value = individual_mapping.get("base_table")
    identifier_column_value = individual_mapping.get("column_name")
    
    if not isinstance(base_table_value, str) or not isinstance(identifier_column_value, str):
        return ["Mapping on individual_id must also contain the name of the table and column. Where is it located?."], warnings
    base_table = base_table_value
    if base_table not in tables:
        errors.append(f"The table '{base_table}' set for mapping individual_id does not exist.")

    for field_name, field_mapping_value in mapping.items():
        field_mapping = _as_string_keyed_mapping(field_mapping_value)
        
        if field_mapping is None:
            errors.append(f"Mapping '{field_name}': it is not an object.")
            continue
        
        table_value = field_mapping.get("base_table")
        column_value = field_mapping.get("column_name")
        if not isinstance(table_value, str) or not isinstance(column_value, str):
            errors.append(f"Mapping '{field_name}': table or column not defined. Nawia has no idea where this field is.")
            continue
        
        table = table_value
        column = column_value
        if table not in tables:
            errors.append(f"Mapping '{field_name}': table '{table}' not found in the schema '{schema}'.")
            continue
        
        if column not in columns_by_table[table]:
            errors.append(f"Mapping '{field_name}': column '{table}.{column}' non found.")
            continue
        
        if table != base_table:
            path_count = _shortest_path_count(graph, base_table, table)
            if path_count == 0:
                errors.append(f"Mapping '{field_name}': no Foreign Key links '{base_table}' to '{table}'.")
            elif path_count > 1:
                warnings.append(
                    f"Mapping '{field_name}': there are {path_count} shorter paths "
                    f"between '{base_table}' and '{table}'; check that the automatic join is what it was expected.")

    identifier_column = identifier_column_value
    
    for field_name in ("coord_ns", "material_description"):
        
        field_mapping = _as_string_keyed_mapping(mapping.get(field_name))
        field_table = field_mapping.get("base_table") if field_mapping else None
        
        if isinstance(field_table, str) and field_table in columns_by_table:
            if identifier_column not in columns_by_table[field_table]:
                errors.append(
                    f"Mapping '{field_name}': the table '{field_table}' "
                    f"must contain '{identifier_column}', which connects it to the individuals' information.")

    # Here stuff can get a little bit more complicated. Please refer to the documentation for an explanation on
    # what hierarchies are and what Nawia expects from the user. Remember: if you don't want to set up all of this
    # yourself and you want to save yourself an headache, use one of the default templates... and add missing info!
    hierarchies = _as_string_keyed_mapping(project_object.get("table_hierarchies", {})) or {}
    
    for child, relationship_value in hierarchies.items():
        relationship = _as_string_keyed_mapping(relationship_value)
        if relationship is None:
            errors.append(f"Hierarchy defined for table '{child}' is not valid.")
            continue
        
        parent_value = relationship.get("parent")
        join_column_value = relationship.get("join_on")
        
        if not isinstance(parent_value, str) or not isinstance(join_column_value, str):
            errors.append(f"Hierarchy defined for table '{child}': parent table or join_on column not defined.")
            continue
        
        parent = parent_value
        join_column = join_column_value
        if child not in tables:
            errors.append(f"Hierarchy: child table '{child}' not found.")
            continue
        if parent not in tables:
            errors.append(f"Hierarchy of child table '{child}': parent table '{parent}' not found.")
            continue
        if join_column not in columns_by_table[child]:
            errors.append(f"Hierarchy of child table '{child}': column '{join_column}' not found in child table.")
            continue
        if join_column not in columns_by_table[parent]:
            errors.append(f"Hierarchy of child table '{child}': column '{join_column}' not found on parent table '{parent}'.")
            continue

        primary_key_info = _as_string_keyed_mapping(inspector.get_pk_constraint(parent, schema=schema)) or {}
        primary_key_columns_value = primary_key_info.get("constrained_columns", [])
        
        primary_key_columns = (primary_key_columns_value if isinstance(primary_key_columns_value, list) else [])
        
        if join_column not in primary_key_columns:
            errors.append(
                f"Hierarchy '{child}': '{parent}.{join_column}' is not a Primary Key; "
                "update through Excel currently checks Primary Keys to define the connection between tables."
            )
        if (child, join_column, parent, join_column) not in foreign_keys:
            errors.append(
                f"Hierarchy '{child}' -> '{parent}' does not corresponds to a real Foreign Key "
                f"on '{join_column}'."
            )

    if not hierarchies:
        warnings.append(
            "There are no table hierarchies defined: uploading through Excel won't automatically fill up "
            "the missing fields in parent tables. If such hierarchies in your SQL schema exist and will not be "
            "respected, this will break data upload! Thread carefully."
        )
    return errors, warnings


def _shortest_path_count(graph, start, target):
    """
    Counts the number of shortest paths between two tables.

    Input:
    - graph: Connection graph between tables built using breadth-first search (BFS) algorithm.
    - start: starting table name.
    - target: target table name.

    Output:
    - an integer defining the number of shortest paths.
    """
    if start == target:
        return 1

    distances = {start: 0}
    path_counts = {start: 1}
    pending = [start]
    while pending:
        current = pending.pop(0)
        
        for neighbor in graph.get(current, set()):
            next_distance = distances[current] + 1
            
            if neighbor not in distances:
                distances[neighbor] = next_distance
                path_counts[neighbor] = path_counts[current]
                pending.append(neighbor)
            
            elif distances[neighbor] == next_distance:
                path_counts[neighbor] += path_counts[current]
    
    return path_counts.get(target, 0)


CONFIGURATION_BACKUP_LIMIT = 3


def _create_configuration_backup(path: Path) -> Path:
    backup_prefix = f"{path.name}.bak"
    backups: list[tuple[int, Path]] = []
    try:
        for candidate in path.parent.iterdir():
            if not candidate.is_file() or not candidate.name.startswith(backup_prefix):
                continue
            suffix = candidate.name[len(backup_prefix):]
            if not suffix:
                index = 0
            elif suffix.startswith(".") and suffix[1:].isdigit():
                index = int(suffix[1:])
            else:
                continue
            backups.append((index, candidate))
    except OSError as error:
        raise ConfigurationError(
            f"Could not inspect existing configuration backups: {error}"
        ) from error
    backups.sort(key=lambda item: item[0])

    staged_backup = None
    rotation_started = False
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".bak.tmp",
            delete=False,
        ) as temporary_file:
            staged_backup = Path(temporary_file.name)
        shutil.copy2(path, staged_backup)

        retained_backups = backups[: CONFIGURATION_BACKUP_LIMIT - 1]
        stale_backups = backups[CONFIGURATION_BACKUP_LIMIT - 1 :]
        rotation_started = True
        for _, stale_backup in stale_backups:
            stale_backup.unlink()
        for index in range(len(retained_backups) - 1, -1, -1):
            source = retained_backups[index][1]
            destination = path.with_suffix(path.suffix + f".bak.{index + 1}")
            if source != destination:
                os.replace(source, destination)

        backup_path = path.with_suffix(path.suffix + ".bak")
        os.replace(staged_backup, backup_path)
        return backup_path
    except OSError as error:
        if staged_backup is not None and staged_backup.exists():
            if rotation_started:
                raise ConfigurationError(
                    f"Could not rotate configuration backups ({error}). "
                    f"The new backup is preserved at {staged_backup}."
                ) from error
            try:
                staged_backup.unlink()
            except OSError as cleanup_error:
                raise ConfigurationError(
                    f"Could not create a configuration backup ({error}); "
                    f"the temporary backup remains at {staged_backup} "
                    f"({cleanup_error})."
                ) from error
        raise ConfigurationError(f"Could not create a configuration backup: {error}") from error


def save_configuration(config, path, overwrite = False):
    """
    Writes a Json configuration file. Saves the current one as a backup.

    Input:
    - config: Python object of class Mapping defining the configuration of your database.
    - path: path to the new configuration file.
    - overwrite: Boolean, fetermines if the new configuration file will overwrite the current one.
    """
    path = path.expanduser().resolve()
    
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    
    except OSError as error:
        raise ConfigurationError(f"Impossible to create folder {path.parent}: {error}") from error

    backup_path = None
    if path.exists():
        if not overwrite:
            raise ConfigurationError(
                f"The file {path} already exists. Use --force to overwrite it.")
        backup_path = _create_configuration_backup(path)

    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            json.dump(config, temporary_file, indent=2, ensure_ascii=False)
            temporary_file.write("\n")
            temporary_path = Path(temporary_file.name)
        
        os.replace(temporary_path, path)
   
    except OSError as error:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()
        raise ConfigurationError(f"Impossible saving configuration at {path}: {error}") from error

    return backup_path
