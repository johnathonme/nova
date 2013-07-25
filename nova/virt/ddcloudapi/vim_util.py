# vim: tabstop=4 shiftwidth=4 softtabstop=4

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
The VMware API utility module.
"""


def build_selection_spec(client_factory, name):
    """Builds the selection spec."""
    sel_spec = client_factory.create('ns0:SelectionSpec')
    sel_spec.name = name
    return sel_spec


def build_traversal_spec(client_factory, name, spec_type, path, skip,
                         select_set):
    """Builds the traversal spec object."""
    traversal_spec = client_factory.create('ns0:TraversalSpec')
    traversal_spec.name = name
    traversal_spec.type = spec_type
    traversal_spec.path = path
    traversal_spec.skip = skip
    traversal_spec.selectSet = select_set
    return traversal_spec


def build_recursive_traversal_spec(client_factory):
    """
    Builds the Recursive Traversal Spec to traverse the object managed
    object hierarchy.
    """
    visit_folders_select_spec = build_selection_spec(client_factory,
                                    "visitFolders")
    # For getting to hostFolder from datacenter
    dc_to_hf = build_traversal_spec(client_factory, "dc_to_hf", "Datacenter",
                                    "hostFolder", False,
                                    [visit_folders_select_spec])
    # For getting to vmFolder from datacenter
    dc_to_vmf = build_traversal_spec(client_factory, "dc_to_vmf", "Datacenter",
                                     "vmFolder", False,
                                     [visit_folders_select_spec])
    # For getting Host System to virtual machine
    h_to_vm = build_traversal_spec(client_factory, "h_to_vm", "HostSystem",
                                   "vm", False,
                                   [visit_folders_select_spec])

    # For getting to Host System from Compute Resource
    cr_to_h = build_traversal_spec(client_factory, "cr_to_h",
                                   "ComputeResource", "host", False, [])

    # For getting to datastore from Compute Resource
    cr_to_ds = build_traversal_spec(client_factory, "cr_to_ds",
                                    "ComputeResource", "datastore", False, [])

    rp_to_rp_select_spec = build_selection_spec(client_factory, "rp_to_rp")
    rp_to_vm_select_spec = build_selection_spec(client_factory, "rp_to_vm")
    # For getting to resource pool from Compute Resource
    cr_to_rp = build_traversal_spec(client_factory, "cr_to_rp",
                                "ComputeResource", "resourcePool", False,
                                [rp_to_rp_select_spec, rp_to_vm_select_spec])

    # For getting to child res pool from the parent res pool
    rp_to_rp = build_traversal_spec(client_factory, "rp_to_rp", "ResourcePool",
                                "resourcePool", False,
                                [rp_to_rp_select_spec, rp_to_vm_select_spec])

    # For getting to Virtual Machine from the Resource Pool
    rp_to_vm = build_traversal_spec(client_factory, "rp_to_vm", "ResourcePool",
                                "vm", False,
                                [rp_to_rp_select_spec, rp_to_vm_select_spec])

    # Get the assorted traversal spec which takes care of the objects to
    # be searched for from the root folder
    traversal_spec = build_traversal_spec(client_factory, "visitFolders",
                                  "Folder", "childEntity", False,
                                  [visit_folders_select_spec, dc_to_hf,
                                   dc_to_vmf, cr_to_ds, cr_to_h, cr_to_rp,
                                   rp_to_rp, h_to_vm, rp_to_vm])
    return traversal_spec


def build_property_spec(client_factory, type="VirtualMachine",
                        properties_to_collect=None,
                        all_properties=False):
    """Builds the Property Spec."""
    if not properties_to_collect:
        properties_to_collect = ["name"]

    property_spec = client_factory.create('ns0:PropertySpec')
    property_spec.all = all_properties
    property_spec.pathSet = properties_to_collect
    property_spec.type = type
    return property_spec


def build_object_spec(client_factory, root_folder, traversal_specs):
    """Builds the object Spec."""
    object_spec = client_factory.create('ns0:ObjectSpec')
    object_spec.obj = root_folder
    object_spec.skip = False
    object_spec.selectSet = traversal_specs
    return object_spec


def build_property_filter_spec(client_factory, property_specs, object_specs):
    """Builds the Property Filter Spec."""
    property_filter_spec = client_factory.create('ns0:PropertyFilterSpec')
    property_filter_spec.propSet = property_specs
    property_filter_spec.objectSet = object_specs
    return property_filter_spec


def get_object_properties(vim, collector, mobj, type, properties):
    """Gets the properties of the Managed object specified."""
    print('vim, collector, mobj, type, properties:  %s  %s  %s  %s  %s'  % (vim, collector, mobj, type, properties))
    return None
    client_factory = vim.client.factory
    if mobj is None:
        return None
    usecoll = collector
    if usecoll is None:
        usecoll = vim.get_service_content().propertyCollector
    property_filter_spec = client_factory.create('ns0:PropertyFilterSpec')
    property_spec = client_factory.create('ns0:PropertySpec')
    property_spec.all = (properties is None or len(properties) == 0)
    property_spec.pathSet = properties
    property_spec.type = type
    object_spec = client_factory.create('ns0:ObjectSpec')
    object_spec.obj = mobj
    object_spec.skip = False
    property_filter_spec.propSet = [property_spec]
    property_filter_spec.objectSet = [object_spec]
    return vim.RetrieveProperties(usecoll, specSet=[property_filter_spec])


def get_dynamic_property(vim, mobj, type, property_name):
    """Gets a particular property of the Managed Object."""
    obj_content = get_object_properties(vim, None, mobj, type, [property_name])
    property_value = None
    if obj_content:
        dynamic_property = obj_content[0].propSet
        if dynamic_property:
            property_value = dynamic_property[0].val
    return property_value


def get_objects(vim, type, properties_to_collect=None, all=False):
    """Gets the list of objects of the type specified."""

    print("VIM and TYPE and PROS  and ALL:   %s    %s   %s   %s"  %(vim, type, properties_to_collect, all))
    # None    VirtualMachine   ['name']   False

    if type == ["VirtualMachine"]:
        import requests
        host_ip = CONF.ddcloudapi_host_ip
        host_username = CONF.ddcloudapi_host_username
        host_password = CONF.ddcloudapi_host_password
        host_url = CONF.ddcloudapi_url
        s = requests.Session()
        response = s.get('https://api-ap.dimensiondata.com/oec/0.9/e2c43389-90de-4498-b7d0-056e8db0b381/serverWithState?', auth=('dev1-apiuser', 'cloudcloudcloud'))
        LOG.info("response: %s" % response.status_code)

        print response.content

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
        #return lst_vm_names

    somedumbstringobj = "blah"
    return somedumbstringobj


    if not properties_to_collect:
        properties_to_collect = ["name"]

    client_factory = vim.client.factory
    object_spec = build_object_spec(client_factory,
                        vim.get_service_content().rootFolder,
                        [build_recursive_traversal_spec(client_factory)])
    property_spec = build_property_spec(client_factory, type=type,
                                properties_to_collect=properties_to_collect,
                                all_properties=all)
    property_filter_spec = build_property_filter_spec(client_factory,
                                [property_spec],
                                [object_spec])
    return vim.RetrieveProperties(vim.get_service_content().propertyCollector,
                                specSet=[property_filter_spec])


def get_prop_spec(client_factory, spec_type, properties):
    """Builds the Property Spec Object."""
    prop_spec = client_factory.create('ns0:PropertySpec')
    prop_spec.type = spec_type
    prop_spec.pathSet = properties
    return prop_spec


def get_obj_spec(client_factory, obj, select_set=None):
    """Builds the Object Spec object."""
    obj_spec = client_factory.create('ns0:ObjectSpec')
    obj_spec.obj = obj
    obj_spec.skip = False
    if select_set is not None:
        obj_spec.selectSet = select_set
    return obj_spec


def get_prop_filter_spec(client_factory, obj_spec, prop_spec):
    """Builds the Property Filter Spec Object."""
    prop_filter_spec = client_factory.create('ns0:PropertyFilterSpec')
    prop_filter_spec.propSet = prop_spec
    prop_filter_spec.objectSet = obj_spec
    return prop_filter_spec


def get_properties_for_a_collection_of_objects(vim, type,
                                               obj_list, properties):
    """
    Gets the list of properties for the collection of
    objects of the type specified.
    """
    client_factory = vim.client.factory
    if len(obj_list) == 0:
        return []
    prop_spec = get_prop_spec(client_factory, type, properties)
    lst_obj_specs = []
    for obj in obj_list:
        lst_obj_specs.append(get_obj_spec(client_factory, obj))
    prop_filter_spec = get_prop_filter_spec(client_factory,
                                            lst_obj_specs, [prop_spec])
    return vim.RetrieveProperties(vim.get_service_content().propertyCollector,
                                   specSet=[prop_filter_spec])

