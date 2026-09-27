import cv2
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.services.data.vod_layouts import crop_slots, load_layout  # noqa: E402

FRAMES_DIR = "backend/data/raw/frames/m7"
TOURNAMENT_ID = "M7_World"
OUTPUT_DIR = "backend/data/crops/m7_test"

layout = load_layout(TOURNAMENT_ID)

# Process every frame
frame_paths = sorted(Path(FRAMES_DIR).glob("*.jpg"))
print(f"Processing {len(frame_paths)} frames...")

for frame_path in frame_paths:
    img = cv2.imread(str(frame_path))
    if img is None:
        continue

    for slot_name, crop in crop_slots(img, layout).items():
        crop = cv2.resize(crop, (64, 64))
        slot_output_dir = Path(OUTPUT_DIR) / slot_name
        slot_output_dir.mkdir(parents=True, exist_ok=True)
        out_path = slot_output_dir / frame_path.name
        cv2.imwrite(str(out_path), crop)

print(f"Done! Crops saved to {OUTPUT_DIR}")
