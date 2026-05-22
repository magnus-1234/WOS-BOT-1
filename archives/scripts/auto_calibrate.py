import cv2
import numpy as np
import os
from alliance_monitor.capture import ScreenCapture
from alliance_monitor.monitor_config import MonitorConfig

def auto_calibrate():
    print("🚀 Starting AI Auto-Calibration...")
    
    capture = ScreenCapture()
    img = capture.capture_bluestacks()
    
    if img is None:
        print("❌ Could not capture BlueStacks window. Is it open?")
        return

    # Convert to grayscale
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    
    # Whiteout Survival cards are rounded white rectangles.
    # We look for large white-ish blobs in the bottom half of the screen
    _, thresh = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
    
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    card_y_coords = []
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        # Filter for shapes that look like half-row player cards (approx 200x100)
        if 150 < w < 250 and 80 < h < 130 and y > 400:
            card_y_coords.append(y)
            print(f"📍 Found card at: x={x}, y={y}, w={w}, h={h}")

    if not card_y_coords:
        print("❌ Could not find any player cards. Please ensure the Alliance Member list is visible.")
        return

    # The first row Y is the minimum Y of the cards found
    first_row_y = min(card_y_coords)
    
    # Calculate row height by looking at the gap between cards
    unique_ys = sorted(list(set(card_y_coords)))
    if len(unique_ys) > 1:
        row_height = unique_ys[1] - unique_ys[0]
    else:
        row_height = 115 # Fallback
        
    print("\n✅ --- CALIBRATION RESULTS ---")
    print(f"FIRST_ROW_Y = {first_row_y}")
    print(f"ROW_HEIGHT = {row_height}")
    print("----------------------------\n")
    
    print("Updating monitor_config.py...")
    # Read and update the file
    config_path = "alliance_monitor/monitor_config.py"
    with open(config_path, "r") as f:
        lines = f.readlines()
        
    with open(config_path, "w") as f:
        for line in lines:
            if "FIRST_ROW_Y =" in line:
                f.write(f"    FIRST_ROW_Y = {first_row_y}  \n")
            elif "ROW_HEIGHT =" in line:
                f.write(f"    ROW_HEIGHT = {row_height}  \n")
            else:
                f.write(line)
    
    print("Done! Please RESTART YOUR BOT now.")

if __name__ == "__main__":
    auto_calibrate()
