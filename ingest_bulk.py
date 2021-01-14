# Standard libraries
import ctypes
from difflib import SequenceMatcher
import logging
import os
import re
import sys
import tempfile
import time

# Third-party libraries
import sgtk

# Intra-studio libraries
from utils.sg_create_entities import create_asset

# App-specific libraries
from MaxPlus import (Atomspherics, Core, Environment, FileManager, Matrix3,
                     Point3, SelectionManager, SuperClassIds, ViewportManager)
from pymxs import runtime as mxs

# Get the script folder from shotgun
mxs_command = '''
(
    local resultPath = undefined
    global DreamView_Scripts_ScriptsPath
    if DreamView_Scripts_ScriptsPath != undefined then
    (
        local toolsDir = getFilenamePath DreamView_Scripts_ScriptsPath
        if doesFileExist toolsDir then
        (
            local checkDirs = getDirectories (toolsDir + "{}")
            if checkDirs.count > 0 then resultPath = checkDirs[1]
        )
    )
    resultPath
)
'''

# Import Check-in module
script_folder = mxs.execute(mxs_command.format('Check_In*'))
if script_folder not in sys.path:
    sys.path.insert(0, script_folder)
from check_in_out import check_in

# Import QC Batch Tool module
script_folder = mxs.execute(mxs_command.format('QC_Batch_Tool'))
if script_folder not in sys.path:
    sys.path.insert(0, script_folder)
from qc_batch_tool import get_qc_tool


DEBUG_EXPORT_COUNT_LIMIT = 0
DEBUG_RENDERED_FLAG = 'Omit'  # None or '' is off.
DEBUG_SCENE_COUNT_LIMIT = 0
DEBUG_SKIP_CHECKIN = False
DEBUG_SKIP_EXPORT_MAX = False
DEBUG_SKIP_QC = False
DEFAULT_PERSP_VIEW_MATRIX = Matrix3(
    Point3(0.707107, 0.353553, -0.612372),
    Point3(-0.707107, 0.353553, -0.612372),
    Point3(0, 0.866025, 0.5),
    Point3(0, 0, -250))
EXCLUDE_SUPERCLASS_IDS = [SuperClassIds.Light, SuperClassIds.Camera]
IGNORE_DIRS = ['downloaded', 'productized', 'AI46_006_BROKEN']
INTERSECTION_DIST = 250.0
logging.basicConfig()
LOGGER = logging.getLogger()
ORIGIN_POSITION = Point3(0, 0, 0)
ORIGIN_TRANSFORM_MATRIX = Matrix3(
    Point3(1, 0, 0),
    Point3(0, 1, 0),
    Point3(0, 0, 1),
    Point3(0, 0, 0))
SG_ENGINE = sgtk.platform.current_engine()
SG = SG_ENGINE.shotgun
SIMILAR_RATIO = 0.80
VIEW_PERSP_USER = 7  # Viewport type "Perspective User" enum


def alphanum_key(string, case_sensitive=False):
    """Turn a string into a list of string and number chunks.
    Using this key for sorting will order alphanumeric strings properly.

    "ab123cd" -> ["ab", 123, "cd"]

    Args:
        string (str): Given string.
        case_sensitive (bool): If True, capital letters will go first.
    Returns:
        list[int|str]: Mixed list of strings and integers in the order they
            occurred in the given string.
    """
    def try_int(str_chunk, cs_snstv):
        """Convert string to integer if it's a number.
        In certain cases, ordering filenames takes case-sensitivity
        into consideration.

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

    return [try_int(chunk, case_sensitive)
            for chunk in re.split('([0-9]+)', string)]


def check_file_io_gamma():
    """Checks and updates scene's gamma settings.

    Returns:
        bool: Did the Gamma settings have to be updated?
    """
    result = True
    if mxs.fileInGamma != mxs.displayGamma:
        mxs.fileInGamma = mxs.displayGamma
        result = False
        print('Updating file in gamma to match display gamma!')
    if mxs.fileOutGamma != mxs.displayGamma:
        mxs.fileOutGamma = mxs.displayGamma
        result = False
        print('Updating file out gamma to match display gamma!')
    return result


def check_geo(node1, node2):
    """Checks to see if 2 given nodes have the same geometry and material.

    Args:
        node1 (MaxPlus.INode): Node to cross-reference.
        node2 (MaxPlus.INode): Node to cross-reference.

    Returns:
        Bool: Do these nodes match?
    """
    # If they're both group nodes and they have the have the same number
    # of children...
    if (node1.Object.SuperClassID == SuperClassIds.Helper and
            node2.Object.SuperClassID == SuperClassIds.Helper):
        node1_children = get_all_nodes([node1])
        node2_children = get_all_nodes([node2])
        if len(node1_children) != len(node2_children):
            return False

        # If the total number of vertices between the children
        # are equal...
        if sum([node.VertexCount for node in node1_children]) != \
                sum([node.VertexCount for node in node2_children]):
            return False

        if sorted([child.GetMaterial().GetName()
                   for child in node1_children if child.GetMaterial()]) == \
                sorted([child.GetMaterial().GetName()
                        for child in node2_children if child.GetMaterial()]):
            return True
    else:
        if node1.VertexCount == node2.VertexCount and \
                node1.GetMaterial() and node2.GetMaterial() and \
                node1.GetMaterial().GetName() == node2.GetMaterial().GetName():
            return True
    return False


def check_in_asset(asset_file_path, wrk_order, asset_name, description):
    """Checks-in supplied MAX file as an asset.

    Args:
        asset_file_path (str): MAX to check-in.
        wrk_order (dict): Work order Shotgun dictionary.
        asset_name (str): Name of asset to check-in.
        description (str): Description of asset for check-in.
    """
    # Open file
    print("\tOpening {}".format(asset_file_path))
    FileManager.Open(asset_file_path, True)

    # Has the asset been ingested before?  If so, find the previous deliverable.
    # Returns None if none are found.
    deliverable = SG.find_one(
        "CustomEntity24",
        [['code', 'contains', '{}_Hi Ingest Bulk'.format(asset_name)]])

    # Create Asset
    asset = create_asset(
        SG, LOGGER, SG_ENGINE.context.project, wrk_order, asset_name,
        deliverable_type='Asset Ingest Bulk', deliverable=deliverable)

    # Get Task from newly created Asset.
    task = SG.find_one(
        "Task",
        [
            [
                'entity.CustomEntity25.sg_deliverable.CustomEntity24.sg_link.Asset.id',
                'is',
                asset['id']
            ]
        ]
    )

    qc_renders = None
    if not DEBUG_SKIP_QC:
        qc_renders_dir = '{}_QC_Tool'.format(asset_file_path.rsplit('.', 1)[0])
        if os.path.isdir(qc_renders_dir):
            qc_renders = [os.path.join(qc_renders_dir, render_file)
                          for render_file in os.listdir(qc_renders_dir)
                          if render_file.lower().endswith('.png')]

    # Check in MAX file.
    flag_rendered = 'In Progress'
    if qc_renders and DEBUG_RENDERED_FLAG:
        flag_rendered = DEBUG_RENDERED_FLAG

    result = check_in(
        task['id'],
        rendered=qc_renders,
        description=description,
        flag_rendered=flag_rendered)

    print(result)


def clean_scene_materials():
    """Removes any references to unused materials."""
    # reset material editors
    for i in range(24):
        mxs.meditMaterials[i] = mxs.VRayMtl(
            name=("0" if i < 9 else "") + str(i + 1) + " - Default")

    for i in range(mxs.sme.GetNumViews()):
        mxs.sme.DeleteView(1, False)

    mxs.sme.CreateView("View 1")

    # reset background

    mxs.environmentMap = None
    mxs.backgroundColor = mxs.white
    Environment.SetMapEnabled(False)

    # Remove Effects
    for c in reversed(range(Atomspherics.GetCount())):
        Atomspherics.Delete(c)

    # Don't render hidden objects
    mxs.rendHidden = False


def compare_node_names(node1, node2):
    """Compares names of 2 nodes and returns the index of single mismatch.
    Returns None if there is more than one mismatch.

    Args:
        node1 (MaxPlus.INode): First node with which to compare.
        node2 (MaxPlus.INode): Second node with which to compare.

    Returns:
        int|None: Index
    """
    if node1.Name == node2.Name:
        return None

    node1_name_list = node1.Name.split('_')
    node2_name_list = node2.Name.split('_')

    if len(node1_name_list) != len(node2_name_list):
        return None

    num_of_mismatches = 0
    index_of_mismatch = None

    for s in range(len(node1_name_list)):
        if node1_name_list[s] != node2_name_list[s]:
            num_of_mismatches += 1

            if num_of_mismatches > 1:
                return None

            index_of_mismatch = s

    # The mismatched portion of the name should only be numeric or alphanumeric.
    # Also, there is an edge case that node1 mismatch is alpha and node2 is not:
    # ["AE34", "002", "garden", "lamp"] <==>
    # ["AE34", "002", "garden", "lamp001"]
    # So test alpha for node2, not node1
    if node2_name_list[index_of_mismatch].isalpha():
        return None

    return index_of_mismatch


def export_node(node, name, nodes_hide_state):
    """Save node to another max file.
    Get one node, move it to the origin, move the viewport to see it,
    save to another max file, move the node back to its previous position.

    Args:
        node (MaxPlus.INode): Node to save into new file.
        name (str): Name of the max file to which the node is going.
        nodes_hide_state (dict[str, bool]): Dictionary of hide states for
            all nodes.

    Returns:
        dict[str, str]: Dictionary of node stats.
            (i.e. 'max': file_path, 'original_node': node name)
    """
    node_export_data = {
        'max': '',
        'original_node': node.Name
    }

    for n in get_all_nodes([node]):
        n.Hide = nodes_hide_state[n.Name]

    node_pos = node.Position
    node.Position = ORIGIN_POSITION
    SelectionManager.ClearNodeSelection(True)
    node.Select()
    ViewportManager.ViewportZoomExtents(True)
    SelectionManager.ClearNodeSelection(True)
    ViewportManager.RedrawViewportsNow(Core.GetCurrentTime())

    print('{}:'.format(name))

    if DEBUG_SKIP_EXPORT_MAX:
        print("\tSkipping exporting MAX file for {}".format(name))
    else:
        save_dir = os.path.join(
            tempfile.gettempdir(),
            "ingest_bulk",
            FileManager.GetFileName().rsplit(".", 1)[0])

        # Make the export directory if it doesn't exist.
        if not os.path.isdir(save_dir):
            os.makedirs(save_dir)
        # Python Temp
        save_dir = get_full_path(save_dir).replace("c:", "C:")
        save_path = os.path.join(
            save_dir, "{}_{}.max".format(name, int(time.time() * 100)))

        node.Select()
        FileManager.SaveNodes(
            SelectionManager.GetNodes(),
            save_path)
        print('\tExporting MAX file: {}'.format(save_path))
        node_export_data['max'] = save_path
        SelectionManager.ClearNodeSelection(True)

    # Reset position
    node.Position = node_pos
    for n in get_all_nodes([node]):
        n.Hide = True

    return node_export_data


def export_nodes(groups_to_export):
    """Exports all nodes from a dictionary.

    Args:
        groups_to_export (dict[str, list[MaxPlus.INode]]): Dictionary of matched
            nodes lists.

    Returns:
        dict[str, dict[str, str]]: Dictionary of lists of filepaths of
            exported files.
            Ex. asset_name: {'max': max_file_path, 'original node': node_name}
    """
    nodes_data_dict = {}
    nodes_hide_state = {}

    # Save viewport settings
    av = ViewportManager.GetActiveViewport()
    av_type = Core.EvalMAXScript('viewport.GetType()').Get()
    maxed = ViewportManager.IsViewportMaxed()
    shading = Core.EvalMAXScript('viewport.GetRenderLevel()').Get()
    avm = None
    layout = None
    zoom = None
    user_persp = av.GetViewType() == VIEW_PERSP_USER

    # Set new viewport settings
    if not maxed:
        layout = ViewportManager.GetViewportLayout()
        ViewportManager.SetViewportMax(True)
    if not user_persp:
        # Even though it's not a user persp view, it could still be a camera
        # persp view.
        if not av.IsPerspView():
            # If it's not a persp view, it's a 2D view.
            zoom = av.GetZoom()
        # Force it to be a user persp view
        av.SetViewUser(True)
        ViewportManager.RedrawViewportsNow(Core.GetCurrentTime())
    else:
        avm = av.GetViewMatrix()
    av.SetViewMatrix(DEFAULT_PERSP_VIEW_MATRIX)
    # Force Default Shading
    Core.EvalMAXScript('viewport.SetRenderLevel #smoothhighlights')
    avef = av.GetEdgedFaces()
    if avef:
        av.SetEdgedFaces(False)

    # Remember all nodes hide state, then hide all nodes to make
    # thumbnail clean.
    for node in get_all_nodes():
        nodes_hide_state[node.Name] = node.Hide
        node.Hide = True

    # Make a list of (node, name) tuples.
    # For every node group, save one node to its own MAX file.
    nodes = [
        (groups_to_export[group_name][0], groups_to_export[group_name][0].Name)
        for group_name in groups_to_export if group_name != "_LEFT_OVER_"]
    # Add the left over nodes.
    nodes.extend(
        [(node, node.Name) for node in groups_to_export["_LEFT_OVER_"]])
    # sort by node name
    nodes.sort(key=lambda x: alphanum_key(x[1]))

    # Export

    # Debug count
    total = DEBUG_EXPORT_COUNT_LIMIT
    count = 0
    num_nodes = len(nodes)

    export_msg = "Exporting {} nodes..."
    if DEBUG_EXPORT_COUNT_LIMIT:
        export_msg.replace("...", " (Debug)...")
        num_nodes = total

    print(export_msg.format(num_nodes))

    for node, name in nodes:
        nodes_data_dict[name] = export_node(node, name, nodes_hide_state)

        # If Debug count is > 0, it will limit the number of nodes
        # being exported.
        if DEBUG_EXPORT_COUNT_LIMIT:
            count += 1
            if count == total:
                break

    SelectionManager.ClearNodeSelection(True)
    # Set all nodes previous visible state.
    for node in get_all_nodes():
        node.Hide = nodes_hide_state[node.Name]

    # Reset viewport to original position.
    if avef:
        av.SetEdgedFaces(avef)
    Core.EvalMAXScript('viewport.SetRenderLevel #{}'.format(shading))
    if user_persp:
        av.SetViewMatrix(avm)
    else:
        Core.EvalMAXScript('viewport.setType #{}'.format(av_type))
        if not av.IsPerspView():
            av.Zoom(zoom)
    if not maxed:
        ViewportManager.SetViewportLayout(layout)

    print("\n{} nodes exported.".format(num_nodes))

    return nodes_data_dict


def get_all_nodes(nodes=None):
    """Returns all descendants of a list of nodes.
    If None is provided, it will return all nodes in the scene.

    Args:
        nodes (list[MaxPlus.INode]|None): Nodes from which to find descendants.

    Returns:
        list[MaxPlus.INode]: List of all nodes.
    """
    all_nodes_list = []
    if nodes is None:
        nodes = Core.GetRootNode().Children
    for node in nodes:
        if node.GetNumChildren():
            all_nodes_list.extend(get_all_nodes(node.Children))
        all_nodes_list.append(node)

    return sorted(all_nodes_list, key=lambda n: alphanum_key(n.Name))


def get_asset_nodes():
    """Get all nodes that represent assets to be ingested.
    If multiple nodes have the same geometry and textures, only the first node
    found will be returned.

    Returns:
       dict[str, list[MaxPlus.INode]]: Nodes that represent assets to ingest.
    """
    top_level_nodes = sorted(
        [c for c in Core.GetRootNode().Children if include_node(c)],
        key=lambda x: alphanum_key(x.Name))

    geo_nodes = [node for node in top_level_nodes
                 if not node.Object.SuperClassID == SuperClassIds.Helper]
    group_nodes = [
        node for node in top_level_nodes
        if node.Object.SuperClassID == SuperClassIds.Helper and
        node.GetNumChildren()]

    matched_geo_nodes, ints1 = match_names(geo_nodes)
    matched_group_nodes, ints2 = match_names(group_nodes)

    matched_nodes = matched_geo_nodes.copy()

    # Merge geo nodes and group nodes into the group dictionary.
    for grp_name in matched_group_nodes:
        if grp_name in matched_geo_nodes:
            matched_nodes[grp_name].extend(matched_group_nodes[grp_name])
        else:
            matched_nodes[grp_name] = matched_group_nodes[grp_name]

    # Print the full list of organized nodes.
    print("==========\n")
    print("Matched nodes:\n")

    for grp_name in \
            sorted(matched_nodes.keys(), key=lambda x: alphanum_key(x)):
        print(grp_name)
        for node in \
                sorted(
                    matched_nodes[grp_name],
                    key=lambda x: alphanum_key(x.Name)):
            print("\t{}".format(node.Name))

    print("==========\n")
    # print("Intersecting nodes:\n")
    #
    # intersect_dict = {}
    # intersect_dict.update(ints1)
    # intersect_dict.update(ints2)
    #
    # for node_name in \
    #         sorted(intersect_dict.keys(), key=lambda x: alphanum_key(x)):
    #     print("{}:".format(node_name))
    #     for n in intersect_dict[node_name]:
    #         print("\t{}".format(n.Name))
    #
    # print("==========\n")

    return matched_nodes


def get_full_path(path):
    """Converts shorter Windows paths with tildes to longer full paths.
    Path MUST exist to convert.

    Short:
    c:\users\john~1.rus\appdata\local\temp\2\ingest_bulk\AE34_001

    Long:
    c:\Users\john.russell\AppData\Local\Temp\2\ingest_bulk\AE34_001

    Args:
        path (str): Short Windows path with tildes.

    Returns:
        str: Long Windows path.
    """
    tmp = unicode(path)

    get_long_path_name = ctypes.windll.kernel32.GetLongPathNameW
    buffer = ctypes.create_unicode_buffer(get_long_path_name(tmp, 0, 0))
    get_long_path_name(tmp, buffer, len(buffer))

    return str(buffer.value)


def include_node(node):
    """Does this node qualify to be included?

    Args:
        node (MaxPlus.INode): 3DS MAX node.

    Returns:
        bool: Does this node qualify to be included?
    """
    if node.Object.SuperClassID in EXCLUDE_SUPERCLASS_IDS:
        return False
    if node.Object.SuperClassID == SuperClassIds.Helper and \
            not node.GetNumChildren:
        return False
    if node.GetMaterial() is None:
        return False
    if node.Visibility is False:
        return False
    if node.Hide is True:
        return False
    if ".Target" in node.Name:
        return False
    if "Particle View" in node.Name:
        return False
    if "vray" in node.Name.lower():
        return False
    return True


def intersect_check(node1, node2):
    """Do these nodes intersect?

    Args:
        node1 (MaxPlus.INode): Node to check intersections.
        node2 (MaxPlus.INode): Node to check intersections.

    Returns:
        bool: Do these nodes intersect?
    """
    return Core.EvalMAXScript(
        "intersects ${} ${}".format(node1.Name, node2.Name)
    ).GetBool() and \
        Core.EvalMAXScript(
            "distance ${} ${}".format(node1.Name, node2.Name)
        ).Get() < INTERSECTION_DIST and \
        similar(node1.Name, node2.Name)


def match_names(nodes):
    """Returns dictionary of similar nodes.

    Args:
        nodes (list[MaxPlus.INode]): List of nodes within which to find matching
            groups of nodes.

    Returns:
        dict[str:list[MaxPlus.INode]]: Groups of similar nodes.
    """
    groups = {}
    matches = []
    intersections = {}
    skip_match = False

    # For every node to the second to last node..
    for i in range(len(nodes) - 1):
        # Only compare a node if it hasn't been matched yet.
        if nodes[i] in matches:
            skip_match = True
            # continue

        # A name list is the list of strings that comprise the full name
        # Example:
        # "AE34_002_lilly_leaf_01" => ["AE34", "002", "lilly", "leaf", "01"]
        node1_name_list = nodes[i].Name.split('_')

        # Comparing similar name lists of nodes that belong to the same group
        # should yield exactly one index that is different between the
        # two lists.
        # Example:
        # ["AE34", "002", "lilly", "01"] <==>
        # ["AE34", "002", "lilly", "02"]
        # Index 3 is different
        index_of_mismatch = None

        # For every node AFTER the node to which you are comparing...
        for j in range(i + 1, len(nodes)):
            # Find nodes with matching geo and textures...
            if not skip_match:
                # Matching nodes have the same number of vertices
                # and materials...
                if not check_geo(nodes[i], nodes[j]):
                    continue

                index_of_mismatch_compare = \
                    compare_node_names(nodes[i], nodes[j])

                # An appropriate mismatch wasn't found.  Move on to the
                # next node.
                if index_of_mismatch_compare is None:
                    continue

                # If index_of_mismatch is None, then this is the first time a
                # match has been found for the first node.  Compare all
                # following name lists to this index.
                if index_of_mismatch is None:
                    index_of_mismatch = index_of_mismatch_compare
                    # Get the cleaned version of the name list.
                    node1_name_list = name_list_clean(
                        node1_name_list, index_of_mismatch)

                # We need to avoid the possibility of false positives:
                # ["AE34", "002", "lilly", "02"] <!=>
                # ["AE34", "002", "weed", "02"]
                # This mismatch is at index 2.
                # The previous mismatch for the first node is at index 3.
                # The indexes of mismatch must be equal if the nodes belong to
                # the same group.
                if index_of_mismatch_compare != index_of_mismatch:
                    continue

                # Get the cleaned version of the name list.
                node2_name_list = name_list_clean(
                    nodes[j].Name.split('_'), index_of_mismatch)

                # If two nodes truly belong to a group, if you clean the
                # mismatching string from their name lists, the remaining name
                # lists should be identical.
                #
                # Example 1:
                # index_of_mismatch = 3
                # first node:
                # ["AE34", "002", "lilly", "01"] => ["AE34", "002", "lilly"]
                #
                # second node
                # ["AE34", "002", "lilly", "02"] => ["AE34", "002", "lilly"]
                #
                # comparison:
                # ["AE34", "002", "lilly"] <==> ["AE34", "002", "lilly"]
                #
                # Therefore,
                # "AE34_002_lilly_01" and "AE34_002_lilly_02" will match.
                #
                # Example 2:
                # index_of_mismatch = 3
                # first node:
                # ["AE34", "002", "garden", "lamp"] =>
                # ["AE34", "002", "garden", "lamp"]
                #
                # second node:
                # ["AE34", "002", "garden", "lamp001"] =>
                # ["AE34", "002", "garden", "lamp"]
                #
                # comparison:
                # ["AE34", "002", "garden", "lamp"] <==>
                # ["AE34", "002", "garden", "lamp"]
                #
                # Therefore,
                # "AE34_002_garden_lamp" and "AE34_002_garden_lamp001"
                # will match.
                #
                # If the mismatches are cleaned and the 2 name lists
                # are identical, then they belong to the same group.
                if node1_name_list != node2_name_list:
                    continue

                # Reassemble name to use as a key for all matching nodes
                matching_str = "_".join(node1_name_list)

                # If this is the first match for the first node, put it in the
                # groups dictionary and matches list.
                if nodes[i] not in matches:
                    groups[matching_str] = [nodes[i]]
                    matches.append(nodes[i])

                matches.append(nodes[j])
                groups[matching_str].append(nodes[j])

                print("Nodes \"{}\" and \"{}\" match".format(
                    nodes[i].Name, nodes[j].Name))

            # # Find nodes that belong together in a group...
            # if intersect_check(nodes[i], nodes[j]):
            #     if nodes[i].Name not in intersections:
            #         intersections[nodes[i].Name] = [nodes[i]]
            #     intersections[nodes[i].Name].append(nodes[j])

        skip_match = False

    # Left over are individual geo nodes with materials or groups with materials
    groups["_LEFT_OVER_"] = [node for node in nodes if node not in matches]

    return groups, intersections


def max_walk(dir_to_walk):
    """Generator that walks through a directory structure and yields MAX files.

    Args:
        dir_to_walk (str): Current directory being searched.

    Yields:
        list[str]: List of paths to MAX files found in directory.
    """
    names = sorted(os.listdir(dir_to_walk))

    dirs, max_files = [], []

    for name in names:
        if os.path.isdir(os.path.join(dir_to_walk, name)):
            if name not in IGNORE_DIRS:
                dirs.append(name)
        else:
            if name.lower().endswith('.max'):
                max_files.append(name)

    # If MAX files are found...
    if max_files:
        yield [os.path.join(dir_to_walk, f) for f in max_files]

    # If no MAX files are found, keep digging...
    else:
        for name in dirs:
            new_path = os.path.join(dir_to_walk, name)
            if not os.path.islink(new_path):
                for x in max_walk(new_path):
                    yield x


def name_list_clean(name_list, index_of_mismatch):
    """Cleans name list of mismatches.

    If the mismatch element is a:
    string: Leave it alone.
    digit: Remove it.
    alphanumeric: Remove the numbers, keep the text.

    Examples:
        index_of_mismatch = 3
        ["AE34", "002", "garden", "lamp"] => ["AE34", "002", "garden", "lamp"]

        index_of_mismatch = 3
        ["AE34", "002", "lilly", "01"] => ["AE34", "002", "lilly"]

        index_of_mismatch = 3
        ["AE34", "002", "garden", "lamp001"] =>
        ["AE34", "002", "garden", "lamp"]

    Args:
        name_list (list[str]): Original name list
        index_of_mismatch (int): Index of the mismatch between node names.

    Returns:
        list[str]: Cleaned name list.
    """
    mismatch_str = name_list[index_of_mismatch]

    # If the mismatch string is alphanumeric, get rid of the
    # numbers and put the remaining string back in the name list.
    if not mismatch_str.isalpha() and not mismatch_str.isdigit():
        mismatch_str = "".join(
            [s for s in alphanum_key(mismatch_str) if type(s) != int])

        name_list[index_of_mismatch] = mismatch_str

        return name_list

    # If mismatch string is just a string, leave it alone.
    if mismatch_str.isalpha():
        return name_list

    # If mismatch string is a number, like a version number, remove it.
    if len(name_list) > 1:
        name_list.pop(index_of_mismatch)

    return name_list


def process_scene(scene_file_path, wrk_order):
    """Process the scene file, checking in assets found in the scene.

    Args:
        scene_file_path (str): Scene path to open and process.
        wrk_order (dict): Work order dictionary.
    """
    # Open the scene file in Quiet mode.
    mxs.loadMaxFile(scene_file_path, useFileUnits=True, quiet=True)
    # Check IO gamma.
    check_file_io_gamma()

    # Remove unused textures.
    clean_scene_materials()

    current_file_path = FileManager.GetFileNameAndPath()

    # Get the nodes to check in.
    asset_nodes = get_asset_nodes()

    # Export the nodes to their own MAX file.
    asset_data_dict = export_nodes(asset_nodes)

    # If DEBUG_SKIP_EXPORT_MAX is True, there is no MAX file to QC or
    # check in.
    if DEBUG_SKIP_EXPORT_MAX:
        print('\tSkipping QC & check-in for all assets in {}'.format(
            current_file_path))
        return

    # QC and check-in all the assets found.
    print("QC and Check-in assets for scene:\n{}".format(current_file_path))
    for asset_name in sorted(asset_data_dict.keys()):

        asset_file_path = asset_data_dict[asset_name].get('max')
        original_node_name = \
            asset_data_dict[asset_name].get('original_node')

        # QC
        if DEBUG_SKIP_QC:
            print("\tSkipping QC for {}".format(asset_name))
        else:
            # QC asset
            qc_asset(asset_file_path)

        # CHECK-IN
        if DEBUG_SKIP_CHECKIN:
            print('\tSkipping Check-in for {}'.format(asset_name))
            continue

        description = "Checked-in from 3DS MAX.\n\n" \
                      "Original File:\n" \
                      "{}\n\n" \
                      "Original Node:\n" \
                      "{}".format(current_file_path, original_node_name)

        # Check-in asset
        check_in_asset(asset_file_path, wrk_order, asset_name, description)


def qc_asset(asset_file_path):
    """Renders QC images for the supplied asset.

    Args:
        asset_file_path (str): Path to MAX file.
    """
    # Open file
    print("\tOpening {}".format(asset_file_path))
    FileManager.Open(asset_file_path, True)

    nodes = [c for c in Core.GetRootNode().Children]
    for node in nodes:
        node.Select()

    Core.EvalMAXScript('group selection name: "QC_Tool"')
    SelectionManager.ClearNodeSelection(True)

    qc_max_file_path = "{}_QC_Tool.max".format(
        asset_file_path.rsplit(".", 1)[0])
    FileManager.Save(qc_max_file_path, True, True)

    # QC Tool
    # QC Tool opens file.
    qc_render(
        [qc_max_file_path],
        'Lookdev',
        '.PNG',
        os.path.dirname(asset_file_path))


# "process_files" function from QC batch tool
def qc_render(files_list, render_mode, render_ext, output_path):
    """Perform QC renders.

    Args:
        files_list (list(str)): List of paths to MAX files to QC render.
        render_mode (str): Render mode: 'Model' or 'Lookdev'
        render_ext (str): File extension for output QC renders.
        output_path (str): Directory output path for the QC renders.
    """
    # get the qc-tool maxscript object
    qc_tool = get_qc_tool()
    if qc_tool:
        # process the files
        for f in files_list:
            f_norm = os.path.normpath(f)
            print('+ Opening file: {}'.format(f_norm))
            if mxs.loadMaxFile(f_norm, useFileUnits=True, quiet=True):
                qc_tool.init()
                if qc_tool.modelRoot:
                    if 'Model' in render_mode:
                        qc_tool.setVal(u"Render Mode", u'Model')
                        qc_tool.setVal(u"Output Types", render_ext)
                        # qc_tool.setVal(u"Hero Resolution", 1) # test with 500x500px
                        # qc_tool.setVal(u"Hero Quality", 1) # test with 0.5 treshold
                        result = qc_tool.renderAll(outPath=output_path)
                        if "Render was cancelled!" in result: break
                    if 'Lookdev' in render_mode:
                        qc_tool.setVal(u"Render Mode", u'Lookdev')
                        qc_tool.setVal(u"Output Types", render_ext)
                        qc_tool.setVal(u"Hero Resolution", 2)  # 1000x1000px
                        # qc_tool.setVal(u"Hero Quality", 1) # test with 0.5 treshold
                        result = qc_tool.renderAll(outPath=output_path)
                        if "Render was cancelled!" in result: break
                else:
                    print('Model not found in file: {}'.format(f_norm))
            else:
                print('Error opening file: {}'.format(f_norm))


def search_and_process(search_path, work_order):
    """Search given directory for MAX files then process them.

    Args:
        search_path (str): Directory to search.
        work_order (dict): Work order dictionary needed to check-in files.
    """
    # Manifest files.
    manifest_file_path = os.path.join(search_path, '__manifest__.txt')
    current_file_path = os.path.join(search_path, '__current__.txt')

    # If the process was interrupted, read the file path in current file and
    # restart processing AFTER that file.
    current = None
    skip = False
    if os.path.exists(current_file_path):
        current_file = open(current_file_path, "r")
        current = current_file.read()
        current_file.close()
        skip = True

    count = 0       # Total count.
    # If process was interrupted, this is the current count for
    # this round of processing.
    cur_count = 0

    # Walk through the search path, file MAX files, process them.
    for files in max_walk(search_path):
        if skip:
            count += 1
            if current in files:
                skip = False
            continue

        # Get MAX file.
        max_file = files[0]

        # Process the MAX file.
        process_scene(max_file, work_order)

        # Write to manifest files.
        manifest_file = open(manifest_file_path, "a")
        manifest_file.write("{}\n".format(max_file))
        manifest_file.close()
        current_file = open(current_file_path, "w")
        current_file.write(max_file)
        current_file.close()

        # Increment count.
        count += 1
        cur_count += 1

        if DEBUG_SCENE_COUNT_LIMIT and cur_count >= DEBUG_SCENE_COUNT_LIMIT:
            break

    # os.remove(current_file_path)
    print("{} total MAX scenes".format(count))
    print("{} current MAX scenes".format(cur_count))
    print("Manifest written: {}".format(manifest_file_path))


def similar(str1, str2):
    """Are the strings similar enough?

    Args:
        str1 (str): First string to compare.
        str2 (str): Second string to compare.

    Returns:
        bool: Are the strings similar enough?
    """
    return SequenceMatcher(None, str1, str2).ratio() > SIMILAR_RATIO


if __name__ == "__main__":
    work_order = {'type': 'CustomEntity17', 'id': 2232}
    search_path = r'Q:\Shared drives\DVS_StockAssets\Evermotion'

    curr_path = FileManager.GetFileNameAndPath()

    # If a scene is already open, process it.
    if curr_path:
        process_scene(curr_path, work_order)
    else:
        search_and_process(search_path, work_order)

    # Reset scene.
    FileManager.Reset(True)

    print('== Ingest Complete ==')

    # Core.EvalMAXScript("quitmax #noprompt")
