from lxml import etree, objectify
from nova.openstack.common import log as logging
from oslo.config import cfg

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

        self.test(host_username, host_password, host_url)

        #print ("class session  - init")
        LOG.info("init %s://%s" % (protocol, host_ip))



    def parseXML(xmlFile):
        with open(xmlFile) as f:
            xml = f.read()

            root = objectify.fromstring(xml)

    def test(self, host_username, host_password, host_url):
        import httplib2
        http = httplib2.Http('.cache')
        http.add_credentials(host_username, host_password)
        response, content = http.request(host_url)
        LOG.info("response: %s" % response.status)
        #print("content:  %s" % content)