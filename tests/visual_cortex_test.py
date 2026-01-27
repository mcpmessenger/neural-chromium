import mmap
import struct
import time
import os

# Named Shared Memory
MAPPING_NAME = "Local\\NeuralChromium_VisualCortex"
FRAME_SIZE = 1920 * 1080 * 4 # Approx
TOTAL_SIZE = 32 * 1024 * 1024

def read_visual_cortex():
    try:
        # Open existing named shared memory
        shm = mmap.mmap(-1, TOTAL_SIZE, tagname=MAPPING_NAME, access=mmap.ACCESS_READ)
        
        # Read Header
        # struct VisualCortexHeader {
        #     uint32_t magic;          // 0x4E455552 "NEUR"
        #     uint32_t version;        // 1
        #     uint32_t width;          // e.g. 1920
        #     uint32_t height;         // e.g. 1080
        #     uint32_t format;         // 1 = RGBA_8888
        #     uint32_t write_cursor;   // Byte offset
        #     uint64_t frame_count;    // Counter
        #     uint64_t timestamp_us;   // Timestamp
        # };
        header_fmt = "IIIIIIQQ"
        header_size = struct.calcsize(header_fmt)
        
        while True:
            header_bytes = shm.read(header_size)
            shm.seek(0)
            
            magic, ver, w, h, fmt, cursor, count, ts = struct.unpack(header_fmt, header_bytes)
            
            if magic != 0x4E455552:
                print(f"Waiting for Visual Cortex... (Magic: {hex(magic)})")
            else:
                print(f"FRAME: {w}x{h} fmt={fmt} count={count} ts={ts}")
            
            time.sleep(1.0)
            
    except FileNotFoundError:
        print("Shared Memory not found. Is Chrome running with Visual Cortex active?")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    read_visual_cortex()
