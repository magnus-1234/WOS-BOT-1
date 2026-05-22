import time
import hashlib
import os
import sys

def main():
    print("Starting Oracle Cloud keep-alive to prevent idle shutdown...")
    print("This script burns CPU and allocates memory to maintain utilization above Oracle's 10% idle threshold.")
    
    # Allocate ~400MB to satisfy memory utilization criteria (>10% of 24GB is 2.4GB, but this helps combined with bot)
    # If the instance is 1GB, 400MB is 40% which is well above the 10% threshold.
    try:
        dummy_memory = bytearray(400 * 1024 * 1024)
    except MemoryError:
        print("Warning: Could not allocate 400MB. Running with minimal memory.")
        dummy_memory = bytearray(10 * 1024 * 1024)

    try:
        while True:
            # Modify memory to prevent it being completely swapped/optimized out
            if dummy_memory:
                dummy_memory[0] = os.urandom(1)[0]
                
            # Burn CPU for 20 seconds
            end_time = time.time() + 20
            while time.time() < end_time:
                _ = hashlib.sha256(os.urandom(128)).hexdigest()
                
            # Sleep for 5 seconds (80% load on 1 core = ~20% on 4-core ARM instance)
            time.sleep(5)
            
    except KeyboardInterrupt:
        print("Shutting down keep-alive...")
        sys.exit(0)

if __name__ == "__main__":
    main()
