# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2011 OpenStack Foundation
# All Rights Reserved.
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

"""Keypair management extension."""

import webob
import webob.exc

from nova.api.openstack.compute import servers
from nova.api.openstack import extensions
from nova.api.openstack import wsgi
from nova.api.openstack import xmlutil
from nova.compute import api as compute_api
from nova import exception


authorize = extensions.extension_authorizer('compute', 'keypairs')
soft_authorize = extensions.soft_extension_authorizer('compute', 'keypairs')


class KeypairTemplate(xmlutil.TemplateBuilder):
    def construct(self):
        return xmlutil.MasterTemplate(xmlutil.make_flat_dict('keypair'), 1)


class KeypairsTemplate(xmlutil.TemplateBuilder):
    def construct(self):
        root = xmlutil.TemplateElement('keypairs')
        elem = xmlutil.make_flat_dict('keypair', selector='keypairs',
                                      subselector='keypair')
        root.append(elem)

        return xmlutil.MasterTemplate(root, 1)


class KeypairController(object):

    """Keypair API controller for the OpenStack API."""
    def __init__(self):
        self.api = compute_api.KeypairAPI()

    @wsgi.serializers(xml=KeypairTemplate)
    def create(self, req, body):
        """
        Create or import keypair.

        Sending name will generate a key and return private_key
        and fingerprint.

        You can send a public_key to add an existing ssh key

        params: keypair object with:
            name (required) - string
            public_key (optional) - string
        """

        context = req.environ['nova.context']
        authorize(context, action='create')

        try:
            params = body['keypair']
            name = params['name']
        except KeyError:
            msg = _("Invalid request body")
            raise webob.exc.HTTPBadRequest(explanation=msg)

        try:
            if 'public_key' in params:
                keypair = self.api.import_key_pair(context,
                                              context.user_id, name,
                                              params['public_key'])
            else:
                keypair = self.api.create_key_pair(context, context.user_id,
                                                   name)

            return {'keypair': keypair}

        except exception.KeypairLimitExceeded:
            msg = _("Quota exceeded, too many key pairs.")
            raise webob.exc.HTTPRequestEntityTooLarge(
                        explanation=msg,
                        headers={'Retry-After': 0})
        except exception.InvalidKeypair as exc:
            raise webob.exc.HTTPBadRequest(explanation=exc.format_message())
        except exception.KeyPairExists as exc:
            raise webob.exc.HTTPConflict(explanation=exc.format_message())

    def delete(self, req, id):
        """
        Delete a keypair with a given name
        """
        context = req.environ['nova.context']
        authorize(context, action='delete')
        try:
            self.api.delete_key_pair(context, context.user_id, id)
        except exception.KeypairNotFound:
            raise webob.exc.HTTPNotFound()
        return webob.Response(status_int=202)

    @wsgi.serializers(xml=KeypairTemplate)
    def show(self, req, id):
        """Return data for the given key name."""
        context = req.environ['nova.context']
        authorize(context, action='show')

        try:
            keypair = self.api.get_key_pair(context, context.user_id, id)
        except exception.KeypairNotFound:
            raise webob.exc.HTTPNotFound()
        return {'keypair': keypair}

    @wsgi.serializers(xml=KeypairsTemplate)
    def index(self, req):
        """
        List of keypairs for a user
        """
        context = req.environ['nova.context']
        authorize(context, action='index')
        key_pairs = self.api.get_key_pairs(context, context.user_id)
        rval = []
        for key_pair in key_pairs:
            rval.append({'keypair': {
                'name': key_pair['name'],
                'public_key': key_pair['public_key'],
                'fingerprint': key_pair['fingerprint'],
            }})

        return {'keypairs': rval}


class ServerKeyNameTemplate(xmlutil.TemplateBuilder):
    def construct(self):
        root = xmlutil.TemplateElement('server')
        root.set('key_name', 'key_name')
        return xmlutil.SlaveTemplate(root, 1)


class ServersKeyNameTemplate(xmlutil.TemplateBuilder):
    def construct(self):
        root = xmlutil.TemplateElement('servers')
        elem = xmlutil.SubTemplateElement(root, 'server', selector='servers')
        elem.set('key_name', 'key_name')
        return xmlutil.SlaveTemplate(root, 1)


class Controller(servers.Controller):

    def _add_key_name(self, req, servers):
        for server in servers:
            db_server = req.get_db_instance(server['id'])
            # server['id'] is guaranteed to be in the cache due to
            # the core API adding it in its 'show'/'detail' methods.
            server['key_name'] = db_server['key_name']

    def _show(self, req, resp_obj):
        if 'server' in resp_obj.obj:
            resp_obj.attach(xml=ServerKeyNameTemplate())
            server = resp_obj.obj['server']
            self._add_key_name(req, [server])

    @wsgi.extends
    def show(self, req, resp_obj, id):
        context = req.environ['nova.context']
        if soft_authorize(context):
            self._show(req, resp_obj)

    @wsgi.extends
    def detail(self, req, resp_obj):
        context = req.environ['nova.context']
        if 'servers' in resp_obj.obj and soft_authorize(context):
            resp_obj.attach(xml=ServersKeyNameTemplate())
            servers = resp_obj.obj['servers']
            self._add_key_name(req, servers)


class Keypairs(extensions.ExtensionDescriptor):
    """Keypair Support."""

    name = "Keypairs"
    alias = "os-keypairs"
    namespace = "http://docs.openstack.org/compute/ext/keypairs/api/v1.1"
    updated = "2011-08-08T00:00:00+00:00"

    def get_resources(self):
        resources = []

        res = extensions.ResourceExtension(
                'os-keypairs',
                KeypairController())
        resources.append(res)
        return resources

    def get_controller_extensions(self):
        controller = Controller(self.ext_mgr)
        extension = extensions.ControllerExtension(self, 'servers', controller)
        return [extension]
