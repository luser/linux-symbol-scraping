#!/usr/bin/env python

from __future__ import print_function

import boto
import gzip
import io
import logging
import shutil

from boto.s3.key import Key
from scanpackages import scrape_all_ddebs, is_dbg_package

def put_to_s3_compressed(bucket, keyname, filename):
    key = Key(bucket, keyname)
    with io.BytesIO() as b, gzip.GzipFile(mode='wb', fileobj=b) as g, \
         open(filename, 'rb') as f:
        shutil.copyfileobj(f, g)
    b.seek(0)
    headers = {'Content-Encoding': 'gzip'}
    key.set_contents_from_file(b, headers, replace=True)
    key.make_public()

def get_from_s3_compressed(bucket, keyname, filename):
    key = Key(bucket, keyname)
    with io.BytesIO() as b:
        key.get_contents_to_file(b)
        b.seek(0)
        with gzip.GzipFile(mode='rb', fileobj=b) as g, \
             open(filename, 'wb') as f:
            shutil.copyfileobj(g, f)

def main():
    logging.basicConfig(filename='scanpackages.log',
                        format='%(asctime)s %(levelname)s %(module)s %(message)s',
                        level=logging.DEBUG)
    # urllib3 is chatty.
    urllib3_logger = logging.getLogger('urllib3')
    urllib3_logger.setLevel(logging.ERROR)
    log = logging.getLogger('aws-cron')
    conn = boto.connect_s3()
    bucket = conn.get_bucket('ubuntu-build-ids')
    bucket_location = bucket.get_location()
    if bucket_location:
        conn = boto.s3.connect_to_region(bucket_location)
        bucket = conn.get_bucket('ubuntu-build-ids')
    log.info('Fetching ddebs.json from s3...')
    get_from_s3_compressed(bucket, 'ddebs.json', '/tmp/ddebs.json')
    try:
        #scrape_all_ddebs(4, 'http://ddebs.ubuntu.com/pool/main/')
        scrape_all_ddebs(4, 'http://us.archive.ubuntu.com/ubuntu/pool/main/',
                         is_dbg_package)
    finally:
        put_to_s3_compressed(bucket, 'ddebs.json', '/tmp/ddebs.json')


if __name__ == '__main__':
    main()
