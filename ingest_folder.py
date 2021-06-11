import logging
import os
import sys

import sgtk

app_path = r"C:\Users\john.russell\Code\git_stuff\dreamview-studios-inc\DreamViewStudios\application\py"
if app_path not in sys.path:
    sys.path.insert(0, app_path)
from checkio.app import check_in
from common.sg_create_entities import create_asset


logging.basicConfig(format="%(asctime)s %(message)s", level=logging.DEBUG)
LOGGER = logging.getLogger()
# parser = argparse.ArgumentParser()
RENDERED_FLAG = "Omit"  # None or "" is off.
SG_ENGINE = sgtk.platform.current_engine()
SG = SG_ENGINE.shotgun
# SG = shotgun
WORK_ORDER = None


def check_in_asset(asset_name, wrk_order, asset_files, renders=None):
    """

    Args:
        asset_name:
        wrk_order:
        asset_files:
        renders:

    Returns:
        dict:
    """
    asset = SG.find_one(
        "Asset",
        [["code", "is", asset_name]],
        ["code", "sg_company", "sg_published_files", "sg_asset_package_links"],
        [{"field_name": "id", "direction": "desc"}])

    if asset is None or get_task(asset.get("id")) is None:
        # Has the asset been ingested before?  If so, find the previous deliverable.
        deliverable = SG.find_one("CustomEntity24", [["code", "contains", "{}_Hi Ingest Bulk".format(asset_name)]])

        # Create Asset
        asset = create_asset(SG, LOGGER, SG_ENGINE.context.project, wrk_order, asset_name,
                             deliverable_type="Asset Ingest Bulk", deliverable=deliverable)

        asset = SG.find_one(
            "Asset",
            [["id", "is", asset.get("id")]],
            ["code", "sg_company", "sg_published_files", "sg_asset_package_links"])

    # Make sure Company column is filled.
    if not asset.get("sg_company"):
        SG.update("Asset", asset.get("id"), {"sg_company": [INGEST_COMPANY_ENTITY]})

    # Get Task from newly created Asset.
    task = get_task(asset.get("id"))

    flag_rendered = "In Progress"
    if RENDERED_FLAG:
        flag_rendered = RENDERED_FLAG

    ############################################################################

    # CHECK-IN
    return check_in(
        task.get("id"),
        rendered=renders,
        # description=description,
        pub_others=asset_files,
        pub_exported=True,
        flag_rendered=flag_rendered)


def get_task(asset_id):
    """Finds Task associated with Asset.

    Args:
        asset_id (int): Asset ID.

    Returns:
        dict|None: Shotgun Task dictionary.
    """
    task = SG.find_one(
        "Task",
        [["entity.CustomEntity25.sg_deliverable.CustomEntity24.sg_link.Asset.id", "is", asset_id]],
        ["entity"])
    return task


def process_folder(foldr_pth, wrk_ordr):
    """

    Args:
        foldr_pth (str): Path to folder to process.
        wrk_ordr (dict): Work Order (CustomEntity17) Shotgun Entity.

    Returns:

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

    # Logging
    LOGGER.debug("asset_file_paths:")
    for x in sorted(asset_file_paths):
        LOGGER.debug("\t{}".format(x))
    LOGGER.debug("renders_file_paths:")
    for x in sorted(renders_file_paths):
        LOGGER.debug("\t{}".format(x))

    return check_in_asset(asset_name, wrk_ordr, asset_file_paths, renders_file_paths)


if __name__ == "__main__":
    work_order = {"type": "CustomEntity17", "id": 2566}  # Asset Library

    # Company
    INGEST_COMPANY_NAME = "CG Trader"  # Must match name in Shotgun
    INGEST_COMPANY_ENTITY = SG.find_one("CustomNonProjectEntity02", [["code", "is", INGEST_COMPANY_NAME]])

    search_folder_paths = [
        # r"Q:\Shared drives\DVS_StockAssets\CGTrader\832980_Gums Teeth and Tongue",
        r"Q:\Shared drives\DVS_StockAssets\CGTrader\784661_New Apple TV Set",
        # r"Q:\Shared drives\DVS_StockAssets\CGTrader\811464_Carpet Natural Jute ZARA HOME",
        # r"Q:\Shared drives\DVS_Production\Active\Suppliers\Sauder\429180 Wall\Reference\Props_and_Artwork\3120_CANDLE_27"
    ]

    if search_folder_paths:
        for folder_path in search_folder_paths:
            file_collection = process_folder(folder_path, work_order)
            print(file_collection)
