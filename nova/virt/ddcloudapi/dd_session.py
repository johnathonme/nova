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
#CONF.import_opt('ddcloudapi_host_ip', 'ddcloudapi_host_ip', 'ddcloudapi_host_password')
#CONF.import_opt('ddcloudapi_url')
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

        #LOG.debug(dir(self._session.__attrs__))

    @property
    def _session(self):
        return self._session

    """
    def @_session.setter
        def _session(self, value):
        self._session = value
    """


    def parseXML(xmlFile):
        with open(xmlFile) as f:
            xml = f.read()

            root = objectify.fromstring(xml)

    def test(self, host_username, host_password, host_url, host_ip):

        #s = requests.Session()
        response = self._session.get(host_url, auth=(host_username, host_password))


        LOG.info("response: %s" % response.status_code)
        response.content
        #print("content:  %s" % content)


        return response.status_code

    def get_instances(self):
        s = requests.Session()
        response = s.get('https://api-ap.dimensiondata.com/oec/0.9/e2c43389-90de-4498-b7d0-056e8db0b381/serverWithState?', auth=(self.host_username, self.host_password))
        LOG.info("response: %s" % response.status_code)

