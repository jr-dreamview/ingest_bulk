################################################################################


__author__ = "John Russell <john.russell@dreamview.com>"


# Standard libraries
import logging
import os
import re
import sys

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
from dvs_max_lib.meta import Meta

# Import Check-out module
script_folder = get_tool_dir("Check_Out*")
if script_folder not in sys.path:
    sys.path.insert(0, script_folder)
from check_in_out import check_out

# Import QC Batch Tool module
script_folder = get_tool_dir("QC_Batch_Tool")
if script_folder not in sys.path:
    sys.path.insert(0, script_folder)
from qc_batch_tool import get_qc_tool


# GLOBALS

# Debug globals
DEBUG_ASSET_EXPORT_COUNT_LIMIT = 0  # 0 exports all.
DEBUG_PRINT = False
DEBUG_SCENE_COUNT_LIMIT = 0  # 0 exports all.
DEBUG_SKIP_ASSET_CHECKIN = False
DEBUG_SKIP_EXPORT_MAX = False
DEBUG_SKIP_MATERIAL_JSON = False
DEBUG_SKIP_QC = False
DEBUG_SKIP_QC_FARM = False
DEBUG_SKIP_SCENE_CHECKIN = False

# Globals
DEFAULT_PERSP_VIEW_MATRIX = mxs.Matrix3(
    mxs.Point3(0.707107, 0.353553, -0.612372),
    mxs.Point3(-0.707107, 0.353553, -0.612372),
    mxs.Point3(0, 0.866025, 0.5),
    mxs.Point3(0, 0, -250))
# Ignore these classes of nodes when finding nodes to ingest.
EXCLUDE_NODE_CLASSES = [mxs.VRayProxy]
INGEST_COMPANY_ENTITY = None
INGEST_COMPANY_NAME = None
# Don't search these directories for MAX files to process.
IGNORE_DIRS = ["AE34_003", "AE34_006", "AE34_007", "AE34_008", "AI46_006_BROKEN", "AI30_001", "downloaded",
               "productized"]
# Don't process MAX files with these words in the filename.
IGNORE_IN_MAX_FILE_NAMES = ["corona"]
logging.basicConfig()
LOGGER = logging.getLogger()
MANIFEST_ASSETS_PATH = None
MANIFEST_FAILED_PATH = None
MANIFEST_FILE_PATH = None
MANIFEST_MOST_RECENT = None  # Path to "current" manifest file.
MAX_FILE_OLDEST = False  # True: Ingest the oldest MAX file; False: newest.
ORIGIN_POSITION = mxs.Point3(0, 0, 0)
ORIGIN_TRANSFORM_MATRIX = mxs.Matrix3(1)
QC_EXPORT = True  # True: Export vrscenes; False: render QC passes in place.
# Import these images to render QC vrscenes.
qc_tool_folder = get_tool_dir("QC-Tool")
QC_IMAGES = [
    # os.path.join(qc_tool_folder, r"QC-Tool\Textures\UV_Coded.jpg")
]
RENDERED_FLAG = "Omit"  # None or "" is off.
SEARCH_PATH = None  # Current dir path used to find 3DS MAX files to ingest.
SESSION_PATH_START_COUNT = mxs.sessionPaths.count(mxs.Name("map"))
SG_ENGINE = sgtk.platform.current_engine()
SG = SG_ENGINE.shotgun
VIEW_PERSP_USER = mxs.Name("view_persp_user")  # Viewport type "Perspective User" enum.


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


def check_in_asset(asset_file_path, wrk_order, asset_name, description, matrix="", qc_renders=None, pub_others=None,
                   full_scene=False):
    """Checks-in supplied MAX file as an asset.

    Args:
        asset_file_path (str): MAX to check-in.
        wrk_order (dict): Work order Shotgun dictionary.
        asset_name (str): Name of asset to check-in.
        description (str): Description of asset for check-in.
        matrix (str): String representation of transform matrix.
        qc_renders (list[str]|None): List of paths to QC renders.
        pub_others (list[str]|None): List of paths to QC vrscene files.
        full_scene (bool): Is the MAX file the full scene?

    Returns:
        dict|None: Shotgun File Collection (CustomEntity16) dictionary.
    """
    # If full_scene, assume the scene is already open.
    if not full_scene:
        # Open file
        print("\tOpening {}".format(asset_file_path))
        mxs.loadMaxFile(asset_file_path, useFileUnits=True, quiet=True)

        # Add images used by QC so vrscenes will render.
        if pub_others is None:
            pub_others = []
        pub_others.extend(QC_IMAGES)

    if DEBUG_SKIP_MATERIAL_JSON or full_scene:
        print("\tSkipping Material Network JSON for {}".format(asset_name))
    else:
        print("\tExporting Material Network JSON for {}".format(asset_name))
        json_mat_path = None
        try_count = 0
        while try_count < 3:
            try:
                json_mat_path = export_material_json(asset_file_path)
                print("\tSuccessfully exported material json export.")
                break
            except Exception:
                print("\tError occurred during material json export.")
                try_count += 1
        if json_mat_path is not None:
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
        pub_exported=not full_scene,
        flag_rendered=flag_rendered
    )

    ############################################################################

    # If check-in fails, it returns False.
    if file_collection is False:
        print("Check-in failed.")
        return None

    # Update published files with matrix transform data.
    if not full_scene:
        for pub_file in file_collection.get("sg_published_file_entity_links"):
            pub_file = SG.find_one(
                "PublishedFile",
                [["id", "is", pub_file.get("id")]],
                ["code", "sg_context", "sg_source_transform_matrix"])

            if pub_file.get("sg_context") in ["geo_abc_exported", "geo_max"]:
                SG.update("PublishedFile", pub_file.get("id"), {"sg_source_transform_matrix": matrix})

        # Get metadata for asset.
        asset_meta_data = get_asset_metadata()

        file_collection = SG.update(
            file_collection.get("type"),
            file_collection.get("id"),
            {
                "sg_bbox_width": asset_meta_data.get("bbox_width"),
                "sg_bbox_height": asset_meta_data.get("bbox_height"),
                "sg_bbox_depth": asset_meta_data.get("bbox_depth"),
                "sg_bbox_units": asset_meta_data.get("bbox_units"),
                "sg_mdl_polygon_count": asset_meta_data.get("poly_count"),
                "sg_mdl_vertex_count": asset_meta_data.get("vert_count"),
                "sg_mtl_bitmap_count": asset_meta_data.get("mtl_bitmap_count"),
                "sg_mtl_material_count": asset_meta_data.get("mtl_material_count"),
                "sg_mtl_roughness_count": asset_meta_data.get("mtl_roughness_count"),
                "sg_mtl_uv_tiles_count": asset_meta_data.get("mtl_uv_tiles_count"),
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


def check_in_scene(current_file_path, scn_file_path, wrk_order):
    """Export and check-in full MAX scene.

    Args:
        current_file_path (str): Path to scene file.
        scn_file_path (str): Path to original scene file.
        wrk_order (dict): Shotgun work order.

    Returns:
        dict: Shotgun File Collection (CustomEntity16) dictionary.
    """
    def find_pre_rendered_images(current_path):
        """Find prerendered images from original MAX scene file path.

        Args:
            current_path (str): Original MAX scene file path.

        Returns:
            list[str]|None: List of prerendered images.
        """
        if current_path == SEARCH_PATH:
            return None
        if "images" in os.listdir(current_path):
            images_path = os.path.join(current_path, "images")
            return [os.path.join(images_path, f) for f in os.listdir(images_path)]
        return find_pre_rendered_images(os.path.dirname(current_path))

    ##########

    scene_name = mxs.getFilenameFile(mxs.maxFileName)

    scene_description = (
        "Checked-in from 3DS MAX.\n"
        "\n"
        "Full scene.\n"
        "\n"
        "Original File:\n"
        "{}".format(current_file_path))

    scene_checkin_dir = os.path.join(SEARCH_PATH, "__ingest_bulk__", scene_name)

    # Make the export directory if it doesn't exist.
    if not os.path.isdir(scene_checkin_dir):
        os.makedirs(scene_checkin_dir)

    scene_checkin_path = os.path.join(scene_checkin_dir, mxs.maxFileName)

    mxs.saveMaxFile(scene_checkin_path, clearNeedSaveFlag=True, useNewFile=True, quiet=True)

    ############################################################################

    # Check-in the scene.
    file_collection = check_in_asset(
        scene_checkin_path,
        wrk_order,
        scene_name,
        scene_description,
        qc_renders=find_pre_rendered_images(os.path.dirname(scn_file_path)),
        full_scene=True)

    ############################################################################

    mxs.resetMaxFile(mxs.Name("noPrompt"))

    if file_collection is None:
        print("Check-in scene failed.")
        return None

    asset = file_collection.get("asset_sg_asset_package_links_assets")[0]
    task = get_task(asset.get("id"))

    print("Check-Out Scene")
    result = check_out(task.get("id"))
    print("Check-Out Result")
    print(result)

    replace_non_ascii_paths()

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


def get_all_nodes(nodes=None):
    """Returns all descendants of a list of nodes.
    If None is provided, it will return all nodes in the scene.

    Args:
        nodes (list[pymxs.MXSWrapperBase]|None): Nodes from which to find descendants.

    Returns:
        list[pymxs.MXSWrapperBase]: List of all nodes.
    """
    all_nodes_list = []
    if nodes is None:
        nodes = list(mxs.rootScene[mxs.name("world")].object.children)
    for node in nodes:
        if node.children.count:
            all_nodes_list.extend(get_all_nodes(list(node.children)))
        all_nodes_list.append(node)

    return sorted(all_nodes_list, key=lambda n: sort_key_alphanum(n.Name))


def get_asset_nodes(nodes_text_file):
    """Get all nodes that represent assets to be ingested.
    If multiple nodes have the same geometry and textures, only the first node found will be returned.

    Args:
       nodes_text_file (str): Path to text file to which to write all node names.

    Returns:
       dict[str, list[pymxs.MXSWrapperBase]]: Nodes that represent assets to ingest.
    """
    top_level_nodes = sorted(
        [c for c in list(mxs.rootScene[mxs.name("world")].object.children) if include_node(c)],
        key=lambda x: sort_key_alphanum(x.Name))

    geo_nodes = []
    group_nodes = []
    for node in top_level_nodes:
        if mxs.classOf(node) == mxs.Dummy:
            group_nodes.append(node)
        else:
            geo_nodes.append(node)

    if DEBUG_PRINT:
        print("Number of Geo Nodes found: {}".format(len(geo_nodes)))
        print("Number of Group Nodes found: {}".format(len(group_nodes)))

    matched_geo_nodes = find_duplicate_nodes(geo_nodes)
    matched_group_nodes = find_duplicate_nodes(group_nodes)

    matched_nodes = matched_geo_nodes.copy()

    # Merge geo nodes and group nodes into the group dictionary.
    for grp_name in matched_group_nodes:
        if grp_name in matched_geo_nodes:
            matched_nodes[grp_name].extend(matched_group_nodes[grp_name])
        else:
            matched_nodes[grp_name] = matched_group_nodes.get(grp_name)

    # Print the full list of organized nodes.
    nodes_text_file_dir = os.path.dirname(nodes_text_file)
    if not os.path.exists(nodes_text_file_dir):
        os.makedirs(nodes_text_file_dir)

    with open(nodes_text_file, "w") as nodes_file:
        # Print the full list of organized nodes.
        nodes_file.write("Matched nodes:\n")

        for grp_name in sorted(matched_nodes.keys(), key=lambda x: sort_key_alphanum(x)):
            nodes_file.write("{}\n".format(grp_name))
            for node in sorted(matched_nodes.get(grp_name), key=lambda x: sort_key_alphanum(x.Name)):
                nodes_file.write("\t{}\n".format(node.Name))

    return matched_nodes


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


def process_scene(scn_file_path, wrk_order):
    """Process the scene file, checking in assets found in the scene.

    Args:
        scn_file_path (str): Scene path to open and process in original dir.
        wrk_order (dict): Work order dictionary.

    Returns:
        bool: Did the scene process successfully?
    """
    def add_session_paths(max_file_path):
        """Add found paths to session paths so images can be found.

        Args:
            max_file_path (str): Path to currently open MAX file.
        """
        mssng_files = get_missing_files()

        if mssng_files:
            new_path = find_missing_filepaths(max_file_path, mssng_files[0])

            if new_path:
                print("Adding path {}".format(new_path))
                mxs.sessionPaths.add(mxs.Name("map"), new_path)

                add_session_paths(max_file_path)
            else:
                return mssng_files
        else:
            print("No missing files.")
            return []

    def find_missing_filepaths(max_file_path, missing_file_name):
        """Searches for directory containing missing file.

        Args:
            max_file_path (str): Path to currently open MAX file.
            missing_file_name (str): File whose path to find.

        Returns:
            str|None: Found file path.
        """
        # Go one directory up from the current MAX file.
        if missing_file_name.startswith(r"C:\Program Files\Autodesk\3ds Max"):
            dirname = mxs.GetDir(mxs.Name("maxroot"))
            missing_file_name = os.path.basename(missing_file_name)
        else:
            dirname = os.path.dirname(os.path.dirname(max_file_path))
        for r, d, f in os.walk(dirname):
            if missing_file_name in f:
                return r

        return None

    def get_missing_files():
        """Returns list of filenames whose paths are missing.

        Returns:
            list: List of filenames whose paths are missing.
        """
        mf = []
        mxs.enumerateFiles(mf.append, mxs.Name("missing"))
        print(mf)
        return test_files_names([collect_scene_files()])

    def remove_added_session_paths():
        """Removes any session paths that were added during processing."""
        session_paths_count = mxs.sessionPaths.count(mxs.Name("map"))
        if session_paths_count > SESSION_PATH_START_COUNT:
            for i in reversed(range(SESSION_PATH_START_COUNT + 1, session_paths_count + 1)):
                print("Removing path {}".format(mxs.sessionPaths.get(mxs.Name("map"), i)))
                mxs.sessionPaths.delete(mxs.Name("map"), i)

    def test_files_names(file_collections):
        """Tests each name for non-ascii characters.

        Args:
            file_collections: File path node data.
                ::
                {
                     file_path: {
                                     node_object: {
                                                       "path_attr": [path_property]
                                                  }
                                }
                }

        Returns:
            list[str]: Path that are missing.
        """
        missing_paths = []
        non_unicode = []
        for file_list in file_collections:
            if not file_list:
                continue
            for f in file_list:
                if not os.path.exists(f):
                    missing_paths.append(f)
                try:
                    unicode(f).encode("ascii")
                except UnicodeEncodeError:
                    non_unicode.append(f)
        if missing_paths:
            print("Missing the following paths:\n{}".format("\n".join(missing_paths)))

        return missing_paths

    ##################################################

    print("Opening scene: {}".format(os.path.basename(scn_file_path)))

    # Open the scene file in Quiet mode.
    mxs.loadMaxFile(scn_file_path, useFileUnits=True, quiet=True)

    scene_name = mxs.getFilenameFile(mxs.maxFileName)

    scene_ingest_dir = os.path.join(SEARCH_PATH, "__ingest_bulk__", scene_name)

    # Check IO gamma.
    check_file_io_gamma()

    # Remove unused textures.
    clean_scene_materials()

    # Refresh bitmaps...
    mxs.redrawViews()

    current_file_path = os.path.join(mxs.maxFilePath, mxs.maxFileName)

    # Add missing file paths to Session Paths for textures, vrmeshes, etc.
    missing_files = add_session_paths(current_file_path)

    # Rename image files that are non-unicode
    if not missing_files:
        print("Searching for non-ascii filenames...")
        replace_non_ascii_paths()

    scene_file_collection = {}

    if not DEBUG_SKIP_SCENE_CHECKIN:
        if missing_files:
            print("Missing the following files:\n{}".format("\n".join(missing_files)))
            print("The scene {} is missing files.  Skipping scene.".format(os.path.basename(scn_file_path)))

            # Reset scene
            mxs.resetMaxFile(mxs.Name("noPrompt"))

            # Remove added paths.
            remove_added_session_paths()

            return False
        else:
            print("No missing files!")

            # Check in the whole scene.
            ####################################################################

            print("Check-in scene {}.".format(os.path.basename(scn_file_path)))
            scene_file_collection = check_in_scene(current_file_path, scn_file_path, wrk_order)

            ####################################################################

    nodes_text_file = os.path.join(scene_ingest_dir, "{}_nodes.txt".format(scene_name))

    # Get the nodes to check in.
    asset_nodes = get_asset_nodes(nodes_text_file)

    # Export the nodes to their own MAX file.
    asset_data_dict = export_nodes(asset_nodes, scene_ingest_dir)

    global MANIFEST_ASSETS_PATH
    MANIFEST_ASSETS_PATH = os.path.join(SEARCH_PATH, "__assets__.txt")
    with open(MANIFEST_ASSETS_PATH, "a") as manifest_assets_file:
        manifest_assets_file.write("{}\n".format(scn_file_path))
        manifest_assets_file.write("\t{} nodes:\n".format(len(asset_data_dict)))

    # If DEBUG_SKIP_EXPORT_MAX is True, there is no MAX file to QC or
    # check in.
    if DEBUG_SKIP_EXPORT_MAX:
        print("\tSkipping QC & check-in for all assets in {}".format(current_file_path))
        return False

    if scene_file_collection is None:
        print("\tScene check-in failed.  Skipping QC and check-in for all assets in {}".format(current_file_path))
        return False

    # QC and check-in all the assets found.
    print("QC and Check-in assets for scene:\n{}".format(current_file_path))
    for asset_name in sorted(asset_data_dict.keys(), key=lambda x: sort_key_alphanum(x)):
        asset_file_path = asset_data_dict[asset_name].get("max")
        original_node_name = asset_data_dict[asset_name].get("original_node")
        original_t_matrix = asset_data_dict[asset_name].get("original_t_matrix")

        qc_renders = None
        pub_others = None

        # QC asset
        if DEBUG_SKIP_QC:
            if DEBUG_PRINT:
                print("\tSkipping QC for {}".format(asset_name))
        else:
            # Open file
            print("\tOpening {}".format(asset_file_path))
            mxs.loadMaxFile(asset_file_path, useFileUnits=True, quiet=True)

            # Put everything in a group.
            nodes = [c for c in list(mxs.rootScene[mxs.name("world")].object.children)]
            for node in nodes:
                node.isSelected = True

            mxs.group(list(mxs.selection), name="__QC__")
            mxs.clearSelection()

            # Make QC directories.
            max_file_name = os.path.basename(asset_file_path).rsplit(".", 1)[0]
            qc_tool_exports_dir = "{}_QC".format(asset_file_path.rsplit(".", 1)[0])
            if not os.path.isdir(qc_tool_exports_dir):
                os.makedirs(qc_tool_exports_dir)
            qc_max_file_path = "{}_QC.max".format(os.path.join(qc_tool_exports_dir, max_file_name))

            mxs.saveMaxFile(qc_max_file_path, clearNeedSaveFlag=True, useNewFile=True, quiet=True)

            # QC EXPORT
            if QC_EXPORT:
                pub_others = qc_vrscene_export(qc_max_file_path)

            # QC RENDER
            else:
                qc_renders = qc_render([qc_max_file_path], "Lookdev", ".PNG", os.path.dirname(qc_max_file_path))

        # CHECK-IN
        if DEBUG_SKIP_ASSET_CHECKIN:
            if DEBUG_PRINT:
                print("\tSkipping Check-in for {}".format(asset_name))
            continue

        description = (
            "Checked-in from 3DS MAX.\n"
            "\n"
            "Original File:\n"
            "{}\n"
            "\n"
            "Original Node:\n"
            "{}".format(current_file_path, original_node_name))

        ########################################################################

        # Check-in asset.
        file_collection = check_in_asset(
            asset_file_path,
            wrk_order,
            asset_name,
            description,
            matrix=original_t_matrix,
            qc_renders=qc_renders,
            pub_others=pub_others)

        ########################################################################

        # Update asset File Collection with upstream File Collection of
        # original scene.
        if scene_file_collection and file_collection:
            SG.update("CustomEntity16", file_collection.get("id"), {"sg_upstream": scene_file_collection})

        if file_collection:
            with open(MANIFEST_ASSETS_PATH, "a") as manifest_assets_file:
                manifest_assets_file.write("\t{}\n".format(asset_name))

    # Reset scene
    mxs.resetMaxFile(mxs.Name("noPrompt"))

    # Remove added paths.
    remove_added_session_paths()

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


def replace_non_ascii_paths():
    """Finds, copies, renames, and replaces non-ascii file paths."""

    def get_non_ascii_nodes(fls_dict=None):
        """Returns non-ascii file paths used in the scene.

        Args:
            fls_dict (dict|None): Path/Nodes dictionary.

        Returns:
            list: List of non-ascii paths.
        """
        if fls_dict is None:
            fls_dict = collect_scene_files()
        non_ascii = []
        if fls_dict:
            for f in fls_dict:
                try:
                    unicode(f).encode("ascii")
                except UnicodeEncodeError:
                    non_ascii.append(f)

        return non_ascii

    ##########

    files_dict = collect_scene_files()
    count = 0
    scene_file_name = mxs.maxFileName.rsplit(".", 1)[0]

    for p in get_non_ascii_nodes(files_dict):
        extension = p.rsplit(".", 1)[1]
        dirpath = mxs.maxFilePath
        new_name = "{}_renamed_file_{}.{}".format(scene_file_name, count, extension)
        new_dir = os.path.join(dirpath, "renamed_files")
        new_path = os.path.join(new_dir, new_name)
        if not os.path.exists(new_dir):
            os.makedirs(new_dir)
        shutil.copy(p, new_path)
        count += 1

        map_dict = files_dict.get(p)
        for n in map_dict:
            attrs_dict = map_dict.get(n)
            for attr in attrs_dict.get("path_attrs"):
                mxs.setProperty(n, attr, new_path)
        print("Non-ascii image file replaced.  "
              "New Path: {}".format(new_path))


def search_and_process(search_path, wrk_order):
    """Search given directory for MAX files then process them.

    Args:
        search_path (str): Directory to search.
        wrk_order (dict): Work order dictionary needed to check-in files.
    """
    # Manifest files.
    global MANIFEST_FILE_PATH
    global MANIFEST_MOST_RECENT
    global MANIFEST_FAILED_PATH
    MANIFEST_FILE_PATH = os.path.join(search_path, "__manifest__.txt")
    MANIFEST_MOST_RECENT = os.path.join(search_path, "__current__.txt")
    MANIFEST_FAILED_PATH = os.path.join(search_path, "__failed__.txt")

    # If the process was interrupted, read the file path in current file and
    # restart processing AFTER that file.
    most_recent_processed_file_path = None
    skip = False
    if os.path.exists(MANIFEST_MOST_RECENT):
        with open(MANIFEST_MOST_RECENT, "r") as most_recent_processed_file:
            most_recent_processed_file_path = most_recent_processed_file.read()
        skip = True

    count = 0  # Total count.
    # If process was interrupted, this is the current count for
    # this round of processing.
    cur_count = 0
    cur_success_count = 0

    print("Searching for scenes to process in {}...".format(search_path))

    # Walk through the search path, file MAX files, process them.
    for files in max_walk(search_path):
        if skip:
            count += 1
            if most_recent_processed_file_path in files:
                skip = False
            continue

        # Get MAX file.
        if len(files) > 1:
            files.sort(key=os.path.getmtime)
            if not MAX_FILE_OLDEST:
                files = reversed(files)

            files = [f for f in files if is_max_file_path_clean(f)]

        if not files:
            continue

        max_file = files[0]
        print("\n\nScene found: {}".format(max_file))

        ####################################################################

        # Process the MAX file.
        process_success = process_scene(max_file, wrk_order)

        ####################################################################

        if process_success:
            # Write to manifest files.
            with open(MANIFEST_FILE_PATH, "a") as manifest_file:
                manifest_file.write("{}\n".format(max_file))
            with open(MANIFEST_MOST_RECENT, "w") as most_recent_processed_file:
                most_recent_processed_file.write(max_file)

            cur_success_count += 1
        else:
            with open(MANIFEST_FAILED_PATH, "a") as failed_processed_file:
                failed_processed_file.write("{}\n".format(max_file))

        # Increment count.
        count += 1
        cur_count += 1

        if DEBUG_SCENE_COUNT_LIMIT and cur_count >= DEBUG_SCENE_COUNT_LIMIT:
            break

    # os.remove(current_file_path)
    print("{} total MAX scenes.".format(count))
    print("{} current MAX scenes attempted.".format(cur_count))
    print("{} current MAX scenes succeeded.".format(cur_success_count))
    print("Manifest written: {}".format(MANIFEST_FILE_PATH))


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
    SEARCH_PATH = r"Q:\Shared drives\DVS_StockAssets\Evermotion"

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
        search_and_process(SEARCH_PATH, work_order)

    # Reset scene.
    mxs.resetMaxFile(mxs.Name("noPrompt"))

    print("== Ingest Complete ==")

    # mxs.quitMax(mxs.Name("noprompt"))
