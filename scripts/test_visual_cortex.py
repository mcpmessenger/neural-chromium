"""
Test script to verify Visual Cortex shared memory infrastructure.

This script creates a test shared memory segment, writes a test frame,
and verifies that the Python VisualCortexClient can read it correctly.
"""

import ctypes
import mmap
import time
import struct

# Match the header structure from nexus_agent.py
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

def create_test_frame(width=1920, height=1080):
    """Create a test RGBA frame with a gradient pattern"""
    pixels = bytearray(width * height * 4)
    
    for y in range(height):
        for x in range(width):
            offset = (y * width + x) * 4
            # Create a gradient pattern
            pixels[offset + 0] = int((x / width) * 255)      # R
            pixels[offset + 1] = int((y / height) * 255)     # G
            pixels[offset + 2] = 128                          # B
            pixels[offset + 3] = 255                          # A (opaque)
    
    return bytes(pixels)

def write_test_frame_to_shared_memory(map_name="Local\\NeuralChromium_VisualCortex_V3", 
                                       width=1920, height=1080):
    """Write a test frame to shared memory"""
    
    # Calculate sizes
    header_size = ctypes.sizeof(VisualCortexHeader)
    pixel_data_size = width * height * 4
    total_size = header_size + pixel_data_size
    
    print(f"Creating shared memory segment: {map_name}")
    print(f"  Header size: {header_size} bytes")
    print(f"  Pixel data size: {pixel_data_size} bytes")
    print(f"  Total size: {total_size} bytes")
    
    # Create security descriptor with NULL DACL
    SECURITY_DESCRIPTOR_MIN_LENGTH = 20
    SECURITY_DESCRIPTOR_REVISION = 1
    
    class SECURITY_DESCRIPTOR(ctypes.Structure):
        _fields_ = [("Revision", ctypes.c_byte),
                    ("Sbz1", ctypes.c_byte),
                    ("Control", ctypes.c_short),
                    ("Owner", ctypes.c_void_p),
                    ("Group", ctypes.c_void_p),
                    ("Sacl", ctypes.c_void_p),
                    ("Dacl", ctypes.c_void_p)]
    
    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("nLength", ctypes.c_ulong),
                    ("lpSecurityDescriptor", ctypes.c_void_p),
                    ("bInheritHandle", ctypes.c_bool)]
    
    sd = ctypes.create_string_buffer(SECURITY_DESCRIPTOR_MIN_LENGTH)
    advapi32 = ctypes.windll.advapi32
    
    if not advapi32.InitializeSecurityDescriptor(sd, SECURITY_DESCRIPTOR_REVISION):
        print(f"  ❌ Failed to init SD: {ctypes.GetLastError()}")
        return False
    
    if not advapi32.SetSecurityDescriptorDacl(sd, True, None, False):
        print(f"  ❌ Failed to set NULL DACL: {ctypes.GetLastError()}")
        return False
    
    sa = SECURITY_ATTRIBUTES()
    sa.nLength = ctypes.sizeof(SECURITY_ATTRIBUTES)
    sa.lpSecurityDescriptor = ctypes.addressof(sd)
    sa.bInheritHandle = False
    
    # Create file mapping
    INVALID_HANDLE_VALUE = -1
    PAGE_READWRITE = 0x04
    
    kernel32 = ctypes.windll.kernel32
    hMap = kernel32.CreateFileMappingW(
        ctypes.c_void_p(INVALID_HANDLE_VALUE),
        ctypes.byref(sa),
        PAGE_READWRITE,
        0,
        total_size,
        map_name
    )
    
    if not hMap:
        print(f"  ❌ Failed to create file mapping: {ctypes.get_last_error()}")
        return False
    
    print("  ✓ Shared memory created with NULL DACL")
    
    # Map the memory
    try:
        mm = mmap.mmap(-1, total_size, map_name, access=mmap.ACCESS_WRITE)
    except Exception as e:
        print(f"  ❌ Failed to map memory: {e}")
        kernel32.CloseHandle(hMap)
        return False
    
    print("  ✓ Memory mapped successfully")
    
    # Create header
    header = VisualCortexHeader()
    header.magic_number = 0x4E455552  # "NEUR" in hex
    header.version = 3
    header.width = width
    header.height = height
    header.format = 1  # RGBA
    header.frame_index = 1
    header.timestamp_us = int(time.time() * 1000000)
    header.row_bytes = width * 4
    
    # Write header
    mm.seek(0)
    mm.write(bytes(header))
    print(f"  ✓ Header written (magic=0x{header.magic_number:08X})")
    
    # Generate and write pixel data
    print("  Generating test frame...")
    pixel_data = create_test_frame(width, height)
    mm.write(pixel_data)
    print(f"  ✓ Pixel data written ({len(pixel_data)} bytes)")
    
    mm.flush()
    
    print("\n✅ Test frame written to shared memory successfully!")
    print("\nNow run nexus_agent.py and call describe_screen() to verify.")
    print("The agent should be able to read this frame and convert it to an image.")
    
    # Keep the mapping alive
    input("\nPress Enter to close shared memory and exit...")
    
    mm.close()
    kernel32.CloseHandle(hMap)
    
    return True

def main():
    print("=" * 60)
    print("VISUAL CORTEX SHARED MEMORY TEST")
    print("=" * 60)
    print()
    
    success = write_test_frame_to_shared_memory()
    
    if success:
        print("\n" + "=" * 60)
        print("TEST COMPLETED SUCCESSFULLY")
        print("=" * 60)
    else:
        print("\n" + "=" * 60)
        print("TEST FAILED")
        print("=" * 60)

if __name__ == "__main__":
    main()
