
#
# kodi_stubs/xbmcgui.py – test stand-in for Kodi's xbmcgui module (only what the add-on uses).
#
# ListItem and its InfoTag keep everything set on them for the tests to inspect, and the InfoTag
# setters reject wrong types exactly like Kodi (setYear needs an int...). Dialogs don't show
# anything: their answers come from ANSWERS, and what they displayed goes to NOTES and PROGRESS.
#

NOTIFICATION_INFO, NOTIFICATION_WARNING, NOTIFICATION_ERROR = 'info', 'warning', 'error'
INPUT_ALPHANUM = 0
NOTES = []
ANSWERS = {'yesno': False, 'input': '', 'cancel_progress': True}
PROGRESS = []


class InfoTag:
	SCALARS = {
		'setTitle': str, 'setOriginalTitle': str, 'setPlot': str, 'setPlotOutline': str, 'setYear': int,
		'setDuration': int, 'setMpaa': str, 'setMediaType': str, 'setTvShowTitle': str, 'setSeason': int,
		'setEpisode': int, 'setDateAdded': str, 'setTrailer': str,
	}
	LISTS = ('setGenres', 'setDirectors', 'setWriters', 'setCountries')

	def __init__(self):
		self.data = {}
		self.audio = []
		self.subs = []

	def __getattr__(self, name):
		if name in InfoTag.SCALARS:
			def setter(value):
				assert type(value) is InfoTag.SCALARS[name], '%s(%r)' % (name, value)
				self.data[name[3:]] = value
			return setter
		if name in InfoTag.LISTS:
			def setter(value):
				assert isinstance(value, list) and all(isinstance(v, str) for v in value), '%s(%r)' % (name, value)
				self.data[name[3:]] = value
			return setter
		raise AttributeError(name)

	def setCast(self, actors):
		import xbmc
		assert all(isinstance(a, xbmc.Actor) for a in actors)
		self.data['Cast'] = [a.name for a in actors]

	def setUniqueIDs(self, ids, default=''):
		assert isinstance(ids, dict) and all(isinstance(v, str) for v in ids.values())
		self.data['UniqueIDs'] = ids

	def addAudioStream(self, stream):
		self.audio.append(stream.language)

	def addSubtitleStream(self, stream):
		self.subs.append(stream.language)

	def addVideoStream(self, stream):
		pass


class ListItem:
	def __init__(self, label='', label2='', path='', offscreen=False):
		self.label = label
		self.path = path
		self.art = {}
		self.props = {}
		self.menu = []
		self.tag = InfoTag()
		self.subtitles = []
		self.mime = None

	def getVideoInfoTag(self):
		return self.tag

	def setArt(self, art):
		assert all(isinstance(v, str) and v for v in art.values()), art
		self.art.update(art)

	def setProperty(self, key, value):
		assert isinstance(key, str) and isinstance(value, str)
		self.props[key] = value

	def addContextMenuItems(self, items):
		self.menu += items

	def setMimeType(self, mime):
		self.mime = mime

	def setContentLookup(self, enable):
		pass

	def setSubtitles(self, paths):
		self.subtitles = paths


class Dialog:
	def notification(self, heading, message, icon=None, time=0):
		NOTES.append(message)

	def ok(self, heading, message):
		NOTES.append('ok: ' + message)
		return True

	def yesno(self, heading, message):
		NOTES.append('yesno: ' + message)
		return ANSWERS['yesno']

	def input(self, heading, type=0):
		return ANSWERS['input']


class DialogProgress:
	def create(self, heading, message=''):
		PROGRESS.append(message)

	def update(self, percent, message=''):
		assert isinstance(percent, int) and 0 <= percent <= 100
		PROGRESS.append(message)

	def iscanceled(self):
		return ANSWERS['cancel_progress']

	def close(self):
		pass
