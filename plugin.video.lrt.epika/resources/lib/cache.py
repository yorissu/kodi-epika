
#
# cache.py – JSON files in the add-on profile folder: safe read/write helpers and an expiring cache.
#
# What it is for
#	Every plugin call starts from nothing, so anything worth keeping (the login in session.py,
#	the search history in plugin.py, Epika responses that are slow to fetch) is stored as JSON
#	files. The cache keeps API responses in profile/cache/ for a while, which makes going back
#	and forth in Kodi's menus fast and spares Epika's servers.
#
# Why the writes look complicated
#	Kodi can run several plugin calls at the same time (a listing, a context-menu action, the
#	playback helper still waiting for subtitles), and they share these files. A reader must never
#	see a half-written file, and two writers must never mix their data. So every write goes to
#	its own temp file (named after the process and thread) and then replaces the real file in
#	one step with os.replace, which is atomic. Readers see either the old or the new file.
#
# Nothing here imports Kodi, so the tests can use it directly.
#

import hashlib
import json
import os
import threading
import time


# Contents of a JSON file, or default when it is missing, unreadable or not valid JSON.
def read_json(path, default=None):
	try:
		with open(path, 'r', encoding='utf-8') as fh:
			return json.load(fh)
	except FileNotFoundError:
		return default
	except (OSError, ValueError):
		return default


# Writes data as JSON atomically (see the top of the file). mode sets the file permissions on
# Linux/LibreELEC; session.py passes 0o600 so only Kodi's user can read the login token.
# Raises OSError when the file can't be written; the temp file is removed in that case.
def write_json(path, data, mode=0o644):
	folder = os.path.dirname(path)
	if folder:
		os.makedirs(folder, exist_ok=True)
	tmp = '%s.%d.%d.tmp' % (path, os.getpid(), threading.get_ident())
	try:
		fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
		with os.fdopen(fd, 'w', encoding='utf-8') as fh:
			json.dump(data, fh)
		os.replace(tmp, path)
	except OSError:
		try:
			os.remove(tmp)
		except OSError:
			pass
		raise
	try:
		os.chmod(path, mode)
	except OSError:
		pass


# Expiring key/value cache, one JSON file per key (named by the key's SHA-1). A value's age is
# the file's modification time, so each get() decides with its own ttl how old is too old.
class Cache:
	def __init__(self, folder):
		self.folder = folder
		os.makedirs(folder, exist_ok=True)

	def _path(self, key):
		return os.path.join(self.folder, hashlib.sha1(key.encode('utf-8')).hexdigest() + '.json')

	# The stored value if it is younger than ttl seconds, otherwise None.
	def get(self, key, ttl):
		path = self._path(key)
		try:
			if time.time() - os.path.getmtime(path) > ttl:
				return None
		except OSError:
			return None
		return read_json(path)

	def set(self, key, value):
		try:
			write_json(self._path(key), value)
		except OSError:
			pass  # a cache that can't be written is just a slower cache

	def delete(self, key):
		try:
			os.remove(self._path(key))
		except OSError:
			pass

	# Deletes entries older than max_age seconds (plugin.py prunes week-old ones when the main
	# menu opens). A negative max_age deletes everything, which is what clear() does.
	def prune(self, max_age):
		now = time.time()
		try:
			names = os.listdir(self.folder)
		except OSError:
			return
		for name in names:
			path = os.path.join(self.folder, name)
			try:
				if now - os.path.getmtime(path) > max_age:
					os.remove(path)
			except OSError:
				pass

	def clear(self):
		self.prune(-1)
