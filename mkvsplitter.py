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
import sys
import re
import os
import argparse
import subprocess
import time
import logging
import math

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
        ret.append({'chapter' : chapter, 'start' : toTime(start), 'end' : toTime(result.group(2)), 'total' : toTime(result.group(2)) - toTime(start)})
  return ret

ret = getChapterList(sys.argv[1])

skiplastchapter = True
cutting = []

# Generally episodes fall within 23min +/- 2min
showduration = 23*60000
while skiplastchapter:
  skiplastchapter = False
  duration = ret[len(ret)-1]['end']
  lastchap = ret[len(ret)-1]['total']

  # Calculate the length of an episode
  episodes = math.floor(duration / showduration)
  episode_length = duration / episodes
  print "Episodes: %d, %s long each" % (episodes, fromTime(episode_length))

  # If episodes exceed 26min it's most likely wrong, decrease by 1min and try again
  if episode_length > (26*60000):
    showduration -= 60000
    skiplastchapter = True
    continue

  # Find outliers and skip the last entry in the list if it's the shortest
  s = []
  for x in ret:
    s.append(x['total'])
  s.sort()
  if s[0] == ret[len(ret)-1]['total']:
    p = s[0] * 100 / s[1]
    print "Last episode is the shortest (%d), comparably %.2f%% of the next size" % (s[0], p)
    if p < 10:
      print "That's too much of an outlier, remove it"
      ret.pop()
      skiplastchapter = True
      continue

  # If not evenly dividable at this point, we need to manually split things
  if (len(ret) % episodes) > 0:
    print "The chapters (%d) don't divide evenly with the number of episodes (%d)" % (len(ret), episodes)

    notdone = True
    retries = 10
    adjusted = episode_length
    while notdone and retries > 0:
      retries -= 1
      print "Episodes: %d, %s long each" % (episodes, fromTime(adjusted))
      tc = 0
      ep = 0
      lf = 100
      notdone = False
      lastfactor = -1
      temp = []
      for i in ret:
        if tc == 0:
          ep += 1
          print "Episode %d:" % ep
          temp.append([])
        temp[ep-1].append(i["chapter"])
        print "  Chapter %2d: %d" % (i["chapter"], i['total'])
        tc += i['total']
        # If total time is equal or more than what we thing an episode should be,
        # make sure it's sensible
        if tc >= adjusted:
          exceed = tc - adjusted
          factor = exceed / adjusted * 100
          # Track delta between estimated length and excess
          if factor > 0 and factor < lf:
            lf = factor
          print "Exceeded with %d, factor of %d" % (exceed, factor)
          # If we exceed with more than 10% in size between this and estimated,
          # start checking if this is the norm (we allow for 5% diff)
          if factor > 10 or (lastfactor != -1 and abs(factor - lastfactor) > 5) : # This is bad, adjust and restart
            print "That's beyond the threshold, adjust and retry ==================================="
            notdone = True
            adjusted *= (100-lf)/100
            break
          lastfactor = factor
          tc = 0
      cutting = temp
  else:
    # Easy! Even split
    sliced = int(len(ret) / episodes)
    print "Easy, %d chapters per episode" % sliced
    for i in range(0,int(episodes)):
      cutting.append(range(i*sliced + 1, i*sliced + sliced + 1))

print repr(cutting)