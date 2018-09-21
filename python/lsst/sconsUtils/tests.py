##
#  @file tests.py
#
#  Control which tests run, and how.
##
import glob
import os
import sys
import pipes
import SCons.Script
from . import state
from . import utils


##
#  @brief A class to control unit tests.
#
#  This class is unchanged from previous versions of sconsUtils, but it will now generally
#  be called via scripts.BasicSConscript.tests().
##
class Control:
    _IGNORE = "IGNORE"
    _EXPECT_FAILURE = "EXPECT_FAILURE"

    ##
    #  @brief Create an object to run tests
    #
    #  @param env           An SCons Environment (almost always lsst.sconsUtils.env).
    #  @param ignoreList    A list of tests that should NOT be run --- useful in conjunction
    #                       with glob patterns.  If a file is listed as "@fileName", the @ is stripped and
    #                       we don't bother to check if fileName exists (useful for machine-generated files).
    #  @param expectedFalures   A dictionary; the keys are tests that are known to fail; the values
    #                           are strings to print.
    #  @param args          A dictionary with testnames as keys, and argument strings as values.
    #                       As scons always runs from the top-level directory, tests has to fiddle with
    #                       paths.  If an argument is a file this is done automatically; if it's e.g.
    #                       just a basename then you have to tell tests that it's really (part of a)
    #                       filename by prefixing the name by "file:".
    #
    #  @param tmpDir        The location of the test outputs.
    #  @param verbose       How chatty you want the test code to be.
    #
    #  @code
    #  tests = lsst.tests.Control(
    #      env,
    #      args={
    #           "MaskIO_1" :      "data/871034p_1_MI_msk.fits",
    #           "MaskedImage_1" : "file:data/871034p_1_MI foo",
    #      },
    #      ignoreList=["Measure_1"],
    #      expectedFailures={"BBox_1": "Problem with single-pixel BBox"}
    # )
    # @endcode
    ##
    def __init__(self, env, ignoreList=None, expectedFailures=None, args=None,
                 tmpDir=".tests", verbose=False):
        if 'PYTHONPATH' in os.environ:
            env.AppendENVPath('PYTHONPATH', os.environ['PYTHONPATH'])

        self._env = env

        self._tmpDir = tmpDir
        self._cwd = os.path.abspath(os.path.curdir)

        # Calculate the absolute path for temp dir if it is relative.
        # This assumes the temp dir is relative to where the tests SConscript
        # file is located. SCons will know how to handle this itself but
        # some options require the code to know where to write things.
        if os.path.isabs(self._tmpDir):
            self._tmpDirAbs = self._tmpDir
        else:
            self._tmpDirAbs = os.path.join(self._cwd, self._tmpDir)

        self._verbose = verbose

        self._info = {}                 # information about processing targets
        if ignoreList:
            for f in ignoreList:
                if f.startswith("@"):  # @dfilename => don't complain if filename doesn't exist
                    f = f[1:]
                else:
                    if not os.path.exists(f):
                        state.log.warn("You're ignoring a non-existent file, %s" % f)
                self._info[f] = (self._IGNORE, None)

        if expectedFailures:
            for f in expectedFailures:
                self._info[f] = (self._EXPECT_FAILURE, expectedFailures[f])

        if args:
            self._args = args           # arguments for tests
        else:
            self._args = {}

        self.runExamples = True                      # should I run the examples?
        try:
            # file is user read/write/executable
            self.runExamples = (os.stat(self._tmpDir).st_mode & 0o700) != 0
        except OSError:
            pass

        if not self.runExamples:
            print("Not running examples; \"chmod 755 %s\" to run them again" % self._tmpDir,
                  file=sys.stderr)

    def args(self, test):
        try:
            return self._args[test]
        except KeyError:
            return ""

    def ignore(self, test):
        if not test.endswith(".py") and \
           len(self._env.Glob(test)) == 0:  # we don't know how to build it
            return True

        ignoreFile = test in self._info and self._info[test][0] == self._IGNORE

        if self._verbose and ignoreFile:
            print("Skipping", test, file=sys.stderr)

        return ignoreFile

    def messages(self, test):
        """Return the messages to be used in case of success/failure; the logicals
        (note that they are strings) tell whether the test is expected to pass"""

        if test in self._info and self._info[test][0] == self._EXPECT_FAILURE:
            msg = self._info[test][1]
            return ("false", "Passed, but should have failed: %s" % msg,
                    "true", "Failed as expected: %s" % msg)
        else:
            return ("true", "passed",
                    "false", "failed")

    def run(self, fileGlob):
        """Create a test target for each file matching the supplied glob.
        """

        if not isinstance(fileGlob, str):  # env.Glob() returns an scons Node
            fileGlob = str(fileGlob)
        targets = []
        if not self.runExamples:
            return targets

        # Determine any library load path values that we have to prepend
        # to the command.
        libpathstr = utils.libraryLoaderEnvironment()

        for f in glob.glob(fileGlob):
            interpreter = ""            # interpreter to run test, if needed

            if f.endswith(".cc"):  # look for executable
                f = os.path.splitext(f)[0]
            else:
                interpreter = "pytest -Wd --junit-xml=${TARGET}.xml"
                interpreter += " --junit-prefix={0}".format(self.junitPrefix())
                interpreter += self._getPytestCoverageCommand()

            if self.ignore(f):
                continue

            target = os.path.join(self._tmpDir, f)

            args = []
            for a in self.args(f).split(" "):
                # if a is a file, make it an absolute name as scons runs from the root directory
                filePrefix = "file:"
                if a.startswith(filePrefix):  # they explicitly said that this was a file
                    a = os.path.join(self._cwd, a[len(filePrefix):])
                else:
                    try:                # see if it's a file
                        os.stat(a)
                        a = os.path.join(self._cwd, a)
                    except OSError:
                        pass

                args += [a]

            (should_pass, passedMsg, should_fail, failedMsg) = self.messages(f)

            # The TRAVIS environment variable is set to allow us to disable
            # the matplotlib font cache. See ticket DM-3856.
            # TODO: Work out better way of solving matplotlib issue in build.
            expandedArgs = " ".join(args)
            result = self._env.Command(target, f, """
            @rm -f ${TARGET}.failed;
            @printf "%%s" 'running ${SOURCES}... ';
            @echo $SOURCES %s > $TARGET; echo >> $TARGET;
            @if %s TRAVIS=1 %s $SOURCES %s >> $TARGET 2>&1; then \
               if ! %s; then mv $TARGET ${TARGET}.failed; fi; \
               echo "%s"; \
            else \
               if ! %s; then mv $TARGET ${TARGET}.failed; fi; \
               echo "%s"; \
            fi;
            """ % (expandedArgs, libpathstr, interpreter, expandedArgs, should_pass,
                   passedMsg, should_fail, failedMsg))

            targets.extend(result)

            self._env.Alias(os.path.basename(target), target)

            self._env.Clean(target, self._tmpDir)

        return targets

    def runPythonTests(self, pyList):
        """Add a single target for testing all python files. pyList is
        a list of nodes corresponding to python test files. The
        IgnoreList is respected when scanning for entries. If pyList
        is None, or an empty list, it uses automated test discovery
        within pytest. This differs from the behavior of scripts.tests()
        where a distinction is made. Returns a list containing a single
        target."""

        if pyList is None:
            pyList = []

        # Determine any library load path values that we have to prepend
        # to the command.
        libpathstr = utils.libraryLoaderEnvironment()

        # Get list of python files with the path included.
        pythonTestFiles = []
        for fileGlob in pyList:
            if not isinstance(fileGlob, str):  # env.Glob() returns an scons Node
                fileGlob = str(fileGlob)
            for f in glob.glob(fileGlob):
                if self.ignore(f):
                    continue
                pythonTestFiles.append(os.path.join(self._cwd, f))

        # Now set up the python testing target
        # We always want to run this with the tests target.
        # We have decided to use pytest caching so that on reruns we only
        # run failed tests.
        lfnfOpt = "none" if 'install' in SCons.Script.COMMAND_LINE_TARGETS else "all"
        interpreter = f"pytest -Wd --lf --lfnf={lfnfOpt}"
        interpreter += " --junit-xml=${TARGET} --session2file=${TARGET}.out"
        interpreter += " --junit-prefix={0}".format(self.junitPrefix())
        interpreter += self._getPytestCoverageCommand()

        target = os.path.join(self._tmpDir, "pytest-{}.xml".format(self._env['eupsProduct']))

        # Work out how many jobs scons has been configured to use
        # and use that number with pytest. This could cause trouble
        # if there are lots of binary tests to run and lots of singles.
        njobs = self._env.GetOption("num_jobs")
        print("Running pytest with {} process{}".format(njobs, "" if njobs == 1 else "es"))
        if njobs > 1:
            # We unambiguously specify the Python interpreter to be used to
            # execute tests. This ensures that all pytest-xdist worker
            # processes refer to the same Python as the xdist master, and
            # hence avoids pollution of ``sys.path`` that can happen when we
            # call the same interpreter by different paths (for example, if
            # the master process calls ``miniconda/bin/python``, and the
            # workers call ``current/bin/python``, the workers will end up
            # with site-packages directories corresponding to both locations
            # on ``sys.path``, even if the one is a symlink to the other).
            executable = os.path.realpath(sys.executable)

            # if there is a space in the executable path we have to use the original
            # method and hope things work okay. This will be rare but without
            # this a space in the path is impossible because of how xdist
            # currently parses the tx option
            interpreter = interpreter + " --max-slave-restart=0"
            if " " not in executable:
                interpreter = (interpreter +
                               " -d --tx={}*popen//python={}".format(njobs, executable))
            else:
                interpreter = interpreter + "  -n {}".format(njobs)

        # Remove target so that we always trigger pytest
        if os.path.exists(target):
            os.unlink(target)

        if not pythonTestFiles:
            print("pytest: automated test discovery mode enabled.")
        else:
            nfiles = len(pythonTestFiles)
            print("pytest: running on {} Python test file{}.".format(nfiles, "" if nfiles == 1 else "s"))

        # If we ran all the test, then copy the previous test
        # execution products to `.all' files so we can retrieve later.
        # If we skip the test (exit code 5), retrieve those `.all' files.
        cmd = """
        @printf "%s\\n" 'running global pytest... ';
        @({2} TRAVIS=1 {0} {1}); \
        export rc="$?"; \
        if [ "$$rc" -eq "0" ]; then \
            echo "Global pytest run completed successfully"; \
            cp ${{TARGET}} ${{TARGET}}.all || true; \
            cp ${{TARGET}}.out ${{TARGET}}.out.all || true; \
        elif [ "$$rc" -eq "5" ]; then \
            echo "Global pytest run completed successfully - no tests ran"; \
            mv ${{TARGET}}.all ${{TARGET}} || true; \
            mv ${{TARGET}}.out.all ${{TARGET}}.out || true; \
        else \
            echo "Global pytest run: failed with $$rc"; \
            mv ${{TARGET}}.out ${{TARGET}}.failed; \
        fi;
        """
        testfiles = " ".join([pipes.quote(p) for p in pythonTestFiles])
        result = self._env.Command(target, None, cmd.format(interpreter, testfiles, libpathstr))

        self._env.Alias(os.path.basename(target), target)
        self._env.Clean(target, self._tmpDir)

        return [result]

    def junitPrefix(self):
        controlVar = "LSST_JUNIT_PREFIX"
        prefix = self._env['eupsProduct']

        if controlVar in os.environ:
            prefix += ".{0}".format(os.environ[controlVar])

        return prefix

    def _getPytestCoverageCommand(self):
        """Form the additional arguments required to enable coverage testing.

        Coverage output files are written using ``${TARGET}`` as a base.

        Returns
        -------
        options : `str`
            String defining the coverage-specific arguments to give to the
            pytest command.
        """

        options = ""

        # Basis for deriving file names
        # We use the magic target from SCons.
        prefix = "${TARGET}"

        # Only report coverage for files in the build tree.
        # If --cov is used full coverage will be reported for all installed
        # code as well, but that is probably a distraction as for this
        # test run we are only interested in coverage of this package.
        # Use "python" instead of "." to remove test files from coverage.
        options += " --cov=."

        # Always enabled branch coverage and terminal summary
        options += " --cov-branch --cov-report=term "

        covfile = "{}-cov-{}.xml".format(prefix, self._env['eupsProduct'])

        # We should specify the output directory explicitly unless the prefix
        # indicates that we are using the SCons target
        if covfile.startswith("${TARGET}"):
            covpath = covfile
        else:
            covpath = os.path.join(self._tmpDirAbs, covfile)
        options += " --cov-report=xml:'{}'".format(covpath)

        # Use the prefix for the HTML output directory
        htmlfile = ":'{}-htmlcov'".format(prefix)
        options += " --cov-report=html{}".format(htmlfile)

        return options
