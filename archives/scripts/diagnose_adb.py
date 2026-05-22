import subprocess
import time

# Use the full path as configured
ADB_PATH = r"C:\adb\platform-tools\adb.exe"

def check_adb_ports():
    ports = [5555, 5565, 5575, 5585, 5595, 5605, 5615, 5625]
    print(f"🔍 Searching for BlueStacks ADB port using {ADB_PATH}...")
    
    # Try to kill and restart server first to clear any stale state
    subprocess.run(f'"{ADB_PATH}" kill-server', shell=True)
    subprocess.run(f'"{ADB_PATH}" start-server', shell=True)
    time.sleep(2)

    for port in ports:
        print(f"Trying port {port}...")
        cmd = f'"{ADB_PATH}" connect localhost:{port}'
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
            if "connected to" in result.stdout:
                # Double check with adb devices
                dev_res = subprocess.run(f'"{ADB_PATH}" devices', shell=True, capture_output=True, text=True)
                if f"localhost:{port}" in dev_res.stdout:
                    print(f"✅ FOUND! BlueStacks is on port {port}")
                    return port
        except:
            pass
            
    # Try getting devices directly
    result = subprocess.run(f'"{ADB_PATH}" devices', shell=True, capture_output=True, text=True)
    print("\nCurrent ADB devices:")
    print(result.stdout)
    return None

if __name__ == "__main__":
    found_port = check_adb_ports()
    if found_port:
        print(f"\nUpdate ADB_PORT in monitor_config.py to: {found_port}")
    else:
        print("\n❌ Could not find BlueStacks via ADB.")
        print("Please enable 'Android Debug Bridge (ADB)' in BlueStacks Settings > Advanced.")
