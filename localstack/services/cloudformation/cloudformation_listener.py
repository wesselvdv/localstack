import re
import uuid
import logging
from requests.models import Response, Request
from six.moves.urllib import parse as urlparse

from localstack.constants import TEST_AWS_ACCOUNT_ID, MOTO_ACCOUNT_ID
from localstack.utils.aws import aws_stack
from localstack.utils.common import to_str, obj_to_xml
from localstack.utils.cloudformation import template_deployer
from localstack.services.generic_proxy import ProxyListener

XMLNS_CLOUDFORMATION = 'http://cloudformation.amazonaws.com/doc/2010-05-15/'
LOG = logging.getLogger(__name__)


def error_response(message, code=400, error_type='ValidationError'):
    response = Response()
    response.status_code = code
    response.headers['x-amzn-errortype'] = error_type
    response._content = """<ErrorResponse xmlns="%s">
          <Error>
            <Type>Sender</Type>
            <Code>%s</Code>
            <Message>%s</Message>
          </Error>
          <RequestId>%s</RequestId>
        </ErrorResponse>""" % (XMLNS_CLOUDFORMATION, error_type, message, uuid.uuid4())
    return response


def make_response(operation_name, content='', code=200):
    response = Response()
    response._content = """<{op_name}Response xmlns="{xmlns}">
      <{op_name}Result>
        {content}
      </{op_name}Result>
      <ResponseMetadata><RequestId>{uid}</RequestId></ResponseMetadata>
    </{op_name}Response>""".format(xmlns=XMLNS_CLOUDFORMATION,
        op_name=operation_name, uid=uuid.uuid4(), content=content)
    response.status_code = code
    return response


def validate_template(req_data):
    LOG.debug('Validate CloudFormation template: %s' % req_data)
    response_content = """
        <Capabilities></Capabilities>
        <CapabilitiesReason></CapabilitiesReason>
        <DeclaredTransforms></DeclaredTransforms>
        <Description></Description>
        <Parameters>
        </Parameters>
    """
    try:
        template_deployer.template_to_json(req_data.get('TemplateBody')[0])
        response = make_response('ValidateTemplate', response_content)
        return response
    except Exception as err:
        response = error_response('Template Validation Error: %s' % err)
        return response


class ProxyListenerCloudFormation(ProxyListener):

    def forward_request(self, method, path, data, headers):
        req_data = None
        if method == 'POST' and path == '/':
            req_data = urlparse.parse_qs(to_str(data))
            action = req_data.get('Action')[0]

            if action != 'ValidateTemplate':
                data = self._reset_account_id(data)
                return Request(data=data, headers=headers, method=method)

        if req_data:
            if action == 'ValidateTemplate':
                return validate_template(req_data)


        return True

    def _reset_account_id(self, data):
        """ Fix account ID in request payload. All external-facing responses contain our
            predefined account ID (defaults to 000000000000), whereas the backend endpoint
            from moto expects a different hardcoded account ID (123456789012). """
        return aws_stack.fix_account_id_in_arns(
            data, colon_delimiter='%3A', existing=TEST_AWS_ACCOUNT_ID, replace=123456789)

    def return_response(self, method, path, data, headers, response):
        if response.status_code >= 400:
            LOG.debug('Error response from CloudFormation (%s) %s %s: %s' %
                      (response.status_code, method, path, response.content))
        if response._content:
            aws_stack.fix_account_id_in_arns(response)

    def _list_stack_names(self):
        client = aws_stack.connect_to_service('cloudformation')
        stack_names = [s['StackName'] for s in client.list_stacks()['StackSummaries']]
        return stack_names


# instantiate listener
UPDATE_CLOUDFORMATION = ProxyListenerCloudFormation()
