import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from nawia.commands.config import run_edit


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
                    "backups_path": "",
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
                    },
                    "summaries_fields": {"individual": []},
                    "table_hierarchies": {},
                    "default_values": {},
                    "users": {},
                }
            ],
        }
    }


class ConfigurationEditTests(unittest.TestCase):
    def test_edit_copies_bundled_config_to_user_path_when_missing(self):
        seed = make_configuration()
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            seed_path = Path(directory) / "packaged-config.json"
            seed_path.write_text(json.dumps(seed), encoding="utf-8")
            output = StringIO()

            with (
                patch("nawia.commands.config.DEFAULT_CONFIG", config_path),
                patch(
                    "nawia.commands.config.packaged_configuration_path",
                    return_value=seed_path,
                ),
                patch("builtins.input", return_value="0"),
                redirect_stdout(output),
            ):
                run_edit(SimpleNamespace(config=config_path))

            copied = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(copied, seed)
        self.assertIn("Copied the bundled configuration", output.getvalue())

    def test_editing_connection_preserves_other_sections_and_creates_backup(self):
        original = make_configuration()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(original), encoding="utf-8")
            output = StringIO()

            with patch(
                "builtins.input",
                side_effect=["1", "y", "db.example.edu", "22103", ""],
            ), redirect_stdout(output):
                run_edit(SimpleNamespace(config=path))

            updated = json.loads(path.read_text(encoding="utf-8"))
            backup = json.loads(path.with_suffix(".json.bak").read_text(encoding="utf-8"))

        self.assertEqual(updated["database"]["host"], "db.example.edu")
        self.assertEqual(updated["database"]["port"], "22103")
        self.assertEqual(updated["database"]["db_name"], "lab")
        self.assertEqual(updated["database"]["projects"], original["database"]["projects"])
        self.assertEqual(backup, original)
        self.assertIn("WARNING: Changing these settings", output.getvalue())

    def test_editing_project_sections_keeps_unselected_connection_settings(self):
        original = make_configuration()
        original["database"]["sslmode"] = "require"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(original), encoding="utf-8")

            with patch(
                "builtins.input",
                side_effect=[
                    "2,3,4,5,6",
                    "human_updated",
                    "",
                    "-",
                    "1",
                    "custom_field",
                    "processed_samples",
                    "batch",
                    "",
                    "3",
                    "1",
                    "processed_samples",
                    "individuals",
                    "lab_id",
                    "",
                    "1",
                    "country",
                    "Poland",
                    "",
                    "1",
                    "tester",
                    r"^(\w+)$",
                    "lab_id",
                    "",
                ],
            ), redirect_stdout(StringIO()):
                run_edit(SimpleNamespace(config=path))

            updated = json.loads(path.read_text(encoding="utf-8"))
            project = updated["database"]["projects"][0]

        self.assertEqual(updated["database"]["host"], original["database"]["host"])
        self.assertEqual(updated["database"]["port"], original["database"]["port"])
        self.assertEqual(updated["database"]["sslmode"], "require")
        self.assertEqual(project["name"], "human_updated")
        self.assertEqual(project["backups_path"], "")
        self.assertEqual(
            project["mapping"]["custom_field"],
            {"base_table": "processed_samples", "column_name": "batch"},
        )
        self.assertEqual(project["summaries_fields"]["individual"], ["custom_field"])
        self.assertEqual(
            project["table_hierarchies"]["processed_samples"],
            {"parent": "individuals", "join_on": "lab_id"},
        )
        self.assertEqual(project["default_values"]["country"], "Poland")
        self.assertEqual(
            project["users"]["tester"],
            {"regex": r"^(\w+)$", "groups": ["lab_id"]},
        )

    def test_invalid_port_does_not_save_or_replace_existing_configuration(self):
        original = make_configuration()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(original), encoding="utf-8")

            with patch(
                "builtins.input",
                side_effect=["1", "y", "db.example.edu", "not-a-port"],
            ), redirect_stdout(StringIO()), redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                run_edit(SimpleNamespace(config=path))

            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertFalse(path.with_suffix(".json.bak").exists())

        self.assertEqual(saved, original)

    def test_declining_connection_warning_does_not_change_or_back_up_config(self):
        original = make_configuration()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(original), encoding="utf-8")
            output = StringIO()

            with patch("builtins.input", side_effect=["1", "n"]), redirect_stdout(output):
                run_edit(SimpleNamespace(config=path))

            saved = json.loads(path.read_text(encoding="utf-8"))

            self.assertFalse(path.with_suffix(".json.bak").exists())

        self.assertEqual(saved, original)
        self.assertIn("No changes were made.", output.getvalue())


if __name__ == "__main__":
    unittest.main()
