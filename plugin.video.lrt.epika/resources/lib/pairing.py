
#
# pairing.py – logging in to Epika with a code, the way Epika's smart-TV apps do it.
#
# How the login works
#	1. Kodi asks Epika for a 6-digit code (epika.py: create_pair_code) and shows it in a dialog.
#	2. The user opens epika.lrt.lt/ziureti-tv on a phone or PC, logs in there and types the code.
#	3. Meanwhile Kodi asks Epika every 4 seconds whether the code was confirmed (login_with_code).
#	   Once it is, Epika answers with the account and a login token, which session.py stores.
#	The code is valid for about 20 minutes; when it runs out the user is offered a new one.
#
# Why this way
#	Nothing has to be typed with the remote, and no e-mail or password ever reaches Kodi: the
#	add-on only gets a device token that can be revoked on the website at any time. Epika's own
#	login (Auth0, in a browser) can't be done inside Kodi anyway.
#

from . import kodi
from .epika import EpikaError

import xbmcgui
import xbmc

import datetime
import time


# How often Epika is asked whether the code was confirmed.
POLL_SECONDS = 4
# Code lifetime assumed when Epika's expiry time can't be read, and the longest one believed.
DEFAULT_LIFETIME = 600
MAX_LIFETIME = 3600


# Seconds until the code expires, from Epika's expiresAt ("2026-10-02T22:49:40+02:00").
# Clamped to 1-60 minutes, so a wrong clock on the device can't end the dialog at once or
# keep it open for hours.
def seconds_left(expires_at):
	try:
		end = datetime.datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
		left = (end - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
	except (AttributeError, TypeError, ValueError):
		return DEFAULT_LIFETIME
	# Guard against a wrong clock on the device: never 0, never hours.
	return min(max(left, 60), MAX_LIFETIME)


# Dialog text with the instructions and the code, shown as "123 456" for easier reading.
def _message(code):
	spaced = '%s %s' % (code[:3], code[3:])
	return '%s\n%s' % (kodi.translate(30031), kodi.translate(30032) % spaced)


# One check whether the user has confirmed the code. Returns Epika's login response (with the
# token) once they have, otherwise None. "Not confirmed yet" comes back from Epika as the error
# DEVICE_CODE_EXPIRED_OR_NOT_EXISTS, so that one is expected and not logged; other errors (e.g.
# a network hiccup) are logged and simply retried at the next poll.
def _poll(epika, code):
	try:
		response = epika.login_with_code(code)
	except EpikaError as err:
		if err.code != 'DEVICE_CODE_EXPIRED_OR_NOT_EXISTS':
			kodi.log('pair poll failed: %s' % err, xbmc.LOGWARNING)
		return None
	if isinstance(response, dict) and isinstance(response.get('token'), str) and response['token']:
		return response
	return None


# Runs the whole login: gets a code, shows the progress dialog (the bar runs down with the code's
# lifetime) and polls until the code is confirmed, the dialog is cancelled or Kodi shuts down.
# An expired code offers a new one. Returns True when logged in (the token is then saved).
# The dialog stays responsive: cancel and shutdown are checked every 0.25 s between polls.
def pair(epika, session):
	monitor = xbmc.Monitor()
	while True:
		try:
			data = epika.create_pair_code()
		except EpikaError as err:
			kodi.log('pair code failed: %s' % err, xbmc.LOGERROR)
			xbmcgui.Dialog().ok(kodi.NAME, kodi.translate(30041) if err.code == 'NETWORK' else kodi.translate(30040) % err.code)
			return False
		code = str(data['code'])
		total = seconds_left(data.get('expiresAt'))
		deadline = time.monotonic() + total
		dialog = xbmcgui.DialogProgress()
		dialog.create(kodi.translate(30030), _message(code))
		try:
			while time.monotonic() < deadline:
				dialog.update(int(100 * max(0, deadline - time.monotonic()) / total), _message(code))
				for _ in range(POLL_SECONDS * 4):
					if dialog.iscanceled() or monitor.waitForAbort(0.25):
						return False
				response = _poll(epika, code)
				if response:
					session.set_login(response)
					kodi.notify(kodi.translate(30034) % (session.account or ''))
					return True
		finally:
			dialog.close()
		if not xbmcgui.Dialog().yesno(kodi.NAME, kodi.translate(30033)):
			return False


# Guard for actions that need an account (playing, My List): True when logged in; otherwise
# asks whether to log in now and runs pair() if the user agrees.
def ensure_login(epika, session):
	if session.logged_in:
		return True
	if xbmcgui.Dialog().yesno(kodi.NAME, kodi.translate(30037)):
		return pair(epika, session)
	return False
