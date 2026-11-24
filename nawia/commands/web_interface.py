import subprocess
import os
import sys

def add_parser(subparsers):
    parser = subparsers.add_parser("web_interface", help="Launch the Streamlit web interface")
    parser.set_defaults(func = run)

def run(args):
    app_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "app_streamlit.py")
    )
    repository_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..")
    )
    
    print("Launching Nawia Web Interface...")
    environment = os.environ.copy()
    python_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        repository_root
        if not python_path
        else repository_root + os.pathsep + python_path
    )
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", app_path],
        env=environment,
    )