"""
:mod:`hashdist.core.run_job` --- Job execution in controlled environment
========================================================================

Executes a set of commands in a controlled environment, determined by
a JSON job specification. This is used as the "build" section of ``build.json``,
the "install" section of ``artifact.json``, and so on.

The job spec may not completely specify the job environment because it
is usually a building block of other specs which may imply certain
additional environment variables. E.g., during a build, ``$ARTIFACT``
and ``$BUILD`` are defined even if they are never mentioned here.


Job specification
-----------------

The job spec is a document that contains what's needed to set up a
controlled environment and run the commands. The idea is to be able
to reproduce a job run, and hash the job spec. Example:

.. code-block:: python
    
    {
        "import" : [
            {"ref": "BASH", "id": "virtual:bash"},
            {"ref": "MAKE", "id": "virtual:gnu-make/3+"},
            {"ref": "ZLIB", "id": "zlib/2d4kh7hw4uvml67q7npltyaau5xmn4pc"},
            {"ref": "UNIX", "id": "virtual:unix"},
            {"ref": "GCC", "id": "gcc/jonykztnjeqm7bxurpjuttsprphbooqt"}
         ],
         "nohash_params" : {
            "NCORES": "4"
         }
         "cwd": "src",
         "commands" : [
             {"prepend_path": "FOOPATH", "value": "$ARTIFACT/bin"},
             {"set": "INCLUDE_FROB", "value": "0"},
             {"cmd": ["pkg-config", "--cflags", "foo"], "to_var": "CFLAGS"},
             {"cmd": ["./configure", "--prefix=$ARTIFACT", "--foo-setting=$FOO"]}
             {"cmd": ["bash", "$in0"],
              "inputs": [
                  {"text": [
                      "[\"$RUN_FOO\" != \"\" ] && ./foo"
                      "make",
                      "make install"
                  ]}
             }
         ],
    }


      
Job spec root node
------------------

The root node is also a command node, as described below, but has two
extra allowed keys:

**import**:
    The artifacts needed in the environment for the run. After the
    job has run they have no effect (i.e., they do not
    affect garbage collection or run-time dependencies of a build,
    for instance). The list is ordered and earlier entries are imported
    before latter ones.

    * **id**: The artifact ID. If the value is prepended with
      ``"virtual:"``, the ID is a virtual ID, used so that the real
      one does not contribute to the hash. See section on virtual
      imports below.

    * **ref**: A name to use to inject information of this dependency
      into the environment. Above, ``$zlib`` will be the
      absolute path to the ``zlib`` artifact, and ``$zlib_id`` will be
      the full artifact ID. This can be set to `None` in order to not
      set any environment variables for the artifact.

    * **in_env**: Whether to add the environment variables of the
      artifact (typically ``$PATH`` if there is a ``bin`` sub-directory
      and so on). Otherwise the artifact can only be used through the
      variables ``ref`` sets up. Defaults to `True`.

**nohash_params**:
    Initial set of environment variables that do not contribute to the
    hash. Should only be used when one is willing to trust that the
    value does not affect the build result in any way. E.g.,
    parallelization flags, paths to manually downloaded binary
    installers, etc.

When executing, the environment is set up as follows:

    * Environment is cleared (``os.environ`` has no effect)
    * The initial environment provided by caller (e.g.,
      :class:`.BuildStore` provides `$ARTIFACT` and `$BUILD`) is loaded
    * The `nohash_params` dict (if present) is loaded into the env
    * The `import` section is processed
    * Commands executed (which may modify env)

Command node
------------

The command nodes is essentially a script language, but lacks any form
of control flow. The purpose is to control the environment, and then
quickly dispatch to a script in a real programming language.

Also, the overall flow of commands to set up the build environment are
typically generated by a pipeline from a package definition, and
generating a text script in a pipeline is no fun.

See example above for basic script structure. Rules:

 * Every item in the job is either a `cmd` or a `commands` or a `hit`, i.e.
   those keys are mutually exclusive and defines the node type.

 * `commands`: Push a new environment and current directory to stack,
   execute sub-commands, and pop the stack.

 * `cmd`: The list is passed straight to :func:`subprocess.Popen` as is
   (after variable substiution). I.e., no quoting, no globbing.

 * `hit`: executes the `hit` tool *in-process*. It acts like `cmd` otherwise,
   e.g., `to_var` works.

 * `set`, `prepend/append_path`, `prepend/append_flag`: Change environment
   variables, inserting the value specified by the `value` key, using
   variable substitution as explained below. `set` simply overwrites
   variable, while the others modify path/flag-style variables, using the
   `os.path.patsep` for `prepend/append_path` and a space for `prepend/append_flag`.

 * `cwd` modifies working directory for the command in question,
   or the scope if it is a scope. The `cwd` acts just like the regular
   `cd` command, i.e., you can do things like ``"cwd": ".."``

 * `files` specifies files that are dumped to temporary files and made available
   as `$in0`, `$in1` and so on. Each file has the form ``{typestr: value}``,
   where `typestr` means:
   
       * ``text``: `value` should be a list of strings which are joined by newlines
       * ``string``: `value` is dumped verbatim to file
       * ``json``: `value` is any JSON document, which is serialized to the file

 * stdout and stderr will be logged, except if `to_var` or
   `append_to_file` is present in which case the stdout is capture to
   an environment variable or redirected in append-mode to file, respectively. (In
   the former case, the resulting string undergoes `strip()`, and is
   then available for the following commands within the same scope.)

 * Variable substitution is performed the following places: The `cmd`,
   `value` of `set` etc., the `cwd`, `stdout_to_file`.  The syntax is
   ``$CFLAGS`` and ``${CFLAGS}``. ``\$`` is an escape for ``$``,
   ``\\`` is an escape for ``\``, other escapes not currently supported
   and ``\`` will carry through unmodified.


For the `hit` tool, in addition to what is listed in ``hit
--help``, the following special command is available for interacting
with the job runner:

 * ``hit logpipe HEADING LEVEL``: Creates a new Unix FIFO and prints
   its name to standard output (it will be removed once the job
   terminates). The job runner will poll the pipe and print
   anything written to it nicely formatted to the log with the given
   heading and log level (the latter is one of ``DEBUG``, ``INFO``,
   ``WARNING``, ``ERROR``).

.. note::

    ``hit`` is not automatically available in the environment in general
    (in launched scripts etc.), for that, see :mod:`hashdist.core.hit_recipe`.
    ``hit logpipe`` is currently not supported outside of the job spec
    at all (this could be supported through RPC with the job runner, but the
    gain seems very slight).




Virtual imports
---------------

Some times one do not wish some imports to become part of the hash.
For instance, if the ``cp`` tool is used in the job, one is normally
ready to trust that the result wouldn't have been different if a newer
version of the ``cp`` tool was used instead.

Virtual imports, such as ``virtual:unix`` in the example above, are
used so that the hash depends on a user-defined string rather than the
artifact contents. If a bug in ``cp`` is indeed discovered, one can
change the user-defined string (e.g, ``virtual:unix/r2``) in order to
change the hash of the job desc.

.. note::
   One should think about virtual dependencies merely as a tool that gives
   the user control (and responsibility) over when the hash should change.
   They are *not* the primary mechanism for providing software
   from the host; though software from the host will sometimes be
   specified as virtual dependencies.

Reference
---------

"""

import sys
import os
import fcntl
from os.path import join as pjoin
import shutil
import subprocess
from glob import glob
from string import Template
from pprint import pformat
import tempfile
import errno
import select
from StringIO import StringIO
import json
from pprint import pprint

from ..hdist_logging import CRITICAL, ERROR, WARNING, INFO, DEBUG

from .common import working_directory

LOG_PIPE_BUFSIZE = 4096


class InvalidJobSpecError(ValueError):
    pass

class JobFailedError(RuntimeError):
    pass

def run_job(logger, build_store, job_spec, override_env, virtuals, cwd, config, temp_dir=None):
    """Runs a job in a controlled environment, according to rules documented above.

    Parameters
    ----------

    logger : Logger

    build_store : BuildStore
        BuildStore to find referenced artifacts in.

    job_spec : document
        See above

    override_env : dict
        Extra environment variables not present in job_spec, these will be added
        last and overwrite existing ones.

    virtuals : dict
        Maps virtual artifact to real artifact IDs.

    cwd : str
        The starting working directory of the job. Currently this
        cannot be changed (though a ``cd`` command may be implemented in
        the future if necesarry)

    config : dict
        Configuration from :mod:`hashdist.core.config`. This will be
        serialied and put into the HDIST_CONFIG environment variable
        for use by ``hit``.

    temp_dir : str (optional)
        A temporary directory for use by the job runner. Files will be left in the
        dir after execution.

    Returns
    -------

    out_env: dict
        The environment after the last command that was run (regardless
        of scoping/nesting).
        
    """
    job_spec = canonicalize_job_spec(job_spec)
    env = get_imports_env(build_store, virtuals, job_spec['import'])
    env.update(job_spec['nohash_params'])
    env.update(override_env)
    env['HDIST_VIRTUALS'] = pack_virtuals_envvar(virtuals)
    env['HDIST_CONFIG'] = json.dumps(config, separators=(',', ':'))
    executor = CommandTreeExecution(logger, temp_dir)
    try:
        executor.run_node(job_spec, env, cwd, ())
    finally:
        executor.close()
    return executor.last_env

def canonicalize_job_spec(job_spec):
    """Returns a copy of job_spec with default values filled in.

    Also performs a tiny bit of validation.
    """
    def canonicalize_import(item):
        item = dict(item)
        item.setdefault('in_env', True)
        if item.setdefault('ref', None) == '':
            raise ValueError('Empty ref should be None, not ""')
        return item

    result = dict(job_spec)
    result['import'] = [
        canonicalize_import(item) for item in result.get('import', ())]
    result.setdefault("nohash_params", {})
    return result
    
def substitute(x, env):
    """
    Substitute environment variable into a string following the rules
    documented above.

    Raises KeyError if an unreferenced variable is not present in env
    (``$$`` always raises KeyError)
    """
    if '$$' in x:
        # it's the escape character of string.Template, hence the special case
        raise KeyError('$$ is not allowed (no variable can be named $): %s' % x)
    x = x.replace(r'\\\\', r'\\')
    x = x.replace(r'\$', r'$$')
    return Template(x).substitute(env)

def get_imports_env(build_store, virtuals, imports):
    """
    Sets up environment variables given by the 'import' section
    of the job spec (see above).

    Parameters
    ----------

    build_store : BuildStore object
        Build store to look up artifacts in

    virtuals : dict
        Maps virtual artifact IDs (including "virtual:" prefix) to concrete
        artifact IDs.

    imports : list
        'import' section of job spec document as documented above.

    Returns
    -------

    env : dict
        Environment variables to set containing variables for the dependency
        artifacts
    """
    env = {}
    # Build the environment variables due to imports, and complain if
    # any dependency is not built

    PATH = []
    HDIST_CFLAGS = []
    HDIST_LDFLAGS = []
    HDIST_IMPORT = []
    HDIST_IMPORT_PATHS = []
    
    for dep in imports:
        dep_ref = dep['ref']
        dep_id = dep['id']
        HDIST_IMPORT.append(dep_id)

        # Resolutions of virtual imports should be provided by the user
        # at the time of build
        if dep_id.startswith('virtual:'):
            try:
                dep_id = virtuals[dep_id]
            except KeyError:
                raise ValueError('build spec contained a virtual dependency "%s" that was not '
                                 'provided' % dep_id)

        dep_dir = build_store.resolve(dep_id)
        if dep_dir is None:
            raise InvalidJobSpecError('Dependency "%s"="%s" not already built, please build it first' %
                                        (dep_ref, dep_id))
        HDIST_IMPORT_PATHS.append(dep_dir)

        if dep_ref is not None:
            env[dep_ref] = dep_dir
            env['%s_ID' % dep_ref] = dep_id

        if dep['in_env']:
            bin_dir = pjoin(dep_dir, 'bin')
            if os.path.exists(bin_dir):
                PATH.append(bin_dir)

            libdirs = [pjoin(dep_dir, x) for x in ('lib', 'lib32', 'lib64')]
            libdirs = [x for x in libdirs if os.path.exists(x)]
            if len(libdirs) == 1:
                HDIST_LDFLAGS.append('-L' + libdirs[0])
                HDIST_LDFLAGS.append('-Wl,-R,' + libdirs[0])
            elif len(libdirs) > 1:
                raise InvalidJobSpecError('in_hdist_compiler_paths set for artifact %s with '
                                          'more than one library dir (%r)' % (dep_id, libdirs))

            incdir = pjoin(dep_dir, 'include')
            if os.path.exists(incdir):
                HDIST_CFLAGS.append('-I' + incdir)

    env['PATH'] = os.path.pathsep.join(PATH)
    env['HDIST_CFLAGS'] = ' '.join(HDIST_CFLAGS)
    env['HDIST_LDFLAGS'] = ' '.join(HDIST_LDFLAGS)
    env['HDIST_IMPORT'] = ' '.join(HDIST_IMPORT)
    env['HDIST_IMPORT_PATHS'] = ' '.join(HDIST_IMPORT_PATHS)
    return env
    
def pack_virtuals_envvar(virtuals):
    return ';'.join('%s=%s' % tup for tup in sorted(virtuals.items()))

def unpack_virtuals_envvar(x):
    if not x:
        return {}
    else:
        return dict(tuple(tup.split('=')) for tup in x.split(';'))

class CommandTreeExecution(object):
    """
    Class for maintaining state (in particular logging pipes) while
    executing script. Note that the environment is passed around as
    parameters instead.

    Executing :meth:`run` multiple times amounts to executing
    different variable scopes (but with same logging pipes set up).
    
    Parameters
    ----------

    logger : Logger

    rpc_dir : str
        A temporary directory on a local filesystem. Currently used for creating
        pipes with the "hit logpipe" command.
    """
    
    def __init__(self, logger, temp_dir=None):
        self.logger = logger
        self.log_fifo_filenames = {}
        if temp_dir is None:
            self.rm_temp_dir = True
            temp_dir = tempfile.mkdtemp(prefix='hashdist-run-job-')
        else:
            if os.listdir(temp_dir) != []:
                raise Exception('temp_dir must be an empty directory')
            self.rm_temp_dir = False
        self.temp_dir = temp_dir
        self.last_env, self.last_cwd = None, None

    def close(self):
        """Removes log FIFOs; should always be called when one is done
        """
        if self.rm_temp_dir:
            shutil.rmtree(self.temp_dir)

    def substitute(self, x, env):
        try:
            return substitute(x, env)
        except KeyError, e:
            msg = 'No such environment variable: %s' % str(e)
            self.logger.error(msg)
            raise ValueError(msg)

    def dump_inputs(self, inputs, node_pos):
        """
        Handles the 'inputs' attribute of a node by dumping to temporary files.

        Returns
        -------

        A dict with environment variables that can be used to update `env`,
        containing ``$in0``, ...
        """
        env = {}
        for i, input in enumerate(inputs):
            if not isinstance(input, dict):
                raise TypeError("input entries should be dict")
            name = 'in%d' % i
            filename = '_'.join(str(x) for x in node_pos) + '_' + name
            filename = pjoin(self.temp_dir, filename)

            if sum(['text' in input, 'json' in input, 'string' in input]) != 1:
                raise ValueError("Need exactly one of 'text', 'json', 'string' in %r" % input)
            if 'text' in input:
                value = '\n'.join(input['text'])
            elif 'string' in input:
                value = input['string']
            elif 'json' in input:
                value = json.dumps(input['json'], indent=4)
                filename += '.json'
            else:
                assert False

            with open(filename, 'w') as f:
                f.write(value)
            env[name] = filename
        return env

    def run_node(self, node, env, cwd, node_pos):
        """Executes a script node and its children

        Parameters
        ----------
        node : dict
            A command node

        env : dict
            The environment (will be modified)

        cwd : str
            Working directory

        node_pos : tuple
            Tuple of the "path" to this command node; e.g., (0, 1) for second
            command in first group.
        """
        type_keys = ['commands', 'cmd', 'hit', 'set', 'prepend_path', 'append_path',
                     'prepend_flag', 'append_flag']
        type = None
        for t in type_keys:
            if t in node:
                if type is not None:
                    msg = 'Several action types present: %s and %s' % (type, t)
                    self.logger.error(msg)
                    raise InvalidJobSpecError(msg)
                type = t
        if type is None:
            msg = 'Node must have one of the keys %s' % ', '.join(type_keys)
            self.logger.error(msg)
            raise InvalidJobSpecError(msg)
        getattr(self, 'handle_%s' % type)(node, env, cwd, node_pos)

    def handle_set(self, node, env, cwd, node_pos):
        self.handle_env_mod(node, env, cwd, node_pos, node['set'], 'set', None)

    def handle_append_path(self, node, env, cwd, node_pos):
        self.handle_env_mod(node, env, cwd, node_pos,
                            node['append_path'], 'append', os.path.pathsep)

    def handle_prepend_path(self, node, env, cwd, node_pos):
        self.handle_env_mod(node, env, cwd, node_pos,
                            node['prepend_path'], 'prepend', os.path.pathsep)

    def handle_append_flag(self, node, env, cwd, node_pos):
        self.handle_env_mod(node, env, cwd, node_pos,
                            node['append_flag'], 'append', ' ')
    
    def handle_prepend_flag(self, node, env, cwd, node_pos):
        self.handle_env_mod(node, env, cwd, node_pos,
                            node['prepend_flag'], 'prepend', ' ')

    def handle_env_mod(self, node, env, cwd, node_pos, varname, action, sep):
        value = self.substitute(node['value'], env)
        if action == 'set' or varname not in env or len(env[varname]) == 0:
            env[varname] = value
        elif action == 'prepend':
            env[varname] = sep.join([value, env[varname]])
        elif action == 'append':
            env[varname] = sep.join([env[varname], value])
        else:
            assert False

    def handle_cmd(self, node, env, cwd, node_pos):
        self.handle_command_nodes(node, env, cwd, node_pos)

    def handle_hit(self, node, env, cwd, node_pos):
        self.handle_command_nodes(node, env, cwd, node_pos)

    def process_cwd(self, node, cwd):
        if 'cwd' in node:
            cwd = pjoin(cwd, node['cwd'])
        return cwd

    def handle_command_nodes(self, node, env, cwd, node_pos):
        if not isinstance(node, dict):
            raise TypeError('command node must be a dict; got %r' % node)
        if sum(['cmd' in node, 'hit' in node, 'commands' in node, 'set' in node]) != 1:
            raise ValueError("Each script node should have exactly one of the 'cmd', 'hit', 'commands' keys")
        if sum(['to_var' in node, 'stdout_to_file' in node]) > 1:
            raise ValueError("Can only have one of to_var, stdout_to_file")
        if 'commands' in node and ('append_to_file' in node or 'to_var' in node or 'inputs' in node):
            raise ValueError('"commands" not compatible with to_var or append_to_file or inputs')


        # Make scopes
        node_env = dict(env)
        node_cwd = self.process_cwd(node, cwd)

        if 'cmd' in node or 'hit' in node:
            inputs = node.get('inputs', ())
            node_env.update(self.dump_inputs(inputs, node_pos))
            if 'cmd' in node:
                key = 'cmd'
                args = node['cmd']
                func = self.run_cmd
            else:
                key = 'hit'
                args = node['hit']
                func = self.run_hit
            if not isinstance(args, list):
                raise TypeError("'%s' arguments must be a list, got %r" % (key, args))
            args = [self.substitute(x, node_env) for x in args]

            if 'to_var' in node:
                stdout = StringIO()
                func(args, node_env, node_cwd, stdout_to=stdout)
                # modifying env, not node_env, to export change
                env[node['to_var']] = stdout.getvalue().strip()

            elif 'append_to_file' in node:
                stdout_filename = self.substitute(node['append_to_file'], node_env)
                if not os.path.isabs(stdout_filename):
                    stdout_filename = pjoin(node_cwd, stdout_filename)
                stdout_filename = os.path.realpath(stdout_filename)
                if stdout_filename.startswith(self.temp_dir):
                    raise NotImplementedError("Cannot currently use stream re-direction to write to "
                                              "a log-pipe (doing the write from a "
                                              "sub-process is OK)")
                with file(stdout_filename, 'a') as stdout:
                    func(args, node_env, node_cwd, stdout_to=stdout)

            else:
                func(args, node_env, node_cwd)
        else:
            assert False

        self.last_env, self.last_cwd = dict(node_env), node_cwd
        
    def handle_commands(self, node, env, cwd, node_pos):
        node_cwd = self.process_cwd(node, cwd)
        sub_env = dict(env)
        for i, command_node in enumerate(node['commands']):
            pos = node_pos + (i,)
            self.run_node(command_node, sub_env, node_cwd, pos)

    def run_cmd(self, args, env, cwd, stdout_to=None):
        logger = self.logger
        logger.debug('running %r' % args)
        logger.debug('cwd: ' + cwd)
        logger.debug('environment:')
        for line in pformat(env).splitlines():
            logger.debug('  ' + line)
        try:
            self.logged_check_call(args, env, cwd, stdout_to)
        except subprocess.CalledProcessError, e:
            logger.error("command failed (code=%d); raising" % e.returncode)
            raise

    def run_hit(self, args, env, cwd, stdout_to=None):
        args = ['hit'] + args
        logger = self.logger
        logger.debug('running %r' % args)
        # run it in the same process, but do not emit
        # INFO-messages from sub-command unless level is DEBUG
        old_level = logger.level
        old_stdout = sys.stdout
        try:
            if logger.level > DEBUG:
                logger.level = WARNING
            if stdout_to is not None:
                sys.stdout = stdout_to

            if len(args) >= 2 and args[1] == 'logpipe':
                if len(args) != 4:
                    raise ValueError('wrong number of arguments to "hit logpipe"')
                sublogger_name, level = args[2:]
                self.create_log_pipe(sublogger_name, level)
            else:
                from ..cli import main as cli_main
                with working_directory(cwd):
                    cli_main(args, env, logger)
        except:
            logger.error("hit command failed")
            raise
        finally:
            logger.level = old_level
            sys.stdout = old_stdout
       

    def logged_check_call(self, args, env, cwd, stdout_to):
        """
        Similar to subprocess.check_call, but multiplexes input from stderr, stdout
        and any number of log FIFO pipes available to the called process into
        a single Logger instance. Optionally captures stdout instead of logging it.
        """
        logger = self.logger
        try:
            proc = subprocess.Popen(args,
                                    cwd=cwd,
                                    env=env,
                                    stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                    close_fds=True)
        except OSError, e:
            if e.errno == errno.ENOENT:
                # fix error message up a bit since the situation is so confusing
                if '/' in args[0]:
                    msg = 'command "%s" not found (cwd: %s)' % (args[0], cwd)
                else:
                    msg = 'command "%s" not found in $PATH (cwd: %s)' % (args[0], cwd)
                logger.error(msg)
                raise OSError(e.errno, msg)
            else:
                raise

        # Weave together input from stdout, stderr, and any attached log
        # pipes.  To avoid any deadlocks with unbuffered stderr
        # interlaced with use of log pipe etc. we avoid readline(), but
        # instead use os.open to read and handle line-assembly ourselves...

        stdout_fd, stderr_fd = proc.stdout.fileno(), proc.stderr.fileno()
        poller = select.poll()
        poller.register(stdout_fd)
        poller.register(stderr_fd)

        # Set up { fd : (logger, level) }
        loggers = {stdout_fd: (logger, DEBUG), stderr_fd: (logger, DEBUG)}
        buffers = {stdout_fd: '', stderr_fd: ''}

        # The FIFO pipes are a bit tricky as they need to the re-opened whenever
        # any client closes. This also modified the loggers dict and fd_to_logpipe
        # dict.

        fd_to_logpipe = {} # stderr/stdout not re-opened
        
        def open_fifo(fifo_filename, logger, level):
            # need to open in non-blocking mode to avoid waiting for printing client process
            fd = os.open(fifo_filename, os.O_NONBLOCK|os.O_RDONLY)
            # remove non-blocking after open to treat all streams uniformly in
            # the reading code
            fcntl.fcntl(fd, fcntl.F_SETFL, os.O_RDONLY)
            loggers[fd] = (logger, level)
            buffers[fd] = ''
            fd_to_logpipe[fd] = fifo_filename
            poller.register(fd)

        def flush_buffer(fd):
            buf = buffers[fd]
            if buf:
                # flush buffer in case last line not terminated by '\n'
                sublogger, level = loggers[fd]
                sublogger.log(level, buf)
            del buffers[fd]

        def close_fifo(fd):
            flush_buffer(fd)
            poller.unregister(fd)
            os.close(fd)
            del loggers[fd]
            del fd_to_logpipe[fd]
            
        def reopen_fifo(fd):
            fifo_filename = fd_to_logpipe[fd]
            logger, level = loggers[fd]
            close_fifo(fd)
            open_fifo(fifo_filename, logger, level)

        for (header, level), fifo_filename in self.log_fifo_filenames.items():
            sublogger = logger.get_sub_logger(header)
            open_fifo(fifo_filename, sublogger, level)
            
        while True:
            # Python poll() doesn't return when SIGCHLD is received;
            # and there's the freak case where a process first
            # terminates stdout/stderr, then trying to write to a log
            # pipe, so we should track child termination the proper
            # way. Being in Python, it's easiest to just poll every
            # 50 ms; the majority of the time is spent in poll() so
            # it doesn't really increase log message latency
            events = poller.poll(50)
            if len(events) == 0:
                if proc.poll() is not None:
                    break # child terminated
            for fd, reason in events:
                if reason & select.POLLHUP and not (reason & select.POLLIN):
                    # we want to continue receiving PULLHUP|POLLIN until all
                    # is read
                    if fd in fd_to_logpipe:
                        reopen_fifo(fd)
                    elif fd in (stdout_fd, stderr_fd):
                        poller.unregister(fd)
                elif reason & select.POLLIN:
                    if stdout_to is not None and fd == stdout_fd:
                        # Just forward
                        buf = os.read(fd, LOG_PIPE_BUFSIZE)
                        stdout_to.write(buf)
                    else:
                        # append new bytes to what's already been read on this fd; and
                        # emit any completed lines
                        new_bytes = os.read(fd, LOG_PIPE_BUFSIZE)
                        assert new_bytes != '' # after all, we did poll
                        buffers[fd] += new_bytes
                        lines = buffers[fd].splitlines(True) # keepends=True
                        if lines[-1][-1] != '\n':
                            buffers[fd] = lines[-1]
                            del lines[-1]
                        else:
                            buffers[fd] = ''
                        # have list of lines, emit them to logger
                        sublogger, level = loggers[fd]
                        for line in lines:
                            if line[-1] == '\n':
                                line = line[:-1]
                            sublogger.log(level, line)

        flush_buffer(stderr_fd)
        flush_buffer(stdout_fd)
        for fd in fd_to_logpipe.keys():
            close_fifo(fd)

        retcode = proc.wait()
        if retcode != 0:
            exc = subprocess.CalledProcessError(retcode, args)
            self.logger.error(str(exc))
            raise exc

    def create_log_pipe(self, sublogger_name, level_str):
        level = dict(CRITICAL=CRITICAL, ERROR=ERROR, WARNING=WARNING, INFO=INFO, DEBUG=DEBUG)[level_str]
        fifo_filename = self.log_fifo_filenames.get((sublogger_name, level), None)
        if fifo_filename is None:
            fifo_filename = pjoin(self.temp_dir, "logpipe-%s-%s" % (sublogger_name, level_str))
            os.mkfifo(fifo_filename, 0600)
            self.log_fifo_filenames[sublogger_name, level] = fifo_filename
        sys.stdout.write(fifo_filename)
        
