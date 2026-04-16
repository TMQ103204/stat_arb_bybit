import json
import os

# Save status — supports per-pair status files for multi-pair mode
def save_status(dict, pair_id=None):
    if pair_id:
        filename = f"status_{pair_id}.json"
    else:
        filename = "status.json"
    with open(filename, "w") as fp:
        json.dump(dict, fp, indent=4)

