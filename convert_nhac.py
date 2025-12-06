#!/usr/bin/env python
"""Convert nhacMovie.mjpeg.avi to MJPEG format with 10-byte headers."""
import re

def extract_jpeg_from_mjpeg(input_file, output_file):
    print(f"[INFO] Starting conversion of {input_file}...")
    
    with open(input_file, 'rb') as f:
        data = f.read()
    
    print(f"[INFO] Read {len(data) / (1024*1024):.1f} MB from input file")
    print(f"[INFO] Scanning for JPEG frames (FFD8...FFD9)...")
    
    # Find all JPEG start and end markers
    frames = []
    soi_pattern = b'\xFF\xD8'
    eoi_pattern = b'\xFF\xD9'
    
    # Find all SOI positions
    soi_positions = [m.start() for m in re.finditer(re.escape(soi_pattern), data)]
    # Find all EOI positions
    eoi_positions = [m.start() + 2 for m in re.finditer(re.escape(eoi_pattern), data)]
    
    print(f"[INFO] Found {len(soi_positions)} SOI and {len(eoi_positions)} EOI markers")
    
    if not soi_positions or not eoi_positions:
        print(f'[ERROR] No JPEG frames found in {input_file}')
        return
    
    # Match SOI with closest following EOI
    for soi in soi_positions:
        matching_eoi = next((eoi for eoi in eoi_positions if eoi > soi), None)
        if matching_eoi:
            jpeg_data = data[soi:matching_eoi]
            frames.append(jpeg_data)
    
    print(f"[INFO] Matched {len(frames)} complete JPEG frames")
    print(f"[INFO] Writing output...")
    
    with open(output_file, 'wb') as out:
        for idx, frame_data in enumerate(frames):
            length = len(frame_data)
            header = f'{length:>10d}'.encode('utf-8')
            out.write(header)
            out.write(frame_data)
            if (idx + 1) % 100 == 0:
                print(f"[PROGRESS] Wrote {idx + 1}/{len(frames)} frames")
    
    print(f'[SUCCESS] Converted {input_file} -> {output_file}')
    print(f'[INFO] Extracted {len(frames)} JPEG frames with 10-byte headers')

if __name__ == "__main__":
    extract_jpeg_from_mjpeg('nhacMovie.mjpeg.avi', 'nhacMovie_fixed.mjpeg')
