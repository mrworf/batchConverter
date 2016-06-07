#!/usr/bin/env python
#
# converter.py
# Copyright (C) 2015 Henric Andersson (henric@sensenet.nu)
#
# Monitors a directory for new files and then applies transcoding using
# https://github.com/donmelton/video_transcoding toolkit.
#
# It's aimed at being completely automated and expects the end-user to
# place MKV files in the monitored folder. These are then cropped and
# converted and placed in a new folder. If it fails or the new file is
# larger than the original, it will retain the logs so you can see if
# somethings went wrong.
#
# Author is using it like so:
#
#   ./converter.py --delete-original \
#   --transcode-args "--add-audio all --copy-audio all --add-subtitle all" \
#   /mnt/storage/incoming/ /mnt/storage/converted/
#
# Future plans...
# Will be adding a simple webserver to show status of the converter,
# allowing the user to easily see that status of the tool.
#
# Requirements:
# - pyinotify module (monitor filesystem changes)
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
#
#################################################################################

import sys
import re
import os
import argparse
import subprocess
import time
import logging

from fcntl import fcntl, F_GETFL, F_SETFL
from os import O_NONBLOCK, read

class DetectMovies():
  supportedExt = [".mkv", ".mp4", ".mov", ".m4v", ".mpeg", ".mpg", ".m2ts"]
  reCrop = re.compile('transcode-video --crop ([0-9]+)\:([0-9]+)\:([0-9]+)\:([0-9]+) .+')
  reTime = re.compile('.* ([0-9\.]+) \% .* ETA ([0-9]{2})h([0-9]{2})m([0-9]{2})s')
  reExt = re.compile('(.+)\.[^\.]+?$')

  def setAutoDeinterlace(self, ad):
    self.autodeint = ad

  def setLogging(self, logging):
    self.log = logging

  def setDeleteOriginal(self, delete):
    self.delete = delete

  def setOutputFolder(self, folder):
    self.output = folder
    self.blacklist = []

  def setArguments(self, args):
    if args:
      self.extra = args.split()
    else:
      self.extra = []

  def detectInterlace(self, filename, sensitivity=100, skip=120):
    reFrames = re.compile('TFF: +([0-9]+) BFF: +([0-9]+) Progressive: +([0-9]+)')

    cmd = ["ffmpeg", '-filter:v', 'idet', '-ss', str(skip), '-frames:v', str(sensitivity), '-an', '-f', 'rawvideo', '-y', '/dev/null', '-i', filename]
    p = subprocess.Popen(cmd, stdin=open(os.devnull), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    interlace = 0
    progressive = 0
    ret = []
    while True:
      s = p.stdout.readline()
      if s == '' and p.poll() != None:
        break
      s = str(s).strip()
      if s.startswith('[Parsed_idet_0 @ '):
        reg = reFrames.search(s)
        if reg:
          interlace += (int(reg.group(1)) + int(reg.group(2)))
          progressive += int(reg.group(3))

    return interlace > 0


  def detectCropping(self, filename):
    cmd = ["detect-crop", filename]
    p = subprocess.Popen(cmd, stdin=open(os.devnull), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    self.log.info('Inspecting "%s"' % filename)
    self.status = "Inspecting"
    self.src_file = filename
    self.dst_file = None

    crops = []
    while True:
      s = p.stdout.readline()
      if s == '' and p.poll() != None:
        break
      s = str(s).strip()
      m = self.reCrop.match(s)
      if m:
        crops.append({
          "t" : int(m.group(1)),
          "b" : int(m.group(2)),
          "l" : int(m.group(3)),
          "r" : int(m.group(4))
        })
    if len(crops) > 1:
      self.log.info("Multiple crop options detected")
      # Take a decision on the crop
      # First, no mix of l/r and t/b
      lr = False
      tb = False
      crop = {"t" : 4000, "b" : 4000, "l" : 4000, "r" : 4000}
      for c in crops:
        if c["t"] or c["b"]: tb = True
        if c["l"] or c["r"]: lr = True
        if c["t"] < crop["t"]: crop["t"] = c['t']
        if c["b"] < crop["b"]: crop["b"] = c['b']
        if c["l"] < crop["l"]: crop["l"] = c['l']
        if c["r"] < crop["r"]: crop["r"] = c['r']
      if lr and tb:
        self.log.warning("Either crop top-bottom or left-right ... Confused, ignoring crop")
        crop = {"t" : 0, "b" : 0, "l" : 0, "r" : 0}
    elif len(crops) == 1:
      crop = crops[0]
    else:
      crop = {"t" : 0, "b" : 0, "l" : 0, "r" : 0}
    crop = "%d:%d:%d:%d" % (crop["t"], crop["b"], crop["l"], crop["r"])
    self.log.info("Final crop selection is " + crop)
    return crop

  def transcodeVideo(self, filename, crop):
    m = self.reExt.match(os.path.join(self.output, os.path.basename(filename)))
    destfile = m.group(1) + ".mkv"

    cmd = ["transcode-video", "--crop", crop]
    cmd += self.extra

    # If auto deinterlace is enabled and we found it to trigger, then add correct
    # parameters to the call.
    if self.autodeint and self.detectInterlace(filename):
      self.log.info('Movie requires deinterlace, this will take longer')
      cmd += ['--filter', 'detelecine']

    cmd += [filename]
    cmd += ["-o", destfile]

    p = subprocess.Popen(cmd, bufsize=0, stdin=open(os.devnull), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    results = ""

    self.log.info('Transcoding "%s"' % filename)
    self.log.debug('using ' + repr(cmd))
    self.status = "Transcoding"
    self.progress = "0.00"
    self.src_file = filename
    self.dst_file = destfile

    # Avoid looping
    self.blacklist.append(destfile)

    result = ""
    while p.returncode is None:
      p.poll()
      buf = p.stdout.read(1024)
      res = self.reTime.findall(buf)
      result += buf
      if res:
        res = res[len(res)-1]
        status = "Transcoding, remaining %s:%s:%s" % (res[1], res[2], res[3])
        self.progress = res[0]
        if status != self.status:
          self.status = status
          self.log.info(self.status)
    if p.returncode != 0:
      self.log.error("Transcoding failed (%d), check logfile \"%s.log\"" % (p.returncode, destfile))
      print result
    else:
      self.log.info("Transcoding completed \"%s\"" % destfile)
      ssize = os.path.getsize(filename)
      dsize = os.path.getsize(destfile)
      delta = ssize - dsize
      if delta < 0:
        # If it grows, keep the original and the log
        self.log.warning("File grew %d bytes (%.2f%%)" % (-delta, float(-delta) / ssize * 100.0))
      else:
        self.log.info("File shrunk %d bytes (%.2f%%)" % (delta, float(delta) / ssize * 100.0))
        if self.delete:
          try:
            os.unlink(filename)
          except:
            pass
        # Don't keep logs around, since this was a success
        try:
          os.unlink(destfile + ".log")
        except:
          pass
    self.status = "Idle"
    self.progress = None
    self.src_file = None
    self.dst_file = None

  def process(self, filename):
    found = False
    for ext in self.supportedExt:
      if filename.lower().endswith(ext):
        found = True
        break
    if not found:
      return
    if filename in self.blacklist:
      return

    crop = self.detectCropping(filename)
    if not crop:
      return
    result = self.transcodeVideo(filename, crop)


parser = argparse.ArgumentParser(description="batchConverter - Convert videos using transcode-video as they are available", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument('input', help="Which folder to monitor for videos")
parser.add_argument('output', help='Where to place converted movies')
parser.add_argument('-ad', '--auto-deinterlace', default=False, action='store_true', help="Automatically determine if deinterlace is needed")
parser.add_argument('-ta', '--transcode-args', default="", help='Additional arguments for transcode-video')
parser.add_argument('-do', '--delete-original', action='store_true', default=False, help='Delete the original after transcoding')
cmdline = parser.parse_args()

logging.getLogger('').handlers = []
loglevel=logging.DEBUG
logformat='%(asctime)s - %(levelname)s - %(message)s'
logging.basicConfig(stream=sys.stdout, level=loglevel, format=logformat)

logging.info("Additional arguments: " + cmdline.transcode_args)
logging.info("Starting monitoring")

dm = DetectMovies()
dm.setOutputFolder(cmdline.output)
dm.setLogging(logging)
dm.setArguments(cmdline.transcode_args)
dm.setDeleteOriginal(cmdline.delete_original)
dm.setAutoDeinterlace(cmdline.auto_deinterlace)

# Start main loop
lstFiles = {}
lstProcessed = []
while True:
	time.sleep(1)
	files = os.listdir(cmdline.input)
	d = []
	# Figure out which files have disappeared so we don't track them
	for f in lstFiles:
		if f not in files:
			d.append(f)
	# Remove them from tracking
	for f in d:
		if f in lstProcessed:
			lstProcessed.remove(f)
		del lstFiles[f]
	# Process newcomers and old favorites
	for f in files:
		size = os.path.getsize(cmdline.input + '/' + f)
		if not os.path.isfile(cmdline.input + '/' + f):
			continue

		if f in lstFiles and lstFiles[f] == size and f not in lstProcessed:
			# Transcode it
			print "File %s didn't change, process it!" % f
			dm.process(cmdline.input + '/' + f)
			lstProcessed.append(f)
			break
		else:
			# Just track the change
			lstFiles[f] = size

