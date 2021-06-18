################################################################################


__author__ = "John Russell <john.russell@dreamview.com>"


# Standard libraries
import io
import json
import logging
import os
import re
import shutil
import string
import sys
import unicodedata

# Third-party libraries
import sgtk

# Intra-studio libraries
# None

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
from dvs_max_lib.meta import Meta


# GLOBALS

# Debug globals
DEBUG_ASSET_EXPORT_COUNT_LIMIT = 0  # 0 exports all.
DEBUG_PRINT = False
DEBUG_SCENE_COUNT_LIMIT = 0  # 0 exports all.
DEBUG_SKIP_EXPORT_MAX = False
DEBUG_SKIP_MATERIAL_JSON = False

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
IGNORE_IN_MAX_FILE_NAMES = ["corona", "__ingest_bulk__"]
logging.basicConfig(level=logging.DEBUG)
LOGGER = logging.getLogger()
MANIFEST_ASSETS_PATH = None
MANIFEST_FAILED_PATH = None
MANIFEST_FILE_PATH = None
MANIFEST_MOST_RECENT = None  # Path to "current" manifest file.
MAX_FILE_OLDEST = False  # True: Ingest the oldest MAX file; False: newest.
ORIGIN_POSITION = mxs.Point3(0, 0, 0)
ORIGINAL_PATH = None
RENDERED_FLAG = "Omit"  # None or "" is off.
SEARCH_PATH = None  # Current dir path used to find 3DS MAX files to ingest.
SESSION_PATH_START_COUNT = mxs.sessionPaths.count(mxs.Name("map"))
SG_ENGINE = sgtk.platform.current_engine()
SG = SG_ENGINE.shotgun
VIEW_PERSP_USER = mxs.Name("view_persp_user")  # Viewport type "Perspective User" enum.


# Classes
# None


# Functions

def check_file_io_gamma():
    """Checks and updates scene's gamma settings.

    Returns:
        bool: Did the Gamma settings have to be updated?
    """
    result = True
    if mxs.fileInGamma != mxs.displayGamma:
        mxs.fileInGamma = mxs.displayGamma
        result = False
        LOGGER.info("Updating file in gamma to match display gamma!")
    if mxs.fileOutGamma != mxs.displayGamma:
        mxs.fileOutGamma = mxs.displayGamma
        result = False
        LOGGER.info("Updating file out gamma to match display gamma!")
    return result


def clean_name(name_string):
    """Remove non-printable characters from filename.

    Args:
        name_string (str): Name to clean.

    Returns:
        str: Filename with non-printable characters removed.
    """
    return "".join(c for c in name_string if c in string.printable)


def clean_scene_materials():
    """Removes any references to unused materials."""
    # reset material editors
    for i in range(24):
        mxs.meditMaterials[i] = mxs.VRayMtl(name="{}{} - Default".format(("0" if i < 9 else ""), str(i + 1)))

    for i in range(mxs.sme.GetNumViews()):
        mxs.sme.DeleteView(1, False)

    mxs.sme.CreateView("View 1")

    # reset background

    mxs.environmentMap = None
    mxs.backgroundColor = mxs.black
    mxs.useEnvironmentMap = False

    # Remove Effects
    for c in reversed(range(1, mxs.numAtmospherics + 1)):
        mxs.deleteAtmospheric(c)

    # Remove Render Elements
    render_ele_mngr = mxs.maxOps.GetCurRenderElementMgr()
    render_ele_mngr.SetElementsActive(False)
    render_ele_mngr.RemoveAllRenderElements()

    # Don't render hidden objects
    mxs.rendHidden = False


def clean_string(input_str):
    """Cleans given string.

    Args:
        input_str (str): String to clean.

    Returns:
        str: Cleaned string.
    """
    if not isinstance(input_str, basestring):
        input_str = str(input_str)
    if isinstance(input_str, str):
        input_str = input_str.decode("utf8", errors="ignore")

    nkfd_form = unicodedata.normalize("NFKD", input_str)
    s = u"".join([c for c in nkfd_form if not unicodedata.combining(c)])
    s = " ".join(s.encode(errors="ignore").decode().split())

    return s


def collect_scene_files():
    """Collect scene file data into dictionary.

    Returns:
        dict: File path node data.
            ::
            {
                file_path: {
                    node_object: {
                        "path_attr": [path_property],
                    },
                },
            }
    """
    def get_all_external_files():
        """Returns paths to all external files used in the scene.

        Returns:
            list[str]: Paths to external files used in the scene.
        """
        all_files = []
        mxs.enumerateFiles(all_files.append)
        return all_files

    def get_current_filename_nodes(max_objects=None):
        """Get current filename nodes.

        Args:
            max_objects (list|None):

        Returns:
            dict: Current objects.
                ::
                {
                    node_object: property_name,
                }
        """
        current_objects = {}
        u_filename = u"filename"
        u_hdri_map_name = u"HDRIMapName"
        prop_filename = mxs.name(u_filename)
        prop_hdri_map_name = mxs.name(u_hdri_map_name)
        # from supplied max objects and nodes
        if max_objects:
            prop_baseobject = mxs.name(u"baseobject")
            for obj in max_objects:
                if mxs.isProperty(obj, prop_baseobject) and mxs.isProperty(obj.baseobject, prop_filename):
                    current_objects[obj.baseobject] = u_filename
                elif mxs.isProperty(obj, prop_filename):
                    current_objects[obj] = u_filename
                elif mxs.isProperty(obj, prop_hdri_map_name):
                    current_objects[obj] = u_hdri_map_name
        # from all objects and nodes in the scene
        else:
            for obj in mxs.objects:
                if mxs.isProperty(obj.baseobject, prop_filename):
                    current_objects[obj.baseobject] = u_filename
            for obj in mxs.getClassInstances(mxs.Bitmaptexture):
                current_objects[obj] = u_filename
            if hasattr(mxs, "VRayHDRI"):
                for obj in mxs.getClassInstances(mxs.VRayHDRI):
                    current_objects[obj] = u_hdri_map_name
        return current_objects

    ##########

    # collect all scene bitmap files as dict
    files_dict = {}
    for f in get_all_external_files():
        if not f:
            continue
        used_file_name = mxs.mapPaths.getFullFilePath(f)
        if used_file_name:
            f = used_file_name
        files_dict[f] = {}

    # collect nodes for each file
    all_nodes = get_current_filename_nodes()
    for n in all_nodes:
        node_prop = all_nodes.get(n)
        file_name = mxs.getProperty(n, mxs.name(node_prop))
        if not file_name:
            continue
        used_file_name = mxs.mapPaths.getFullFilePath(file_name)
        if used_file_name and file_name != used_file_name:
            file_name = used_file_name
            mxs.setProperty(n, mxs.name(node_prop), u"{}".format(used_file_name))
        if file_name not in files_dict:
            files_dict[file_name] = {}
        if n not in files_dict.get(file_name):
            files_dict[file_name][n] = {"path_attrs": [node_prop]}

    return files_dict


def export_json_material(json_path, nodes=mxs.objects):
    """Write a JSON file mimicking the material network per node.

    Args:
        json_path (str): Path to json file to which to write.
        nodes (list[pymxs.MXSWrapperBase]): Nodes whose material networks to extract.
    """
    def get_properties(obj_material):
        """Get properties from object material.

        Args:
            obj_material (Any): Material or property of material.

        Returns:
            dict: Dictionary mimicking material network.
                ::
                {
                    property_name: property_value
                }
        """
        # cache maxscript refs
        array_class = mxs.Array
        array_parameter_class = mxs.arrayParameter
        boolean_class = mxs.booleanClass
        class_of = mxs.classOf
        get_prop_names = mxs.getPropNames
        get_property = mxs.getProperty
        number_class = mxs.number
        string_class = mxs.string
        superclass_of = mxs.superclassOf
        undefined_class = mxs.undefinedClass
        value_class = mxs.value

        obj_class = class_of(obj_material)
        obj_super_class = superclass_of(obj_material)
        if obj_material is None or obj_class is None or obj_class == undefined_class:
            result = None
        elif obj_class == string_class or obj_class == boolean_class or obj_super_class == number_class:
            result = obj_material
        elif obj_super_class == value_class:
            # result = str(obj_material)
            # unicode as long as there are paths in Shotgun that are non-ascii
            result = unicode(obj_material)
        elif obj_class == array_parameter_class or obj_class == array_class:
            result = {}
            for i in range(obj_material.count):
                val = get_properties(obj_material[i])
                if val:
                    result[i] = val
            if result and obj_class:
                result["class"] = str(obj_class)
        else:
            result = {}
            try:
                obj_properties = get_prop_names(obj_material)
            except:
                obj_properties = []
            if hasattr(obj_material, "name"):
                result["name"] = str(obj_material.name)
            if obj_class:
                result["class"] = str(obj_class)
            for p in obj_properties:
                prop = get_property(obj_material, p)
                if prop:
                    val = get_properties(prop)
                    if val:
                        result[str(p)] = val
        return result

    ####################

    json_dict = {}
    for max_obj in nodes:
        # get object's material info as a dict
        mat_dict = get_properties(max_obj.material)

        if mat_dict:
            json_dict[max_obj.name] = mat_dict

    with io.open(json_path, "w", encoding="utf8") as json_file:
        json_file.write(unicode(json.dumps(json_dict, ensure_ascii=False, indent=4)))


def export_json_metadata(node, data, json_path):
    """Export a json file with metadata to later be used in check-in.

    Args:
        node (pymxs.MXSWrapperBase): Node whose metadata to export.
        data (dict): Data to add to the metadata json file.
        json_path (str): Path to json file to write.
    """
    def check_bbox(obj=mxs.objects):
        """Check the bounding box info based on the scene and display units.

        This info is supplied to a SG field(s)

        Args:
            obj (pymxs.MXSWrapperBase): Node or MaxScript collection of all objects.
                Default: All objects collection.

        Returns:
            dict, dict: units, bounding box dimensions
                ::
                {type: unit type},
                {"d": float, "h": float, "w": float}
        """
        units = {}
        bounding_box = {}

        units_system = str(mxs.units.systemType).lower()
        units_display = str(mxs.units.displayType).lower()

        try:
            if units_display == "metric":
                # Max formats: Millimeters, Centimeters, Meters, Kilometers
                units_display = str(mxs.units.metricType).lower()
                if units_display == "centimeters":
                    units_display = "cm"
                elif units_display == "millimeters":
                    units_display = "mm"
                elif units_display == "meters":
                    units_display = "m"

            else:
                # Max formats:
                # Frac_In, Dec_In, Frac_Ft, Dec_Ft, Ft_Frac_In, Ft_Dec_In
                # Frac_1_1, Frac_1_2, Frac_1_4, Frac_1_8, Frac_1_10, Frac_1_16
                # Frac_1_32, Frac_1_64, Frac_1_100
                # units_display = str(mxs.units.usType).lower()
                units_display = "ft"

        except Exception as e:
            LOGGER.warning("Error: {}".format(e))
            return None

        units["System Units"] = units_system
        units["Display Units"] = units_display

        try:
            obj_min = obj.min
            obj_max = obj.max
        except AttributeError:
            obj_min = None
            obj_max = None

        if obj_min:
            size_w = obj_max.y - obj_min.y
            size_d = obj_max.x - obj_min.x
            size_h = obj_max.z - obj_min.z

            bounding_box["d"] = size_d
            bounding_box["h"] = size_h
            bounding_box["w"] = size_w

        return units, bounding_box

    def get_added_session_paths():
        """Returns any session paths that were added during processing."""
        paths = []
        session_paths_count = mxs.sessionPaths.count(mxs.Name("map"))
        if session_paths_count > SESSION_PATH_START_COUNT:
            for i in reversed(range(SESSION_PATH_START_COUNT + 1, session_paths_count + 1)):
                paths.append(mxs.sessionPaths.get(mxs.Name("map"), i))

        return paths

    def get_metadata(cur_node):
        """Returns metadata for given node.

        Args:
            cur_node (pymxs.MXSWrapperBase): Node from which to get metadata.

        Returns:
            dict: Metadata for given node.
        """
        meta = Meta()

        material_nodes = []
        material_vray_nodes = []
        texture_nodes = []
        texture_nodes_unique = set()
        texture_file_nodes = set()
        texture_files_unique = set()
        texture_coords_nodes = []
        objs = get_all_nodes([cur_node])
        materials_and_textures = meta.get_materials_and_textures(objs)
        nodes_data = meta.get_nodes_data(materials_and_textures)

        for nd, node_info in nodes_data.items():
            if node_info["type"] == "Material":
                material_nodes.append(nd)
                if mxs.classOf(nd) == mxs.VRayMtl:
                    material_vray_nodes.append(nd)
            elif node_info["type"] == "Texture":
                texture_nodes.append(unicode(nd))
                texture_nodes_unique.add(unicode(nd))
                if node_info["files"]:
                    text_file_path = node_info["files"].values()
                    text_file_name = os.path.basename(text_file_path[0])
                    node_with_file_name = "{} >>> {}".format(unicode(nd), text_file_name)
                    texture_file_nodes.add(node_with_file_name)
                    # only add unique file names
                    texture_files_unique.add(text_file_name)
                if hasattr(nd, "coords"):
                    texture_coords_nodes.append(nd)

        # converting sets to list to return same types
        texture_nodes_unique = list(texture_nodes_unique)
        texture_file_nodes = list(texture_file_nodes)
        texture_files_unique = list(texture_files_unique)

        # file format check
        texture_files_unwanted = meta.check_file_format(texture_files_unique)

        # pbr check
        material_vray_roughness, material_vray_roughness_off = meta.check_bdrf_roughness(material_vray_nodes)

        # bbox
        unit_types, bbox_dim = check_bbox(cur_node)
        # check num uv maps
        num_uv_maps = meta.check_num_uv_maps(objs)

        metadata = {
            "material_nodes": material_nodes,
            "texture_nodes": texture_nodes,
            "texture_nodes_unique": texture_nodes_unique,
            "texture_file_nodes": texture_file_nodes,
            "texture_files_unique": texture_files_unique,
            "texture_files_unwanted": texture_files_unwanted,
            "material_vray_nodes": material_vray_nodes,
            "material_vray_roughness": material_vray_roughness,
            "material_vray_roughness_off": material_vray_roughness_off,
            "unit_types": unit_types,
            "bbox_dim": bbox_dim,
            "num_uv_maps": num_uv_maps}

        return metadata

    def get_total_poly_and_vert_count(root_node):
        """Returns list pair of poly_count and vert_count of root_node and all descendants.

        Args:
            root_node (pymxs.MXSWrapperBase): Parent node.

        Returns:
            list[int]: List pair of poly_count and vert_count.
        """
        objs = get_all_nodes([root_node])
        objs.append(root_node)
        counter = [0, 0]
        for obj in objs:
            if mxs.canConvertTo(obj, mxs.Editable_Poly):
                temp = mxs.copy(obj)
                mxs.convertToPoly(temp)
                temp_count = mxs.getPolygonCount(temp)
                counter[0] += temp_count[0]
                counter[1] += temp_count[1]
                mxs.delete(temp)

        return counter

    ###########
    ###########

    col_nodes = get_metadata(node)

    mtl_bitmap_count = len(col_nodes.get("texture_files_unique"))
    mtl_material_count = len(col_nodes.get("material_nodes"))
    mtl_roughness_count = len(col_nodes.get("material_vray_roughness_off"))
    mtl_uv_tiles_count = len(col_nodes.get("num_uv_maps"))

    # if the bbox dimensions are this large...
    # something is wrong. The SG field
    # won't accept a num > 1.00E9
    if col_nodes["bbox_dim"].get("d") > 1.00E9:
        col_nodes["bbox_dim"]["d"] = None
    if col_nodes["bbox_dim"].get("h") > 1.00E9:
        col_nodes["bbox_dim"]["h"] = None
    if col_nodes["bbox_dim"].get("w") > 1.00E9:
        col_nodes["bbox_dim"]["w"] = None
    bbox = {
        "units": col_nodes["unit_types"].get("Display Units"),
        "depth": col_nodes["bbox_dim"].get("d"),
        "height": col_nodes["bbox_dim"].get("h"),
        "width": col_nodes["bbox_dim"].get("w")}

    poly_count, vert_count = get_total_poly_and_vert_count(node)

    scene_meta = {
        "bbox_width": bbox.get("width"),
        "bbox_depth": bbox.get("depth"),
        "bbox_height": bbox.get("height"),
        "bbox_units": bbox.get("units"),
        "company_entity_id": INGEST_COMPANY_ENTITY.get("id"),
        "mtl_bitmap_count": mtl_bitmap_count,
        "mtl_material_count": mtl_material_count,
        "mtl_roughness_count": mtl_roughness_count,
        "mtl_uv_tiles_count": mtl_uv_tiles_count,
        "original_max_file": os.path.join(mxs.maxFilePath, mxs.maxFileName),
        "original_t_matrix": data.get("original_t_matrix"),
        "texture_files_unique": col_nodes.get("texture_files_unique"),
        "texture_paths": get_added_session_paths(),
        "poly_count": poly_count,
        "vert_count": vert_count}

    with io.open(json_path, "w", encoding="utf8") as json_file:
        json_file.write(unicode(json.dumps(scene_meta, ensure_ascii=False, indent=4)))


def export_node(node, name, nodes_hide_state, export_dir):
    """Save node to another max file.
    Get one node, move it to the origin, move the viewport to see it,
    save to another max file, move the node back to its previous position.

    Args:
        node (pymxs.MXSWrapperBase): Node to save into new file.
        name (str): Name of the max file to which the node is going.
        nodes_hide_state (dict[str, bool]): Dictionary of hide states for
            all nodes.
        export_dir (str): Directory to which to export asset MAX file.

    Returns:
        dict[str, str]: Dictionary of node stats.
            (i.e. "max": file_path, "original_node": node name)
    """
    def get_transform_matrix(nd):
        """Returns string representation of 4x3 transform matrix.

        For future reference:
        To get a tuple of points out of the returned matrix string:
        matrix_tuple = [
            tuple(
                [
                    float(x)
                    if "." in x
                    else int(x)
                    for x in re.sub("[()]", "", s).split(", ")])
            for s in matrix_str.split("), ")]

        Args:
            nd (pymxs.MXSWrapperBase): Node of asset.

        Returns:
            str: String representation of 4x3 transform matrix.
        """
        tm = nd.objecttransform
        point_string_list = []

        for row in range(4):
            point = tm[row]
            point_string_list.append("({}, {}, {})".format(point.x, point.y, point.z))

        return ", ".join(point_string_list)

    ##########
    ##########

    node_export_data = {"max": "", "original_node": node.Name}

    for n in get_all_nodes([node]):
        n.isNodeHidden = nodes_hide_state.get(n.Name)

    bbox_min = node.min
    bbox_max = node.max
    pivot = mxs.Point3((bbox_max.x + bbox_min.x)/2.0, (bbox_max.y + bbox_min.y)/2.0, bbox_min.z)

    node_pivot = node.pivot
    node.pivot = pivot

    node_export_data["original_t_matrix"] = get_transform_matrix(node)

    node_pos = node.Position
    node.Position = ORIGIN_POSITION

    # rotate
    # TODO: rotation code goes here.
    z_rotation = mxs.getProperty(node, "rotation.z_rotation")
    mxs.setProperty(node, "rotation.z_rotation", 0.0)

    # Move z so lowest point is at the ground plane.
    lowest_point = node.min[2]
    mxs.setProperty(node, "position.z", -lowest_point)

    mxs.clearSelection()
    node.isSelected = True
    mxs.execute("max zoomext sel")  # Zoom to selected object.
    mxs.clearSelection()
    mxs.redrawViews()

    LOGGER.debug("{}:".format(name))

    if DEBUG_SKIP_EXPORT_MAX:
        LOGGER.debug("\tSkipping exporting MAX file for {}".format(name))
    else:
        save_dir = os.path.join(export_dir, "assets")

        # Make the export directory if it doesn't exist.
        if not os.path.isdir(save_dir):
            os.makedirs(save_dir)
        path_max_scene_node_export = os.path.join(save_dir, "{}.max".format(name))
        path_json_meta = os.path.join(save_dir, "{}_metadata.json".format(name))
        path_json_mat = os.path.join(save_dir, "{}_mat_network.json".format(name))

        LOGGER.debug("\tExporting MAX file: {}".format(path_max_scene_node_export))

        node.isSelected = True
        mxs.saveNodes(list(mxs.selection), path_max_scene_node_export, quiet=True)
        node_export_data["max"] = path_max_scene_node_export
        export_json_metadata(node, node_export_data, path_json_meta)
        try:
            export_json_material(path_json_mat, get_all_nodes([node]))
        except RuntimeError:
            LOGGER.warning("Material JSON export failed for {}".format(node.name))

            # current_file_path = os.path.join(mxs.maxFilePath, mxs.maxFileName)
            #
            # global MANIFEST_FAILED_PATH
            # MANIFEST_FAILED_PATH = os.path.join(SEARCH_PATH, "__failed__.txt")
            # with open(MANIFEST_FAILED_PATH, "a") as failed_processed_file:
            #     failed_processed_file.write("{}\n\t{}\n".format(scn_file_path, msg.replace("\n", "\n\t")))

        mxs.clearSelection()

    # Reset rotation
    mxs.setProperty(node, "rotation.z_rotation", z_rotation)

    # Reset position
    node.Position = node_pos
    node.pivot = node_pivot
    for n in get_all_nodes([node]):
        n.isNodeHidden = True

    return node_export_data


def export_nodes(groups_to_export, export_dir):
    """Exports all nodes from a dictionary.

    Args:
        groups_to_export (list[pymxs.MXSWrapperBase]): Dictionary of matched
            nodes lists.
        export_dir (str): Directory to which to export asset MAX files.

    Returns:
        dict[str, dict[str, str]]: Dictionary of lists of filepaths of
            exported files.
            Ex. asset_name: {"max": max_file_path, "original node": node_name}
    """
    nodes_data_dict = {}
    nodes_hide_state = {}

    # Current viewport settings
    view_active = mxs.viewport.activeViewport
    view_layout = mxs.viewport.getLayout()
    mxs.viewport.activeViewportEx(1)
    view_type = mxs.viewport.getType()
    view_shading = mxs.viewport.getRenderLevel()
    view_edge_faces = mxs.viewport.GetShowEdgeFaces()
    view_tm = None
    if view_type == VIEW_PERSP_USER:
        view_tm = mxs.getViewTM()

    # Set new viewport settings
    mxs.viewport.setLayout(mxs.Name("layout_1"))
    mxs.viewport.setType(mxs.Name("view_persp_user"))
    mxs.viewport.SetRenderLevel(mxs.Name("smoothhighlights"))
    mxs.viewport.SetShowEdgeFaces(False)
    mxs.viewport.setTM(DEFAULT_PERSP_VIEW_MATRIX)
    mxs.redrawViews()

    # Remember all nodes hide state, then hide all nodes to make
    # thumbnail clean.
    for node in get_all_nodes():
        nodes_hide_state[node.Name] = node.isNodeHidden
        node.isNodeHidden = True

    # Make a list of (node, name) tuples.
    # For every node group, save one node to its own MAX file.
    nodes = [(node, clean_name(node.Name)) for node in groups_to_export]
    # sort by node name
    nodes.sort(key=lambda x: sort_key_alphanum(x[1], True))

    # Export

    # Debug count
    total = DEBUG_ASSET_EXPORT_COUNT_LIMIT
    count = 0
    num_nodes = len(nodes)

    export_msg = "Exporting {} nodes..."
    if DEBUG_ASSET_EXPORT_COUNT_LIMIT:
        export_msg.replace("...", " (Debug)...")
        num_nodes = total

    LOGGER.info(export_msg.format(num_nodes))

    # EXPORT Node to it's own MAX node.
    ############################################################################

    for node, name in nodes:
        nodes_data_dict[name] = export_node(node, name, nodes_hide_state, export_dir)

        # If Debug count is > 0, it will limit the number of nodes
        # being exported.
        if DEBUG_ASSET_EXPORT_COUNT_LIMIT:
            count += 1
            if count == total:
                break

    ############################################################################

    mxs.clearSelection()
    # Set all nodes previous visible state.
    for node in get_all_nodes():
        node.isNodeHidden = nodes_hide_state.get(node.Name)

    # Reset viewport to original position.
    if view_tm:
        mxs.viewport.setTM(view_tm)
    if view_edge_faces:
        mxs.viewport.SetShowEdgeFaces(view_edge_faces)
    mxs.viewport.SetRenderLevel(view_shading)
    mxs.viewport.setType(view_type)
    mxs.viewport.setLayout(view_layout)
    mxs.viewport.activeViewportEx(view_active)

    mxs.redrawViews()

    LOGGER.info("\n{} nodes exported.".format(num_nodes))

    return nodes_data_dict


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
        return sorted(list(mxs.objects), key=lambda n: sort_key_alphanum(n.Name))

    for node in nodes:
        if node.children.count:
            all_nodes_list.extend(get_all_nodes(list(node.children)))
        all_nodes_list.append(node)

    return sorted(all_nodes_list, key=lambda n: sort_key_alphanum(n.Name))


def get_asset_nodes():
    """Get all nodes that represent assets to be ingested.
    If multiple nodes have the same geometry and textures, only the first node found will be returned.

    Returns:
       list[pymxs.MXSWrapperBase]: Nodes that represent assets to ingest.
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

    LOGGER.debug("Number of Group Nodes found: {}".format(len(group_nodes)))

    return group_nodes


def include_node(node):
    """Does this node qualify to be included in the export?

    Args:
        node (pymxs.MXSWrapperBase): 3DS MAX node.

    Returns:
        bool: Does this node qualify to be included in the export?
    """
    if not (node in mxs.geometry or mxs.classOf(node) == mxs.Dummy):
        return False
    if mxs.classOf(node) in EXCLUDE_NODE_CLASSES:
        return False
    if mxs.classOf(node) == mxs.Dummy and not node.children.count:
        return False
    if node in mxs.geometry and list(mxs.getPolygonCount(node))[1] == 0:
        return False
    if node in mxs.geometry and node.material is None:
        return False
    if node.visibility is False:
        return False
    if node.isNodeHidden is True:
        return False
    if ".Target" in node.Name:
        return False
    if "Particle View" in node.Name:
        return False
    if "vray" in node.Name.lower():
        return False
    return True


def is_max_file_path_clean(path):
    """Is the path to the MAX file clean of words to avoid?

    Args:
        path (str): Path to check.

    Returns:
        bool: Is the path to the MAX file clean of words to avoid?
    """
    elements = []
    path = path.lower()
    for e in IGNORE_IN_MAX_FILE_NAMES:
        if e.lower() in path:
            elements.append(e)

    return not bool(elements)


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
            if name not in IGNORE_DIRS:
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


def process_scene(scn_file_path):
    """Process the scene file, checking in assets found in the scene.

    Args:
        scn_file_path (str): Scene path to open and process in original dir.

    Returns:
        bool: Did the scene process successfully?
    """
    def add_session_paths(max_file_path):
        """Add found paths to session paths so images can be found.

        Args:
            max_file_path (str): Path to currently open MAX file.
        """
        mssng_files = test_files_names([collect_scene_files()])

        if not mssng_files:
            LOGGER.info("No missing files.")
            return []

        new_path = find_missing_filepaths(max_file_path, mssng_files[0])

        if not new_path:
            return mssng_files

        LOGGER.info("Adding path {}".format(new_path))
        mxs.sessionPaths.add(mxs.Name("map"), new_path)
        add_session_paths(max_file_path)

    def find_missing_filepaths(max_file_path, missing_file_path):
        """Searches for directory containing missing file.

        Args:
            max_file_path (str): Path to currently open MAX file.
            missing_file_path (str): File whose path to find.

        Returns:
            str|None: Found file path.
        """
        missing_file_name = os.path.basename(missing_file_path)
        # Go one directory up from the current MAX file.
        if missing_file_path.startswith(r"C:\Program Files\Autodesk\3ds Max"):
            dirname = mxs.GetDir(mxs.Name("maxroot"))
        else:
            shot_nm = os.path.basename(os.path.dirname(max_file_path))
            dirname = os.path.join(ORIGINAL_PATH, shot_nm)

        LOGGER.debug("Searching for missing file {} in: {}".format(missing_file_name, dirname))

        for r, d, f in os.walk(dirname):
            if missing_file_name.lower() in [fl.lower() for fl in f]:
                return r

        return None

    def remove_added_session_paths():
        """Removes any session paths that were added during processing."""
        session_paths_count = mxs.sessionPaths.count(mxs.Name("map"))
        if session_paths_count > SESSION_PATH_START_COUNT:
            for i in reversed(range(SESSION_PATH_START_COUNT + 1, session_paths_count + 1)):
                LOGGER.info("Removing path {}".format(mxs.sessionPaths.get(mxs.Name("map"), i)))
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
            LOGGER.warning("Missing the following paths:\n\t{}".format("\n\t".join(missing_paths)))

        return missing_paths

    ##################################################

    LOGGER.info("Opening scene: {}".format(os.path.basename(scn_file_path)))

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

    # If files are still missing, don't process.
    if missing_files:
        shot_name = os.path.basename(os.path.dirname(current_file_path))
        search_dir = os.path.join(ORIGINAL_PATH, shot_name)

        msg = "Could not find missing files:\n\t{}".format("\n\t".join(missing_files))
        msg = "{}\nSearched directory: {}".format(msg, search_dir)

        LOGGER.warning(msg)

        global MANIFEST_FAILED_PATH
        MANIFEST_FAILED_PATH = os.path.join(SEARCH_PATH, "__failed__.txt")
        with open(MANIFEST_FAILED_PATH, "a") as failed_processed_file:
            failed_processed_file.write("{}\n\t{}\n".format(scn_file_path, msg.replace("\n", "\n\t")))

        # Reset scene
        mxs.resetMaxFile(mxs.Name("noPrompt"))

        # Remove added paths.
        remove_added_session_paths()

        return False

    # Rename image files that are non-unicode
    LOGGER.info("Searching for non-ascii filenames...")
    replace_non_ascii_paths()

    # Get the nodes to check in.
    asset_nodes = get_asset_nodes()

    global MANIFEST_ASSETS_PATH
    MANIFEST_ASSETS_PATH = os.path.join(SEARCH_PATH, "__assets__.txt")
    with open(MANIFEST_ASSETS_PATH, "a") as manifest_assets_file:
        manifest_assets_file.write("{}\n".format(scn_file_path))
        manifest_assets_file.write("\t{} nodes:\n".format(len(asset_nodes)))

    # Export the nodes to their own MAX file.
    asset_data_dict = export_nodes(asset_nodes, scene_ingest_dir)

    # Reset scene
    mxs.resetMaxFile(mxs.Name("noPrompt"))

    # Remove added paths.
    remove_added_session_paths()

    return True


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
        LOGGER.info("Non-ascii image file replaced.  New Path: {}".format(new_path))


def search_and_process(search_path):
    """Search given directory for MAX files then process them.

    Args:
        search_path (str): Directory to search.
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

    LOGGER.info("Searching for scenes to process in {}...".format(search_path))

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
        LOGGER.info("\n\nScene found: {}".format(max_file))

        ####################################################################

        # Process the MAX file.
        process_success = process_scene(max_file)

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
    LOGGER.info("{} total MAX scenes.".format(count))
    LOGGER.info("{} current MAX scenes attempted.".format(cur_count))
    LOGGER.info("{} current MAX scenes succeeded.".format(cur_success_count))
    LOGGER.info("Manifest written: {}".format(MANIFEST_FILE_PATH))


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
            int|str: If the string represents a number, return the number.  Otherwise, return the string.
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
    # Company
    INGEST_COMPANY_NAME = "Evermotion"  # Must match name in Shotgun
    # INGEST_COMPANY_NAME = "CG Trader"
    INGEST_COMPANY_ENTITY = SG.find_one("CustomNonProjectEntity02", [["code", "is", INGEST_COMPANY_NAME]])

    # Directory to search.
    SEARCH_PATH = r"Q:\Shared drives\DVS_StockAssets\Evermotion\From_Adnet\June\AD_2021-06-04"
    ORIGINAL_PATH = r"Q:\Shared drives\DVS_StockAssets\Evermotion"

    # Specific scenes to process
    scene_file_paths = [
        # r"Q:\Shared drives\DVS_StockAssets\Evermotion\AE34_001\scenes\AE34_001.max",
        # r"Q:\Shared drives\DVS_StockAssets\Evermotion\AE34_002\002\scenes\AE34_002_forestPack_2011.max",
        # r"Q:\Shared drives\DVS_StockAssets\Evermotion\AE34_003\003\AE34_003.max",
        # r"Q:\Shared drives\DVS_StockAssets\Evermotion\AE34_005\005\AE34_005.max",
        # r"Q:\Shared drives\DVS_StockAssets\Evermotion\AE34_002\002\scenes\AE34_002_forestPack_2020.max",
        # r"Q:\Shared drives\DVS_StockAssets\Evermotion\From_Adnet\June\AD_08-06-2021\Evermotion\ArchInteriors_17_06\ArchInteriors_17_06_2020.max",
        # r"Q:\Shared drives\DVS_StockAssets\Evermotion\From_Adnet\June\AD_09-06-2021\Evermotion\AE34_005\AE34_005_2020.max"
    ]

    # Silence V-Ray dialog for older versions.
    mxs.setVRaySilentMode()

    curr_path = os.path.join(mxs.maxFilePath, mxs.maxFileName)

    # If a scene is already open, process it.
    if curr_path:
        success = process_scene(curr_path)
    # If you have specific scenes to process...
    elif scene_file_paths:
        for scene_file_path in scene_file_paths:
            success = process_scene(scene_file_path)
    # Or search a directory and process
    else:
        search_and_process(SEARCH_PATH)

    # Reset scene.
    mxs.resetMaxFile(mxs.Name("noPrompt"))

    LOGGER.info("== Ingest Complete ==")

    # mxs.quitMax(mxs.Name("noprompt"))
