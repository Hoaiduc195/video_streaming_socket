"""
Convert an MJPEG file to a clean standard format with consistent 5-byte headers.
This fixes glitching by ensuring each frame has a reliable length header.
"""
import struct
from pathlib import Path

def extract_jpeg_from_mjpeg(input_file, output_file):
    """Extract JPEG frames from MJPEG and re-write with clean 5-byte length headers."""
    
    frames = []
    
    with open(input_file, 'rb') as f:
        data = f.read()
    
    # Find all JPEG boundaries (FFD8 = SOI, FFD9 = EOI)
    jpeg_frames = []
    i = 0
    while i < len(data) - 1:
        # Look for JPEG SOI marker (FFD8)
        if data[i:i+2] == b'\xFF\xD8':
            start = i
            # Look for JPEG EOI marker (FFD9)
            j = i + 2
            while j < len(data) - 1:
                if data[j:j+2] == b'\xFF\xD9':
                    end = j + 2
                    jpeg_data = data[start:end]
                    jpeg_frames.append(jpeg_data)
                    i = end
                    break
                j += 1
            else:
                i += 1
        else:
            i += 1
    
    if not jpeg_frames:
        print(f"[ERROR] No JPEG frames found in {input_file}")
        return
    
    # Write output with 5-byte ASCII length headers
    with open(output_file, 'wb') as out:
        for frame_data in jpeg_frames:
            # Write 5-byte length header (ASCII, space-padded)
            length = len(frame_data)
            header = f"{length:5d}".encode('utf-8')
            out.write(header)
            out.write(frame_data)
    
    print(f"[SUCCESS] Converted {input_file} -> {output_file}")
    print(f"[INFO] Extracted {len(jpeg_frames)} JPEG frames")
    print(f"[INFO] Output file: {Path(output_file).absolute()}")

if __name__ == "__main__":
    input_file = "movie.Mjpeg"
    output_file = "movie_fixed.mjpeg"
    
    if not Path(input_file).exists():
        print(f"[ERROR] {input_file} not found")
    else:
        extract_jpeg_from_mjpeg(input_file, output_file)
