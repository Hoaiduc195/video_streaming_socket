from random import randint
import sys, traceback, threading, socket
import time
import struct
import queue

from VideoStream import VideoStream
from RtpPacket import RtpPacket

class ServerWorker:
	SETUP = 'SETUP'
	PLAY = 'PLAY'
	PAUSE = 'PAUSE'
	TEARDOWN = 'TEARDOWN'
	
	INIT = 0
	READY = 1
	PLAYING = 2
	state = INIT

	OK_200 = 0
	FILE_NOT_FOUND_404 = 1
	CON_ERR_500 = 2
	
	# HD Configuration
	MTU = 2000  # Increased to 16KB for better throughput and reduced fragmentation
	
	# Frame rate control (change this to adjust playback speed)
	# Set to None for natural video speed (from source file)
	# Set to integer for fixed FPS (e.g., 30, 60, 120)
	TARGET_FPS = 30  # None = natural speed, or set to 30, 60, 120, etc.
	
	clientInfo = {}
	
	def __init__(self, clientInfo):
		self.clientInfo = clientInfo
		
		# Statistics for network analysis
		self.stats = {
			'frames_sent': 0,
			'frames_lost': 0,
			'bytes_sent': 0,
			'fragments_sent': 0,
			'start_time': None
		}

		# Prefetch queue and control for parallel frame reads
		# Bounded to avoid uncontrolled memory growth
		self.frame_queue = queue.Queue(maxsize=50)
		self._prefetch_thread = None
		self._stop_prefetch = threading.Event()
		
	def run(self):
		threading.Thread(target=self.recvRtspRequest).start()
	
	def recvRtspRequest(self):
		"""Receive RTSP request from the client."""
		connSocket = self.clientInfo['rtspSocket'][0]
		while True:            
			data = connSocket.recv(256)
			if data:
				print("Data received:\n" + data.decode("utf-8"))
				self.processRtspRequest(data.decode("utf-8"))
	
	def processRtspRequest(self, data):
		"""Process RTSP request sent from the client."""
		request = data.split('\n')
		line1 = request[0].split(' ')
		requestType = line1[0]
		
		filename = line1[1]
		seq = request[1].split(' ')
		
		# Process SETUP request
		if requestType == self.SETUP:
			if self.state == self.INIT:
				print("processing SETUP\n")
				
				try:
					self.clientInfo['videoStream'] = VideoStream(filename)
					self.state = self.READY
				except IOError:
					self.replyRtsp(self.FILE_NOT_FOUND_404, seq[1])
				
				self.clientInfo['session'] = randint(100000, 999999)
				self.replyRtsp(self.OK_200, seq[1])
				self.clientInfo['rtpPort'] = request[2].split(' ')[3]
		
		# Process PLAY request 		
		elif requestType == self.PLAY:
			if self.state == self.READY:
				print("processing PLAY\n")
				self.state = self.PLAYING
				
				# Create a new socket for RTP/UDP
				self.clientInfo["rtpSocket"] = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
				
				# Increase buffer sizes for high-speed HD streaming
				self.clientInfo["rtpSocket"].setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 16*1024*1024)  # 16MB send buffer for 360 FPS
				# Enable QoS (Quality of Service) for prioritized video delivery
				try:
					self.clientInfo["rtpSocket"].setsockopt(socket.IPPROTO_IP, socket.IP_TOS, 0x88)
				except:
					pass
				
				self.replyRtsp(self.OK_200, seq[1])
				
				# Start statistics
				self.stats['start_time'] = time.time()
				
				# Create control event for playback and start prefetch + sender threads
				self.clientInfo['event'] = threading.Event()
				# Recreate/clear prefetch queue and start prefetch thread to read frames in parallel
				self.frame_queue = queue.Queue(maxsize=50)
				self._stop_prefetch.clear()
				self._prefetch_thread = threading.Thread(target=self._prefetch_frames, daemon=True)
				self._prefetch_thread.start()
				# Start RTP sender thread
				self.clientInfo['worker']= threading.Thread(target=self.sendRtp)
				self.clientInfo['worker'].start()
		
		# Process PAUSE request
		elif requestType == self.PAUSE:
			if self.state == self.PLAYING:
				print("processing PAUSE\n")
				self.state = self.READY
				# Signal threads to stop reading/sending
				self.clientInfo['event'].set()
				self._stop_prefetch.set()
				self.replyRtsp(self.OK_200, seq[1])
		
		# Process TEARDOWN request
		elif requestType == self.TEARDOWN:
			print("processing TEARDOWN\n")
			# Signal threads to stop
			self.clientInfo['event'].set()
			self._stop_prefetch.set()
			self.replyRtsp(self.OK_200, seq[1])
			
			
			# Close the RTP socket
			self.clientInfo['rtpSocket'].close()
			# Join prefetch thread (short timeout) to release resources
			try:
				if self._prefetch_thread and self._prefetch_thread.is_alive():
					self._prefetch_thread.join(timeout=0.2)
			except:
				pass
			
	def sendRtp(self):
		"""Send RTP packets over UDP with HD support."""
		# Calculate consumer pacing based on TARGET_FPS
		if self.TARGET_FPS is None:
			consumer_delay = 0.02
		else:
			consumer_delay = 1.0 / self.TARGET_FPS

		while True:
			# Wait for control event to exist
			if self.clientInfo.get('event') is None:
				time.sleep(0.001)
				continue

			# Stop sending if request is PAUSE or TEARDOWN
			if self.clientInfo['event'].isSet():
				break

			# Prefer prefetched frames (producer-consumer)
			data = None
			try:
				data = self.frame_queue.get(timeout=0.01)
			except queue.Empty:
				# Fallback: direct read (if prefetcher can't fill queue)
				try:
					data = self.clientInfo['videoStream'].nextFrame()
				except:
					data = None

			if data:
				frameNumber = self.clientInfo['videoStream'].frameNbr()
				try:
					address = self.clientInfo['rtspSocket'][1][0]
					port = int(self.clientInfo['rtpPort'])

					# Check if frame needs fragmentation (HD frames)
					if len(data) > self.MTU:
						self.sendFragmented(data, frameNumber, address, port)
					else:
						# Send regular frame
						packet = self.makeRtp(data, frameNumber, marker=1)
						self.clientInfo['rtpSocket'].sendto(packet, (address, port))
						self.stats['frames_sent'] += 1
						self.stats['bytes_sent'] += len(packet)

				except Exception:
					print("Connection Error")
					self.stats['frames_lost'] += 1

			# Pace consumer if fixed TARGET_FPS is set
			if consumer_delay > 0:
				time.sleep(consumer_delay)

	def sendFragmented(self, data, frameNumber, address, port):
		"""Fragment and send large frames exceeding MTU."""
		try:
			frameSize = len(data)
			numFragments = (frameSize + self.MTU - 1) // self.MTU
			
			for fragNum in range(numFragments):
				# Calculate fragment boundaries
				offset = fragNum * self.MTU
				fragmentSize = min(self.MTU, frameSize - offset)
				fragmentData = data[offset:offset + fragmentSize]
				
				# Create fragmentation header (6 bytes)
				# Format: fragment_num (2), total_fragments (2), frame_size (2)
				fragHeader = struct.pack('!HHH', 
					fragNum,
					numFragments,
					frameSize & 0xFFFF  # Use lower 16 bits
				)
				
				# Combine header + data
				payload = fragHeader + fragmentData
				
				# Marker bit = 1 only for last fragment
				marker = 1 if (fragNum == numFragments - 1) else 0
				
				# Create and send RTP packet
				packet = self.makeRtp(payload, frameNumber, marker)
				self.clientInfo['rtpSocket'].sendto(packet, (address, port))
				
				self.stats['fragments_sent'] += 1
				self.stats['bytes_sent'] += len(packet)
			
			self.stats['frames_sent'] += 1
			
		except Exception as e:
			print(f"Fragmentation error: {e}")
			self.stats['frames_lost'] += 1

	def makeRtp(self, payload, frameNbr, marker=0):
		"""RTP-packetize the video data."""
		version = 2
		padding = 0
		extension = 0
		cc = 0
		pt = 26  # MJPEG type
		seqnum = frameNbr
		ssrc = 0 
		
		rtpPacket = RtpPacket()
		rtpPacket.encode(version, padding, extension, cc, seqnum, marker, pt, ssrc, payload)
		
		return rtpPacket.getPacket()

	def _prefetch_frames(self):
		"""Background thread that reads frames from VideoStream into a bounded queue."""
		vs = self.clientInfo.get('videoStream')
		if vs is None:
			return
		while not self._stop_prefetch.is_set() and not self.clientInfo.get('event', threading.Event()).isSet():
			# If queue is full, wait briefly
			if self.frame_queue.full():
				time.sleep(0.002)
				continue
			# Read next frame (may block on file I/O)
			try:
				frame = vs.nextFrame()
			except Exception:
				frame = None
			if not frame:
				# End of stream or read error - small pause and retry
				time.sleep(0.01)
				continue
			# Put frame into queue (skip if full)
			try:
				self.frame_queue.put(frame, timeout=0.01)
			except queue.Full:
				pass
		
	def replyRtsp(self, code, seq):
		"""Send RTSP reply to the client."""
		if code == self.OK_200:
			reply = 'RTSP/1.0 200 OK\nCSeq: ' + seq + '\nSession: ' + str(self.clientInfo['session'])
			connSocket = self.clientInfo['rtspSocket'][0]
			connSocket.send(reply.encode())
		
		elif code == self.FILE_NOT_FOUND_404:
			print("404 NOT FOUND")
		elif code == self.CON_ERR_500:
			print("500 CONNECTION ERROR")
	