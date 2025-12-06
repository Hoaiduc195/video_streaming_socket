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
	BUFFER_SIZE = 20  # Pre-buffer N frames for smooth playback (increased for higher FPS)
	
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
		
		# Frame buffer for jitter reduction
		self.frameBuffer = deque(maxlen=100)
		self.bufferThreshold = self.BUFFER_SIZE
		self.buffering = False
		
		# Fragment reassembly buffer
		self.fragmentBuffer = {}
		
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
		# Configure window size and resizability
		self.master.geometry("1024x768")
		self.master.minsize(800, 600)
		self.master.title("RTP Video Streaming Client")
		
		# Configure grid weights for resizing
		self.master.grid_rowconfigure(0, weight=1)
		self.master.grid_columnconfigure(0, weight=1)
		
		# Video display label (main area)
		self.label = Label(self.master, bg="black", height=25, width=80)
		self.label.grid(row=0, column=0, columnspan=4, sticky=W+E+N+S, padx=5, pady=5)
		self.master.grid_rowconfigure(0, weight=1)
		
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
		"""Teardown button handler."""
		self.sendRtspRequest(self.TEARDOWN)
		self.printStats()
		self.master.destroy()
		
		# Cleanup any per-session cache files
		try:
			pattern = f"{CACHE_FILE_NAME}{self.sessionId}-*{CACHE_FILE_EXT}"
			for f in glob.glob(pattern):
				try:
					os.remove(f)
				except:
					pass
		except:
			pass
	
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
				return
			
				# Buffering phase (more aggressive buffering for higher FPS)
				if self.buffering:
					if len(self.frameBuffer) >= self.bufferThreshold:
						self.buffering = False
						print(f"[BUFFER] Ready - {len(self.frameBuffer)} frames buffered")
					else:
						self.updateStatsLabel(f"Buffering: {len(self.frameBuffer)}/{self.bufferThreshold}")
						self.master.after(50, self.displayFramesScheduled)  # Faster buffer check
						return
				
				# Check buffer level (lower threshold for continuous playback)
				if len(self.frameBuffer) < 5:
					self.buffering = True
					print("[BUFFER] Rebuffering...")
					self.master.after(50, self.displayFramesScheduled)
					return			# Display frame
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
				
				# Adaptive delay (shorter delays for higher FPS)
				if len(self.frameBuffer) > 15:
					delay = 5   # Very smooth when buffer full
				elif len(self.frameBuffer) > 10:
					delay = 10  # Smooth playback
				elif len(self.frameBuffer) < 5:
					delay = 30  # Light buffering
				else:
					delay = 15  # Normal playback
				
				self.master.after(delay, self.displayFramesScheduled)
			else:
				self.master.after(50, self.displayFramesScheduled)
				
		except Exception as e:
			print(f"Display error: {e}")
			self.master.after(50, self.displayFramesScheduled)

	
	def listenRtp(self):
		"""Listen for RTP packets with fragmentation support."""
		print("[CLIENT] Listening for RTP packets...")
		
		while True:
			try:
				data = self.rtpSocket.recv(65536)  # Larger receive buffer for more frames per read
				
				if data:
					rtpPacket = RtpPacket()
					rtpPacket.decode(data)
					
					self.stats['bytes_received'] += len(data)
					payload = rtpPacket.getPayload()
					
					# Check if fragmented
					if len(payload) > 6 and self.isFragmented(payload):
						self.handleFragment(rtpPacket, payload)
					else:
						# Regular frame
						self.handleFrame(rtpPacket.seqNum(), payload)
					
			except Exception as e:
				if self.playEvent.isSet():
					break
	
	def isFragmented(self, payload):
		"""Check if payload contains fragmentation header."""
		try:
			fragNum, numFrags, frameSize = struct.unpack('!HHH', payload[:6])
			return numFrags > 1
		except:
			return False
	
	def handleFragment(self, rtpPacket, payload):
		"""Handle fragmented frame."""
		try:
			# Extract fragmentation header
			fragNum, numFragments, frameSize = struct.unpack('!HHH', payload[:6])
			fragmentData = payload[6:]
			
			frameNumber = rtpPacket.seqNum()
			marker = rtpPacket.getMarker()
			
			self.stats['fragments_received'] += 1
			
			# Initialize buffer for this frame
			if frameNumber not in self.fragmentBuffer:
				self.fragmentBuffer[frameNumber] = {
					'fragments': {},
					'total': numFragments,
					'size': frameSize,
					'timestamp': time.time()
				}
			
			# Store fragment
			self.fragmentBuffer[frameNumber]['fragments'][fragNum] = fragmentData
			
			# Check if complete
			if len(self.fragmentBuffer[frameNumber]['fragments']) == numFragments:
				# Reassemble
				completeFrame = b''
				for i in range(numFragments):
					if i in self.fragmentBuffer[frameNumber]['fragments']:
						completeFrame += self.fragmentBuffer[frameNumber]['fragments'][i]
				
				# Process complete frame
				self.handleFrame(frameNumber, completeFrame)
				print(f"[REASSEMBLE] Frame {frameNumber}: {numFragments} fragments")
				
				# Cleanup
				del self.fragmentBuffer[frameNumber]
			
			# Timeout old fragments
			current_time = time.time()
			for fn in list(self.fragmentBuffer.keys()):
				if current_time - self.fragmentBuffer[fn]['timestamp'] > 5.0:
					del self.fragmentBuffer[fn]
					self.stats['frames_dropped'] += 1
					
		except Exception as e:
			print(f"Fragment error: {e}")
			self.stats['frames_dropped'] += 1
	
	def handleFrame(self, frameNumber, data):
		"""Process complete frame and add to buffer."""
		try:
			# Write each frame to a unique cache file atomically to avoid
			# the reader seeing a partially-written file (causes visual glitches).
			filename = f"{CACHE_FILE_NAME}{self.sessionId}-{frameNumber}{CACHE_FILE_EXT}"
			tempname = filename + ".tmp"
			with open(tempname, "wb") as f:
				f.write(data)
			# Atomic replace (works on Windows and POSIX)
			try:
				os.replace(tempname, filename)
			except Exception:
				# Fallback: try to clean temp and write final file
				try:
					os.remove(tempname)
				except:
					pass
				with open(filename, "wb") as f:
					f.write(data)
			
			frame_info = {
				'frame_num': frameNumber,
				'file': filename,
				'timestamp': time.time()
			}
			
			self.frameBuffer.append(frame_info)
			self.stats['frames_received'] += 1
			self.frameNbr = frameNumber
			
			# Cleanup old cache files periodically (keep only 50 newest)
			if frameNumber % 50 == 0:
				self.cleanupOldCacheFiles(max_keep=50)
			
		except Exception as e:
			print(f"Frame processing error: {e}")
	
	def cleanupOldCacheFiles(self, max_keep=50):
		"""Remove old cache files, keeping only the newest N files."""
		try:
			pattern = f"{CACHE_FILE_NAME}{self.sessionId}-*{CACHE_FILE_EXT}"
			files = sorted(glob.glob(pattern), key=os.path.getctime)
			
			# Keep only the newest max_keep files
			if len(files) > max_keep:
				files_to_delete = files[:-max_keep]
				for f in files_to_delete:
					try:
						os.remove(f)
					except:
						pass
				print(f"[CACHE] Cleaned up {len(files_to_delete)} old cache files (kept {max_keep} newest)")
		except Exception as e:
			print(f"Cache cleanup error: {e}")
	
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
	
	def updateMovie(self, imageFile):
		"""Update the image file as video frame."""
		try:
			photo = ImageTk.PhotoImage(Image.open(imageFile))	
			self.label.configure(image=photo, height=288)
			self.label.image = photo
		except:
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
		self.rtpSocket.settimeout(0.1)  # Shorter timeout for faster packet handling
		# Increase receive buffer for high-bandwidth streams
		try:
			self.rtpSocket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024*1024)  # 1MB buffer
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
		"""Print final statistics."""
		if self.stats['start_time']:
			elapsed = time.time() - self.stats['start_time']
			fps = self.stats['frames_received'] / elapsed
			avg_latency = sum(self.stats['latency']) / len(self.stats['latency']) if self.stats['latency'] else 0
			
			print("\n" + "="*60)
			print("CLIENT STATISTICS")
			print("="*60)
			print(f"Frames Received:  {self.stats['frames_received']}")
			print(f"Frames Dropped:   {self.stats['frames_dropped']}")
			print(f"Fragments:        {self.stats['fragments_received']}")
			print(f"Average FPS:      {fps:.2f}")
			print(f"Average Latency:  {avg_latency:.2f}ms")
			print(f"Total Bytes:      {self.stats['bytes_received']:,}")
			print("="*60 + "\n")