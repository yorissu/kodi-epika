
#
# build_zip.py – packages the Kodi add-ons in this repository as installable zips.
#
# What it does
#	Writes dist/<add-on id>-<version>.zip for every add-on in ADDONS, plus dist/SHA256SUMS.
#	(File names use "-" where the Kodi version has "~": see build().)
#	Each zip has the layout Kodi expects for "Install from zip file": one top-level folder named
#	after the add-on id, holding addon.xml and everything else from the add-on folder.
#	The files in ROOT_FILES (license.txt, icon.png) live once, next to the readme, and are copied
#	into every zip; each add-on's addon.xml refers to them at its own root.
#
# Where the version comes from
#	The git tag is the only place the plugin version is set. plugin.video.lrt.epika/addon.xml holds
#	the placeholder 0.0.0 and this script writes the real version into the zip's copy:
#		v0.3.0               -> 0.3.0
#		v0.3.0-rc.2          -> 0.3.0~rc.2    pre-release tag vX.Y.Z-<suffix>; Kodi sorts "~" below
#		                                      the release, and only the Kodi version has it
#		3 commits after it   -> 0.3.1~dev3    newer than v0.3.0, older than any next release
#		no git / no tags     -> 0.0.0~dev
#	repository.lrt.epika keeps the version in its own addon.xml; it only changes with its URLs.
#
# Usage
#	python build_zip.py                 version from "git describe" (local and CI builds)
#	python build_zip.py --tag v0.3.0    version from the tag (what the Release workflow runs)
#	In GitHub Actions it also writes version= and prerelease= to $GITHUB_OUTPUT.
#
# The zips are reproducible: fixed timestamps and permissions and sorted entries, so the same
# source always gives byte-identical files and identical SHA-256 sums.
#

import argparse
import hashlib
import os
import re
import subprocess
import sys
import zipfile


ROOT = os.path.dirname(os.path.abspath(__file__))
ADDONS = ('plugin.video.lrt.epika', 'repository.lrt.epika')
PLUGIN = ADDONS[0]
# Files kept once at the repository root and copied into every zip: Kodi can only read files
# inside an add-on's own folder, so each zip needs its own copy.
ROOT_FILES = ('license.txt', 'icon.png')
DIST = os.path.join(ROOT, 'dist')
STAMP = (2020, 1, 1, 0, 0, 0)
NO_VERSION = '0.0.0~dev'
VERSION_ATTR = re.compile(r'(<addon\b[^>]*?\bversion=")([^"]+)(")')
# Release tag vX.Y.Z, or vX.Y.Z-<suffix> for a pre-release (suffix: letters, digits, dots, hyphens).
TAG = re.compile(r'^v(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z][0-9A-Za-z.-]*))?$')


def addon_version(addon_xml):
	match = VERSION_ATTR.search(addon_xml)
	if not match:
		raise SystemExit('no version in addon.xml')
	return match.group(2)


# Converts a release tag to a Kodi version: v0.3.0 -> 0.3.0, v0.3.0-rc.2 -> 0.3.0~rc.2.
# Tags never contain "~"; it only appears in the Kodi version, where it marks a pre-release
# (Kodi sorts 0.3.0~rc.2 below 0.3.0). The suffix is lower-cased and its hyphens become dots,
# because "-" has a special meaning in Kodi versions: v0.3.0-Hotfix-login -> 0.3.0~hotfix.login.
# Pre-releases of the same version sort alphabetically/numerically by suffix (beta.2 < beta.10
# < rc.1). Anything else stops the build, so a mistyped tag can never publish a release.
def tag_to_version(tag):
	match = TAG.match(tag or '')
	if not match:
		raise SystemExit('tag %r is not vX.Y.Z or vX.Y.Z-<suffix>' % tag)
	major, minor, patch, suffix = match.groups()
	version = '%s.%s.%s' % (major, minor, patch)
	return '%s~%s' % (version, suffix.lower().replace('-', '.')) if suffix else version


# Converts "git describe --tags --long" output (<tag>-<commits since tag>-g<hash>) to a Kodi version.
#	v0.3.0-0-gabc          -> 0.3.0              exactly on the tag
#	v0.3.0-3-gabc          -> 0.3.1~dev3         after a release: next patch, below that release
#	v0.3.0-rc.2-2-gabc     -> 0.3.0~rc.2.dev2    after a pre-release: between it and the release
#	anything unparsable    -> 0.0.0~dev
# The tag part may itself contain hyphens (v0.3.0-hotfix-login): --long always appends the two
# last fields, so they are split off from the end. Dev versions always contain "~", so
# build_repo.py never publishes them.
def describe_to_version(described):
	match = re.match(r'^(v.+)-(\d+)-g[0-9a-f]+$', described or '')
	if not match:
		return NO_VERSION
	tag, distance = match.group(1), int(match.group(2))
	version = tag_to_version(tag)
	if distance == 0:
		return version
	if is_prerelease(version):
		return '%s.dev%d' % (version, distance)  # 0.3.0~rc.2 < 0.3.0~rc.2.dev2 < 0.3.0
	major, minor, patch = TAG.match(tag).groups()[:3]
	return '%s.%s.%d~dev%d' % (major, minor, int(patch) + 1, distance)


# Version for a build without --tag; 0.0.0~dev when git or the tags are not available.
def git_version():
	try:
		described = subprocess.run(['git', 'describe', '--tags', '--long', '--match', 'v[0-9]*'], cwd=ROOT,
			capture_output=True, text=True, timeout=30).stdout.strip()
	except (OSError, subprocess.SubprocessError):
		return NO_VERSION
	return describe_to_version(described)


def is_prerelease(version):
	return '~' in version


# One zip member with a fixed date and permissions, which keeps the zips reproducible.
def _entry(name, data, mode=0o644):
	info = zipfile.ZipInfo(name, STAMP)
	info.compress_type = zipfile.ZIP_DEFLATED
	info.external_attr = (0o100000 | mode) << 16
	return info, data


# Zips one add-on folder into out_dir (dist/ by default) and returns (zip path, version).
# version, when given, replaces the version attribute in the zipped addon.xml; the source file is
# never changed. Python caches, temp and hidden files are left out; ROOT_FILES are added at the
# add-on's root, and a copy of one of them inside the add-on folder stops the build.
# The file name uses "-" where the Kodi version has "~" (plugin.video.lrt.epika-0.3.0-rc.2.zip,
# like the tag): GitHub renames special characters in release files, which would make a
# pre-release look like a normal one. Kodi reads the version from addon.xml, not the file name.
def build(addon_id, version=None, out_dir=DIST):
	source = os.path.join(ROOT, addon_id)
	with open(os.path.join(source, 'addon.xml'), encoding='utf-8') as fh:
		version = version or addon_version(fh.read())
	entries = []
	for folder, dirs, files in os.walk(source):
		dirs[:] = sorted(d for d in dirs if d != '__pycache__' and not d.startswith('.'))
		for name in sorted(files):
			if name.endswith(('.pyc', '.pyo', '.tmp')) or name.startswith('.'):
				continue
			path = os.path.join(folder, name)
			with open(path, 'rb') as fh:
				data = fh.read()
			rel = os.path.relpath(path, source).replace(os.sep, '/')
			if rel == 'addon.xml':
				data = VERSION_ATTR.sub(lambda m: m.group(1) + version + m.group(3), data.decode('utf-8'), count=1).encode('utf-8')
			entries.append(_entry('%s/%s' % (addon_id, rel), data))
	for name in ROOT_FILES:
		with open(os.path.join(ROOT, name), 'rb') as fh:
			entries.append(_entry('%s/%s' % (addon_id, name), fh.read()))
	names = [info.filename for info, _ in entries]
	if len(names) != len(set(names)):
		raise SystemExit('%s: a file from the repository root also exists in the add-on folder' % addon_id)
	entries.sort(key=lambda e: e[0].filename)
	os.makedirs(out_dir, exist_ok=True)
	target = os.path.join(out_dir, '%s-%s.zip' % (addon_id, version.replace('~', '-')))
	with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as zf:
		for info, data in entries:
			zf.writestr(info, data)
	return target, version


def sha256(path):
	digest = hashlib.sha256()
	with open(path, 'rb') as fh:
		for block in iter(lambda: fh.read(65536), b''):
			digest.update(block)
	return digest.hexdigest()


def main():
	parser = argparse.ArgumentParser(description='Package the Kodi add-ons as installable zips in dist/.')
	parser.add_argument('--tag', help='release tag the plugin version is taken from (default: git describe)')
	args = parser.parse_args()
	version = tag_to_version(args.tag) if args.tag else git_version()
	# Start from an empty dist/ so a release can never pick up a zip from an earlier build.
	if os.path.isdir(DIST):
		for name in os.listdir(DIST):
			if name.endswith('.zip') or name == 'SHA256SUMS':
				os.remove(os.path.join(DIST, name))
	built = {PLUGIN: build(PLUGIN, version)}
	built.update((addon_id, build(addon_id)) for addon_id in ADDONS if addon_id != PLUGIN)
	with open(os.path.join(DIST, 'SHA256SUMS'), 'w', encoding='utf-8', newline='\n') as fh:
		for path, _ in built.values():
			fh.write('%s  %s\n' % (sha256(path), os.path.basename(path)))
	for path, _ in built.values():
		print(path)
	output = os.environ.get('GITHUB_OUTPUT')
	if output:
		with open(output, 'a', encoding='utf-8') as fh:
			fh.write('version=%s\nprerelease=%s\n' % (version, 'true' if is_prerelease(version) else 'false'))
	kind = ' (dev build)' if '~dev' in version or '.dev' in version else ' (pre-release)' if is_prerelease(version) else ''
	print('plugin version %s%s' % (version, kind))


if __name__ == '__main__':
	sys.exit(main())
