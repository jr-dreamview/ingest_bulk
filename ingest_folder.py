#! python3

from argparse import ArgumentParser, ArgumentTypeError
import json
import logging
import os
import sys


app_path = r"C:\Users\john.russell\Code\git_stuff\shotgunsoftware\tk-core\python"
if app_path not in sys.path:
    sys.path.insert(0, app_path)
# import sgtk
app_path = r"C:\Users\john.russell\Code\git_stuff\shotgunsoftware\python-api"
if app_path not in sys.path:
    sys.path.insert(0, app_path)
import shotgun_api3

# TODO: Import these from monorepo properly.
app_path = r"C:\Users\john.russell\Code\git_stuff\dreamview-studios-inc\DreamViewStudios\application\py"
if app_path not in sys.path:
    sys.path.insert(0, app_path)
from checkio.app import check_in
from common.sg_create_entities import create_asset


logging.basicConfig(format="%(asctime)s %(message)s", level=logging.DEBUG)
LOGGER = logging.getLogger()
WORK_ORDER_ID_DEFAULT = 2566


def check_in_asset(asset_name, wrk_order, asset_files, renders=None):
    """Check in given files.

    Args:
        asset_name (str): Name of asset.
        wrk_order (dict): Work Order (CustomEntity17) Shotgun Entity to link to deliverable.
            Required fields: "project", "sg_company"
        asset_files (list[str]): List of paths to files to check in.
        renders (list[str]|None): List of path to rendered files to check in.

    Returns:
        dict|None: FileCollection (CustomEntity16) Shotgun Entity of the checked in files.
    """
    asset = sg.find_one(
        "Asset",
        [["code", "is", asset_name]],
        ["code", "sg_company", "sg_published_files", "sg_asset_package_links"],
        [{"field_name": "id", "direction": "desc"}])

    if asset is None or get_task(asset.get("id")) is None:
        # Has the asset been ingested before?  If so, find the previous deliverable.
        deliverable = sg.find_one("CustomEntity24", [["code", "contains", "{}_Hi Ingest Bulk".format(asset_name)]])

        # Create Asset
        asset = create_asset(
            sg, LOGGER, wrk_order, asset_name, deliverable_type="Asset Ingest Bulk", deliverable=deliverable)

        asset = sg.find_one(
            "Asset",
            [["id", "is", asset.get("id")]],
            ["code", "sg_company", "sg_published_files", "sg_asset_package_links"])

    # Get Task from newly created Asset.
    task = get_task(asset.get("id"))

    ####################################################################################################################

    # CHECK-IN
    return check_in(
        task.get("id"), pub_others=asset_files, rendered=renders, flag_rendered="Pending Review", current_engine=sg)


def cli():
    """Command line argument parsing.

    Raises:
        ArgumentTypeError: If provided directory doesn't exist.
        ArgumentTypeError: If work order ID doesn't exist
    """
    parser = ArgumentParser()
    parser.add_argument("-d", "--dir", required=True, help="Directory containing the files of the asset to ingest.")
    parser.add_argument(
        "-w", "--work_order_id", required=False, type=int, help="ID for work order associated with asset to ingest.")

    cliargs = parser.parse_args(sys.argv[1:])

    folder_path = cliargs.dir
    if not os.path.exists(folder_path):
        raise ArgumentTypeError("Ingest halted: Invalid directory.")

    work_order_id = cliargs.work_order_id
    if not work_order_id:
        work_order_id = WORK_ORDER_ID_DEFAULT
    work_order = sg.find_one("CustomEntity17", [["id", "is", work_order_id]], ["code", "project", "sg_company"])
    if work_order is None:
        raise ArgumentTypeError("Ingest halted: Invalid worker order ID.")

    LOGGER.info("folder_path: {}".format(folder_path))
    LOGGER.info("work_order: {}".format(work_order.get("code")))
    LOGGER.info("project: {}".format(work_order.get("project").get("name")))
    LOGGER.info("company: {}".format(work_order.get("sg_company").get("name")))

    ####################################################################################################################

    file_collection = process_folder(folder_path, work_order)

    ####################################################################################################################

    if not file_collection:
        LOGGER.error("No FileCollection returned.")
        return

    if not all(key in file_collection for key in ["code", "asset_sg_asset_package_links_assets"]):
        file_collection.update(
            sg.find_one(
                file_collection.get("type"),
                [["id", "is", file_collection.get("id")]],
                ["code", "asset_sg_asset_package_links_assets"]))
    asset = file_collection.get("asset_sg_asset_package_links_assets")[0]

    LOGGER.info("Ingest complete.")
    LOGGER.info("Asset: {}".format(asset.get("name")))
    LOGGER.info("Created FileCollection: {}".format(file_collection.get("code")))


def get_sg_instance():
    """Creates a sg instance using the given file path to a json file.
    Current formatting of file should be:

    ::
    {
        "script_name": "script_name",
        "base_url": "base_url",
        "api_key": "api_key"
    }

    Returns:
         shotgun_api3.shotgun.Shotgun: sg instance object.
    """
    file_path = os.path.join(os.path.dirname(__file__), "sg_script_credentials.json")
    with open(file_path) as json_data:
        login_data = json.load(json_data)
    sg_instance = shotgun_api3.Shotgun(
        login_data.get("base_url"),
        script_name=login_data.get("script_name"),
        api_key=login_data.get("api_key"))

    return sg_instance


sg = get_sg_instance()


def get_task(asset_id):
    """Finds Task associated with Asset.

    Args:
        asset_id (int): Asset ID.

    Returns:
        dict|None: Shotgun Task dictionary.
    """
    task = sg.find_one(
        "Task",
        [["entity.CustomEntity25.sg_deliverable.CustomEntity24.sg_link.Asset.id", "is", asset_id]],
        ["entity"])
    return task


def process_folder(foldr_pth, wrk_ordr):
    """Search given folder for files and check them in.

    Args:
        foldr_pth (str): Path to folder to process.
        wrk_ordr (dict): Work Order (CustomEntity17) Shotgun Entity.

    Returns:
        dict|None: FileCollection (CustomEntity16) Shotgun Entity of the checked in files.
    """
    asset_name = os.path.basename(foldr_pth)

    asset_file_paths = []
    renders_file_paths = []
    for r, _, f in os.walk(foldr_pth):
        for fl in f:
            file_path = os.path.join(r, fl)
            if os.path.basename(r).lower() == "images":
                renders_file_paths.append(file_path)
            else:
                asset_file_paths.append(file_path)

    LOGGER.debug("asset_file_paths:")
    for x in sorted(asset_file_paths):
        LOGGER.debug("\t{}".format(x))
    LOGGER.debug("renders_file_paths:")
    for x in sorted(renders_file_paths):
        LOGGER.debug("\t{}".format(x))

    return check_in_asset(asset_name, wrk_ordr, asset_file_paths, renders_file_paths)


if __name__ == "__main__":
    try:
        cli()
    except ArgumentTypeError as e:
        LOGGER.error(e)
