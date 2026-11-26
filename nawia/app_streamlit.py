from psycopg2 import OperationalError
import streamlit as st
import sys
from pathlib import Path

# When Streamlit is started directly with `streamlit run nawia/app_streamlit.py`,
# Streamlit may put only this package directory on sys.path. Add the repository
# root so package imports such as `nawia.db_manager` work in that mode too.
_repository_root = Path(__file__).resolve().parent.parent
if str(_repository_root) not in sys.path:
    sys.path.insert(0, str(_repository_root))

from nawia.db_manager import LabDatabase
import pandas as pd
import glob
import os
from datetime import datetime
import plotly.express as px
from sqlalchemy import MetaData, Table, inspect as sqlalchemy_inspect, select, text
from sqlalchemy.sql.sqltypes import (
    Date,
    DateTime,
    Float,
    Integer,
    Numeric,
)
from pathlib import Path
import io
import json
import copy
import math
import plotly.graph_objects as go
import importlib.util

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.exc import OperationalError as SQLAlchemyOperationalError

from nawia.configuration import (
    ConfigurationError,
    backup_root_path,
    default_data_directory,
    default_configuration_path,
    save_configuration,
    validate_configuration,
    validate_project_schema,
)

@st.cache_resource(show_spinner="Connecting to the database...")
def init_db(user, password):
    """
    Initiates and caches the Database Object.
    
    Inputs:
    - user (str): user name for database authentication.
    - password (str): password for database authenticatin.
    
    Output:
    - db (LabDatabase): an initialized instance of the LabDatabase class.
    """

    db = LabDatabase(user=user, password=password)
    # Establish a connection.
    with db.engine.connect() as conn:
        pass
    return db

def get_database_connection():
    """
    Renders the sidebar authentication fields and manages database connection state.
    
    Inputs:
    - None.
    
    Outputs:
    - db (LabDatabase or None): Active database connection instance, or None if unauthenticated/failed.
    """
    st.sidebar.title("Database Access")
    db_user = st.sidebar.text_input("Username Database")
    db_password = st.sidebar.text_input("Password Database", type="password")

    if db_user and db_password:
        missing_dependencies = []
        for module_name in ("sqlalchemy", "psycopg2", "pandas", "plotly"):
            if importlib.util.find_spec(module_name) is None:
                missing_dependencies.append(module_name)

        if missing_dependencies:
            st.sidebar.error(
                "Dependency missing: "
                + ", ".join(missing_dependencies)
                + ". Install with `python -m pip install -e .` "
                "using the same Python environment that launched Streamlit."
            )
            return None

        try:
            db = init_db(db_user, db_password)
            st.sidebar.success("Connection established!")
            return db

        # Missing dependencies
        except (ModuleNotFoundError, ImportError) as error:
            missing_module = getattr(error, "name", None) or str(error)
            st.sidebar.error(
                f"Python could not load a dependency ({missing_module}). "
                "Run `python -m pip install -e .` with the same Python "
                "interpreter used by Streamlit."
            )
            return None

        # Credentials or Database status problems
        except SQLAlchemyOperationalError as error:
            original_error = getattr(error, "orig", error)
            st.sidebar.error(f"Database connection failed: {original_error}")
            return None

        except OperationalError as e:
            error_msg = str(e).lower()

            if "password authentication failed" in error_msg or "access denied" in error_msg:
                st.sidebar.error("Username or password not correct.")
            
            elif "could not connect to server" in error_msg or "connection refused" in error_msg:
                st.sidebar.error("Database not reachable.")
            
            elif "database" in error_msg and "does not exist" in error_msg:
                st.sidebar.error("The database name specified in `config.json` does not exist.")
            
            else:
                st.sidebar.error(f"Database Operational Error: {e.orig if hasattr(e, 'orig') else e}")
            
            return None
        # Other types of errors
        except Exception as e:
            st.sidebar.error(f"Generic Error: {e}")
            return None
    else:
        st.warning("Please provide your credentials.")
        return None

db = get_database_connection()
if not db:
    st.stop()
else:
    st.write(f"Welcome to database **{db.db_name}**!")

def get_active_project_context():
    """
    Manages the project/schema selection widget in the sidebar and keeps state in session_state.
    
    Inputs:
    - None.
    
    Outputs:
    - selected_project (str): Selected project name.
    - selected_schema (str): Corresponding database schema name.
    """
    # Obtain all possible projects from the .config file
    project_entries = db.config["database"]["projects"]
    labels = [p.get("name") or p.get("schema_name") for p in project_entries]

    # If nothing is selected through the sidebar, it falls back to the first possible project.
    if "selected_project_label" not in st.session_state:
        st.session_state.selected_project_label = labels[0]

    selected_label = st.sidebar.selectbox("Project", 
                                          options=labels,
                                          index=labels.index(st.session_state.selected_project_label),)

    # If a project is selected on the sidebar, keeps the selection in memory
    st.session_state.selected_project_label = selected_label

    selected_project = next(
        (p.get("name") for p in project_entries if (p.get("name") or p.get("schema_name")) == selected_label),
        None)

    selected_schema = next(
        (p.get("schema_name") for p in project_entries if (p.get("name") or p.get("schema_name")) == selected_label),
        None
    )
    return selected_project, selected_schema

@st.cache_data
def get_individuals_status(_db_engine, project, schema):
    """
    Gets summary status details for all individuals in a project/schema.
    
    Inputs:
    - _db_engine (Engine): SQLAlchemy engine instance.
    - project (str): Active project name.
    - schema (str): Active database schema name.
    
    Outputs:
    - combined_df (DataFrame): Pandas DataFrame containing individual status metrics.
    """
    project_config = next(
    (p for p in db.config["database"]["projects"]
        if (project is not None and p.get("name") == project)
        or (schema is not None and p.get("schema_name") == schema)), db.config["database"]["projects"][0])

    mapping = project_config["mapping"]

    all_individual_ids = db.get_column_values(
        table=mapping["individual_id"]["base_table"],
        project=project,
        schema=schema,
        columns=mapping["individual_id"]["column_name"])

    requested_fields = ["individual_id"]
    requested_fields.extend(project_config["summaries_fields"]["individual"])
    
    details_frames = []
    for individual_id in all_individual_ids:
        df = db.get_sample_details(individual_id, project=project, schema=schema)
        if df.empty:
            continue
    
        if "individual_id" not in df.columns:
            df = df.copy()
            df["individual_id"] = individual_id
    
        df = df.reindex(columns=requested_fields, fill_value=pd.NA)
        details_frames.append(df)
    
    combined_df = pd.DataFrame(columns=requested_fields)
    if details_frames:
        combined_df = pd.concat(details_frames, ignore_index=True)

    return combined_df

@st.cache_data
def get_map_coordinates(_db_engine, schema=None, project=None):
    """
    Retrieves geographic coordinates (NS/WE) mapped to individual IDs for map rendering.
    
    Inputs:
    - _db_engine: SQLAlchemy engine instance.
    - schema: Target schema name.
    - project: Target project name.
    
    Outputs:
    - coordinates_df: DataFrame containing individual IDs and clean coordinates.
    """
    project_config = next(
        (p for p in db.config["database"]["projects"]
        if (project is not None and p.get("name") == project)
        or (schema is not None and p.get("schema_name") == schema)),
        db.config["database"]["projects"][0])

    mapping = project_config["mapping"]

    individual_id_column_name = mapping["individual_id"]["column_name"]
    individual_id_table = mapping["individual_id"]["base_table"]
    coord_ns_info = mapping["coord_ns"]
    coord_we_info = mapping["coord_we"]

    coord_ns_info_column_name = coord_ns_info["column_name"]
    coord_we_info_column_name = coord_we_info["column_name"]
    coords_table = coord_ns_info["base_table"]

    if not individual_id_column_name or not individual_id_table:
        raise ValueError("The mapping for individual_id is incomplete in config.json")

    if not coord_ns_info_column_name or not coord_we_info_column_name:
        raise ValueError("The mapping for coord_ns/coord_we is incomplete in config.json")


    if str(individual_id_table).lower() == str(coords_table).lower():
        query = f"""
        SELECT {individual_id_column_name} AS individual_id,
               {coord_ns_info_column_name} AS coord_ns, 
               {coord_we_info_column_name} AS coord_we
        FROM {schema}.{individual_id_table}
        """
    else:
        query = f"""
        SELECT i.{individual_id_column_name} AS individual_id, 
               c.{coord_ns_info_column_name} AS coord_ns,
               c.{coord_we_info_column_name} AS coord_we
        FROM {schema}.{individual_id_table} AS i
        LEFT JOIN {schema}.{coords_table} AS c
        ON i.{individual_id_column_name} = c.{individual_id_column_name}
        """
    return pd.read_sql(query, _db_engine).dropna()

@st.cache_data
def get_any_table(_db_engine, table_name, schema=None):
    """
    Fetches the complete contents of any database table.
    
    Inputs:
    - _db_engine: SQLAlchemy engine instance.
    - table_name: Target table name.
    - schema: Target schema name.
    
    Outputs:
    - table_df (DataFrame): DataFrame with full table contents.
    """
    query = f"SELECT * FROM {schema}.{table_name}"
    return pd.read_sql(query, _db_engine)

@st.cache_data
def get_backup_files(path, extension):
    """
    Scans a directory for backup files with a specific extension, ordered by creation time.
    
    Inputs:
    - path: Directory path to scan.
    - extension: Target file extension.
    
    Outputs:
    - sorted_files: List of file paths sorted newest first.
    """
    files = glob.glob(f"{path}/*.{extension}")
    # Sort files by date
    return sorted(files, key=os.path.getctime, reverse=True)

########################################## Initial page #################################################
resources_dir = Path(__file__).resolve().parent / "resources"
logo = resources_dir / "static" / "LPiGK_transparent.png"
page_icon = resources_dir / "static" / "transparent_LPCG_logo.png"

st.set_page_config(page_title = "Nawia: Lab Database Portal", layout = "wide", page_icon = page_icon)
st.logo(logo, size = "large", icon_image = page_icon)

col1, col2 = st.columns([1, 3])
with col1:
    st.image(logo, width = 800)
with col2:
    st.markdown("#")
    st.title("Nawia: Keep All Metadata Properly and Orderly Stored")
    st.text("v0.1, by Flavia Leotta (2026)")
st.subheader("", divider = 'orange')

####################################### All the other pages ###############################################
def home_page():
    """
    Renders the Home page view including multi-sample ID search, paginated data view, and interactive map.
    
    Inputs:
    - None
    
    Outputs:
    - None (Displays elements directly).
    """
    selected_project, selected_schema = get_active_project_context()
    st.subheader("Home page")
    
    try:
        df = get_individuals_status(db.engine, project=selected_project, schema=selected_schema)
        coords_all = get_map_coordinates(db.engine, schema=selected_schema)

        # Research through ID
        search_input = st.text_input("Search by Individual ID (separate multiple IDs with commas):", "").strip().upper()

        if search_input:
            search_list = [id.strip() for id in search_input.split(",") if id.strip()]

            import re
            # Searching using regex is faster
            regex_search = "|".join(map(re.escape, search_list))
            df_filtered = df[df["individual_id"].str.contains(regex_search, na=False, regex=True)]
            st.caption(f"Found {len(df_filtered)} samples matching your search criteria.")
        else:
            df_filtered = df

        # Build the dataframe page(s)
        rows_per_page = 15
        total_rows = len(df_filtered)
        total_pages = (total_rows // rows_per_page) + (1 if total_rows % rows_per_page > 0 else 0)

        # The current page is saved in memory. If none is found, the user is redirected to the number 1.
        if 'current_page' not in st.session_state:
            st.session_state.current_page = 1

        # If the number of pages accidentally adds up to more than what is available, move to the last page.
        if st.session_state.current_page > total_pages and total_pages > 0:
            st.session_state.current_page = total_pages

        # Buttons to navigate
        _, btn_prev, _, page_text, _, btn_next = st.columns([2, 1, 1, 2, 1, 1])

        # Go back by one
        with btn_prev:
            if st.button("⬅️", width='stretch') and st.session_state.current_page > 1:
                st.session_state.current_page -= 1
                st.rerun()

        # Show the current page
        with page_text:
            st.markdown(f"<p style='text-align: center; padding-top: 5px;'>Page <b>{st.session_state.current_page}</b> / {total_pages}</p>", unsafe_allow_html=True)

        # Go forward by one
        with btn_next:
            if st.button("➡️", width='stretch') and st.session_state.current_page < total_pages:
                st.session_state.current_page += 1
                st.rerun()

        # Calculating dataframs slicing
        start_idx = (st.session_state.current_page - 1) * rows_per_page
        end_idx = start_idx + rows_per_page
        df_page = df_filtered.iloc[start_idx:end_idx]

        # Visualize
        col_left, col_right = st.columns([1, 3])
        
        with col_right:
            if df_page.empty:
                st.warning("No samples found matching your search.")
            else:
                event = st.dataframe(
                    df_page, 
                    width='stretch',
                    on_select="rerun",
                    selection_mode="multi-row",
                    height=565)
                
                selected_indices = event.selection.rows
                selected_ids = [df_page.iloc[i]["individual_id"] for i in selected_indices] if selected_indices else []

        with col_left:
            with st.expander("📍 Map View", expanded=True):
                # If the user researched something, the map only shows those samples
                map_source = coords_all[coords_all["individual_id"].isin(df_filtered["individual_id"])]
                
                if selected_ids:
                    map_data = map_source[map_source["individual_id"].isin(selected_ids)]
                else:
                    map_data = map_source
                
                st.map(map_data, latitude='coord_ns', longitude='coord_we')

    except Exception as e:
        st.error(f"Error: {e}")

def full_db():
    """
    Renders the Full Database inspection page allowing single-table selection and TSV export.
    
    Inputs:
    - None
    
    Outputs:
    - None
    """
    selected_project, selected_schema = get_active_project_context()

    st.subheader("Full Database")
    options = db.get_tables_names(schema=selected_schema, project=selected_project)
    selection = st.pills("Tables", options, selection_mode = 'single')

    if selection:
        df = get_any_table(db.engine, selection, schema=selected_schema)
        tsv_data = df.to_csv(sep='\t', index=False).encode('utf-8')
        tsv_file_name = f"table_{selection}.tsv"
        
        st.download_button("Export table in a .tsv format", data = tsv_data, file_name = tsv_file_name)
        st.dataframe(data = df)
    else:
        st.text("Please select one option to display table information.")

def upload_data():
    """
    Handles template Excel generation/download and uploading user Excel files back to the database.
    
    Inputs:
    - None
    
    Outputs:
    - None
    """
    selected_project, selected_schema = get_active_project_context()
    
    st.subheader("Upload Excel")
    st.write(f"Active project: {selected_project} (schema: **{selected_schema}**).")

    templates_dir = default_data_directory() / "templates"
    templates_dir.mkdir(parents=True, exist_ok=True)
    # We save one template for each schema
    template_path = templates_dir / f"{selected_schema}_excel_upload.xlsx"

    # If file does not exist, it is automatically generated the first time
    if not template_path.exists():
        with st.spinner("Generating template for the first time..."):
            db.generate_excel_template(schema=selected_schema)

    col1, col2 = st.columns([2, 1])

    # Button to download the existing template
    with col1:
        if template_path.exists():
            with open(template_path, "rb") as f:
                st.download_button(
                    label=f"Download Template ({selected_schema})",
                    data=f,
                    file_name=f"{selected_schema}_excel_upload.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # Button to regenerate the template if anything changed in the database
    with col2:
        if st.button("Regenerate Template", help="Click to refresh template if new columns/tables were added to DB"):
            with st.spinner("Updating template..."):
                db.generate_excel_template(schema=selected_schema, project=selected_project)
                st.success("Template updated!")
                st.rerun()
    st.divider()

    uploaded_file = st.file_uploader("Choose an Excel file (.xlsx)", type=['xlsx'])
    if uploaded_file and st.button("Upload to Database"):
        try:
            db.upload_from_excel(uploaded_file, schema=selected_schema)
            st.cache_data.clear()
            st.success("Data successfully uploaded!")
        except Exception as e:
            st.error(f"Error during upload: {e}")

def edit_entries():
    """
    Renders an interactive Data Editor table for safe online row edits and database sync.
    
    Inputs:
    - None
    
    Outputs:
    - None
    """
    selected_project, selected_schema = get_active_project_context() 
    st.subheader("Edit entries")
    
    options = db.get_tables_names(schema=selected_schema, project=selected_project)
    selection = st.pills("Select Table", options, selection_mode='single')

    if selection:
        # Cache: we reload (again) the table only if it has changed after edits
        if "last_selection" not in st.session_state or st.session_state.last_selection != selection:
            with st.spinner(f"Loading {selection}..."):
                st.session_state.df_to_edit = pd.read_sql(f"SELECT * FROM {selected_schema}.{selection}", db.engine)
                st.session_state.last_selection = selection

        edited_df = st.data_editor(
            st.session_state.df_to_edit, 
            width='stretch', 
            num_rows="fixed",
            key="my_editor")
        
        if st.button("Save all changes"):
            with st.spinner("Synchronizing with Database..."):
                success = db.safe_update_table(selection, edited_df, schema=selected_schema, project=selected_project)
                if success:
                    st.success("Database updated!")
                    # Update memory with the updated dataframe
                    st.session_state.df_to_edit = edited_df.copy()
                    st.cache_data.clear()
    else:
        st.text("Please select one option to start editing.")

def backup():
    """
    Provides interface for downloading daily/monthly system backups (Dump, Excel, SQLite) and trigger manual syncs.
    
    Inputs:
    - None
    
    Outputs:
    - None
    """
    selected_project, selected_schema = get_active_project_context()
    st.subheader("💾 Backup Management")

    project_config = next(
        (p for p in db.config["database"]["projects"] if p["name"] == selected_project),
            None)
    
    configured_path = project_config.get("backups_path")
    backup_root = backup_root_path(configured_path)

    # Daily backups directory
    daily_dir = backup_root / "daily" / f"{selected_schema}"
    # Monthly backups directory
    monthly_dir = backup_root / "monthly" / f"{selected_schema}"
    # Make these directories if they do not exist
    daily_dir.mkdir(parents=True, exist_ok=True)
    monthly_dir.mkdir(parents=True, exist_ok=True)

    st.markdown("## Daily backups")
    col1, col2, col3 = st.columns(3)

    def backup_column(number, title, directory, extension):
        """
        This internal function helps create the same column for each type of (daily) backup.
        
        Inputs:
        - numer: incremental, depending on the number of columns.
        - title: title of the column.
        - directory: directory where to look for the backup files.
        - extension: type of file to search for.
        
        Ouput:
        - the rendered column.
        """
        st.markdown(f"### {number}. {title}")
        backup_files = get_backup_files(directory, extension)
        
        if backup_files:
            with st.popover(f"Select {title} Backup"):
                for file in backup_files:
                    ctime = os.path.getctime(file)
                    readable_time = datetime.fromtimestamp(ctime).strftime("%Y-%m-%d %H:%M")
                    filename = os.path.basename(file)
                    
                    with open(file, "rb") as f:
                        st.download_button(
                            label=f"{readable_time}",
                            data=f,
                            file_name=filename,
                            key=f"dl_{extension}_{filename}")
        else:
            st.warning(f"No .{extension} backups found.")       

    with col1:
        backup_column(1, "PostgreSQL", daily_dir, "dump")

    with col2:
        backup_column(2, "Excel", daily_dir, "xlsx")

    with col3:
        backup_column(3, "SQLite", daily_dir, "sqlite3")

    st.markdown("## Monthly backups")

    all_monthly_files = glob.glob(f"{monthly_dir}/*")

    if all_monthly_files:
        available_dates = {}
        for file in all_monthly_files:
            ctime = os.path.getctime(file)
            dt = datetime.fromtimestamp(ctime)
            year = str(dt.year)
            month = dt.strftime("%B")
            month_num = dt.month

            available_dates.setdefault(year, {})
            available_dates[year].setdefault((month_num, month), []).append(file)
        
        m_col1, m_col2 = st.columns(2)
        sorted_years = sorted(available_dates.keys(), reverse=True)
        
        with m_col1:
            selected_year = st.selectbox("Select Year:", sorted_years, key="monthly_year_select")

        months_in_year = available_dates[selected_year]
        sorted_months = sorted(months_in_year.keys(), key=lambda x: x[0])
        month_labels = [m[1] for m in sorted_months]

        with m_col2:
            selected_month_label = st.selectbox("Select Month:", month_labels, key="monthly_month_select")

        selected_month_num = next(m[0] for m in sorted_months if m[1] == selected_month_label)

        st.markdown(f"#### Backups for **{selected_month_label} {selected_year}**")
        m_col_a, m_col_b, m_col_c = st.columns(3)

        def backup_month_column(number, title, extension):
            """
            This internal function helps create the same column for each type of (monthly) backup.
            Comment: maybe to be combined with the function for the daily backups.
            
            Inputs:
            - number: incremental, depending on the number of columns.
            - title: title of the column.
            - extension: type of file to search for.
            
            Ouput:
            - the rendered column.
            """
            def get_filtered_monthly_files(ext):
                files = available_dates[selected_year].get((selected_month_num, selected_month_label), [])
                return [f for f in files if f.endswith(f".{ext}")]

            st.markdown(f"### {number}. {title}")
            pg_files = get_filtered_monthly_files(extension)
            if pg_files:
                with st.popover(f"Select {title} Backup"):
                    for file in pg_files:
                        fname = os.path.basename(file)
                        with open(file, "rb") as f:
                            st.download_button(f"{fname}", f, file_name=fname, key=f"m_{extension}_{fname}")
            else:
                st.info(f"No .{extension} files for this month.")

        with m_col_a:
            backup_month_column(1, "PostgreSQL", "dump")
            
        with m_col_b:
            backup_month_column(2, "Excel", "xlsx")

        with m_col_c:
            backup_month_column(3, "SQLite", "sqlite3")

    else:
        st.warning("No monthly backups available yet.")

    st.divider()
    
    # Manual backup
    st.subheader("Manual Sync")
    st.write("Need a snapshot of the database right now?")

    if st.button("Execute the backup now", type="primary"):
        with st.spinner("Generating backup files..."):
            errors = db.run_backup(schema=selected_schema, project=selected_project)
            st.cache_data.clear()

            if errors:
                st.warning("⚠️ Backup completed with some warnings:")
                for err in errors:
                    st.caption(f"• {err}") # These warnings are a little fast though
                st.success("Successful formats were saved correctly!")
            else:
                st.success("All backups (Excel, PostgreSQL, SQLite) were generated successfully!")

            st.rerun()

def sample_view():
    """
    Renders detailed multi-tab page for a single individual sample across Archaeology, Wet Lab, and Dry Lab.
    
    Inputs:
    - None
    
    Outputs:
    - None
    """
    selected_project, selected_schema = get_active_project_context()
    st.title("🧬 Sample Detailed View")

    project_config = next(
        (p for p in db.config["database"]["projects"] if p["schema_name"] == selected_schema),
        None)

    if project_config:
        mapping = project_config["mapping"]
    else:
        raise ValueError(f"No configuration found for schema: {selected_schema}")

    id_info = mapping["individual_id"]

    # Search for ID
    all_ids = db.get_column_values(table=id_info["base_table"], columns=id_info["column_name"], schema=selected_schema)
    target_id = st.selectbox("Search for a Lab ID:", [""] + all_ids, placeholder = "Start typing an individual ID...", filter_mode = "prefix")

    if target_id:
        data = db.get_long_sample_details(target_id, project=selected_project, schema=selected_schema)

        if data.empty:
            st.warning("No data found for this ID.")
            return
        
        data = data.loc[:, ~data.columns.duplicated()]

        def get_scalar_val(df, col_name, default="N/A"):
            """
            Internal function to transofrm values in strings or NA fields.
            
            Inputs:
            - df: the target dataframe.
            - col_name: the column whose values we are interest in.
            - default: default value in which to convert empty fields.
            
            Output:
            - the value turned into a string, or NA (default) if empty.
            """
            if col_name in df.columns and not df[col_name].empty:
                val = df[col_name].dropna().iloc[0] if not df[col_name].dropna().empty else None
                if val is not None:
                    return str(val)
    
            return default

        # Header
        col1, col2, col3 = st.columns(3)
        col1.metric("Individual ID", get_scalar_val(data, 'individual_id'))
        col2.metric("Project", get_scalar_val(data, 'project_id'))
        col3.metric("Site", get_scalar_val(data, 'site_name'))

        st.divider()

        # Tabs
        tab1, tab2, tab3 = st.tabs(["🏛 Context & Origin", "🧪 Lab Process", "📊 Sequencing Results"])

        with tab1:
            st.subheader("Archeological Context")
            c1, c2 = st.columns(2)
            c1.write(f"**Epoch:** {get_scalar_val(data, 'epoch', 'Unknown')}")
            c1.write(f"**Period:** {get_scalar_val(data, 'time_period', 'Unknown')}")
            c2.write(f"**Grave No:** {get_scalar_val(data, 'grave_no', 'N/A')}")
            c2.write(f"**Contact Person:** {get_scalar_val(data, 'contact_person', 'N/A')}")

            coord_ns = get_scalar_val(data, 'coord_ns', None)
            coord_we = get_scalar_val(data, 'coord_we', None)
            
            if coord_ns and coord_we:
                try:
                    map_data = pd.DataFrame({'lat': [float(coord_ns)], 'lon': [float(coord_we)]})
                    st.map(map_data, zoom=4)
                except ValueError:
                    st.caption("Coordinates available but not in valid numeric format.")

        with tab2:
            st.subheader("Laboratory History")
            with st.expander("Sampled Materials (Processed Samples)", expanded=True):
                mat_cols = [c for c in ['material_id', 'material_description', 'prep_date', 'prep_operator'] if c in data.columns]
                if mat_cols:
                    materials_df = data[mat_cols].drop_duplicates().dropna(how='all')
                    if not materials_df.empty:
                        st.dataframe(materials_df, width="stretch", hide_index=True)
                    else:
                        st.info("No processed samples found for this individual.")

            st.markdown("#### 🧬 DNA Extractions")
            extract_cols = [c for c in ['individual_id', 'material_id', 'extract_date', 'extract_operator', 'extract_method'] if c in data.columns]

            if extract_cols:
                extractions_df = data[extract_cols].drop_duplicates().dropna(how='all')
                if not extractions_df.empty:
                    st.dataframe(extractions_df, width="stretch", hide_index=True)
                else:
                    st.info("No DNA extraction records found.")
            else:
                st.info("No extraction columns mapped.")

            st.divider()

            st.markdown("#### 🧪 Library Preparation")
            lib_cols = [c for c in ['library_id', 'library_type', 'library_operator', 'library_date'] if c in data.columns]
            if lib_cols:
                libraries_df = data[lib_cols].drop_duplicates().dropna(how='all')
                if not libraries_df.empty:
                    st.dataframe(libraries_df, width="stretch", hide_index=True)
                else:
                    st.info("No library preparation records found.")

        with tab3:
            st.subheader("Bioinformatics Overview")
            
            seq_cols = [c for c in ['fastq_name', 'raw_reads', 'tot_reads_map', 'endo_dna'] if c in data.columns]
            seq_data = data[seq_cols].drop_duplicates().dropna(subset=['fastq_name']) if 'fastq_name' in data.columns else pd.DataFrame()

            if not seq_data.empty:
                tot_raw = seq_data['raw_reads'].sum() if 'raw_reads' in seq_data.columns else 0
                tot_mapped = seq_data['tot_reads_map'].sum() if 'tot_reads_map' in seq_data.columns else 0
                avg_endo = seq_data['endo_dna'].mean() if 'endo_dna' in seq_data.columns else 0

                c1, c2, c3 = st.columns(3)
                c1.metric("Total Raw Reads", f"{int(tot_raw):,}" if pd.notnull(tot_raw) else "0")
                c2.metric("Total Mapped Reads", f"{int(tot_mapped):,}" if pd.notnull(tot_mapped) else "0")
                c3.metric("Avg Endogenous DNA", f"{avg_endo:.2f}%" if pd.notnull(avg_endo) else "N/A")

                st.markdown("#### FASTQ Files & Details")
                st.dataframe(seq_data, width="stretch", hide_index=True)
            else:
                st.info("Sequencing data not yet available for this sample.")

def project_view():
    """
    Renders macro-level project dashboards including KPI summary metrics and Plotly charts (Site distribution & Material counts).
    
    Inputs:
    - None
    
    Outputs:
    - None
    """
    st.title("📁 Project Management Overview")

    selected_project, selected_schema = get_active_project_context()

    project_config = next(
                (p for p in db.config["database"]["projects"] if p["schema_name"] == selected_schema),
                None)

    if project_config:
        mapping = project_config["mapping"]
    else:
        raise ValueError(f"No configuration found for schema: {selected_schema}")
    
    # Projects
    project_info = mapping["project_id"]
    project_list = db.get_column_values(table=project_info["base_table"], columns=project_info["column_name"], schema=selected_schema)
    selected_project_ID = st.selectbox("Select Project ID:", [""] + project_list)
    
    if selected_project_ID:
        stats_df = db.get_project_details(selected_project_ID, schema=selected_schema)
        if not stats_df.empty:
            stats = stats_df.iloc[0]
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Individuals", stats['total_individuals'])
            c2.metric("Extractions", stats['total_extractions'])
            c3.metric("Libraries", stats['total_libraries'])

            endo = stats['avg_endogenous']
            c4.metric("Avg Endogenous", f"{endo:.2f}%" if endo is not None else "N/A")

        st.divider()
        
        individual_info = mapping["individual_id"]
        project_info = mapping["project_id"]
        material_info = mapping["material_description"]

        individual_table = individual_info.get("base_table")
        individual_key_col = individual_info.get("column_name")
        project_col = project_info.get("column_name")

        query = text(f"""
            SELECT *
            FROM {selected_schema}.{individual_table}
            WHERE {project_col} = :pid
        """)

        mat_query = text(f"""
            SELECT ps.{material_info["column_name"]}
            FROM {selected_schema}.{material_info["base_table"]} ps
            JOIN {selected_schema}.{individual_table} i
            ON ps.{individual_key_col} = i.{individual_key_col}
            WHERE i.{project_col} = :pid
        """)

        try:
            with db.engine.connect() as conn:
                # Individuals
                full_data = pd.read_sql(query, conn, params={"pid": selected_project_ID})
                # Materials
                mat_data = pd.read_sql(mat_query, conn, params={"pid": selected_project_ID})
        except Exception as e:
            st.error(f"Error in database: {e}")
            return

        # Tabs
        tab1, tab2 = st.tabs(["📊 Analytics", "📋 Sample List"])
        
        with tab1:
            col_a, col_b = st.columns(2)
            
            with col_a:
                st.subheader("Distribution by Site")
                if not full_data.empty and 'site_name' in full_data.columns:
                    site_counts = full_data['site_name'].value_counts().reset_index()
                    site_counts.columns = ['site_name', 'count']
                    fig_site = px.pie(site_counts, names='site_name', values='count', hole=0.4,
                                    color_discrete_sequence=px.colors.qualitative.Pastel)
                    st.plotly_chart(fig_site, width='stretch')
                else:
                    st.info("No site data available.")
                
            with col_b:
                st.subheader("Samples by Material")
                if not mat_data.empty:
                    mat_counts = mat_data['material_description'].value_counts().reset_index()
                    mat_counts.columns = ['material', 'count']
                    fig_mat = px.bar(mat_counts, x='count', y='material', orientation='h', 
                                   color='material', color_discrete_sequence=px.colors.qualitative.Safe)
                    # Miglioriamo il layout del grafico a barre
                    fig_mat.update_layout(showlegend=False, yaxis={'categoryorder':'total ascending'})
                    st.plotly_chart(fig_mat, width='stretch')
                else:
                    st.info("No material records for this project.")

        with tab2:
            st.subheader("Complete Project Records")
            st.dataframe(full_data, width='stretch', hide_index=True)
            
            csv = full_data.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download CSV",
                data=csv,
                file_name=f"{selected_project_ID.replace(' ', '_')}_data.csv",
                mime="text/csv",)

def configuration_view():
    """
    Interactive configuration editor for mappings and table hierarchies.

    The page reads PostgreSQL metadata through SQLAlchemy, so it can offer only
    tables and foreign-key relationships that actually exist. It never creates
    or alters database objects.
    """
    st.title("⚙️ Configuration")

    config_path = default_configuration_path()
    try:
        with config_path.open("r", encoding="utf-8-sig") as config_file:
            config = json.load(config_file)
    except (OSError, json.JSONDecodeError) as error:
        st.error(f"Could not read {config_path.name}: {error}")
        return

    projects = config.get("database", {}).get("projects", [])
    if not projects:
        st.error("No projects are configured.")
        return

    project_labels = [
        project.get("name") or project.get("schema_name") or f"Project {index + 1}"
        for index, project in enumerate(projects)
    ]
    selected_label = st.selectbox("Project", project_labels)
    project_index = project_labels.index(selected_label)
    project = copy.deepcopy(projects[project_index])
    schema = str(project.get("schema_name", "")).strip()

    with st.expander("Project settings", expanded=True):
        project_name = st.text_input("Project name", value=project.get("name", ""))
        schema_name = st.text_input("Schema name", value=schema)
        backups_path = st.text_input(
            "Backups directory",
            value=project.get("backups_path", ""),
            help="Optional. Relative paths are resolved from the configuration directory.",
        )
        project["name"] = project_name.strip()
        project["schema_name"] = schema_name.strip()
        project["backups_path"] = backups_path.strip()

    st.divider()
    st.subheader("Foreign-key graph")
    st.caption(
        "This graph is read-only. It shows relationships discovered from PostgreSQL "
        "and is not SQL that Nawia will execute."
    )

    inspector = sqlalchemy_inspect(db.engine)
    tables = sorted(inspector.get_table_names(schema=schema))
    graph_edges = []
    for child in tables:
        for foreign_key in inspector.get_foreign_keys(child, schema=schema):
            parent = foreign_key.get("referred_table")
            if parent not in tables:
                continue
            child_columns = foreign_key.get("constrained_columns") or []
            parent_columns = foreign_key.get("referred_columns") or []
            if child_columns and parent_columns:
                graph_edges.append(
                    {
                        "child": child,
                        "parent": parent,
                        "join": ", ".join(
                            f"{child_column} -> {parent_column}"
                            for child_column, parent_column in zip(
                                child_columns, parent_columns
                            )
                        ),
                    }
                )

    if not graph_edges:
        st.info("No foreign keys were discovered in this schema.")
    else:
        st.dataframe(
            pd.DataFrame(graph_edges),
            width="stretch",
            hide_index=True,
        )

        node_names = list(dict.fromkeys(
            [edge["parent"] for edge in graph_edges]
            + [edge["child"] for edge in graph_edges]
        ))
        node_positions = {
            name: (
                0.5 + 0.42 * math.cos(2 * math.pi * index / len(node_names)),
                0.5 + 0.42 * math.sin(2 * math.pi * index / len(node_names)),
            )
            for index, name in enumerate(node_names)
        }

        edge_x = []
        edge_y = []
        for edge in graph_edges:
            x0, y0 = node_positions[edge["parent"]]
            x1, y1 = node_positions[edge["child"]]
            edge_x.extend([x0, x1, None])
            edge_y.extend([y0, y1, None])

        figure = go.Figure()
        figure.update_layout(annotations=[
            {
                "x": node_positions[edge["child"]][0] - (
                    node_positions[edge["child"]][0]
                    - node_positions[edge["parent"]][0]
                ) * 0.12,
                "y": node_positions[edge["child"]][1] - (
                    node_positions[edge["child"]][1]
                    - node_positions[edge["parent"]][1]
                ) * 0.12,
                "ax": node_positions[edge["parent"]][0] + (
                    node_positions[edge["child"]][0]
                    - node_positions[edge["parent"]][0]
                ) * 0.12,
                "ay": node_positions[edge["parent"]][1] + (
                    node_positions[edge["child"]][1]
                    - node_positions[edge["parent"]][1]
                ) * 0.12,
                "xref": "x",
                "yref": "y",
                "axref": "x",
                "ayref": "y",
                "text": "",
                "showarrow": True,
                "arrowhead": 3,
                "arrowsize": 1.2,
                "arrowwidth": 1.5,
                "arrowcolor": "#566573",
            }
            for edge in graph_edges
        ])
        figure.add_trace(go.Scatter(
            x=edge_x,
            y=edge_y,
            mode="lines",
            line=dict(width=1, color="#8b96a3"),
            hoverinfo="none",
        ))
        figure.add_trace(go.Scatter(
            x=[node_positions[name][0] for name in node_names],
            y=[node_positions[name][1] for name in node_names],
            mode="markers+text",
            text=node_names,
            textposition="top center",
            marker=dict(size=18, color="#2f81f7"),
            hovertext=[
                f"{name}<br>{len([edge for edge in graph_edges if edge['child'] == name or edge['parent'] == name])} relationship(s)"
                for name in node_names
            ],
            hoverinfo="text",
        ))
        figure.update_layout(
            height=420,
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis=dict(visible=False),
            yaxis=dict(visible=False),
            showlegend=False,
        )
        st.plotly_chart(figure, width="stretch")

    st.divider()
    st.subheader("Hierarchy editor")
    st.caption(
        "Add one row per child table. Nawia will save only relationships backed by "
        "a real foreign key."
    )

    relationship_options = [
        f"{edge['child']} -> {edge['parent']} ({edge['join']})"
        for edge in graph_edges
    ]
    existing_hierarchies = project.get("table_hierarchies", {})
    existing_rows = []
    for child, relationship in existing_hierarchies.items():
        if not isinstance(relationship, dict):
            continue
        parent = relationship.get("parent")
        join_on = relationship.get("join_on")
        matching_join = next(
            (
                edge["join"]
                for edge in graph_edges
                if edge["child"] == child
                and edge["parent"] == parent
                and join_on in edge["join"]
            ),
            None,
        )
        option = next(
            (
                option
                for option in relationship_options
                if option.startswith(f"{child} -> {parent} ")
            ),
            relationship_options[0] if relationship_options else None,
        )
        if option:
            existing_rows.append({"relationship": option, "enabled": True})

    if not relationship_options:
        st.warning("Cannot add a hierarchy because no foreign keys are available.")
        selected_rows = []
    else:
        selected_rows = st.data_editor(
            pd.DataFrame(existing_rows, columns=["relationship", "enabled"]),
            column_config={
                "relationship": st.column_config.SelectboxColumn(
                    "Foreign key",
                    options=relationship_options,
                    required=True,
                ),
                "enabled": st.column_config.CheckboxColumn("Enabled", default=True),
            },
            num_rows="dynamic",
            width="stretch",
            hide_index=True,
            key="hierarchy_editor",
        ).to_dict("records")

    hierarchies = {}
    for row in selected_rows:
        if not row.get("enabled"):
            continue
        selected_option = row.get("relationship")
        edge = next(
            (
                edge
                for edge in graph_edges
                if f"{edge['child']} -> {edge['parent']} ({edge['join']})"
                == selected_option
            ),
            None,
        )
        if edge is None:
            continue
        child_columns = [
            foreign_key.get("constrained_columns") or []
            for foreign_key in inspector.get_foreign_keys(edge["child"], schema=schema)
            if foreign_key.get("referred_table") == edge["parent"]
        ]
        if child_columns:
            hierarchies[edge["child"]] = {
                "parent": edge["parent"],
                "join_on": child_columns[0][0],
            }
    project["table_hierarchies"] = hierarchies

    st.divider()
    st.subheader("Validation")
    draft_config = copy.deepcopy(config)
    draft_config["database"]["projects"][project_index] = project

    local_errors = validate_configuration(draft_config)
    if local_errors:
        st.error("Local configuration errors:")
        for error in local_errors:
            st.caption(f"- {error}")
    else:
        st.success("The local configuration is structurally valid.")

    if st.button("Test against PostgreSQL", type="secondary"):
        schema_errors, warnings = validate_project_schema(project, inspector)
        if schema_errors:
            st.error("Database validation errors:")
            for error in schema_errors:
                st.caption(f"- {error}")
        else:
            st.success("The configuration matches the selected PostgreSQL schema.")
        for warning in warnings:
            st.warning(warning)

    st.divider()
    with st.expander("JSON preview"):
        st.code(json.dumps(draft_config, indent=2, ensure_ascii=False), language="json")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Save configuration", type="primary", disabled=bool(local_errors)):
            try:
                backup_path = save_configuration(
                    draft_config,
                    config_path,
                    overwrite=True,
                )
                st.cache_resource.clear()
                st.success(
                    f"Configuration saved.{' Backup: ' + str(backup_path) if backup_path else ''}"
                )
            except ConfigurationError as error:
                st.error(str(error))
    with col2:
        st.caption("Saving does not create or alter PostgreSQL tables.")

def exploratory_charts():
    """Build read-only exploratory charts from columns in the active schema."""
    st.title("📈 Explore Data")
    st.write(
        "Choose a table and fields to create an exploratory chart. "
        "This page does not require Nawia field mappings."
    )

    _, schema = get_active_project_context()
    inspector = sqlalchemy_inspect(db.engine)
    tables = sorted(inspector.get_table_names(schema=schema))
    if not tables:
        st.info(f"No tables were found in schema '{schema}'.")
        return

    table_name = st.selectbox("Table", tables)
    column_details = inspector.get_columns(table_name, schema=schema)
    column_names = [column["name"] for column in column_details]

    numeric_columns = []
    date_columns = []
    categorical_columns = []
    for column in column_details:
        column_name = column["name"]
        column_type = column["type"]
        if isinstance(column_type, (DateTime, Date)):
            date_columns.append(column_name)
        elif isinstance(column_type, (Integer, Float, Numeric)):
            numeric_columns.append(column_name)
        else:
            categorical_columns.append(column_name)

    chart_types = []
    if numeric_columns:
        chart_types.extend(["Histogram", "Box plot"])
    if categorical_columns:
        chart_types.append("Bar chart")
    if len(numeric_columns) >= 2:
        chart_types.append("Scatter plot")
    if date_columns and numeric_columns:
        chart_types.append("Line chart")

    if not chart_types:
        st.info("This table has no supported columns for the available chart types.")
        return

    chart_type = st.selectbox("Chart type", chart_types)
    selected_columns = []

    if chart_type == "Histogram":
        value_column = st.selectbox("Numeric field", numeric_columns)
        selected_columns = [value_column]
    elif chart_type == "Box plot":
        value_column = st.selectbox("Numeric field", numeric_columns)
        group_column = st.selectbox(
            "Group by (optional)",
            ["(no grouping)"] + categorical_columns,
        )
        selected_columns = [value_column]
        if group_column != "(no grouping)":
            selected_columns.append(group_column)
    elif chart_type == "Bar chart":
        category_column = st.selectbox("Category field", categorical_columns)
        numeric_value = st.selectbox(
            "Numeric field (optional)",
            ["(count records)"] + numeric_columns,
        )
        aggregation = "count"
        if numeric_value != "(count records)":
            aggregation = st.selectbox(
                "Summarize numeric values by",
                ["mean", "median", "sum"],
            )
            selected_columns = [category_column, numeric_value]
        else:
            selected_columns = [category_column]
    elif chart_type == "Scatter plot":
        x_column = st.selectbox("X-axis", numeric_columns, key="explore_scatter_x")
        y_options = [column for column in numeric_columns if column != x_column]
        y_column = st.selectbox("Y-axis", y_options, key="explore_scatter_y")
        selected_columns = [x_column, y_column]
    else:
        x_column = st.selectbox("Date/time field", date_columns)
        y_column = st.selectbox("Numeric field", numeric_columns)
        selected_columns = [x_column, y_column]

    st.caption(
        "Charts use at most 10,000 rows returned by the database for responsiveness. "
        "Treat them as exploratory, not as a statistically representative sample."
    )
    if not st.button("Build chart", type="primary"):
        return

    reflected_table = Table(
        table_name,
        MetaData(),
        schema=schema,
        autoload_with=db.engine,
    )
    statement = select(
        *(reflected_table.c[column] for column in selected_columns)
    ).limit(10000)
    try:
        with db.engine.connect() as connection:
            chart_data = pd.read_sql(statement, connection)
    except SQLAlchemyError as error:
        st.error(f"Could not read chart data from '{table_name}': {error}")
        return

    if chart_data.empty:
        st.info("The selected table has no rows to chart.")
        return

    if chart_type == "Histogram":
        figure = px.histogram(chart_data, x=value_column)
    elif chart_type == "Box plot":
        figure = px.box(
            chart_data,
            x=None if group_column == "(no grouping)" else group_column,
            y=value_column,
        )
    elif chart_type == "Bar chart":
        if numeric_value == "(count records)":
            counts = (
                chart_data[category_column]
                .fillna("(missing)")
                .astype(str)
                .value_counts()
                .head(30)
                .rename_axis(category_column)
                .reset_index(name="records")
            )
            figure = px.bar(counts, x=category_column, y="records")
        else:
            chart_data[category_column] = (
                chart_data[category_column].fillna("(missing)").astype(str)
            )
            grouped = (
                chart_data.groupby(category_column, dropna=False)[numeric_value]
                .agg(aggregation)
                .reset_index()
                .sort_values(numeric_value, ascending=False)
                .head(30)
            )
            figure = px.bar(grouped, x=category_column, y=numeric_value)
    elif chart_type == "Scatter plot":
        figure = px.scatter(chart_data, x=x_column, y=y_column)
    else:
        grouped = (
            chart_data.groupby(x_column, dropna=True)[y_column]
            .mean()
            .reset_index()
            .sort_values(x_column)
        )
        figure = px.line(grouped, x=x_column, y=y_column, markers=True)

    st.plotly_chart(figure, width="stretch")
    st.dataframe(chart_data, width="stretch", hide_index=True)

# Navigation bar
pg = st.navigation({
    "Management": [
        st.Page(home_page, title="Home", icon="🏠"),
        st.Page(full_db, title="Full Database", icon="📊"),
    ],
    "Data Entry": [
        st.Page(upload_data, title="Upload Excel", icon="📤"),
        st.Page(edit_entries, title="Edit Entries", icon="📝"),
    ],
    "Deep Dive": [
        st.Page(sample_view, title="Sample View", icon="🧬"),
        st.Page(project_view, title="Project View", icon="📁"),
        st.Page(exploratory_charts, title="Explore Data", icon="📈"),
    ],
    "System": [
        st.Page(backup, title="Backup", icon="💾"),
        st.Page(configuration_view, title="Configuration", icon="⚙️"),
    ]
})

# Execution
pg.run()