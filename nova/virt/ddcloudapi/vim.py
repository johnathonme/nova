# vim: tabstop=4 shiftwidth=4 softtabstop=4

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
Classes for making VMware VI SOAP calls.
"""

import httplib

try:
    import suds
except ImportError:
    suds = None

from oslo.config import cfg

from nova.virt.ddcloudapi import error_util

RESP_NOT_XML_ERROR = 'Response is "text/html", not "text/xml"'
CONN_ABORT_ERROR = 'Software caused connection abort'
ADDRESS_IN_USE_ERROR = 'Address already in use'

vmwareapi_wsdl_loc_opt = cfg.StrOpt('vmwareapi_wsdl_loc',
        default=None,
        help='Optional VIM Service WSDL Location '
             'e.g http://<server>/vimService.wsdl. '
             'Optional over-ride to default location for bug work-arounds')

CONF = cfg.CONF
CONF.register_opt(vmwareapi_wsdl_loc_opt)

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
    cfg.StrOpt('ddcloudapi_url',
               default=None,
               help='Name of a Cloudcontrol API url. '
                    'Used only if compute_driver is '
                    'ddcloudapi.CloudcontrolVCDriver.'),
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

#CONF = cfg.CONF
CONF.register_opts(ddcloudapi_opts)


if suds:

    class VIMMessagePlugin(suds.plugin.MessagePlugin):

        def addAttributeForValue(self, node):
            # suds does not handle AnyType properly.
            # VI SDK requires type attribute to be set when AnyType is used
            if node.name == 'value':
                node.set('xsi:type', 'xsd:string')

        def marshalled(self, context):
            """suds will send the specified soap envelope.
            Provides the plugin with the opportunity to prune empty
            nodes and fixup nodes before sending it to the server.
            """
            # suds builds the entire request object based on the wsdl schema.
            # VI SDK throws server errors if optional SOAP nodes are sent
            # without values, e.g. <test/> as opposed to <test>test</test>
            context.envelope.prune()
            context.envelope.walk(self.addAttributeForValue)


class Vim:
    """The VIM Object."""

    def __init__(self,
                 protocol="https",
                 host="localhost"):

        """
        Creates the necessary Communication interfaces and gets the
        ServiceContent for initiating SOAP transactions.

        :param protocol:
        :param host:
        protocol: http or https
        host    : ESX IPAddress[:port] or ESX Hostname[:port]
        """
        if not suds:
            raise Exception(_("Unable to import suds."))

        self._protocol = protocol
        self._host_name = host
        #self.wsdl_url = Vim.get_wsdl_url(protocol, host)
        #self.servers_url = Vim.get_servers_url(protocol, host)
        #self.url = Vim.get_soap_url(protocol, host)
        #self.url = Vim.get_servers_url(protocol, host)

        import urllib2



    	host_ip = CONF.ddcloudapi_host_ip
        host_username = CONF.ddcloudapi_host_username
        host_password = CONF.ddcloudapi_host_password
    	host_url = CONF.ddcloudapi_url

        """
        password_manager = urllib2.HTTPPasswordMgrWithDefaultRealm()
        password_manager.add_password(
            None, 'https://api-ap.dimensiondata.com/', delicious_user, delicious_pass
        )
        auth_handler = urllib2.HTTPBasicAuthHandler(password_manager)
        opener = urllib2.build_opener(auth_handler)
        urllib2.install_opener(opener)
        respxml = urllib2.urlopen('https://api-ap.dimensiondata.com/oec/0.9/myaccount').read()

        print ("respxml:  %s" % respxml)

        respxml = urllib2.urlopen('https://api-ap.dimensiondata.com/oec/0.9/e2c43389-90de-4498-b7d0-056e8db0b381/serverWithState?').read()

        print ("respxml2:  %s" % respxml)

        #self.client = suds.client.Client(self.servers_url, location=self.url,
        #                                 plugins=[VIMMessagePlugin()])
        #self._service_content = self.RetrieveServiceContent(
        #                                "ServiceInstance")
        self.client = opener
        """

        """
        import httplib2
        http = httplib2.Http('.cache')
        response, content = http.request('http://developer.yahoo.com/')
        print("YAHOOOOOOO")
        """

        #import dd_session as ddsession
        #mysession = ddsession.DDsession()

        #print mysession



        import sys
        import time
        #time.sleep( 15 )
        sys.exit()

        """
        ET.register_namespace('', 'http://oec.api.opsource.net/schemas/server')
        ET.register_namespace('', 'http://oec.api.opsource.net/schemas/directory')
        ET.register_namespace('', 'http://oec.api.opsource.net/schemas/organization')
        ET.register_namespace('', 'http://oec.api.opsource.net/schemas/network')
        ET.register_namespace('', 'http://oec.api.opsource.net/schemas/vip')
        ET.register_namespace('', 'http://oec.api.opsource.net/schemas/whitelabel')
        ET.register_namespace('', 'http://oec.api.opsource.net/schemas/datacenter')
        ET.register_namespace('', 'http://oec.api.opsource.net/schemas/general')
        ET.register_namespace('', 'http://oec.api.opsource.net/schemas/admin')
        ET.register_namespace('', 'http://oec.api.opsource.net/schemas/multigeo')
        ET.register_namespace('', 'http://oec.api.opsource.net/schemas/reset')
        ET.register_namespace('', 'http://oec.api.opsource.net/schemas/support')
        ET.register_namespace('', 'http://oec.api.opsource.net/schemas/storage')
        ET.register_namespace('', 'http://oec.api.opsource.net/schemas/manualimport')
        import re
        content = re.sub(' xmlns="[^"]+"', '', content, count=1)
        #ET.register_namespace('', 'http://oec.api.opsource.net/schemas/server')
        tree = ET.fromstring(content)
        root = tree
        print("%s   :   %s"   %(root.tag, root.attrib))

        for elem in tree.iter():
            print elem.tag, elem.attrib, elem.text

        e = tree.findall("serverWithState")
        for server in e:
            print server.text

        namespace = {"http://oec.api.opsource.net/schemas/server"}
        #e = doc.findall('./ServersWithState/serverWithState/name')
        #for i in e:
            #print i.text

        #for server in doc:
            #print("DOC:  %s" % doc)
            #print("%s: %s " % (server.attrib['id'], server.attrib['location'] ))
            #print server.tag, server.attrib
            #print("%s " % (server.Element.findall('name')) )
            #print ("%s" % server.findall("."))
            #print ("BLAH")

        #for server2 in doc.findall("./*"):
            #print ("%s" % server2.tag )

        #serverWithState

        import sys
        import time
        #time.sleep( 15 )
        sys.exit()
        self.client = doc
        """


    @staticmethod
    def get_servers_url(protocol, hostname):
        servers_url = None
        if servers_url is None:
            #servers_url = '%s://%s/sdk/vimService.wsdl' % (protocol, host_name )
            servers_url = 'https://api-ap.dimensiondata.com/oec/0.9/e2c43389-90de-4498-b7d0-056e8db0b381/serverWithState?'
        return servers_url

    @staticmethod
    def get_wsdl_url(protocol, host_name):
        print ("WSDL URL")
        """
        allows override of the wsdl location, making this static
        means we can test the logic outside of the constructor
        without forcing the test environment to have multiple valid
        wsdl locations to test against.

        :param protocol: https or http
        :param host_name: localhost or other server name
        :return: string to WSDL location for vSphere WS Management API
        """
        # optional WSDL location over-ride for work-arounds
        wsdl_url = CONF.vmwareapi_wsdl_loc
        if wsdl_url is None:
            # calculate default WSDL location if no override supplied
            wsdl_url = '%s://%s/sdk/vimService.wsdl' % (protocol, host_name)

            print ("wsdl_Url: %s" % wsdl_url)
        return wsdl_url

    @staticmethod
    def get_soap_url(protocol, host_name):
        print ("SOAP URL")
        """
        Calculates the location of the SOAP services
        for a particular server. Created as a static
        method for testing.

        :param protocol: https or http
        :param host_name: localhost or other vSphere server name
        :return: the url to the active vSphere WS Management API
        """
        print ("SOAP URL: %s://%s/sdk"  % (protocol, host_name))
        return '%s://%s/sdk' % (protocol, host_name)

    def get_service_content(self):
        """Gets the service content object."""
        return self._service_content

    def __getattr__(self, attr_name):
        """Makes the API calls and gets the result."""
        def vim_request_handler(managed_object, **kwargs):
            """
            Builds the SOAP message and parses the response for fault
            checking and other errors.

            managed_object    : Managed Object Reference or Managed
                                Object Name
            **kwargs          : Keyword arguments of the call
            """
            # Dynamic handler for VI SDK Calls
            try:
                request_mo = self._request_managed_object_builder(
                             managed_object)
                request = getattr(self.client.service, attr_name)
                response = request(request_mo, **kwargs)
                # To check for the faults that are part of the message body
                # and not returned as Fault object response from the ESX
                # SOAP server
                if hasattr(error_util.FaultCheckers,
                                attr_name.lower() + "_fault_checker"):
                    fault_checker = getattr(error_util.FaultCheckers,
                                attr_name.lower() + "_fault_checker")
                    fault_checker(response)
                return response
            # Catch the VimFaultException that is raised by the fault
            # check of the SOAP response
            except error_util.VimFaultException as excep:
                raise
            except suds.WebFault as excep:
                doc = excep.document
                detail = doc.childAtPath("/Envelope/Body/Fault/detail")
                fault_list = []
                for child in detail.getChildren():
                    fault_list.append(child.get("type"))
                raise error_util.VimFaultException(fault_list, excep)
            except AttributeError as excep:
                raise error_util.VimAttributeError(_("No such SOAP method "
                     "'%s' provided by VI SDK") % (attr_name), excep)
            except (httplib.CannotSendRequest,
                    httplib.ResponseNotReady,
                    httplib.CannotSendHeader) as excep:
                raise error_util.SessionOverLoadException(_("httplib "
                                "error in %s: ") % (attr_name), excep)
            except Exception as excep:
                # Socket errors which need special handling for they
                # might be caused by ESX API call overload
                if (str(excep).find(ADDRESS_IN_USE_ERROR) != -1 or
                        str(excep).find(CONN_ABORT_ERROR)) != -1:
                    raise error_util.SessionOverLoadException(_("Socket "
                                "error in %s: ") % (attr_name), excep)
                # Type error that needs special handling for it might be
                # caused by ESX host API call overload
                elif str(excep).find(RESP_NOT_XML_ERROR) != -1:
                    raise error_util.SessionOverLoadException(_("Type "
                                "error in  %s: ") % (attr_name), excep)
                else:
                    raise error_util.VimException(
                       _("Exception in %s ") % (attr_name), excep)
        return vim_request_handler

    def _request_managed_object_builder(self, managed_object):
        """Builds the request managed object."""
        # Request Managed Object Builder
        if isinstance(managed_object, str):
            mo = suds.sudsobject.Property(managed_object)
            mo._type = managed_object
        else:
            mo = managed_object
        return mo

    def __repr__(self):
        return "VIM Object"

    def __str__(self):
        return "VIM Object"
