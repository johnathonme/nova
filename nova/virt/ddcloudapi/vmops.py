# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright (c) 2013 Hewlett-Packard Development Company, L.P.
# Copyright (c) 2012 VMware, Inc.
# Copyright (c) 2011 Citrix Systems, Inc.
# Copyright 2011 OpenStack Foundation
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""
Class for VM tasks like spawn, snapshot, suspend, resume etc.
"""

import base64
import os
import time
import urllib
import urllib2
import uuid
import simplejson as json
import sys
from lxml import etree, objectify
import requests
from netaddr import IPAddress

# For DDAPI
import requests
from lxml import etree, objectify
from eventlet import event
from nova.openstack.common import loopingcall

from oslo.config import cfg

from nova import block_device
from nova.openstack.common import timeutils
from nova.compute import power_state
from nova.compute import task_states
from nova.compute import vm_states
from nova import conductor
from nova import context as nova_context
from nova import exception
from nova import context
from nova.openstack.common import excutils
from nova.openstack.common import log as logging
from nova.virt import driver
from nova.virt.ddcloudapi import vmwarevif as vmwarevif
from nova.virt.ddcloudapi import vim_util
from nova.virt.ddcloudapi import vm_util
from nova.virt.ddcloudapi import vmware_images
from nova import compute


vmware_vif_opts = [
    cfg.StrOpt('integration_bridge',
               default='br-int',
               help='Name of Integration Bridge'),
    ]

vmware_group = cfg.OptGroup(name='vmware',
                            title='VMware Options')

CONF = cfg.CONF
CONF.register_group(vmware_group)
CONF.register_opts(vmware_vif_opts, vmware_group)
CONF.import_opt('base_dir_name', 'nova.virt.ddcloudapi.imagecache')
CONF.import_opt('vnc_enabled', 'nova.vnc')

LOG = logging.getLogger(__name__)

VMWARE_POWER_STATES = {
                   'poweredOff': power_state.SHUTDOWN,
                    'poweredOn': power_state.RUNNING,
                    'suspended': power_state.SUSPENDED,
                    'building' : power_state.BUILDING}
VMWARE_PREFIX = 'vmware'


RESIZE_TOTAL_STEPS = 4
SPAWN_TOTAL_STEPS = 13


class VMwareVMOps(object):
    """Management class for VM-related tasks."""

    def __init__(self, session, virtapi, volumeops, cluster=None):
        """Initializer."""
        self.conductor_api = conductor.API()
        self._session = session
        self._virtapi = virtapi
        self._volumeops = volumeops
        self._cluster = cluster
        self._instance_path_base = VMWARE_PREFIX + CONF.base_dir_name
        self._default_root_device = 'vda'
        self._rescue_suffix = '-rescue'
        self._poll_rescue_last_ran = None

    def list_instances(self):
        """Lists the VM instances that are registered with the ESX host."""
        LOG.debug("Getting list of instances")

        """
        vms = self._session._call_method(vim_util, "get_objects",
                     "VirtualMachine",
                     ["name", "runtime.connectionState"])
        """
        # Lets do it using REST - https://api-ap.dimensiondata.com/oec/0.9/e2c43389-90de-4498-b7d0-056e8db0b381/serverWithState?
        import requests
        host_ip = CONF.ddcloudapi_host_ip
        host_username = CONF.ddcloudapi_host_username
        host_password = CONF.ddcloudapi_host_password
        host_url = CONF.ddcloudapi_url
        s = requests.Session()
        response = s.get('https://api-ap.dimensiondata.com/oec/0.9/e2c43389-90de-4498-b7d0-056e8db0b381/serverWithState?', auth=(host_username , host_password ))
        #LOG.info("list_instances response: %s" % response.status_code)

        #print response.content

        # Namespace stuff
        DD_NAMESPACE = "http://oec.api.opsource.net/schemas/server"
        NS = "{%s}" % DD_NAMESPACE


        from lxml import etree, objectify

        root = objectify.fromstring(response.content)

        lst_vm_names = []
        #for element in root.iter("serverWithState"):
        #    print("%s - %s" % (element.tag, element.text))

        #for e in root.serverWithState.iterchildren():
        #    print "%s => %s" % (e.tag, e.text)

        servers  = root.findall("//%sserverWithState" % NS) # find all the groups

        for server in servers:
            vm_name = server.name
            vm_id = server.attrib['id']
            vm_location = server.attrib['location']
            lst_vm_names.append(vm_name)
            LOG.debug(vm_name)
        return lst_vm_names

        #import dd_session as ddsession
        #mysession = ddsession.DDsession()
        #instances = ddsession.get_instances
        #print instances
        sys.exit()

        vms = None

        lst_vm_names = []
        for vm in vms:
            vm_name = None
            conn_state = None
            for prop in vm.propSet:
                if prop.name == "name":
                    vm_name = prop.val
                elif prop.name == "runtime.connectionState":
                    conn_state = prop.val
            # Ignoring the orphaned or inaccessible VMs
            if conn_state not in ["orphaned", "inaccessible"]:
                lst_vm_names.append(vm_name)
        LOG.debug(_("Got total of %s instances") % str(len(lst_vm_names)))
        return lst_vm_names



    def fetchNetworkid(self, label):
        #https://api-ap.dimensiondata.com/oec/0.9/e2c43389-90de-4498-b7d0-056e8db0b381/networkWithLocation
        """
        <ns4:network>
        <ns4:id>48359012-a8da-11e2-96ef-000af700e018</ns4:id>
        <ns4:name>Stack-Network-AP1-1</ns4:name>
        <ns4:description>Openstack for CaaS testing</ns4:description>
        <ns4:location>AP1</ns4:location>
        <ns4:privateNet>10.72.7.0</ns4:privateNet>
        <ns4:multicast>false</ns4:multicast>
        </ns4:network>
        """

        host_ip = CONF.ddcloudapi_host_ip
        host_username = CONF.ddcloudapi_host_username
        host_password = CONF.ddcloudapi_host_password
        host_url = CONF.ddcloudapi_url
        s = requests.Session()
        response = s.get('https://api-ap.dimensiondata.com/oec/0.9/e2c43389-90de-4498-b7d0-056e8db0b381/networkWithLocation', auth=(host_username , host_password ))
        #LOG.info("list_instances response: %s" % response.status_code)

        #print response.content

        # Namespace stuff
        DD_NAMESPACE = "http://oec.api.opsource.net/schemas/server"
        DD_NAMESPACE = "http://oec.api.opsource.net/schemas/network"
        NS = "{%s}" % DD_NAMESPACE


        from lxml import etree, objectify

        root = objectify.fromstring(response.content)

        lst_network_names = []
        #for element in root.iter("serverWithState"):
        #    print("%s - %s" % (element.tag, element.text))

        #for e in root.serverWithState.iterchildren():
        #    print "%s => %s" % (e.tag, e.text)

        networks  = root.findall("//%snetwork" % NS) # find all the groups

        for network in networks:
            if network.name == label:
                return network.id


        return None


    def fetchImageid(self, imagelabel, cclocation):
        #https://api-ap.dimensiondata.com/oec/0.9/e2c43389-90de-4498-b7d0-056e8db0b381/networkWithLocation
        #https://api-ap.dimensiondata.com/oec/0.9/base/image/deployedWithSoftwareLabels/AP1
        #https://api-ap.dimensiondata.com/oec/0.9/base/image
        """
        <ns4:network>
        <ns4:id>48359012-a8da-11e2-96ef-000af700e018</ns4:id>
        <ns4:name>Stack-Network-AP1-1</ns4:name>
        <ns4:description>Openstack for CaaS testing</ns4:description>
        <ns4:location>AP1</ns4:location>
        <ns4:privateNet>10.72.7.0</ns4:privateNet>
        <ns4:multicast>false</ns4:multicast>
        </ns4:network>
        """

        import requests
        host_ip = CONF.ddcloudapi_host_ip
        host_username = CONF.ddcloudapi_host_username
        host_password = CONF.ddcloudapi_host_password
        host_url = CONF.ddcloudapi_url
        fetchurl = ("https://api-ap.dimensiondata.com/oec/0.9/base/image/deployedWithSoftwareLabels/%s" % cclocation)
        s = requests.Session()
        response = s.get(fetchurl, auth=(host_username , host_password ))
        LOG.info("fetchImageid response: %s" % response.status_code)

        print response.content

        # Namespace stuff
        DD_NAMESPACE = "http://oec.api.opsource.net/schemas/server"
        #DD_NAMESPACE = "http://oec.api.opsource.net/schemas/network"
        #DD_NAMESPACE = ""
        NS = "{%s}" % DD_NAMESPACE


        from lxml import etree, objectify

        root = objectify.fromstring(response.content)

        lst_network_names = []
        #for element in root.iter("serverWithState"):
        #    print("%s - %s" % (element.tag, element.text))

        #for e in root.serverWithState.iterchildren():
        #    print "%s => %s" % (e.tag, e.text)

        images  = root.findall("//%sDeployedImageWithSoftwareLabels" % NS) # find all the groups
        print images

        for image in images:
            if image.name == imagelabel and image.location == cclocation:
                return image.id


        return '65d2a7c4-dfe2-11e2-a7c0-000af700e018'


    def spawn(self, context, instance, image_meta, network_info, admin_password, block_device_info=None):

        LOG.debug('SPAWNING: %s %s %s %s %s' % (context, instance, image_meta, network_info, block_device_info))
        LOG.debug('SPAWNING NETWORK_INFO:  %s' % vars(network_info))
        LOG.debug('SPAWNING NETWORK_INFO JSON:  %s' % network_info[0])
        #LOG.warning('SPAWNING NETWORK_INFO fixed_ips: %s' % network_info[0]['info']['ips'])
        LOG.warning('SPAWNING Network Label:  %s'  % network_info[0]["network"]["label"])
        LOG.warning('SPAWNING IMAGE_REF: %s' % instance['image_ref'])
        LOG.warning('SPAWNING image_meta: %s' % image_meta)

        # Get what we need
        apihostname, apiver, ddorgid, targetid, ddusername, ddpassword, location = self.ddpreconnect_withinstance(instance)

        # Preping needed variables
        #ccnetworkid = self.fetchNetworkid(network_info[0]["network"]["label"])
        ccnetworkid = "48367324-a8da-11e2-96ef-000af700e018"
        cctenantid = "e2c43389-90de-4498-b7d0-056e8db0b381"
        #ccimageid = self.fetchImageid('Ubuntu 12.04 2 CPU','AP1')
        #ccimageid = "65cf01aa-dfe2-11e2-a7c0-000af700e018"
        ccimagename = image_meta['name']
        ccimageid = self.fetchImageid(ccimagename, location)
        ccinstanceadminpwd = admin_password or "cloudcloudcloud"
        cclocation = location
        LOG.warning('SPAWN PARAMS FOR SPAWN: ccnetworkid: %s, cctenantid: %s, ccimagename: %s, ccimageid: %s, ccinstanceadminpwd: %s, cclocation: %s' % (ccnetworkid, cctenantid, ccimagename, ccimageid, ccinstanceadminpwd, cclocation))


        ddstate = ""
        ddaction = ""
        ddtotalsteps = 0
        ddstepnumber = 0
        ddstepname = ""






        #self._update_instance_progress(context, instance,
        #                               step=1,
        #                               total_steps=SPAWN_TOTAL_STEPS)

        """
        Creates a VM instance.

        Steps followed are:

        1. Create a VM with no disk and the specifics in the instance object
           like RAM size.
        2. For flat disk
          2.1. Create a dummy vmdk of the size of the disk file that is to be
               uploaded. This is required just to create the metadata file.
          2.2. Delete the -flat.vmdk file created in the above step and retain
               the metadata .vmdk file.
          2.3. Upload the disk file.
        3. For sparse disk
          3.1. Upload the disk file to a -sparse.vmdk file.
          3.2. Copy/Clone the -sparse.vmdk file to a thin vmdk.
          3.3. Delete the -sparse.vmdk file.
        4. Attach the disk to the VM by reconfiguring the same.
        5. Power on the VM.
        USefull stuff
        instance['name']

        """


        """
        client_factory = self._session._get_vim().client.factory
        service_content = self._session._get_vim().get_service_content()
        ds = vm_util.get_datastore_ref_and_name(self._session, self._cluster)
        data_store_ref = ds[0]
        data_store_name = ds[1]
        """

        def _get_image_properties():
            """
            Get the Size of the flat vmdk file that is there on the storage
            repository.
            """
            _image_info = vmware_images.get_vmdk_size_and_properties(context,
                                                        instance['image_ref'],
                                                        instance)
            image_size, image_properties = _image_info
            vmdk_file_size_in_kb = int(image_size) / 1024
            os_type = image_properties.get("vmware_ostype", "otherGuest")
            adapter_type = image_properties.get("vmware_adaptertype",
                                                "lsiLogic")
            disk_type = image_properties.get("vmware_disktype",
                                             "preallocated")
            # Get the network card type from the image properties.
            vif_model = image_properties.get("hw_vif_model", "VirtualE1000")
            return (vmdk_file_size_in_kb, os_type, adapter_type, disk_type,
                vif_model)

        """
        (vmdk_file_size_in_kb, os_type, adapter_type,
            disk_type, vif_model) = _get_image_properties()

        vm_folder_ref = self._get_vmfolder_ref()
        res_pool_ref = self._get_res_pool_ref()
        """

        def _get_vif_infos():
            vif_infos = []
            if network_info is None:
                return vif_infos
            for vif in network_info:
                mac_address = vif['address']
                network_name = vif['network']['bridge'] or \
                               CONF.vmware.integration_bridge
                if vif['network'].get_meta('should_create_vlan', False):
                    network_ref = vmwarevif.ensure_vlan_bridge(
                                                        self._session, vif,
                                                        self._cluster)
                else:
                    # FlatDHCP network without vlan.
                    network_ref = vmwarevif.ensure_vlan_bridge(
                        self._session, vif, self._cluster, create_vlan=False)
                vif_infos.append({'network_name': network_name,
                                  'mac_address': mac_address,
                                  'network_ref': network_ref,
                                  'iface_id': vif['id'],
                                  'vif_model': vif_model
                                 })
            return vif_infos

        #vif_infos = _get_vif_infos()

        """
        # Get the create vm config spec
        config_spec = vm_util.get_vm_create_spec(
                            client_factory, instance,
                            data_store_name, vif_infos, os_type)
        """

        def _execute_create_vm():
            """Create VM on ESX host."""
            LOG.debug(_("Creating VM on the ESX host"), instance=instance)



            #Prepare DescriptionJson
            instancedesc = { 'stackname': instance['hostname'], 'stackuuid':instance['uuid'], 'stackproject':instance['project_id'],'stackimage':instance['image_ref']}
            instancedescjson = json.dumps(instancedesc)

            from lxml import etree
            root = etree.Element("Server", xmlns="http://oec.api.opsource.net/schemas/server")
            itemname = etree.SubElement(root, "name")
            itemname.text = instance['display_name']
            #"0fde991d-4fa0-489b-a365-72f7d5ce85bc"
            itemdescription = etree.SubElement(root, "description")
            itemdescription.text = instancedescjson
            #("stackname:%s,stackuuid:%s,stackproject_id:%s,stackimage_ref:%s" % (instance['hostname'],instance['uuid'], instance['project_id'], instance['image_ref']) )
            itemvlanResourcePath = etree.SubElement(root, "vlanResourcePath")
            itemvlanResourcePath.text = ('/oec/%s/network/%s' % (cctenantid,ccnetworkid))
            #itemvlanResourcePath.text = "/oec/e2c43389-90de-4498-b7d0-056e8db0b381/network/48359012-a8da-11e2-96ef-000af700e018"
            itemimageResourcePath = etree.SubElement(root, "imageResourcePath")
            itemimageResourcePath.text = ("/oec/base/image/%s" % ccimageid)
            itemadministratorPassword = etree.SubElement(root, "administratorPassword")
            itemadministratorPassword.text = ccinstanceadminpwd
            itemisStarted = etree.SubElement(root, "isStarted")
            itemisStarted.text = "true"
            newserverstring = etree.tostring(root, method='xml', encoding="UTF-8", xml_declaration=True)

            LOG.debug('ABOUT TO POST:  %s'  % newserverstring)


            # NEW Method here:
            #apihostname, apiver, ddorgid, targetid, ddusername, ddpassword, location = self.ddpreconnect_withinstance(instance)
            host_url = str('https://%s/oec/%s/%s/server' % (apihostname,apiver,ddorgid))
            s = requests.Session()
            headers = {'content-type': 'application/xml'}
            response = s.post(host_url, data=newserverstring, headers=headers, auth=('dev1-apiuser', 'cloudcloudcloud'))
            LOG.debug('Action Response: %s - %s' % (response.status_code,response.content))

            if response.status_code == 200:
                LOG.info('Request to DD Cloudocontol a SUCCESS - fetching privateIp')
                cc_uuid = self.fetchCcUuid(instance['display_name'],instance['uuid'], instance)
                privateip = self.fetchPrivateIpForInstance(cc_uuid, instance)
                LOG.info('Request to DD Cloudocontol a SUCCESS - privateIp: %s' % privateip)


            """
            s = requests.Session()
            url = 'https://api-ap.dimensiondata.com/oec/0.9/e2c43389-90de-4498-b7d0-056e8db0b381/server'

            #print s.post(url, data=newserverstring, headers=headers, auth=('dev1-apiuser', 'cloudcloudcloud')).text
            LOG.debug('POSTING:  %s'  % response.text)

            LOG.debug('POST RESPONSE: %s and explain: %s' % (response, response.text))
            """

            LOG.debug(("Created VM on CloudControl "), instance=instance)

            #instance.task_state = task_states.SPAWNING
            #instance.save()
            #instance.vm_state = vm_states.BUILDING
            #instance.save()




        _execute_create_vm()
        vm_ref = vm_util.get_vm_ref(self._session, instance)

        # Set the machine.id parameter of the instance to inject
        # the NIC configuration inside the VM
        if CONF.flat_injected:
            #self._set_machine_id(client_factory, instance, network_info)
            pass




        def _power_on_vm():
            """Power on the VM."""
            LOG.debug(_("Powering on the VM instance"), instance=instance)
            # Power On the VM
            power_on_task = self._session._call_method(
                               self._session._get_vim(),
                               "PowerOnVM_Task", vm_ref)
            self._session._wait_for_task(instance['uuid'], power_on_task)
            LOG.debug(_("Powered on the VM instance"), instance=instance)
        #_power_on_vm()




        #return
        ddstatestring = "PENDING_ADD"
        self._wait_for_task(instance, ddstatestring)

        ddstatestring = "NORMAL"
        self._wait_for_task(instance, ddstatestring)

        #self._power_on(instance)


        #self._session._wait_for_task(instance['uuid'], _spawnwatch())


    def ddstatewatch(self, instance, ddstatestring, sleepsecs):
        LOG.warning('---DDSTATE-WATCH----%s------------------------------------------------' % instance['hostname'])
        ctxt = context.get_admin_context()
        time.sleep(10)

        while True:
            ddstates = self.get_info(instance)
            LOG.warning('DDSTATEWATCH - ddstates: %s' % ddstates)

            if ddstates['ddstate'] == "NORMAL" and ddstates['ddaction'] == "anyaction":
                break

            if ddstates['ddstate'] != ddstatestring:
                break

            if not ddstates['ddhasstep'] and ddstates['ddstate'] == ddstatestring:
                self._update_instance_progress(ctxt, instance,
                                       step=1,
                                       total_steps=1)
                time.sleep(sleepsecs)
                ddstates = self.get_info(instance)
                continue

            if ddstates['ddhasstep']:
                self._update_instance_progress(ctxt, instance,
                                       step=ddstates['ddstepnumber'],
                                       total_steps=ddstates['ddtotalsteps'])
                time.sleep(sleepsecs)
                pass

            if ddstates['ddstepnumber'] == ddstates['ddtotalsteps']:
                self._update_instance_progress(ctxt, instance,
                                       step=ddstates['ddstepnumber'],
                                       total_steps=ddstates['ddtotalsteps'])
                break



            if ddstates['ddstate'] == "":
                time.sleep(5)
                continue

        self._update_instance_progress(ctxt, instance,
                                       step=1,
                                       total_steps=1)

        LOG.warning('---DDSTATE-WATCH-COMPLETE---%s-------------------------------------------------' % instance['hostname'])
        LOG.info('DDSTATEWATCH - End ddstates: %s' % ddstates)

    def snapshot(self, context, instance, snapshot_name, update_task_state):
        LOG.warning('NOT YET IMPLEMENTED - snapshot')
        return
        """Create snapshot from a running VM instance.

        Steps followed are:

        1. Get the name of the vmdk file which the VM points to right now.
           Can be a chain of snapshots, so we need to know the last in the
           chain.
        2. Create the snapshot. A new vmdk is created which the VM points to
           now. The earlier vmdk becomes read-only.
        3. Call CopyVirtualDisk which coalesces the disk chain to form a single
           vmdk, rather a .vmdk metadata file and a -flat.vmdk disk data file.
        4. Now upload the -flat.vmdk file to the image store.
        5. Delete the coalesced .vmdk and -flat.vmdk created.
        """
        vm_ref = vm_util.get_vm_ref(self._session, instance)
        client_factory = self._session._get_vim().client.factory
        service_content = self._session._get_vim().get_service_content()

        def _get_vm_and_vmdk_attribs():
            # Get the vmdk file name that the VM is pointing to
            hardware_devices = self._session._call_method(vim_util,
                        "get_dynamic_property", vm_ref,
                        "VirtualMachine", "config.hardware.device")
            (vmdk_file_path_before_snapshot, controller_key, adapter_type,
             disk_type, unit_number) = vm_util.get_vmdk_path_and_adapter_type(
                                        hardware_devices)
            datastore_name = vm_util.split_datastore_path(
                                        vmdk_file_path_before_snapshot)[0]
            os_type = self._session._call_method(vim_util,
                        "get_dynamic_property", vm_ref,
                        "VirtualMachine", "summary.config.guestId")
            return (vmdk_file_path_before_snapshot, adapter_type, disk_type,
                    datastore_name, os_type)

        (vmdk_file_path_before_snapshot, adapter_type, disk_type,
         datastore_name, os_type) = _get_vm_and_vmdk_attribs()

        def _create_vm_snapshot():
            # Create a snapshot of the VM
            LOG.debug(_("Creating Snapshot of the VM instance"),
                      instance=instance)
            snapshot_task = self._session._call_method(
                        self._session._get_vim(),
                        "CreateSnapshot_Task", vm_ref,
                        name="%s-snapshot" % instance['uuid'],
                        description="Taking Snapshot of the VM",
                        memory=False,
                        quiesce=True)
            self._session._wait_for_task(instance['uuid'], snapshot_task)
            LOG.debug(_("Created Snapshot of the VM instance"),
                      instance=instance)

        _create_vm_snapshot()
        update_task_state(task_state=task_states.IMAGE_PENDING_UPLOAD)

        def _check_if_tmp_folder_exists():
            # Copy the contents of the VM that were there just before the
            # snapshot was taken
            ds_ref_ret = vim_util.get_dynamic_property(
                                    self._session._get_vim(),
                                    vm_ref,
                                    "VirtualMachine",
                                    "datastore")
            if ds_ref_ret is None:
                raise exception.DatastoreNotFound()
            ds_ref = ds_ref_ret.ManagedObjectReference[0]
            ds_browser = vim_util.get_dynamic_property(
                                       self._session._get_vim(),
                                       ds_ref,
                                       "Datastore",
                                       "browser")
            # Check if the vmware-tmp folder exists or not. If not, create one
            tmp_folder_path = vm_util.build_datastore_path(datastore_name,
                                                           "vmware-tmp")
            if not self._path_exists(ds_browser, tmp_folder_path):
                self._mkdir(vm_util.build_datastore_path(datastore_name,
                                                         "vmware-tmp"))

        _check_if_tmp_folder_exists()

        # Generate a random vmdk file name to which the coalesced vmdk content
        # will be copied to. A random name is chosen so that we don't have
        # name clashes.
        random_name = str(uuid.uuid4())
        dest_vmdk_file_location = vm_util.build_datastore_path(datastore_name,
                   "vmware-tmp/%s.vmdk" % random_name)
        dc_ref = self._get_datacenter_ref_and_name()[0]

        def _copy_vmdk_content():
            # Copy the contents of the disk ( or disks, if there were snapshots
            # done earlier) to a temporary vmdk file.
            copy_spec = vm_util.get_copy_virtual_disk_spec(client_factory,
                                                           adapter_type,
                                                           disk_type)
            LOG.debug(_('Copying disk data before snapshot of the VM'),
                      instance=instance)
            copy_disk_task = self._session._call_method(
                self._session._get_vim(),
                "CopyVirtualDisk_Task",
                service_content.virtualDiskManager,
                sourceName=vmdk_file_path_before_snapshot,
                sourceDatacenter=dc_ref,
                destName=dest_vmdk_file_location,
                destDatacenter=dc_ref,
                destSpec=copy_spec,
                force=False)
            self._session._wait_for_task(instance['uuid'], copy_disk_task)
            LOG.debug(_("Copied disk data before snapshot of the VM"),
                      instance=instance)

        _copy_vmdk_content()

        cookies = self._session._get_vim().client.options.transport.cookiejar

        def _upload_vmdk_to_image_repository():
            # Upload the contents of -flat.vmdk file which has the disk data.
            LOG.debug(_("Uploading image %s") % snapshot_name,
                      instance=instance)
            vmware_images.upload_image(
                context,
                snapshot_name,
                instance,
                os_type=os_type,
                adapter_type=adapter_type,
                image_version=1,
                host=self._session._host_ip,
                data_center_name=self._get_datacenter_ref_and_name()[1],
                datastore_name=datastore_name,
                cookies=cookies,
                file_path="vmware-tmp/%s-flat.vmdk" % random_name)
            LOG.debug(_("Uploaded image %s") % snapshot_name,
                      instance=instance)

        update_task_state(task_state=task_states.IMAGE_UPLOADING,
                          expected_state=task_states.IMAGE_PENDING_UPLOAD)
        _upload_vmdk_to_image_repository()

        def _clean_temp_data():
            """
            Delete temporary vmdk files generated in image handling
            operations.
            """
            # Delete the temporary vmdk created above.
            LOG.debug(_("Deleting temporary vmdk file %s")
                        % dest_vmdk_file_location, instance=instance)
            remove_disk_task = self._session._call_method(
                self._session._get_vim(),
                "DeleteVirtualDisk_Task",
                service_content.virtualDiskManager,
                name=dest_vmdk_file_location,
                datacenter=dc_ref)
            self._session._wait_for_task(instance['uuid'], remove_disk_task)
            LOG.debug(_("Deleted temporary vmdk file %s")
                        % dest_vmdk_file_location, instance=instance)

        _clean_temp_data()

    def reboot(self, instance, network_info):
        #https://<Cloud API URL>/oec/0.9/{org-id}/server/{server-id}?reset

        apihostname, apiver, ddorgid, targetid, ddusername, ddpassword, location = self.ddpreconnect_withinstance(instance)
        host_url = str('https://%s/oec/%s/%s/server/%s?reset' % (apihostname,apiver,ddorgid,targetid))
        s = requests.Session()
        response = s.get(host_url, auth=(ddusername , ddpassword ))
        LOG.debug('Action Response: %s - %s' % (response.status_code,response.content))

        ddstatestring = "PENDING_CHANGE"
        ddactionstring = "REBOOT_SERVER"
        self._wait_for_task(instance, ddstatestring)

        LOG.warning('IN DEV  - reboot')
        return
        """Reboot a VM instance."""
        vm_ref = vm_util.get_vm_ref(self._session, instance)
        self.plug_vifs(instance, network_info)

        lst_properties = ["summary.guest.toolsStatus", "runtime.powerState",
                          "summary.guest.toolsRunningStatus"]
        props = self._session._call_method(vim_util, "get_object_properties",
                           None, vm_ref, "VirtualMachine",
                           lst_properties)
        pwr_state = None
        tools_status = None
        tools_running_status = False
        for elem in props:
            for prop in elem.propSet:
                if prop.name == "runtime.powerState":
                    pwr_state = prop.val
                elif prop.name == "summary.guest.toolsStatus":
                    tools_status = prop.val
                elif prop.name == "summary.guest.toolsRunningStatus":
                    tools_running_status = prop.val

        # Raise an exception if the VM is not powered On.
        if pwr_state not in ["poweredOn"]:
            reason = _("instance is not powered on")
            raise exception.InstanceRebootFailure(reason=reason)

        # If latest vmware tools are installed in the VM, and that the tools
        # are running, then only do a guest reboot. Otherwise do a hard reset.
        if (tools_status == "toolsOk" and
                tools_running_status == "guestToolsRunning"):
            LOG.debug(_("Rebooting guest OS of VM"), instance=instance)
            self._session._call_method(self._session._get_vim(), "RebootGuest",
                                       vm_ref)
            LOG.debug(_("Rebooted guest OS of VM"), instance=instance)
        else:
            LOG.debug(_("Doing hard reboot of VM"), instance=instance)
            reset_task = self._session._call_method(self._session._get_vim(),
                                                    "ResetVM_Task", vm_ref)
            self._session._wait_for_task(instance['uuid'], reset_task)
            LOG.debug(_("Did hard reboot of VM"), instance=instance)


    def ddpreconnect_withinstance(self, instance):
        LOG.debug("ddpreconnect_withinstance: %s" % instance['display_name'])
        host = instance['host']
        az_name = instance['availability_zone']
        project_id = instance['project_id']
        ddorgid = ""
        ddorgname = ""
        ddusername = ""
        ddpassword = ""
        apiver = ""
        location = ""
        apihostname = ""
        geo = ""
        targetid = self.fetchCcUuid(instance['display_name'],instance['uuid'],instance)

        # Fetch AZ and AGG details from nova
        ctxt = context.get_admin_context()
        aggregates = self._virtapi.aggregate_get_by_host(
                        ctxt, host, key=None)
        #LOG.debug('AGGREGATES FOR THIS HOST: %s' % aggregates)

        for agg in aggregates:
            meta = agg['metadetails']
            #print('%s:%s' % (agg['name'],meta['filter_tenant_id']))
            try:
                if (project_id == meta['filter_tenant_id'] and instance['availability_zone'] == meta['availability_zone']):
                    ddorgid = meta['ddorgid']
                    ddorgname = meta['ddorgname']
                    ddusername = meta['ddusername']
                    ddpassword = meta['ddpassword']
                    apiver = meta['apiver']
                    location = meta['location']
                    apihostname = meta['apihostname']
                    geo = meta['geo']
            except:
                 continue


        # Exception if we got nothing
        if not aggregates:
                        msg = ('Aggregate for host %(host)s count not be'
                                ' found.') % dict(host=host)
                        raise exception.NotFound(msg)
        return apihostname, apiver, ddorgid, targetid, ddusername, ddpassword, location


    def _delete(self, instance, network_info):
        #LOG.warning('NOT YET IMPLEMENTED - _delete')
        #return
        """
        Destroy a VM instance. Steps followed are:
        1. Power off the VM, if it is in poweredOn state.
        2. Destroy the VM.
        """
        try:
            #vm_ref = vm_util.get_vm_ref(self._session, instance)
            self.power_off(instance)
            try:
                #LOG.debug(_("Destroying the VM"), instance=instance)
                #destroy_task = self._session._call_method(
                #    self._session._get_vim(),
                #    "Destroy_Task", vm_ref)
                #self._session._wait_for_task(instance['uuid'], destroy_task)
                LOG.debug(_("Destroyed the VM"), instance=instance)
            except Exception as excep:
                LOG.warn(_("In ddcloudapi:vmops:delete, got this exception"
                           " while destroying the VM: %s") % str(excep))

            if network_info:
                self.unplug_vifs(instance, network_info)
        except Exception as exc:
            LOG.exception(exc, instance=instance)

    def destroy(self, instance, network_info, destroy_disks=True):
        LOG.warning('----DESTROY---%s-----------------------------------------------------------------------------------------------------' % instance['display_name'])
        """
        host = instance['host']
        az_name = instance['availability_zone']
        project_id = instance['project_id']
        ddorgid = ""
        ddorgname = ""
        ddusername = ""
        ddpassword = ""
        apiver = ""
        location = ""
        apihostname = ""
        geo = ""
        destroytargetid = self.fetchCcUuid(instance['display_name'],instance['uuid'])

        # Fetch AZ and AGG details from nova
        ctxt = context.get_admin_context()
        aggregates = self._virtapi.aggregate_get_by_host(
                        ctxt, host, key=None)
        LOG.debug('AGGREGATES FOR THIS HOST: %s' % aggregates)

        for agg in aggregates:
            meta = agg['metadetails']
            print('%s:%s' % (agg['name'],meta['filter_tenant_id']))
            if project_id == meta['filter_tenant_id']:
                ddorgid = meta['ddorgid']
                ddorgname = meta['ddorgname']
                ddusername = meta['ddusername']
                ddpassword = meta['ddpassword']
                apiver = meta['apiver']
                location = meta['location']
                apihostname = meta['apihostname']
                geo = meta['geo']


        # Exception if we got nothing
        if not aggregates:
                        msg = ('Aggregate for host %(host)s count not be'
                                ' found.') % dict(host=host)
                        raise exception.NotFound(msg)
        """
	# Shutdown if needed
	ddstates = self.get_info(instance)
	if (ddstates['state'] != power_state.NOSTATE and ddstates['ccexists']):
                LOG.warning('DESTROY - Not at NOSTATE - Powering Off')
        	self.power_off(instance)	

        # Emergency Terminate
        ddstates = self.get_info(instance)
        if not ddstates['ccexists']:
            return

        # Terminate Procedure Proper
        apihostname, apiver, ddorgid, targetid, ddusername, ddpassword, location = self.ddpreconnect_withinstance(instance)
        host_url = str('https://%s/oec/%s/%s/server/%s?delete' % (apihostname,apiver,ddorgid,targetid))
        s = requests.Session()
        response = s.get(host_url, auth=(ddusername , ddpassword ))
        LOG.debug('Action Response: %s - %s' % (response.status_code,response.content))

        ddstatestring = "PENDING_DELETE"
        #self.ddstatewatch(instance, ddstatestring, 1)
        self._wait_for_task(instance, ddstatestring)

        LOG.warning('----DESTROYED!-----%s----------------------------------------------------------------------------------------------------' % instance['display_name'])
        return

        """
        Destroy a VM instance. Steps followed are:
        1. Power off the VM, if it is in poweredOn state.
        2. Un-register a VM.
        3. Delete the contents of the folder holding the VM related data.
        """
        try:
            vm_ref = vm_util.get_vm_ref(self._session, instance)
            lst_properties = ["config.files.vmPathName", "runtime.powerState"]
            props = self._session._call_method(vim_util,
                        "get_object_properties",
                        None, vm_ref, "VirtualMachine", lst_properties)
            pwr_state = None
            for elem in props:
                vm_config_pathname = None
                for prop in elem.propSet:
                    if prop.name == "runtime.powerState":
                        pwr_state = prop.val
                    elif prop.name == "config.files.vmPathName":
                        vm_config_pathname = prop.val
            if vm_config_pathname:
                _ds_path = vm_util.split_datastore_path(vm_config_pathname)
                datastore_name, vmx_file_path = _ds_path
            # Power off the VM if it is in PoweredOn state.
            if pwr_state == "poweredOn":
                LOG.debug(_("Powering off the VM"), instance=instance)
                poweroff_task = self._session._call_method(
                       self._session._get_vim(),
                       "PowerOffVM_Task", vm_ref)
                self._session._wait_for_task(instance['uuid'], poweroff_task)
                LOG.debug(_("Powered off the VM"), instance=instance)

            # Un-register the VM
            try:
                LOG.debug(_("Unregistering the VM"), instance=instance)
                self._session._call_method(self._session._get_vim(),
                                           "UnregisterVM", vm_ref)
                LOG.debug(_("Unregistered the VM"), instance=instance)
            except Exception as excep:
                LOG.warn(_("In ddcloudapi:vmops:destroy, got this exception"
                           " while un-registering the VM: %s") % str(excep))

            if network_info:
                self.unplug_vifs(instance, network_info)

            # Delete the folder holding the VM related content on
            # the datastore.
            if destroy_disks:
                try:
                    dir_ds_compliant_path = vm_util.build_datastore_path(
                                     datastore_name,
                                     os.path.dirname(vmx_file_path))
                    LOG.debug(_("Deleting contents of the VM from "
                                "datastore %(datastore_name)s") %
                               {'datastore_name': datastore_name},
                              instance=instance)
                    vim = self._session._get_vim()
                    delete_task = self._session._call_method(
                        vim,
                        "DeleteDatastoreFile_Task",
                        vim.get_service_content().fileManager,
                        name=dir_ds_compliant_path,
                        datacenter=self._get_datacenter_ref_and_name()[0])
                    self._session._wait_for_task(instance['uuid'], delete_task)
                    LOG.debug(_("Deleted contents of the VM from "
                                "datastore %(datastore_name)s") %
                               {'datastore_name': datastore_name},
                              instance=instance)
                except Exception as excep:
                    LOG.warn(_("In ddcloudapi:vmops:destroy, "
                                 "got this exception while deleting"
                                 " the VM contents from the disk: %s")
                                 % str(excep))
        except Exception as exc:
            LOG.exception(exc, instance=instance)

    def pause(self, instance):
        msg = _("pause not supported for ddcloudapi")
        raise NotImplementedError(msg)

    def unpause(self, instance):
        msg = _("unpause not supported for ddcloudapi")
        raise NotImplementedError(msg)

    def suspend(self, instance):
        apihostname, apiver, ddorgid, targetid, ddusername, ddpassword, location = self.ddpreconnect_withinstance(instance)
        host_url = str('https://%s/oec/%s/%s/server/%s?shutdown' % (apihostname,apiver,ddorgid,targetid))
        s = requests.Session()
        response = s.get(host_url, auth=(ddusername , ddpassword ))
        LOG.debug('Action Response: %s - %s' % (response.status_code,response.content))

        ddstatestring = "PENDING_CHANGE"
        ddactionstring = "POWER_OFF_SERVER"
        #self._session._wait_for_task(instance['uuid'], self.ddstatewatch(instance, ddstatestring, 1))
        self._wait_for_task(instance, ddstatestring)


        LOG.warning('IN DEV - suspend')
        return
        """Suspend the specified instance."""
        vm_ref = vm_util.get_vm_ref(self._session, instance)
        pwr_state = self._session._call_method(vim_util,
                    "get_dynamic_property", vm_ref,
                    "VirtualMachine", "runtime.powerState")
        # Only PoweredOn VMs can be suspended.
        if pwr_state == "poweredOn":
            LOG.debug(_("Suspending the VM"), instance=instance)
            suspend_task = self._session._call_method(self._session._get_vim(),
                    "SuspendVM_Task", vm_ref)
            self._session._wait_for_task(instance['uuid'], suspend_task)
            LOG.debug(_("Suspended the VM"), instance=instance)
        # Raise Exception if VM is poweredOff
        elif pwr_state == "poweredOff":
            reason = _("instance is powered off and cannot be suspended.")
            raise exception.InstanceSuspendFailure(reason=reason)
        else:
            LOG.debug(_("VM was already in suspended state. So returning "
                      "without doing anything"), instance=instance)

    def resume(self, instance):

        apihostname, apiver, ddorgid, targetid, ddusername, ddpassword, location  = self.ddpreconnect_withinstance(instance)
        host_url = str('https://%s/oec/%s/%s/server/%s?start' % (apihostname,apiver,ddorgid,targetid))
        s = requests.Session()
        response = s.get(host_url, auth=(ddusername , ddpassword ))
        LOG.debug('Action Response: %s - %s' % (response.status_code,response.content))

        ddstatestring = "PENDING_CHANGE"
        ddactionstring = "START_SERVER"
        self._wait_for_task(instance, ddstatestring)

        LOG.warning('IN DEV - resume')
        return
        """Resume the specified instance."""
        vm_ref = vm_util.get_vm_ref(self._session, instance)
        pwr_state = self._session._call_method(vim_util,
                                     "get_dynamic_property", vm_ref,
                                     "VirtualMachine", "runtime.powerState")
        if pwr_state.lower() == "suspended":
            LOG.debug(_("Resuming the VM"), instance=instance)
            suspend_task = self._session._call_method(
                                        self._session._get_vim(),
                                       "PowerOnVM_Task", vm_ref)
            self._session._wait_for_task(instance['uuid'], suspend_task)
            LOG.debug(_("Resumed the VM"), instance=instance)
        else:
            reason = _("instance is not in a suspended state")
            raise exception.InstanceResumeFailure(reason=reason)

    def rescue(self, context, instance, network_info, image_meta):
        LOG.warning('NOT YET IMPLEMENTED - rescue')
        return
        """Rescue the specified instance.

            - shutdown the instance VM.
            - spawn a rescue VM (the vm name-label will be instance-N-rescue).

        """
        vm_ref = vm_util.get_vm_ref(self._session, instance)

        self.power_off(instance)
        instance['name'] = instance['name'] + self._rescue_suffix
        self.spawn(context, instance, image_meta, network_info)

        # Attach vmdk to the rescue VM
        hardware_devices = self._session._call_method(vim_util,
                        "get_dynamic_property", vm_ref,
                        "VirtualMachine", "config.hardware.device")
        vmdk_path, controller_key, adapter_type, disk_type, unit_number \
            = vm_util.get_vmdk_path_and_adapter_type(hardware_devices)
        # Figure out the correct unit number
        unit_number = unit_number + 1
        rescue_vm_ref = vm_util.get_vm_ref_from_uuid(self._session,
                                                     instance['uuid'])
        if rescue_vm_ref is None:
            rescue_vm_ref = vm_util.get_vm_ref_from_name(self._session,
                                                     instance['name'])
        self._volumeops.attach_disk_to_vm(
                                rescue_vm_ref, instance,
                                adapter_type, disk_type, vmdk_path,
                                controller_key=controller_key,
                                unit_number=unit_number)

    def unrescue(self, instance):
        LOG.warning('NOT YET IMPLEMENTED - unrescue')
        return
        """Unrescue the specified instance."""
        instance_orig_name = instance['name']
        instance['name'] = instance['name'] + self._rescue_suffix
        self.destroy(instance, None)
        instance['name'] = instance_orig_name
        self._power_on(instance)

    def power_off(self, instance):

        apihostname, apiver, ddorgid, targetid, ddusername, ddpassword, location = self.ddpreconnect_withinstance(instance)
        host_url = str('https://%s/oec/%s/%s/server/%s?poweroff' % (apihostname,apiver,ddorgid,targetid))
        s = requests.Session()
        response = s.get(host_url, auth=(ddusername , ddpassword ))
        LOG.debug('Action Response: %s - %s' % (response.status_code,response.content))

        ddstatestring = "PENDING_CHANGE"
        ddactionstring = "POWER_OFF_SERVER"
        #self._session._wait_for_task(instance['uuid'], self.ddstatewatch(instance, ddstatestring, 1))
        self._wait_for_task(instance, ddstatestring)

        LOG.warning('IN DEV - power_off')
        return
        """Power off the specified instance."""
        vm_ref = vm_util.get_vm_ref(self._session, instance)

        pwr_state = self._session._call_method(vim_util,
                    "get_dynamic_property", vm_ref,
                    "VirtualMachine", "runtime.powerState")
        # Only PoweredOn VMs can be powered off.
        if pwr_state == "poweredOn":
            LOG.debug(_("Powering off the VM"), instance=instance)
            poweroff_task = self._session._call_method(
                                        self._session._get_vim(),
                                        "PowerOffVM_Task", vm_ref)
            self._session._wait_for_task(instance['uuid'], poweroff_task)
            LOG.debug(_("Powered off the VM"), instance=instance)
        # Raise Exception if VM is suspended
        elif pwr_state == "suspended":
            reason = _("instance is suspended and cannot be powered off.")
            raise exception.InstancePowerOffFailure(reason=reason)
        else:
            LOG.debug(_("VM was already in powered off state. So returning "
                        "without doing anything"), instance=instance)

    def _power_on(self, instance):

        apihostname, apiver, ddorgid, targetid, ddusername, ddpassword, location  = self.ddpreconnect_withinstance(instance)
        host_url = str('https://%s/oec/%s/%s/server/%s?start' % (apihostname,apiver,ddorgid,targetid))
        s = requests.Session()
        response = s.get(host_url, auth=(ddusername , ddpassword ))
        LOG.debug('Action Response: %s - %s' % (response.status_code,response.content))

        ddstatestring = "PENDING_CHANGE"
        ddactionstring = "START_SERVER"
        self._wait_for_task(instance, ddstatestring)


        #LOG.warning('NOT YET IMPLEMENTED - _power_on')
        return
        """Power on the specified instance."""
        vm_ref = vm_util.get_vm_ref(self._session, instance)

        pwr_state = self._session._call_method(vim_util,
                                     "get_dynamic_property", vm_ref,
                                     "VirtualMachine", "runtime.powerState")
        if pwr_state == "poweredOn":
            LOG.debug(_("VM was already in powered on state. So returning "
                      "without doing anything"), instance=instance)
        # Only PoweredOff and Suspended VMs can be powered on.
        else:
            LOG.debug(_("Powering on the VM"), instance=instance)
            poweron_task = self._session._call_method(
                                        self._session._get_vim(),
                                        "PowerOnVM_Task", vm_ref)
            self._session._wait_for_task(instance['uuid'], poweron_task)
            LOG.debug(_("Powered on the VM"), instance=instance)

    def fetchPrivateIpForInstance(self, dd_uuid, instance):

        #LOG.info("Fetching stackdisplay_name: %s with stackuuid: %s" % (stackdisplay_name, stackuuid))
        #self._host_ip = CONF.ddcloudapi_host_ip
        #host_username = CONF.ddcloudapi_host_username
        #host_password = CONF.ddcloudapi_host_password
        #api_retry_count = CONF.ddcloudapi_api_retry_count
        #ddservermethodurl = ("https://%s/%s/%s/serverWithState?" % ( CONF.ddcloudapi_host_ip, CONF.ddcloudapi_apistring, CONF.ddcloudapi_orgid))


        LOG.debug("ddpreconnect_withinstance: %s" % instance['display_name'])
        host = instance['host']
        az_name = instance['availability_zone']
        project_id = instance['project_id']
        ddorgid = ""
        ddorgname = ""
        ddusername = ""
        ddpassword = ""
        apiver = ""
        location = ""
        apihostname = ""
        geo = ""

        # Fetch AZ and AGG details from nova
        ctxt = context.get_admin_context()
        aggregates = self._virtapi.aggregate_get_by_host(
                        ctxt, host, key=None)
        #LOG.debug('AGGREGATES FOR THIS HOST: %s' % aggregates)

        for agg in aggregates:
            meta = agg['metadetails']
            #print('%s:%s' % (agg['name'],meta['filter_tenant_id']))
            try:
                if (project_id == meta['filter_tenant_id'] and instance['availability_zone'] == meta['availability_zone']):
                    ddorgid = meta['ddorgid']
                    ddorgname = meta['ddorgname']
                    ddusername = meta['ddusername']
                    ddpassword = meta['ddpassword']
                    apiver = meta['apiver']
                    location = meta['location']
                    apihostname = meta['apihostname']
                    geo = meta['geo']
            except:
                 continue


        # Exception if we got nothing
        if not aggregates:
                        msg = ('Aggregate for host %(host)s count not be'
                                ' found.') % dict(host=host)
                        raise exception.NotFound(msg)



        host_url = str('https://%s/oec/%s/%s/serverWithState?' % (apihostname,apiver,ddorgid))
        s = requests.Session()
        response = s.get(host_url, auth=(ddusername , ddpassword ))
        #print response.content


        # Namespace stuff
        DD_NAMESPACE = "http://oec.api.opsource.net/schemas/server"
        #DD_NAMESPACE = "http://oec.api.opsource.net/schemas/network"
        #DD_NAMESPACE = ""
        NS = "{%s}" % DD_NAMESPACE


        # XML Parse Stuff
        root = objectify.fromstring(response.content)
        servers  = root.findall("//%sserverWithState" % NS ) # find all the groups

        for server in servers:
            if server.attrib['id'] == dd_uuid:
                return server.privateIp

        return None


    def fetchCcUuid(self, stackdisplay_name, stackuuid, instance):
        LOG.info("Fetching stackdisplay_name: %s with stackuuid: %s" % (stackdisplay_name, stackuuid))
        self._host_ip = CONF.ddcloudapi_host_ip
        host_username = CONF.ddcloudapi_host_username
        host_password = CONF.ddcloudapi_host_password
        api_retry_count = CONF.ddcloudapi_api_retry_count



        LOG.debug("ddpreconnect_withinstance: %s" % instance['display_name'])
        host = instance['host']
        az_name = instance['availability_zone']
        project_id = instance['project_id']
        ddorgid = ""
        ddorgname = ""
        ddusername = ""
        ddpassword = ""
        apiver = ""
        location = ""
        apihostname = ""
        geo = ""

        # Fetch AZ and AGG details from nova
        ctxt = context.get_admin_context()
        aggregates = self._virtapi.aggregate_get_by_host(
                        ctxt, host, key=None)
        #LOG.debug('AGGREGATES FOR THIS HOST: %s' % aggregates)

        for agg in aggregates:
            meta = agg['metadetails']
            #print('%s:%s' % (agg['name'],meta['filter_tenant_id']))
            try:
                if (project_id == meta['filter_tenant_id'] and instance['availability_zone'] == meta['availability_zone']):
                    ddorgid = meta['ddorgid']
                    ddorgname = meta['ddorgname']
                    ddusername = meta['ddusername']
                    ddpassword = meta['ddpassword']
                    apiver = meta['apiver']
                    location = meta['location']
                    apihostname = meta['apihostname']
                    geo = meta['geo']
            except:
                 continue


        # Exception if we got nothing
        if not aggregates:
                        msg = ('Aggregate for host %(host)s count not be'
                                ' found.') % dict(host=host)
                        raise exception.NotFound(msg)


        #ddservermethodurl = ("https://%s/%s/%s/serverWithState?" % ( CONF.ddcloudapi_host_ip, CONF.ddcloudapi_apistring, CONF.ddcloudapi_orgid))
        #s = requests.Session()
        #response = s.get(ddservermethodurl, auth=(host_username , host_password ))
        host_url = str('https://%s/oec/%s/%s/serverWithState?' % (apihostname,apiver,ddorgid))
        s = requests.Session()
        response = s.get(host_url, auth=(ddusername , ddpassword ))
        print response.content

        # Namespace stuff
        DD_NAMESPACE = "http://oec.api.opsource.net/schemas/server"
        #DD_NAMESPACE = "http://oec.api.opsource.net/schemas/network"
        #DD_NAMESPACE = ""
        NS = "{%s}" % DD_NAMESPACE


        # XML Parse Stuff
        root = objectify.fromstring(response.content)
        servers  = root.findall("//%sserverWithState" % NS ) # find all the groups

        for server in servers:
            if server.name == stackdisplay_name:
                return server.attrib['id']

        return None

    def power_on(self, context, instance, network_info, block_device_info):
        LOG.warning('NOT YET IMPLEMENTED - power_on')
        return

        #https://<Cloud API URL>/oec/0.9/{org-id}/server/{server-id}?start

        self._host_ip = CONF.ddcloudapi_host_ip
        host_username = CONF.ddcloudapi_host_username
        host_password = CONF.ddcloudapi_host_password
        api_retry_count = CONF.ddcloudapi_api_retry_count
        ccuid = self.fetchCcUuid(instance['display_name'],instance['uuid'],instance)

        # Johnathon Test
        LOG.info("Powering On Server: %s - %s" % (instance['display_name'],instance['uuid']))
        #self._session = VMwareAPISession(self._host_ip,
        #                                 host_username, host_password,
        #                                 api_retry_count, scheme=scheme)
        ddservermethodurl = ("https://%s/%s/%s/server/%s?start" % ( CONF.ddcloudapi_host_ip, CONF.ddcloudapi_apistring, CONF.ddcloudapi_orgidi,ccuid))

        #print ddservermethodurl

        thisreq = requests.Session()
        response = thisreq.get(ddservermethodurl, auth=(host_username, host_password))
        LOG.info("response: %s" % response.status_code)
        #response.content

        self._power_on(instance)

    def _get_orig_vm_name_label(self, instance):
        return instance['name'] + '-orig'

    def _update_instance_progress(self, context, instance, step, total_steps):
        """Update instance progress percent to reflect current step number
        """
        # Divide the action's workflow into discrete steps and "bump" the
        # instance's progress field as each step is completed.
        #
        # For a first cut this should be fine, however, for large VM images,
        # the clone disk step begins to dominate the equation. A
        # better approximation would use the percentage of the VM image that
        # has been streamed to the destination host.

        progress = round(float(step) / total_steps * 100)
        instance_uuid = instance['uuid']
        LOG.warning(_("Updating instance '%(instance_uuid)s' progress to"
                    " %(progress)d"),
                  {'instance_uuid': instance_uuid, 'progress': progress},
                  instance=instance)
        self._virtapi.instance_update(context, instance_uuid,
                                      {'progress': progress})

    def migrate_disk_and_power_off(self, context, instance, dest,
                                   instance_type):
        LOG.warning('NOT YET IMPLEMENTED - migrate_disk_and_power_off')
        return
        """
        Transfers the disk of a running instance in multiple phases, turning
        off the instance before the end.
        """
        # 0. Zero out the progress to begin
        self._update_instance_progress(context, instance,
                                       step=0,
                                       total_steps=RESIZE_TOTAL_STEPS)

        vm_ref = vm_util.get_vm_ref(self._session, instance)
        host_ref = self._get_host_ref_from_name(dest)
        if host_ref is None:
            raise exception.HostNotFound(host=dest)

        # 1. Power off the instance
        self.power_off(instance)
        self._update_instance_progress(context, instance,
                                       step=1,
                                       total_steps=RESIZE_TOTAL_STEPS)

        # 2. Rename the original VM with suffix '-orig'
        name_label = self._get_orig_vm_name_label(instance)
        LOG.debug(_("Renaming the VM to %s") % name_label,
                  instance=instance)
        rename_task = self._session._call_method(
                            self._session._get_vim(),
                            "Rename_Task", vm_ref, newName=name_label)
        self._session._wait_for_task(instance['uuid'], rename_task)
        LOG.debug(_("Renamed the VM to %s") % name_label,
                  instance=instance)
        self._update_instance_progress(context, instance,
                                       step=2,
                                       total_steps=RESIZE_TOTAL_STEPS)

        # Get the clone vm spec
        ds_ref = vm_util.get_datastore_ref_and_name(
                            self._session, None, dest)[0]
        client_factory = self._session._get_vim().client.factory
        rel_spec = vm_util.relocate_vm_spec(client_factory, ds_ref, host_ref)
        clone_spec = vm_util.clone_vm_spec(client_factory, rel_spec)
        vm_folder_ref = self._get_vmfolder_ref()

        # 3. Clone VM on ESX host
        LOG.debug(_("Cloning VM to host %s") % dest, instance=instance)
        vm_clone_task = self._session._call_method(
                                self._session._get_vim(),
                                "CloneVM_Task", vm_ref,
                                folder=vm_folder_ref,
                                name=instance['name'],
                                spec=clone_spec)
        self._session._wait_for_task(instance['uuid'], vm_clone_task)
        LOG.debug(_("Cloned VM to host %s") % dest, instance=instance)
        self._update_instance_progress(context, instance,
                                       step=3,
                                       total_steps=RESIZE_TOTAL_STEPS)

    def confirm_migration(self, migration, instance, network_info):
        LOG.warning('NOT YET IMPLEMENTED - confirm_migration')
        return
        """Confirms a resize, destroying the source VM."""
        instance_name = self._get_orig_vm_name_label(instance)
        # Destroy the original VM.
        vm_ref = vm_util.get_vm_ref_from_uuid(self._session, instance['uuid'])
        if vm_ref is None:
            vm_ref = vm_util.get_vm_ref_from_name(self._session, instance_name)
        if vm_ref is None:
            LOG.debug(_("instance not present"), instance=instance)
            return

        try:
            LOG.debug(_("Destroying the VM"), instance=instance)
            destroy_task = self._session._call_method(
                                        self._session._get_vim(),
                                        "Destroy_Task", vm_ref)
            self._session._wait_for_task(instance['uuid'], destroy_task)
            LOG.debug(_("Destroyed the VM"), instance=instance)
        except Exception as excep:
            LOG.warn(_("In ddcloudapi:vmops:confirm_migration, got this "
                     "exception while destroying the VM: %s") % str(excep))

        if network_info:
            self.unplug_vifs(instance, network_info)

    def finish_revert_migration(self, instance, network_info,
                                block_device_info, power_on=True):
        LOG.warning('NOT YET IMPLEMENTED - finish_revert_migration')
        return
        """Finish reverting a resize."""
        # The original vm was suffixed with '-orig'; find it using
        # the old suffix, remove the suffix, then power it back on.
        name_label = self._get_orig_vm_name_label(instance)
        vm_ref = vm_util.get_vm_ref_from_name(self._session, name_label)
        if vm_ref is None:
            raise exception.InstanceNotFound(instance_id=name_label)

        LOG.debug(_("Renaming the VM from %s") % name_label,
                  instance=instance)
        rename_task = self._session._call_method(
                            self._session._get_vim(),
                            "Rename_Task", vm_ref, newName=instance['uuid'])
        self._session._wait_for_task(instance['uuid'], rename_task)
        LOG.debug(_("Renamed the VM from %s") % name_label,
                  instance=instance)
        if power_on:
            self._power_on(instance)

    def finish_migration(self, context, migration, instance, disk_info,
                         network_info, image_meta, resize_instance=False,
                         block_device_info=None, power_on=True):
        LOG.warning('NOT YET IMPLEMENTED - finish_migration')
        return
        """Completes a resize, turning on the migrated instance."""
        # 4. Start VM
        if power_on:
            self._power_on(instance)
        self._update_instance_progress(context, instance,
                                       step=4,
                                       total_steps=RESIZE_TOTAL_STEPS)

    def live_migration(self, context, instance_ref, dest,
                       post_method, recover_method, block_migration=False):
        LOG.warning('NOT YET IMPLEMENTED - live_migration')
        return
        """Spawning live_migration operation for distributing high-load."""
        vm_ref = vm_util.get_vm_ref(self._session, instance_ref)

        host_ref = self._get_host_ref_from_name(dest)
        if host_ref is None:
            raise exception.HostNotFound(host=dest)

        LOG.debug(_("Migrating VM to host %s") % dest, instance=instance_ref)
        try:
            vm_migrate_task = self._session._call_method(
                                    self._session._get_vim(),
                                    "MigrateVM_Task", vm_ref,
                                    host=host_ref,
                                    priority="defaultPriority")
            self._session._wait_for_task(instance_ref['uuid'], vm_migrate_task)
        except Exception:
            with excutils.save_and_reraise_exception():
                recover_method(context, instance_ref, dest, block_migration)
        post_method(context, instance_ref, dest, block_migration)
        LOG.debug(_("Migrated VM to host %s") % dest, instance=instance_ref)

    def poll_rebooting_instances(self, timeout, instances):
        LOG.warning('NOT YET IMPLEMENTED - poll_reboot_instances')
        return
        """Poll for rebooting instances."""
        ctxt = nova_context.get_admin_context()

        instances_info = dict(instance_count=len(instances),
                timeout=timeout)

        if instances_info["instance_count"] > 0:
            LOG.info(_("Found %(instance_count)d hung reboots "
                    "older than %(timeout)d seconds") % instances_info)

        for instance in instances:
            LOG.info(_("Automatically hard rebooting"), instance=instance)
            self.conductor_api.compute_reboot(ctxt, instance, "HARD")

    def get_info(self, instance):
        """Return data about the VM instance."""
        """
        vm_ref = vm_util.get_vm_ref(self._session, instance)

        lst_properties = ["summary.config.numCpu",
                    "summary.config.memorySizeMB",
                    "runtime.powerState"]
        vm_props = self._session._call_method(vim_util,
                    "get_object_properties", None, vm_ref, "VirtualMachine",
                    lst_properties)
        max_mem = None
        pwr_state = None
        num_cpu = None
        for elem in vm_props:
            for prop in elem.propSet:
                if prop.name == "summary.config.numCpu":
                    num_cpu = int(prop.val)
                elif prop.name == "summary.config.memorySizeMB":
                    # In MB, but we want in KB
                    max_mem = int(prop.val) * 1024
                elif prop.name == "runtime.powerState":
                    pwr_state = VMWARE_POWER_STATES[prop.val]

        return {'state': pwr_state,
                'max_mem': max_m2147483648em,
                'mem': max_mem,
                'num_cpu': num_cpu,
                'cpu_time': 0}
        """
        # Get we need from the instance
        az_name = instance['availability_zone']
        project_id = instance['project_id']
        host = instance['host']

        #{u'ddpassword': u'Sma11ta1k', u'availability_zone': u'Tokyo MCP', u'ddorgid': u'e2c43389-90de-4498-b7d0-056e8db0b381', u'apiver': u'0.9', u'location': u'AP1', u'apihostname': u'api-ap.dimensiondata.com', u'ddusername': u'Johnathon.Meichtry-AP-Z000002-CBU-Dev1', u'project_id': u'cd2407e2994c4557891fb9df175957fe', u'geo': u'AP', u'ddorgname': u'AP-Z000002-Dimension Data Asia Pacific-OHQ-CBU-Dev1'}

        ddorgid = ""
        ddorgname = ""
        ddusername = ""
        ddpassword = ""
        apiver = ""
        location = ""
        apihostname = ""
        geo = ""

        # Fetch AZ and AGG details from nova
        ctxt = context.get_admin_context()
        aggregates = self._virtapi.aggregate_get_by_host(
                        ctxt, host, key=None)
        LOG.warning('AGGREGATES FOR THIS HOST: %s' % aggregates)

        for agg in aggregates:
            meta = agg['metadetails']
            #print('%s:%s' % (agg['name'],meta['filter_tenant_id']))
            try:
                if project_id == meta['filter_tenant_id']:
                    ddorgid = meta['ddorgid']
                    ddorgname = meta['ddorgname']
                    ddusername = meta['ddusername']
                    ddpassword = meta['ddpassword']
                    apiver = meta['apiver']
                    location = meta['location']
                    apihostname = meta['apihostname']
                    geo = meta['geo']
            except:
                continue



        # Exception if we got nothing
        if not aggregates:
                        msg = ('Aggregate for host %(host)s count not be'
                                ' found.') % dict(host=host)
                        raise exception.NotFound(msg)

        host_url = str('https://%s/oec/%s/%s/serverWithState?' % (apihostname,apiver,ddorgid))
        s = requests.Session()
        response = s.get(host_url, auth=(ddusername , ddpassword ))
        #LOG.info("list_instances response: %s" % response.status_code)
        #print response.content

        # Namespace stuff
        DD_NAMESPACE = "http://oec.api.opsource.net/schemas/server"
        #DD_NAMESPACE = "http://oec.api.opsource.net/schemas/network"
        #DD_NAMESPACE = ""
        NS = "{%s}" % DD_NAMESPACE


        # XML Parse Stuff
        root = objectify.fromstring(response.content)

        lst_network_names = []
        #for element in root.iter("serverWithState"):
        #    print("%s - %s" % (element.tag, element.text))

        #for e in root.serverWithState.iterchildren():
        #    print "%s => %s" % (e.tag, e.text)

        servers  = root.findall("//%sserverWithState" % NS ) # find all the groups
        #state = power_state.SHUTDOWN
        state = 9
        mem = 9999
        num_cpu = 50
        max_mem = 9999
        cpu_time = 0
        decoded_serverdesc = ""
        ddstate = ""
        ddaction = "anyaction"
        ddtotalsteps = 2
        ddstepnumber = 1
        ddstepname = "anystep"
        ddhasstep = False


        #LOG.debug("INSTANCE: %s" % vars(instance))
        ccexists = False

        for server in servers:
            hasjson = False
            #LOG.debug("INSTANCE COMPARE for %s and %s:" % (server.name, instance['display_name']))
            privateIp = str(server.privateIp)
            ip = IPAddress(privateIp)
            descriptionstr = str(server.description)

            if server.find('%sstate' % NS) is not None:
                ddstate = server.state

            if server.find('%sstatus' % NS) is not None:
                ddaction = server.status.action
                for status in server.status:
                    if status.find('%sstep' % NS) is not None:
                        ddhasstep = True
                        ddstepname = server.status.step.name
                        ddstepnumber = server.status.step.number
                        ddtotalsteps = server.status.numberOfSteps


            if server.description == "":
                continue

            if descriptionstr[0] != "{":
                #LOG.warning("IS NOT JSON STR: %s" % descriptionstr[0])
                continue


            try:
                LOG.debug("Seeing if server has JSON: %s : %s" % (server.name,server.description))
                decoded_serverdesc = json.loads(descriptionstr)
                decoded_serveruuid = decoded_serverdesc['stackuuid']
                hasjson = True
                #LOG.debug("zserver.description: %s = %s" % (decoded_serverdesc,descriptionstr))
                #decoded_serverdesc = json.loads(server.description)
                #LOG.warning("GOING FOR UUID: %s" % decoded_serverdesc['stackuuid'])
            except TypeError as excep:
                #LOG.warn("%s is not Openstack Instance - No JSON - %s" % (server.name,str(excep) ))
                continue


            if instance['uuid'] == decoded_serveruuid:
                ccexists = True

            if hasjson and instance['uuid'] == decoded_serveruuid and server.isDeployed == True and server.isStarted == True and server.state == "NORMAL":
                LOG.info("Instance %s - isDeployed: %s  isStarted: %s  state:  %s" % (instance['display_name'],server.isDeployed,server.isStarted,server.state))
                state = power_state.RUNNING
                ddstate = server.state
                ccexists = True
                """
                instance.access_ip_v4 = privateIp
                instance.save()
                instance['power_state'] = power_state.RUNNING
                instance['task_state'] = None
                instance['vm_state'] = vm_states.ACTIVE
                instance.save(expected_task_state=(None,task_states.SPAWNING,task_states.POWERING_OFF,
                                                   task_states.STOPPING))
                #state = power_state.SHUTDOWN
                """

            if hasjson and instance['uuid'] == decoded_serveruuid and server.isDeployed == True and server.isStarted == False and server.state == "NORMAL":
                LOG.info("NORMAL & RUNNING - Instance %s - isDeployed: %s  isStarted: %s  state:  %s" % (instance['display_name'],server.isDeployed,server.isStarted,server.state))
                #print vars(instance)
                state = power_state.NOSTATE
                ddstate = server.state
                ccexists = True

            if hasjson and instance['uuid'] == decoded_serveruuid and server.isDeployed == False and server.isStarted == True and server.state != "NORMAL":
                LOG.info("NORMAL & RUNNING - Instance %s - isDeployed: %s  isStarted: %s  state:  %s" % (instance['display_name'],server.isDeployed,server.isStarted,server.state))
                #print vars(instance)
                state = power_state.RUNNING
                ddstate = server.state
                ccexists = True

            if hasjson and instance['uuid'] == decoded_serveruuid and server.isDeployed == False and server.isStarted == False and server.state != "NORMAL":
                LOG.info("NORMAL & RUNNING - Instance %s - isDeployed: %s  isStarted: %s  state:  %s" % (instance['display_name'],server.isDeployed,server.isStarted,server.state))
                #print vars(instance)
                state = power_state.RUNNING
                ddstate = server.state
                ccexists = True
            """
            if hasjson and instance['uuid'] == decoded_serveruuid and server.isDeployed == True and server.isStarted == False and server.state == "NORMAL":
                LOG.info("NORMAL & RUNNING - Instance %s - isDeployed: %s  isStarted: %s  state:  %s" % (instance['display_name'],server.isDeployed,server.isStarted,server.state))
                #print vars(instance)
                state = power_state.NOSTATE
                ccexists = True
            """
            LOG.warning("Existing THREE States are: %s : %s : %s" % (instance['power_state'],instance['task_state'],instance['vm_state']))


            if hasjson and server.name == instance['display_name'] and server.state == "PENDING_ADD":
                LOG.warning("PENDING_ADD - INSTANCE MATCH for: %s and %s" % (server['name'], instance['display_name']))
                #state = power_state.RUNNING
                ccexists = True
                ddstate = server.state
                ddhasstep = True
                ddstepname = server.status.step.name
                ddstepnumber = server.status.step.number
                ddtotalsteps = server.status.numberOfSteps
                #if server.status.find('step') is not None:
                #ddstepname = server.status.step.name
                #ddstepnumber = server.status.step.number
                #ddtotalsteps = server.status.numberOfSteps
                #ddstate = server.state
                #ddaction = server.status.action
                #ddtotalsteps = server.status.numberOfSteps
                #ddstepnumber = server.status.step.number
                #ddstepname = server.status.step.name

            if hasjson and server.name == instance['display_name'] and server.state == "PENDING_CHANGE":
                LOG.warning("PENDING_CHANGE - INSTANCE MATCH for: %s and %s" % (server['name'], instance['display_name']))
                #state = power_state.RUNNING
                ccexists = True
                #ddstate = server.state
                #ddaction = server.status.action
                #ddtotalsteps = 2
                #ddstepnumber = 1
                #ddstepname = "Change Server"

            if hasjson and server.name == instance['display_name'] and server.state == "PENDING_DELETE":
                LOG.warning("PENDING_DELETE - INSTANCE MATCH: %s and %s" % (server['name'], instance['display_name']))
                #state = power_state.RUNNING
                ccexists = True
                #ddstate = server.state
                #ddaction = server.status.action
                #ddtotalsteps = 2
                #ddstepnumber = 1
                #ddstepname = "Delete Server"

            if hasjson and server.name == instance['display_name'] and server.state == "PENDING_CLEAN":
                LOG.warning("PENDING_CLEAN - INSTANCE MATCH: %s and %s" % (server['name'], instance['display_name']))
                #state = power_state.RUNNING
                ccexists = True
                #ddstate = server.state
                #ddaction = server.status.action
                #ddtotalsteps = 2
                #ddstepnumber = 1
                #ddstepname = server.status.step.name

            if hasjson and server.name == instance['display_name'] and server.state == "FAILED_ADD":
                LOG.warning("FAILED_ADD - INSTANCE MATCH for: %s and %s" % (server['name'], instance['display_name']))
                state = power_state.RUNNING
                ccexists = True
                """
                instance['vm_state'] = vm_states.ERROR
                instance.save()
                instance['task_state'] = task_states.SPAWNING
                instance.save()
                """

            mem = server.memoryMb
            num_cpu = server.cpuCount
            max_mem = server.memoryMb

        if not ccexists:
            LOG.warning('*************************************Unmanaged Server, Not in Cloudcontrol - %s' % instance['display_name'])
            #state = power_state.NOSTATE

        return {'state': state,
                'max_mem': max_mem,
                'mem': mem,
                'num_cpu': num_cpu,
                'cpu_time': cpu_time,
                'ddstate': ddstate,
                'ddaction': ddaction,
                'ddtotalsteps': ddtotalsteps,
                'ddstepnumber': ddstepnumber,
                'ddstepname': ddstepname,
                'ddhasstep':ddhasstep,
                'ccexists': ccexists}


    def get_diagnostics(self, instance):
        """Return data about VM diagnostics."""
        msg = _("get_diagnostics not implemented for ddcloudapi")
        raise NotImplementedError(msg)

    def get_console_output(self, instance):
        LOG.warning('NOT YET IMPLEMENTED - get_console_output')
        return "DDCloud does not support console"
        """Return snapshot of console."""
        vm_ref = vm_util.get_vm_ref(self._session, instance)

        param_list = {"id": str(vm_ref.value)}
        base_url = "%s://%s/screen?%s" % (self._session._scheme,
                                         self._session._host_ip,
                                         urllib.urlencode(param_list))
        request = urllib2.Request(base_url)
        base64string = base64.encodestring(
                        '%s:%s' % (
                        self._session._host_username,
                        self._session._host_password)).replace('\n', '')
        request.add_header("Authorization", "Basic %s" % base64string)
        result = urllib2.urlopen(request)
        if result.code == 200:
            return result.read()
        else:
            return ""

    def get_vnc_console(self, instance):
        LOG.warning('NOT YET IMPLEMENTED - get_vnc_console')
        return
        """Return connection info for a vnc console."""
        vm_ref = vm_util.get_vm_ref(self._session, instance)

        return {'host': CONF.ddcloudapi_host_ip,
                'port': self._get_vnc_port(vm_ref),
                'internal_access_path': None}

    def get_vnc_console_vcenter(self, instance):
        LOG.warning('NOT YET IMPLEMENTED - get_vnc_console_vcenter')
        return
        """Return connection info for a vnc console using vCenter logic."""

        # vCenter does not run virtual machines and does not run
        # a VNC proxy. Instead, you need to tell OpenStack to talk
        # directly to the ESX host running the VM you are attempting
        # to connect to via VNC.

        vnc_console = self.get_vnc_console(instance)
        host_name = vm_util.get_host_name_for_vm(
                        self._session,
                        instance)
        vnc_console['host'] = host_name

        # NOTE: VM can move hosts in some situations. Debug for admins.
        LOG.debug(_("VM %(uuid)s is currently on host %(host_name)s"),
                {'uuid': instance['name'], 'host_name': host_name})

        return vnc_console

    @staticmethod
    def _get_vnc_port(vm_ref):
        """Return VNC port for an VM."""
        vm_id = int(vm_ref.value.replace('vm-', ''))
        port = CONF.vnc_port + vm_id % CONF.vnc_port_total

        return port

    @staticmethod
    def _get_machine_id_str(network_info):
        machine_id_str = ''
        for vif in network_info:
            # TODO(vish): add support for dns2
            # TODO(sateesh): add support for injection of ipv6 configuration
            network = vif['network']
            ip_v4 = netmask_v4 = gateway_v4 = broadcast_v4 = dns = None
            subnets_v4 = [s for s in network['subnets'] if s['version'] == 4]
            if len(subnets_v4[0]['ips']) > 0:
                ip_v4 = subnets_v4[0]['ips'][0]
            if len(subnets_v4[0]['dns']) > 0:
                dns = subnets_v4[0]['dns'][0]['address']

            netmask_v4 = str(subnets_v4[0].as_netaddr().netmask)
            gateway_v4 = subnets_v4[0]['gateway']['address']
            broadcast_v4 = str(subnets_v4[0].as_netaddr().broadcast)

            interface_str = ";".join([vif['address'],
                                      ip_v4 and ip_v4['address'] or '',
                                      netmask_v4 or '',
                                      gateway_v4 or '',
                                      broadcast_v4 or '',
                                      dns or ''])
            machine_id_str = machine_id_str + interface_str + '#'
        return machine_id_str

    def _set_machine_id(self, client_factory, instance, network_info):
        LOG.warning('NOT YET IMPLEMENTED - _set_machine_id')
        return
        """
        Set the machine id of the VM for guest tools to pick up and reconfigure
        the network interfaces.
        """
        vm_ref = vm_util.get_vm_ref(self._session, instance)

        machine_id_change_spec = vm_util.get_machine_id_change_spec(
                                 client_factory,
                                 self._get_machine_id_str(network_info))

        LOG.debug(_("Reconfiguring VM instance to set the machine id"),
                  instance=instance)
        reconfig_task = self._session._call_method(self._session._get_vim(),
                           "ReconfigVM_Task", vm_ref,
                           spec=machine_id_change_spec)
        self._session._wait_for_task(instance['uuid'], reconfig_task)
        LOG.debug(_("Reconfigured VM instance to set the machine id"),
                  instance=instance)

    def _set_vnc_config(self, client_factory, instance, port, password):
        LOG.warning('NOT YET IMPLEMENTED - _set_vnc_config')
        return
        """
        Set the vnc configuration of the VM.
        """
        vm_ref = vm_util.get_vm_ref(self._session, instance)

        vnc_config_spec = vm_util.get_vnc_config_spec(
                                      client_factory, port, password)

        LOG.debug(_("Reconfiguring VM instance to enable vnc on "
                  "port - %(port)s") % {'port': port},
                  instance=instance)
        reconfig_task = self._session._call_method(self._session._get_vim(),
                           "ReconfigVM_Task", vm_ref,
                           spec=vnc_config_spec)
        self._session._wait_for_task(instance['uuid'], reconfig_task)
        LOG.debug(_("Reconfigured VM instance to enable vnc on "
                  "port - %(port)s") % {'port': port},
                  instance=instance)

    def _get_datacenter_ref_and_name(self):
        LOG.warning('NOT YET IMPLEMENTED - _get_datacenter_ref_and_name')
        return None
        """Get the datacenter name and the reference."""
        dc_obj = self._session._call_method(vim_util, "get_objects",
                "Datacenter", ["name"])
        return dc_obj[0].obj, dc_obj[0].propSet[0].val

    def _get_host_ref_from_name(self, host_name):
        LOG.warning('NOT YET IMPLEMENTED - _get_host_ref_from_name')
        return None
        """Get reference to the host with the name specified."""
        host_objs = self._session._call_method(vim_util, "get_objects",
                    "HostSystem", ["name"])
        for host in host_objs:
            if host.propSet[0].val == host_name:
                return host.obj
        return None

    def _get_vmfolder_ref(self):
        LOG.warning('NOT YET IMPLEMENTED - _get_vmfolder_ref')
        return
        """Get the Vm folder ref from the datacenter."""
        """
        dc_objs = self._session._call_method(vim_util, "get_objects",
                                             "Datacenter", ["vmFolder"])
        # There is only one default datacenter in a standalone ESX host
        vm_folder_ref = dc_objs[0].propSet[0].val
        """
        vm_folder_ref = "BLAH"
        return vm_folder_ref

    def _get_res_pool_ref(self):
        LOG.warning('NOT YET IMPLEMENTED - _get_res_pool_ref')
        return
        # Get the resource pool. Taking the first resource pool coming our
        # way. Assuming that is the default resource pool.
        if self._cluster is None:
            res_pool_ref = self._session._call_method(vim_util, "get_objects",
                                                      "ResourcePool")[0].obj
        else:
            res_pool_ref = self._session._call_method(vim_util,
                                                      "get_dynamic_property",
                                                      self._cluster,
                                                      "ClusterComputeResource",
                                                      "resourcePool")
        return res_pool_ref

    def _path_exists(self, ds_browser, ds_path):
        LOG.warning('NOT YET IMPLEMENTED - _path_exists')
        return True
        """Check if the path exists on the datastore."""
        search_task = self._session._call_method(self._session._get_vim(),
                                   "SearchDatastore_Task",
                                   ds_browser,
                                   datastorePath=ds_path)
        # Wait till the state changes from queued or running.
        # If an error state is returned, it means that the path doesn't exist.
        while True:
            task_info = self._session._call_method(vim_util,
                                       "get_dynamic_property",
                                       search_task, "Task", "info")
            if task_info.state in ['queued', 'running']:
                time.sleep(2)
                continue
            break
        if task_info.state == "error":
            return False
        return True

    def _path_file_exists(self, ds_browser, ds_path, file_name):
        """Check if the path and file exists on the datastore."""
        client_factory = self._session._get_vim().client.factory
        search_spec = vm_util.search_datastore_spec(client_factory, file_name)
        search_task = self._session._call_method(self._session._get_vim(),
                                   "SearchDatastore_Task",
                                   ds_browser,
                                   datastorePath=ds_path,
                                   searchSpec=search_spec)
        # Wait till the state changes from queued or running.
        # If an error state is returned, it means that the path doesn't exist.
        while True:
            task_info = self._session._call_method(vim_util,
                                       "get_dynamic_property",
                                       search_task, "Task", "info")
            if task_info.state in ['queued', 'running']:
                time.sleep(2)
                continue
            break
        if task_info.state == "error":
            return False, False

        file_exists = (getattr(task_info.result, 'file', False) and
                       task_info.result.file[0].path == file_name)
        return True, file_exists

    def _mkdir(self, ds_path):
        LOG.warning('NOT YET IMPLEMENTED - _mkdir')
        return
        """
        Creates a directory at the path specified. If it is just "NAME",
        then a directory with this name is created at the topmost level of the
        DataStore.
        """
        LOG.debug(_("Creating directory with path %s") % ds_path)
        dc_ref = self._get_datacenter_ref_and_name()[0]
        self._session._call_method(self._session._get_vim(), "MakeDirectory",
                    self._session._get_vim().get_service_content().fileManager,
                    name=ds_path, datacenter=dc_ref,
                    createParentDirectories=False)
        LOG.debug(_("Created directory with path %s") % ds_path)

    def _check_if_folder_file_exists(self, ds_ref, ds_name,
                                     folder_name, file_name):
        LOG.warning('NOT YET IMPLEMENTED - _check_if_folder_file_exists')
        return
        ds_browser = vim_util.get_dynamic_property(
                                self._session._get_vim(),
                                ds_ref,
                                "Datastore",
                                "browser")
        # Check if the folder exists or not. If not, create one
        # Check if the file exists or not.
        folder_path = vm_util.build_datastore_path(ds_name, folder_name)
        folder_exists, file_exists = self._path_file_exists(ds_browser,
                                                            folder_path,
                                                            file_name)
        if not folder_exists:
            self._mkdir(vm_util.build_datastore_path(ds_name, folder_name))

        return file_exists

    def inject_network_info(self, instance, network_info):
        LOG.warning('NOT YET IMPLEMENTED - inject_network_info')
        return
        """inject network info for specified instance."""
        # Set the machine.id parameter of the instance to inject
        # the NIC configuration inside the VM
        client_factory = self._session._get_vim().client.factory
        self._set_machine_id(client_factory, instance, network_info)

    def plug_vifs(self, instance, network_info):
        """Plug VIFs into networks."""
        pass

    def unplug_vifs(self, instance, network_info):
        """Unplug VIFs from networks."""
        pass

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
        self.ddstatewatch(instance_uuid, task_ref, 5)

        done.send("success")
        return
