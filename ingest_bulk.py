from difflib import SequenceMatcher
import logging
import os
import re
import sys

import sgtk

# from utils.sg_create_entities import create_asset
from utils.sg_create_entities import create_deliverable

from MaxPlus import (Core, FileManager, Matrix3, PathManager, Point3,
                     SelectionManager, SuperClassIds, ViewportManager)
from pymxs import runtime as mxs

# get the script folder from shotgun
script_folder = mxs.execute('''
(
    local resultPath = undefined
    global DreamView_Scripts_ScriptsPath
    if DreamView_Scripts_ScriptsPath != undefined then
    (
        local toolsDir = getFilenamePath DreamView_Scripts_ScriptsPath
        if doesFileExist toolsDir then
        (
            local checkDirs = getDirectories (toolsDir + "Check-*")
            if checkDirs.count > 0 then resultPath = checkDirs[1]
        )
    )
    resultPath
)
''')

# import check_out and check_in
if script_folder not in sys.path:
    sys.path.insert(0, script_folder)

from check_in_out import check_in


DEBUG_EXPORT_COUNT = 5
DEBUG_SKIP_EXPORT_MAX = False
DEBUG_SKIP_CHECKIN = True
DEFAULT_PERSP_VIEW_MATRIX = Matrix3(
    Point3(0.707107, 0.353553, -0.612372),
    Point3(-0.707107, 0.353553, -0.612372),
    Point3(0, 0.866025, 0.5),
    Point3(0, 0, -250))
EXCLUDE_SUPERCLASS_IDS = [SuperClassIds.Light, SuperClassIds.Camera]
INTERSECTION_DIST = 250.0
logging.basicConfig()
LOGGER = logging.getLogger()
NODES_HIDE_STATE = {}
ORIGIN_POSITION = Point3(0, 0, 0)
ORIGIN_TRANSFORM_MATRIX = Matrix3(
    Point3(1, 0, 0),
    Point3(0, 1, 0),
    Point3(0, 0, 1),
    Point3(0, 0, 0))
SG_ENGINE = sgtk.platform.current_engine()
SG = SG_ENGINE.shotgun
SIMILAR_RATIO = 0.80
VIEW_PERSP_USER = 7  # Viewport type Perspective User enum


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


def create_asset(sg, logger, project, wrk_ordr, name, deliverable_type=None,
                 deliverable_sub_type='Hi', deliverable=None, **fields):
    """Creates an asset in shotgun.
    If a deliverable is not provided, one will be created.

    Args:
        sg (tank.authentication.shotgun_wrapper.ShotgunWrapper): Shotgun
            API instance.
        logger (logging.Logger): Pythong logger.
        project (dict): Shotgun project.
        wrk_ordr (dict): Shotgun entity work order.
        name (str): Asset name.
        deliverable_type (str): Deliverable type.
        deliverable_sub_type (str): Deliverable sub-type.
        deliverable (dict): Deliverable dictionary.
        **fields (dict): Keyword filter fields.

    Returns:
        dict: New asset dictionary.
    """
    filters = list()
    for key, value in fields.items():
        filters.append([key, 'is', value])
    new_asset = sg.create('Asset', {'project': project, 'code': name}, filters)
    if deliverable:
        sg.update(
            'CustomEntity24', deliverable.get('id'), {'sg_link': new_asset})
    else:
        deliverable_data = {
            'project': project,
            'sg_work_order': wrk_ordr,
            'deliverable_type': deliverable_type,
            'sub_type': deliverable_sub_type
        }
        create_deliverable(sg, logger, deliverable_data, new_asset)
    return new_asset


def export_node(node, name):
    """Save node to another max file.
    Get one node, move it to the origin, move the viewport to see it,
    save to another max file, move the node back to its previous position.

    Args:
        node (MaxPlus.INode): Node to save into new file.
        name (str): Name of the max file to which the node is going.

    Returns:
        dict[str, str]:
    """
    node_export_data = {
        'max': '',
        'original_node': ''
    }

    for n in get_all_nodes([node]):
        n.Hide = NODES_HIDE_STATE[n.Name]

    node_pos = node.Position
    node.Position = ORIGIN_POSITION
    SelectionManager.ClearNodeSelection(True)
    node.Select()
    ViewportManager.ViewportZoomExtents(True)
    SelectionManager.ClearNodeSelection(True)
    ViewportManager.RedrawViewportsNow(Core.GetCurrentTime())

    save_dir = os.path.join(PathManager.GetSceneDir(), "Export",
                            FileManager.GetFileName().rsplit(".", 1)[0])
    # Make the export directory if it doesn't exist.
    if not os.path.isdir(save_dir):
        os.makedirs(save_dir)
    save_path = os.path.join(save_dir, "{}.max".format(name))
    print('{}:'.format(name))

    if not DEBUG_SKIP_EXPORT_MAX:
        node.Select()
        FileManager.SaveNodes(
            SelectionManager.GetNodes(),
            save_path)
        print('\tExporting MAX file: {}'.format(save_path))
        node_export_data['max'] = save_path
        node_export_data['original_node'] = node.Name
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
    node_data_dict = {}

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
        NODES_HIDE_STATE[node.Name] = node.Hide
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

    print("Exporting {} nodes...".format(len(nodes)))

    # Export

    # Debug count
    total = DEBUG_EXPORT_COUNT
    count = 0

    for node, name in nodes:
        node_data_dict[name] = export_node(node, name)

        # debug count
        if DEBUG_EXPORT_COUNT:
            count += 1
            if count == total:
                break

    SelectionManager.ClearNodeSelection(True)
    # Set all nodes previous visible state.
    for node in get_all_nodes():
        node.Hide = NODES_HIDE_STATE[node.Name]

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

    print("\n{} nodes exported.".format(len(nodes)))

    return node_data_dict


def get_all_nodes(nodes=None):
    """Returns all descendants of a node.
    If None is provided, it will return all nodes in the scene.

    Args:
        nodes (list[MaxPlus.INode]|None): Nodes to find descendants.

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


def process_scenes(scn_file_paths, wrk_order):
    """Process the list of scene files to check in assets.

    Args:
        scn_file_paths (list[str]): List of scene paths to open and process.
        wrk_order (dict): Work order dictionary.
    """
    for scene_file_path in scn_file_paths:
        FileManager.Open(scene_file_path, True)

        current_file_path = FileManager.GetFileNameAndPath()

        asset_nodes = get_asset_nodes()

        asset_data_dict = export_nodes(asset_nodes)

        print("Checking-in Files for scene:\n{}".format(current_file_path))

        for asset_name in asset_data_dict:
            asset_file_path = asset_data_dict[asset_name].get('max')
            original_node_name = \
                asset_data_dict[asset_name].get('original_node')

            # open file
            if not asset_file_path:
                print('Skipping {}'.format(asset_name))

                continue

            print("\tOpening {}".format(asset_file_path))

            FileManager.Open(asset_file_path, True)

            if not DEBUG_SKIP_CHECKIN:
                # create Asset

                asset = create_asset(
                    SG, LOGGER, SG_ENGINE.context.project, wrk_order,
                    asset_name, deliverable_type='Asset Ingest Bulk')

                # Get Task

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

                # check in

                description = "Checked-in from 3DS MAX.\n\n" \
                              "Original File:\n" \
                              "{}\n\n" \
                              "Original Node:\n" \
                              "{}".format(current_file_path, original_node_name)
                result = check_in(task['id'], description=description)

                print(result)

    FileManager.Reset(True)

    print('Done.')


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

    test_file_path = r'C:\Users\john.russell\Documents\3ds Max 2020\scenes\AE34_001.max'
    scene_file_paths = [test_file_path]

    process_scenes(scene_file_paths, work_order)

    # Core.EvalMAXScript("quitmax #noprompt")
