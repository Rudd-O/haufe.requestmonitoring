# -*- coding: utf-8 -*-
"""Fine resolution request logging.

Used as base for "ztop" and "zanalyse", i.e. helps to determine
the Zope load, detect long running requests
and to analyse the causes of restarts.


The implementation in this module registers subscribers for
"IPubStart" and "IPubSuccess/IPubFailure".
For each of these events, a log entry of the form
"timestamp status request_time type request_id request_info"
is written.

*timestamp* is the current time in the format "%y%m%dT%H%M%S".

*status* is "0" for "IPubStart" events, "390" for requests that will
be retried and the result of "IStatus" applied to the response otherwise.

"request_time" is "0" for "IPubStart" events. Otherwise, it will be
the request time in seconds.

"type" is "+" for "IPubStart" and "-" otherwise.

"request_id" is the (process) unique request id.

"request_info" is "IInfo" applied to the request.


In addition, a log entry with "request_info == restarted" is
written when this logging is activated. Apart from "request_info"
and "timestamp" all other fields are "0". It indicates (obviously)
that the server has been restarted. Following requests get
request ids starting with "1".


To activate this logging, both "timelogging.zcml" must be activated
and a "product-config" section with name "timelogging" must be defined
containing the key "filebase".  It specifies the basename of the logfile;
".<date>" will be appended to this base.  If "filebase" is `/dev/stderr`
then the standard Python logger will be used instead of disk files.

Then, "ITicket", "IInfo" adapters must be defined (e.g. the one
from "info"). An "IStatus" adapter may be defined for response.

If this logging is not activated via the product config, but
"timelogging.zcml" is still included in your Zope configuration
(the default if this product's ZCML is included), then a standard
logging logger is used to log events, and events are written
as INFO level log entries to that logger.
"""
from .interfaces import IInfo
from .interfaces import IStatus
from .interfaces import ITicket
from .Rotator import Rotator
from logging import getLogger
from threading import Lock
from time import strftime
from time import time
from zope.processlifetime import IProcessStarting
from zope.component import adapter
from zope.component import provideHandler
from ZPublisher.interfaces import IPubFailure
from ZPublisher.interfaces import IPubStart
from ZPublisher.interfaces import IPubSuccess

_log_format = '%s %3d %10.4f %c %6d %s\n'
_log_time_format = '%y%m%dT%H%M%S'

_lock = Lock()
_state = {}
_logfile = None


_LOGGER = getLogger(__name__)
STDERR = "/dev/stderr"


def account_request(request, status=0):
    ticket = ITicket(request)
    id = ticket.id
    info = str(IInfo(request))
    request_time = 0
    type = status and '-' or '+'
    ct = time()
    _lock.acquire()
    try:
        if status:
            request_time = ct - _state[id]
            del _state[id]
        else:
            _state[id] = ct
    finally:
        _lock.release()
    _log(type=type,
         status=status,
         request_id=id,
         request_time=request_time,
         info=info)


@adapter(IProcessStarting)
def start_timelogging(unused):
    """start timelogging if configured."""
    from App.config import getConfiguration
    config = getConfiguration().product_config.get('timelogging')
    if config is None:
        return  # not configured

    global _logfile
    if config['filebase'] == STDERR:
        _logfile = config['filebase']
    else:
        _logfile = Rotator(config['filebase'], lock=True)
    # indicate restart
    _log('0', info='restarted')
    # register publication observers
    provideHandler(handle_request_start)
    provideHandler(handle_request_success)
    provideHandler(handle_request_failure)

@adapter(IPubStart)
def handle_request_start(event):
    """handle "IPubStart"."""
    account_request(event.request)


@adapter(IPubSuccess)
def handle_request_success(event):
    """handle "IPubSuccess"."""
    request = event.request
    response = request.response
    status = IStatus(response, None)
    if status is None:
        status = response.getStatus()
    else:
        status = int(status)
    assert status
    account_request(request, status)


@adapter(IPubFailure)
def handle_request_failure(event):
    """handle "IPubFailure"."""
    request = event.request
    if event.retry:
        account_request(request, 390)
    else:
        # Note: Zope forgets (at least sometimes)
        #   to inform the response about the exception.
        #   Work around this bug.
        # When Zope3 views are used for error handling, they no longer
        #   communicate via exceptions with the ZPublisher. Instead, they seem
        #   to use 'setBody' which interferes with the 'exception' call below.
        #   We work around this problem by saving the response state and then
        #   restore it again. Of course, this no longer works around the Zope
        #   bug (forgetting to call 'exception') mentioned above.
        response = request.response
        saved = response.__dict__.copy()
        response.setStatus(event.exc_info[0])
        handle_request_success(event)
        response.__dict__.update(saved)  # restore response again


def _log(type, status=0, request_id=0, request_time=0, info=''):
    string = _log_format % (
        strftime(_log_time_format),
        status,
        request_time,
        type,
        request_id,
        info,
    )
    if _logfile is not None:
        if _logfile == STDERR:
            _LOGGER.info(string.strip())
        else:
            _logfile.write(string)
