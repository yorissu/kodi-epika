
#
# session.py – who this Kodi is to Epika: the device id and the login token.
#
# What is stored
#	profile/session.json holds three things:
#		device_uid   random id of this Kodi installation, sent with every request (API-DeviceUid)
#		token        the login token Epika returns after pairing (API-Authentication), or null
#		account      the masked e-mail shown in the menu, e.g. "an***@example.com"
#	No e-mail or password is ever stored: the login happens on the Epika website (pairing.py), and
#	the token can be revoked at any time by removing the device there (account → devices) or with
#	Log out in the add-on, which also gives the installation a new device id.
#
# Why not in settings.xml
#	Kodi's settings are visible in the settings dialog and are often copied around in backups.
#	session.json is written with permissions 0600, readable only by the user Kodi runs as.
#
# The file is shared by plugin calls that may run at the same time; writes are atomic (cache.py).
#

import os
import uuid

from .cache import read_json, write_json


# Marker returned by load() when session.json exists but couldn't be read or parsed.
UNREADABLE = object()


# The login state of this installation, loaded from session.json when created.
# On the very first run there is no file yet: a device id is generated and saved.
class Session:
	def __init__(self, path):
		self.path = path
		self.device_uid = None
		self.token = None
		self.account = None
		state = self.load()
		# Only start fresh when there is no usable file. A file that exists but can't be read
		# right now (another process mid-replace, I/O error) must not be overwritten.
		if not self.device_uid and state is not UNREADABLE:
			self.device_uid = str(uuid.uuid4())
			self._save_quietly()
		elif not self.device_uid:
			self.device_uid = str(uuid.uuid4())

	@property
	def logged_in(self):
		return bool(self.token)

	# Reads session.json into the attributes. Returns None when there is no file, UNREADABLE when
	# it exists but is broken, otherwise the data. Values of the wrong type are treated as missing.
	def load(self):
		if not os.path.exists(self.path):
			return None
		data = read_json(self.path, UNREADABLE)
		if not isinstance(data, dict):
			return UNREADABLE
		self.device_uid = _text(data.get('device_uid'))
		self.token = _text(data.get('token'))
		self.account = _text(data.get('account'))
		return data

	def save(self):
		write_json(self.path, {'device_uid': self.device_uid, 'token': self.token, 'account': self.account}, 0o600)

	def _save_quietly(self):
		try:
			self.save()
		except OSError:
			pass

	# Stores the result of a successful pairing (the JSON Epika returns from subscribers/login).
	# Only the token and a masked e-mail are kept; the rest of the response (profiles, ids) is not.
	def set_login(self, response):
		token = response.get('token') if isinstance(response, dict) else None
		if not isinstance(token, str) or not token:
			raise ValueError('login response has no token')
		self.token = token
		self.account = _mask(response.get('email'))
		self.save()

	# Forgets the login (Log out, or a token Epika no longer accepts).
	def clear(self):
		self.token = None
		self.account = None
		# A new device id on logout, so the next pairing is a fresh device.
		self.device_uid = str(uuid.uuid4())
		self.save()


def _text(value):
	return value if isinstance(value, str) and value else None


# Keep just enough to recognise the account in the menu: an***@example.com
def _mask(email):
	if not isinstance(email, str) or '@' not in email:
		return ''
	name, domain = email.split('@', 1)
	return '%s***@%s' % (name[:2], domain)
