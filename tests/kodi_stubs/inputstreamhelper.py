
#
# kodi_stubs/inputstreamhelper.py – test stand-in for script.module.inputstreamhelper.
#
# check_inputstream() answers READY[0]: True means Widevine is installed, False lets tests check
# what happens when it isn't.
#

READY = [True]


class Helper:
	def __init__(self, protocol, drm=None):
		self.protocol = protocol
		self.drm = drm

	def check_inputstream(self):
		return READY[0]
