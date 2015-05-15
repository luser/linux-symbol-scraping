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

import re
import os
import itertools
import tempfile
import shutil
import urllib

def RunCommand(command):
	"""Helper function to execute shell commands. Putting the functionality here
makes it easy to add diagnostics or make other global changes"""
	print "Executing '%s'" % command
	lines = os.popen(command).readlines()
	for line in lines:
		print line,
	return lines



def GetBuildID(dso):
	"""This function uses 'file' and 'readelf' to see if the specified file is an ELF
file, and if so to try to get its build ID. If no build ID is found then it returns None."""
	# First see if the file is an ELF file -- this avoids error messages
	# from readelf.
	fileType = os.popen("file %s" % dso).read()
	if fileType.count(" ELF ") == 0:
		return None
	
	# Now execute readelf. Note that some older versions don't understand build IDs.
	# If you are running such an old version then you can dump the contents of the
	# build ID section and parse the raw data.
	lines = os.popen("readelf -n %s" % dso).readlines()
	buildID = None
	# We're looking for this output:
	# Build ID: 99c2106c44189e354e1826aa285a0ccf7cbdf726
	for line in lines:
		match = re.match("Build ID: (.*)", line.strip())
		if match:
			buildID = match.groups()[0]
			if len(buildID) == 40:
				return buildID;
	return None



def FillPackageList(data):
	"""This function reads the specified file, which is assumed to be a Packages
file such as those found at http://ddebs.ubuntu.com/dists/precise/main/binary-i386/Packages
and breaks it into individual package description blocks. These blocks are then put into
a dictionary, indexed by the download URL."""
	# Individual package descriptions start with a line that starts with Package: so splitting
	# on this is a simple way to break the file into package blocks.
	packageLabel = "Package: "
	packageCount = 0
	# The URL is the only part of the package that we parse. It is contained in a line
	# that starts with 'Filename: '
	filenameRe = re.compile("Filename: (.*)")
	result = {}
	try:
		for block in data.split("\n" + packageLabel):
			# The splitting process removes the package label from
			# the beginning of all but the first block so let's put it
			# back on.
			if not block.startswith( packageLabel ):
				block = packageLabel + block
			# Look for the package URL
			for line in block.split("\n"):
				line = line.strip()
				match = filenameRe.match(line)
				if match:
					packageURL = match.groups()[0]
					# For some reason the Packages file lists some packages multiple times with
					# the exact same download URL. In every case seen so far the package description
					# is identical, but lets print a message if that stops being true.
					if result.has_key(packageURL) and block.strip() != result[packageURL].strip():
						print "Download URL %s found multiple times with different descriptions." % packageURL
					packageCount += 1
					result[packageURL] = block
	except IOError as e:
		# On the first run the PackagesProcessed file will not exist. We must continue.
		print e
	print "Found %d packages" % (packageCount)
	return result



def BuildIDScanFunc(results, dirname, names):
	"""Callback function for use with os.path.walk. This function looks for files that
contain build IDs and records them as path/buildID tuples in the results array."""
	for name in names:
		path = os.path.join(dirname, name)
		if os.path.isfile(path):
			buildID = GetBuildID(path)
			if buildID:
				results.append((path, buildID))



# Parse the list of processed packages, if present.
enhancedPackageName = "PackagesEnhanced"
processedPackages = {}
if os.path.isfile(enhancedPackageName):
        processedPackages = FillPackageList(open(enhancedPackageName, 'r').read())
print "\n"


# Rewrite the PackagesProcessed file -- this allows any format changes to be
# applied.
output = open(enhancedPackageName, "w")
for packageURL in processedPackages.keys():
	output.write(processedPackages[packageURL] + "\n\n")

# This is a list of Packages files that we will download and process
packageTypes = [ "trusty", "trusty-updates" ]
archs = ["i386", "amd64"]

startDir = os.getcwd()
# Iterate through all of the Packages files that we care about.
for packageType, arch in itertools.product(packageTypes, archs):
	# Download the package list and process it into a dictionary
	# index by the package URLs. The payload is the blob of text
	# associated with the package.
	packageURL = "http://ddebs.ubuntu.com/dists/%s/main/binary-%s/Packages" % (packageType, arch)
	print "Downloading Packages list from %s" % packageURL
        u = urllib.urlopen(packageURL)
	allPackages = FillPackageList(u.read())
	alreadyProcessed = 0
	processed = 0
	for packageNumber, packageURL in enumerate(allPackages.keys()):
		if processedPackages.has_key(packageURL):
			alreadyProcessed += 1
		else:
			# Let's process this package. First we create a temporary directory.
			tempDir = tempfile.mkdtemp()
			os.chdir(tempDir)

			# Then we download the package.
			fullPackageURL = "http://ddebs.ubuntu.com/" + packageURL
			filePart = packageURL[packageURL.rfind("/") + 1:]
			RunCommand("wget -q %s" % fullPackageURL)
			if not os.path.exists(filePart):
				print "Couldn't find %s after downloading from %s" % (filePart, fullPackageURL)
				# Clean up our temporary directory.
				os.chdir(startDir)
				shutil.rmtree( tempDir )
				continue
			processed += 1

			# Now we unpack the package
			RunCommand("ar -x %s" % filePart)

			# Create a subdirectory to unpackage the data file into.
			os.mkdir("contents")
			os.chdir("contents")
			cwdLen = len(os.getcwd())

			# Unpack the data file.
			RunCommand("tar -xf ../data.tar.*z")
			# Now we have unpacked the package into the current directory.
			# Let's iterate over all of the files and look for build IDs.
			results = []
			os.path.walk(os.getcwd(), BuildIDScanFunc, results)
			print "%d build IDs found in %s" % (len(results), packageURL)
			block = allPackages[packageURL]
			for result in results:
				path, buildID = result
				# Slice off our temporary directory path
				path = path[cwdLen:]
				# Print the build ID and the associated download path and install path
				block += "BuildID: %s %s %s\n" % (buildID, path, fullPackageURL)
			block += "\n\n"
			output.write(block)
			print "Processed %d packages, gone through %d of %d." % (processed, packageNumber, len(allPackages))
			# Put some spacing between separate commands
			print ""

			# Clean up our temporary directory.
			os.chdir(startDir)
			shutil.rmtree( tempDir )
	print "%d were already processed, processed %d more." % (alreadyProcessed, processed)
	print "\n"

# Make sure buffers are flushed
output.close()
