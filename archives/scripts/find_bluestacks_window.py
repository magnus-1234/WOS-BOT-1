"""
Helper script to find all BlueStacks windows
Run this to see which window title to use in monitor_config.py
"""

import win32gui

def enum_windows_callback(hwnd, windows):
    if win32gui.IsWindowVisible(hwnd):
        title = win32gui.GetWindowText(hwnd)
        if "BlueStacks" in title or "Whiteout" in title:
            rect = win32gui.GetWindowRect(hwnd)
            x, y, x2, y2 = rect
            width = x2 - x
            height = y2 - y
            windows.append({
                'title': title,
                'size': f"{width}x{height}",
                'hwnd': hwnd
            })

windows = []
win32gui.EnumWindows(enum_windows_callback, windows)

print("=" * 70)
print("FOUND BLUESTACKS/WHITEOUT WINDOWS:")
print("=" * 70)

if not windows:
    print("❌ No BlueStacks or Whiteout Survival windows found!")
    print("\nMake sure:")
    print("1. BlueStacks is running")
    print("2. Whiteout Survival is open in BlueStacks")
else:
    for i, win in enumerate(windows, 1):
        print(f"\n{i}. Window Title: {win['title']}")
        print(f"   Size: {win['size']}")
        print(f"   HWND: {win['hwnd']}")

print("\n" + "=" * 70)
print("RECOMMENDATION:")
print("=" * 70)
print("Update WINDOW_TITLE_PATTERN in alliance_monitor/monitor_config.py")
print("to match the window title that contains Whiteout Survival.")
print("\nExample:")
print('WINDOW_TITLE_PATTERN = "Whiteout Survival"')
print('or')
print('WINDOW_TITLE_PATTERN = "BlueStacks App Player"')
print("=" * 70)
