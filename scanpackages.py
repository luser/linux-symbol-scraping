#!/usr/bin/env python
"""
The purpose of this script, as discussed at
http://randomascii.wordpress.com/2013/02/20/symbols-on-linux-part-three-linux-versus-windows/,
is to download various Packages files from ddebs.ubuntu.com, download all of the packages listed
within, extract build IDs from files installed by these packages, and add these build IDs to a
single, consolidated, enhanced Packages file -- multiple Packages files, and build IDs,
in one file.
The PackagesProcessed result file contains additional lines of text of the format:
    BuildID SOName PackageURL
The idea is that a simple grep through the file for a build ID will return all of the
information needed to download the relevant package.
"""

from __future__ import print_function

import html5lib
import itertools
import json
import logging
import multiprocessing
import os
import re
import requests
import shutil
import subprocess
import sys
import tempfile
import urlparse

from common import fetch_to_file
from concurrent.futures import ThreadPoolExecutor, as_completed

log = logging.getLogger('scanpackages')

class AutoSaveDict(dict):
    def __init__(self, path):
        if os.path.isfile(path):
            self.update(json.load(open(path, 'rb')))
        self.path = path
    def __setitem__(self, *args, **kwargs):
        dict.__setitem__(self, *args, **kwargs)
        with tempfile.NamedTemporaryFile(delete=False) as f:
            json.dump(self, f)
            f.close()
            os.rename(f.name, self.path)

def GetBuildID(dso):
    """This function uses 'file' and 'readelf' to see if the specified file is an ELF
file, and if so to try to get its build ID. If no build ID is found then it returns None."""
    # First see if the file is an ELF file -- this avoids error messages
    # from readelf.
    if not subprocess.check_output(['file', '-Lb', dso]).startswith('ELF'):
        return None

    # Now execute readelf. Note that some older versions don't understand build IDs.
    # If you are running such an old version then you can dump the contents of the
    # build ID section and parse the raw data.
    lines = subprocess.check_output(['readelf', '-n', dso]).splitlines()
    # We're looking for this output:
    # Build ID: 99c2106c44189e354e1826aa285a0ccf7cbdf726
    for line in lines:
        match = re.match("Build ID: (.*)", line.strip())
        if match:
            buildID = match.group(1)
            if len(buildID) == 40:
                return buildID
    return None

def process_deb(deb_url):
    log.info('Processing deb %s...' % deb_url)
    buildid_files = []
    try:
        tempDir = tempfile.mkdtemp()
        # Then we download the package.
        deb_file = os.path.join(tempDir, 'file.deb')
        fetch_to_file(deb_url, deb_file)

        # Now we unpack the package
        subprocess.check_call(['dpkg-deb', '-x', deb_file, tempDir])

        for root, dirs, files in os.walk(tempDir):
            for f in files:
                path = os.path.join(root, f)
                # There are dangling symlinks in -dbg packages...
                if not os.path.exists(f):
                    continue
                buildID = GetBuildID(path)
                if buildID:
                    buildid_files.append(('/' + os.path.relpath(path, tempDir),
                                          buildID))
    finally:
        shutil.rmtree(tempDir)
    return buildid_files

def scrape_html_directory_listing(url):
    r = requests.get(url)
    if r.status_code == 200:
        doc = html5lib.parse(r.text, treebuilder='dom')
        for a in doc.getElementsByTagName('a'):
            href = a.getAttribute('href')
            text = a.childNodes[0].data
            if href == text:
                yield urlparse.urljoin(url, href)

def scrape_x86_debs(url):
    archs = {'amd64', 'i386'}
    for deb in scrape_html_directory_listing(url):
        arch = os.path.splitext(urlparse.urlparse(deb).path)[0].split('_')[-1]
        if arch in archs:
            yield deb

def scrape_package_list(main_url):
    cached_allpackages = '/tmp/%s_allpackages' % ''.join(x for x in main_url if x.isalnum())
    if os.path.isfile(cached_allpackages):
        return json.load(open(cached_allpackages, 'rb'))

    log.info('Scraping package listing from %s...' % main_url)
    package_list = []
    for url in scrape_html_directory_listing(main_url):
        package_list.extend(list(scrape_html_directory_listing(url)))
        json.dump(package_list, open(cached_allpackages, 'wb'))
    return package_list

def chunk(iterable, chunk_size):
    i = iter(iterable)
    while True:
        this_chunk = list(itertools.islice(i, chunk_size))
        if not this_chunk:
            return
        yield this_chunk

def scrape_all_ddebs(worker_count, main_url, filter_func=None):
    ddebs = AutoSaveDict('/tmp/ddebs.json')
    processed_packages = AutoSaveDict('/tmp/processed-packages.json')
    skip_packages = {}
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        package_urls = [url for url in scrape_package_list(main_url) if url not in processed_packages]
        for urls_chunk in chunk(package_urls, worker_count):
            log.info('Processing next %d packages...' % len(urls_chunk))
            for url, debs in zip(urls_chunk,
                                 executor.map(scrape_x86_debs, urls_chunk)):
                log.info('Processing package %s...' % url)
                debs = [
                    deb for deb in debs if deb not in ddebs
                    # The linux-image packages are just huge.
                    and not os.path.basename(urlparse.urlparse(deb).path).startswith('linux-image')
                    and (filter_func(deb) if filter_func else True)
                ]
                log.info('%d debs to process...' % len(debs))
                deb_jobs = dict((executor.submit(process_deb, deb), deb) for deb in debs)
                for future in as_completed(deb_jobs):
                    deb = deb_jobs[future]
                    if future.exception() is not None:
                        log.info('Error processing %s: %s' % (deb, future.exception()))
                    else:
                        log.info('Finished processing deb %s' % deb)
                        ddebs[deb] = future.result()
                processed_packages[url] = True

def is_dbg_package(url):
    name = os.path.splitext(os.path.basename(urlparse.urlparse(url).path))[0].split('_')[0]
    return name.endswith('-dbg')

def main():
    logging.basicConfig(filename='scanpackages.log',
                        level=logging.DEBUG)
    n = int(sys.argv[1]) if len(sys.argv) > 1 else multiprocessing.cpu_count()
    scrape_all_ddebs(n, 'http://ddebs.ubuntu.com/pool/main/')
    scrape_all_ddebs(n, 'http://us.archive.ubuntu.com/ubuntu/pool/main/',
                     is_dbg_package)

if __name__ == '__main__':
    main()
