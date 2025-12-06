class VideoStream:
	def __init__(self, filename):
		self.filename = filename
		try:
			self.file = open(filename, 'rb')
		except:
			raise IOError
		self.frameNum = 0
		# Auto-detect format on first read
		self.format_type = None  # 'header' (10-byte) or 'raw' (pure JPEG stream)
		
	def nextFrame(self):
		"""Get next frame (handles both header-based and raw JPEG formats)."""
		import re
		
		# Auto-detect format on first frame
		if self.format_type is None:
			self.format_type = self._detectFormat()
		
		if self.format_type == 'header':
			return self._readHeaderedFrame()
		else:  # 'raw'
			return self._readRawJpegFrame()
	
	def _detectFormat(self):
		"""Detect if file uses 10-byte headers or raw JPEG stream."""
		pos = self.file.tell()
		test = self.file.read(20)
		self.file.seek(pos)
		
		if not test:
			return 'raw'
		
		# Check if starts with JPEG marker (FFD8)
		if test.startswith(b'\xFF\xD8'):
			print("[FORMAT] Detected raw JPEG stream format")
			return 'raw'
		
		# Check if starts with ASCII digits (10-byte header)
		if test[0:1].isdigit() or test[0:1] in b' ':
			try:
				int(test[:10].decode('utf-8').strip())
				print("[FORMAT] Detected 10-byte header format")
				return 'header'
			except:
				pass
		
		# Default to raw JPEG if unsure
		print("[FORMAT] Defaulting to raw JPEG format")
		return 'raw'
	
	def _readHeaderedFrame(self):
		"""Read frame with 10-byte ASCII length header."""
		import re
		
		# Read header: 10-byte ASCII decimal
		header = b''
		while len(header) < 10:
			chunk = self.file.read(10 - len(header))
			if not chunk:
				break
			header += chunk

		if not header:
			return None

		# Parse frame length robustly (strip whitespace)
		try:
			framelength = int(header.decode('utf-8').strip())
		except (ValueError, UnicodeDecodeError):
			# Fallback: extract first contiguous digit sequence
			m = re.search(rb"(\d+)", header)
			if not m:
				print(f"[ERROR] Cannot parse frame length from header: {header}")
				return None
			framelength = int(m.group(1))

		# Read the full frame
		remaining = framelength
		chunks = []
		while remaining > 0:
			chunk = self.file.read(remaining)
			if not chunk:
				break
			chunks.append(chunk)
			remaining -= len(chunk)

		frameData = b''.join(chunks)
		if len(frameData) == framelength:
			self.frameNum += 1
			return frameData
		else:
			print(f"[ERROR] Expected {framelength} bytes, got {len(frameData)}")
			return None
	
	def _readRawJpegFrame(self):
		"""Read raw JPEG frame from stream (FFD8...FFD9)."""
		import re
		
		# Look for JPEG start marker (FFD8)
		while True:
			byte = self.file.read(1)
			if not byte:
				return None
			
			if byte == b'\xFF':
				next_byte = self.file.read(1)
				if not next_byte:
					return None
				
				if next_byte == b'\xD8':  # Found SOI (start of image)
					frame_data = byte + next_byte
					
					# Read until end of image (FFD9)
					while True:
						byte = self.file.read(1)
						if not byte:
							break
						frame_data += byte
						
						if byte == b'\xFF':
							next_byte = self.file.read(1)
							if not next_byte:
								break
							frame_data += next_byte
							
							if next_byte == b'\xD9':  # Found EOI (end of image)
								self.frameNum += 1
								return frame_data
		
		return None
		
	def frameNbr(self):
		"""Get frame number."""
		return self.frameNum