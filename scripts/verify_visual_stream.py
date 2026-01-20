
import os
import sys
import time
import ctypes
import mmap

# Add src to path to import VisualCortexHeader class if needed, 
# or just redefine it here to be standalone.
# Redefining for simplicity and no dependencies.

class VisualCortexHeader(ctypes.Structure):
    _fields_ = [
        ("magic_number", ctypes.c_uint32),
        ("version", ctypes.c_uint32),
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("format", ctypes.c_uint32),
        ("frame_index", ctypes.c_uint64),
        ("timestamp_us", ctypes.c_int64),
        ("row_bytes", ctypes.c_uint32),
        ("reserved", ctypes.c_uint8 * 128)
    ]

def verify_stream():
    print("Connecting to Visual Cortex...")
    map_name = "Local\\NeuralChromium_VisualCortex_V3"
    size = 1024 * 1024 * 16
    
    try:
        mm = mmap.mmap(-1, size, map_name, access=mmap.ACCESS_READ)
        print("Connected to Shared Memory!")
    except Exception as e:
        print(f"Failed to connect: {e}")
        return

    last_index = -1
    print("Listening for frames... (Ctrl+C to stop)")
    
    while True:
        mm.seek(0)
        header_bytes = mm.read(ctypes.sizeof(VisualCortexHeader))
        header = VisualCortexHeader.from_buffer_copy(header_bytes)
        
        if header.magic_number != 0x4E455552:
            print(f"Invalid Magic Number: {hex(header.magic_number)}")
        else:
            if header.frame_index > last_index:
                print(f"Frame #{header.frame_index}: {header.width}x{header.height} ts={header.timestamp_us}")
                last_index = header.frame_index
            else:
                # print(".", end="", flush=True)
                pass
        
        time.sleep(0.1)

if __name__ == "__main__":
    verify_stream()
