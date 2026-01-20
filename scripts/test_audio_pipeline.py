"""
Test script to verify the audio file tailing fix.

This script simulates Chrome writing audio chunks to chrome_debug.log
and verifies that nexus_agent.py successfully processes them.
"""

import base64
import time
import os
import struct
import random

# Paths
LOG_DIR = r"C:\tmp\neural_chrome_profile"
CHROME_LOG = os.path.join(LOG_DIR, "chrome_debug.log")
AGENT_LOG = r"C:\tmp\nexus_agent.log"

def generate_synthetic_pcm(duration_seconds=0.5, sample_rate=16000):
    """Generate synthetic PCM audio (sine wave)"""
    num_samples = int(duration_seconds * sample_rate)
    samples = []
    
    # Generate a 440Hz tone (A note)
    frequency = 440
    for i in range(num_samples):
        t = i / sample_rate
        # Sine wave with amplitude 0.3 to avoid clipping
        value = int(0.3 * 32767 * (2.0 * 3.14159 * frequency * t % (2 * 3.14159) - 3.14159) / 3.14159)
        samples.append(value)
    
    # Pack as 16-bit signed integers
    pcm_bytes = struct.pack(f"<{len(samples)}h", *samples)
    return pcm_bytes

def write_audio_chunk_to_log(pcm_data):
    """Write a synthetic audio chunk to chrome_debug.log in the expected format"""
    b64_data = base64.b64encode(pcm_data).decode('ascii')
    
    # Ensure log directory exists
    os.makedirs(LOG_DIR, exist_ok=True)
    
    # Append to log file (simulating Chrome's behavior)
    with open(CHROME_LOG, "a", encoding='utf-8') as f:
        # Simulate Chrome's log format
        timestamp = time.strftime("%H:%M:%S")
        f.write(f"[{timestamp}] AUDIO_DATA:{b64_data}\n")
        f.flush()  # Force write to disk

def check_agent_log_for_processing(timeout=5):
    """Check if nexus_agent.log shows the chunk was processed"""
    start_time = time.time()
    
    if not os.path.exists(AGENT_LOG):
        return False, "Agent log file doesn't exist"
    
    # Get initial file size
    initial_size = os.path.getsize(AGENT_LOG)
    
    while time.time() - start_time < timeout:
        current_size = os.path.getsize(AGENT_LOG)
        
        if current_size > initial_size:
            # File grew, read new content
            with open(AGENT_LOG, "r", encoding='utf-8', errors='ignore') as f:
                f.seek(initial_size)
                new_content = f.read()
                
                # Check for processing indicators
                if "Received Audio Chunk" in new_content or "Audio chunks processed" in new_content:
                    return True, "Agent processed chunk successfully"
                elif "Audio Decode Error" in new_content:
                    return False, f"Decode error: {new_content}"
        
        time.sleep(0.1)
    
    return False, f"Timeout: No processing detected after {timeout}s"

def main():
    print("=" * 60)
    print("AUDIO FILE TAILING TEST")
    print("=" * 60)
    
    # Check if nexus_agent.py is running
    print("\n[1] Checking if nexus_agent.py is running...")
    if not os.path.exists(AGENT_LOG):
        print("    ⚠️  WARNING: nexus_agent.log not found")
        print("    Make sure nexus_agent.py is running before this test")
        response = input("    Continue anyway? (y/n): ")
        if response.lower() != 'y':
            return
    else:
        print("    ✓ Agent log found")
    
    # Generate synthetic audio
    print("\n[2] Generating synthetic PCM audio (0.5s, 16kHz)...")
    pcm_data = generate_synthetic_pcm(duration_seconds=0.5)
    print(f"    ✓ Generated {len(pcm_data)} bytes of PCM data")
    
    # Write multiple chunks to test continuous processing
    num_chunks = 5
    print(f"\n[3] Writing {num_chunks} audio chunks to chrome_debug.log...")
    
    for i in range(num_chunks):
        write_audio_chunk_to_log(pcm_data)
        print(f"    ✓ Chunk {i+1}/{num_chunks} written")
        time.sleep(0.2)  # Simulate realistic timing
    
    # Verify processing
    print("\n[4] Waiting for nexus_agent.py to process chunks...")
    success, message = check_agent_log_for_processing(timeout=10)
    
    if success:
        print(f"    ✅ TEST PASSED: {message}")
        print("\n" + "=" * 60)
        print("RESULT: Audio pipeline is working correctly!")
        print("=" * 60)
    else:
        print(f"    ❌ TEST FAILED: {message}")
        print("\n" + "=" * 60)
        print("RESULT: Audio pipeline may have issues")
        print("=" * 60)
        print("\nTroubleshooting:")
        print("1. Ensure nexus_agent.py is running")
        print("2. Check C:\\tmp\\nexus_agent.log for errors")
        print("3. Verify chrome_debug.log exists and is being written to")

if __name__ == "__main__":
    main()
