#!/usr/bin/env python

"""Produces a report of all assertions in the MongoDB server codebase.

Parses .cpp files for assertions and verifies assertion codes are distinct.
Optionally replaces zero codes in source code with new distinct values.
"""

import bisect
import os
import sys
import utils
from collections import defaultdict, namedtuple
from optparse import OptionParser

try:
    import regex as re
except ImportError:
    print("*** Run 'pip2 install --user regex' to speed up error code checking")
    import re

ASSERT_NAMES = [ "uassert" , "massert", "fassert", "fassertFailed" ]
MINIMUM_CODE = 10000

codes = []

# Each AssertLocation identifies the C++ source location of an assertion
AssertLocation = namedtuple( "AssertLocation", ['sourceFile', 'byteOffset', 'lines', 'code'] )


# Of historical interest only
def assignErrorCodes():
    cur = MINIMUM_CODE
    for root in ASSERT_NAMES:
        for x in utils.getAllSourceFiles():
            print( x )
            didAnything = False
            fixed = ""
            for line in open( x ):
                s = line.partition( root + "(" )
                if s[1] == "" or line.startswith( "#define " + root):
                    fixed += line
                    continue
                fixed += s[0] + root + "( " + str( cur ) + " , " + s[2]
                cur = cur + 1
                didAnything = True
            if didAnything:
                out = open( x , 'w' )
                out.write( fixed )
                out.close()


def parseSourceFiles( callback ):
    """Walks MongoDB sourcefiles and invokes callback for each AssertLocation found."""

    quick = [ "assert" , "Exception"]

    patterns = [
        re.compile( r"(?:u|m(?:sg)?)asser(?:t|ted)(?:NoTrace)?\s*\(\s*(\d+)", re.MULTILINE ) ,
        re.compile( r"(?:DB|Assertion)Exception\s*\(\s*(\d+)", re.MULTILINE ),
        re.compile( r"fassert(?:Failed)?(?:WithStatus)?(?:NoTrace)?(?:StatusOK)?\s*\(\s*(\d+)",
                    re.MULTILINE ),
    ]

    bad = [ re.compile( r"^\s*assert *\(" ) ]

    for sourceFile in utils.getAllSourceFiles(prefix='src/mongo/'):
        with open(sourceFile) as f:
            text = f.read()

            if not any([zz in text for zz in quick]):
                continue

            # TODO: move check for bad assert type to the linter.
            for b in bad:
                if b.search(text):
                    msg = "Bare assert prohibited. Replace with [umwdf]assert"
                    print( "%s: %s" % (sourceFile, msg) )
                    raise Exception(msg)

            matchiters = [p.finditer(text) for p in patterns]
            for matchiter in matchiters:
                for match in matchiter:
                    code = match.group(1)
                    span = match.span()

                    thisLoc = AssertLocation(sourceFile,
                                             span[1],
                                             text[span[0]:span[1]],
                                             code)

                    callback( thisLoc )

# Converts an absolute position in a file into a line number.
def getLineForPosition(loc, _file_cache={}):
    if loc.sourceFile not in _file_cache:
        with open(loc.sourceFile) as f:
            text = f.read()
            line_offsets = [0]
            for line in text.splitlines(True):
                line_offsets.append(line_offsets[-1] + len(line))
            _file_cache[loc.sourceFile] = line_offsets

    return bisect.bisect(_file_cache[loc.sourceFile], loc.byteOffset)

def isTerminated( lines ):
    """Given .cpp/.h source lines as text, determine if assert is terminated."""
    x = " ".join(lines)
    return ';' in x \
        or x.count('(') - x.count(')') <= 0


def getNextCode():
    """Finds next unused assertion code.

    Called by: SConstruct and main()
    Since SConstruct calls us, codes[] must be global OR WE REPARSE EVERYTHING
    """
    if not len(codes) > 0:
        readErrorCodes()

    highest = reduce( lambda x, y: max(int(x), int(y)),
                      (loc.code for loc in codes) )
    return highest + 1


def checkErrorCodes():
    """SConstruct expects a boolean response from this function.
    """
    (codes, errors) = readErrorCodes()
    return len( errors ) == 0


def readErrorCodes():
    """Defines callback, calls parseSourceFiles() with callback,
    and saves matches to global codes list.
    """
    seen = {}
    errors = []
    dups = defaultdict(list)

    # define callback
    def checkDups( assertLoc ):
        codes.append( assertLoc )
        code = assertLoc.code

        if not code in seen:
            seen[code] = assertLoc
        else:
            if not code in dups:
                # on first duplicate, add original to dups, errors
                dups[code].append( seen[code] )
                errors.append( seen[code] )

            dups[code].append( assertLoc )
            errors.append( assertLoc )

    parseSourceFiles( checkDups )

    if seen.has_key("0"):
        code = "0"
        bad = seen[code]
        errors.append( bad )
        print( "ZERO_CODE:" )
        print( "  %s:%d:%s" % (bad.sourceFile, getLineForPosition(bad), bad.lines) )

    for code, locations in dups.items():
        print( "DUPLICATE IDS: %s" % code )
        for loc in locations:
            print( "  %s:%d:%s" % (loc.sourceFile, getLineForPosition(loc), loc.lines) )

    return (codes, errors)


def replaceBadCodes( errors, nextCode ):
    """Modifies C++ source files to replace invalid assertion codes.
    For now, we only modify zero codes.

    Args:
        errors: list of AssertLocation
        nextCode: int, next non-conflicting assertion code
    """
    zero_errors = [e for e in errors if int(e.code) == 0]
    skip_errors = [e for e in errors if int(e.code) != 0]

    for loc in skip_errors:
        print ("SKIPPING NONZERO code=%s: %s:%s"
                % (loc.code, loc.sourceFile, getLineForPosition(loc)))

    # Dedupe, sort, and reverse so we don't have to update offsets as we go.
    for assertLoc in reversed(sorted(set(zero_errors))):
        (sourceFile, byteOffset, lines, code) = assertLoc
        lineNum = getLineForPosition(assertLoc)
        print "UPDATING_FILE: %s:%s" % (sourceFile, lineNum)

        ln = lineNum - 1

        with open(sourceFile, 'r+') as f:
            print "LINE_%d_BEFORE:%s" % (lineNum, f.readlines()[ln].rstrip())

            f.seek(0)
            text = f.read()
            assert text[byteOffset-1] == '0'
            f.seek(0)
            f.write(text[:byteOffset-1])
            f.write(str(nextCode))
            f.write(text[byteOffset:])
            f.seek(0)

            print "LINE_%d_AFTER :%s" % (lineNum, f.readlines()[ln].rstrip())
        nextCode += 1


def getBestMessage( lines , codeStr ):
    """Extracts message from one AssertionLocation.lines entry

    Args:
        lines: list of contiguous C++ source lines
        codeStr: assertion code found in first line
    """
    line = lines if isinstance(lines, str) else " ".join(lines)

    err = line.partition( codeStr )[2]
    if not err:
        return ""

    # Trim to outer quotes
    m = re.search(r'"(.*)"', err)
    if not m:
        return ""
    err = m.group(1)

    # Trim inner quote pairs
    err = re.sub(r'" +"', '', err)
    err = re.sub(r'" *<< *"', '', err)
    err = re.sub(r'" *<<[^<]+<< *"', '<X>', err)
    err = re.sub(r'" *\+[^+]+\+ *"', '<X>', err)

    # Trim escaped quotes
    err = re.sub(r'\\"', '', err)

    # Iff doublequote still present, trim that and any trailing text
    err = re.sub(r'".*$', '', err)

    return err.strip()

def main():
    parser = OptionParser(description=__doc__.strip())
    parser.add_option("--fix", dest="replace",
                      action="store_true", default=False,
                      help="Fix zero codes in source files [default: %default]")
    parser.add_option("-q", "--quiet", dest="quiet",
                      action="store_true", default=False,
                      help="Suppress output on success [default: %default]")
    (options, args) = parser.parse_args()

    (codes, errors) = readErrorCodes()
    ok = len(errors) == 0

    if ok and options.quiet:
        return

    next = getNextCode()

    print("ok: %s" % ok)
    print("next: %s" % next)

    if ok:
        sys.exit(0)
    elif options.replace:
        replaceBadCodes(errors, next)
    else:
        print ERROR_HELP
        sys.exit(1)


ERROR_HELP = """
ERRORS DETECTED. To correct, run "buildscripts/errorcodes.py --fix" to replace zero codes.
Other errors require manual correction.
"""

if __name__ == "__main__":
    main()
