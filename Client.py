import sys
from tkinter import *
import tkinter.messagebox as messagebox
from PIL import Image, ImageTk
import socket, threading, time, os, glob
from collections import deque
import struct

from RtpPacket import RtpPacket

CACHE_FILE_NAME = "cache-"
CACHE_FILE_EXT = ".jpg"

class Client:
	INIT = 0
	READY = 1
	PLAYING = 2
	state = INIT
	
	SETUP = 0
	PLAY = 1
	PAUSE = 2
	TEARDOWN = 3
	
	# HD Configuration
	BUFFER_SIZE = 1  # Minimal pre-buffer for ultra-low latency
	MAX_BUFFER = 50  # Maximum frames to buffer for high FPS
	
	def __init__(self, master, serveraddr, serverport, rtpport, filename):
		self.master = master
		self.master.protocol("WM_DELETE_WINDOW", self.handler)
		self.createWidgets()
		self.serverAddr = serveraddr
		self.serverPort = int(serverport)
		self.rtpPort = int(rtpport)
		self.fileName = filename
		self.rtspSeq = 0
		self.sessionId = 0
		self.requestSent = -1
		self.teardownAcked = 0
		self.connectToServer()
		self.frameNbr = 0
		
		# Frame buffer for high-speed streaming (keep in memory, skip disk I/O)
		self.frameBuffer = deque(maxlen=100)  # Auto-manages buffer size
		self.bufferThreshold = self.BUFFER_SIZE
		self.buffering = False
		
		# Display optimization
		self.lastLabelWidth = 0
		self.lastLabelHeight = 0
		self.frameDisplayCount = 0  # Track frames for performance monitoring
		
		# Fragment reassembly buffer with improved timeout handling
		self.fragmentBuffer = {}
		self.fragmentTimeout = 3.0  # Reduced from 5.0 for faster recovery
		
		# Packet loss tracking
		self.lastSeqNum = -1
		self.seqNumGaps = 0
		
		# Statistics
		self.stats = {
			'frames_received': 0,
			'frames_dropped': 0,
			'fragments_received': 0,
			'bytes_received': 0,
			'start_time': None,
			'latency': []
		}
	
	def createWidgets(self):
		"""Build GUI."""
		# Configure window size for 1280x720 minimum HD display
		self.master.geometry("1280x720")
		self.master.minsize(960, 540)  # 16:9 aspect ratio
		self.master.title("RTP Video Streaming Client - HD")
		
		# Configure grid weights for resizing
		self.master.grid_rowconfigure(0, weight=1)  # Video label expands to fill
		self.master.grid_rowconfigure(1, weight=0)  # Button frame stays small
		self.master.grid_columnconfigure(0, weight=1)
		
		# Video display label (main area) - use fill to show full resolution
		self.label = Label(self.master, bg="black")
		self.label.grid(row=0, column=0, columnspan=4, sticky=W+E+N+S, padx=5, pady=5)
		self.label.pack_propagate(False)  # Prevent label from shrinking to content size
		
		# Control buttons frame
		button_frame = Frame(self.master)
		button_frame.grid(row=1, column=0, columnspan=4, sticky=W+E, padx=5, pady=5)
		button_frame.grid_columnconfigure(0, weight=1)
		button_frame.grid_columnconfigure(1, weight=1)
		button_frame.grid_columnconfigure(2, weight=1)
		button_frame.grid_columnconfigure(3, weight=1)
		
		# Setup button
		self.setup = Button(button_frame, text="Setup", command=self.setupMovie, 
							font=("Arial", 12), padx=20, pady=10, bg="#4CAF50", fg="white")
		self.setup.grid(row=0, column=0, padx=2, pady=2, sticky=W+E)
		
		# Play button		
		self.start = Button(button_frame, text="Play", command=self.playMovie,
						   font=("Arial", 12), padx=20, pady=10, bg="#2196F3", fg="white")
		self.start.grid(row=0, column=1, padx=2, pady=2, sticky=W+E)
		
		# Pause button			
		self.pause = Button(button_frame, text="Pause", command=self.pauseMovie,
						   font=("Arial", 12), padx=20, pady=10, bg="#FF9800", fg="white")
		self.pause.grid(row=0, column=2, padx=2, pady=2, sticky=W+E)
		
		# Teardown button
		self.teardown = Button(button_frame, text="Teardown", command=self.exitClient,
							   font=("Arial", 12), padx=20, pady=10, bg="#F44336", fg="white")
		self.teardown.grid(row=0, column=3, padx=2, pady=2, sticky=W+E)
		
		# Statistics label
		self.statsLabel = Label(self.master, text="Ready", fg="blue", font=("Arial", 10), 
							   bg="lightgray", height=2, wraplength=800)
		self.statsLabel.grid(row=2, column=0, columnspan=4, sticky=W+E, padx=5, pady=2)
	
	def setupMovie(self):
		"""Setup button handler."""
		if self.state == self.INIT:
			self.sendRtspRequest(self.SETUP)
	
	def exitClient(self):
		"""Teardown button handler - properly close all threads and connections."""
		try:
			# Signal all threads to stop
			if hasattr(self, 'playEvent') and self.playEvent:
				self.playEvent.set()
			
			# Send TEARDOWN to server
			self.sendRtspRequest(self.TEARDOWN)
			
			# Wait briefly for threads to exit gracefully
			time.sleep(0.2)
			
			# Close RTP socket if open
			try:
				if hasattr(self, 'rtpSocket') and self.rtpSocket:
					self.rtpSocket.close()
			except:
				pass
			
			# Close RTSP socket if open
			try:
				if hasattr(self, 'rtspSocket') and self.rtspSocket:
					self.rtspSocket.close()
			except:
				pass
			
			# Print final statistics
			self.printStats()
			
		except Exception as e:
			print(f"Error during teardown: {e}")
		
		finally:
			# Cleanup cache files
			try:
				pattern = f"{CACHE_FILE_NAME}{self.sessionId}-*{CACHE_FILE_EXT}"
				for f in glob.glob(pattern):
					try:
						os.remove(f)
					except:
						pass
			except:
				pass
			
			# Force window closure
			try:
				self.master.destroy()
			except:
				import sys
				sys.exit(0)
	
	def pauseMovie(self):
		"""Pause button handler."""
		if self.state == self.PLAYING:
			self.sendRtspRequest(self.PAUSE)
	
	def playMovie(self):
		"""Play button handler."""
		if self.state == self.READY:
			self.stats['start_time'] = time.time()
			self.buffering = True
			
			# Start RTP listener thread
			threading.Thread(target=self.listenRtp).start()
			
			# Start display thread
			self.master.after(50, self.displayFramesScheduled)
			
			self.playEvent = threading.Event()
			self.playEvent.clear()
			self.sendRtspRequest(self.PLAY)

	def displayFramesScheduled(self):

		try:
			if self.playEvent.isSet():
				return  # Exit gracefully when teardown is called
			
				# Buffering phase (ultra-minimal for 1-frame startup)
				if self.buffering:
					if len(self.frameBuffer) >= self.bufferThreshold:
						self.buffering = False
						print(f"[BUFFER] Ready - {len(self.frameBuffer)} frames buffered")
					else:
						self.updateStatsLabel(f"Buffering: {len(self.frameBuffer)}/{self.bufferThreshold}")
						self.master.after(1, self.displayFramesScheduled)  # Nano-fast buffer check (1ms)
						return
				
				# Check buffer level (ultra-low threshold for continuous playback at 240 FPS)
				if len(self.frameBuffer) < 1:
					self.buffering = True
					print("[BUFFER] Rebuffering...")
					self.master.after(10, self.displayFramesScheduled)
					return			# Display frame
			if len(self.frameBuffer) > 0:
				frame_info = self.frameBuffer.popleft()
				
				# Calculate latency (keep lightweight statistics)
				latency = (time.time() - frame_info['timestamp']) * 1000
				if len(self.stats['latency']) >= 100:
					self.stats['latency'].pop(0)
				self.stats['latency'].append(latency)
				
				self.updateMovie(frame_info['data'])  # Pass raw JPEG data directly
				self.frameDisplayCount += 1
				
				# Update stats less frequently to reduce GUI overhead (every 20 frames)
				if self.frameDisplayCount % 20 == 0:
					self.updateStatsLabel()
				
				# Ultra-aggressive timing for smooth playback (0ms = display as fast as possible)
				if len(self.frameBuffer) > 15:
					delay = 0   # No delay when buffer full (display as fast as possible)
				elif len(self.frameBuffer) > 10:
					delay = 0   # Still fast
				elif len(self.frameBuffer) > 5:
					delay = 1   # 1ms minimum
				elif len(self.frameBuffer) > 2:
					delay = 2   # 2ms normal playback
				else:
					delay = 3   # 3ms catch-up
				
				self.master.after(delay, self.displayFramesScheduled)
			else:
				self.master.after(50, self.displayFramesScheduled)
				
		except Exception as e:
			# Only reschedule if window is still valid
			if not self.playEvent.isSet():
				print(f"Display error: {e}")
				try:
					self.master.after(50, self.displayFramesScheduled)
				except:
					pass  # Window already destroyed

	
	def listenRtp(self):
		"""Listen for RTP packets with high-speed packet processing."""
		print("[CLIENT] Listening for RTP packets...")
		
		packet_count = 0
		while True:
			# Check if playback has stopped
			if hasattr(self, 'playEvent') and self.playEvent and self.playEvent.isSet():
				print("[CLIENT] RTP listener stopping...")
				break
			
			try:
				data = self.rtpSocket.recv(524288)  # 512KB receive per call (4x larger for 360 FPS)
				
				if data:
					rtpPacket = RtpPacket()
					rtpPacket.decode(data)
					
					self.stats['bytes_received'] += len(data)
					payload = rtpPacket.getPayload()
					packet_count += 1
					
					# Log very infrequently (every 2000 packets) to avoid performance impact
					if packet_count % 2000 == 0:
						print(f"[STATS] Received {packet_count} RTP packets, Buffer: {len(self.frameBuffer)}")
					
					# Check if fragmented
					if len(payload) > 6 and self.isFragmented(payload):
						self.handleFragment(rtpPacket, payload)
					else:
						# Regular frame (not fragmented)
						self.handleFrame(rtpPacket.seqNum(), payload)
					
			except socket.timeout:
				# Socket timeout is normal, just continue listening
				continue
			except Exception as e:
				if self.playEvent.isSet():
					break
				print(f"[RTP ERROR] {e}")
	
	def isFragmented(self, payload):
		"""Check if payload contains fragmentation header."""
		try:
			fragNum, numFrags, frameSize = struct.unpack('!HHH', payload[:6])
			return numFrags > 1
		except:
			return False
	
	def handleFragment(self, rtpPacket, payload):
		"""Handle fragmented frame with improved loss detection."""
		try:
			# Extract fragmentation header
			fragNum, numFragments, frameSize = struct.unpack('!HHH', payload[:6])
			fragmentData = payload[6:]
			
			frameNumber = rtpPacket.seqNum()
			marker = rtpPacket.getMarker()
			
			self.stats['fragments_received'] += 1
			
			# Detect sequence number gaps (packet loss indicator)
			if self.lastSeqNum >= 0 and frameNumber > self.lastSeqNum + 1:
				gap = frameNumber - self.lastSeqNum - 1
				self.seqNumGaps += gap
				# Log less frequently to avoid overhead
				if self.seqNumGaps % 10 == 0:
					print(f"[LOSS] Detected {gap} missing frame(s) between seq {self.lastSeqNum} and {frameNumber}")
			self.lastSeqNum = frameNumber
			
			# Initialize buffer for this frame
			if frameNumber not in self.fragmentBuffer:
				self.fragmentBuffer[frameNumber] = {
					'fragments': {},
					'total': numFragments,
					'size': frameSize,
					'timestamp': time.time(),
					'received_count': 0
				}
			
			# Store fragment (avoid duplicates)
			if fragNum not in self.fragmentBuffer[frameNumber]['fragments']:
				self.fragmentBuffer[frameNumber]['fragments'][fragNum] = fragmentData
				self.fragmentBuffer[frameNumber]['received_count'] += 1
			
			received = self.fragmentBuffer[frameNumber]['received_count']
			total = numFragments
			
			# Check if complete
			if received == total:
				# Reassemble in correct order
				completeFrame = b''
				for i in range(numFragments):
					if i in self.fragmentBuffer[frameNumber]['fragments']:
						completeFrame += self.fragmentBuffer[frameNumber]['fragments'][i]
				
				# Verify reassembled frame
				if len(completeFrame) >= frameSize - 16:  # Allow some tolerance
					self.handleFrame(frameNumber, completeFrame)
					del self.fragmentBuffer[frameNumber]
				else:
					self.stats['frames_dropped'] += 1
					del self.fragmentBuffer[frameNumber]
			
			# Timeout old fragments (check less frequently - every 100 fragments)
			if self.stats['fragments_received'] % 100 == 0:
				current_time = time.time()
				timeout_frames = []
				for fn in list(self.fragmentBuffer.keys()):
					age = current_time - self.fragmentBuffer[fn]['timestamp']
					if age > self.fragmentTimeout:
						timeout_frames.append(fn)
				
				for fn in timeout_frames:
					del self.fragmentBuffer[fn]
					self.stats['frames_dropped'] += 1
					
		except Exception as e:
			self.stats['frames_dropped'] += 1
	
	def handleFrame(self, frameNumber, data):
		"""Process complete frame and add to buffer (keep in memory for speed)."""
		try:
			# Store frame with raw JPEG data (no disk I/O overhead)
			frame_info = {
				'frame_num': frameNumber,
				'data': data,  # Raw JPEG data stored directly in memory
				'timestamp': time.time()
			}
			
			# deque with maxlen auto-removes oldest frames when buffer is full
			self.frameBuffer.append(frame_info)
			self.stats['frames_received'] += 1
			self.frameNbr = frameNumber
			
		except Exception as e:
			print(f"Frame processing error: {e}")
	
	def cleanupOldCacheFiles(self, max_keep=50):
		"""Remove old in-memory frames (no disk I/O needed anymore)."""
		# No-op now - we use in-memory caching instead of disk files
		pass
	
	def displayFrames(self):
		"""Display frames from buffer with smooth playback."""
		print("[CLIENT] Starting playback with buffering...")
		
		while True:
			try:
				if self.playEvent.isSet():
					break
				
				# Buffering phase
				if self.buffering:
					if len(self.frameBuffer) >= self.bufferThreshold:
						self.buffering = False
						print(f"[BUFFER] Ready - {len(self.frameBuffer)} frames buffered")
					else:
						self.updateStatsLabel(f"Buffering: {len(self.frameBuffer)}/{self.bufferThreshold}")
						time.sleep(0.1)
						continue
				
				# Check buffer level
				if len(self.frameBuffer) < 2:
					self.buffering = True
					print("[BUFFER] Rebuffering...")
					continue
				
				# Display frame
				if len(self.frameBuffer) > 0:
					frame_info = self.frameBuffer.popleft()
					
					# Calculate latency
					latency = (time.time() - frame_info['timestamp']) * 1000
					self.stats['latency'].append(latency)
					if len(self.stats['latency']) > 100:
						self.stats['latency'].pop(0)
					
					self.updateMovie(frame_info['file'])
					# Remove cache file after display to avoid stale/overwritten files
					try:
						os.remove(frame_info['file'])
					except:
						pass
					self.updateStatsLabel()
					
					# Adaptive delay
					if len(self.frameBuffer) > 7:
						delay = 0.01
					elif len(self.frameBuffer) < 3:
						delay = 0.1
					else:
						delay = 0.05
					
					time.sleep(delay)
				else:
					time.sleep(0.05)
					
			except Exception as e:
				print(f"Display error: {e}")
				time.sleep(0.1)
	
	def updateMovie(self, jpegData):
		"""Update the image from JPEG data with fast scaling for smooth playback."""
		try:
			from io import BytesIO
			
			# Open image
			img = Image.open(BytesIO(jpegData))
			orig_width, orig_height = img.size
			
			# Get label dimensions (cache to avoid repeated winfo calls which are slow)
			label_width = self.label.winfo_width()
			label_height = self.label.winfo_height()
			
			# Use cached dimensions if not yet sized
			if label_width <= 1:
				label_width = self.lastLabelWidth if self.lastLabelWidth > 0 else 1280
			if label_height <= 1:
				label_height = self.lastLabelHeight if self.lastLabelHeight > 0 else 680
			else:
				# Cache successful label dimensions
				self.lastLabelWidth = label_width
				self.lastLabelHeight = label_height
			
			# Check if scaling is needed
			needs_scaling = orig_width > label_width or orig_height > label_height
			
			if needs_scaling:
				# Calculate scaling to fit within label while preserving aspect ratio
				scale = min(label_width / orig_width, label_height / orig_height)
				
				if scale < 1.0:
					new_width = int(orig_width * scale)
					new_height = int(orig_height * scale)
					# Use BILINEAR (fast) instead of LANCZOS for video playback
					img = img.resize((new_width, new_height), Image.BILINEAR)
			
			# Convert to PhotoImage and display (Tkinter update)
			photo = ImageTk.PhotoImage(img)
			self.label.configure(image=photo)
			self.label.image = photo  # Keep reference to prevent garbage collection
			
		except Exception as e:
			pass
	
	def updateStatsLabel(self, custom_msg=None):
		"""Update statistics display."""
		if custom_msg:
			self.statsLabel.config(text=custom_msg)
		else:
			elapsed = time.time() - self.stats['start_time'] if self.stats['start_time'] else 1
			fps = self.stats['frames_received'] / elapsed
			avg_latency = sum(self.stats['latency']) / len(self.stats['latency']) if self.stats['latency'] else 0
			
			stats_text = (f"Frame: {self.frameNbr} | FPS: {fps:.1f} | "
			             f"Latency: {avg_latency:.1f}ms | Buffer: {len(self.frameBuffer)} | "
			             f"Received: {self.stats['frames_received']} | Dropped: {self.stats['frames_dropped']}")
			
			self.statsLabel.config(text=stats_text)
	
	def connectToServer(self):
		"""Connect to the Server."""
		self.rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		try:
			self.rtspSocket.connect((self.serverAddr, self.serverPort))
		except:
			messagebox.showwarning('Connection Failed', 'Connection to server failed.')
	
	def sendRtspRequest(self, requestCode):
		"""Send RTSP request to the server."""
		if requestCode == self.SETUP and self.state == self.INIT:
			threading.Thread(target=self.recvRtspReply).start()
			self.rtspSeq += 1
			request = f"SETUP {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nTransport: RTP/UDP; client_port= {self.rtpPort}"
			self.requestSent = self.SETUP
			
		elif requestCode == self.PLAY and self.state == self.READY:
			self.rtspSeq += 1
			request = f"PLAY {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nSession: {self.sessionId}"
			self.requestSent = self.PLAY
			
		elif requestCode == self.PAUSE and self.state == self.PLAYING:
			self.rtspSeq += 1
			request = f"PAUSE {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nSession: {self.sessionId}"
			self.requestSent = self.PAUSE
			
		elif requestCode == self.TEARDOWN and not self.state == self.INIT:
			self.rtspSeq += 1
			request = f"TEARDOWN {self.fileName} RTSP/1.0\nCSeq: {self.rtspSeq}\nSession: {self.sessionId}"
			self.requestSent = self.TEARDOWN
		else:
			return
		
		self.rtspSocket.send(request.encode())
		print('\nData sent:\n' + request)
	
	def recvRtspReply(self):
		"""Receive RTSP reply from the server."""
		while True:
			reply = self.rtspSocket.recv(1024)
			
			if reply:
				self.parseRtspReply(reply.decode("utf-8"))
			
			if self.requestSent == self.TEARDOWN:
				self.rtspSocket.shutdown(socket.SHUT_RDWR)
				self.rtspSocket.close()
				break
	
	def parseRtspReply(self, data):
		"""Parse the RTSP reply from the server."""
		lines = data.split('\n')
		seqNum = int(lines[1].split(' ')[1])
		
		if seqNum == self.rtspSeq:
			session = int(lines[2].split(' ')[1])
			
			if self.sessionId == 0:
				self.sessionId = session
			
			if self.sessionId == session:
				if self.requestSent == self.SETUP:
					self.state = self.READY
					self.openRtpPort()
				elif self.requestSent == self.PLAY:
					self.state = self.PLAYING
				elif self.requestSent == self.PAUSE:
					self.state = self.READY
					self.playEvent.set()
				elif self.requestSent == self.TEARDOWN:
					self.state = self.INIT
					self.teardownAcked = 1
	
	def openRtpPort(self):
		"""Open RTP socket."""
		self.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
		# Optimize socket for high-speed streaming
		self.rtpSocket.settimeout(0.01)  # 10ms timeout (ultra-fast for 360 FPS)
		# Increase receive buffer for high-bandwidth streams
		try:
			self.rtpSocket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4*1024*1024)  # 4MB receive buffer for 360 FPS
		except:
			pass
		
		try:
			self.rtpSocket.bind(('', self.rtpPort))
		except:
			messagebox.showwarning('Unable to Bind', 'Unable to bind PORT=%d' % self.rtpPort)
	
	def handler(self):
		"""Handler on closing the GUI."""
		self.pauseMovie()
		if messagebox.askokcancel("Quit?", "Are you sure you want to quit?"):
			self.exitClient()
		else:
			self.playMovie()
	
	def printStats(self):
		"""Print final statistics with packet loss analysis."""
		if self.stats['start_time']:
			elapsed = time.time() - self.stats['start_time']
			fps = self.stats['frames_received'] / elapsed if elapsed > 0 else 0
			avg_latency = sum(self.stats['latency']) / len(self.stats['latency']) if self.stats['latency'] else 0
			loss_rate = (self.stats['frames_dropped'] / (self.stats['frames_received'] + self.stats['frames_dropped']) * 100) if (self.stats['frames_received'] + self.stats['frames_dropped']) > 0 else 0
			
			print("\n" + "="*70)
			print("CLIENT STATISTICS")
			print("="*70)
			print(f"Frames Received:      {self.stats['frames_received']}")
			print(f"Frames Dropped:       {self.stats['frames_dropped']}")
			print(f"Frame Loss Rate:      {loss_rate:.2f}%")
			print(f"Seq Num Gaps:         {self.seqNumGaps} (missing frames detected)")
			print(f"Fragments Received:   {self.stats['fragments_received']}")
			print(f"Average FPS:          {fps:.2f}")
			print(f"Average Latency:      {avg_latency:.2f}ms")
			print(f"Total Bytes:          {self.stats['bytes_received']:,}")
			print(f"Elapsed Time:         {elapsed:.1f}s")
			print("="*70 + "\n")
			
			# Recommendations based on loss
			if loss_rate > 5:
				print("[WARNING] High packet loss detected!")
				print("  → Reduce FRAME_RATE on server")
				print("  → Check network conditions (latency, bandwidth, jitter)")
				print("  → Increase MTU size on both client/server")
				print("  → Consider using TCP instead of UDP if possible")
			elif loss_rate > 1:
				print("[INFO] Minor packet loss detected. Consider optimizations.")