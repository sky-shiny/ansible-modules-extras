#!/usr/bin/env python
#THIS SOFTWARE CONTRIBUTION IS PROVIDED ON BEHALF OF BSKYB LTD.
#BY THE CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES,
#INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF
#MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED

# Copyright (c) 2015, <maxwell.cameron@bskyb.com>

DOCUMENTATION = '''
---
module: os_load_balancer
version_added: "1.9.1"
short_description: Creates an openstack load balancer, health monitor, pool and vip, returning the floating ip.

options:
  net:
    required: true
  ext_net:
    required: true
  name:
    required: true
  protocol:
    required: false
    default: 'HTTP'
    choices: ['HTTP', 'HTTPS', 'TCP']
  health_http_method:
    required: false
    default: 'GET'
    choices: ['GET', 'PUT', 'POST']
  url_path:
    required: false
    default: '/'
  method:
    required: false
    default: 'LEAST_CONNECTIONS'
    choices: ['ROUND_ROBIN', 'LEAST_CONNECTIONS', 'SOURCE_IP']
  port:
    required: false
    default: 80
  interval:
    required: false
    default: 2
  max_retries:
    required: false
    default: 3
  timeout:
    required: false
    default: 1
  expected_codes:
    required: false
    default: "200-299"
  floatingip_address:
    required: false
  healthcheck_protocol:
    required: false
    default: 'HTTP'
    choices: ['HTTP', 'HTTPS', 'TCP']

'''


EXAMPLES = '''
os_load_balancer: net=mcint_net_0 ext_net=vlan3320 name=mc_live_lb1
'''
from ansible.module_utils.basic import *
from retrying import retry
try:
  from neutronclient.v2_0 import client as neutronclient
  from neutronclient.neutron import v2_0 as neutronV20
except ImportError:
  print "failed=True msg='neutronclient and keystone client are required'"

def get_keystone_credentials_v1(region=os.environ.get('OS_REGION_NAME')):
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

def lb_exists(NEUTRON, name):
  """
  Boolean function to check for existence of a load balancer with this name
  """
  pools = NEUTRON.list_pools()['pools']
  i = 0
  while i < len(pools):
    if pools[i]['name'] == name:
      return True
    i = i+1
  return False

@retry(wait_exponential_multiplier=500, wait_exponential_max=10000, stop_max_attempt_number=7)
def get_vip(NEUTRON, vip_id):
  vip = NEUTRON.show_vip(vip_id)
  if 'address' in vip['vip']:
    return vip['vip']['address']

@retry(wait_exponential_multiplier=500, wait_exponential_max=10000, stop_max_attempt_number=7)
def get_fip_from_addr(NEUTRON, floatingip_address):
  address_list = NEUTRON.list_floatingips()
  for fip in address_list['floatingips']:
    if fip['floating_ip_address'] == floatingip_address:
      return fip
  raise Exception("Floating ip not found")

def find_neutron_object(NEUTRON, object_type, object_ref):
  neutron_object = neutronV20.find_resourceid_by_name_or_id(NEUTRON, object_type, object_ref)
  return neutron_object

@retry(wait_exponential_multiplier=500, wait_exponential_max=10000, stop_max_attempt_number=7)
def associate_health_monitor(NEUTRON, healthmonitor_id, pool_id):
  pool = find_neutron_object(NEUTRON, 'pool', pool_id)
  body_value = {
    'health_monitor': {
      'id': healthmonitor_id
    }
  }
  response = NEUTRON.associate_health_monitor(pool, body=body_value)
  return response

def get_first_subnet_id(NEUTRON, net):
  subnets = NEUTRON.list_subnets()
  net_id = find_neutron_object(NEUTRON, 'network', net)
  for subnet in subnets['subnets']:
    if subnet['network_id'] == str(net_id):
      return subnet['id']
  raise Exception("Subnet Not Found")

def create_haproxy_lb(NEUTRON, net, name, method, tenant_id, port, interval, health_http_method, max_retries, url_path, timeout, protocol, expected_codes, ext_net, floatingip_address, healthcheck_protocol):
  """
  Create a load balancer with a VIP on an external network and the pool on the internal network.
  """
  subnet_id = get_first_subnet_id(NEUTRON, net)
  try:
    body_value = {
      'pool': {
        'name': name,
        'lb_method': method,
        'protocol': protocol,
        'subnet_id': subnet_id,
      }
    }
    response = NEUTRON.create_pool(body=body_value)
    pool_id = response['pool']['id']
    vip_name = name + '-vip'
    body_value = {
      'vip': {
        'name': vip_name,
        'protocol_port': port,
        'protocol': protocol,
        'subnet_id': subnet_id,
        'pool_id': pool_id,
      }
    }
    response = NEUTRON.create_vip(body=body_value)
    port_id = response['vip']['port_id']
    vip_address = get_vip(NEUTRON, response['vip']['id'])
    body_value = {
      'health_monitor': {
        'delay': interval,
        'http_method': health_http_method,
        'max_retries': max_retries,
        'url_path': url_path,
        'timeout': timeout,
        'type': healthcheck_protocol,
        'expected_codes': expected_codes,
      }
    }
    response = NEUTRON.create_health_monitor(body=body_value)
    healthmonitor_id = response['health_monitor']['id']
    associate_health_monitor(NEUTRON, healthmonitor_id, pool_id)
    if floatingip_address is None:
      _network = find_neutron_object(NEUTRON, 'network', ext_net)
      body_value = {
        'floatingip': {
          'floating_network_id': _network
        }
      }
      response = NEUTRON.create_floatingip(body=body_value)
      floatingip_id = response['floatingip']['id']
      floatingip_address = response['floatingip']['floating_ip_address']
    else:
      floatingip_id = get_fip_from_addr(NEUTRON, floatingip_address)['id']
    body_value = {
      'port_id': port_id,
      'fixed_ip_address': vip_address
    }
    NEUTRON.update_floatingip(floatingip_id, {'floatingip': body_value})
    return floatingip_address
  except Exception, error:
    raise error
    #TODO delete any objects created

def main(net, name, method, tenant_id, port, interval, health_http_method, max_retries, url_path, timeout, protocol, expected_codes, ext_net, floatingip_address, healthcheck_protocol):
  try:
    NEUTRON = initialise_neutron()
  except Exception, error:
    print "failed=True msg='Please provide correct credentials for neutron'"

  if lb_exists(NEUTRON, name):
    module.exit_json(changed=False, msg="Load balancer exists")
  try:
    vip = create_haproxy_lb(NEUTRON, net, name, method, tenant_id, port, interval, health_http_method, max_retries, url_path, timeout, protocol, expected_codes, ext_net, floatingip_address, healthcheck_protocol)
    module.exit_json(changed=True, msg="Load balancer created with floating ip: {0}".format(vip), fip=vip)
  except Exception, error:
    module.fail_json(msg=error.message)

module = AnsibleModule(
  argument_spec = dict(
    net=dict(
      required=True
    ),
    name=dict(
      required=True
    ),
    method=dict(
      default="LEAST_CONNECTIONS",
      choices=["ROUND_ROBIN", "LEAST_CONNECTIONS", "SOURCE_IP"]
    ),
    tenant_id=dict(
      default=os.environ.get('OS_TENANT_ID')
    ),
    port=dict(
      default=80
    ),
    interval=dict(
      default=2
    ),
    health_http_method=dict(
      default="GET",
      choices=["GET", "PUT", "POST"]
    ),
    max_retries=dict(
      default=3
    ),
    url_path=dict(
      default='/'
    ),
    timeout=dict(
      default=1
    ),
    protocol=dict(
      default="HTTP",
      choices=["HTTP", "HTTPS", "TCP"]
    ),
    expected_codes=dict(
      default="200",
      choices=["200", "201", "202", "203", "204"]
    ),
    ext_net=dict(
      required=True
    ),
    floatingip_address=dict(
      default=None
    ),
    healthcheck_protocol=dict(
      default="HTTP",
      choices=["HTTP", "HTTPS", "TCP"]
    )
  )
)
NET = module.params.get("net")
NAME = module.params.get("name")
METHOD = module.params.get("method")
TENANT_ID = module.params.get("tenant_id")
PORT = module.params.get("port")
INTERVAL = module.params.get("interval")
HEALTH_HTTP_METHOD = module.params.get("health_http_method")
MAX_RETRIES = module.params.get("max_retries")
URL_PATH = module.params.get("url_path")
TIMEOUT = module.params.get("timeout")
PROTOCOL = module.params.get("protocol")
EXPECTED_CODES = module.params.get("expected_codes")
EXT_NET = module.params.get("ext_net")
FLOATINGIP_ADDRESS = module.params.get("floatingip_address")
HEALTHCHECK_PROTOCOL = module.params.get("healthcheck_protocol")
main(NET, NAME, METHOD, TENANT_ID, PORT, INTERVAL, HEALTH_HTTP_METHOD, MAX_RETRIES, URL_PATH, TIMEOUT, PROTOCOL, EXPECTED_CODES, EXT_NET, FLOATINGIP_ADDRESS, HEALTHCHECK_PROTOCOL)
