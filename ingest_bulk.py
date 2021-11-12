################################################################################


__author__ = "John Russell <john.russell@dreamview.com>"


# Standard libraries
import json
import logging
import os
import re
import shutil
import sys
from tempfile import gettempdir

# Third-party libraries
import sgtk

# Intra-studio libraries
from utils.sg_create_entities import create_asset

# Intra-package libraries
# None

# App-specific libraries
from pymxs import runtime as mxs


# Get the script folder from shotgun script dir
def get_tool_dir(tool_name):
    """Returns directory of tool name.

    Args:
        tool_name (str): Name of tool, with optional wildcards (*).

    Returns:
        str|None: Path to tool.
    """
    result_path = None
    try:
        dreamview_scripts_scriptspath = mxs.DreamView_Scripts_ScriptsPath
    except AttributeError:
        dreamview_scripts_scriptspath = None

    if dreamview_scripts_scriptspath is None:
        return result_path
    tools_dir = os.path.dirname(dreamview_scripts_scriptspath)
    if not os.path.exists(tools_dir):
        return result_path
    check_dirs = list(mxs.getDirectories(os.path.join(tools_dir, tool_name)))
    if not check_dirs:
        return result_path
    result_path = check_dirs[0]

    return result_path


# Import Check-in module
script_folder = get_tool_dir("Check_In*")
if script_folder not in sys.path:
    sys.path.insert(0, script_folder)
from check_in_out import check_in


# GLOBALS

# Debug globals
DEBUG_PRINT = False
DEBUG_SCENE_COUNT_LIMIT = 0  # 0 exports all.
DEBUG_SKIP_ASSET_CHECKIN = False
DEBUG_SKIP_EXPORT_MAX = False
DEBUG_SKIP_QC = False
DEBUG_SKIP_QC_FARM = False
DEBUG_START_NUM = None  # None starts at the beginning.

# Globals
INGEST_COMPANY_ENTITY = None
INGEST_COMPANY_NAME = None
logging.basicConfig()
LOGGER = logging.getLogger()
MANIFEST_ASSETS_PATH = None
MANIFEST_FAILED_PATH = None
MANIFEST_FILE_PATH = None
MANIFEST_MOST_RECENT = None  # Path to "current" manifest file.
QC_EXPORT = True  # True: Export vrscenes; False: render QC passes in place.
# Import these images to render QC vrscenes.
# qc_tool_folder = get_tool_dir("QC-Tool")
# QC_IMAGES = [
#     os.path.join(qc_tool_folder, r"QC-Tool\Textures\UV_Coded.jpg")
# ]
RENDERED_FLAG = "Omit"  # None or "" is off.
SEARCH_PATH = None  # Current dir path used to find 3DS MAX files to ingest.
SG_ENGINE = sgtk.platform.current_engine()
SG = SG_ENGINE.shotgun


def check_file_io_gamma():
    """Checks and updates scene's gamma settings.

    Returns:
        bool: Did the Gamma settings have to be updated?
    """
    result = True
    if mxs.fileInGamma != mxs.displayGamma:
        mxs.fileInGamma = mxs.displayGamma
        result = False
        print("Updating file in gamma to match display gamma!")
    if mxs.fileOutGamma != mxs.displayGamma:
        mxs.fileOutGamma = mxs.displayGamma
        result = False
        print("Updating file out gamma to match display gamma!")
    return result


def check_in_asset(asset_file_path, wrk_order, asset_name, description, metadata, qc_renders=None, pub_others=None):
    """Checks-in supplied MAX file as an asset.

    Args:
        asset_file_path (str): MAX to check-in.
        wrk_order (dict): Work order Shotgun dictionary.
        asset_name (str): Name of asset to check-in.
        description (str): Description of asset for check-in.
        metadata (dict): Object metadata.
        qc_renders (list[str]|None): List of paths to QC renders.
        pub_others (list[str]|None): List of paths to QC vrscene files.

    Returns:
        dict|None: Shotgun File Collection (CustomEntity16) dictionary.
    """
    # Open file
    print("\tOpening {}".format(asset_file_path))
    mxs.loadMaxFile(asset_file_path, useFileUnits=True, quiet=True)

    # # Add images used by QC so vrscenes will render.
    # if pub_others is None:
    #     pub_others = []
    # pub_others.extend(QC_IMAGES)

    json_mat_path = asset_file_path.replace(".max", "_mat_network.json")
    if pub_others is None:
        pub_others = []
    pub_others.append(json_mat_path)

    # Has the asset been ingested before?
    asset = SG.find_one(
        "Asset",
        [["code", "is", asset_name]],
        ["code", "sg_company", "sg_published_files", "sg_asset_package_links"],
        [{"field_name": "id", "direction": "desc"}])

    if asset is None or get_task(asset.get("id")) is None:
        # Has the asset been ingested before?  If so, find the
        # previous deliverable.
        deliverable = SG.find_one("CustomEntity24", [["code", "contains", "{}_Hi Ingest Bulk".format(asset_name)]])

        # Create Asset
        asset = create_asset(SG, LOGGER, SG_ENGINE.context.project, wrk_order, asset_name,
                             deliverable_type="Asset Ingest Bulk", deliverable=deliverable)

        asset = SG.find_one(
            "Asset",
            [["id", "is", asset.get("id")]],
            ["code", "sg_company", "sg_published_files",
             "sg_asset_package_links"])

    # Make sure Company column is filled.
    if not asset.get("sg_company"):
        SG.update("Asset", asset.get("id"), {"sg_company": [INGEST_COMPANY_ENTITY]})

    # Get Task from newly created Asset.
    task = get_task(asset.get("id"))

    # Check in MAX file.
    flag_rendered = "In Progress"
    if qc_renders and RENDERED_FLAG:
        flag_rendered = RENDERED_FLAG

    ############################################################################

    # CHECK-IN
    file_collection = check_in(
        task.get("id"),
        rendered=qc_renders,
        description=description,
        pub_others=pub_others,
        flag_rendered=flag_rendered
    )

    ############################################################################

    # If check-in fails, it returns False.
    if file_collection is False:
        print("Check-in failed.")
        return None

    # Update published files with matrix transform data.
    for pub_file in file_collection.get("sg_published_file_entity_links"):
        pub_file = SG.find_one(
            "PublishedFile",
            [["id", "is", pub_file.get("id")]],
            ["code", "sg_context", "sg_source_transform_matrix"])

        if pub_file.get("sg_context") in ["geo_abc_exported", "geo_max"]:
            SG.update(
                "PublishedFile", pub_file.get("id"), {"sg_source_transform_matrix": metadata.get("original_t_matrix")})

    file_collection = SG.update(
        file_collection.get("type"),
        file_collection.get("id"),
        {
            "sg_bbox_width": metadata.get("bbox_width"),
            "sg_bbox_height": metadata.get("bbox_height"),
            "sg_bbox_depth": metadata.get("bbox_depth"),
            "sg_bbox_units": metadata.get("bbox_units"),
            "sg_mdl_polygon_count": metadata.get("poly_count"),
            "sg_mdl_vertex_count": metadata.get("vert_count"),
            "sg_mtl_bitmap_count": metadata.get("mtl_bitmap_count"),
            "sg_mtl_material_count": metadata.get("mtl_material_count"),
            "sg_mtl_roughness_count": metadata.get("mtl_roughness_count"),
            "sg_mtl_uv_tiles_count": metadata.get("mtl_uv_tiles_count"),
        }
    )

    # Submit vrscenes to farm.
    if not DEBUG_SKIP_QC_FARM and not DEBUG_SKIP_QC and QC_EXPORT:
        jobs = qc_vrscene_farm_submit(task, file_collection)

        if jobs:
            print("Farm Jobs submitted for {}:".format(asset_name))
            for job in jobs:
                print("\t{}".format(job.get("code")))

    return file_collection


def export_vrscene_file(suffix=""):
    """Exports vrscene file for the currently open scene.
    If cam is supplied, the exported file will have cam in the file name.

    Args:
        suffix (str): Filename suffix, if needed.

    Returns:
        str: Path to exported vrscene.
    """
    result = None
    this_file = os.path.join(mxs.maxFilePath, mxs.maxFileName)
    this_file_folder = mxs.getFilenamePath(this_file)
    this_file_name = mxs.getFilenameFile(this_file)
    if mxs.classOf(mxs.VRayRT) == mxs.RendererClass:
        this_renderer = mxs.renderers.current
        if mxs.classOf(this_renderer) != mxs.VRayRT:
            mxs.renderers.current = mxs.VRayRT()

        vrscene_file = os.path.join(this_file_folder, "{}{}.vrscene".format(this_file_name, suffix))
        if os.path.exists(vrscene_file):
            os.remove(vrscene_file)
        mxs.vrayExportVRScene(vrscene_file, exportCompressed=True, exportHEXFormatMesh=True,
                              exportHEXFormatTransf=False, separateFiles=False, stripPaths=True)
        if os.path.exists(vrscene_file):
            result = vrscene_file

        if mxs.renderers.current != this_renderer:
            mxs.renderers.current = this_renderer

    return result


def get_qc_tool():
    """Retrieve the QC Tool.

    Returns:
        pymxs.MXSWrapperBase|None: QC Tool.
    """
    qc_tool = None
    toolkit_ui_path = mxs.execute("global DreamView_Scripts_ScriptsPath; DreamView_Scripts_ScriptsPath")
    if not toolkit_ui_path:
        print("3ds Max Toolkit not found!")
        return qc_tool
    toolkit_ui_path = os.path.dirname(toolkit_ui_path)
    if not os.path.exists(toolkit_ui_path):
        print("3ds Max Toolkit folder not found!")
        return qc_tool
    qc_tool_struct_path = os.path.join(toolkit_ui_path, "QC-Tool/QC-Tool/_Core/QC-Tool_Struct.ms")
    if not os.path.exists(qc_tool_struct_path):
        print("QC-Tool struct file not found!")
        return qc_tool
    qc_tool_struct = mxs.fileIn(os.path.normpath(qc_tool_struct_path))
    qc_tool = qc_tool_struct()
    return qc_tool


def get_task(asset_id):
    """Finds Task associated with Asset.

    Args:
        asset_id (int): Asset ID.

    Returns:
        dict|None: Shotgun Task dictionary.
    """
    task = SG.find_one(
        "Task",
        [
            [
                "entity.CustomEntity25.sg_deliverable.CustomEntity24.sg_link.Asset.id",
                "is",
                asset_id
            ]
        ],
        ["entity"]
    )
    return task


def max_walk(dir_to_search):
    """Generator that walks through a directory structure and yields MAX files.

    Args:
        dir_to_search (str): Current directory being searched.

    Yields:
        list[str]: List of paths to MAX files found in directory.
    """
    names = sorted(os.listdir(dir_to_search))

    dirs, max_files = [], []

    for name in names:
        if os.path.isdir(os.path.join(dir_to_search, name)):
            dirs.append(name)
        else:
            if name.lower().endswith(".max"):
                max_files.append(name)

    # If MAX files are found...
    if max_files:
        yield [os.path.join(dir_to_search, f) for f in max_files]

    # If no MAX files are found, keep digging...
    else:
        for name in dirs:
            new_path = os.path.join(dir_to_search, name)
            if not os.path.islink(new_path):
                for x in max_walk(new_path):
                    yield x


def process_scene(scn_file_path, wrk_order):
    """Process the scene file, checking in assets found in the scene.

    Args:
        scn_file_path (str): Scene path to open and process in original dir.
        wrk_order (dict): Work order dictionary.

    Returns:
        bool: Did the scene process successfully?
    """
    print("Opening scene: {}".format(os.path.basename(scn_file_path)))

    # Open the scene file in Quiet mode.
    mxs.loadMaxFile(scn_file_path, useFileUnits=True, quiet=True)

    json_path = scn_file_path.replace(".max", "_metadata.json")

    shot_name = scn_file_path.split("__ingest_bulk__")[1].split("\\")[1]

    with open(json_path) as json_file:
        meta_data = json.load(json_file)

    # Check IO gamma.
    check_file_io_gamma()

    # Refresh bitmaps...
    mxs.redrawViews()

    # Get the nodes to check in.
    asset_node = list(mxs.rootScene[mxs.name('world')].object.children)[0]
    asset_name = "{}_{}".format(shot_name, asset_node.Name)

    global MANIFEST_ASSETS_PATH
    MANIFEST_ASSETS_PATH = os.path.join(SEARCH_PATH, "__assets__.txt")
    with open(MANIFEST_ASSETS_PATH, "a") as manifest_assets_file:
        manifest_assets_file.write("{}\n".format(scn_file_path))

    # If DEBUG_SKIP_EXPORT_MAX is True, there is no MAX file to QC or
    # check in.
    if DEBUG_SKIP_EXPORT_MAX:
        print("\tSkipping QC & check-in for all assets in {}".format(scn_file_path))
        return False

    # QC and check-in all the assets found.
    print("QC and Check-in assets for scene:\n{}".format(scn_file_path))
    asset_file_path = scn_file_path
    original_node_name = asset_node.Name
    original_max_path = meta_data.get("original_max_file")
    qc_tool_exports_dir = None

    qc_renders = None
    vr_scenes = None

    # QC asset
    if DEBUG_SKIP_QC:
        if DEBUG_PRINT:
            print("\tSkipping QC for {}".format(asset_name))
    else:
        mxs.clearSelection()

        # Make QC directories.
        max_file_name = os.path.basename(asset_file_path).rsplit(".", 1)[0]
        qc_tool_exports_dir = os.path.join(gettempdir(), "__ingest_bulk_QC__", shot_name, "{}".format(max_file_name))
        if not os.path.isdir(qc_tool_exports_dir):
            os.makedirs(qc_tool_exports_dir)
        qc_max_file_path = "{}_QC.max".format(os.path.join(qc_tool_exports_dir, max_file_name))

        mxs.saveMaxFile(qc_max_file_path, clearNeedSaveFlag=True, useNewFile=True, quiet=True)

        # QC EXPORT
        if QC_EXPORT:
            vr_scenes = qc_vrscene_export(qc_max_file_path)

        # QC RENDER
        else:
            qc_renders = qc_render([qc_max_file_path], "Lookdev", ".PNG", os.path.dirname(qc_max_file_path))

    # CHECK-IN
    if DEBUG_SKIP_ASSET_CHECKIN:
        if DEBUG_PRINT:
            print("\tSkipping Check-in for {}".format(asset_name))
    else:
        description = (
            "Checked-in from 3DS MAX.\n"
            "\n"
            "Original File:\n"
            "{}\n"
            "\n"
            "Original Node:\n"
            "{}".format(original_max_path, original_node_name))

        ########################################################################

        # Check-in asset.
        file_collection = check_in_asset(
            asset_file_path,
            wrk_order,
            asset_name,
            description,
            meta_data,
            qc_renders=qc_renders,
            pub_others=vr_scenes)

        ########################################################################

        if file_collection:
            with open(MANIFEST_ASSETS_PATH, "a") as manifest_assets_file:
                manifest_assets_file.write("\t{}\n".format(asset_name))

            if not DEBUG_SKIP_QC and os.path.exists(qc_tool_exports_dir):
                shutil.rmtree(qc_tool_exports_dir)

    # Reset scene
    mxs.resetMaxFile(mxs.Name("noPrompt"))

    return True


def qc_render(files_list, render_mode, render_ext, output_path):
    """Perform QC renders.

    Args:
        files_list (list(str)): List of paths to MAX files to QC render.
        render_mode (str): Render mode: "Model" or "Lookdev"
        render_ext (str): File extension for output QC renders.
        output_path (str): Directory output path for the QC renders.

    Returns:
        list[str]|None: List of paths to rendered files.
    """
    # get the qc-tool maxscript object
    qc_renders = []
    qc_tool = get_qc_tool()
    if not qc_tool:
        return qc_renders
    # process the files
    for f in files_list:
        f_norm = os.path.normpath(f)
        print("  Opening file: {}".format(f_norm))
        if not mxs.loadMaxFile(f_norm, useFileUnits=True, quiet=True):
            print("Error opening file: {}".format(f_norm))
            return qc_renders
        qc_tool.init()
        if not qc_tool.modelRoot:
            print("Model not found in file: {}".format(f_norm))
            return qc_renders
        if "Model" in render_mode:
            qc_tool.setVal(u"Render Mode", u"Model")
            qc_tool.setVal(u"Output Types", render_ext)
            result = qc_tool.renderAll(outPath=output_path)
            if "Render was cancelled!" in result:
                return qc_renders
        else:  # "Lookdev"
            qc_tool.setVal(u"Render Mode", u"Lookdev")
            qc_tool.setVal(u"Output Types", render_ext)
            qc_tool.setVal(u"Hero Resolution", 2)  # 1000x1000px
            result = qc_tool.renderAll(outPath=output_path)
            if "Render was cancelled!" in result:
                return qc_renders

    if os.path.isdir(output_path):
        qc_renders = [
            os.path.join(output_path, dir_file)
            for dir_file in os.listdir(output_path)
            if dir_file.lower().endswith(".png")]

    return qc_renders


# "process_files" function from QC batch tool
def qc_vrscene_export(max_file_path):
    """Export VRScenes instead of renders.

    Args:
        max_file_path (str): Path to MAX file used to generate vrscene files.

    Returns:
        list[str]|None: List of paths to exported vrscene files.
    """
    qc_vrscenes = None

    qc_tool = get_qc_tool()
    if not qc_tool:
        return qc_vrscenes
    f_norm = os.path.normpath(max_file_path)
    for render_mode in [u"Model", u"Lookdev"]:
        print("  Opening file: {}".format(f_norm))
        if not mxs.loadMaxFile(f_norm, useFileUnits=True, quiet=True):
            continue
        qc_tool.init()
        qc_tool.setVal(u"Render Mode", render_mode)
        qc_tool.setVal(u"Hero Resolution", 2)  # 1000x1000px
        qc_tool.setVal(u"Active Camera", 6)  # Cam_Hero
        if render_mode == u"Lookdev":
            qc_tool.overWhiteMode(1)  # sets background to White

        cam_node = mxs.getNodeByName("Cam_Hero")
        mxs.setProperty(cam_node, "clip_on", False)  # Clipping planes
        mxs.redrawViews()

        if qc_vrscenes is None:
            qc_vrscenes = []
        qc_vrscenes.append(export_vrscene_file("_Cam_Hero_{}".format(render_mode)))
        mxs.saveMaxFile(f_norm.replace("_QC.max", "_QC_Cam_Hero_{}.max".format(render_mode)), quiet=True)

    return qc_vrscenes


def qc_vrscene_farm_submit(task, file_collection):
    """Submits vrscenes of an asset to the farm.

    Args:
        task (dict): Shotgun Task dictionary.
        file_collection (dict): Shotgun File Collection dictionary.

    Returns:
        jobs (list[dict]): List of Shotgun FarmJob dictionaries.
    """
    pub_files = []
    jobs = []

    file_collection = SG.find_one(
        file_collection.get("type"),
        [["id", "is", file_collection.get("id")]],
        ["sg_published_file_entity_links"])

    for pub_file in file_collection.get("sg_published_file_entity_links"):
        if pub_file.get("name").endswith(".vrscene") and "_QC_" in pub_file.get("name"):
            pub_files.append(pub_file)

    for pub_file in pub_files:
        sg_dict = {
            "code": "{}".format(pub_file.get("name").replace(".vrscene", "_qc_render")),
            "project": SG_ENGINE.context.project,
            "sg_asset_package_upstream": file_collection,
            "sg_asset_package_downstream": file_collection,
            "sg_deliverable_package": task.get("entity"),
            "sg_frames": "{}".format(int(mxs.currentTime.frame)),
            "sg_frames_increment": 1,
            "sg_image_filename": "{}".format(pub_file.get("name").replace(".vrscene", ".jpg")),
            "sg_image_height": 1000,
            "sg_image_width": 1000,
            "sg_media_status_override": "ip",
            "sg_noise_threshold": str(0.05),
            "sg_priority": 30,
            "sg_render_published_file": pub_file,
            "sg_status_list": "que",
            "sg_task": task,
        }
        job = SG.create("CustomThreadedEntity02", sg_dict, return_fields=["code"])

        jobs.append(job)

    return jobs


def search_and_process(search_path, wrk_order, start_num=None):
    """Search given directory for MAX files then process them.

    Args:
        search_path (str): Directory to search.
        wrk_order (dict): Work order dictionary needed to check-in files.
        start_num (int|None): Which number of paths on which to start.
    """
    # Manifest files.
    global MANIFEST_FILE_PATH
    global MANIFEST_MOST_RECENT
    global MANIFEST_FAILED_PATH
    MANIFEST_FILE_PATH = os.path.join(search_path, "__manifest__.txt")
    MANIFEST_MOST_RECENT = os.path.join(search_path, "__most_recent__.txt")
    MANIFEST_FAILED_PATH = os.path.join(search_path, "__failed__.txt")

    json_manifest_path = os.path.join(search_path, "manifest.json")
    with open(json_manifest_path) as json_file:
        manifest_data = json.load(json_file)

    # If the process was interrupted, read the file path in current file and
    # restart processing AFTER that file.
    most_recent_processed_num = None
    skip = False
    if os.path.exists(MANIFEST_MOST_RECENT):
        with open(MANIFEST_MOST_RECENT, "r") as most_recent_processed_file:
            most_recent_processed_num = most_recent_processed_file.read()
        skip = True

    if start_num is not None:
        skip = True

    count = 0  # Total count.
    # If process was interrupted, this is the current count for
    # this round of processing.
    cur_count = 0
    cur_success_count = 0

    print("Searching for scenes to process in {}...".format(search_path))

    # Walk through the search path, file MAX files, process them.
    for num in sorted(manifest_data, key=lambda x: int(x)):
        max_file = manifest_data[num]
        if skip:
            if start_num is not None:
                count += 1
                if num == str(start_num - 1):
                    skip = False
                continue
            else:
                count += 1
                if most_recent_processed_num == num:
                    skip = False
                continue

        print("\n\nScene found: {}".format(max_file))

        ####################################################################

        # Process the MAX file.
        process_success = process_scene(max_file, wrk_order)

        ###################################################################

        if process_success:
            # Write to manifest files.
            with open(MANIFEST_FILE_PATH, "a") as manifest_file:
                manifest_file.write("{}\n".format(max_file))
            with open(MANIFEST_MOST_RECENT, "w") as most_recent_processed_file:
                most_recent_processed_file.write(num)

            cur_success_count += 1
        else:
            with open(MANIFEST_FAILED_PATH, "a") as failed_processed_file:
                failed_processed_file.write("{}\n".format(max_file))

        # Increment count.
        count += 1
        cur_count += 1

        if DEBUG_SCENE_COUNT_LIMIT and cur_count >= DEBUG_SCENE_COUNT_LIMIT:
            break

    print("{} total MAX scenes.".format(count))
    print("{} current MAX scenes attempted.".format(cur_count))
    print("{} current MAX scenes succeeded.".format(cur_success_count))
    print("Manifest written: {}".format(MANIFEST_FILE_PATH))

    if os.path.exists(MANIFEST_MOST_RECENT):
        os.remove(MANIFEST_MOST_RECENT)


def sort_key_alphanum(s, case_sensitive=False):
    """Turn a string into a list of string and number chunks.
    Using this key for sorting will order alphanumeric strings properly.

    "ab123cd" -> ["ab", 123, "cd"]

    Args:
        s (str): Given string.
        case_sensitive (bool): If True, capital letters will go first.
    Returns:
        list[int|str]: Mixed list of strings and integers in the order they occurred in the given string.
    """
    def cast_to_int(str_chunk, cs_snstv):
        """Convert string to integer if it's a number.
        In certain cases, ordering filenames takes case-sensitivity into consideration.

        Windows default sorting behavior:
        ["abc1", "ABC2", "abc3"]

        Linux, Mac, PySide, & Python default sorting behavior:
        ["ABC2", "abc1", "abc3"]

        Case-sensitivity is off by default.

        Args:
            str_chunk (str): Given string chunk.
            cs_snstv (bool): If True, capital letters will go first.

        Returns:
            int|str: If the string represents a number, return the number.
                Otherwise, return the string.
        """
        try:
            return int(str_chunk)
        except ValueError:
            if cs_snstv:
                return str_chunk
            # Make it lowercase so the ordering is no longer case-sensitive.
            return str_chunk.lower()

    ##########

    return [cast_to_int(chunk, case_sensitive) for chunk in re.split("([0-9]+)", s)]


if __name__ == "__main__":
    # Work order
    work_order = {"type": "CustomEntity17", "id": 2566}  # Asset Library
    # work_order = {"type": "CustomEntity17", "id": 2232}  # Evermotion
    # work_order = {"type": "CustomEntity17", "id": 48}  # CG Trader

    # Company
    INGEST_COMPANY_NAME = "Evermotion"  # Must match name in Shotgun
    # INGEST_COMPANY_NAME = "CG Trader"
    INGEST_COMPANY_ENTITY = SG.find_one("CustomNonProjectEntity02", [["code", "is", INGEST_COMPANY_NAME]])

    # Directory to search.
    SEARCH_PATH = r"Q:\Shared drives\DVS_StockAssets\Evermotion\From_Adnet\__ingest_bulk__"

    # Specific scenes to process
    scene_file_paths = [
        # r"Q:\Shared drives\DVS_StockAssets\Evermotion\AE34_001\scenes\AE34_001.max",
        # r"Q:\Shared drives\DVS_StockAssets\Evermotion\AE34_002\002\scenes\AE34_002_forestPack_2011.max",
        # r"Q:\Shared drives\DVS_StockAssets\Evermotion\AE34_003\003\AE34_003.max",
        # r"Q:\Shared drives\DVS_StockAssets\Evermotion\AE34_005\005\AE34_005.max"
    ]

    # Silence V-Ray dialog for older versions.
    mxs.setVRaySilentMode()

    curr_path = os.path.join(mxs.maxFilePath, mxs.maxFileName)

    # If a scene is already open, process it.
    if curr_path:
        success = process_scene(curr_path, work_order)
    # If you have specific scenes to process...
    elif scene_file_paths:
        for scene_file_path in scene_file_paths:
            success = process_scene(scene_file_path, work_order)
    # Or search a directory and process
    else:
        search_and_process(SEARCH_PATH, work_order, DEBUG_START_NUM)

    # Reset scene.
    mxs.resetMaxFile(mxs.Name("noPrompt"))

    print("== Ingest Complete ==")

    # mxs.quitMax(mxs.Name("noprompt"))
