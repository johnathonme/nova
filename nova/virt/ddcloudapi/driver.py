"""
A connection to the Cloudcontrolapi platform.

**Related Flags**

:ddcloudapi_host_ip:         IP address or Name of Cloudcontrol server.
:ddcloudapi_host_username:   Username for connection to Cloudcontrol Server.
:ddcloudapi_host_password:   Password for connection to Cloudcontrol Server.
:ddcloudapi_cluster_name:    Name of a Cloudcontrol Cluster ComputeResource.
:ddcloudapi_task_poll_interval: The interval (seconds) used for polling of
                            remote tasks
                            (default: 5.0).
:ddcloudapi_api_retry_count: The API retry count in case of failure such as
                            network failures (socket errors etc.)
                            (default: 10).
:vnc_port:                  VNC starting port (default: 5900)
:vnc_port_total:            Total number of VNC ports (default: 10000)
:vnc_password:              VNC password
:use_linked_clone:          Whether to use linked clone (default: True)
"""
# Libvirt imports
import errno
import eventlet
import functools
import glob
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import uuid

from eventlet import greenio
from eventlet import greenthread
from eventlet import patcher
from eventlet import tpool
from eventlet import util as eventlet_util
from lxml import etree
from oslo.config import cfg
from xml.dom import minidom

from nova.api.metadata import base as instance_metadata
from nova import block_device
from nova.compute import flavors
from nova.compute import power_state
from nova.compute import task_states
from nova.compute import vm_mode
from nova import context as nova_context
from nova import exception
from nova.image import glance
from nova.openstack.common import excutils
from nova.openstack.common import fileutils
from nova.openstack.common import importutils
from nova.openstack.common import jsonutils
from nova.openstack.common import log as logging
from nova.openstack.common import loopingcall
from nova.openstack.common.notifier import api as notifier
from nova.openstack.common import processutils
from nova import utils
from nova import version
from nova.virt import configdrive
from nova.virt.disk import api as disk
from nova.virt import driver
from nova.virt import event as virtevent
from nova.virt import firewall
from nova.virt import netutils

native_threading = patcher.original("threading")
native_Queue = patcher.original("Queue")
# DDCloudAPI Imports
#import time
#import uuid
from eventlet import event
#from oslo.config import cfg
#from nova import exception
#from nova.openstack.common import jsonutils
#from nova.openstack.common import log as logging
#from nova.openstack.common import loopingcall
#from nova.virt import driver
#from nova.openstack.common import excutils
#from nova.openstack.common import fileutils
#from nova.openstack.common import importutils

from nova.virt.ddcloudapi import vm_util
from nova.virt.ddcloudapi import vim
from nova.virt.ddcloudapi import vim_util
from nova.virt.ddcloudapi import error_util
from nova.virt.ddcloudapi import host
#from nova.virt.ddcloudapi import vmops
from nova.virt.ddcloudapi import volumeops
from nova.virt.ddcloudapi import firewall as libvirt_firewall
from nova.virt.ddcloudapi import config as vconfig
from nova.virt.ddcloudapi import utils as libvirt_utils
from nova.virt.ddcloudapi import blockinfo
from nova.virt.ddcloudapi import imagebackend
from nova.virt.ddcloudapi import imagecache
import dd_session
#import requests


LOG = logging.getLogger(__name__)
#logging.debug('ddcloudapi: A debug message!')

_db_content = {}

libvirt = None

ddcloudapi_opts = [
    cfg.StrOpt('ddcloudapi_host_ip',
               default=None,
               help='URL for connection to CloudControl API host. Required if '
                    'compute_driver is ddcloudapi.CloudcontrolESXDriver or '
                    'ddcloudapi.CloudcontrolVCDriver.'),
    cfg.StrOpt('ddcloudapi_host_username',
               default=None,
               help='Username for connection to Cloudcontrol host. '
                    'Used only if compute_driver is '
                    'ddcloudapi.CloudcontrolESXDriver or ddcloudapi.CloudcontrolVCDriver.'),
    cfg.StrOpt('ddcloudapi_host_password',
               default=None,
               help='Password for connection to Cloudcontrol host. '
                    'Used only if compute_driver is '
                    'ddcloudapi.CloudcontrolESXDriver or ddcloudapi.CloudcontrolVCDriver.',
               secret=True),
    cfg.StrOpt('ddcloudapi_cluster_name',
               default=None,
               help='Name of a Cloudcontrol Cluster ComputeResource. '
                    'Used only if compute_driver is '
                    'ddcloudapi.CloudcontrolVCDriver.'),
    cfg.FloatOpt('ddcloudapi_task_poll_interval',
                 default=5.0,
                 help='The interval used for polling of remote tasks. '
                       'Used only if compute_driver is '
                       'ddcloudapi.CloudcontrolESXDriver or '
                       'ddcloudapi.CloudcontrolVCDriver.'),
    cfg.IntOpt('ddcloudapi_api_retry_count',
               default=10,
               help='The number of times we retry on failures, e.g., '
                    'socket error, etc. '
                    'Used only if compute_driver is '
                    'ddcloudapi.CloudcontrolESXDriver or ddcloudapi.CloudcontrolVCDriver.'),
    cfg.StrOpt('ddcloudapi_apistring',
                default = None,
                help='DD Cloud API version string like oec/0.9'),
    cfg.StrOpt('ddcloudapi_orgid',
                default = None,
                help='DD Cloud client Org Id, helper value during dev'),
    cfg.StrOpt('ddcloudapi_vif_driver',
               default='nova.virt.ddcloudapi.vif.LibvirtGenericVIFDriver',
               help='The ddcloudapiVIF driver to configure the VIFs.'),
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
CONF.register_opts(ddcloudapi_opts)

libvirt_opts = [
    cfg.StrOpt('rescue_image_id',
               default=None,
               help='Rescue ami image'),
    cfg.StrOpt('rescue_kernel_id',
               default=None,
               help='Rescue aki image'),
    cfg.StrOpt('rescue_ramdisk_id',
               default=None,
               help='Rescue ari image'),
    cfg.StrOpt('libvirt_type',
               default='qemu',
               help='Libvirt domain type (valid options are: '
                    'kvm, lxc, qemu, uml, xen)'),
    cfg.StrOpt('libvirt_uri',
               default='',
               help='Override the default libvirt URI '
                    '(which is dependent on libvirt_type)'),
    cfg.BoolOpt('libvirt_inject_password',
                default=False,
                help='Inject the admin password at boot time, '
                     'without an agent.'),
    cfg.BoolOpt('libvirt_inject_key',
                default=True,
                help='Inject the ssh public key at boot time'),
    cfg.IntOpt('libvirt_inject_partition',
                default=1,
                help='The partition to inject to : '
                     '-2 => disable, -1 => inspect (libguestfs only), '
                     '0 => not partitioned, >0 => partition number'),
    cfg.BoolOpt('use_usb_tablet',
                default=True,
                help='Sync virtual and real mouse cursors in Windows VMs'),
    cfg.StrOpt('live_migration_uri',
               default="qemu+tcp://%s/system",
               help='Migration target URI '
                    '(any included "%s" is replaced with '
                    'the migration target hostname)'),
    cfg.StrOpt('live_migration_flag',
               default='VIR_MIGRATE_UNDEFINE_SOURCE, VIR_MIGRATE_PEER2PEER',
               help='Migration flags to be set for live migration'),
    cfg.StrOpt('block_migration_flag',
               default='VIR_MIGRATE_UNDEFINE_SOURCE, VIR_MIGRATE_PEER2PEER, '
                       'VIR_MIGRATE_NON_SHARED_INC',
               help='Migration flags to be set for block migration'),
    cfg.IntOpt('live_migration_bandwidth',
               default=0,
               help='Maximum bandwidth to be used during migration, in Mbps'),
    cfg.StrOpt('snapshot_image_format',
               default=None,
               help='Snapshot image format (valid options are : '
                    'raw, qcow2, vmdk, vdi). '
                    'Defaults to same as source image'),
    cfg.StrOpt('libvirt_vif_driver',
               default='nova.virt.libvirt.vif.LibvirtGenericVIFDriver',
               help='The libvirt VIF driver to configure the VIFs.'),
    cfg.ListOpt('libvirt_volume_drivers',
                default=[
                  'iscsi=nova.virt.libvirt.volume.LibvirtISCSIVolumeDriver',
                  'local=nova.virt.libvirt.volume.LibvirtVolumeDriver',
                  'fake=nova.virt.libvirt.volume.LibvirtFakeVolumeDriver',
                  'rbd=nova.virt.libvirt.volume.LibvirtNetVolumeDriver',
                  'sheepdog=nova.virt.libvirt.volume.LibvirtNetVolumeDriver',
                  'nfs=nova.virt.libvirt.volume.LibvirtNFSVolumeDriver',
                  'aoe=nova.virt.libvirt.volume.LibvirtAOEVolumeDriver',
                  'glusterfs='
                      'nova.virt.libvirt.volume.LibvirtGlusterfsVolumeDriver',
                  'fibre_channel=nova.virt.libvirt.volume.'
                      'LibvirtFibreChannelVolumeDriver',
                  'scality='
                      'nova.virt.libvirt.volume.LibvirtScalityVolumeDriver',
                  ],
                help='Libvirt handlers for remote volumes.'),
    cfg.StrOpt('libvirt_disk_prefix',
               default=None,
               help='Override the default disk prefix for the devices attached'
                    ' to a server, which is dependent on libvirt_type. '
                    '(valid options are: sd, xvd, uvd, vd)'),
    cfg.IntOpt('libvirt_wait_soft_reboot_seconds',
               default=120,
               help='Number of seconds to wait for instance to shut down after'
                    ' soft reboot request is made. We fall back to hard reboot'
                    ' if instance does not shutdown within this window.'),
    cfg.BoolOpt('libvirt_nonblocking',
                default=True,
                help='Use a separated OS thread pool to realize non-blocking'
                     ' libvirt calls'),
    cfg.StrOpt('libvirt_cpu_mode',
               default=None,
               help='Set to "host-model" to clone the host CPU feature flags; '
                    'to "host-passthrough" to use the host CPU model exactly; '
                    'to "custom" to use a named CPU model; '
                    'to "none" to not set any CPU model. '
                    'If libvirt_type="kvm|qemu", it will default to '
                    '"host-model", otherwise it will default to "none"'),
    cfg.StrOpt('libvirt_cpu_model',
               default=None,
               help='Set to a named libvirt CPU model (see names listed '
                    'in /usr/share/libvirt/cpu_map.xml). Only has effect if '
                    'libvirt_cpu_mode="custom" and libvirt_type="kvm|qemu"'),
    cfg.StrOpt('libvirt_snapshots_directory',
               default='$instances_path/snapshots',
               help='Location where libvirt driver will store snapshots '
                    'before uploading them to image service'),
    cfg.StrOpt('xen_hvmloader_path',
                default='/usr/lib/xen/boot/hvmloader',
                help='Location where the Xen hvmloader is kept'),
    cfg.ListOpt('disk_cachemodes',
                 default=[],
                 help='Specific cachemodes to use for different disk types '
                      'e.g: ["file=directsync","block=none"]'),
    cfg.StrOpt('vcpu_pin_set',
                default=None,
                help='Which pcpus can be used by vcpus of instance '
                     'e.g: "4-12,^8,15"'),
    ]

CONF.register_opts(libvirt_opts)

TIME_BETWEEN_API_CALL_RETRIES = 2.0

CONF.import_opt('host', 'nova.netconf')
CONF.import_opt('my_ip', 'nova.netconf')
CONF.import_opt('default_ephemeral_format', 'nova.virt.driver')
CONF.import_opt('use_cow_images', 'nova.virt.driver')
CONF.import_opt('live_migration_retry_count', 'nova.compute.manager')
CONF.import_opt('vncserver_proxyclient_address', 'nova.vnc')
CONF.import_opt('server_proxyclient_address', 'nova.spice', group='spice')

DEFAULT_FIREWALL_DRIVER = "%s.%s" % (
    libvirt_firewall.__name__,
    libvirt_firewall.IptablesFirewallDriver.__name__)

MAX_CONSOLE_BYTES = 102400


def patch_tpool_proxy():
    """eventlet.tpool.Proxy doesn't work with old-style class in __str__()
    or __repr__() calls. See bug #962840 for details.
    We perform a monkey patch to replace those two instance methods.
    """
    def str_method(self):
        return str(self._obj)

    def repr_method(self):
        return repr(self._obj)

    tpool.Proxy.__str__ = str_method
    tpool.Proxy.__repr__ = repr_method


patch_tpool_proxy()

VIR_DOMAIN_NOSTATE = 0
VIR_DOMAIN_RUNNING = 1
VIR_DOMAIN_BLOCKED = 2
VIR_DOMAIN_PAUSED = 3
VIR_DOMAIN_SHUTDOWN = 4
VIR_DOMAIN_SHUTOFF = 5
VIR_DOMAIN_CRASHED = 6
VIR_DOMAIN_PMSUSPENDED = 7

LIBVIRT_POWER_STATE = {
    VIR_DOMAIN_NOSTATE: power_state.NOSTATE,
    VIR_DOMAIN_RUNNING: power_state.RUNNING,
    # NOTE(maoy): The DOMAIN_BLOCKED state is only valid in Xen.
    # It means that the VM is running and the vCPU is idle. So,
    # we map it to RUNNING
    VIR_DOMAIN_BLOCKED: power_state.RUNNING,
    VIR_DOMAIN_PAUSED: power_state.PAUSED,
    # NOTE(maoy): The libvirt API doc says that DOMAIN_SHUTDOWN
    # means the domain is being shut down. So technically the domain
    # is still running. SHUTOFF is the real powered off state.
    # But we will map both to SHUTDOWN anyway.
    # http://libvirt.org/html/libvirt-libvirt.html
    VIR_DOMAIN_SHUTDOWN: power_state.SHUTDOWN,
    VIR_DOMAIN_SHUTOFF: power_state.SHUTDOWN,
    VIR_DOMAIN_CRASHED: power_state.CRASHED,
    VIR_DOMAIN_PMSUSPENDED: power_state.SUSPENDED,
}

MIN_LIBVIRT_VERSION = (0, 9, 6)
# When the above version matches/exceeds this version
# delete it & corresponding code using it
MIN_LIBVIRT_HOST_CPU_VERSION = (0, 9, 10)
MIN_LIBVIRT_CLOSE_CALLBACK_VERSION = (1, 0, 1)
# Live snapshot requirements
REQ_HYPERVISOR_LIVESNAPSHOT = "QEMU"
MIN_LIBVIRT_LIVESNAPSHOT_VERSION = (1, 0, 0)
MIN_QEMU_LIVESNAPSHOT_VERSION = (1, 3, 0)


def libvirt_error_handler(context, err):
    # Just ignore instead of default outputting to stderr.
    pass



class Failure(Exception):
    """Base Exception class for handling task failures."""

    def __init__(self, details):
        self.details = details

    def __str__(self):
        return str(self.details)


class VMwareESXDriver(driver.ComputeDriver):
    """The ESX host connection object."""

    # VMwareAPI has both ESXi and vCenter API sets.
    # The ESXi API are a proper sub-set of the vCenter API.
    # That is to say, nearly all valid ESXi calls are
    # valid vCenter calls. There are some small edge-case
    # exceptions regarding VNC, CIM, User management & SSO.

    def __init__(self, virtapi, read_only=False, scheme="https"):
        super(VMwareESXDriver, self).__init__(virtapi)

        self._host_ip = CONF.ddcloudapi_host_ip
        host_username = CONF.ddcloudapi_host_username
        host_password = CONF.ddcloudapi_host_password
        api_retry_count = CONF.ddcloudapi_api_retry_count
        if not self._host_ip or host_username is None or host_password is None:
            raise Exception(_("Must specify vmwareapi_host_ip,"
                              "vmwareapi_host_username "
                              "and vmwareapi_host_password to use"
                              "compute_driver=vmwareapi.VMwareESXDriver or "
                              "vmwareapi.VMwareVCDriver"))

        self._session = VMwareAPISession(self._host_ip,
                                         host_username, host_password,
                                         api_retry_count, scheme=scheme)
        self._volumeops = volumeops.VMwareVolumeOps(self._session)
        self._vmops = vmops.VMwareVMOps(self._session, self.virtapi,
                                        self._volumeops)
        self._host = host.Host(self._session)
        self._host_state = None

    @property
    def host_state(self):
        if not self._host_state:
            self._host_state = host.HostState(self._session,
                                              self._host_ip)
        return self._host_state

    def init_host(self, host):
        """Do the initialization that needs to be done."""
        # FIXME(johnathon): implement may need to do this
        pass

    def legacy_nwinfo(self):
        return False

    def list_instances(self):
        """List VM instances."""
        return self._vmops.list_instances()

    def spawn(self, context, instance, image_meta, injected_files,
              admin_password, network_info=None, block_device_info=None):
        """Create VM instance."""
        self._vmops.spawn(context, instance, image_meta, network_info, admin_password, block_device_info)

    def snapshot(self, context, instance, name, update_task_state):
        """Create snapshot from a running VM instance."""
        self._vmops.snapshot(context, instance, name, update_task_state)

    def reboot(self, context, instance, network_info, reboot_type,
               block_device_info=None, bad_volumes_callback=None):
        """Reboot VM instance."""
        self._vmops.reboot(instance, network_info)

    def destroy(self, instance, network_info, block_device_info=None,
                destroy_disks=True):
        """Destroy VM instance."""
        self._vmops.destroy(instance, network_info, destroy_disks)

    def pause(self, instance):
        """Pause VM instance."""
        self._vmops.pause(instance)

    def unpause(self, instance):
        """Unpause paused VM instance."""
        self._vmops.unpause(instance)

    def suspend(self, instance):
        """Suspend the specified instance."""
        self._vmops.suspend(instance)

    def resume(self, instance, network_info, block_device_info=None):
        """Resume the suspended VM instance."""
        self._vmops.resume(instance)

    def rescue(self, context, instance, network_info, image_meta,
               rescue_password):
        """Rescue the specified instance."""
        self._vmops.rescue(context, instance, network_info, image_meta)

    def unrescue(self, instance, network_info):
        """Unrescue the specified instance."""
        self._vmops.unrescue(instance)

    def power_off(self, instance):
        """Power off the specified instance."""
        self._vmops.power_off(instance)

    def power_on(self, context, instance, network_info,
                 block_device_info=None):
        """Power on the specified instance."""
        self._vmops._power_on(instance)

    def resume_state_on_host_boot(self, context, instance, network_info,
                                  block_device_info=None):
        """resume guest state when a host is booted."""
        # Check if the instance is running already and avoid doing
        # anything if it is.
        instances = self.list_instances()
        if instance['uuid'] not in instances:
            LOG.warn(_('Instance cannot be found in host, or in an unknown'
                'state.'), instance=instance)
        else:
            state = vm_util.get_vm_state_from_name(self._session,
                instance['uuid'])
            ignored_states = ['poweredon', 'suspended']

            if state.lower() in ignored_states:
                return
        # Instance is not up and could be in an unknown state.
        # Be as absolute as possible about getting it back into
        # a known and running state.
        self.reboot(context, instance, network_info, 'hard',
            block_device_info)

    def poll_rebooting_instances(self, timeout, instances):
        """Poll for rebooting instances."""
        self._vmops.poll_rebooting_instances(timeout, instances)

    def get_info(self, instance):
        """Return info about the VM instance."""
        return self._vmops.get_info(instance)

    def get_diagnostics(self, instance):
        """Return data about VM diagnostics."""
        return self._vmops.get_info(instance)

    def get_console_output(self, instance):
        """Return snapshot of console."""
        return self._vmops.get_console_output(instance)

    def get_vnc_console(self, instance):
        """Return link to instance's VNC console."""
        return self._vmops.get_vnc_console(instance)

    def get_volume_connector(self, instance):
        """Return volume connector information."""
        return self._volumeops.get_volume_connector(instance)

    def get_host_ip_addr(self):
        """Retrieves the IP address of the ESX host."""
        return self._host_ip

    def attach_volume(self, connection_info, instance, mountpoint):
        """Attach volume storage to VM instance."""
        return self._volumeops.attach_volume(connection_info,
                                             instance,
                                             mountpoint)

    def detach_volume(self, connection_info, instance, mountpoint):
        """Detach volume storage to VM instance."""
        return self._volumeops.detach_volume(connection_info,
                                             instance,
                                             mountpoint)

    def get_console_pool_info(self, console_type):
        """Get info about the host on which the VM resides."""
        return {'address': CONF.vmwareapi_host_ip,
                'username': CONF.vmwareapi_host_username,
                'password': CONF.vmwareapi_host_password}

    def get_available_resource(self, nodename):
        """Retrieve resource info.

        This method is called when nova-compute launches, and
        as part of a periodic task.

        :returns: dictionary describing resources

        """
        LOG.debug('Getting Available Resource')
        host_stats = self.get_host_stats(refresh=True)

        # Updating host information
        dic = {'vcpus': host_stats["vcpus"],
               'memory_mb': host_stats['host_memory_total'],
               'local_gb': host_stats['disk_total'],
               'vcpus_used': 0,
               'memory_mb_used': host_stats['host_memory_total'] -
                                 host_stats['host_memory_free'],
               'local_gb_used': host_stats['disk_used'],
               'hypervisor_type': host_stats['hypervisor_type'],
               'hypervisor_version': host_stats['hypervisor_version'],
               'hypervisor_hostname': host_stats['hypervisor_hostname'],
               'cpu_info': jsonutils.dumps(host_stats['cpu_info'])}

        return dic

    def update_host_status(self):
        """Update the status info of the host, and return those values
           to the calling program.
        """

        return self.host_state.update_status()

    def get_host_stats(self, refresh=False):
        """Return the current state of the host.

           If 'refresh' is True, run the update first.
        """
        return self.host_state.get_host_stats(refresh=refresh)

    def host_power_action(self, host, action):
        """Reboots, shuts down or powers up the host."""
        return self._host.host_power_action(host, action)

    def host_maintenance_mode(self, host, mode):
        """Start/Stop host maintenance window. On start, it triggers
           guest VMs evacuation.
        """
        return self._host.host_maintenance_mode(host, mode)

    def set_host_enabled(self, host, enabled):
        """Sets the specified host's ability to accept new instances."""
        return self._host.set_host_enabled(host, enabled)

    def inject_network_info(self, instance, network_info):
        """inject network info for specified instance."""
        self._vmops.inject_network_info(instance, network_info)

    def plug_vifs(self, instance, network_info):
        """Plug VIFs into networks."""
        #self._vmops.plug_vifs(instance, network_info)
        for (network, mapping) in network_info:
            self.vif_driver.plug(instance, (network, mapping))

    def unplug_vifs(self, instance, network_info):
        """Unplug VIFs from networks."""
        #self._vmops.unplug_vifs(instance, network_info)
        for (network, mapping) in network_info:
            self.vif_driver.unplug(instance, (network, mapping))


class VMwareVCDriver(VMwareESXDriver):
    """The ESX host connection object."""

    # The vCenter driver includes several additional VMware vSphere
    # capabilities that include API that act on hosts or groups of
    # hosts in clusters or non-cluster logical-groupings.
    #
    # vCenter is not a hypervisor itself, it works with multiple
    # hypervisor host machines and their guests. This fact can
    # subtly alter how vSphere and OpenStack interoperate.

    def __init__(self, virtapi, read_only=False, scheme="https"):
        super(VMwareVCDriver, self).__init__(virtapi)
        self._cluster_name = CONF.vmwareapi_cluster_name
        if not self._cluster_name:
            self._cluster = None
        else:
            self._cluster = vm_util.get_cluster_ref_from_name(
                            self._session, self._cluster_name)
            if self._cluster is None:
                raise exception.NotFound(_("VMware Cluster %s is not found")
                                           % self._cluster_name)
        self._volumeops = volumeops.VMwareVolumeOps(self._session,
                                                    self._cluster)
        self._vmops = vmops.VMwareVMOps(self._session, self.virtapi,
                                        self._volumeops, self._cluster)
        self._vc_state = None


    @property
    def host_state(self):
        if not self._vc_state:
            self._vc_state = host.VCState(self._session,
                                          self._host_ip,
                                          self._cluster)
        return self._vc_state

    def migrate_disk_and_power_off(self, context, instance, dest,
                                   instance_type, network_info,
                                   block_device_info=None):
        """
        Transfers the disk of a running instance in multiple phases, turning
        off the instance before the end.
        """
        return self._vmops.migrate_disk_and_power_off(context, instance,
                                                      dest, instance_type)

    def confirm_migration(self, migration, instance, network_info):
        """Confirms a resize, destroying the source VM."""
        self._vmops.confirm_migration(migration, instance, network_info)

    def finish_revert_migration(self, instance, network_info,
                                block_device_info=None, power_on=True):
        """Finish reverting a resize, powering back on the instance."""
        self._vmops.finish_revert_migration(instance, network_info,
                                            block_device_info, power_on)

    def finish_migration(self, context, migration, instance, disk_info,
                         network_info, image_meta, resize_instance=False,
                         block_device_info=None, power_on=True):
        """Completes a resize, turning on the migrated instance."""
        self._vmops.finish_migration(context, migration, instance, disk_info,
                                     network_info, image_meta, resize_instance,
                                     block_device_info, power_on)

    def live_migration(self, context, instance_ref, dest,
                       post_method, recover_method, block_migration=False,
                       migrate_data=None):
        """Live migration of an instance to another host."""
        self._vmops.live_migration(context, instance_ref, dest,
                                   post_method, recover_method,
                                   block_migration)

    def get_vnc_console(self, instance):
        """Return link to instance's VNC console using vCenter logic."""
        # In this situation, ESXi and vCenter require different
        # API logic to create a valid VNC console connection object.
        # In specific, vCenter does not actually run the VNC service
        # itself. You must talk to the VNC host underneath vCenter.
        return self._vmops.get_vnc_console_vcenter(instance)


class DataObject(object):
    """Data object base class."""
    def __init__(self, obj_name=None):
        self.obj_name = obj_name


class CloudcontrolapiDriver(driver.ComputeDriver):
    """The Cloudontrol host connection object."""

    def __init__(self, virtapi, read_only=False, scheme="https"):
        super(CloudcontrolapiDriver, self).__init__(virtapi)
        global libvirt
        if libvirt is None:
            libvirt = __import__('libvirt')
        self._session = None
        self._host_state = None
        self._initiator = None
        self._fc_wwnns = None
        self._fc_wwpns = None
        self._wrapped_conn = None
        self._wrapped_conn_lock = threading.Lock()
        self._caps = None
        self._vcpu_total = 0
        self.read_only = read_only

        #self._conn = property(self._get_connection)
        """
        self.firewall_driver = firewall.load_driver(
            DEFAULT_FIREWALL_DRIVER,
            self.virtapi,
            get_connection=self._get_connection)
        """
        #vif_class = importutils.import_class(CONF.ddcloudapi_vif_driver)
        #self.vif_driver = vif_class(self._get_connection)
           

        self._host_ip = CONF.ddcloudapi_host_ip
        host_username = CONF.ddcloudapi_host_username
        host_password = CONF.ddcloudapi_host_password
        api_retry_count = CONF.ddcloudapi_api_retry_count

        # Johnathon Test
        """
        import requests
        LOG.info("CloudcontrolapiDriver Routines")
        self._session = VMwareAPISession(self._host_ip,
                                         host_username, host_password,
                                         api_retry_count, scheme=scheme)
        ddmyaccounturl = ("https://%s/%s/myaccount" % ( CONF.ddcloudapi_host_ip, CONF.ddcloudapi_apistring, ))
        ddservermethodurl = ("https://%s/%s/%s/serverWithState?" % ( CONF.ddcloudapi_host_ip, CONF.ddcloudapi_apistring, CONF.ddcloudapi_orgid))

        print ddmyaccounturl
        print ddservermethodurl

        thisreq = requests.Session()
        response = thisreq.get(ddmyaccounturl, auth=(host_username, host_password))
        LOG.info("response: %s" % response.status_code)
        #response.content
        """

        self._host = host.Host(self._session)
        self._host_state = None
        self._cluster = 'AP1'

        print self._host

        self._volumeops = volumeops.VMwareVolumeOps(self._session,
                                                    self._cluster)
        self._vmops = vmops.VMwareVMOps(self._session, self.virtapi,
                                        self._volumeops, self._cluster)

        self._vc_state = None
        return
        import sys
        sys.exit()
        #super(CloudcontrolapiDriver, self).__init__(virtapi)
        self._cluster_name = CONF.ddcloudapi_cluster_name
        if not self._cluster_name:
            self._cluster = None
        else:
            self._cluster = vm_util.get_cluster_ref_from_name(
                            self._session, self._cluster_name)
            if self._cluster is None:
                raise exception.NotFound(_("Cloudcontrol Cluster %s is not found")
                                           % self._cluster_name)

        self._session = VMwareAPISession(self._host_ip,
                                         host_username, host_password,
                                         api_retry_count, scheme=scheme)

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



    def init_host(self, host):
        """Do the initialization that needs to be done."""
        # FIXME(johnathon): implement may need to do this
        pass

    def legacy_nwinfo(self):
        return False

    def list_instances(self):
        """List VM instances."""
        return self._vmops.list_instances()
    
    def _lookup_by_id(self, instance_id):
        """Retrieve libvirt domain object given an instance id.

        All libvirt error handling should be handled in this method and
        relevant nova exceptions should be raised in response.
        """

        try:
            return self._conn.lookupByID(instance_id)
        except libvirt.libvirtError as ex:
            error_code = ex.get_error_code()
            if error_code == libvirt.VIR_ERR_NO_DOMAIN:
                raise exception.InstanceNotFound(instance_id=instance_id)

            msg = (_("Error from libvirt while looking up %(instance_id)s: "
                     "[Error Code %(error_code)s] %(ex)s")
                   % {'instance_id': instance_id,
                      'error_code': error_code,
                      'ex': ex})
            raise exception.NovaException(msg)

    def _lookup_by_name(self, instance_name):
        """Retrieve libvirt domain object given an instance name.

        All libvirt error handling should be handled in this method and
        relevant nova exceptions should be raised in response.
        """

        try:
            return self._conn.lookupByName(instance_name)
        except libvirt.libvirtError as ex:
            error_code = ex.get_error_code()
            if error_code == libvirt.VIR_ERR_NO_DOMAIN:
                raise exception.InstanceNotFound(instance_id=instance_name)

            msg = (_('Error from libvirt while looking up %(instance_name)s: '
                     '[Error Code %(error_code)s] %(ex)s') %
                   {'instance_name': instance_name,
                    'error_code': error_code,
                    'ex': ex})
            raise exception.NovaException(msg)
       
    @exception.wrap_exception()
    def attach_interface(self, instance, image_meta, network_info):
        virt_dom = self._lookup_by_name(instance['name'])
        for (network, mapping) in network_info:
            self.vif_driver.plug(instance, (network, mapping))
            self.firewall_driver.setup_basic_filtering(instance,
                                                       [(network, mapping)])
            cfg = self.vif_driver.get_config(instance, network, mapping,
                                             image_meta)
            try:
                flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
                state = LIBVIRT_POWER_STATE[virt_dom.info()[0]]
                if state == power_state.RUNNING:
                    flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE
                virt_dom.attachDeviceFlags(cfg.to_xml(), flags)
            except libvirt.libvirtError:
                LOG.error(_('attaching network adapter failed.'),
                         instance=instance)
                self.vif_driver.unplug(instance, (network, mapping))
                raise exception.InterfaceAttachFailed(instance)

    @exception.wrap_exception()
    def detach_interface(self, instance, network_info):
        virt_dom = self._lookup_by_name(instance['name'])
        for (network, mapping) in network_info:
            cfg = self.vif_driver.get_config(instance, network, mapping, None)
            try:
                self.vif_driver.unplug(instance, (network, mapping))
                flags = libvirt.VIR_DOMAIN_AFFECT_CONFIG
                state = LIBVIRT_POWER_STATE[virt_dom.info()[0]]
                if state == power_state.RUNNING:
                    flags |= libvirt.VIR_DOMAIN_AFFECT_LIVE
                virt_dom.detachDeviceFlags(cfg.to_xml(), flags)
            except libvirt.libvirtError as ex:
                error_code = ex.get_error_code()
                if error_code == libvirt.VIR_ERR_NO_DOMAIN:
                    LOG.warn(_("During detach_interface, "
                               "instance disappeared."),
                             instance=instance)
                else:
                    LOG.error(_('detaching network adapter failed.'),
                             instance=instance)
                    raise exception.InterfaceDetachFailed(instance)
   
    def _get_connection(self):
        with self._wrapped_conn_lock:
            wrapped_conn = self._wrapped_conn

        if not wrapped_conn or not self._test_connection(wrapped_conn):
            LOG.debug(_('Connecting to libvirt: %s'), self.uri())
            if not CONF.libvirt_nonblocking:
                wrapped_conn = self._connect(self.uri(), self.read_only)
            else:
                wrapped_conn = tpool.proxy_call(
                    (libvirt.virDomain, libvirt.virConnect),
                    self._connect, self.uri(), self.read_only)
            with self._wrapped_conn_lock:
                self._wrapped_conn = wrapped_conn

            try:
                LOG.debug("Registering for lifecycle events %s" % str(self))
                wrapped_conn.domainEventRegisterAny(
                    None,
                    libvirt.VIR_DOMAIN_EVENT_ID_LIFECYCLE,
                    self._event_lifecycle_callback,
                    self)
            except Exception:
                LOG.warn(_("URI %s does not support events"),
                         self.uri())

            if self.has_min_version(MIN_LIBVIRT_CLOSE_CALLBACK_VERSION):
                try:
                    LOG.debug("Registering for connection events: %s" %
                              str(self))
                    wrapped_conn.registerCloseCallback(
                        self._close_callback, None)
                except libvirt.libvirtError:
                    LOG.debug(_("URI %s does not support connection events"),
                             self.uri())
        return wrapped_conn

    #_conn = property(_get_connection)
    #blah

    

    def spawn(self, context, instance, image_meta, injected_files,
              admin_password, network_info=None, block_device_info=None):
        """Create VM instance."""
        self._vmops.spawn(context, instance, image_meta, network_info, admin_password, block_device_info)

    def snapshot(self, context, instance, name, update_task_state):
        """Create snapshot from a running VM instance."""
        self._vmops.snapshot(context, instance, name, update_task_state)

    def reboot(self, context, instance, network_info, reboot_type,
               block_device_info=None, bad_volumes_callback=None):
        """Reboot VM instance."""
        self._vmops.reboot(instance, network_info)

    def destroy(self, instance, network_info, block_device_info=None,
                destroy_disks=True):
        """Destroy VM instance."""
        self._vmops.destroy(instance, network_info, destroy_disks)

    def pause(self, instance):
        """Pause VM instance."""
        self._vmops.pause(instance)

    def unpause(self, instance):
        """Unpause paused VM instance."""
        self._vmops.unpause(instance)

    def suspend(self, instance):
        """Suspend the specified instance."""
        self._vmops.suspend(instance)

    def resume(self, instance, network_info, block_device_info=None):
        """Resume the suspended VM instance."""
        self._vmops.resume(instance)

    def rescue(self, context, instance, network_info, image_meta,
               rescue_password):
        """Rescue the specified instance."""
        self._vmops.rescue(context, instance, network_info, image_meta)

    def unrescue(self, instance, network_info):
        """Unrescue the specified instance."""
        self._vmops.unrescue(instance)

    def power_off(self, instance):
        """Power off the specified instance."""
        self._vmops.power_off(instance)

    def power_on(self, context, instance, network_info,
                 block_device_info=None):
        """Power on the specified instance."""
        self._vmops._power_on(instance)

    def resume_state_on_host_boot(self, context, instance, network_info,
                                  block_device_info=None):
        """resume guest state when a host is booted."""
        # Check if the instance is running already and avoid doing
        # anything if it is.
        instances = self.list_instances()
        if instance['uuid'] not in instances:
            LOG.warn(_('Instance cannot be found in host, or in an unknown'
                'state.'), instance=instance)
        else:
            state = vm_util.get_vm_state_from_name(self._session,
                instance['uuid'])
            ignored_states = ['poweredon', 'suspended']

            if state.lower() in ignored_states:
                return
        # Instance is not up and could be in an unknown state.
        # Be as absolute as possible about getting it back into
        # a known and running state.
        self.reboot(context, instance, network_info, 'hard',
            block_device_info)

    def poll_rebooting_instances(self, timeout, instances):
        """Poll for rebooting instances."""
        self._vmops.poll_rebooting_instances(timeout, instances)

    def get_info(self, instance):
        """Return info about the VM instance."""
        return self._vmops.get_info(instance)

    def get_diagnostics(self, instance):
        """Return data about VM diagnostics."""
        return self._vmops.get_info(instance)

    def get_console_output(self, instance):
        """Return snapshot of console."""
        return self._vmops.get_console_output(instance)

    def get_vnc_console(self, instance):
        """Return link to instance's VNC console."""
        return self._vmops.get_vnc_console(instance)

    def get_volume_connector(self, instance):
        """Return volume connector information."""
        return self._volumeops.get_volume_connector(instance)

    def get_host_ip_addr(self):
        """Retrieves the IP address of the ESX host."""
        return self._host_ip

    def attach_volume(self, connection_info, instance, mountpoint):
        """Attach volume storage to VM instance."""
        return self._volumeops.attach_volume(connection_info,
                                             instance,
                                             mountpoint)

    def detach_volume(self, connection_info, instance, mountpoint):
        """Detach volume storage to VM instance."""
        return self._volumeops.detach_volume(connection_info,
                                             instance,
                                             mountpoint)

    def get_console_pool_info(self, console_type):
        """Get info about the host on which the VM resides."""
        return {'address': CONF.vmwareapi_host_ip,
                'username': CONF.vmwareapi_host_username,
                'password': CONF.vmwareapi_host_password}

    def get_available_resource(self, nodename):
        """Retrieve resource info.

        This method is called when nova-compute launches, and
        as part of a periodic task.

        :returns: dictionary describing resources

        """
        host_stats = self.get_host_stats(refresh=True)

        # Updating host information
        dic = {'vcpus': host_stats["vcpus"],
               'memory_mb': host_stats['host_memory_total'],
               'local_gb': host_stats['disk_total'],
               'vcpus_used': 0,
               'memory_mb_used': host_stats['host_memory_total'] -
                                 host_stats['host_memory_free'],
               'local_gb_used': host_stats['disk_used'],
               'hypervisor_type': host_stats['hypervisor_type'],
               'hypervisor_version': host_stats['hypervisor_version'],
               'hypervisor_hostname': host_stats['hypervisor_hostname'],
               'cpu_info': jsonutils.dumps(host_stats['cpu_info'])}

        return dic

    def update_host_status(self):
        """Update the status info of the host, and return those values
           to the calling program.
        """

        return self.host_state.update_status()

    def get_host_stats(self, refresh=False):
        """Return the current state of the host.

           If 'refresh' is True, run the update first.
        """
        return self.host_state.get_host_stats(refresh=refresh)

    def host_power_action(self, host, action):
        """Reboots, shuts down or powers up the host."""
        return self._host.host_power_action(host, action)

    def host_maintenance_mode(self, host, mode):
        """Start/Stop host maintenance window. On start, it triggers
           guest VMs evacuation.
        """
        return self._host.host_maintenance_mode(host, mode)

    def set_host_enabled(self, host, enabled):
        """Sets the specified host's ability to accept new instances."""
        return self._host.set_host_enabled(host, enabled)

    def inject_network_info(self, instance, network_info):
        """inject network info for specified instance."""
        self._vmops.inject_network_info(instance, network_info)

    def plug_vifs(self, instance, network_info):
        """Plug VIFs into networks."""
        #self._vmops.plug_vifs(instance, network_info)
        for (network, mapping) in network_info:
            self.vif_driver.plug(instance, (network, mapping))

    def unplug_vifs(self, instance, network_info):
        """Unplug VIFs from networks."""
        #self._vmops.unplug_vifs(instance, network_info)
        for (network, mapping) in network_info:
            self.vif_driver.unplug(instance, (network, mapping))



class VMwareAPISession(object):
    """
    Sets up a session with the ESX host and handles all
    the calls made to the host.
    """

    def __init__(self, host_ip, host_username, host_password,
                 api_retry_count, scheme="https"):
        self._host_ip = host_ip
        self._host_username = host_username
        self._host_password = host_password
        self.api_retry_count = api_retry_count
        self._scheme = scheme
        self._session_id = None
        self.vim = None
        self._create_session()

    def _get_vim_object(self):
        """Create the VIM Object instance."""
        return vim.Vim(protocol=self._scheme, host=self._host_ip)

    def _create_session(self):

        self._session = str(uuid.uuid4())
        session = DataObject()
        session.key = self._session
        #_db_content['session'][self._session] = session
        return session

        LOG.info("CREATE SESSION")
        """Creates a session with the ESX host."""
        while True:
            try:
                # Login and setup the session with the ESX host for making
                # API calls
                #self.vim = self._get_vim_object()
                LOG.info("GOT VIM OBJECT")
                #sesobj = ddsession.DDsession()
                #session = sesobj._session

                host_ip = CONF.ddcloudapi_host_ip
                host_username = CONF.ddcloudapi_host_username
                host_password = CONF.ddcloudapi_host_password
    	        host_url = CONF.ddcloudapi_url

                LOG.debug("%s" % (CONF.ddcloudapi_url))
                session = requests.Session()

                self._session_id = "cloudcontrol-AP1"
                return
                session = self.vim.Login(
                               self.vim.get_service_content().sessionManager,
                               userName=self._host_username,
                               password=self._host_password)
                # Terminate the earlier session, if possible ( For the sake of
                # preserving sessions as there is a limit to the number of
                # sessions we can have )
                if self._session_id:
                    try:
                        self.vim.TerminateSession(
                                self.vim.get_service_content().sessionManager,
                                sessionId=[self._session_id])
                    except Exception as excep:
                        # This exception is something we can live with. It is
                        # just an extra caution on our side. The session may
                        # have been cleared. We could have made a call to
                        # SessionIsActive, but that is an overhead because we
                        # anyway would have to call TerminateSession.
                        LOG.debug(excep)
                self._session_id = session.key
                return
            except Exception as excep:
                LOG.critical(_("In ddcloudapi:_create_session, "
                              "got this exception: %s") % excep)
                raise exception.NovaException(excep)

    def __del__(self):
        """Logs-out the session."""
        # Logout to avoid un-necessary increase in session count at the
        # ESX host
        try:
            self.vim.Logout(self.vim.get_service_content().sessionManager)
        except Exception as excep:
            # It is just cautionary on our part to do a logout in del just
            # to ensure that the session is not left active.
            LOG.debug(excep)

    def _is_vim_object(self, module):
        """Check if the module is a VIM Object instance."""
        return isinstance(module, vim.Vim)

    def _call_method(self, module, method, *args, **kwargs):
        """
        Calls a method within the module specified with
        args provided.
        """
        args = list(args)
        retry_count = 0
        exc = None
        last_fault_list = []
        while True:
            try:
                if not self._is_vim_object(module):
                    # If it is not the first try, then get the latest
                    # vim object
                    if retry_count > 0:
                        args = args[1:]
                    args = [self.vim] + args
                retry_count += 1
                temp_module = module

                for method_elem in method.split("."):
                    temp_module = getattr(temp_module, method_elem)

                return temp_module(*args, **kwargs)
            except error_util.VimFaultException as excep:
                # If it is a Session Fault Exception, it may point
                # to a session gone bad. So we try re-creating a session
                # and then proceeding ahead with the call.
                exc = excep
                if error_util.FAULT_NOT_AUTHENTICATED in excep.fault_list:
                    # Because of the idle session returning an empty
                    # RetrievePropertiesResponse and also the same is returned
                    # when there is say empty answer to the query for
                    # VMs on the host ( as in no VMs on the host), we have no
                    # way to differentiate.
                    # So if the previous response was also am empty response
                    # and after creating a new session, we get the same empty
                    # response, then we are sure of the response being supposed
                    # to be empty.
                    if error_util.FAULT_NOT_AUTHENTICATED in last_fault_list:
                        return []
                    last_fault_list = excep.fault_list
                    self._create_session()
                else:
                    # No re-trying for errors for API call has gone through
                    # and is the caller's fault. Caller should handle these
                    # errors. e.g, InvalidArgument fault.
                    break
            except error_util.SessionOverLoadException as excep:
                # For exceptions which may come because of session overload,
                # we retry
                exc = excep
            except Exception as excep:
                # If it is a proper exception, say not having furnished
                # proper data in the SOAP call or the retry limit having
                # exceeded, we raise the exception
                exc = excep
                break
            # If retry count has been reached then break and
            # raise the exception
            if retry_count > self.api_retry_count:
                break
            time.sleep(TIME_BETWEEN_API_CALL_RETRIES)

        LOG.critical(_("In ddcloudapi:_call_method, "
                     "got this exception: %s") % exc)
        raise

    def _get_vim(self):
        """Gets the VIM object reference."""
        if self.vim is None:
            self._create_session()
        return self.vim

    def _wait_for_task(self, instance_uuid, task_ref):
        """
        Return a Deferred that will give the result of the given task.
        The task is polled until it completes.
        """
        done = event.Event()
        loop = loopingcall.FixedIntervalLoopingCall(self._poll_task,
                                                    instance_uuid,
                                                    task_ref, done)
        loop.start(CONF.ddcloudapi_task_poll_interval)
        ret_val = done.wait()
        loop.stop()
        return ret_val

    def _poll_task(self, instance_uuid, task_ref, done):
        """
        Poll the given task, and fires the given Deferred if we
        get a result.
        """
        self._vmops.ddstatewatch(instance_uuid, task_ref, 5)

        done.send("success")
        return
        try:
            task_info = self._call_method(vim_util, "get_dynamic_property",
                            task_ref, "Task", "info")
            task_name = task_info.name
            if task_info.state in ['queued', 'running']:
                return
            elif task_info.state == 'success':
                LOG.debug(_("Task [%(task_name)s] %(task_ref)s "
                            "status: success"),
                          {'task_name': task_name, 'task_ref': task_ref})
                done.send("success")
            else:
                error_info = str(task_info.error.localizedMessage)
                LOG.warn(_("Task [%(task_name)s] %(task_ref)s "
                          "status: error %(error_info)s"),
                         {'task_name': task_name, 'task_ref': task_ref,
                          'error_info': error_info})
                done.send_exception(exception.NovaException(error_info))
        except Exception as excep:
            LOG.warn(_("In ddcloudapi:_poll_task, Got this error %s") % excep)
            done.send_exception(excep)
