
#
# test_units.py – unit tests for code that doesn't need a full plugin run.
#
# Storage (cache expiry, atomic writes under concurrency, private session file), items helpers
# (language codes, image URLs, age ratings, HTML to text), pairing (code lifetime clamping) and
# the build scripts (tag/describe to version, Kodi version order, the repository site, the
# version written into the zip, reproducible zips, the shared icon).
#
# Run: python -m unittest discover -s tests
#

import os
import shutil
import stat
import tempfile
import threading
import time
import unittest
import zipfile

from helpers import KodiTestCase, ROOT

import build_repo
import build_zip


class Storage(unittest.TestCase):
	def setUp(self):
		self.tmp = tempfile.mkdtemp(prefix='epika-unit-')

	def tearDown(self):
		shutil.rmtree(self.tmp, ignore_errors=True)

	def test_cache_ttl_and_delete(self):
		from resources.lib.cache import Cache
		cache = Cache(os.path.join(self.tmp, 'c'))
		cache.set('k', {'a': 1})
		self.assertEqual(cache.get('k', 60), {'a': 1})
		self.assertIsNone(cache.get('k', -1))
		cache.delete('k')
		self.assertIsNone(cache.get('k', 60))

	# Six threads keep rewriting one file while the main thread keeps reading it, like several
	# plugin calls sharing a file. Every read must see a complete file (or none yet) and no temp
	# files may be left behind.
	def test_concurrent_writes_never_leave_partial_files(self):
		from resources.lib.cache import read_json, write_json
		path = os.path.join(self.tmp, 'race.json')
		errors = []

		def writer(n):
			try:
				for i in range(50):
					write_json(path, {'writer': n, 'payload': 'x' * 2000, 'i': i})
			except OSError as err:  # Windows may refuse a replace while another thread reads
				if os.name != 'nt':
					errors.append(err)

		threads = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
		for t in threads:
			t.start()
		for _ in range(200):
			value = read_json(path, {})
			self.assertTrue(value == {} or len(value['payload']) == 2000)
		for t in threads:
			t.join()
		self.assertEqual(errors, [])
		self.assertEqual([n for n in os.listdir(self.tmp) if n.endswith('.tmp')], [])

	def test_session_file_is_private(self):
		from resources.lib.session import Session
		session = Session(os.path.join(self.tmp, 's', 'session.json'))
		session.set_login({'token': 'abc', 'email': 'someone@example.com'})
		self.assertEqual(Session(session.path).token, 'abc')
		self.assertEqual(session.account, 'so***@example.com')
		if os.name != 'nt':
			self.assertEqual(stat.S_IMODE(os.stat(session.path).st_mode), 0o600)
		with self.assertRaises(ValueError):
			session.set_login({'email': 'x@y'})


class Items(KodiTestCase):
	def test_lang_codes_are_filename_safe(self):
		from resources.lib import items
		self.assertEqual(items.lang2('LIT'), 'lt')
		self.assertEqual(items.lang2('UND'), '')
		self.assertEqual(items.lang2('../'), '')
		self.assertEqual(items.lang2(None), '')

	def test_images_https_only(self):
		from resources.lib import items
		self.assertIsNone(items.image({'1x1': [{'url': 'http://x/y.jpg'}]}, '1x1', 1, 1))
		self.assertIsNone(items.image({'1x1': 'junk'}, '1x1', 1, 1))
		self.assertEqual(items.image({'1x1': [{'url': '//x/y.jpg'}]}, '1x1', 1, 1), 'https://x/y.jpg')

	def test_mpaa(self):
		from resources.lib import items
		self.assertEqual(items.mpaa({'rating': 0}), 'V')
		self.assertEqual(items.mpaa({'rating': '7'}), 'N-7')
		self.assertEqual(items.mpaa({'rating': 'x'}), '')
		self.assertEqual(items.mpaa({'tags': [{'name': 'N-18'}], 'rating': 7}), 'N-18')

	def test_plain(self):
		from resources.lib import items
		self.assertEqual(items.plain('<p>A&nbsp;b</p><br/>c<script>x</script>'), 'A\xa0b\n\ncx')


class Pairing(KodiTestCase):
	def test_seconds_left_is_clamped(self):
		from resources.lib import pairing
		self.assertEqual(pairing.seconds_left('1999-01-01T00:00:00Z'), 60)
		self.assertEqual(pairing.seconds_left('2999-01-01T00:00:00Z'), 3600)
		self.assertEqual(pairing.seconds_left(None), 600)


class Build(unittest.TestCase):
	def setUp(self):
		self.tmp = tempfile.mkdtemp(prefix='epika-build-')

	def tearDown(self):
		shutil.rmtree(self.tmp, ignore_errors=True)

	def test_tag_to_version(self):
		self.assertEqual(build_zip.tag_to_version('v1.2.3'), '1.2.3')
		self.assertEqual(build_zip.tag_to_version('v1.2.3-beta.4'), '1.2.3~beta.4')
		self.assertEqual(build_zip.tag_to_version('v1.2.3-rc2'), '1.2.3~rc2')
		self.assertEqual(build_zip.tag_to_version('v1.2.3-Hotfix-login'), '1.2.3~hotfix.login')
		self.assertEqual(build_zip.tag_to_version('v1.2.3-7'), '1.2.3~7')
		for bad in ('1.2.3', 'v1.2', 'v1.2.3-', 'v1.2.3~beta', 'v1.2.3-beta_1', 'v1.2.3-.x', 'v1.2.3 ', 'v1.2.3-a/b'):
			with self.assertRaises(SystemExit, msg=bad):
				build_zip.tag_to_version(bad)

	def test_describe_to_version(self):
		self.assertEqual(build_zip.describe_to_version('v0.3.0-0-gabc1234'), '0.3.0')
		self.assertEqual(build_zip.describe_to_version('v0.3.0-3-gabc1234'), '0.3.1~dev3')
		self.assertEqual(build_zip.describe_to_version('v0.3.0-rc.2-0-gabc1234'), '0.3.0~rc.2')
		self.assertEqual(build_zip.describe_to_version('v0.3.0-rc.2-2-gabc1234'), '0.3.0~rc.2.dev2')
		self.assertEqual(build_zip.describe_to_version('v0.3.0-hotfix-login-1-gabc1234'), '0.3.0~hotfix.login.dev1')
		self.assertEqual(build_zip.describe_to_version(''), '0.0.0~dev')
		self.assertEqual(build_zip.describe_to_version('abc1234'), '0.0.0~dev')

	def test_version_order_matches_kodi(self):
		order = ['0.0.0~dev', '0.2.0', '0.2.1~dev4', '0.3.0~beta.2', '0.3.0~beta.2.dev2', '0.3.0~beta.10', '0.3.0~rc.1', '0.3.0', '0.10.0']
		self.assertEqual([build_zip.describe_to_version('v0.2.0-4-gabc'), build_zip.describe_to_version('v0.3.0-beta.2-2-gabc')], ['0.2.1~dev4', '0.3.0~beta.2.dev2'])
		self.assertEqual(sorted(reversed(order), key=build_repo.version_key), order)

	def fake_zip(self, addon_id, version, extra=None):
		path = os.path.join(self.tmp, 'zips', '%s-%s.zip' % (addon_id, version))
		os.makedirs(os.path.dirname(path), exist_ok=True)
		with zipfile.ZipFile(path, 'w') as zf:
			zf.writestr('%s/addon.xml' % addon_id, '<?xml version="1.0"?>\n<addon id="%s" version="%s"><extension point="xbmc.addon.metadata"><assets><icon>icon.png</icon></assets></extension></addon>' % (addon_id, version))
			zf.writestr('%s/icon.png' % addon_id, b'png')
			for name in extra or []:
				zf.writestr(name, b'x')
		return path

	def test_repository_site(self):
		for v in ('0.1.0', '0.2.0', '0.3.0~beta.1'):
			self.fake_zip('plugin.video.lrt.epika', v)
		self.fake_zip('repository.lrt.epika', '1.0.0')
		out = os.path.join(self.tmp, 'site')
		listed = build_repo.build(os.path.join(self.tmp, 'zips'), out)
		self.assertEqual(listed, [('plugin.video.lrt.epika', '0.1.0'), ('plugin.video.lrt.epika', '0.2.0'), ('repository.lrt.epika', '1.0.0')])
		with open(os.path.join(out, 'repo', 'addons.xml'), encoding='utf-8') as fh:
			xml = fh.read()
		self.assertEqual(xml.count('<addon '), 3)
		self.assertNotIn('beta', xml)
		for rel in ('repo/addons.xml.md5', 'repo/plugin.video.lrt.epika/plugin.video.lrt.epika-0.2.0.zip.sha256',
				'repo/plugin.video.lrt.epika/icon.png', 'repository.lrt.epika-1.0.0.zip', 'index.html', '.nojekyll'):
			self.assertTrue(os.path.exists(os.path.join(out, *rel.split('/'))), rel)
		with open(os.path.join(out, 'index.html'), encoding='utf-8') as fh:
			self.assertIn('<a href="repository.lrt.epika-1.0.0.zip">repository.lrt.epika-1.0.0.zip</a>', fh.read())

	def test_rejects_unsafe_zip(self):
		self.fake_zip('plugin.video.lrt.epika', '0.1.0', extra=['plugin.video.lrt.epika/../../evil'])
		with self.assertRaises(SystemExit):
			build_repo.build(os.path.join(self.tmp, 'zips'), os.path.join(self.tmp, 'site'))

	def test_tag_version_is_written_into_the_zip_only(self):
		path, version = build_zip.build('plugin.video.lrt.epika', '1.2.3~beta.4', self.tmp)
		# "~" in the version, "-" in the file name (GitHub would rename "~" in release files)
		self.assertEqual(os.path.basename(path), 'plugin.video.lrt.epika-1.2.3-beta.4.zip')
		self.assertEqual(version, '1.2.3~beta.4')
		with zipfile.ZipFile(path) as zf:
			self.assertEqual(build_zip.addon_version(zf.read('plugin.video.lrt.epika/addon.xml').decode('utf-8')), '1.2.3~beta.4')
		with open(os.path.join(ROOT, 'plugin.video.lrt.epika', 'addon.xml'), encoding='utf-8') as fh:
			source = fh.read()
		self.assertEqual(build_zip.addon_version(source), '0.0.0')
		self.assertEqual(source.count('version="3.0.1"'), 1)  # dependency versions untouched

	def test_real_build_is_reproducible(self):
		first = build_zip.build('plugin.video.lrt.epika', out_dir=os.path.join(self.tmp, 'a'))[0]
		with open(first, 'rb') as fh:
			a = fh.read()
		time.sleep(1.1)
		with open(build_zip.build('plugin.video.lrt.epika', out_dir=os.path.join(self.tmp, 'b'))[0], 'rb') as fh:
			self.assertEqual(a, fh.read())
		with zipfile.ZipFile(first) as zf:
			names = zf.namelist()
			plugin_icon = zf.read('plugin.video.lrt.epika/icon.png')
		with zipfile.ZipFile(build_zip.build('repository.lrt.epika', out_dir=self.tmp)[0]) as zf:
			self.assertEqual(sorted(zf.namelist()), ['repository.lrt.epika/addon.xml', 'repository.lrt.epika/icon.png', 'repository.lrt.epika/license.txt'])
			repo_icon = zf.read('repository.lrt.epika/icon.png')
		# One icon next to the readme, identical copies in both zips, where each addon.xml points.
		with open(os.path.join(ROOT, 'icon.png'), 'rb') as fh:
			root_icon = fh.read()
		self.assertEqual(plugin_icon, root_icon)
		self.assertEqual(repo_icon, root_icon)
		for addon_id in build_zip.ADDONS:
			with open(os.path.join(ROOT, addon_id, 'addon.xml'), encoding='utf-8') as fh:
				self.assertIn('<icon>icon.png</icon>', fh.read())
		self.assertIn('plugin.video.lrt.epika/license.txt', names)
		self.assertFalse([n for n in names if '__pycache__' in n or n.endswith('.pyc')])


if __name__ == '__main__':
	unittest.main()
