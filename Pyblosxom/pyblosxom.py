"""
This is the main module for PyBlosxom functionality.  PyBlosxom's setup 
and default handlers are defined here.
"""

# Python imports
from __future__ import nested_scopes, generators
import os, time, re, sys
import cgi
try: from cStringIO import StringIO
except ImportError: from StringIO import StringIO

# Pyblosxom imports
import tools
from entries.fileentry import FileEntry


VERSION = "1.3.1"
VERSION_DATE = VERSION + " 2/7/2006"
VERSION_SPLIT = tuple(VERSION.split('.'))

class PyBlosxom:
    """
    This is the main class for PyBlosxom functionality.  It handles
    initialization, defines default behavior, and also pushes the
    request through all the steps until the output is rendered and
    we're complete.
    """
    def __init__(self, config, environ, data=None):
        """
        Sets configuration and environment.
        Creates the L{Request} object.

        @param config: A dict containing the configuration variables.
        @type config: dict

        @param environ: A dict containing the environment variables.
        @type environ: dict

        @param data: A dict containing data variables.
        @type data: dict
        """
        config['pyblosxom_name'] = "pyblosxom"
        config['pyblosxom_version'] = VERSION_DATE

        # wbg 10/6/2005 - the plugin changes won't happen until
        # PyBlosxom 1.4.  so i'm commenting this out until then.
        # add the included plugins directory
        # p = config.get("plugin_dirs", [])
        # f = __file__[:__file__.rfind(os.sep)] + os.sep + "plugins"
        # p.append(f)
        # config['plugin_dirs'] = p

        self._config = config
        self._request = Request(config, environ, data)

    def initialize(self):
        """
        The initialize step further initializes the Request by setting
        additional information in the _data dict, registering plugins,
        and entryparsers.
        """
        global VERSION_DATE

        data = self._request.getData()
        pyhttp = self._request.getHttp()
        config = self._request.getConfiguration()

        # initialize the tools module
        tools.initialize(config)

        data["pyblosxom_version"] = VERSION_DATE
        data['pi_bl'] = ''

        # Get our URL and configure the base_url param
        if pyhttp.has_key('SCRIPT_NAME'):
            if not config.has_key('base_url'):
                # allow http and https
                config['base_url'] = '%s://%s%s' % (pyhttp['wsgi.url_scheme'], pyhttp['HTTP_HOST'], pyhttp['SCRIPT_NAME'])
        else:
            config['base_url'] = config.get('base_url', '')

        # take off the trailing slash for base_url
        if config['base_url'].endswith("/"):
            config['base_url'] = config['base_url'][:-1]

        datadir = config["datadir"]
        if datadir.endswith("/") or datadir.endswith("\\"):
            datadir = datadir[:-1]
            config['datadir'] = datadir

        # import and initialize plugins
        import plugin_utils
        plugin_utils.initialize_plugins(config.get("plugin_dirs", []), config.get("load_plugins", None))

        # entryparser callback is run here first to allow other plugins
        # register what file extensions can be used
        data['extensions'] = tools.run_callback("entryparser",
                                        {'txt': blosxom_entry_parser},
                                        mappingfunc=lambda x,y:y,
                                        defaultfunc=lambda x:x)

    def cleanup(self):
        """
        Cleanup everything.
        This should be called when Pyblosxom has done all its work.
        Right before exiting.
        """
        # log some useful stuff for debugging
        # this will only be logged if the log_level is "debug"
        log = tools.getLogger()
        response = self.getResponse()
        log.debug("status = %s" % response.status)
        log.debug("headers = %s" % response.headers)

        tools.cleanup()

    def getRequest(self):
        """
        Returns the L{Request} object.
        
        @returns: the request object 
        @rtype: L{Request}
        """
        return self._request

    def getResponse(self):
        """
        Returns the L{Response} object which handles all output 
        related functionality.
        
        @see: L{Response}
        @returns: the reponse object 
        @rtype: L{Response}
        """
        return self._request.getResponse()

    def run(self, static=False):
        """
        Main loop for pyblosxom.  This method will run the handle callback
        to allow registered handlers to handle the request.  If nothing
        handles the request, then we use the default_blosxom_handler.
        """
        self.initialize()

        # buffer the input stream in a StringIO instance if dynamic rendering 
        # is used.  This is done to have a known/consistent way of accessing 
        # incomming data.
        if static == False:
            self.getRequest().buffer_input_stream()

        # run the start callback
        tools.run_callback("start", {'request': self._request})

        data = self._request.getData()
        pyhttp = self._request.getHttp()
        config = self._request.getConfiguration()

        # allow anyone else to handle the request at this point
        handled = tools.run_callback("handle", 
                        {'request': self._request},
                        mappingfunc=lambda x,y:x,
                        donefunc=lambda x:x)

        if not handled == 1:
            blosxom_handler(self._request)

        # do end callback
        tools.run_callback("end", {'request': self._request})

        # we're done, clean up.
        # only call this if we're not in static rendering mode.
        if static == False:
            self.cleanup()

    def runCallback(self, callback="help"):
        """
        Generic method to run the engine for a specific callback
        """
        self.initialize()

        # run the start callback
        tools.run_callback("start", {'request': self._request})

        config = self._request.getConfig()
        data = self._request.getData()

        # invoke all callbacks for the 'callback'
        handled = tools.run_callback(callback,
                        {'request': self._request},
                        mappingfunc=lambda x,y:x,
                        donefunc=lambda x:x)

        # do end callback
        tools.run_callback("end", {'request': request})


    def runStaticRenderer(self, incremental=0):
        """
        This will go through all possible things in the blog
        and statically render everything to the "static_dir"
        specified in the config file.

        This figures out all the possible path_info settings
        and calls self.run() a bazillion times saving each file.

        @param incremental: whether (1) or not (0) to incrementally
            render the pages.  if we're incrementally rendering pages,
            then we render only the ones that have changed.
        @type  incremental: boolean
        """
        self.initialize()

        config = self._request.getConfiguration()
        data = self._request.getData()
        print "Performing static rendering."
        if incremental:
            print "Incremental is set."

        staticdir = config.get("static_dir", "")
        datadir = config["datadir"]

        if not staticdir:
            raise Exception("You must set static_dir in your config file.")

        flavours = config.get("static_flavours", ["html"])

        renderme = []

        monthnames = config.get("static_monthnames", 1)
        monthnumbers = config.get("static_monthnumbers", 0)

        dates = {}
        categories = {}

        # first we handle entries and categories
        listing = tools.Walk(self._request, datadir)

        for mem in listing:
            # skip the ones that have bad extensions
            ext = mem[mem.rfind(".")+1:]
            if not ext in data["extensions"].keys():
                continue

            # grab the mtime of the entry file
            mtime = time.mktime(tools.filestat(self._request, mem))

            # remove the datadir from the front and the bit at the end
            mem = mem[len(datadir):mem.rfind(".")]

            # this is the static filename
            fn = os.path.normpath(staticdir + mem)

            # grab the mtime of one of the statically rendered file
            try:
                smtime = os.stat(fn + "." + flavours[0])[8]
            except:
                smtime = 0

            # if the entry is more recent than the static, we want to re-render
            if smtime < mtime or not incremental:

                # grab the categories
                temp = os.path.dirname(mem).split(os.sep)
                for i in range(len(temp)+1):
                    p = os.sep.join(temp[0:i])
                    categories[p] = 0

                # grab the date
                mtime = time.localtime(mtime)
                year = time.strftime("%Y", mtime)
                month = time.strftime("%m", mtime)
                day = time.strftime("%d", mtime)

                dates[year] = 1

                if monthnumbers:
                    dates[year + "/" + month] = 1
                    dates[year + "/" + month + "/" + day] = 1

                if monthnames:
                    monthname = tools.num2month[month]
                    dates[year + "/" + monthname] = 1
                    dates[year + "/" + monthname + "/" + day] = 1

                # toss in the render queue
                for f in flavours:
                    renderme.append( (mem + "." + f, "") )

        print "rendering %d entries." % len(renderme)

        # handle categories
        categories = categories.keys()
        categories.sort()

        # if they have stuff in their root category, it'll add a "/"
        # to the category list and we want to remove that because it's
        # a duplicate of "".
        if "/" in categories:
            categories.remove("/")

        print "rendering %d category indexes." % len(categories)

        for mem in categories:
            mem = os.path.normpath( mem + "/index." )
            for f in flavours:
                renderme.append( (mem + f, "") )

        # now we handle dates
        dates = dates.keys()
        dates.sort()

        dates = ["/" + d for d in dates]

        print "rendering %d date indexes." % len(dates)

        for mem in dates:
            mem = os.path.normpath( mem + "/index." )
            for f in flavours:
                renderme.append( (mem + f, "") )
            
        # now we handle arbitrary urls
        additional_stuff = config.get("static_urls", [])
        print "rendering %d arbitrary urls." % len(additional_stuff)

        for mem in additional_stuff:
            if mem.find("?") != -1:
                url = mem[:mem.find("?")]
                query = mem[mem.find("?")+1:]
            else:
                url = mem
                query = ""

            renderme.append( (url, query) )

        # now we pass the complete render list to all the plugins
        # via cb_staticrender_filelist and they can add to the filelist
        # any ( url, query ) tuples they want rendered.
        print "(before) building %s files." % len(renderme)
        handled = tools.run_callback("staticrender_filelist",
                        {'request': self._request, 
                         'filelist': renderme,
                         'flavours': flavours})

        print "building %s files." % len(renderme)

        for url, q in renderme:
            url = url.replace(os.sep, "/")
            print "rendering '%s' ..." % url
            tools.render_url(config, url, q)

        # we're done, clean up
        self.cleanup()

    def testInstallation(self):
        """
        Goes through and runs some basic tests of the installation
        to make sure things are working.

        FIXME - This could probably use some work.  Maybe make this like
        MoinMoin's SystemInfo page?
        """
        tools.initialize(self._config)
        test_installation(self._request)
        tools.cleanup()

class EnvDict(dict):
    """
    Wrapper arround a dict to provide a backwards compatible way
    to get the L{form<cgi.FieldStorage>} with syntax as:
    request.getHttp()['form'] 
    instead of:
    request.getForm()
    """
    def __init__(self, request, env):
        self._request = request
        for key in env:
            self[key] = env[key]

    def __getitem__(self, key):
        if key == "form":
            return self._request.getForm()
        else:
            return dict.__getitem__(self, key)   

class Request(object):
    """
    This class holds the PyBlosxom request.  It holds configuration
    information, HTTP/CGI information, and data that we calculate
    and transform over the course of execution.

    There should be only one instance of this class floating around
    and it should get created by pyblosxom.cgi and passed into the
    PyBlosxom instance which will do further manipulation on the
    Request instance.
    """
    def __init__(self, config, environ, data):
        """
        Sets configuration and environment.
        Creates the L{Response} object which handles all output 
        related functionality.
        
        @param config: A dict containing the configuration variables.
        @type config: dict

        @param environ: A dict containing the environment variables.
        @type environ: dict

        @param data: A dict containing data variables.
        @type data: dict
        """
        # this holds configuration data that the user changes 
        # in config.py
        self._configuration = config

        # this holds HTTP/CGI oriented data specific to the request
        # and the environment in which the request was created
        self._http = EnvDict(self, environ)

        # this holds run-time data which gets created and transformed
        # by pyblosxom during execution
        if data == None:
            self._data = dict()
        else:
            self._data = data

        # this holds the input stream.
        # initialized for dynamic rendering in Pyblosxom.run.
        # for static rendering there is no input stream.
        self._in = StringIO()

        # copy methods to the Request object.
        self.__copy_members()

        # this holds the FieldStorage instance.
        # initialized when request.getForm is called the first time
        self._form = None
        
        # create and set the Response
        self.setResponse(Response(self))


    def __copy_members(self):
        """
        Copies methods from the underlying input stream to the request object.
        """
        props = ['read', 'readline', 'readlines', 'seek', 'tell']
        for prop in props:
            setattr(self, prop, getattr(self._in, prop))

    def __iter__(self):
        """
        Can't copy the __iter__ method over from the StringIO instance cause
        iter looks for the method in the class instead of the instance.
        So can't do this with __copy_members, have to define it seperatly.
        See http://aspn.activestate.com/ASPN/Cookbook/Python/Recipe/252151
        """
        return self._in

    def buffer_input_stream(self):
        """
        Buffer the input stream in a StringIO instance.
        This is done to have a known/consistent way of accessing incomming data.
        For example the input stream passed by mod_python does not offer the same 
        functionallity as sys.stdin.
        """
        # TODO: tests on memory consumption when uploading huge files
        pyhttp = self.getHttp()
        input = pyhttp['wsgi.input']
        method = pyhttp["REQUEST_METHOD"]

        # there's no data on stdin for a GET request.  pyblosxom
        # will block indefinitely on the read for a GET request with
        # thttpd.
        if method != "GET":
            try:
                length = int(pyhttp.get("CONTENT_LENGTH", 0))
            except ValueError:
                length = 0

            if length > 0:
                self._in.write(input.read(length))
                # rewind to start
                self._in.seek(0)

    def setResponse(self, response):
        """
        Sets the L{Response} object.

        @param response: A pyblosxom Response object
        @type response: L{Response}
        """
        self._response = response
        # for backwards compatibility
        self.getConfiguration()['stdoutput'] = response

    def getResponse(self):
        """
        Returns the L{Response} object which handles all output 
        related functionality.
        
        @returns: L{Response}
        """
        return self._response

    def __getForm(self):
        """
        Parses and returns the form data submitted by the client.
        Rewinds the input buffer after calling cgi.FieldStorage.

        @returns: L{cgi.FieldStorage}
        """        
        form = cgi.FieldStorage(fp=self._in, environ=self._http, keep_blank_values=0)
        # rewind the input buffer
        self._in.seek(0)
        return form

    def getForm(self):
        """
        Returns the form data submitted by the client.
        The L{form<cgi.FieldStorage>} instance is created
        only when requested to prevent overhead and unnecessary
        consumption of the input stream.

        @returns: L{cgi.FieldStorage}
        """
        if self._form == None:
            self._form = self.__getForm()
        return self._form

    def getConfiguration(self):
        """
        Returns the _actual_ configuration dict.  The configuration
        dict holds values that the user sets in their config.py file.

        Modifying the contents of the dict will affect all downstream 
        processing.

        @returns: dict
        """
        return self._configuration

    def getHttp(self):
        """
        Returns the _actual_ http dict.   Holds HTTP/CGI data derived 
        from the environment of execution.

        Modifying the contents of the dict will affect all downstream 
        processing. 

        @returns: dict
        """
        return self._http

    def getData(self):
        """
        Returns the _actual_ data dict.   Holds run-time data which is 
        created and transformed by pyblosxom during execution.

        Modifying the contents of the dict will affect all downstream 
        processing. 

        @returns: dict
        """
        return self._data

    def dumpRequest(self):
        # some dumping method here?  pprint?
        pass

    def __populateDict(self, currdict, newdict):
        for mem in newdict.keys():
            currdict[mem] = newdict[mem]

    def addHttp(self, d):
        """
        Takes in a dict and adds/overrides values in the existing
        http dict with the new values.

        @param d: the dict with the new keys/values to add
        @type  d: dict
        """
        self.__populateDict(self._http, d)

    def addData(self, d):
        """
        Takes in a dict and adds/overrides values in the existing
        data dict with the new values.

        @param d: the dict with the new keys/values to add
        @type  d: dict
        """
        self.__populateDict(self._data, d)

    def addConfiguration(self, d):
        """
        Takes in a dict and adds/overrides values in the existing
        configuration dict with the new values.

        @param d: the dict with the new keys/values to add
        @type  d: dict
        """
        self.__populateDict(self._configuration, d)

    def __getattr__(self, name, default=None):
        """
        Sort of simulates the dict except we only have three
        valid attributes: config, data, and http.

        @param name: the name of the attribute to get
        @type  name: string

        @param default: varies
        @type  default: varies
        """
        if name in ["config", "configuration", "conf"]:
            return self._configuration

        if name == "data":
            return self._data

        if name == "http":
            return self._http

        return default

    def __repr__(self):
        return "Request"


class Response(object):
    """
    Response class to handle all output related tasks in one place.

    This class is basically a wrapper arround a StringIO instance.
    It also provides methods for managing http headers.
    """
    def __init__(self, request):
        """
        Sets the L{Request} object that leaded to this response.
        Creates a L{StringIO} that is used as a output buffer.
        
        @param request: request object.
        @type request: L{Request}
        """
        self._request = request
        self._out = StringIO()
        self._headers_sent = False
        self.headers = {}
        self.status = "200 OK"
        self.__copy_members()
    
    def __copy_members(self):
        """
        Copies methods from the underlying output buffer to the response object.
        """
        props = ['close', 'flush', 
            'read', 'readline', 'readlines', 'seek', 'tell',
            'write', 'writelines']
        for prop in props:
            setattr(self, prop, getattr(self._out, prop))

    def __iter__(self):
        """
        Can't copy the __iter__ method over from the StringIO instance cause
        iter looks for the method in the class instead of the instance.
        So can't do this with __copy_members, have to define it seperatly.
        See http://aspn.activestate.com/ASPN/Cookbook/Python/Recipe/252151
        """
        return self._out

    def setStatus(self, status):
        """
        Sets the status code for this response.
        
        @param status: A status code and message like '200 OK'.
        @type status: str
        """
        self.status = status

    def getStatus(self):
        """
        Returns the status code and message of this response.
        
        @returns: str
        """
        return self.status

    def addHeader(self, *args):
        """
        Populates the HTTP header with lines of text.
        Sets the status code on this response object if the given argument
        list containes a 'Status' header.

        @param args: Paired list of headers
        @type args: argument lists
        @raises ValueError: This happens when the parameters are not correct
        """
        args = list(args)
        if not len(args) % 2:
            while args:
                key = args.pop(0).strip()
                if key.find(' ') != -1 or key.find(':') != -1:
                    raise ValueError, 'There should be no spaces in header keys'
                value = args.pop(0).strip()

                if key.lower() == "status":
                    self.setStatus(str(value))
                else:
                    self.headers.update({key: str(value)})
        else:
            raise ValueError, 'Headers recieved are not in the correct form'

    def getHeaders(self):
        """
        Returns the headers of this response.
        
        @returns: the HTTP response headers
        @rtype: dict
        """
        return self.headers

    def sendHeaders(self, out):
        """
        Send HTTP Headers to the given output stream.

        @param out: File like object
        @type out: file
        """
        out.write("Status: %s\n" % self.status)
        out.write('\n'.join(['%s: %s' % (x, self.headers[x]) 
                for x in self.headers.keys()]))
        out.write('\n\n')
        self._headers_sent = True

    def sendBody(self, out):
        """
        Send the response body to the given output stream.

        @param out: File like object
        @type out: file
        """
        self.seek(0)
        try:
            out.write(self.read())
        except IOError:
            # this is usually a Broken Pipe because the client dropped the
            # connection.  so we skip it.
            pass


def blosxom_handler(request):
    """
    This is the default blosxom handler.

    It calls the renderer callback to get a renderer.  If there is 
    no renderer, it uses the blosxom renderer.

    It calls the pathinfo callback to process the path_info http
    variable.

    It calls the filelist callback to build a list of entries to
    display.

    It calls the prepare callback to do any additional preparation
    before rendering the entries.

    Then it tells the renderer to render the entries.

    @param request: A standard request object
    @type request: L{Pyblosxom.pyblosxom.Request} object
    """

    config = request.getConfiguration()
    data = request.getData()

    # go through the renderer callback to see if anyone else
    # wants to render.  this renderer gets stored in the data dict 
    # for downstream processing.
    r = tools.run_callback('renderer', 
                           {'request': request},
                           donefunc = lambda x: x != None, 
                           defaultfunc = lambda x: None)

    if not r:
        # get the renderer we want to use
        r = config.get("renderer", "blosxom")

        # import the renderer
        r = tools.importName("Pyblosxom.renderers", r)

        # get the renderer object
        r = r.Renderer(request, config.get("stdoutput", sys.stdout))

    data['renderer'] = r

    # generate the timezone variable
    data["timezone"] = time.tzname[time.localtime()[8]]

    # process the path info to determine what kind of blog entry(ies) 
    # this is
    tools.run_callback("pathinfo",
                           {"request": request},
                           donefunc=lambda x:x != None,
                           defaultfunc=blosxom_process_path_info)

    # call the filelist callback to generate a list of entries
    data["entry_list"] = tools.run_callback("filelist",
                               {"request": request},
                               donefunc=lambda x:x != None,
                               defaultfunc=blosxom_file_list_handler)

    # figure out the blog-level mtime which is the mtime of the head of
    # the entry_list
    entry_list = data["entry_list"]
    if isinstance(entry_list, list) and len(entry_list) > 0:
        mtime = entry_list[0].get("mtime", time.time())
    else:
        mtime = time.time()
    mtime_tuple = time.localtime(mtime)
    mtime_gmtuple = time.gmtime(mtime)

    data["latest_date"] = time.strftime('%a, %d %b %Y', mtime_tuple)
    data["latest_w3cdate"] = time.strftime('%Y-%m-%dT%H:%M:%SZ', mtime_gmtuple)
    data['latest_rfc822date'] = time.strftime('%a, %d %b %Y %H:%M GMT', mtime_gmtuple)

    # we pass the request with the entry_list through the prepare callback
    # giving everyone a chance to transform the data.  the request is
    # modified in place.
    tools.run_callback("prepare", {"request": request})

    # now we pass the entry_list through the renderer
    entry_list = data["entry_list"]
    renderer = data['renderer']

    if renderer and not renderer.rendered:
        if entry_list:
            renderer.setContent(entry_list)
            # Log it as success
            tools.run_callback("logrequest", 
                    {'filename':config.get('logfile',''), 
                     'return_code': '200', 
                     'request': request})
        else:
            renderer.addHeader('Status', '404 Not Found')
            renderer.setContent(
                {'title': 'The page you are looking for is not available',
                 'body': 'Somehow I cannot find the page you want. ' + 
                 'Go Back to <a href="%s">%s</a>?' 
                 % (config["base_url"], config["blog_title"])})
            # Log it as failure
            tools.run_callback("logrequest", 
                    {'filename':config.get('logfile',''), 
                     'return_code': '404', 
                     'request': request})
        renderer.render()

    elif not renderer:
        output = config.get('stdoutput', sys.stdout)
        output.write("Content-Type: text/plain\n\nThere is something wrong with your setup.\n  Check your config files and verify that your configuration is correct.\n")

    cache = tools.get_cache(request)
    if cache:
        cache.close()


def blosxom_entry_parser(filename, request):
    """
    Open up a *.txt file and read its contents.  The first line
    becomes the title of the entry.  The other lines are the
    body of the entry.

    @param filename: A filename to extract data and metadata from
    @type filename: string

    @param request: A standard request object
    @type request: L{Pyblosxom.pyblosxom.Request} object

    @returns: A dict containing parsed data and meta data with the 
            particular file (and plugin)
    @rtype: dict
    """
    config = request.getConfiguration()

    entryData = {}

    f = open(filename, "r")
    lines = f.readlines()
    f.close()

    # the file has nothing in it...  so we're going to return
    # a blank entry data object.
    if len(lines) == 0:
        return { "title": "", "body": "" }

    # NOTE: you can probably use the next bunch of lines verbatim
    # for all entryparser plugins.  this pulls the first line off as
    # the title, the next bunch of lines that start with # as 
    # metadata lines, and then everything after that is the body
    # of the entry.
    title = lines.pop(0).strip()
    entryData['title'] = title

    # absorb meta data lines which begin with a #
    while lines and lines[0].startswith("#"):
        meta = lines.pop(0)
        meta = meta[1:].strip()     # remove the hash
        meta = meta.split(" ", 1)
        entryData[meta[0].strip()] = meta[1].strip()

    # Call the preformat function
    args = {'parser': entryData.get('parser', config.get('parser', 'plain')),
            'story': lines,
            'request': request}
    entryData['body'] = tools.run_callback('preformat', 
                                           args,
                                           donefunc = lambda x:x != None,
                                           defaultfunc = lambda x: ''.join(x['story']))

    # Call the postformat callbacks
    tools.run_callback('postformat',
                      {'request': request,
                       'entry_data': entryData})
        
    return entryData


def blosxom_file_list_handler(args):
    """
    This is the default handler for getting entries.  It takes the
    request object in and figures out which entries based on the
    default behavior that we want to show and generates a list of
    EntryBase subclass objects which it returns.

    @param args: dict containing the incoming Request object
    @type args: L{Pyblosxom.pyblosxom.Request}

    @returns: the content we want to render
    @rtype: list of EntryBase objects
    """
    request = args["request"]

    data = request.getData()
    config = request.getConfiguration()

    if data['bl_type'] == 'dir':
        filelist = tools.Walk(request, data['root_datadir'], int(config['depth']))
    elif data['bl_type'] == 'file':
        filelist = [data['root_datadir']]
    else:
        filelist = []

    entrylist = []
    for ourfile in filelist:
        e = FileEntry(request, ourfile, data['root_datadir'])
        entrylist.append((e._mtime, e))

    # this sorts entries by mtime in reverse order.  entries that have
    # no mtime get sorted to the top.
    entrylist.sort()
    entrylist.reverse()
    
    # Match dates with files if applicable
    if data['pi_yr']:
        # This is called when a date has been requested, e.g. /some/category/2004/Sep
        month = (data['pi_mo'] in tools.month2num.keys() and tools.month2num[data['pi_mo']] or data['pi_mo'])
        matchstr = "^" + data["pi_yr"] + month + data["pi_da"]
        valid_list = [x for x in entrylist if re.match(matchstr, x[1]._fulltime)]
    else:
        valid_list = entrylist

    # This is the maximum number of entries we can show on the front page
    # (zero indicates show all entries)
    max = config.get("num_entries", 0)
    if max and not data["pi_yr"]:
        valid_list = valid_list[:max]
        data["debugme"] = "done"

    valid_list = [x[1] for x in valid_list]

    return valid_list

def blosxom_process_path_info(args):
    """ 
    Process HTTP PATH_INFO for URI according to path specifications, fill in
    data dict accordingly
    
    The paths specification looks like this:
        - C{/foo.html} and C{/cat/foo.html} - file foo.* in / and /cat
        - C{/cat} - category
        - C{/2002} - year
        - C{/2002/Feb} (or 02) - Year and Month
        - C{/cat/2002/Feb/31} - year and month day in category.
    To simplify checking, four digits directory name is not allowed.

    @param args: dict containing the incoming Request object
    @type args: L{Pyblosxom.pyblosxom.Request}
    """
    request = args['request']
    config = request.getConfiguration()
    data = request.getData()
    pyhttp = request.getHttp()

    logger = tools.getLogger()

    form = request.getForm()

    # figure out which flavour to use.  the flavour is determined
    # by looking at the "flav" post-data variable, the "flav"
    # query string variable, the "default_flavour" setting in the
    # config.py file, or "html"
    flav = config.get("default_flavour", "html")
    if form.has_key("flav"):
        flav = form["flav"].value

    data['flavour'] = flav

    data['pi_yr'] = ''
    data['pi_mo'] = ''
    data['pi_da'] = ''
    
    path_info = pyhttp.get("PATH_INFO", "")

    data['root_datadir'] = config['datadir']

    data["pi_bl"] = path_info

    # first we check to see if this is a request for an index and we can pluck
    # the extension (which is certainly a flavour) right off.
    newpath, ext = os.path.splitext(path_info)
    if newpath.endswith("/index") and ext:
        # there is a flavour-like thing, so that's our new flavour
        # and we adjust the path_info to the new filename
        data["flavour"] = ext[1:]
        path_info = newpath

    if path_info.startswith("/"):
        path_info = path_info[1:]

    absolute_path = os.path.join(config["datadir"], path_info)

    path_info = path_info.split("/")

    if os.path.isdir(absolute_path):

        # this is an absolute path

        data['root_datadir'] = absolute_path
        data['bl_type'] = 'dir'

    elif absolute_path.endswith("/index") and \
            os.path.isdir(absolute_path[:-6]):

        # this is an absolute path with /index at the end of it

        data['root_datadir'] = absolute_path[:-6]
        data['bl_type'] = 'dir'

    else:

        # this is either a file or a date

        ext = tools.what_ext(data["extensions"].keys(), absolute_path)
        if not ext:
            # it's possible we didn't find the file because it's got a flavour
            # thing at the end--so try removing it and checking again.
            newpath, flav = os.path.splitext(absolute_path)
            if flav:
                ext = tools.what_ext(data["extensions"].keys(), newpath)
                if ext:
                    # there is a flavour-like thing, so that's our new flavour
                    # and we adjust the absolute_path and path_info to the new 
                    # filename
                    data["flavour"] = flav[1:]
                    absolute_path = newpath
                    path_info, flav = os.path.splitext("/".join(path_info))
                    path_info = path_info.split("/")

        if ext:

            # this is a file
            data["bl_type"] = "file"
            data["root_datadir"] = absolute_path + "." + ext

        else:
            data["bl_type"] = "dir"

            # it's possible to have category/category/year/month/day
            # (or something like that) so we pluck off the categories
            # here.
            pi_bl = ""
            while len(path_info) > 0 and \
                    not (len(path_info[0]) == 4 and path_info[0].isdigit()):
                pi_bl = os.path.join(pi_bl, path_info.pop(0))

            # handle the case where we do in fact have a category
            # preceeding the date.
            if pi_bl:
                pi_bl = pi_bl.replace("\\", "/")
                data["pi_bl"] = pi_bl
                data["root_datadir"] = os.path.join(config["datadir"], pi_bl)

            if len(path_info) > 0:
                item = path_info.pop(0)
                # handle a year token
                if len(item) == 4 and item.isdigit():
                    data['pi_yr'] = item
                    item = ""

                    if (len(path_info) > 0):
                        item = path_info.pop(0)
                        # handle a month token
                        if item in tools.MONTHS:
                            data['pi_mo'] = item
                            item = ""

                            if (len(path_info) > 0):
                                item = path_info.pop(0)
                                # handle a day token
                                if len(item) == 2 and item.isdigit():
                                    data["pi_da"] = item
                                    item = ""

                                    if len(path_info) > 0:
                                        item = path_info.pop(0)

                # if the last item we picked up was "index", then we
                # just ditch it because we don't need it.
                if item == "index":
                    item = ""

                # if we picked off an item we don't recognize and/or
                # there is still stuff in path_info to pluck out, then
                # it's likely this wasn't a date.
                if item or len(path_info) > 0:
                    data["bl_type"] = "dir"
                    data["root_datadir"] = absolute_path


    # figure out the blog_title_with_path data variable
    blog_title = config["blog_title"]

    if data['pi_bl'] != '':
        data['blog_title_with_path'] = '%s : %s' % (blog_title, data['pi_bl'])
    else:
        data['blog_title_with_path'] = blog_title

    # construct our final URL
    data['url'] = '%s%s' % (config['base_url'], data['pi_bl'])
    url = config['base_url']
    if data['pi_bl'].startswith("/"):
        url = url + data['pi_bl']
    else:
        url = url + "/" + data['pi_bl']
    data['url'] = url

    # set path_info to our latest path_info
    data['path_info'] = path_info


def test_installation(request):
    """
    This function gets called when someone starts up pyblosxom.cgi
    from the command line with no REQUEST_METHOD environment variable.

    It:

      1. tests properties in their config.py file
      2. verifies they have a datadir and that it exists
      3. initializes all the plugins they have installed
      4. runs "cb_verify_installation"--plugins can print out whether
         they are installed correctly (i.e. have valid config property
         settings and can read/write to data files)
      5. exits

    The goal is to be as useful and informative to the user as we can be
    without being overly verbose and confusing.

    This is designed to make it much much much easier for a user to
    verify their PyBlosxom installation is working and also to install
    new plugins and verify that their configuration is correct.
    """
    import sys, os, os.path
    from Pyblosxom import pyblosxom

    config = request.getConfiguration()

    # BASE STUFF
    print "Welcome to PyBlosxom's installation verification system."
    print "------"
    print "]] printing diagnostics [["
    print "pyblosxom:   %s" % pyblosxom.VERSION_DATE
    print "sys.version: %s" % sys.version.replace("\n", " ")
    print "os.name:     %s" % os.name
    print "codebase:    %s" % config.get("codebase", "--default--")
    print "------"

    # CONFIG FILE
    print "]] checking config file [["
    print "config has %s properties set." % len(config)
    print ""
    required_config = ["datadir"]

    nice_to_have_config = ["blog_title", "blog_author", "blog_description",
                           "blog_language", "blog_encoding", 
                           "base_url", "depth", "num_entries", "renderer", 
                           "cacheDriver", "cacheConfig", "plugin_dirs", 
                           "load_plugins"]
    missing_properties = 0
    for mem in required_config:
        if not config.has_key(mem):
            print "   missing required property: '%s'" % mem
            missing_properties = 1

    for mem in nice_to_have_config:
        if not config.has_key(mem):
            print "   missing optional property: '%s'" % mem

    print ""
    print "Refer to the documentation for what properties are available"
    print "and what they do."

    if missing_properties:
        print ""
        print "Missing properties must be set in order for your blog to"
        print "work."
        print ""
        print "This must be done before we can go further.  Exiting."
        return

    print "PASS: config file is fine."

    print "------"
    print "]] checking datadir [["

    # DATADIR
    if not os.path.isdir(config["datadir"]):
        print "datadir '%s' does not exist." % config["datadir"]          
        print "You need to create your datadir and give it appropriate"
        print "permissions."
        print ""
        print "This must be done before we can go further.  Exiting."
        return

    print "PASS: datadir is there.  Note: this does not check whether"
    print "      your webserver has permissions to view files therein!"

    print "------"
    print "Now we're going to verify your plugin configuration."

    if config.has_key("plugin_dirs"):

        from Pyblosxom import plugin_utils
        plugin_utils.initialize_plugins(config["plugin_dirs"],
                                        config.get("load_plugins", None))

        no_verification_support = []

        for mem in plugin_utils.plugins:
            if "verify_installation" in dir(mem):
                print "=== plugin: '%s'" % mem.__name__
                print "    file: %s" % mem.__file__

                if "__version__" in dir(mem):
                    print "    version: %s" % mem.__version__
                else:
                    print "    plugin has no version."

                try:
                    if mem.verify_installation(request) == 1:
                        print "    PASS"
                    else:
                        print "    FAIL!!!"
                except AssertionError, error_message:
                    print " FAIL!!! ", error_message

            else:
                no_verification_support.append( "'%s' (%s)" % (mem.__name__, mem.__file__))

        if len(no_verification_support) > 0:
            print ""
            print "The following plugins do not support installation verification:"
            for mem in no_verification_support:
                print "   %s" % mem
    else:
        print "You have chosen not to load any plugins."

# vim: shiftwidth=4 tabstop=4 expandtab
