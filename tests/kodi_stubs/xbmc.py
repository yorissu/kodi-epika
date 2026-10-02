
#
# kodi_stubs/xbmc.py – test stand-in for Kodi's xbmc module (only what the add-on uses).
#
# Records log lines (LOG) and builtins such as Container.Refresh (BUILTINS); BUILD_VERSION and
# PLAYER let tests pretend to be another Kodi version or a player state. The stream detail and
# Actor classes check argument types like the real Kodi, which raises TypeError on wrong types.
#

LOGDEBUG, LOGINFO, LOGWARNING, LOGERROR = 0, 1, 2, 3
ENGLISH_NAME = 1
LOG = []
BUILTINS = []
BUILD_VERSION = ['21.2 (21.2.0) Git:stub']
PLAYER = {'playing': True, 'subtitles': ['lt'], 'shown': None}


def log(msg, level=LOGDEBUG):
	assert isinstance(msg, str)
	LOG.append((level, msg))


def getInfoLabel(label):
	return BUILD_VERSION[0] if label == 'System.BuildVersion' else ''


def convertLanguage(code, fmt):
	return {'lt': 'Lithuanian', 'en': 'English', 'ru': 'Russian'}.get(code, '')


def executebuiltin(command, wait=False):
	BUILTINS.append(command)


class Actor:
	def __init__(self, name='', role='', order=-1, thumbnail=''):
		assert isinstance(name, str) and isinstance(role, str) and isinstance(order, int)
		self.name = name


class AudioStreamDetail:
	def __init__(self, channels=-1, codec='', language=''):
		assert isinstance(channels, int) and isinstance(codec, str) and isinstance(language, str)
		self.language = language


class SubtitleStreamDetail:
	def __init__(self, language=''):
		assert isinstance(language, str)
		self.language = language


class VideoStreamDetail:
	def __init__(self, width=0, height=0, aspect=0.0, duration=0, codec='', stereomode='', language='', hdrtype=''):
		assert isinstance(width, int) and isinstance(height, int) and isinstance(duration, int)


class Monitor:
	def waitForAbort(self, timeout=0):
		return False

	def abortRequested(self):
		return False


class Player:
	def isPlayingVideo(self):
		return PLAYER['playing']

	def getAvailableSubtitleStreams(self):
		return PLAYER['subtitles']

	def showSubtitles(self, visible):
		PLAYER['shown'] = visible
