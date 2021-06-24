echo off
set work_order_id=%1
set dir_path=%2
shift
shift
C:\dvs_ingest\venv\Scripts\python -m ingest_folder %work_order_id% %dir_path%