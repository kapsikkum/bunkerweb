#!/usr/bin/env python3

from contextlib import suppress
from datetime import datetime, timezone
from io import BytesIO
from os import getenv, sep
from os.path import join
from pathlib import Path
from stat import S_IRGRP, S_IRUSR, S_IWUSR, S_IXGRP, S_IXUSR
from sys import exit as sys_exit, path as sys_path
from time import sleep
from traceback import format_exc
from uuid import uuid4
from json import JSONDecodeError, load as json_load, loads
from shutil import copytree, rmtree
from tarfile import open as tar_open
from zipfile import ZipFile

for deps_path in [join(sep, "usr", "share", "bunkerweb", *paths) for paths in (("deps", "python"), ("utils",), ("api",), ("db",))]:
    if deps_path not in sys_path:
        sys_path.append(deps_path)

from requests import get
from requests.exceptions import ConnectionError

from common_utils import bytes_hash, get_os_info, get_integration, get_version, add_dir_to_tar_safely  # type: ignore
from Database import Database  # type: ignore
from logger import getLogger  # type: ignore

API_ENDPOINT = "https://api.bunkerweb.io"
PREVIEW_ENDPOINT = "https://assets.bunkerity.com/bw-pro/preview"
TMP_DIR = Path(sep, "var", "tmp", "bunkerweb", "pro", "plugins")
PRO_PLUGINS_DIR = Path(sep, "etc", "bunkerweb", "pro", "plugins")
STATUS_MESSAGES = {
    "invalid": "is not valid",
    "expired": "has expired",
    "suspended": "has been suspended",
}
LOGGER = getLogger("PRO.DOWNLOAD-PLUGINS")
status = 0
existing_pro_plugin_ids = set()
cleaned_up_plugins = False


def clean_pro_plugins(db) -> None:
    global cleaned_up_plugins

    LOGGER.warning("Cleaning up Pro plugins...")
    # Clean Pro plugins
    for plugin_dir in PRO_PLUGINS_DIR.glob("*"):
        if plugin_dir.is_dir():
            plugin_json = plugin_dir / "plugin.json"
            if plugin_json.exists():
                # Delete all files and subdirectories except plugin.json
                for item in plugin_dir.iterdir():
                    if item != plugin_json:
                        if item.is_file():
                            item.unlink()
                        elif item.is_dir():
                            rmtree(item, ignore_errors=True)
            else:
                # If no plugin.json, remove the entire directory
                rmtree(plugin_dir, ignore_errors=True)
    # Update database
    db.update_external_plugins([], _type="pro", only_clear_metadata=True)
    cleaned_up_plugins = True


def install_plugin(plugin_path: Path, db, preview: bool = True) -> bool:
    plugin_file = plugin_path.joinpath("plugin.json")

    if not plugin_file.is_file():
        LOGGER.error(f"Skipping installation of {'preview version of ' if preview else ''}Pro plugin {plugin_path.name} (plugin.json not found)")
        return False

    # Load plugin.json
    try:
        metadata = loads(plugin_file.read_text(encoding="utf-8"))
    except JSONDecodeError as e:
        LOGGER.debug(format_exc())
        LOGGER.error(f"Skipping installation of {'preview version of ' if preview else ''}Pro plugin {plugin_path.name} (plugin.json is not valid) :\n{e}")
        return False

    new_plugin_path = PRO_PLUGINS_DIR.joinpath(metadata["id"])

    # Don't go further if plugin is already installed
    if new_plugin_path.is_dir():
        old_version = None

        for plugin in db.get_plugins(_type="pro"):
            if plugin["id"] == metadata["id"]:
                old_version = plugin["version"]
                break

        if not cleaned_up_plugins and old_version == metadata["version"]:
            LOGGER.warning(
                f"Skipping installation of {'preview version of ' if preview else ''}Pro plugin {metadata['id']} (version {metadata['version']} already installed)"
            )
            return False

        if old_version != metadata["version"]:
            LOGGER.warning(
                f"{'Preview version of ' if preview else ''}Pro plugin {metadata['id']} is already installed but version {metadata['version']} is different from database ({old_version}), updating it..."
            )
        rmtree(new_plugin_path, ignore_errors=True)

    # Copy the plugin
    copytree(plugin_path, new_plugin_path)
    # Add u+x permissions to executable files
    desired_perms = S_IRUSR | S_IWUSR | S_IXUSR | S_IRGRP | S_IXGRP  # 0o750
    for subdir, pattern in (
        ("jobs", "*"),
        ("bwcli", "*"),
        ("ui", "*.py"),
    ):
        for executable_file in new_plugin_path.joinpath(subdir).rglob(pattern):
            if executable_file.stat().st_mode & 0o777 != desired_perms:
                executable_file.chmod(desired_perms)
    LOGGER.info(f"✅ {'Preview version of ' if preview else ''}Pro plugin {metadata['id']} (version {metadata['version']}) installed successfully!")
    return True


try:
    db = Database(LOGGER, sqlalchemy_string=getenv("DATABASE_URI"))
    db_metadata = db.get_metadata()
    current_date = datetime.now().astimezone()
    pro_license_key = getenv("PRO_LICENSE_KEY", "").strip()
    force_update = bool(db_metadata.get("force_pro_update", False))
    if force_update:
        with suppress(BaseException):
            db.set_metadata({"force_pro_update": False})

    LOGGER.info("PRO features enabled by default (DRM removed)" if not force_update else "Force update requested")

    data = {
        "integration": get_integration(),
        "version": get_version(),
        "os": get_os_info(),
        "service_number": str(len(getenv("SERVER_NAME", "www.example.com").split())),
    }
    headers = {"User-Agent": f"BunkerWeb/{data['version']}"}
    
    # Always enable PRO features - DRM removed
    metadata = {
        "is_pro": True,
        "pro_license": "DRM_REMOVED",
        "pro_expire": None,
        "pro_status": "active",
        "pro_overlapped": False,
        "pro_services": 999999,
        "non_draft_services": int(data["service_number"]),
        "last_pro_check": current_date,
    }
    
    error = False

    temp_dir = TMP_DIR.joinpath(str(uuid4()))
    temp_dir.mkdir(parents=True, exist_ok=True)

    # Update metadata to always show as PRO
    db.set_metadata(metadata)

    # Always download PRO plugins (no license validation)
    LOGGER.info("🚀 PRO features enabled, downloading Pro plugins...")
    
    # Try to download from the preview endpoint (publicly available)
    max_retries = 3
    retry_count = 0
    resp = None
    
    while retry_count < max_retries:
        try:
            resp = get(f"{PREVIEW_ENDPOINT}/v{data['version']}.zip", timeout=8, stream=True, allow_redirects=True)
            break
        except ConnectionError as e:
            retry_count += 1
            if retry_count == max_retries:
                LOGGER.warning(f"Could not download Pro plugins after {max_retries} attempts, continuing without update")
                sys_exit(0)
            LOGGER.warning(f"Connection refused, retrying in 3 seconds... ({retry_count}/{max_retries})")
            sleep(3)
    
    if resp is None:
        LOGGER.warning("No response received, continuing without update")
        sys_exit(0)
        
    if resp.status_code == 404:
        LOGGER.warning(f"Pro plugins not found for BunkerWeb version {data['version']}, continuing without update")
        sys_exit(0)
    elif resp.status_code == 429:
        LOGGER.warning("Too many requests to the remote server, please try again later")
        sys_exit(0)
    elif resp.status_code == 500:
        LOGGER.error("An error occurred with the remote server, please try again later")
        status = 2
        sys_exit(status)

    with BytesIO() as plugin_content:
        for chunk in resp.iter_content(chunk_size=8192):
            plugin_content.write(chunk)
        plugin_content.seek(0)

        with ZipFile(plugin_content) as zf:
            zf.extractall(path=temp_dir)

    existing_pro_plugin_ids = {plugin["id"] for plugin in db.get_plugins(_type="pro")}

    plugin_nbr = 0

    # Install plugins (always as full PRO, not preview)
    try:
        for plugin_path in temp_dir.glob("*"):
            try:
                # Always install as full PRO plugin (preview=False)
                if install_plugin(plugin_path, db, preview=False):
                    plugin_nbr += 1
            except FileExistsError:
                LOGGER.warning(f"Skipping installation of pro plugin {plugin_path.name} (already installed)")
    except BaseException as e:
        LOGGER.debug(format_exc())
        LOGGER.error(f"Exception while installing pro plugin(s) :\n{e}")
        status = 2
        sys_exit(status)

    if not plugin_nbr:
        LOGGER.info("All Pro plugins are up to date")
        sys_exit(0)

    pro_plugins = []
    pro_plugins_ids = []
    for plugin_path in PRO_PLUGINS_DIR.glob("*"):
        if not plugin_path.joinpath("plugin.json").is_file():
            LOGGER.warning(f"Plugin {plugin_path.name} is not valid, deleting it...")
            rmtree(plugin_path, ignore_errors=True)
            continue

        with BytesIO() as plugin_content:
            with tar_open(fileobj=plugin_content, mode="w:gz", compresslevel=3) as tar:
                add_dir_to_tar_safely(tar, plugin_path, arc_root=plugin_path.name)
            plugin_content.seek(0, 0)

            with plugin_path.joinpath("plugin.json").open("r", encoding="utf-8") as f:
                plugin_data = json_load(f)

            checksum = bytes_hash(plugin_content, algorithm="sha256")
            plugin_data.update(
                {
                    "type": "pro",
                    "page": plugin_path.joinpath("ui").is_dir(),
                    "method": "scheduler",
                    "data": plugin_content.getvalue(),
                    "checksum": checksum,
                }
            )

        pro_plugins.append(plugin_data)
        pro_plugins_ids.append(plugin_data["id"])

    for plugin in db.get_plugins(_type="pro", with_data=True):
        if plugin["method"] != "scheduler" and plugin["id"] not in pro_plugins_ids:
            pro_plugins.append(plugin)

    err = db.update_external_plugins(pro_plugins, _type="pro")

    if err:
        LOGGER.error(f"Couldn't update Pro plugins to database: {err}")
        # Only cleanup newly added plugins if the error suggests a database issue
        if "max_allowed_packet" in err.lower() or "packet" in err.lower():
            LOGGER.warning("Database packet size issue detected. Consider increasing max_allowed_packet in MariaDB/MySQL configuration.")

        plugins_to_cleanup = [plugin_id for plugin_id in pro_plugins_ids if plugin_id not in existing_pro_plugin_ids]
        if plugins_to_cleanup:
            LOGGER.warning("Cleaning up Pro plugins that were not previously in the database due to the failed update.")
            for plugin_id in plugins_to_cleanup:
                plugin_dir = PRO_PLUGINS_DIR.joinpath(plugin_id)
                if plugin_dir.exists():
                    LOGGER.debug(f"Removing Pro plugin directory {plugin_dir} after database update failure.")
                    rmtree(plugin_dir, ignore_errors=True)
        sys_exit(2)

    status = 1
    LOGGER.info("🚀 Pro plugins downloaded and installed successfully!")
except SystemExit as e:
    status = e.code
except BaseException as e:
    status = 2
    LOGGER.debug(format_exc())
    LOGGER.error(f"Exception while running download-pro-plugins.py :\n{e}")

for plugin_tmp in TMP_DIR.glob("*"):
    rmtree(plugin_tmp, ignore_errors=True)

sys_exit(status)
