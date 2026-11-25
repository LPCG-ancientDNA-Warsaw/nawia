import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nawia.configuration import (
    ConfigurationError,
    _shortest_path_count,
    default_configuration_path,
    find_project,
    packaged_configuration_path,
    save_configuration,
    validate_configuration,
    validate_project_schema,
)


def make_configuration():
    return {
        "database": {
            "type": "postgresql",
            "db_name": "lab",
            "host": "localhost",
            "port": "5432",
            "projects": [
                {
                    "name": "human",
                    "schema_name": "lab_data",
                    "mapping": {
                        "individual_id": {
                            "base_table": "individuals",
                            "column_name": "lab_id",
                        },
                        "project_id": {
                            "base_table": "individuals",
                            "column_name": "project_id",
                        },
                        "coord_ns": {
                            "base_table": "individuals",
                            "column_name": "coord_ns",
                        },
                        "coord_we": {
                            "base_table": "individuals",
                            "column_name": "coord_we",
                        },
                        "material_description": {
                            "base_table": "processed_samples",
                            "column_name": "material_description",
                        },
                        "site_name": {
                            "base_table": "individuals",
                            "column_name": "site_name",
                        },
                    },
                    "summaries_fields": {"individual": ["site_name"]},
                    "table_hierarchies": {
                        "processed_samples": {
                            "parent": "individuals",
                            "join_on": "lab_id",
                        }
                    },
                    "default_values": {},
                    "users": {},
                }
            ],
        }
    }


class FakeInspector:
    """Imitates SQLAlchemy Inspector."""

    def __init__(self):
        self.columns = {
            "individuals": {
                "lab_id",
                "project_id",
                "coord_ns",
                "coord_we",
                "site_name",
            },
            "processed_samples": {"lab_id", "material_description"},
        }

    def get_schema_names(self):
        return ["lab_data"]

    def get_table_names(self, schema=None):
        return list(self.columns)

    def get_columns(self, table_name, schema=None):
        return [{"name": column} for column in self.columns[table_name]]

    def get_foreign_keys(self, table_name, schema=None):
        if table_name == "processed_samples":
            return [
                {
                    "referred_schema": "lab_data",
                    "referred_table": "individuals",
                    "constrained_columns": ["lab_id"],
                    "referred_columns": ["lab_id"],
                }
            ]
        return []

    def get_pk_constraint(self, table_name, schema=None):
        if table_name == "individuals":
            return {"constrained_columns": ["lab_id"]}
        return {"constrained_columns": ["lab_id", "material_id"]}


class ConfigurationTests(unittest.TestCase):
    def test_explicit_config_environment_path_takes_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            explicit_path = Path(directory) / "custom.json"
            with patch.dict(
                os.environ,
                {"NAWIA_CONFIG": str(explicit_path)},
                clear=True,
            ):
                self.assertEqual(default_configuration_path(), explicit_path)

    def test_existing_working_directory_config_is_used(self):
        with tempfile.TemporaryDirectory() as directory:
            working_path = Path(directory) / "config.json"
            working_path.write_text("{}", encoding="utf-8")
            with (
                patch.dict(os.environ, {}, clear=True),
                patch("nawia.configuration.Path.cwd", return_value=Path(directory)),
            ):
                self.assertEqual(default_configuration_path(), working_path)

    def test_packaged_configuration_is_available_as_a_seed(self):
        self.assertEqual(
            packaged_configuration_path(),
            Path(sys.prefix) / "config.json",
        )

    def test_valid_configuration_has_no_structural_errors(self):
        self.assertEqual(validate_configuration(make_configuration()), [])

    def test_missing_required_mapping_is_reported(self):
        config = make_configuration()
        del config["database"]["projects"][0]["mapping"]["coord_ns"]

        errors = validate_configuration(config)

        self.assertTrue(any("coord_ns" in error for error in errors))

    def test_project_id_must_share_individual_table_for_current_ui(self):
        config = make_configuration()
        config["database"]["projects"][0]["mapping"]["project_id"]["base_table"] = "projects"

        errors = validate_configuration(config)

        self.assertTrue(any("project_id.base_table" in error for error in errors))

    def test_summary_fields_must_have_a_mapping(self):
        config = make_configuration()
        config["database"]["projects"][0]["summaries_fields"]["individual"].append("unknown")

        errors = validate_configuration(config)

        self.assertTrue(any("unknown" in error for error in errors))

    def test_database_schema_checks_columns_and_foreign_key_hierarchy(self):
        project = make_configuration()["database"]["projects"][0]

        errors, warnings = validate_project_schema(project, FakeInspector())

        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_database_schema_reports_missing_mapped_column(self):
        project = make_configuration()["database"]["projects"][0]
        inspector = FakeInspector()
        inspector.columns["individuals"].remove("coord_ns")

        errors, _ = validate_project_schema(project, inspector)

        self.assertTrue(any("individuals.coord_ns" in error for error in errors))

    def test_hierarchy_join_column_must_be_parent_primary_key(self):
        project = make_configuration()["database"]["projects"][0]
        project["table_hierarchies"]["processed_samples"]["join_on"] = "site_name"
        inspector = FakeInspector()
        inspector.columns["processed_samples"].add("site_name")

        errors, _ = validate_project_schema(project, inspector)

        self.assertTrue(any("is not a Primary Key" in error for error in errors))

    def test_shortest_join_path_counter_detects_ambiguity(self):
        graph = {
            "individuals": {"by_material", "by_library"},
            "by_material": {"individuals", "libraries"},
            "by_library": {"individuals", "libraries"},
            "libraries": {"by_material", "by_library"},
        }

        self.assertEqual(_shortest_path_count(graph, "individuals", "libraries"), 2)

    def test_find_project_accepts_name_or_schema(self):
        config = make_configuration()

        self.assertEqual(find_project(config, "human")["schema_name"], "lab_data")
        self.assertEqual(find_project(config, "lab_data")["name"], "human")

    def test_overwrite_keeps_original_configuration_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text("original", encoding="utf-8")

            backup = save_configuration(make_configuration(), path, overwrite=True)

            self.assertIsNotNone(backup)
            self.assertEqual(backup.read_text(encoding="utf-8"), "original")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["database"]["db_name"], "lab")

    def test_overwrite_requires_explicit_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text("original", encoding="utf-8")

            with self.assertRaises(ConfigurationError):
                save_configuration(make_configuration(), path)


if __name__ == "__main__":
    unittest.main()
