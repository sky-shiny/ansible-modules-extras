#!/usr/bin/env python
#THIS SOFTWARE CONTRIBUTION IS PROVIDED ON BEHALF OF BSKYB LTD.
#BY THE CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES,
#INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF
#MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED

# Copyright (c) 2015, <maxwell.cameron@bskyb.com>

DOCUMENTATION = '''
---
module: os_server_load_balancer
version_added: "1.9.1"
short_description: Attach a named instance to a named load balancer

options:
  lb_pool_name:
    description: Load balancer pool to attach server to
    required: true
  server_name:
    description: Server name to attach to loadbalancer
    required: true
  port:
    description: Port to listen on
    required: false
    default: 80

'''

EXAMPLES = '''
os_server_load_balancer: name=my_graphite_server lb_pool_name=graphite_relay_lb port=2003
'''

from ansible.module_utils.basic import *
from neutronclient.v2_0 import client as neutronclient
from novaclient.client import Client

def get_nova_credentials_v2(region='Slo'):
  d = {}
  d['version'] = '2'
  d['username'] = os.environ['OS_USERNAME']
  d['api_key'] = os.environ['OS_PASSWORD']
  d['auth_url'] = os.environ['OS_AUTH_URL']
  d['project_id'] = os.environ['OS_TENANT_NAME']
  d['region_name'] = region
  return d

def get_keystone_credentials_v1(region='Slo'):
  d = {}
  d['auth_url'] = os.environ['OS_AUTH_URL']
  d['username'] = os.environ['OS_USERNAME']
  d['password'] = os.environ['OS_PASSWORD']
  d['tenant_name'] = os.environ['OS_TENANT_NAME']
  d['region_name'] = region
  return d

def initialise_neutron(region=os.environ.get('OS_REGION_NAME')):
  credentials = get_keystone_credentials_v1(region=region)
  neutron = neutronclient.Client(**credentials)
  return neutron

def initialise_nova(region=os.environ.get('OS_REGION_NAME')):
  credentials = get_nova_credentials_v2(region)
  nova_client = Client(**credentials)
  return nova_client

def get_pool(NEUTRON, lb_pool_name):
  pools = NEUTRON.list_pools()['pools']
  i = 0
  while i < len(pools):
    if pools[i]['name'] == lb_pool_name:
      return pools[i]['id']
    i = i+1

def instance_attached(NEUTRON, pool_name, private_ip_address):
  """
  Boolean function to check for existence of a member in a load balancer
  """
  pool_id = get_pool(NEUTRON, pool_name)
  members = NEUTRON.list_members()['members']
  i = 0
  while i < len(members):
    if members[i]['pool_id'] == pool_id and members[i]['address'] == private_ip_address:
      return True
    i = i+1
  return False

def get_named_instance_ip(name):
  NOVA_CLIENT = initialise_nova()
  instance = NOVA_CLIENT.servers.find(name=name)
  private_ip_address = instance.addresses.values()[0][0]['addr']
  return private_ip_address

def attach_instance(NEUTRON, name, lb_pool_name, port=80):
  """
  Attach an instance to a load balancer
  """
  private_ip_address = get_named_instance_ip(name)
  if not instance_attached(NEUTRON, lb_pool_name, private_ip_address):
    pool_id = get_pool(NEUTRON, lb_pool_name)
    body_value = {
      'member': {
        'pool_id': pool_id,
        'address': private_ip_address,
        'protocol_port': port
      }
    }
    try:
      response = NEUTRON.create_member(body=body_value)
      return response
    except Exception, error:
      raise Exception("Instance failed to attach addr: {0}".format(private_ip_address))
  else:
    raise Exception("Instance already attached addr: {0}".format(private_ip_address))


def main(server_name, lb_pool_name, port):
  NEUTRON = initialise_neutron()
  try:
    attach_instance(NEUTRON, server_name, lb_pool_name, port)
    module.exit_json(change=True, msg="Instance attached")
  except Exception, error:
    if 'already' in error.message:
      module.exit_json(changed=False, msg="Instance already attached")
    else:
      module.fail_json(msg=error.message)

module = AnsibleModule(
  argument_spec=dict(
    server_name=dict(
      required=True
    ),
    lb_pool_name=dict(
      required=True
    ),
    port=dict(
      default=80
    )
  )
)

NAME = module.params.get("server_name")
LB_POOL_NAME = module.params.get("lb_pool_name")
PORT = module.params.get("port")
main(NAME, LB_POOL_NAME, PORT)

