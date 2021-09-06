"""Global state for sconsUtils.

This module acts like a singleton, holding all global state for sconsUtils.
This includes the primary Environment object (`lsst.sconsUtils.state.env`),
the message log (`lsst.sconsUtils.state.log`), the command-line variables
object (`lsst.sconsUtils.state.state.opts`), and a dictionary of command-line
targets used to setup aliases, default targets, and dependencies
(`lsst.sconsUtils.state.targets`).  All four of these variables are aliased
to the main `lsst.sconsUtils` scope, so there should be no need for users to
deal with the state module directly.

These are all initialized when the module is imported, but may be modified
by other code (particularly `lsst.sconsUtils.dependencies.configure`).
"""

import os
import re
import shlex

import SCons.Script
import SCons.Conftest
from . import eupsForScons
from .utils import get_conda_prefix, use_conda_compilers

SCons.Script.EnsureSConsVersion(2, 1, 0)

"""A dictionary of SCons aliases and targets.

These are used to setup aliases, default targets, and dependencies by
`lsst.sconsUtils.scripts.BasicSConstruct.finish`.
While one can still use env.Alias to setup aliases (and should for "install"),
putting targets here will generally provide better build-time dependency
handling (like ensuring everything is built before we try to install, and
making sure SCons doesn't rebuild the world before installing).

Users can add additional keys to the dictionary if desired.

Targets should be added by calling extend() or using ``+=`` on the dict
values, to keep the lists of targets from turning into lists-of-lists.
"""
targets = {"doc": [], "tests": [], "lib": [], "python": [], "examples": [], "include": [], "version": [],
           "shebang": []}

env = None
log = None
opts = None


def _initOptions():
    SCons.Script.AddOption('--checkDependencies', dest='checkDependencies',
                           action='store_true', default=False,
                           help="Verify dependencies with autoconf-style tests.")
    SCons.Script.AddOption('--filterWarn', dest='filterWarn', action='store_true', default=False,
                           help="Filter out a class of warnings deemed irrelevant"),
    SCons.Script.AddOption('--force', dest='force', action='store_true', default=False,
                           help="Set to force possibly dangerous behaviours")
    SCons.Script.AddOption('--linkFarmDir', dest='linkFarmDir', action='store', default=None,
                           help="The directory of symbolic links needed to build and use the package")
    SCons.Script.AddOption('--prefix', dest='prefix', action='store', default=False,
                           help="Specify the install destination")
    SCons.Script.AddOption('--setenv', dest='setenv', action='store_true', default=False,
                           help="Treat arguments such as Foo=bar as defining construction variables")
    SCons.Script.AddOption('--tag', dest='tag', action='store', default=None,
                           help="Declare product with this eups tag")
    SCons.Script.AddOption('--verbose', dest='verbose', action='store_true', default=False,
                           help="Print additional messages for debugging.")
    SCons.Script.AddOption('--traceback', dest='traceback', action='store_true', default=False,
                           help="Print full exception tracebacks when errors occur.")
    SCons.Script.AddOption('--no-eups', dest='no_eups', action='store_true', default=False,
                           help="Do not use EUPS for configuration")


def _initLog():
    from . import utils
    global log
    log = utils.Log()


def _initVariables():
    files = []
    if "optfile" in SCons.Script.ARGUMENTS:
        configfile = SCons.Script.ARGUMENTS["optfile"]
        if configfile not in files:
            files.append(configfile)
    for file in files:
        if not os.path.isfile(file):
            log.warn("Warning: Will ignore non-existent options file, %s" % file)
    if "optfile" not in SCons.Script.ARGUMENTS:
        files.append("buildOpts.py")
    global opts
    opts = SCons.Script.Variables(files)
    opts.AddVariables(
        ('archflags', 'Extra architecture specification to add to CC/LINK flags (e.g. -m32)', ''),
        ('cc', 'Choose the compiler to use', ''),
        SCons.Script.BoolVariable('debug', 'Set to enable debugging flags (use --debug)', True),
        ('eupsdb', 'Specify which element of EUPS_PATH should be used', None),
        ('flavor', 'Set the build flavor', None),
        SCons.Script.BoolVariable('force', 'Set to force possibly dangerous behaviours', False),
        ('optfile', 'Specify a file to read default options from', None),
        ('prefix', 'Specify the install destination', None),
        SCons.Script.EnumVariable('opt', 'Set the optimisation level', 3,
                                  allowed_values=('g', '0', '1', '2', '3')),
        SCons.Script.EnumVariable('profile', 'Compile/link for profiler', 0,
                                  allowed_values=('0', '1', 'pg', 'gcov')),
        ('version', 'Specify the version to declare', None),
        ('baseversion', 'Specify the current base version', None),
        ('optFiles', "Specify a list of files that SHOULD be optimized", None),
        ('noOptFiles', "Specify a list of files that should NOT be optimized", None),
        ('macosx_deployment_target', 'Deployment target for Mac OS X', '10.9'),
    )


def _initEnvironment():
    """Construction and basic setup of the state.env variable."""

    ourEnv = {}
    preserveVars = """
      DYLD_LIBRARY_PATH
      EUPS_DIR
      EUPS_LOCK_PID
      EUPS_PATH
      EUPS_SHELL
      EUPS_USERDATA
      LD_LIBRARY_PATH
      PATH
      SHELL
      TEMP
      TERM
      TMP
      TMPDIR
      XPA_PORT
      CONDA_BUILD_SYSROOT
      SDKROOT
    """.split()

    codeCheckerVars = """
      LD_PRELOAD
      CC_LOGGER_FILE
      CC_LOGGER_GCC_LIKE
      CC_LIB_DIR
      CC_DATA_FILES_DIR
      CC_LOGGER_BIN
    """.split()

    for key in preserveVars:
        if key in os.environ:
            ourEnv[key] = os.environ[key]

    # check if running in CodeChecker environment
    if "CC_LOGGER_BIN" in os.environ:
        for key in codeCheckerVars:
            if key in os.environ:
                ourEnv[key] = os.environ[key]

    # Find and propagate EUPS environment variables.
    cfgPath = []
    for k in os.environ:
        m = re.search(r"^(?P<name>\w+)_DIR(?P<extra>_EXTRA)?$", k)
        if not m:
            continue
        cfgPath.append(os.path.join(os.environ[k], "ups"))
        cfgPath.append(os.path.join(os.environ[k], "configs"))
        if m.group("extra"):
            cfgPath.append(os.environ[k])
        else:
            cfgPath.append(os.path.join(os.environ[k], "ups"))
            p = m.group("name")
            varname = eupsForScons.utils.setupEnvNameFor(p)
            if varname in os.environ:
                ourEnv[varname] = os.environ[varname]
                ourEnv[k] = os.environ[k]

    # add <build root>/ups directory to the configuration search path
    # this allows the .cfg file for the package being built to be found without
    # requiring <product name>_DIR to be in the env
    cfgPath.append(os.path.join(SCons.Script.Dir('#').abspath, 'ups'))

    #
    # Add any values marked as export=FOO=XXX[,GOO=YYY] to ourEnv
    #
    exportVal = SCons.Script.ARGUMENTS.pop("export", None)
    if exportVal:
        for kv in exportVal.split(','):
            k, v = kv.split('=')
            ourEnv[k] = v
    global env
    sconsUtilsPath, thisFile = os.path.split(__file__)
    toolPath = os.path.join(sconsUtilsPath, "tools")
    env = SCons.Script.Environment(
        ENV=ourEnv,
        variables=opts,
        toolpath=[toolPath],
        tools=["default", "cuda"]
    )
    env.cfgPath = cfgPath
    #
    # We don't want "lib" inserted at the beginning of loadable module names;
    # we'll import them under their given names.
    #
    env['LDMODULEPREFIX'] = ""
    if env['PLATFORM'] == 'darwin':
        env['LDMODULESUFFIX'] = ".so"
        if not re.search(r"-install_name", str(env['SHLINKFLAGS'])):
            env.Append(SHLINKFLAGS=["-install_name", "${TARGET.file}"])
        if not re.search(r"-headerpad_max_install_names", str(env['SHLINKFLAGS'])):
            env.Append(SHLINKFLAGS=["-Wl,-headerpad_max_install_names"])
        #
        # We want to be explicit about the OS X version we're targeting
        #
        env['ENV']['MACOSX_DEPLOYMENT_TARGET'] = env['macosx_deployment_target']
        # This flag is required for std::variant and std::filesystem
        # on deployment platforms < 10.13 and 10.15
        for flag in ("-D_LIBCPP_DISABLE_AVAILABILITY=1", ):
            env["CFLAGS"].append(flag)
            env["CXXFLAGS"].append(flag)
        log.info("Setting OS X binary compatibility level: %s" % env['ENV']['MACOSX_DEPLOYMENT_TARGET'])
        #
        # For XCode 7.3 we need to explicitly add a trailing slash to library
        # paths. This does not hurt things on older XCodes. We can remove this
        # once XCode is fixed. See Apple radar rdr://25313838
        #
        env['LIBDIRSUFFIX'] = '/'

    if use_conda_compilers():
        _conda_prefix = get_conda_prefix()
        if "LDFLAGS" in os.environ:
            LDFLAGS = shlex.split(os.environ['LDFLAGS'])  # respects quoting!
            LDFLAGS = [v for v in LDFLAGS if v[0:2] != '-L']
            # this one breaks some linking in the eups build
            LDFLAGS = [v for v in LDFLAGS if v != '-Wl,-dead_strip_dylibs']
            env.Append(LIBPATH=["%s/lib" % _conda_prefix])
            env.Append(LINKFLAGS=LDFLAGS)
            env.Append(SHLINKFLAGS=LDFLAGS)

        if "CFLAGS" in os.environ:
            CFLAGS = shlex.split(os.environ['CFLAGS'])  # respects quoting!
            CFLAGS = [v for v in CFLAGS if v[0:2] != '-I']
            env.Append(CCFLAGS=CFLAGS)

        if "CXXFLAGS" in os.environ:
            CXXFLAGS = shlex.split(os.environ['CXXFLAGS'])  # respects quoting!
            CXXFLAGS = [v for v in CXXFLAGS if v[0:2] != '-I']
            CXXFLAGS = [v for v in CXXFLAGS if v[0:5] != '-std=']  # we let LSST set this
            CXXFLAGS = [v for v in CXXFLAGS if v not in CFLAGS]  # conda puts in duplicates
            env.Append(CXXFLAGS=CXXFLAGS)

    #
    # Remove valid options from the arguments
    #
    # SCons Variables do not behave like dicts
    for opt in opts.keys():
        try:
            del SCons.Script.ARGUMENTS[opt]
        except KeyError:
            pass
    #
    # Process those arguments
    #
    for k in ("force", "prefix"):       # these may now be set as options instead of variables
        if SCons.Script.GetOption(k):
            env[k] = SCons.Script.GetOption(k)

    if env['debug']:
        env.Append(CCFLAGS=['-g'])

    #
    # determine if EUPS is present
    #

    # --no-eups overides probing
    # XXX is it possible to test python snippets as a scons action?
    if SCons.Script.GetOption("no_eups"):
        env['no_eups'] = True
    else:
        env['no_eups'] = not eupsForScons.haveEups()

    if not env.GetOption("no_progress"):
        if env['no_eups']:
            log.info('EUPS integration: disabled')
        else:
            log.info('EUPS integration: enabled')

    #
    # Find the eups path, replace 'flavor' in favor of 'PLATFORM' if needed.
    #
    eupsPath = None
    try:
        db = env['eupsdb']
        if 'EUPS_PATH' not in os.environ:
            raise RuntimeError("You can't use eupsdb=XXX without an EUPS_PATH set")
        eupsPath = None
        for d in os.environ['EUPS_PATH'].split(':'):
            if re.search(r"/%s$|^%s/|/%s/" % (db, db, db), d):
                eupsPath = d
                break
        if not eupsPath:
            raise RuntimeError("I cannot find DB \"%s\" in $EUPS_PATH" % db)
    except KeyError:
        if 'EUPS_PATH' in os.environ:
            eupsPath = os.environ['EUPS_PATH'].split(':')[0]
    env['eupsPath'] = eupsPath
    try:
        env['PLATFORM'] = env['flavor']
        del env['flavor']
    except KeyError:
        pass
    #
    # Check arguments
    #
    errorStr = ""
    #
    # Process otherwise unknown arguments.  If setenv is true,
    # set construction variables; otherwise generate an error
    #
    if SCons.Script.GetOption("setenv"):
        for key in SCons.Script.ARGUMENTS:
            env[key] = SCons.Script.Split(SCons.Script.ARGUMENTS[key])
    else:
        for key in SCons.Script.ARGUMENTS:
            errorStr += " %s=%s" % (key, SCons.Script.ARGUMENTS[key])
        if errorStr:
            log.fail("Unprocessed arguments:%s" % errorStr)
    #
    # We need a binary name, not just "Posix"
    #
    env['eupsFlavor'] = eupsForScons.flavor()


_configured = False


def _configureCommon():
    """Configuration checks for the compiler, platform, and standard
    libraries."""
    global _configured
    if _configured:
        return
    _configured = True

    def ClassifyCc(context):
        """Return a pair of string identifying the compiler in use.

        Parameters
        ----------
        context : context
            Context.

        Returns
        -------
        compiler : `str`
            Compiler to use, or "unknown".
        version : `str`
            Compiler version or "unknown".
        """
        versionNameList = (
            (r"gcc(?:\-.+)? +\(.+\) +([0-9.a-zA-Z]+)", "gcc"),
            (r"gnu-cc(?:\-.+)? +\(.+\) +([0-9.a-zA-Z]+)", "gcc"),  # catch the conda-build compiler on linux
            (r"\(GCC\) +([0-9.a-zA-Z]+) ", "gcc"),
            (r"LLVM +version +([0-9.a-zA-Z]+) ", "clang"),  # clang on Mac
            (r"clang +version +([0-9.a-zA-Z]+) ", "clang"),  # clang on linux or clang w/ conda on Mac
            (r"\(ICC\) +([0-9.a-zA-Z]+) ", "icc"),
            (r"cc \(Ubuntu +([0-9\~\-.a-zA-Z]+)\)", "gcc"),  # gcc on Ubuntu (not always caught by #3 above)
        )

        context.Message("Checking who built the CC compiler...")
        result = context.TryAction(SCons.Script.Action(r"$CC --version > $TARGET"))
        ccVersDumpOK, ccVersDump = result[0:2]
        if ccVersDumpOK:
            for reStr, compilerName in versionNameList:
                match = re.search(reStr, ccVersDump)
                if match:
                    compilerVersion = match.groups()[0]
                    context.Result("%s=%s" % (compilerName, compilerVersion))
                    return (compilerName, compilerVersion)
        context.Result("unknown")
        return ("unknown", "unknown")

    if env.GetOption("clean") or env.GetOption("no_exec") or env.GetOption("help"):
        env.whichCc = "unknown"         # who cares? We're cleaning/not execing, not building
    else:
        if use_conda_compilers():
            # conda-build expects you to use the compilers as-is
            env['CC'] = os.environ['CC']
            env['CXX'] = os.environ['CXX']

            conf = env.Configure(custom_tests={'ClassifyCc': ClassifyCc})
            env.whichCc, env.ccVersion = conf.ClassifyCc()
            if not env.GetOption("no_progress"):
                log.info("CC is **CONDA** %s version %s" % (env.whichCc, env.ccVersion))
            conf.Finish()
        else:
            if env['cc'] != '':
                CC = CXX = None
                if re.search(r"^gcc(-\d+(\.\d+)*)?( |$)", env['cc']):
                    CC = env['cc']
                    CXX = re.sub(r"^gcc", "g++", CC)
                elif re.search(r"^icc( |$)", env['cc']):
                    CC = env['cc']
                    CXX = re.sub(r"^icc", "icpc", CC)
                elif re.search(r"^clang( |$)", env['cc']):
                    CC = env['cc']
                    CXX = re.sub(r"^clang", "clang++", CC)
                elif re.search(r"^cc( |$)", env['cc']):
                    CC = env['cc']
                    CXX = re.sub(r"^cc", "c++", CC)
                else:
                    log.fail("Unrecognised compiler: %s" % env['cc'])
                env0 = SCons.Script.Environment()
                if CC and env['CC'] == env0['CC']:
                    env['CC'] = CC
                if CC and env['CXX'] == env0['CXX']:
                    env['CXX'] = CXX
            conf = env.Configure(custom_tests={'ClassifyCc': ClassifyCc})
            env.whichCc, env.ccVersion = conf.ClassifyCc()

            # If we have picked up a default compiler called gcc that is really
            # clang, we call it clang to avoid confusion (gcc on macOS has
            # subtly different options)
            if not env['cc'] and env.whichCc == "clang" and env['CC'] == "gcc":
                env['CC'] = "clang"
                env['CXX'] = "clang++"

            if not env.GetOption("no_progress"):
                log.info("CC is %s version %s" % (env.whichCc, env.ccVersion))
            conf.Finish()

    #
    # Compiler flags, including CCFLAGS for C and C++ and CXXFLAGS for C++ only
    #
    ARCHFLAGS = os.environ.get("ARCHFLAGS", env.get('archflags'))
    if ARCHFLAGS:
        env.Append(CCFLAGS=ARCHFLAGS.split())
        env.Append(LINKFLAGS=ARCHFLAGS.split())
    # We'll add warning and optimisation options last
    if env['profile'] == '1' or env['profile'] == "pg":
        env.Append(CCFLAGS=['-pg'])
        env.Append(LINKFLAGS=['-pg'])
    elif env['profile'] == 'gcov':
        env.Append(CCFLAGS='--coverage')
        env.Append(LINKFLAGS='--coverage')

    #
    # Enable C++20 support
    #
    if not (env.GetOption("clean") or env.GetOption("help") or env.GetOption("no_exec")):
        if not env.GetOption("no_progress"):
            log.info("Checking for C++20 support")
        conf = env.Configure()
        for cpp20Arg in ("-std=%s" % (val,) for val in ("c++20",)):
            conf.env = env.Clone()
            conf.env.Append(CXXFLAGS=cpp20Arg)
            if conf.CheckCXX():
                env.Append(CXXFLAGS=cpp20Arg)
                if not env.GetOption("no_progress"):
                    log.info("C++20 supported with %r" % (cpp20Arg,))
                break
        else:
            log.fail("C++20 extensions could not be enabled for compiler %r" % env.whichCc)
        conf.Finish()

    #
    # Byte order
    #
    import socket
    if socket.htons(1) != 1:
        env.Append(CCFLAGS=['-DLSST_LITTLE_ENDIAN=1'])
    #
    # If we're linking to libraries that themselves linked to
    # shareable libraries we need to do something special.
    #
    if (re.search(r"^(Linux|Linux64)$", env["eupsFlavor"]) and "LD_LIBRARY_PATH" in os.environ):
        env.Append(LINKFLAGS=["-Wl,-rpath-link"])
        env.Append(LINKFLAGS=["-Wl,%s" % os.environ["LD_LIBRARY_PATH"]])
    #
    # Set the optimization level.
    #
    if env['opt']:
        env["CCFLAGS"] = [o for o in env["CCFLAGS"] if not re.search(r"^-O(\d|s|g|fast)$", o)]
        env.MergeFlags('-O%s' % env['opt'])
    #
    # Set compiler-specific warning flags.
    #
    if env.whichCc == "clang":
        env.Append(CCFLAGS=['-Wall'])
        env["CCFLAGS"] = [o for o in env["CCFLAGS"] if not re.search(r"^-mno-fused-madd$", o)]

        ignoreWarnings = {
            "unused-function": 'boost::regex has functions in anon namespaces in headers',
        }
        filterWarnings = {
            "attributes": "clang pretends to be g++, but complains about g++ attributes such as flatten",
            "char-subscripts": 'seems innocous enough, and is used by boost',
            "constant-logical-operand": "Used by eigen 2.0.15. Should get this fixed",
            "format-security": "format string is not a string literal",
            "mismatched-tags": "mixed class and struct.  Used by gcc 4.2 RTL and eigen 2.0.15",
            "parentheses": "equality comparison with extraneous parentheses",
            "shorten-64-to-32": "implicit conversion loses integer precision",
            "self-assign": "x = x",
            "unused-local-typedefs": "unused typedef",  # lots from boost
            "unknown-pragmas": "unknown pragma ignored",
            "deprecated-register": "register is deprecated",
        }
        for k in ignoreWarnings:
            env.Append(CCFLAGS=["-Wno-%s" % k])
        if env.GetOption('filterWarn'):
            for k in filterWarnings:
                env.Append(CCFLAGS=["-Wno-%s" % k])
    elif env.whichCc == "gcc":
        env.Append(CCFLAGS=['-Wall'])
        env.Append(CCFLAGS=["-Wno-unknown-pragmas"])  # we don't want complaints about icc/clang pragmas
        env.Append(CCFLAGS=["-Wno-unused-local-typedefs"])  # boost generates a lot of these
    elif env.whichCc == "icc":
        env.Append(CCFLAGS=['-Wall'])
        filterWarnings = {
            21: 'type qualifiers are meaningless in this declaration',
            68: 'integer conversion resulted in a change of sign',
            111: 'statement is unreachable',
            191: 'type qualifier is meaningless on cast type',
            193: 'zero used for undefined preprocessing identifier "SYMB"',
            279: 'controlling expression is constant',
            304: 'access control not specified ("public" by default)',  # comes from boost
            383: 'value copied to temporary, reference to temporary used',
            # 424: 'Extra ";" ignored',
            444: 'destructor for base class "CLASS" is not virtual',
            981: 'operands are evaluated in unspecified order',
            1418: 'external function definition with no prior declaration',
            1419: 'external declaration in primary source file',
            1572: 'floating-point equality and inequality comparisons are unreliable',
            1720: 'function "FUNC" has no corresponding member operator delete'
                  '(to be called if an exception is thrown during initialization of an allocated object)',
            2259: 'non-pointer conversion from "int" to "float" may lose significant bits',
        }
        if env.GetOption('filterWarn'):
            env.Append(CCFLAGS=["-wd%s" % (",".join([str(k) for k in filterWarnings]))])
        # Workaround intel bug; cf. RHL's intel bug report 580167
        env.Append(LINKFLAGS=["-Wl,-no_compact_unwind", "-wd,11015"])
    #
    # Disable link-time-optimization on GCC, for compatibility with conda
    # binaries
    #
    if env.whichCc == "gcc":
        env.Append(CCFLAGS=['-fno-lto'])
        env.Append(LINKFLAGS=['-fno-lto'])


def _saveState():
    """Save state such as optimization level used.

    Notes
    -----
    The scons mailing lists were unable to tell RHL how to get this back
    from ``.sconsign.dblite``.
    """

    if env.GetOption("clean"):
        return

    # Python 2 uses ConfigParser, Python 3 uses configparser
    try:
        from configparser import ConfigParser
    except ImportError:
        from ConfigParser import ConfigParser

    config = ConfigParser()
    config.add_section('Build')
    config.set('Build', 'cc', env.whichCc)
    if env['opt']:
        config.set('Build', 'opt', env['opt'])

    try:
        confFile = os.path.join(env.Dir(env["CONFIGUREDIR"]).abspath, "build.cfg")
        with open(confFile, 'w') as configfile:
            config.write(configfile)
    except Exception as e:
        log.warn("Unexpected exception in _saveState: %s" % e)


_initOptions()
_initLog()
_initVariables()
_initEnvironment()
