import cv2
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.services.data.vod_layouts import (  # noqa: E402
    LAYOUTS_PATH,
    detect_active_video_bounds,
    load_layout,
    slot_bounds_for_frame,
)

FRAME_PATH = "backend/data/raw/frames/m7/frame_00001.jpg"
TOURNAMENT_ID = "M7_World"

layout = load_layout(TOURNAMENT_ID, LAYOUTS_PATH)

# Load frame
img = cv2.imread(FRAME_PATH)

# Draw each slot
for name, (x, y, sw, sh) in slot_bounds_for_frame(
    layout,
    img.shape,
    active_bounds=detect_active_video_bounds(img),
).items():
    color = (0, 200, 0) if "blue" in name else (0, 0, 200)  # green=blue team, red=red team
    cv2.rectangle(img, (x, y), (x + sw, y + sh), color, 1)
    cv2.putText(img, name, (x, y - 4),
                cv2.FONT_HERSHEY_SIMPLEX, 0.35, color, 1)

cv2.imshow("Layout Preview", img)
cv2.waitKey(0)
cv2.destroyAllWindows()
