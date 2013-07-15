"""
A connection to the Cloudcontrolapi platform.

**Related Flags**

:cloudcontrol_host_ip:         IP address or Name of Cloudcontrol server.
:cloudcontrol_host_username:   Username for connection to Cloudcontrol Server.
:cloudcontrol_host_password:   Password for connection to Cloudcontrol Server.
:cloudcontrol_cluster_name:    Name of a Cloudcontrol Cluster ComputeResource.
:cloudcontrol_task_poll_interval: The interval (seconds) used for polling of
                            remote tasks
                            (default: 5.0).
:cloudcontrol_api_retry_count: The API retry count in case of failure such as
                            network failures (socket errors etc.)
                            (default: 10).
:vnc_port:                  VNC starting port (default: 5900)
:vnc_port_total:            Total number of VNC ports (default: 10000)
:vnc_password:              VNC password
:use_linked_clone:          Whether to use linked clone (default: True)
"""

import time

from eventlet import event
from oslo.config import cfg

from nova import exception
from nova.openstack.common import jsonutils
from nova.openstack.common import log as logging
from nova.openstack.common import loopingcall
from nova.virt import driver



LOG = logging.getLogger(__name__)
logging.debug('cloudcontrol: A debug message!')

cloudcontrol_opts = [
    cfg.StrOpt('cloudcontrol_host_ip',
               default=None,
               help='URL for connection to CloudControl API host. Required if '
                    'compute_driver is cloudcontrol.CloudcontrolESXDriver or '
                    'cloudcontrol.CloudcontrolVCDriver.'),
    cfg.StrOpt('cloudcontrol_host_username',
               default=None,
               help='Username for connection to Cloudcontrol host. '
                    'Used only if compute_driver is '
                    'cloudcontrol.CloudcontrolESXDriver or cloudcontrol.CloudcontrolVCDriver.'),
    cfg.StrOpt('cloudcontrol_host_password',
               default=None,
               help='Password for connection to Cloudcontrol host. '
                    'Used only if compute_driver is '
                    'cloudcontrol.CloudcontrolESXDriver or cloudcontrol.CloudcontrolVCDriver.',
               secret=True),
    cfg.StrOpt('cloudcontrol_cluster_name',
               default=None,
               help='Name of a Cloudcontrol Cluster ComputeResource. '
                    'Used only if compute_driver is '
                    'cloudcontrol.CloudcontrolVCDriver.'),
    cfg.FloatOpt('cloudcontrol_task_poll_interval',
                 default=5.0,
                 help='The interval used for polling of remote tasks. '
                       'Used only if compute_driver is '
                       'cloudcontrol.CloudcontrolESXDriver or '
                       'cloudcontrol.CloudcontrolVCDriver.'),
    cfg.IntOpt('cloudcontrol_api_retry_count',
               default=10,
               help='The number of times we retry on failures, e.g., '
                    'socket error, etc. '
                    'Used only if compute_driver is '
                    'cloudcontrol.CloudcontrolESXDriver or cloudcontrol.CloudcontrolVCDriver.'),
    cfg.IntOpt('vnc_port',
               default=5900,
               help='VNC starting port'),
    cfg.IntOpt('vnc_port_total',
               default=10000,
               help='Total number of VNC ports'),
    cfg.StrOpt('vnc_password',
               default=None,
               help='VNC password',
               secret=True),
    cfg.BoolOpt('use_linked_clone',
                default=True,
                help='Whether to use linked clone'),
    ]

CONF = cfg.CONF
CONF.register_opts(cloudcontrol_opts)

TIME_BETWEEN_API_CALL_RETRIES = 2.0


class Failure(Exception):
    """Base Exception class for handling task failures."""

    def __init__(self, details):
        self.details = details

    def __str__(self):
        return str(self.details)


class CloudcontrolapiDriver(driver.ComputeDriver):
    """The Cloudontrol host connection object."""

    def __init__(self, virtapi, read_only=False, scheme="https"):
        super(CloudcontrolapiDriver, self).__init__(virtapi)
        self._cluster_name = CONF.ddcloudapi_cluster_name
        if not self._cluster_name:
            self._cluster = None
        else:
            self._cluster = vm_util.get_cluster_ref_from_name(
                            self._session, self._cluster_name)
            if self._cluster is None:
                raise exception.NotFound(_("Cloudcontrol Cluster %s is not found")
                                           % self._cluster_name)

        """self._volumeops = volumeops.VMwareVolumeOps(self._session,
                                                    self._cluster)
        self._vmops = vmops.VMwareVMOps(self._session, self.virtapi,
                                        self._volumeops, self._cluster)
                                        """
        self._vc_state = None


    @property
    def host_state(self):
        if not self._vc_state:
            self._vc_state = host.VCState(self._session,
                                          self._host_ip,
                                          self._cluster)
        return self._vc_state
