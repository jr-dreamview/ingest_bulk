from PySide2 import QtCore, QtWidgets

import MaxPlus
import pymxs

threshold = 30

selected_nodes = [s for s in MaxPlus.SelectionManager.Nodes]
if selected_nodes:
    selected_object = selected_nodes[0]
    object_list = [c for c in MaxPlus.Core.GetRootNode().Children if c != selected_object]

    for node in object_list:
        pos = pymxs.runtime.getNodeByName(node.Name).pos

        for vert in range(selected_object.VertexCount):
            v_pos = selected_object.Object.GetPoint(vert)
            dist = pymxs.runtime.distance(pos, pymxs.runtime.point3(v_pos.X, v_pos.Y, v_pos.Z))

            if dist <= threshold:
                node.Select()
                print("Node {} is within threshold {} of {}".format(node.Name, threshold, selected_object.Name))
                break

widget = QtWidgets.QWidget(parent=MaxPlus.GetQMaxMainWindow())
widget.show()
