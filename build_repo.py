
#
# build_repo.py – builds the Kodi repository website that GitHub Pages serves.
#
# What it is for
#	Kodi installs and updates add-ons from "repositories": plain websites with an index of add-ons
#	(addons.xml) and their zips. repository.lrt.epika points Kodi at the site this script builds,
#	which is how LRT Epika gets automatic updates and how older versions stay installable.
#
# What it does
#	Reads every add-on zip in ZIP_DIR (the Release workflow downloads all published releases into
#	it), checks each zip is safe and well-formed, and writes OUT_DIR:
#		repo/addons.xml (+ .md5)                    the index Kodi reads, all versions of all add-ons
#		repo/<id>/<id>-<version>.zip (+ .sha256)    every released version, with a checksum Kodi verifies
#		repo/<id>/<assets>                          icon etc. that Kodi shows before installing
#		index.html + repository.lrt.epika-<v>.zip   the page Kodi's file manager browses for the first install
#	Pre-releases (Kodi versions with "~", e.g. 0.3.0~rc.2 from tag v0.3.0-rc.2, and dev builds) are skipped: they are only
#	installed by hand from their GitHub pre-release, never offered as Kodi updates.
#
# Usage
#	python build_repo.py ZIP_DIR OUT_DIR
#

import hashlib
import html
import os
import re
import shutil
import sys
import zipfile
from xml.etree import ElementTree

from build_zip import is_prerelease


USAGE = 'usage: python build_repo.py ZIP_DIR OUT_DIR'
REPOSITORY = 'repository.lrt.epika'
# Folder of the site that repository.lrt.epika/addon.xml points at.
CHANNEL = 'repo'
# Add-on ids and asset paths may only use these characters (they become paths on the site).
SAFE_PATH = re.compile(r'^[A-Za-z0-9._/-]+$')


# Sort key that orders versions the way Kodi does: 1.0.0~beta.2 < 1.0.0~beta.10 < 1.0.0~rc.1 < 1.0.0 < 1.0.1.
# The part after "~" is a pre-release label, which sorts below the plain release.
def version_key(version):
	main, _, pre = version.partition('~')
	numbers = tuple(int(p) if p.isdigit() else 0 for p in main.split('.'))
	if not pre:
		return numbers, 1, ()
	label = tuple((0, int(part)) if part.isdigit() else (1, part) for part in re.findall(r'\d+|[a-z]+', pre))
	return numbers, 0, label


# Opens one add-on zip and returns (id, version, addon.xml text, {asset path: bytes}).
# Refuses (stops the build) zips that Kodi would reject or that could write outside their folder:
# more than one top-level folder, absolute or ".." paths, an id that doesn't match the folder,
# or an id/version with characters that are unsafe in a URL path.
def read_addon(path):
	with zipfile.ZipFile(path) as zf:
		names = zf.namelist()
		roots = {n.split('/', 1)[0] for n in names}
		if len(roots) != 1:
			raise SystemExit('%s: expected one top-level folder, found %s' % (path, sorted(roots)))
		addon_id = roots.pop()
		for name in names:
			if name.startswith('/') or '..' in name.split('/') or '\\' in name:
				raise SystemExit('%s: unsafe path %r' % (path, name))
		text = zf.read('%s/addon.xml' % addon_id).decode('utf-8')
		root = ElementTree.fromstring(text)
		if root.get('id') != addon_id:
			raise SystemExit('%s: addon.xml id %r does not match folder %r' % (path, root.get('id'), addon_id))
		version = root.get('version')
		if not version or not SAFE_PATH.match(addon_id) or not re.match(r'^[0-9][0-9a-z.~+-]*$', version):
			raise SystemExit('%s: bad id/version %r %r' % (path, addon_id, version))
		files = {}
		for asset in assets(root):
			try:
				files[asset] = zf.read('%s/%s' % (addon_id, asset))
			except KeyError:
				print('warning: %s %s lists missing asset %s' % (addon_id, version, asset))
	return addon_id, version, text, files


# Files Kodi shows before install: icon, fanart, screenshots declared in <assets>.
def assets(root):
	found = []
	for node in root.iter('assets'):
		for child in node:
			value = (child.text or '').strip()
			if value and SAFE_PATH.match(value) and '..' not in value.split('/'):
				found.append(value)
	return found


# addon.xml text without its optional <?xml ...?> line, so it can be embedded in addons.xml.
def strip_declaration(text):
	return re.sub(r'^\s*<\?xml[^>]*\?>\s*', '', text).strip()


def write(path, data):
	os.makedirs(os.path.dirname(path), exist_ok=True)
	with open(path, 'wb') as fh:
		fh.write(data if isinstance(data, bytes) else data.encode('utf-8'))


# Builds the whole site in out_dir (replacing what was there) and returns the published
# (id, version) pairs. Stops if no released zips are found, or if repository.lrt.epika is missing:
# without it in addons.xml Kodi could never update the repository add-on itself.
def build(zip_dir, out_dir):
	addons = {}
	for name in sorted(os.listdir(zip_dir)):
		if not name.endswith('.zip'):
			continue
		path = os.path.join(zip_dir, name)
		addon_id, version, text, files = read_addon(path)
		if is_prerelease(version):
			print('skipping pre-release %s %s' % (addon_id, version))
			continue
		addons.setdefault((addon_id, version), (text, files, path))
	if not addons:
		raise SystemExit('no released add-on zips in %s' % zip_dir)
	if not any(a == REPOSITORY for a, _ in addons):
		raise SystemExit('%s zip missing: Kodi could never update the repository itself' % REPOSITORY)
	if os.path.isdir(out_dir):
		shutil.rmtree(out_dir)
	entries = []
	for (addon_id, version), (text, files, path) in sorted(addons.items(), key=lambda kv: (kv[0][0], version_key(kv[0][1]))):
		with open(path, 'rb') as fh:
			data = fh.read()
		base = os.path.join(out_dir, CHANNEL, addon_id)
		write(os.path.join(base, '%s-%s.zip' % (addon_id, version)), data)
		write(os.path.join(base, '%s-%s.zip.sha256' % (addon_id, version)), hashlib.sha256(data).hexdigest())
		for asset, content in files.items():
			write(os.path.join(base, *asset.split('/')), content)
		entries.append(strip_declaration(text))
	xml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<addons>\n%s\n</addons>\n' % '\n'.join(entries)
	ElementTree.fromstring(xml.encode('utf-8'))  # must stay well-formed
	write(os.path.join(out_dir, CHANNEL, 'addons.xml'), xml)
	write(os.path.join(out_dir, CHANNEL, 'addons.xml.md5'), hashlib.md5(xml.encode('utf-8')).hexdigest())
	# Newest repository zip at the root, for "Install from zip file" from a Kodi file source.
	newest = max((v for a, v in addons if a == REPOSITORY), key=version_key)
	repo_zip = '%s-%s.zip' % (REPOSITORY, newest)
	shutil.copyfile(addons[(REPOSITORY, newest)][2], os.path.join(out_dir, repo_zip))
	write(os.path.join(out_dir, 'index.html'), index_page(repo_zip))
	write(os.path.join(out_dir, '.nojekyll'), '')
	return sorted(addons)


# The site's front page. Kodi's file manager reads it like a folder listing when it's added as a
# source; its parser only lists links whose text equals the file name, so the repository zip
# link must stay exactly like that. The other text is for people opening it in a browser.
def index_page(repo_zip):
	name = html.escape(repo_zip)
	return '''<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>LRT Epika for Kodi</title></head>
<body>
<h1>LRT Epika for Kodi</h1>
<p>Add this page as a source in Kodi's file manager, then Add-ons &rarr; Install from zip file &rarr; this source.</p>
<ul>
<li><a href="%s">%s</a></li>
</ul>
<p><a href="https://github.com/yorissu/kodi-epika">Source and releases on GitHub</a></p>
</body>
</html>
''' % (name, name)


def main():
	if len(sys.argv) != 3:
		raise SystemExit(USAGE)
	for addon_id, version in build(sys.argv[1], sys.argv[2]):
		print('%s %s' % (addon_id, version))


if __name__ == '__main__':
	main()
