# Bulk Ingest script for 3DS MAX

The purpose of this script is to ingest many individual assets from many 3DS MAX scene files for eventual artist use.

### How To Use

You will need to supply 2 values under `if __name__ == "__main__":`:
* The work order to which to check-in: `work_order`
* The company from which the scene originate: `INGEST_COMPANY_NAME`; This name must match the name used on the
  Company (`CustomNonProjectEntity02`) entity in Shotgun.

Once provided, there are 3 ways to process scenes:
* Open a scene file you would like to process and execute the script; Only this scene will be processed.
* Add file paths of scenes you would like to process to the `scene_file_paths` list and execute the script.
* Provide a search directory path to `SEARCH_PATH` and execute the script; Only one scene per subdirectory will be
  processed; if multiple scenes are found in the same directory, the latest scene file will be processed.

### Script Functionality Breakdown

* Search location for Max scene files.
  * Given a main directory of a company, each immediate sub-folder is treated as a different scene.  Search each
    scene
    folder for a 3DS MAX file.  Often multiple MAX files are found.  Choose only one per scene folder.  The default is the newest file.
* Once the scene file is found, open the scene file.
  * Search for missing external files and repath.  The files are usually located in an adjacent folder to wherever the
    scene file is located.
* Check-in the entire scene into Shotgun.
  * Search adjacent folders for renders of the scene.  Include these images in the check-in.
  * This will upload every external file and repath.
* Check-out the entire scene from Shotgun.
  * All files will now download to a local directory mapped to V:.  All external file paths will point to a folder
    on V:.
* Find all unique geometry.
  * Analyze all top level nodes in the DAG.  Do not dive into groups; each group will be considered a single asset.
  * Duplicate assets will be ignored; only one of each asset will be ingested.  Duplicate assets have the same geo
    AND materials.  If the geo is the same but materials are different, it is considered unique, not a duplicate.
* Once a full list of ingestible nodes is found, save each node into their own 3DS MAX file.
  * Saving the node immediately will cause the geo to be in the same position in 3D space as it was in the scene.  
    For convenience of the artist checking the asset out later, the asset should be moved to the origin.  The lowest point should be ground level (XY plane, Z = 0).
* Open each generated asset 3DS MAX scene file.
* Check in each asset scene.
  * Generate a JSON file the mimics the material network of the asset.
  * Include .vrscene.
  * Include textures from QC Tool, otherwise it won't render on the farm correctly.
* Generate thumbnail.
  * Import QC Tool.  This will import lights and cameras into scene.  It will also include textures for IBL.
  * Export 2 .vrscene files.  1 for lookdev, 1 for UVs.
* Create farm jobs to render each .vrscene.  These will render thumbnails and be ingested back into the rest of the
  files for the asset.
* After every asset is checked-in, reset 3DS MAX and search in the next shot folder for the next 3DS MAX file.
