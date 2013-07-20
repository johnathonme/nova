from lxml import etree, objectify
from nova.openstack.common import log as logging
from oslo.config import cfg
import requests

CONF = cfg.CONF
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
    ]

CONF.register_opts(ddcloudapi_opts)
LOG = logging.getLogger(__name__)
#logging.debug('ddcloudapi: A debug message!')




#----------------------------------------------------------------------

class DDsession:
    """ the session object """

    def __init__(self,
                 protocol="https",
                 host_ip="localhost"):
        host_ip = CONF.ddcloudapi_host_ip
        host_username = CONF.ddcloudapi_host_username
        host_password = CONF.ddcloudapi_host_password
        host_url = CONF.ddcloudapi_url

        if (self.test(host_username, host_password, host_url, host_ip) == 200):
            LOG.debug("DDLCOUD CONNECTION TEST SUCCESS")


        #print ("class session  - init")
        LOG.info("init %s://%s" % (protocol, host_ip))

        self._session = requests.Session()



    def parseXML(xmlFile):
        with open(xmlFile) as f:
            xml = f.read()

            root = objectify.fromstring(xml)

    def test(self, host_username, host_password, host_url, host_ip):

        s = requests.Session()
        response = s.get(host_url, auth=(host_username, host_password))


        LOG.info("response: %s" % response.status_code)
        response.content
        #print("content:  %s" % content)


        return response.status_code