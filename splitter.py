#!/usr/bin/env python
#
# A hack which takes MKV files from MakeMKV that were created
# from Anime DVDs and try to split them into individual epsisodes.
#
# This tool may work for other kinds of TV series, but no promises.
# If your MKV file lacks chapter info, this tool will not function properly.
#
# A quick breakdown of how it works:
# 1. Figure out chapters, lenght of them and total
# 2. Remove outliers (chapters whose length just doesn't make sense)
# 3. Split remaining chapters evenly, if possible, we're done!
# 4. If not evenly split, start iterating
# 5. Add chapters and split when time exceeds episode duration (an estimation)
# 6. If episodes differ wildly in length, assume the duration is wrong and adjust
# 7. Repeat steps 5 & 6 until we don't hit the limits of #6
#
# Once the tool has decided it's manage to split the content, it will cut
# the original file into separate episode files.
#
# It will NEVER delete the original file, in-case something went wrong
#
# TODO: Add arguments for duration, splitting naming and better logging
#
# REQUIRES: mkvinfo and mkvmerge in the path to function
#
# It is HIGHLY recommended to run the tool once without --doit and confirm
# that it will do what you want it to (ie, get the right number of episodes, etc)
#
import sys
import re
import os
import argparse
import subprocess
import time
import logging
import math
import shutil

# Parse input
parser = argparse.ArgumentParser(description="MKVsplitter - Making Anime backup easier", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument('--logfile', metavar="FILE", help="Log to file instead of stdout")
parser.add_argument('--duration', metavar="MINUTES", type=int, default=23, help="Change the default duration of an episode")
parser.add_argument('--delta', metavar="MINUTES", type=int, default=1, help="Minimum delta detecting short/long duration")
parser.add_argument('--debug', action='store_true', default=False, help="Enable debugging (more output)")
parser.add_argument('--dryrun', action='store_true', default=False, help='Don\'t actually do it, just show me what would happen')
parser.add_argument('file', metavar="FILE", help="Which file to split")
parser.add_argument('output', metavar="OUTPUT", help="Where to save split file")
cmdline = parser.parse_args()

# Setup logging so we can use it
level = logging.INFO
if cmdline.debug:
    level = logging.DEBUG
logging.getLogger('').handlers = []
logging.basicConfig(filename=cmdline.logfile, level=level, format='%(filename)s@%(lineno)d - %(levelname)s - %(message)s')

def toTime(input):
  reTime = re.compile('([0-9]{2}):([0-9]{2}):([0-9]{2})\.([0-9]{3})')
  result = reTime.search(input)
  t = 0
  if result:
    t += int(result.group(1)) * 1000 * 60 * 60
    t += int(result.group(2)) * 1000 * 60
    t += int(result.group(3)) * 1000
    t += int(result.group(4))
  return t

def fromTime(t):
  h = t / 1000 / 60 / 60
  m = t / 60000 % 60
  s = t / 1000 % 60
  ms = t % 1000
  return "%02d:%02d:%02d.%03d" % (h, m, s, ms)

def getChapterList(filename):
  reChapter = re.compile('ChapterTime(Start|End): ([0-9\:\.]+)')

  cmd = ["mkvinfo", filename]
  p = subprocess.Popen(cmd, stdin=open(os.devnull), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

  start = None
  chapter = 0
  ret = []
  while True:
    s = p.stdout.readline()
    if s == '' and p.poll() != None:
      break
    s = str(s).strip()

    result = reChapter.search(s)
    if result:
      if result.group(1) == "Start":
        start = result.group(2)
      elif result.group(1) == "End" and start is not None:
        chapter += 1
        ret.append({'chapter' : chapter, 'start' : toTime(start), 'end' : toTime(result.group(2)), 'duration' : toTime(result.group(2)) - toTime(start)})
  return ret

chapters = getChapterList(cmdline.file)
originalChapterCount = len(chapters)

# Remove insanely small chapters
temp = []
for chapter in chapters:
  if chapter['duration'] < 5000:
    continue
  temp.append(chapter)
chapters = temp

forms = []
forms.append('short:long')
forms.append('short:long:long')
forms.append('short:long:long:short') 
forms.append('short:short:long:long:short')
forms.append('short:long:long:short:short')
forms.append('long:long')

file_duration = chapters[len(chapters)-1]['end']
show_duration = cmdline.duration*60000 # in ms
averageEpisodeCount = int(round(float(file_duration) / float(show_duration)))

logging.debug('Number of episodes based on file duration and show duration: %d' % averageEpisodeCount)

# Next, iterate through and figure out long vs short
durations = []
for entry in chapters:
  durations.append(entry['duration'])
durations.sort()
delta = durations[len(durations)-1] - durations[0]
longDuration = cmdline.duration*20000

logging.debug('Differance between shortest and longest: %s' % fromTime(delta))
hasShort = True
hasLong = True
if delta > cmdline.delta*60000:
  logging.debug('File has short and long chapters')
else:
  logging.debug('All chapters are considered of equal length')
  # Figure out if they're all short or long (must be 33% or more of the total duration)
  if durations[len(durations)-1] >= longDuration:
    hasShort = False
    hasLong = True
  else:
    hasShort = True
    hasLong = False

# Depending on presence of short, filter the format list
filtered = []
for form in forms:
  if 'short' in form and not hasShort:
    continue
  if 'long' in form and not hasLong:
    continue
  filtered.append(form)

# Convert chapterlist into short/long entries
abstract = []
logging.info('%d chapters in file' % len(chapters))
logging.debug('%s is the limit for a long chapter' % fromTime(longDuration))
for chapter in chapters:
  if chapter['duration'] < longDuration:
    logging.debug('Short: %s' % fromTime(chapter['duration']))
    abstract.append('short')
  else:
    logging.debug(' Long: %s' % fromTime(chapter['duration']))
    abstract.append('long')

desc = ':'.join(abstract)

logging.debug('Abstract definition of file: %s' % desc)

count = 0
cutting = []
while desc != '':
  longest = 0
  for form in filtered:
    if len(form) > longest and desc.startswith(form):
      longest = len(form)
  if longest == 0:
    if len(desc.split(':')) < 2:
      duration = chapters[count]['duration']
      if duration > 120000:
        logging.error('Duration of remaining chapter is %s, which might mean we mismatched the splits' % fromTime(duration))
        sys.exit(255)
      else:
        logging.warning('One chapters left and it\'s only %s long, most likely not an episode' % fromTime(duration))
      break
    else:
      logging.error('Fatal error, there\'s more than one chapter in the remains')
      sys.exit(255)
  logging.debug('Episode layout: %s' % desc[:longest])
  parts = len(desc[:longest].split(':'))
  cutting.append({'start' : count+1, 'end' : count+parts})
  count += parts 
  desc = desc[longest+1:]

logging.info('Found %d episodes' % len(cutting))
logging.debug('Final episode count is %d, compared to %d if using duration as indicator' % (len(cutting), averageEpisodeCount))

# Figure out if we need to cut at all
if len(cutting) == 1 and cutting[0]['start'] == 1 and cutting[0]['end'] == originalChapterCount:
  logging.info('This file holds ONE episode without any excess chapters, no need to split')
else:
  logging.debug('Splitting according to the following cutting list')
  logging.debug(repr(cutting))
  # Create mkvmerge chapter splitter
  cuts = []
  for cut in cutting:
    if cut['start'] != 1:
      cuts.append(cut['start'])
  # Make sure any junk chapters at the end is split off too
  removeLast = False
  removeFirst = False
  if cutting[len(cutting)-1]['end'] != originalChapterCount:
    cuts.append(cutting[len(cutting)-1]['end']+1)
    logging.debug('Last episode is a junk episode from excess chapters in file')
    removeLast = True
  if cutting[0]['start'] != 1:
    logging.debug('First episode is a junk episode')
    removeFirst = True
  logging.debug('MKVmerge split right before chapters: %s' % repr(cuts))

  # Build arguments
  basename = os.path.splitext(os.path.basename(cmdline.file))[0]
  basename = os.path.join(cmdline.output, basename) + "-%02d.mkv"
  args = ['mkvmerge', '-o', basename, cmdline.file, '--split', 'chapters:'+','.join(str(x) for x in cuts)]
  if cmdline.dryrun:
    logging.info('Would execute the following:')
    logging.info(' %s' % ' '.join(args))
  else:
    p = subprocess.Popen(args, stdin=open(os.devnull), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    file = 0
    lastprogress = 'Progress: 0%'
    while True:
      s = p.stdout.readline()
      if s == '' and p.poll() != None:
        break
      s = str(s).strip()
      if 'opened for writing' in s:
        if file < len(cutting):
          file += 1
        sys.stderr.write('\r' + lastprogress + ' - File %d of %d' % (file, len(cutting)))
        sys.stderr.flush()
      if 'Progress' in s:
        lastprogress = s
        sys.stderr.write('\r' + lastprogress + ' - File %d of %d' % (file, len(cutting)))
        sys.stderr.flush()
    sys.stderr.write('\n')
    sys.stderr.flush()

    # Now, just remove any potential junk file
    if removeFirst:
      os.remove(basename % 1)
    if removeLast:
      os.remove(basename % (len(cutting)+1))