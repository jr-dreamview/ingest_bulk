import io
import json
import os


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


def generate_manifest(search_path):
    """Writes out the list of found max files.

    Args:
        search_path (str): Path to search for max files.
    """
    json_path = os.path.join(search_path, "manifest.json")
    files_dict = {}
    count = 0
    for files in max_walk(search_path):
        for max_file_path in files:
            count += 1
            files_dict[count] = max_file_path
            
    with io.open(json_path, "w", encoding="utf8") as json_file:
        json_file.write(unicode(json.dumps(files_dict, ensure_ascii=False, indent=4)))
    

if __name__ == "__main__":
    SEARCH_PATH = r"Q:\Shared drives\DVS_StockAssets\Evermotion\From_Adnet\__ingest_bulk__"
    generate_manifest(SEARCH_PATH)
    print("==Done==")
