"""Comandi interattivi per inizializzare e verificare la configurazione."""

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
    """Registra i comandi `config init`, `config validate` e `config test`."""
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
    """Esegue i controlli locali: non apre connessioni e non modifica file."""
    try:
        config = load_configuration(args.config)
    except ConfigurationError as error:
        _fail(str(error))

    errors = validate_configuration(config)
    if errors:
        print("Configurazione non valida:")
        for error in errors:
            print(f"- {error}")
        raise SystemExit(1)

    print(f"Configurazione valida: {args.config}")
    print("Questo controllo non verifica l'esistenza reale delle tabelle nel database.")


def run_test(args: Any) -> None:
    """Prova la connessione e verifica mapping e gerarchie contro lo schema reale."""
    try:
        config = load_configuration(args.config)
    except ConfigurationError as error:
        _fail(str(error))

    errors = validate_configuration(config)
    if errors:
        print("Correggi prima la configurazione:")
        for error in errors:
            print(f"- {error}")
        raise SystemExit(1)

    try:
        project = find_project(config, args.project)
    except ConfigurationError as error:
        _fail(str(error))

    username = args.username or input("Database username: ").strip()
    if not username:
        _fail("Il nome utente del database non puo' essere vuoto.")
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
            "Connessione riuscita, ma non e' stato possibile leggere lo schema "
            f"({type(error).__name__}). Verifica i permessi dell'utente."
        )
    finally:
        engine.dispose()

    if errors:
        print(f"Connessione riuscita, ma lo schema '{project['schema_name']}' non e' valido per Nawia:")
        for error in errors:
            print(f"- {error}")
        for warning in warnings:
            print(f"Attenzione: {warning}")
        raise SystemExit(1)

    print(f"Connessione riuscita. Mapping e relazioni di '{project['name']}' verificati.")
    for warning in warnings:
        print(f"Attenzione: {warning}")


def run_init(args: Any) -> None:
    """Guida l'utente nel creare una configurazione per uno schema gia' esistente.

    Il comando legge il catalogo del database, ma non crea o modifica tabelle.
    Le credenziali sono usate solo per la connessione corrente e non sono salvate.
    """
    output_path = args.output.expanduser()
    if output_path.exists() and not args.force:
        _fail(
            f"Il file {output_path} esiste gia'. Per proteggerlo, init non lo "
            "sovrascrive: scegli --output oppure usa --force per conservarne un backup."
        )

    try:
        template = _choose_template(args.template)
    except ConfigurationError as error:
        _fail(str(error))

    database_value = template.get("database")
    if not isinstance(database_value, dict):
        _fail("Il template selezionato non contiene una sezione 'database' valida.")
    database_template = cast(dict[str, Any], database_value)
    host = _prompt_text("Database host", database_template.get("host"))
    port = _prompt_text("Database port", str(database_template.get("port") or "5432"))
    database_name = _prompt_text("Database name", database_template.get("db_name"))
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        _fail("La porta deve essere un numero tra 1 e 65535.")
    username = _prompt_text("Database username")
    password = getpass.getpass("Database password (not saved): ")
    if not password:
        _fail("La password non puo' essere vuota.")

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

        # I sommari possono contenere solo concetti che l'utente ha effettivamente mappato.
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
            raise ConfigurationError("Il template selezionato non contiene una sezione database valida.")
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
                "La configurazione generata non supera i controlli locali:\n- "
                + "\n- ".join(local_errors)
            )

        schema_errors, warnings = validate_project_schema(project_template, inspector)
        if schema_errors:
            raise ConfigurationError(
                "La configurazione generata non corrisponde allo schema selezionato:\n- "
                + "\n- ".join(schema_errors)
            )

        backup_path = save_configuration(config, output_path, overwrite=args.force)
    except ConfigurationError as error:
        _fail(str(error))
    except SQLAlchemyError as error:
        _fail(
            "Connessione riuscita, ma non e' stato possibile leggere lo schema "
            f"({type(error).__name__}). Verifica i permessi dell'utente."
        )
    finally:
        engine.dispose()

    print(f"Configurazione salvata in: {output_path.resolve()}")
    if backup_path:
        print(f"Configurazione precedente conservata in: {backup_path}")
    print("Le credenziali non sono state salvate.")
    for warning in warnings:
        print(f"Attenzione: {warning}")


def _choose_template(template_path: Path | None) -> dict[str, Any]:
    """Carica un template indicato oppure mostra i preset disponibili."""
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

    print("Da quale configurazione vuoi partire?")
    for index, (label, _) in enumerate(presets, start=1):
        print(f"  {index}. {label}")
    print(f"  {len(presets) + 1}. Custom (solo i mapping richiesti dall'interfaccia attuale)")
    choice = _prompt_index("Scelta", len(presets) + 1, default=1)

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
    """Rifiuta template incompleti prima di iniziare la procedura interattiva."""
    database = template.get("database")
    if not isinstance(database, dict):
        raise ConfigurationError("Il template deve contenere un oggetto 'database'.")
    database = cast(dict[str, Any], database)
    projects_value = database.get("projects")
    if not isinstance(projects_value, list) or not projects_value:
        raise ConfigurationError("Il template deve contenere almeno un progetto.")
    projects = cast(list[Any], projects_value)
    first_project_value = projects[0]
    if not isinstance(first_project_value, dict):
        raise ConfigurationError("Il primo progetto del template deve essere un oggetto.")
    first_project = cast(dict[str, Any], first_project_value)
    if not isinstance(first_project.get("mapping", {}), dict):
        raise ConfigurationError("Il mapping del primo progetto deve essere un oggetto.")


def _choose_project_template(template: dict[str, Any]) -> dict[str, Any]:
    """Seleziona il primo progetto del template come base per la nuova configurazione."""
    database_value = template.get("database")
    if not isinstance(database_value, dict):
        raise ConfigurationError("Il template non contiene una sezione database valida.")
    database = cast(dict[str, Any], database_value)
    projects_value = database.get("projects", [])
    if not isinstance(projects_value, list):
        raise ConfigurationError("database.projects deve essere una lista.")
    projects = cast(list[Any], projects_value)
    if not projects or not isinstance(projects[0], dict):
        raise ConfigurationError("Il template deve contenere almeno un database.projects.")
    return copy.deepcopy(cast(dict[str, Any], projects[0]))


def _configure_mappings(
    template_mapping: dict[str, Any],
    inspector: Any,
    schema: str,
) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Lascia confermare i mapping del preset o li sostituisce con colonne reali."""
    tables = sorted(inspector.get_table_names(schema=schema))
    if not tables:
        raise ConfigurationError(f"Lo schema '{schema}' non contiene tabelle.")

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

        print(f"\nMapping del concetto Nawia '{field_name}'")
        selected_table = _choose_from_list(
            "Tabella",
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
            "Colonna",
            columns,
            default=default_column if default_column in columns else None,
            optional=False,
        )
        if selected_column is None:
            raise ConfigurationError(f"Non e' stata selezionata una colonna per '{field_name}'.")
        mappings[field_name] = {
            "base_table": selected_table,
            "column_name": selected_column,
        }

    while _prompt_yes_no("\nVuoi aggiungere un altro campo personalizzato?", default=False):
        field_name = _prompt_text("Nome del concetto (es. extraction_batch)")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", field_name):
            print("Usa solo lettere minuscole, numeri e underscore; il nome deve iniziare con una lettera.")
            continue
        if field_name in mappings:
            print(f"Il mapping '{field_name}' esiste gia'.")
            continue

        table = _choose_from_list("Tabella", tables)
        if table is None:
            raise ConfigurationError("Non e' stata selezionata una tabella.")
        columns = sorted(
            column["name"] for column in inspector.get_columns(table, schema=schema)
        )
        column = _choose_from_list("Colonna", columns)
        if column is None:
            raise ConfigurationError("Non e' stata selezionata una colonna.")
        mappings[field_name] = {"base_table": table, "column_name": column}
        if _prompt_yes_no("Includere questo campo nel riepilogo individuale?", default=False):
            summary_fields.append(field_name)

    return mappings, summary_fields


def _choose_schema(inspector: Any, template: dict[str, Any]) -> str:
    """Mostra gli schemi che contengono tabelle e propone quello del template."""
    system_schemas = {"information_schema", "pg_catalog"}
    schemas = [
        schema
        for schema in inspector.get_schema_names()
        if schema not in system_schemas and inspector.get_table_names(schema=schema)
    ]
    schemas.sort()
    if not schemas:
        raise ConfigurationError("Non sono stati trovati schemi utente con tabelle.")

    database = cast(dict[str, Any], template["database"])
    projects = cast(list[dict[str, Any]], database["projects"])
    preferred_value = projects[0].get("schema_name")
    preferred = preferred_value if isinstance(preferred_value, str) else None
    print("\nSchemi disponibili:")
    selected_schema = _choose_from_list(
        "Schema",
        schemas,
        default=preferred if preferred in schemas else None,
    )
    if selected_schema is None:
        raise ConfigurationError("Non e' stato selezionato uno schema.")
    return selected_schema


def _choose_from_list(
    label: str,
    choices: list[str],
    default: str | None = None,
    optional: bool = False,
) -> str | None:
    """Fa scegliere una voce numerata, con scelta predefinita e opzione facoltativa."""
    if not choices:
        raise ConfigurationError(f"Nessuna opzione disponibile per '{label}'.")

    default_index = choices.index(default) + 1 if default in choices else None
    print(f"{label}:")
    for index, choice in enumerate(choices, start=1):
        suffix = " (preset)" if index == default_index else ""
        print(f"  {index}. {choice}{suffix}")
    if optional:
        print("  0. Non mappare questo campo")

    while True:
        answer = input(
            f"Scelta [{default_index if default_index is not None else ''}]: "
        ).strip()
        if not answer and default_index is not None:
            return choices[default_index - 1]
        if optional and answer == "0":
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(choices):
            return choices[int(answer) - 1]
        print("Inserisci il numero di una delle opzioni elencate.")


def _prompt_text(label: str, default: str | None = None) -> str:
    """Legge un testo non vuoto, usando il default se l'utente preme Invio."""
    while True:
        suffix = f" [{default}]" if default else ""
        answer = input(f"{label}{suffix}: ").strip()
        if answer:
            return answer
        if default:
            return str(default)
        print("Questo valore e' obbligatorio.")


def _prompt_index(label: str, maximum: int, default: int) -> int:
    """Legge un numero compreso tra 1 e il massimo mostrato."""
    while True:
        answer = input(f"{label} [{default}]: ").strip()
        if not answer:
            return default
        if answer.isdigit() and 1 <= int(answer) <= maximum:
            return int(answer)
        print(f"Inserisci un numero tra 1 e {maximum}.")


def _prompt_yes_no(label: str, default: bool) -> bool:
    """Legge una risposta si/no e permette di accettare il default con Invio."""
    suffix = "Y/n" if default else "y/N"
    while True:
        answer = input(f"{label} [{suffix}]: ").strip().lower()
        if not answer:
            return default
        if answer in {"y", "yes", "s", "si", "sì"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Rispondi y o n.")


def _connect_and_inspect(
    database: dict[str, Any],
    username: str,
    password: str,
) -> tuple[Any, Any]:
    """Apre una connessione PostgreSQL e restituisce engine e inspector SQLAlchemy."""
    try:
        from sqlalchemy import URL, create_engine, inspect, text
        from sqlalchemy.exc import SQLAlchemyError
    except ImportError as error:
        raise ConfigurationError(
            "SQLAlchemy non e' installato. Installa le dipendenze del progetto con 'pip install .'."
        ) from error

    database_type = str(database.get("type", "postgresql")).lower()
    if database_type not in {"postgresql", "postgres"}:
        raise ConfigurationError("Il test di connessione supporta al momento solo PostgreSQL.")

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
            "Il driver PostgreSQL non e' installato. Installa le dipendenze del progetto con 'pip install .'."
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
            "Il driver PostgreSQL non e' installato. Installa le dipendenze del progetto con 'pip install .'."
        ) from error
    except SQLAlchemyError as error:
        engine.dispose()
        raise ConfigurationError(
            f"Connessione al database non riuscita ({type(error).__name__}). "
            "Controlla host, porta, nome database, credenziali e accesso di rete."
        ) from error
    return engine, inspector


def _fail(message: str) -> NoReturn:
    """Stampa un errore CLI leggibile e termina con un codice diverso da zero."""
    print(f"Errore: {message}", file=sys.stderr)
    raise SystemExit(1)
